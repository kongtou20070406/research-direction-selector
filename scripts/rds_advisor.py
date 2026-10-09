"""RDS Programmatic Advisor Engine.

Reports heuristic signals and candidate diagnostics for human or experimental review.
It does not solve parameter ranges or establish causes from endpoint telemetry.
"""
import json
import base64
import math
import os
import re
from pathlib import Path
import hashlib
import sqlite3
import time
import signal
import subprocess
import sys
from typing import Any, Dict, List, Optional

from rds_artifacts import strict_json

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_KNOWLEDGE_ROWS = 10000


def _configure_wal(db):
    # SQLite's initial journal-mode transition can return BUSY without using
    # busy_timeout. Retry only this cold-start transition, before any transaction.
    deadline = time.monotonic() + 15
    while True:
        try:
            db.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 255
            if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _read_document(path):
    if not path.is_file():
        raise ValueError("Document must be an existing regular file")
    with path.open("rb") as handle:
        raw = handle.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("Advisor document exceeds the 2 MiB limit")
    try:
        return raw, raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Advisor document must be UTF-8") from exc


def _knowledge_entry(rule, entry_id, document_sha, *, legacy=False):
    if (not isinstance(rule, dict) or type(rule.get("line_number")) is not int
            or rule["line_number"] < 1 or not isinstance(rule.get("excerpt"), str)
            or not rule["excerpt"].strip() or not isinstance(rule.get("source"), str)
            or not rule["source"].strip() or len(rule["source"]) > 1024
            or not isinstance(rule.get("topic"), str) or not rule["topic"].strip()
            or len(rule["topic"]) > 256):
        raise ValueError("Invalid legacy advisor knowledge entry")
    normalized = {key: rule[key] for key in ("line_number", "excerpt", "topic", "source")}
    normalized["excerpt"] = normalized["excerpt"][:120]
    normalized.update(adoption_status="UNREVIEWED", assurance="HEURISTIC_ONLY")
    if legacy:
        # This hashes the migration bundle, not the source named by an old excerpt.
        normalized["legacy_bundle_sha256"] = document_sha
        normalized["source_hash_status"] = "UNVERIFIED_LEGACY_SOURCE"
    else:
        normalized["source_sha256"] = document_sha
    return entry_id, document_sha, json.dumps(normalized, ensure_ascii=False, allow_nan=False, sort_keys=True)



def _advice(advisor_type, **fields):
    return {"advisor_type": advisor_type, "assurance": "HEURISTIC_ONLY", "observations": [],
            "alternative_explanations": [], "minimal_test": [],
            "limitations": [], "evidence": [], **fields}


def _number(value, name):
    try:
        finite = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _bounded_theory_gate(request, seconds):
    """Bound generation and independent replay together, including native children."""
    from rds_project import _Job
    bootstrap = ("import json,sys; request=json.loads(sys.stdin.buffer.read()); "
                 "sys.path.insert(0,sys.argv[1]); from rds_probe import declarative_probe; "
                 "result=declarative_probe(request['formal'],request['committed']); "
                 "sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False,allow_nan=False).encode('utf-8'))")
    deadline = time.monotonic() + seconds
    process = job = None
    output = errors = b""
    try:
        process = subprocess.Popen([sys.executable, "-I", "-c", bootstrap, str(Path(__file__).resolve().parent)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   shell=False, start_new_session=os.name != "nt",
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        job = _Job(process)
        output, errors = process.communicate(_json(request, cap=2 * MAX_DOCUMENT_BYTES).encode("utf-8"),
                                            timeout=max(0, deadline - time.monotonic()))
        if process.returncode or time.monotonic() > deadline:
            raise ValueError(errors.decode("utf-8", errors="replace")[-1000:] or "Theory deadline exceeded")
        gate = strict_json(output.decode("utf-8"))
        _json(gate, cap=MAX_DOCUMENT_BYTES)
        if time.monotonic() > deadline:
            raise ValueError("Theory deadline exceeded")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        gate = {"status": "UNKNOWN", "assurance": "NONE", "reason": "Bounded theory check: " + str(exc)}
    finally:
        if process is not None:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif job is not None:
                job.stop()
            else:
                process.kill()
            output, errors = process.communicate()
        if job is not None:
            job.close()
    worker = {name: {"base64": base64.b64encode(raw[:cap]).decode("ascii"), "truncated": len(raw) > cap}
              for name, raw, cap in (("stdout", output, MAX_DOCUMENT_BYTES), ("stderr", errors, 65536))}
    worker["exit_code"] = process.returncode if process is not None else None
    return gate, worker



class RDSAdvisor:
    def __init__(self, root_dir: Path):
        self.root_dir = root_dir.resolve()
        self.knowledge_db = self.root_dir / ".rds" / "advisor.sqlite3"
        self.literature_load_errors = []
        self.literature_principles = self._load_scientific_principles()

    def execute_theory_probe(self, run_spec, formal, *, theory_allowance, committed=None):
        """Check a declared side condition before reserving a project probe.

        Uses the existing verifier and transactional runner. A proof concerns the
        declared model; the probe still needs an empirical manipulation check.
        Ordinary project commands remain available through ProjectStore.
        """
        from rds_probe import formal_requirement
        from rds_project import ProjectStore, digest
        from rds_verify import verifier_id

        # Freeze the request used for both checking and execution. A committed
        # result is replayed, never trusted for its caller-supplied PASS label.
        manifest = strict_json(_json(run_spec))
        requirement = formal_requirement({"formal": strict_json(_json(formal))})
        if requirement is None or requirement["kind"] != "declarative":
            raise ValueError("Theory probes require an explicit declarative formal obligation")
        request = {"formal": requirement, "committed": strict_json(_json(committed, cap=MAX_DOCUMENT_BYTES)),
                   "verifier_sha256": verifier_id(), "native_executable": os.environ.get("RDS_LEAN_EXECUTABLE"),
                   "allowance": strict_json(_json(theory_allowance))}
        store = ProjectStore(self.root_dir)
        started = time.monotonic()
        with store.theory_allowance(manifest, request, theory_allowance) as charge:
            gate, worker = _bounded_theory_gate(request, charge["allowance"]["wall_seconds"])
            charge.update(status=gate["status"], result_sha256=digest(gate), result=gate, worker_output=worker)
        result = {"formal_gate": gate, "theory_wall_seconds": time.monotonic() - started,
                  "theory_charge": {key: charge[key] for key in ("attempt_id", "allowance", "status", "result_sha256")},
                  "run_spec": manifest, "receipt": None}
        if gate["status"] != "PASS" or gate.get("application_status", "PASS") != "PASS":
            return result
        store.register(manifest)
        result["receipt"] = store.execute(manifest["id"])
        return result

    def _load_scientific_principles(self) -> List[Dict[str, Any]]:
        paths = [self.root_dir / "references/scientific_tuning_principles.json",
                 Path(__file__).resolve().parent.parent / "references/scientific_tuning_principles.json"]
        for path in dict.fromkeys(paths):
            if not path.exists():
                continue
            try:
                records = json.loads(_read_document(path)[1])
                if not isinstance(records, list) or not all(isinstance(p, dict) for p in records):
                    raise ValueError("Principles must be a list of objects")
                return records
            except (OSError, ValueError) as exc:
                self.literature_load_errors.append(f"{path}: {exc}")
                # A malformed explicit library must not silently become a different library.
                return []
        return []


    def query_literature_principles(self, query: str) -> List[Dict[str, Any]]:
        """Search source records; matching a topic does not establish applicability."""
        terms = [t for t in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", query.lower()) if len(t) > 1]
        aliases = {"lr": ["learning rate"], "学习率": ["learning rate"],
                   "wd": ["weight decay"], "权重衰减": ["weight decay"],
                   "欠拟合": ["underfitting"], "过拟合": ["overfitting"],
                   "梯度": ["gradient"], "预热": ["warmup"], "停滞": ["plateau"]}
        for term, translations in aliases.items():
            if term in terms or (not term.isascii() and term in query):
                if term in terms:
                    terms.remove(term)
                terms.extend(translations)
        return [p for p in self.literature_principles if not terms or any(
            term in json.dumps(p, ensure_ascii=False).lower() for term in terms)]


    def _legacy_entries(self):
        path = self.root_dir / ".rds" / "advisor_knowledge.json"
        if not path.exists():
            return []
        raw, text = _read_document(path)
        rows = json.loads(text)
        if not isinstance(rows, list) or len(rows) > MAX_KNOWLEDGE_ROWS:
            raise ValueError("Legacy advisor knowledge must contain at most 10000 entries")
        digest = hashlib.sha256(raw).hexdigest()
        entries = []
        for row in rows:
            entry_id = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False,
                                                allow_nan=False).encode("utf-8")).hexdigest()
            entries.append(_knowledge_entry(row, "legacy:" + entry_id, digest, legacy=True))
        return entries

    def _needs_legacy_migration(self):
        if not self.knowledge_db.exists():
            return True
        db = sqlite3.connect(self.knowledge_db.as_uri() + "?mode=ro", uri=True, timeout=15)
        try:
            table = db.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone()
            return table is None or db.execute("SELECT 1 FROM metadata WHERE key='legacy_migrated'").fetchone() is None
        finally:
            db.close()

    def advise_on_rejection(self, gate_error: str, plan_spec: Dict[str, Any]) -> Dict[str, Any]:
        advice = _advice("GATE_REJECTION_ADVICE", detected_bottleneck="GATE_FAILURE",
                         actionable_suggestion="检查原始门禁错误与锁定声明后重新预检。", recommended_patch={})
        advice["observations"] = [{"gate_error": gate_error, "plan_id": plan_spec.get("id")}]
        advice["evidence"] = [{"kind": "gate_output", "source": "plan admission", "text": gate_error}]
        advice["limitations"] = ["错误文本不能替代正式求解；本建议未完成符号逆解，也未修改锁定假说。"]
        if "Formal gate FAIL" in gate_error or "m < 1" in gate_error:
            advice.update(detected_bottleneck="FORMAL_GATE_REJECTED",
                          actionable_suggestion="对照锁定的 formal 定义、实际代码和定义域，核实拒绝针对哪个谓词及可达范围。")
            advice["alternative_explanations"] = ["干预未实现边界跨越", "代码、域或声明不一致", "拒绝针对其他性质"]
            advice["minimal_test"] = [{"question": "原干预能否实现声明性质？",
                "protocol": "保留原假说；检查 admission probe 原始结论和实际算子/域。需要新干预时另建修订并重新过门禁。",
                "decision_rule": "可达性结论仅适用于已验证形式和定义域；不从错误字符串生成替代模型。"}]
        elif "Self-signed" in gate_error:
            forbidden = {"manipulation_verified", "falsifier_triggered", "primary_metric_gain",
                         "final_run_authorized", "matched_recipe", "matched_compute"}
            advice.update(detected_bottleneck="FORBIDDEN_SELF_SIGNING",
                          actionable_suggestion="移除包括嵌套对象在内的自签验证字段，由门禁和执行收据产生结论。",
                          recommended_patch={"remove_fields": sorted(forbidden.intersection(plan_spec))})
            advice["minimal_test"] = [{"question": "方案字段是否合规？", "protocol": "重新做 plan pre-check。",
                                       "decision_rule": "预检通过不是执行或研究成功收据。"}]
        elif any(word in gate_error for word in ("Exploration requires development", "one-time confirmation",
                                                 "Confirmation lineage", "Confirmation contaminated")):
            advice.update(detected_bottleneck="DATA_SPLIT_ELIGIBILITY",
                          actionable_suggestion="按合同里实际注册的 development 分区探索；按暴露谱系核实确认资格。")
            advice["alternative_explanations"] = ["用途与分区角色不匹配", "确认数据或同谱系数据已暴露"]
            advice["minimal_test"] = [{"question": "当前用途可读取哪个分区？",
                "protocol": "查合同 splits 和 exposure 谱系，选择真实分区 ID，再预检。",
                "decision_rule": "改用途或复用缓存不能恢复已暴露分区的确认资格。"}]
        elif "Budget unavailable" in gate_error or "maximum allocation" in gate_error:
            advice.update(detected_bottleneck="BUDGET_CONSTRAINT",
                          actionable_suggestion="核算 limits、spent、reserved 和确认保留额；可行时用实测吞吐量设计有界探针。")
            advice["alternative_explanations"] = ["额度耗尽或已预留", "确认保留额受保护", "超出适配器单次上限"]
            advice["minimal_test"] = [{"question": "剩余额度可承担有判别力的探针吗？",
                "protocol": "读实际可用资源并核对已有步时/开销记录；只在缺少相关记录且有额度时做小 canary。",
                "decision_rule": "无可用额度则重分配或停止；不能把固定毫秒数换算成未经测量的训练步数。"}]
        return advice

    def advise_on_loss_dynamics(self, compressed_telemetry: Dict[str, Any]) -> Dict[str, Any]:
        """Prioritize numerical/data faults; hyperparameter changes remain tests."""
        if not isinstance(compressed_telemetry, dict):
            raise ValueError("Telemetry must be an object")
        telemetry = compressed_telemetry
        advice = _advice("DYNAMICS_DIAGNOSIS", status="INSUFFICIENT_EVIDENCE", diagnostics=[], action_items=[])
        advice["limitations"] = ["压缩遥测不能确定根因；loss/梯度范数没有跨任务通用阈值。",
                                  "超参候选应与原配置做同 seed 配对对照，记录预算和回退条件。"]
        nonfinite = []
        for key in ("peak_grad_norm", "grad_norm_limit", "loss", "train_loss", "val_loss",
                    "loss_variance", "step", "warmup_steps"):
            value = telemetry.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a number")
            try:
                finite = math.isfinite(value)
            except OverflowError:
                raise ValueError(f"{key} exceeds the supported numeric range") from None
            if not finite:
                nonfinite.append(key)
            else:
                advice["observations"].append({"field": key, "value": value})
        for key in ("nan_or_inf", "decay_on_1d_params"):
            if key in telemetry and not isinstance(telemetry[key], bool):
                raise ValueError(f"{key} must be a boolean")
        if "loss_trend" in telemetry and not isinstance(telemetry["loss_trend"], str):
            raise ValueError("loss_trend must be a string")
        for key in ("loss_trend", "nan_or_inf", "decay_on_1d_params"):
            if key in telemetry:
                advice["observations"].append({"field": key, "value": telemetry[key]})
        if telemetry.get("nan_or_inf") is True or nonfinite:
            advice["status"] = "CRITICAL_ANOMALY"
            advice["diagnostics"] = ["观测到非有限数值；尚未定位首个异常算子或根因。"]
            if nonfinite:
                advice["observations"].append({"nonfinite_fields": nonfinite})
            advice["alternative_explanations"] = ["输入/标签异常", "非法定义域或不稳定算子",
                                                   "低精度溢出/loss scaling 异常", "更新或梯度异常传播"]
            advice["action_items"] = ["保留异常批次与检查点，定位输入、前向、loss、反向或更新中首个非有限值。",
                                       "固定批次与随机状态，先检查数据/算子定义域，再对照原精度与 FP32 重放。"]
            advice["minimal_test"] = [{"question": "首个非有限值从哪里产生？",
                "protocol": "固定批次/检查点/随机状态，逐阶段记录范围与有限性；对照 FP32，先不更新参数。",
                "decision_rule": "定位后只改一个相关因素；只有定义域/分母故障确诊才试适合尺度的稳定化，不一律加 eps 或 clamp。"}]
            return advice

        peak, limit = telemetry.get("peak_grad_norm"), telemetry.get("grad_norm_limit")
        if (peak is not None and peak < 0) or (limit is not None and limit <= 0):
            raise ValueError("Gradient norm must be nonnegative and its declared limit positive")
        if peak is not None and limit is not None and peak > limit:
            advice["status"] = "GRADIENT_SPIKE_SUSPECTED"
            advice["diagnostics"].append("梯度峰值超过本次声明的 grad_norm_limit；这是待定位的异常候选。")
            advice["alternative_explanations"].extend(["异常批次/目标尺度", "loss reduction 或 AMP unscale 差异", "更新路径梯度放大"])
            advice["action_items"].append("先核对测量位置和 AMP unscale，记录逐层范数及更新/参数范数比。")
            advice["minimal_test"].append({"question": "峰值来自测量、数据还是更新不稳定？",
                "protocol": "固定异常批次核对 unscale 与 reduction；确认为更新异常后，单因素对照 LR 或按既有范数统计校准的 clipping。",
                "decision_rule": "比较异常频率、更新量和开发指标；被截小的范数不证明根因已解决。"})
            advice["evidence"].append({"kind": "literature", "source": "https://proceedings.mlr.press/v28/pascanu13.html",
                "supports": "梯度爆炸和截断是可研究的优化问题，不提供通用范数阈值。"})
        elif peak is not None:
            advice["diagnostics"].append("单个梯度峰值不能认定梯度爆炸；需同模型同量纲的分布和校准阈值。")
            advice["action_items"].append("记录时间序列、更新尺度和本任务校准的 grad_norm_limit。")

        trend = str(telemetry.get("loss_trend", "")).upper()
        if trend in {"EXPLODING", "DIVERGENT", "DIVERGING", "INCREASING"}:
            if advice["status"] == "INSUFFICIENT_EVIDENCE":
                advice["status"] = "DIVERGENCE_REPORTED"
            advice["diagnostics"].append("遥测报告 loss 上升/发散，需用原始曲线和相同评估设置核验。")
            advice["alternative_explanations"].extend(["数据/目标/reduction 变化", "精度或 optimizer state 故障", "LR/调度与更新尺度不匹配"])
            advice["action_items"].append("先排查输入、loss 定义、精度与 optimizer state；之后单独对照 LR 或调度。")
            advice["minimal_test"].append({"question": "发散能否在固定批次和检查点重现？",
                "protocol": "回到异常前检查点核验数据和精度；确有需要时记录原 LR 与一个预声明候选的短对照。",
                "decision_rule": "非有限值或开发指标恶化即停止候选；不能同时改结构、LR 和 loss。"})
        elif trend in {"STAGNANT", "PLATEAU"}:
            if advice["status"] == "INSUFFICIENT_EVIDENCE":
                advice["status"] = "PLATEAU_REPORTED"
            advice["diagnostics"].append("遥测报告平台；平台不等于容量不足或训练应该继续。")
            advice["alternative_explanations"].extend(["评估窗口/精度不足", "优化器未更新、梯度断开或数据故障", "已收敛", "优化/容量限制"])
            advice["action_items"].append("检查训练/验证序列、梯度和参数更新，必要时做固定小批次可拟合性检查。")
            advice["minimal_test"].append({"question": "平台来自不可更新、配置限制还是已收敛？",
                "protocol": "核验更新与数据/loss；必要时用固定小批次做有上限的可拟合性检查，再仅对一个因素做对照。",
                "decision_rule": "预声明有效变化尺度和停止条件；小批次失败不能单独区分容量与优化，成功不保证泛化。"})
        elif trend in {"DECREASING", "IMPROVING"}:
            if advice["status"] == "INSUFFICIENT_EVIDENCE":
                advice["status"] = "NO_REPORTED_ANOMALY"
            advice["diagnostics"].append("遥测报告 loss 下降；仍需核验趋势，不能据此宣布健康收敛。")
        if telemetry.get("loss_variance") is not None:
            if telemetry["loss_variance"] < 0:
                raise ValueError("loss_variance must be nonnegative")
            advice["diagnostics"].append("loss_variance 需结合窗口、目标尺度和调度解释，不能单独诊断 Warmup 缺失。")
            advice["action_items"].append("核对实际 LR/Warmup 轨迹；只有可重复早期不稳定时才对照预热。")
        if telemetry.get("decay_on_1d_params") is True:
            advice["diagnostics"].append("报告对 1D 参数衰减；先确认哪些是 LayerNorm/BatchNorm/bias，维数本身不证明错误。")
            advice["action_items"].append("检查实际参数分组；若怀疑衰减影响，匹配其余配置后单独对照 norm/bias 衰减设置。")
            advice["evidence"].append({"kind": "literature", "source": "https://arxiv.org/abs/1711.05101",
                "supports": "AdamW 研究 L2 正则与解耦衰减的差别；不证明所有 1D 参数必须免衰减。"})
        if not advice["observations"]:
            advice["diagnostics"].append("没有可解释的遥测，无法判断训练动态。")
        return advice

    def diagnose_fit_status(self, train_loss: float, val_loss: float,
                            baseline_loss: Optional[float] = None, *, telemetry=None) -> Dict[str, Any]:
        """Single losses are observations; matched curves support only candidate patterns."""
        train_loss, val_loss = _number(train_loss, "train_loss"), _number(val_loss, "val_loss")
        if baseline_loss is not None:
            baseline_loss = _number(baseline_loss, "baseline_loss")
        result = _advice("FITNESS_DIAGNOSIS", train_loss=train_loss, val_loss=val_loss,
            baseline_loss=baseline_loss, verdict="INSUFFICIENT_EVIDENCE",
            paper_reference="Goodfellow, Bengio & Courville (2016), Deep Learning, Chapter 11",
            forbidden_actions=[], required_actions=[], causal_explanation="单点损失不能确定欠拟合、过拟合或容量不足。")
        gap = val_loss - train_loss
        result["observations"] = [{"train_loss": train_loss, "val_loss": val_loss,
                                   "baseline_loss": baseline_loss, "raw_gap": gap if math.isfinite(gap) else None}]
        result["alternative_explanations"] = ["loss 定义、模式、正则项或预处理不可比", "分布差异/噪声", "优化/容量限制", "泛化随训练变化"]
        result["limitations"] = ["缺少配对曲线和协议时，不使用绝对 loss 或基线比例阈值。",
                                  "曲线模式不能单独确定容量或优化根因。"]
        result["evidence"] = [{"kind": "literature", "source": "https://www.deeplearningbook.org/contents/guidelines.html",
            "supports": "用训练和验证表现指导可检验的选择，不支持通用单点诊断阈值。"}]
        result["minimal_test"] = [{"question": "可比配对曲线支持什么模式？",
            "protocol": "核验相同目标尺度、reduction、数据与评估模式；收集同检查点 train/validation 历史、噪声尺度和调度。",
            "decision_rule": "训练改善/验证退化时调查泛化；停滞先排查更新与数据，只有未决且影响决策时才做有界单因素对照。"}]
        if telemetry is None:
            return result
        if not isinstance(telemetry, dict):
            raise ValueError("Fit telemetry must be an object")
        if telemetry.get("losses_comparable") is not True or telemetry.get("matched_checkpoints") is not True:
            result["limitations"].append("配对检查点或可比协议未声明，暂不解释曲线差异。")
            return result
        train_history, val_history = telemetry.get("train_loss_history"), telemetry.get("val_loss_history")
        if not isinstance(train_history, list) or not isinstance(val_history, list) or len(train_history) != len(val_history) or len(train_history) < 3:
            result["limitations"].append("至少需要三个同检查点配对观测；缺失、长度不同或更短序列不用于趋势诊断。")
            return result
        train_history = [_number(v, "train_loss_history") for v in train_history]
        val_history = [_number(v, "val_loss_history") for v in val_history]
        if not math.isclose(train_history[-1], train_loss) or not math.isclose(val_history[-1], val_loss):
            raise ValueError("Fit history endpoints must match the supplied losses")
        tolerance = telemetry.get("trend_tolerance")
        if tolerance is None:
            result["limitations"].append("缺少按指标精度/噪声校准的 trend_tolerance，暂不把微小变化判成趋势。")
            return result
        tolerance = _number(tolerance, "trend_tolerance")
        if tolerance < 0:
            raise ValueError("trend_tolerance must be nonnegative")
        result["observations"].append({"train_loss_history": train_history, "val_loss_history": val_history,
                                       "declared_trend_tolerance": tolerance})
        if train_history[-1] < train_history[0] - tolerance and val_history[-1] > val_history[0] + tolerance:
            result["verdict"] = "POSSIBLE_GENERALIZATION_GAP"
            result["causal_explanation"] = "声明可比的配对曲线中训练改善、验证退化；支持调查泛化，仍不能排除分布差异和噪声。"
        elif max(train_history) - min(train_history) <= tolerance and max(val_history) - min(val_history) <= tolerance:
            result["verdict"] = "POSSIBLE_OPTIMIZATION_PLATEAU"
            result["causal_explanation"] = "配对曲线在声明变化尺度内平坦；需区分已收敛、不可更新与优化/容量限制。"
        else:
            result["verdict"] = "NO_CLEAR_FIT_PATTERN"
            result["causal_explanation"] = "未显示明确的训练改善/验证退化或平台候选，不能由此确定健康收敛。"
        return result

    def ingest_document(self, doc_path: Path, topic: Optional[str] = None) -> Dict[str, Any]:
        """Store unreviewed source excerpts with a short isolated WAL transaction."""
        doc_path = doc_path.resolve()
        if not doc_path.exists():
            raise FileNotFoundError(f"Document not found: {doc_path}")
        if doc_path.suffix.lower() in {".pdf", ".docx"}:
            raise ValueError("Document ingestion accepts UTF-8 text; extract binary documents to text first")
        if topic is not None and (not isinstance(topic, str) or not topic.strip() or len(topic) > 256):
            raise ValueError("Advisor topic must be a nonempty string of at most 256 characters")
        raw, text = _read_document(doc_path)
        document_sha = hashlib.sha256(raw).hexdigest()
        extracted_rules = []
        
        # Keyword matching anchors candidate evidence; it never adopts a scientific rule.
        terms = ("underfitting", "overfitting", "learning rate", "plateau", "gradient", "warmup",
                 "weight decay", "欠拟合", "过拟合", "学习率", "梯度", "停滞", "预热", "权重衰减")
        lines = text.splitlines()
        for idx, line in enumerate(lines, 1):
            line_str = line.strip()
            if any(kw in line_str.lower() for kw in terms):
                extracted_rules.append({
                    "line_number": idx,
                    "excerpt": line_str[:120],
                    "topic": topic or "general_tuning",
                    "source": doc_path.name
                })
                if len(extracted_rules) > MAX_KNOWLEDGE_ROWS:
                    raise ValueError("Advisor document exceeds the 10000-excerpt limit")
        entries = []
        for rule in extracted_rules:
            key = f"{document_sha}:{rule['line_number']}:{rule['topic']}"
            entries.append(_knowledge_entry(rule, hashlib.sha256(key.encode("utf-8")).hexdigest(), document_sha))
        # Parsing and possible legacy loading finish before any writer lock.
        legacy = self._legacy_entries() if self._needs_legacy_migration() else []
        self.knowledge_db.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.knowledge_db, timeout=15, isolation_level=None)
        try:
            _configure_wal(db)
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS knowledge (entry_id TEXT PRIMARY KEY, document_sha TEXT NOT NULL, body TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            migrate = db.execute("SELECT 1 FROM metadata WHERE key='legacy_migrated'").fetchone() is None
            existing_ids = {row[0] for row in db.execute("SELECT entry_id FROM knowledge")}
            candidates = {entry[0]: entry for entry in ((legacy if migrate else []) + entries)}
            added = [entry for entry_id, entry in candidates.items() if entry_id not in existing_ids]
            if len(existing_ids) + len(added) > MAX_KNOWLEDGE_ROWS:
                raise ValueError("Advisor knowledge exceeds the 10000-entry limit")
            db.executemany("INSERT INTO knowledge VALUES (?,?,?)", added)
            if migrate:
                db.execute("INSERT INTO metadata VALUES ('legacy_migrated','1')")
            total = len(existing_ids) + len(added)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

        evidence = [json.loads(entry[2]) for entry in added]
        return _advice("DOCUMENT_EXCERPT_INGESTION", status="INGESTED", doc_path=str(doc_path),
            adoption_status="UNREVIEWED", rules_extracted=len(extracted_rules), rules_added=len(added),
            excerpts_extracted=len(extracted_rules), total_knowledge_entries=total, evidence=evidence,
            limitations=["rules_extracted 是兼容字段，计数匹配的未审查摘录；rules_added 计数本次实际新增行，包括显式迁移。",
                         "关键词命中不会自动成为可执行原则；legacy_bundle_sha256 不代表源文档身份。"])

    def recommend_next_directions(self, state: Dict[str, Any], judgment_graph: Dict[str, Any],
                                  *, priority_action_ids=()) -> List[Dict[str, Any]]:
        """Read real state and supplied graph; recommend a review, never a causal ranking."""
        recommendations = []
        context = state.get("advisor_context", {})
        if isinstance(context, dict):
            from rds_advisor_coverage import project_context
            context = project_context(self.root_dir, context)
            state = {**state, 'advisor_context': context}
        research_mode = context.get("research_mode") if isinstance(context, dict) else None
        if research_mode is not None and research_mode not in ("theory", "empirical", "mixed"):
            raise ValueError("research_mode must be theory, empirical or mixed")
        if isinstance(context, dict) and "frontier" in context:
            from rds_frontier import discover_frontier
            frontier = discover_frontier(context["frontier"])
            if "frontier_proposals" in context:
                from rds_frontier_proposals import review_proposals
                frontier["proposal_review"] = review_proposals(frontier, context["frontier"], context["frontier_proposals"])
            recommendations.append(_advice("STRATEGIC_RESEARCH_ADVICE", type="RESEARCH_FRONTIER", urgency="REVIEW",
                reason="Use sourced graph gaps, residuals and integer dimensional constraints to ask for new relations.",
                frontier=frontier, observations=frontier["gaps"], limitations=frontier["limitations"]))
            if "decision" not in context:
                if "obstructions" in context:  # Validated, but only a direction search has goal obligations to consume them.
                    from rds_advisor_search import _obstruction_records
                    _obstruction_records(context)
                return recommendations  # Pure frontier queries do not need unrelated ML advice or rule libraries.
        if not isinstance(context, dict) or "decision" in context or "frontier" not in context:
            from rds_advisor_search import search_directions, review_selection
            options = {"templates": state["advisor_templates"]} if state.get("advisor_templates") else {}
            if priority_action_ids:
                options['priority_action_ids'] = priority_action_ids
            if isinstance(context, dict):
                options.update({key: context[key] for key in ("max_depth", "max_candidates")
                                if key in context})
                for key in ("audit_receipts", "audit_files"):
                    if context.get(key) is True:
                        options[key] = True
            read_receipt = None
            if options.get("audit_receipts"):
                # One lookup for this call: the dependency audit and obstruction audit share each receipt read.
                from rds_hypergraph import receipt_reader
                read_receipt = receipt_reader()
            dependency = None
            if isinstance(context, dict) and "dependency_map" in context:
                # History filters candidates, then the final review evaluates them.
                options["_defer_selection_review"] = True
                from rds_advisor_search import _operation_dependency
                dependency = _operation_dependency(context,
                                                   audit_receipts=options.get("audit_receipts", False),
                                                   audit_files=options.get("audit_files", False),
                                                   read_receipt=read_receipt)
                options["_dependency"] = dependency
            search = search_directions(judgment_graph, context, **options)
            loop_review = self._review_loop_history(state, context, search)
            search["selection_review"] = review_selection(search, context, _dependency=dependency,
                                                          audit_receipts=options.get("audit_receipts", False),
                                                          audit_files=options.get("audit_files", False),
                                                          _read_receipt=read_receipt)
            recommendations.append(_advice("STRATEGIC_RESEARCH_ADVICE", type="EXECUTABLE_DIRECTION_SEARCH", urgency="REVIEW",
                reason="按明确的下一决策、带来源事实和图中结构化前置条件组合有界候选。",
                search=search, observations=search["candidates"], limitations=search["limitations"],
                minimal_test=[candidate["action"] for candidate in search["candidates"]]))
            if loop_review is not None:
                recommendations.append(_advice("STRATEGIC_RESEARCH_ADVICE", type="RESEARCH_LOOP_REVIEW", urgency="REVIEW",
                    reason="Review scoped choices recorded in the existing transaction ledger.", review=loop_review,
                    observations=loop_review["flags"], limitations=loop_review["limitations"]))
            if "resources" in context:
                from rds_resources import plan_resource_batch
                resource_plan = plan_resource_batch(search, context["resources"])
                recommendations.append(_advice("STRATEGIC_RESEARCH_ADVICE", type="RESOURCE_BATCH_PLAN", urgency="REVIEW",
                    reason="Use explicit research priority, current capacity and full-duration estimates to propose one concurrent batch.",
                    resource_plan=resource_plan, observations=resource_plan["batch"], limitations=resource_plan["limitations"]))
            if isinstance(context, dict) and "obstructions" in context:
                # One fixed point after every existing check, whether or not the review was deferred.
                from rds_advisor_search import review_obstructions
                review_obstructions(search, context, _read_receipt=read_receipt)
        if research_mode is not None:
            # Explicit domain work uses its configured obligations/comparisons;
            # legacy ML branch heuristics and reference catalogs are unrelated.
            for recommendation in recommendations:
                recommendation["assurance"] = "HEURISTIC_ONLY"
            return recommendations
        active = state.get("active_branch", "main")
        branches = state.get("branches", {})
        branch, count = branches.get(active, {}), branches.get(active, {}).get("stagnation_count", 0)
        if count >= 2:
            explored = {b.get("orthogonal_dimension") for b in branches.values()}
            dimensions = [("representation", "改变表示，保留其余比较条件"), ("mechanism", "对机制做可观测单因素干预"),
                          ("loss_formulation", "对监督信号做单因素对照"), ("optimization", "固定结构/数据检验一个优化因素")]
            recommendations.append(_advice("STRATEGIC_RESEARCH_ADVICE", type="ORTHOGONAL_BRANCH_RECOMMENDATION",
                urgency="HIGH" if count >= 3 else "REVIEW", reason=f"分支 '{active}' 已记录 {count} 次连续无有效收益。",
                recommended_action="先审查失败证据与可用预算；存在可判别的新问题时，再 branch fork 做单因素对照。",
                candidate_dimensions=[{"dimension": d, "concept": c, "basis": "通用候选，需由项目证据选择"}
                                      for d, c in dimensions if d not in explored],
                observations=[{"branch_id": active, "stagnation_count": count, "status": branch.get("status")}],
                alternative_explanations=["候选确无增益", "协议或执行故障", "预算/噪声不足以判别"],
                minimal_test=[{"question": "下一对照能区分至少两个解释吗？",
                    "protocol": "用锁定指标、失败收据和预算选择一个变量；优先复用同身份对照、同 seed 配对，并预声明停止条件。",
                    "decision_rule": "无可判别问题或可用预算则停止/复核；仅当已观测 seed 不稳定且会改变决策时考虑追加。"}],
                limitations=["没有成功概率排名；停滞次数不证明一类方法永久失败，名称不证明正交。"],
                evidence=[{"kind": "state", "source": f"branches.{active}", "value": branch}]))
        cache = state.get("baseline_cache", {})
        if cache:
            entries = [{"key": key, "first_run_id": item.get("first_run_id"), "sample_count": len(item.get("observations", {}))}
                       for key, item in cache.items() if isinstance(item, dict)]
            recommendations.append(_advice("STRATEGIC_RESEARCH_ADVICE", type="COMPUTE_REUSE_ADVICE", urgency="INFO",
                reason=f"状态记录了 {len(entries)} 组对照缓存候选。",
                recommended_action="当前确定性适配器按锁定 control AST 和数据字节 SHA-256 匹配；匹配且收据可核验时复用。",
                observations=entries, minimal_test=[{"question": "本方案与缓存的计算身份是否相同？",
                    "protocol": "核对实际 baseline_key、逐样本输出和成功的 first_run_id 收据；随机/训练适配器还需 seed、checkpoint、recipe 与数值设置。",
                    "decision_rule": "身份和来源核验通过才复用；缓存不能恢复确认资格，也不保证免除整个基线训练成本。"}],
                limitations=["本方法只读状态条目，未独立验证收据；相同函数签名不等于相同计算。"],
                evidence=[{"kind": "state", "source": "baseline_cache", "entries": entries}]))
        nodes = [node for node in judgment_graph.get("nodes", []) if isinstance(node, dict)]
        if nodes:
            hypotheses = state.get("hypotheses", {})
            context = [{"hypothesis_id": hid, "task_gain": hypotheses.get(hid, {}).get("task_gain"),
                        "mechanism": hypotheses.get(hid, {}).get("mechanism")} for hid in branch.get("hypotheses", [])]
            recommendations.append(_advice("STRATEGIC_RESEARCH_ADVICE", type="JUDGMENT_GRAPH_REVIEW", urgency="REVIEW",
                reason=f"提供的判断图含 {len(nodes)} 条规则；当前分支关联 {len(context)} 个假说。",
                recommended_action="核实符合证据的 scope/trigger，再选 discriminator；文字触发器不会自动执行。",
                observations=context, candidate_rules=[{k: node.get(k) for k in
                    ("id", "scope", "trigger", "correction", "alternatives", "discriminator", "primary_gate", "falsifier", "sources")}
                    for node in nodes], limitations=["这是候选规则清单，未断言每条规则已触发或必然有效。"],
                evidence=[{"kind": "judgment_graph", "rule_id": node.get("id"), "sources": node.get("sources", [])} for node in nodes]))
        if self.knowledge_db.exists():
            try:
                db = sqlite3.connect(self.knowledge_db.as_uri() + "?mode=ro", uri=True,
                                     timeout=0.05, isolation_level=None)
                try:
                    db.execute("PRAGMA query_only=ON")
                    db.execute("BEGIN")
                    count = db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
                    latest = db.execute("SELECT body FROM knowledge ORDER BY rowid DESC LIMIT 1").fetchone()
                finally:
                    db.close()
                if count:
                    record = json.loads(latest[0])
                    if not isinstance(record, dict):
                        raise ValueError("Stored excerpt must be an object")
                    record["adoption_status"] = "UNREVIEWED"
                    recommendations.append(_advice("STRATEGIC_RESEARCH_ADVICE", type="DOC_REVIEW_CANDIDATES", urgency="INFO",
                        reason=f"存在 {count} 条摘录候选，需核实来源与适用范围。", latest_insight=record.get("excerpt"),
                        adoption_status="UNREVIEWED", limitations=["关键字摘录和旧记录均不自动成为已采纳原则。"], evidence=[record]))
            except (sqlite3.Error, ValueError, KeyError, TypeError, AttributeError) as exc:
                recommendations.append(_advice("STRATEGIC_RESEARCH_ADVICE", type="KNOWLEDGE_LOAD_ERROR", urgency="REVIEW",
                    reason="文档知识库不可读，未用其产生研究结论。", limitations=[str(exc)]))
        if self.literature_principles:
            recommendations.append(_advice("STRATEGIC_RESEARCH_ADVICE", type="LITERATURE_TUNING_GROUNDING", urgency="INFO",
                reason=f"加载了 {len(self.literature_principles)} 条文献记录；引用与适用范围需逐条审查。",
                sample_guideline=self.literature_principles[0].get("title", ""),
                limitations=["文献条件和历史启发不构成当前项目因果诊断或收益保证。"],
                evidence=[{"kind": "literature_record", "id": p.get("id"), "paper": p.get("paper"),
                           "sources": p.get("sources", [])} for p in self.literature_principles]))
        if self.literature_load_errors:
            recommendations.append(_advice("STRATEGIC_RESEARCH_ADVICE", type="LITERATURE_LOAD_ERROR", urgency="REVIEW",
                reason="文献库格式或读取失败；未加载替代库。", limitations=self.literature_load_errors))
        for recommendation in recommendations:
            recommendation["assurance"] = "HEURISTIC_ONLY"
        return recommendations

    def _review_loop_history(self, state, context, search):
        """Filter unchanged rejected routes; checkpoints record choices, not scientific truth."""
        decision = context.get("decision", {})
        if not (isinstance(decision, dict) and _text(decision.get("id"))
                and _text(decision.get("goal_revision")) and isinstance(decision.get("scope"), dict)):
            return None
        _scope(decision["scope"])
        evidence = context.get("facts", {})
        _require(isinstance(evidence, dict), "Loop evidence must be context facts")
        _json(evidence)
        # Identical acceptance predicates and revision identify the same goal across renamed questions.
        goal_key = _goal_key(decision.get("goal_conditions"))
        directory = self.root_dir / ".rds"
        kind = "project" if (directory / "project.sqlite3").is_file() else "reference"
        path = directory / ("project.sqlite3" if kind == "project" else "state.sqlite3")
        if not path.is_file():
            return None
        from rds_checkpoints import SCHEMA, MAX_BYTES as checkpoint_cap, _sha
        review = {"assurance": "RECORDED_INPUT_NOT_SCIENTIFIC_VERIFICATION", "flags": [],
                  "authorization": "UNCHANGED", "limitations": [
                      "Checkpoint hashes authenticate recorded choices, not scientific validity or execution admission.",
                      "Changed facts or conditions reopen review; source labels do not independently verify new evidence."]}
        history = []
        checked_witnesses = set()
        skipped_goal_records = []

        def decision_row(prior, checkpoint_id, sha, same_question, same_goal, records, root):
            row = {**_checkpoint_route(prior, checkpoint_id, records, checked_witnesses, same_question),
                   "checkpoint_id": checkpoint_id, "checkpoint_sha256": sha,
                   "same_question": same_question, "same_goal": same_goal}
            if root is not None:
                row["root"] = root  # A record read from a predecessor root through its successor chain (#178).
            return row

        def collect(db, records, contract_sha, rows, skipped, root=None, pinned=None, contracts=None):
            seen = set()
            for checkpoint_id, sha, raw in db.execute("SELECT id,sha,body FROM checkpoints ORDER BY rowid"):
                if pinned is not None:
                    if checkpoint_id not in pinned:
                        continue  # Records a predecessor gained after it was superseded are not part of the chain.
                    # The successor's pin, not the row's own hash, decides which bytes the chain admits (#178).
                    _require(sha == pinned[checkpoint_id], "Predecessor checkpoint differs from the pinned digest: " + checkpoint_id)
                    seen.add(checkpoint_id)
                _require(isinstance(raw, str) and len(raw.encode("utf-8")) <= checkpoint_cap
                         and hashlib.sha256(raw.encode("utf-8")).hexdigest() == sha, "Checkpoint integrity failure: " + checkpoint_id)
                record = strict_json(raw)
                _require(record.get("id") == checkpoint_id and record.get("schema") == SCHEMA
                         and record.get("kind") == kind and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", checkpoint_id),
                         "Checkpoint identity mismatch: " + checkpoint_id)
                recorded_contract_sha = record.get("contract_sha256")
                allowed_contracts = contracts if contracts is not None else {contract_sha: None}
                _require(recorded_contract_sha in allowed_contracts
                         and _sha(record["snapshot"]["contract"]) == recorded_contract_sha
                         and (allowed_contracts[recorded_contract_sha] is None
                              or record["snapshot"]["contract"] == allowed_contracts[recorded_contract_sha]),
                         "Checkpoint contract mismatch: " + checkpoint_id)
                prior = record.get("decision", {})
                if not isinstance(prior, dict):
                    continue
                same_question = prior.get("question_id") == decision["id"]
                same_goal = (goal_key is not None and prior.get("goal_revision") == decision["goal_revision"]
                             and _text(prior.get("question_id")) and _goal_key(prior.get("goal_conditions")) == goal_key)
                if not (same_question or same_goal):
                    continue
                if not all(key in prior for key in CHECKPOINT_ROUTE_FIELDS):
                    continue  # Older opaque contexts never become route decisions.
                if same_question:
                    rows.append(decision_row(prior, checkpoint_id, sha, True, same_goal, records, root))
                    continue
                try:  # Another question's record adds history; it was never part of this question's review.
                    rows.append(decision_row(prior, checkpoint_id, sha, False, True, records, root))
                except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
                    skipped.append(checkpoint_id)
            if pinned is not None:
                _require(seen == set(pinned), "Pinned predecessor checkpoint is missing: "
                         + ", ".join(sorted(set(pinned) - seen)[:3]))

        def connect(database):
            db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=0.05)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            return db

        try:
            db = connect(path)
            try:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone():
                    db.close()
                    db = None
                elif kind == "project":
                    from rds_method_revision import contract_history
                    lineage = contract_history(db)
                    contract, contract_sha = lineage[-1]['contract'], lineage[-1]['sha256']
                    checkpoint_contracts = {entry['sha256']: entry['contract'] for entry in lineage}
                else:
                    live = strict_json(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
                    contract, contract_sha = live["contract"], live["contract_sha256"]
                if db is not None:
                    _require(_sha(contract) == contract_sha and state.get("contract_sha256") == contract_sha
                             and state.get("contract") == contract, "Live contract integrity or scope mismatch")
                chain = []
                if kind == "project":
                    from rds_project import PREDECESSOR_DEPTH, ProjectStore
                    depth = context.get("predecessor_depth", PREDECESSOR_DEPTH)
                    chain = ProjectStore(self.root_dir).predecessor_chain(depth)
                unverified = "Records at and beyond this root were not read; this root's own history is unaffected."
                for hop in chain:
                    if hop["status"] == "MISMATCH":
                        review["flags"].append({"kind": "PREDECESSOR_CHAIN_UNVERIFIED", "root": hop["root"], "reason": hop["reason"],
                                                "effect": unverified})
                    elif hop["status"] == "TRUNCATED":
                        review["limitations"].append(f"PREDECESSOR_CHAIN_TRUNCATED: roots from {hop['root']} back were not read "
                                                     f"({hop['reason']}).")
                inherited = []
                for hop in (hop for hop in chain if hop["status"] == "VERIFIED"):  # Newest predecessor first.
                    # Each hop is re-read in its own transaction and must still match the pins the chain check used;
                    # a hop that fails here is dropped with every older root, as a MISMATCH in the chain would be.
                    rows, skipped = [], []
                    try:
                        root = Path(hop["root"])
                        other = connect(root / ".rds" / "project.sqlite3")
                        try:
                            row = other.execute("SELECT body,sha256 FROM contract WHERE id=1").fetchone()
                            _require(row is not None and row[1] == hop["contract_sha256"]
                                     and _sha(strict_json(row[0])) == row[1], "Predecessor contract integrity failure: " + hop["root"])
                            pinned = {item["id"]: item["sha256"] for item in hop["checkpoint_shas"]}
                            if pinned:
                                collect(other, root / ".rds", row[1], rows, skipped, hop["root"], pinned)
                        finally:
                            other.close()
                    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError,
                            RecursionError) as exc:
                        review["flags"].append({"kind": "PREDECESSOR_CHAIN_UNVERIFIED", "root": hop["root"], "reason": str(exc),
                                                "effect": unverified})
                        break
                    inherited.append((rows, skipped))
                if inherited:
                    review["limitations"].append(
                        f"History includes pinned records from {len(inherited)} predecessor root(s); they are recorded input "
                        "of earlier frozen contracts, not scientific verification.")
                for rows, skipped in reversed(inherited):  # Oldest root first, so a later decision still wins.
                    history.extend(rows)
                    skipped_goal_records.extend(skipped)
                if db is not None:
                    collect(db, directory, contract_sha, history, skipped_goal_records,
                            contracts=checkpoint_contracts if kind == "project" else None)
                elif not inherited and not review["flags"]:
                    return None
            finally:
                if db is not None:
                    db.close()
        except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError, RecursionError) as exc:
            review["flags"].append({"kind": "LOOP_HISTORY_REVIEW_ERROR", "reason": str(exc)})
            review["status"] = "REVIEW_REQUIRED"
            search["loop_review"] = review
            return review  # No partial or corrupt history has pruning authority.
        recorded = history
        if skipped_goal_records:  # Partial same-goal history neither prunes routes nor prompts a jump.
            recorded = [row for row in recorded if row["same_question"]]
            review["flags"].append({"kind": "GOAL_HISTORY_SKIPPED", "count": len(skipped_goal_records),
                "checkpoint_ids": skipped_goal_records[-5:],
                "reason": "Another question's malformed record left same-goal history incomplete; only this question's records were used."})
        history = [row for row in recorded if row["same_question"]]
        current_scope = (decision["goal_revision"], _json(decision["scope"]))
        scoped = [row for row in history if (row["goal_revision"], _json(row["scope"])) == current_scope]
        # This question's and same-goal records in checkpoint order: the latest applicable decision wins.
        applicable = [row for row in recorded if (row["same_question"] or row["same_goal"])
                      and (row["goal_revision"], _json(row["scope"])) == current_scope]
        choices = []
        for row in scoped:
            if not choices or choices[-1]["route_sha256"] != row["route_sha256"]:
                choices.append(row)
            else:
                choices[-1] = row
        if len(choices) >= 3 and choices[-3]["route_sha256"] == choices[-1]["route_sha256"]:
            review["flags"].append({"kind": "DECISION_OSCILLATION", "question_id": decision["id"],
                "checkpoint_ids": [row["checkpoint_id"] for row in choices[-3:]],
                "route_sha256": [row["route_sha256"] for row in choices[-3:]]})
            if any("root" in row for row in choices[-3:]):
                review["flags"][-1]["roots"] = [row.get("root") for row in choices[-3:]]

        def filter_search(output):
            for key in ("experiment_composition", "rule_search"):
                if isinstance(output.get(key), dict):
                    filter_search(output[key])
            kept = []
            for candidate in output.get("candidates", []):
                route = _loop_route(candidate)
                # A renamed question with the same goal does not reset its routes, and an older decision under
                # either question never overrides a later one.
                prior = next((row for row in reversed(applicable) if row["route_sha256"] == route),
                    next((row for row in reversed(recorded) if row["route_sha256"] == route), None))
                domain_match = False
                if prior is None:
                    from rds_guard import in_domain, same_family
                    prior = next((row for row in reversed(scoped) if row['outcome'] == 'rejected'
                        and 'rejected_domain' in row and same_family(row['candidate'], candidate, row['rejected_domain']['parameters'])
                        and in_domain(row['rejected_domain']['parameters'], candidate.get('action', {}).get('parameters', {}))), None)
                    domain_match = prior is not None
                if prior is None or prior["outcome"] != "rejected":
                    kept.append(candidate)
                    continue
                changed = ((prior["goal_revision"], _json(prior["scope"])) != current_scope
                           or prior["review_context"] != _loop_context(candidate, evidence))
                repeat_kind = 'REPEAT_DECLARED_REJECTED_DOMAIN' if domain_match else 'REPEAT_REJECTED_ROUTE'
                flag = {"kind": "REOPEN_REVIEW" if changed else repeat_kind, "candidate_id": candidate.get("id"),
                        "route_sha256": route, "checkpoint_id": prior["checkpoint_id"], "checkpoint_sha256": prior["checkpoint_sha256"]}
                if domain_match:
                    flag['witness_sha256'] = prior['falsification']['sha256']
                if prior["question_id"] != decision["id"]:
                    flag["recorded_question_id"] = prior["question_id"]
                if "root" in prior:
                    flag["root"] = prior["root"]
                if flag not in review["flags"]:
                    review["flags"].append(flag)
                if changed:
                    kept.append({**candidate, "loop_review": flag})
                else:
                    output.setdefault("blocked_candidates", []).append({**candidate, "status": 'BLOCKED_DECLARED_DOMAIN' if domain_match else "BLOCKED_REJECTED_ROUTE", "loop_review": flag})
            output["candidates"] = kept
            kept_ids = {row.get("id") for row in kept}
            ranking = output.get("ranking", {})
            if "cost_unknown" in ranking:
                ranking["cost_unknown"] = [candidate_id for candidate_id in ranking["cost_unknown"] if candidate_id in kept_ids]
            if "dominance" in ranking:
                ranking["dominance"] = [row for row in ranking["dominance"] if row["better"] in kept_ids and row["worse"] in kept_ids]
            for candidate in kept:
                if "dominated_by" in candidate:
                    candidate["dominated_by"] = [candidate_id for candidate_id in candidate["dominated_by"] if candidate_id in kept_ids]
            if "pareto_front" in ranking:
                ranking["pareto_front"] = [row["id"] for row in kept if not row.get("dominated_by")]
            if "queries" in output:
                used = {step.get("id") for candidate in kept for step in candidate.get("steps", [])}
                output["queries"] = [row for row in output["queries"] if row.get("id") in used]

        filter_search(search)
        for flag in review["flags"]:
            if flag["kind"] == "DECISION_OSCILLATION":
                flag["candidate_ids"] = [c["id"] for c in search.get("candidates", [])
                    if c.get("status") == "READY" and _loop_route(c) in flag["route_sha256"]]
        latest = {}
        for row in applicable:
            if row["same_goal"]:  # The latest choice for a route in this scope supersedes earlier outcomes.
                latest.pop(row["route_sha256"], None)
                latest[row["route_sha256"]] = row
        rejected = [row for row in latest.values() if row["outcome"] == "rejected" and row["family_sha256"] is not None]
        variants, related = [], {}
        for candidate in search.get("candidates", []):
            family, route = _loop_family(candidate), _loop_route(candidate)
            if candidate.get("status") != "READY" or family is None:
                continue
            rows = [row for row in rejected if row["family_sha256"] == family and row["route_sha256"] != route]
            if rows:  # Changed relevant evidence reopens review, as for exact repeats.
                context = _loop_context(candidate, evidence)
                rows = [row for row in rows if row["review_context"] == context]
            if rows:
                variants.append(candidate.get("id"))
                related.update(((row.get("root"), row["checkpoint_id"]), row) for row in rows)
        if related:
            # Parameter-only variants of routes rejected for this goal; not a capacity bound or a guilty premise.
            order = {(row.get("root"), row["checkpoint_id"]): index for index, row in enumerate(recorded)}
            related = sorted(related.values(), key=lambda row: order[(row.get("root"), row["checkpoint_id"])])
            review["flags"].append({"kind": "GOAL_ROUTES_REJECTED", "goal_revision": decision["goal_revision"],
                "rejected_routes": len(related), "candidate_ids": variants,
                "question_ids": list(dict.fromkeys(row["question_id"] for row in related))[-8:],
                "checkpoint_ids": [row["checkpoint_id"] for row in related][-5:]})
        review["status"] = "REVIEW_REQUIRED" if review["flags"] else "RECORDED_HISTORY_REVIEWED"
        search["loop_review"] = review
        return review


CHECKPOINT_ROUTE_FIELDS = ("goal_revision", "scope", "candidate", "outcome", "evidence")


def _checkpoint_route(prior, checkpoint_id, directory, checked_witnesses, same_question):
    """Structural checks loop-history review applies to one recorded route decision."""
    _require(_text(prior["goal_revision"]) and prior["outcome"] in
             ("rejected", "accepted", "plan_locked", "deferred") and isinstance(prior["evidence"], dict),
             "Invalid checkpoint decision: " + checkpoint_id)
    _scope(prior["scope"])
    _json(prior["evidence"])
    route = _loop_route(prior["candidate"])
    _require(route is not None, "Checkpoint decision needs a structured candidate: " + checkpoint_id)
    # Declared domains prune only within their own question, so only that question checks the witness.
    if same_question and prior['outcome'] == 'rejected' and 'rejected_domain' in prior:
        from rds_guard import validate_domain, MAX_BYTES
        validate_domain(prior['rejected_domain'], prior['candidate'])
        witness = prior.get('falsification', {})
        witness_sha = witness.get('sha256')
        _require(isinstance(witness_sha, str) and re.fullmatch('[0-9a-f]{64}', witness_sha), 'Declared-domain rejection needs a witness hash')
        original = Path(witness['path']).resolve()
        _require(original.is_relative_to((directory / 'cas').resolve()), 'Domain witness must be in the project CAS')
        if (str(original), witness_sha) not in checked_witnesses:
            with original.open('rb') as handle:
                witness_raw = handle.read(MAX_BYTES + 1)
            _require(len(witness_raw) <= MAX_BYTES and hashlib.sha256(witness_raw).hexdigest() == witness_sha,
                     'Declared-domain witness CAS integrity failure')
            checked_witnesses.add((str(original), witness_sha))
    return {**prior, "route_sha256": route, "family_sha256": _loop_family(prior["candidate"]),
            "review_context": _loop_context(prior["candidate"], prior["evidence"])}


def validate_checkpoint_decision(decision, checkpoint_id, directory):
    """Refuse at save time a route record that its question's loop-history review would always refuse."""
    if not (isinstance(decision, dict) and _text(decision.get("question_id"))
            and all(key in decision for key in CHECKPOINT_ROUTE_FIELDS)):
        return  # Opaque contexts are never read as route decisions.
    try:
        _checkpoint_route(decision, checkpoint_id, Path(directory), set(), True)
        if decision["outcome"] == "rejected" and "rejected_domain" in decision:
            # Review resolves the witness from its own working directory; only an absolute path stays valid.
            _require(Path(decision["falsification"]["path"]).is_absolute(), "Domain witness path must be absolute")
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, OSError) as exc:
        raise ValueError("Checkpoint decision for question " + json.dumps(decision["question_id"][:80], ensure_ascii=False)
                         + " would block loop-history review: "
                         + str(exc) + ". Record the structured candidate from the Advisor output, "
                         "or save the note without the route fields (" + ", ".join(CHECKPOINT_ROUTE_FIELDS) + ")") from exc


def _loop_route(candidate):
    """Exact structured route equality; display IDs never establish route identity."""
    if not isinstance(candidate, dict):
        return None
    action = candidate.get("action")
    if isinstance(action, dict) and isinstance(action.get("description"), str) and action["description"].strip():
        display = {"id", "goal_contribution"}
        if any(key in action for key in ("target", "intervention", "parameters", "operation")):
            display.update(("description", "question", "competing_explanations", "required_observables", "outcomes", "discrimination", "stop_condition"))
        route = {"action": {key: value for key, value in action.items() if key not in display}}
    elif isinstance(candidate.get("interventions"), list) and candidate["interventions"] and isinstance(candidate.get("control_bindings"), list):
        route = {"interventions": sorted(candidate["interventions"], key=_json),
                 "controls": sorted([{key: value for key, value in control.items() if key not in
                     ("source", "reason", "truth", "evidence_status")} for control in candidate["control_bindings"]], key=_json)}
    else:
        return None
    return hashlib.sha256(_json(route).encode("utf-8")).hexdigest()


def _loop_family(candidate):
    """Route identity without action parameters: a parameter-only variant shares its family."""
    action = candidate.get("action") if isinstance(candidate, dict) else None
    # Without a structured target, intervention or operation, the description is part of route identity.
    if (not isinstance(action, dict) or not isinstance(action.get("parameters", {}), dict)
            or not any(key in action for key in ("target", "intervention", "operation"))):
        return None
    return _loop_route({**candidate, "action": {**action, "parameters": {}}})


def _goal_key(goals):
    """Order-insensitive identity of explicit goal predicates; None when absent or malformed."""
    if not (isinstance(goals, list) and 1 <= len(goals) <= 32 and all(isinstance(goal, dict) for goal in goals)):
        return None
    try:
        return _json(sorted(_json(goal) for goal in goals))
    except (ValueError, TypeError, RecursionError):
        return None


def _loop_context(candidate, evidence):
    """Only declared dependencies and review conditions can reopen a route."""
    derivation, action = candidate.get("derivation", []), candidate.get("action", {})
    _require(isinstance(derivation, list) and all(isinstance(row, dict) for row in derivation), "Invalid recorded candidate derivation")
    traces = [item for row in derivation if row.get("step") == "rule_to_template" for item in row.get("trace", [])]
    _require(all(isinstance(row, dict) for row in traces), "Invalid recorded rule trace")
    reports = [row for row in derivation + traces if row.get("step") == "fact_to_rule"]
    reports += candidate.get("facts", []) + candidate.get("control_bindings", []) + candidate.get("observables", [])
    reports += candidate.get("discrimination", {}).get("conditions", [])
    _require(all(isinstance(row, dict) for row in reports), "Invalid recorded candidate predicates")
    observable_names = action.get("required_observables", candidate.get("required_observables", []))
    names = {row["fact"] for row in reports if isinstance(row.get("fact"), str)}
    names.update(name for name in observable_names if isinstance(name, str) and name in evidence)
    selected = {}
    for name in names:
        fact = evidence.get(name, {})
        if isinstance(fact, dict):
            source = fact.get("source", {})
            selected[name] = {"value": fact.get("value"), "binding": fact.get("binding", {}),
                "reliable": fact.get("reliable") is not False and fact.get("reliability") not in ("UNRELIABLE", "UNKNOWN"),
                "source_sha256": source.get("sha256") if isinstance(source, dict) else None}
        else:
            selected[name] = fact
    predicates = sorted([{key: row[key] for key in ("fact", "operator", "expected", "role") if key in row}
                         for row in reports], key=_json)
    return _json({"facts": selected, "conditions": predicates, "observables": observable_names,
        "outcomes": action.get("outcomes", candidate.get("result_to_decision", [])),
        "stop": action.get("stop_condition", candidate.get("stop_conditions", []))})


# Bounded structured inputs for scoped ledger decisions.
MAX_LOOP_BYTES = 256 * 1024


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 512


def _json(value, cap=MAX_LOOP_BYTES):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
        size = len(raw.encode("utf-8"))
        _require(size <= cap, f"Advisor loop JSON exceeds byte limit ({size} > {cap}); use a scoped context and shared manifest digest/source locator while retaining full originals. For repeated code file-hash maps: scripts/rds_context_compact.py --input CONTEXT --output-dir NEW_DIRECTORY")
        strict_json(raw)
        return raw
    except (TypeError, RecursionError, UnicodeError, OverflowError) as exc:
        raise ValueError("Advisor loop input must be finite, bounded JSON") from exc


def _scope(value):
    _require(isinstance(value, dict), f"scope/parameters must be a JSON object (dict); got {type(value).__name__}")
    _require(len(value) <= 16, f"scope/parameters has {len(value)} fields; at most 16 are allowed")
    for key, child in value.items():
        _require(_text(key), "scope/parameters field names must be non-empty strings of at most 512 characters")
        if child is not None and type(child) not in (str, int, float, bool):
            field = json.dumps(key[:80], ensure_ascii=True) + ("..." if len(key) > 80 else "")
            kind = "array" if isinstance(child, list) else "object" if isinstance(child, dict) else None
            actual = f"{kind} ({type(child).__name__})" if kind else type(child).__name__
            raise ValueError(f"scope/parameters field {field} must be a JSON atom; got {actual}. "
                "Keep the structured original input in a source file and reference it with an explicit source binding.")
    _json(value, 2048)

"""Sequential real-model Bare/RDS feasibility experiment; no production wiring.

Transcript files are audit outputs, not an execution ledger. RDS execution uses
the original CLI/ProjectStore/CAS path. Paid requests are never auto-retried.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def commands(message):
    actions = message.get("tool_calls", [])
    if not 1 <= len(actions) <= 8 or len({a["id"] for a in actions}) != len(actions):
        raise ValueError("Function batch must contain 1..8 distinct calls")
    return [(a["id"], {**json.loads(a["function"]["arguments"]), "tool": a["function"]["name"]})
            for a in actions]


def bound_artifacts(job, receipt, output):
    """Read only contained files whose original receipt hashes and sizes match."""
    values = {}
    for item in receipt["artifacts"]:
        if item["kind"] not in {"stdout.bin", "stderr.bin"} and item["path"] != output:
            continue
        path = (job / item["path"]).resolve()
        if not path.is_relative_to(job.resolve()):
            raise ValueError("Receipt artifact escapes original job")
        raw = path.read_bytes()
        if len(raw) != item["size"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("Original receipt artifact is missing or tampered")
        values[item["kind"]] = raw
    if not {"stdout.bin", "stderr.bin"} <= values.keys():
        raise ValueError("Original process stream bindings are missing")
    return values


def execute(root, arm, cli, code, attempt):
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    for retained in root.glob("tool-*.json"):
        prior = json.loads(retained.read_text(encoding="utf-8"))
        if prior["code_sha256"] == code_hash:
            return {**prior, "reused_original_attempt": True}
    code_path, out = root / f"analysis-{attempt}.py", f"out/result-{attempt}.json"
    code_path.write_text(code, encoding="utf-8")
    argv = [sys.executable, "-B", "analysis_worker.py", code_path.name, out]
    if arm == "RDS":
        argv = [sys.executable, "-B", str(cli), "--root", str(root), "exec", "--name", f"analysis-{attempt}",
                "--timeout", "30", "--output", out, "--bind", "code=" + code_path.name,
                "--json", *[v for p in sorted(root.glob("*.csv")) for v in ("--bind", "data=" + p.name)], "--", *argv]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "RDS_USAGE_DB": str(root / ".rds/usage/cli.sqlite3")}
    started = time.monotonic()
    try:
        result = subprocess.run(argv, cwd=root, env=env, capture_output=True, timeout=50 if arm == "RDS" else 30)
        report = {"status": "FINISHED", "exit_code": result.returncode,
                  "stdout": result.stdout.decode("utf-8", errors="replace"),
                  "stderr": result.stderr.decode("utf-8", errors="replace")}
    except subprocess.TimeoutExpired as exc:
        # Original RDS runner owns settlement; outer timeout is UNKNOWN and no retry.
        report = {"status": "TIMEOUT_UNKNOWN", "exit_code": None,
                  "stdout": (exc.stdout or b"").decode("utf-8", errors="replace"),
                  "stderr": (exc.stderr or b"").decode("utf-8", errors="replace")}
    report.update(attempt=attempt, argv=argv, code_sha256=sha(code_path),
                  wall_seconds=time.monotonic() - started, cost_status="UNKNOWN")
    if arm == "RDS" and report["status"] == "FINISHED":
        quick = json.loads(report["stdout"])
        if "receipt" in quick:
            job = Path(quick["job_root"]).resolve()
            if not job.is_relative_to((root / ".rds/exec").resolve()):
                raise ValueError("RDS returned an unrelated job root")
            sys.path.insert(0, str(cli.parent))
            from rds_project import ProjectStore
            store = ProjectStore(job)
            with store._db(True) as db:
                row = db.execute("SELECT run_id,sha256,body FROM receipts WHERE run_id=?",
                                 (quick["receipt"]["run_id"],)).fetchone()
                receipt = store._receipt(row)
            if receipt["sha256"] != quick["receipt"]["sha256"]:
                raise ValueError("CLI receipt differs from original retained receipt")
            artifacts = bound_artifacts(job, receipt, out)
            report["cli_stdout"] = report["stdout"]
            report["stdout"] = artifacts["stdout.bin"].decode("utf-8", errors="replace")
            report["stderr"] += artifacts["stderr.bin"].decode("utf-8", errors="replace")
            report["rds"] = {k: receipt[k] for k in ("run_id", "attempt_id", "run_status", "sha256", "errors")}
            if "project_output" in artifacts:
                report["artifact"] = json.loads(artifacts["project_output"])
    if (root / out).is_file():
        report["artifact"] = json.loads((root / out).read_text(encoding="utf-8"))
        report["artifact_sha256"] = sha(root / out)
    save(root / f"tool-{attempt}.json", report)
    return report


def run(args):
    root, cli = Path(args.workspace).resolve(), Path(args.cli).resolve()
    if root.exists():
        raise ValueError("Use a new experiment root; never overwrite prior attempts")
    root.mkdir(parents=True)
    source = Path(args.inputs).resolve()
    inputs = {p.name: sha(p) for p in sorted(source.glob("*.csv"))}
    if not inputs:
        raise ValueError("No declared CSV inputs")
    task = (source / "task.json").read_text(encoding="utf-8")
    if not 2 <= args.max_calls <= 1040 or args.max_calls % 2:
        raise ValueError("Matched arms require an even remaining call budget within 2..1040")
    deadline = min(args.deadline or time.time() + 2700, time.time() + 2700)
    frozen = {"model": args.model, "model_weights": "UNKNOWN", "inputs": inputs,
              "task_sha256": sha(source / "task.json"), "cli_sha256": sha(cli),
              "rds_source": {str(p.relative_to(cli.parent)): sha(p) for p in cli.parent.rglob("*.py")},
              "worker_sha256": sha(Path(__file__).with_name("analysis_worker.py")),
              "loop_sha256": sha(__file__), "response_model": args.response_model,
              "max_calls": args.max_calls, "deadline_epoch": deadline,
              "total_wall_seconds": 2700, "per_arm_calls": args.max_calls // 2,
              "arms": ["Bare", "RDS"], "tool_timeout_seconds": 30,
              "comparison_scope": "RDS_EXEC_ONLY", "scientific_benefit": "UNKNOWN"}
    save(root / "frozen.json", frozen)
    started, calls, reports = time.monotonic(), 0, []
    for arm in frozen["arms"]:
        folder = root / arm
        folder.mkdir()
        for name in inputs:
            shutil.copyfile(source / name, folder / name)
        shutil.copyfile(Path(__file__).with_name("analysis_worker.py"), folder / "analysis_worker.py")
        tools = [{"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": {field: {"type": "string"}},
                           "required": [field], "additionalProperties": False}}}
            for name, field, desc in (("read", "file", "Preview one declared CSV file"),
                ("execute", "code", "Run CSV analysis Python; no imports; emit one evidence object"),
                ("finish", "answer", "Finish with an evidence-backed answer and limitations"))]
        instruction = ("You are a data analysis research agent. Solve the given real task with measured CSV evidence. "
            "Use the provided function tools; multiple calls are executed serially. Do not write tool markup or JSON as text. "
            "The execute tool provides csv,json,math,statistics,read(name),emit(dict) "
            "and ordinary numeric/list builtins. Only csv/json/math/statistics imports are permitted. Emit exactly one evidence JSON object. "
            f"read(name) returns UTF-8 CSV TEXT, not rows. Code has a 30s wall limit; {frozen['per_arm_calls']} model calls available. Keep failures and revise methods. "
            "Do not fabricate numerical findings. Files: " + json.dumps(inputs) + ". Task: " + task)
        if arm == "RDS":
            instruction += (" Your execute tool is the actual frozen RDS exec route: code/data binding, "
                "bounded admitted execution and retained operational receipts. Read its failure/UNKNOWN evidence; "
                "operational success is not scientific support. No automatic uncertain retry.")
        messages = [{"role": "system", "content": instruction}]
        attempts, reason, final, arm_start_calls = 0, "CALL_CAP", None, calls
        for turn in range(frozen["per_arm_calls"]):
            remaining = deadline - time.time()
            if remaining <= 0 or calls >= args.max_calls:
                reason = "TOTAL_CAP"
                break
            calls += 1
            request = {"model": args.model, "messages": messages, "tools": tools, "max_tokens": 3000}
            save(folder / f"request-{turn}.json", request)
            began = time.monotonic()
            try:
                req = urllib.request.Request(args.endpoint.rstrip("/") + "/chat/completions",
                    data=json.dumps(request).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer magpie"})
                with urllib.request.urlopen(req, timeout=min(120, remaining)) as response:
                    raw = response.read()
                (folder / f"response-{turn}.json").write_bytes(raw)
                response = json.loads(raw)
                if "deepseek" not in response.get("model", "").lower():
                    raise ValueError("Response model identity is missing or differs from DeepSeek")
                if frozen["response_model"] != response["model"]:
                    raise ValueError("Frozen resolved model identity changed")
                message = response["choices"][0]["message"]
                messages.append(message)
                batch = commands(message)
            except Exception as exc:
                save(folder / f"failure-{turn}.json", {"type": type(exc).__name__, "error": str(exc),
                    "call": calls, "wall_seconds": time.monotonic() - began, "paid_cost": "UNKNOWN"})
                reason = "REQUEST_OR_PARSE_FAILURE_NO_RETRY"
                break
            for action_id, command in batch:
                tool = command.get("tool")
                if tool == "finish":
                    final, reason = command.get("answer"), "AGENT_FINISHED"
                    break
                try:
                    if tool == "read":
                        name = command["file"]
                        if name not in inputs:
                            raise ValueError("Undeclared input")
                        result = (folder / name).read_text(encoding="utf-8-sig")[:12000]
                    elif tool == "execute":
                        if deadline - time.time() < 50:
                            reason = "INSUFFICIENT_REMAINING_TOOL_BUDGET"
                            break
                        if arm == "RDS" and any(sha(cli.parent / p) != h for p, h in frozen["rds_source"].items()):
                            raise ValueError("Frozen RDS source changed")
                        attempts += 1
                        result = execute(folder, arm, cli, command["code"], attempts)
                        if result["status"] == "TIMEOUT_UNKNOWN":
                            reason = "UNCERTAIN_TOOL_NO_RETRY"
                            break
                    else:
                        raise ValueError("Unsupported tool")
                except (ValueError, KeyError) as exc:
                    result = {"tool_error": str(exc)}
                feedback = {k: v for k, v in result.items() if k != "cli_stdout"} if type(result) is dict else result
                messages.append({"role": "tool", "tool_call_id": action_id,
                                 "content": json.dumps(feedback, ensure_ascii=False)[:16000]})
            if reason != "CALL_CAP":
                break
        report = {"arm": arm, "stop_reason": reason, "final_answer": final, "attempts": attempts,
                  "calls": calls - arm_start_calls, "scientific_benefit": "UNKNOWN"}
        save(folder / "summary.json", report)
        reports.append(report)
        save(root / "summary.json", {"calls": calls, "wall_seconds": time.monotonic() - started, "arms": reports})
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("workspace", "inputs", "cli"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--model", default="group/deepseek-v4.1flash")
    parser.add_argument("--endpoint", default="http://127.0.0.1:3425/v1")
    parser.add_argument("--max-calls", type=int, default=40)
    parser.add_argument("--response-model", required=True, help="Exact resolved model ID from retained provider metadata")
    parser.add_argument("--deadline", type=float, help="Original experiment epoch deadline; recovery never resets it")
    sys.stdout.buffer.write(json.dumps(run(parser.parse_args()), ensure_ascii=False, indent=2).encode("utf-8"))

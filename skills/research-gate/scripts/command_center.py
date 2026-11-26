"""Render the daily Obsidian research dashboard from local project receipts."""

import argparse
from collections import Counter
from datetime import date, datetime
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time


PROJECTS = {
    "proj-alpha": "proj-alpha成果库",
    "proj-beta": "proj-beta成果库",
    "proj-gamma": "proj-gamma成果库",
}
PAGE = "00-今日科研指挥台.md"
OVERVIEW = "00-总览（先看这页）"
NIGHTLY = "proj-alpha成果库/90-自动生成/夜间任务状态"
CONTRIB = "proj-alpha成果库/90-自动生成/外部投稿合并建议"
LOCAL_GPU_FILE = "local_gpu_status.json"
FAILED = {"fail", "failed", "error", "失败"}


def read_text(path):
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ""


def read_json(path):
    try:
        value = json.loads(read_text(path))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def active_knowledge(data):
    counts = Counter()
    for path in data.glob("*.jsonl"):
        if path.name == "总览刷新记录.jsonl":
            continue
        for line in read_text(path).splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("status") == "active":
                counts[path.stem] += 1
    return counts


def link(path, label, *, table=False):
    return "[[" + path + (r"\|" if table else "|") + label + "]]"


def latest_pdfs(vault, library):
    latest = {}
    drafts = vault / library / "论文草稿"
    for pdf in drafts.rglob("*.pdf"):
        relative = pdf.relative_to(drafts)
        if not pdf.is_file() or any(part in {"_已并入大稿", "99-归档"} for part in relative.parts[:-1]):
            continue
        try:
            modified = pdf.stat().st_mtime_ns
        except OSError:
            continue
        suffix = re.search(r"[_-](zh|cn|en)$", pdf.stem, re.I)
        language = "ZH" if suffix and suffix[1].lower() in {"zh", "cn"} else "EN"
        candidate = modified, -len(relative.parts), relative.as_posix()
        if language not in latest or candidate > latest[language]:
            latest[language] = candidate
    return " · ".join(link(f"{library}/论文草稿/{name}", Path(name).stem, table=True)
                      for _, _, name in sorted(latest.values(), key=lambda entry: entry[2])) or "—"


def table_rows(text):
    for line in text.splitlines():
        if line.startswith("|") and line.endswith("|"):
            yield [value.strip() for value in line[1:-1].split("|")]


def project_status(root):
    if root is None:
        return "—", "", ""
    raw = read_text(root / "STATUS.md")
    block = re.search(r"<!-- AUTO:STATUS START[^>]*-->(.*?)<!-- AUTO:STATUS END[^>]*-->", raw, re.S)
    text = block[1] if block else ""
    running = re.search(r"(?m)^-\s*进行中[：:]\s*(.+)$", text)
    cards = [row for row in table_rows(text) if len(row) >= 3 and row[0].isdigit()]
    status = []
    if cards:
        card, _, verdict, *_ = cards[-1]
        status.append(f"{card} {verdict}")
    if running and running[1].strip() not in {"无", "—", "-"}:
        status.append(running[1].strip())
    jump = re.search(r"(?m)^-\s*(主线连续未过线[：:].*跳出复盘.*)$", text)
    return " · ".join(status) or "—", jump[1].strip() if jump else "", read_text(root / "ledger/COLAB_LIVE_BOARD.md")


def _resolve_dir(vault, root, raw):
    p = Path(raw)
    if p.is_absolute():
        return p
    if (vault / p).exists():
        return vault / p
    if root is not None and (root / p).exists():
        return root / p
    return vault / p


def paper_debt(vault, config, library):
    raw_dirs = []
    pdirs = config.get("paper_dirs")
    if isinstance(pdirs, list):
        raw_dirs.extend(d for d in pdirs if isinstance(d, str) and d.strip())
    if isinstance(config.get("paper_dir"), str) and config["paper_dir"].strip():
        raw_dirs.append(config["paper_dir"])
    root = (vault / config["root"]) if isinstance(config.get("root"), str) and config["root"].strip() else None
    if root is not None:
        raw_dirs.append(str(root))
    drafts = vault / library / "论文草稿"
    if drafts.is_dir():
        for latex_dir in sorted(drafts.glob("*/latex")):
            raw_dirs.append(str(latex_dir))

    seen, errs, sorries, tbds = set(), 0, 0, 0
    for raw in raw_dirs:
        d = _resolve_dir(vault, root, raw)
        for rep in sorted(d.glob("paper_gate_report*.md")):
            try:
                key = str(rep.resolve())
            except OSError:
                key = str(rep)
            if key in seen or not rep.is_file():
                continue
            seen.add(key)
            text = read_text(rep)
            m = re.search(r"errors\s+(\d+)\s*\|\s*(?:sorry\s+(\d+)\s*\|)?", text)
            if m:
                errs += int(m.group(1))
                if m.group(2):
                    sorries += int(m.group(2))
            p = re.search(r"(?m)^-\s*placeholder\s+(\d+)\s+allowed placeholder", text)
            if p:
                tbds += int(p.group(1))
    parts = [label for count, label in (
        (errs, f"err {errs}"), (sorries, f"sorry {sorries}"), (tbds, f"TBD {tbds}"),
    ) if count]
    return " · ".join(parts), errs


def local_gpu_banner(data):
    info = read_json(data / LOCAL_GPU_FILE)
    name = str(info.get("name") or "").strip()
    if not name:
        return ""
    status = str(info.get("status") or "ok").strip()
    summary = " ".join(str(info.get("summary") or "").split())[:48]
    return f"本地 GPU {name} {status}" + (f"（{summary}）" if summary else "")


def north_star_actions(vault, projects, today):
    prog_script = Path(__file__).with_name("program.py")
    if not prog_script.is_file():
        return []
    spec = importlib.util.spec_from_file_location("cc_program", prog_script)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return []
    actions = []
    for name, config in projects.items():
        if not isinstance(config, dict) or not isinstance(config.get("root"), str) or not config["root"].strip():
            continue
        prog_path = vault / config["root"] / "program.json"
        if not prog_path.is_file():
            continue
        try:
            agenda = mod.review(mod.load(prog_path), today=str(today))
        except Exception:
            continue
        if agenda.get("ready_to_compose"):
            actions.append(f"北极星可往回合（{name}）：" + "、".join(agenda["ready_to_compose"]))
        if agenda.get("paper_candidates"):
            actions.append(f"北极星可切新论文/并入大稿（{name}）：" + "、".join(agenda["paper_candidates"]))
    return actions


def colab_status(boards):
    candidates = []
    for board in boards:
        cu = re.search(r"(?im)^(?:Colab.*?[：:]\s*)?(?:剩余\s*)?`?(\d[\d,]*(?:\.\d+)?)\s*CU\b", board)
        active = re.search(r"(?i)(?:活跃实例\s*[：:]?\s*|Active:\s*)`?(\d+)", board)
        updated = re.search(r"(?im)^Updated:\s*`?(\d{4}-\d\d-\d\d[ T]\d\d:\d\d(?::\d\d)?)", board)
        if cu or active:
            candidates.append((updated[1].replace("T", " ") if updated else "",
                               cu[1] if cu else "—", active[1] if active else "—"))
    _, cu, active = max(candidates, default=("", "—", "—"))
    quota = f"{cu} CU" if cu != "—" else "—"
    return f"Colab 剩余 {quota} · 活跃实例 {active}"


def notify_windows(title, body):
    if sys.platform != "win32":
        return
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "''")
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
          "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
          f"$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{esc(title)}</text><text>{esc(body)}</text></binding></visual></toast>');"
          "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, timeout=15, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        pass


def query_gpu_brief():
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            return " ".join(r.stdout.strip().splitlines()[0].split())[:60]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def record_local_run(vault, name, status, summary="", *, duration_s=None, notify_user=True, today=None):
    data = vault / "知识库" / "_数据"
    data.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": name,
        "status": status,
        "summary": " ".join((summary or "").split())[:240],
        "gpu": query_gpu_brief(),
        "duration_s": round(duration_s, 1) if duration_s is not None else None,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    (data / LOCAL_GPU_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if notify_user:
        dur = f" ({payload['duration_s']}s)" if payload["duration_s"] is not None else ""
        notify_windows(f"本地任务 {status.upper()}：{name}{dur}", payload["summary"] or "已刷新今日科研指挥台")
    out = vault / PAGE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(vault, today=today or date.today()), encoding="utf-8", newline="\n")
    return out


def render(vault, *, today, doctor_json=None):
    data = vault / "知识库" / "_数据"
    projects = read_json(data / "项目.json")
    counts = active_knowledge(data)
    nightly = read_text(vault / (NIGHTLY + ".md"))
    contrib = read_text(vault / (CONTRIB + ".md"))
    doctor = read_json(doctor_json) if doctor_json else {}
    doctor_checks = doctor.get("checks", {})
    if not isinstance(doctor_checks, dict):
        doctor_checks = {}
    nightly_steps = {row[0]: row[1].lower() for row in table_rows(nightly) if len(row) >= 2}
    failures = [step for step, state in nightly_steps.items() if state in FAILED]
    if "有失败" in nightly and not failures:
        failures.append("夜间任务")
    failures.extend(f"doctor/{name}" for name, result in doctor_checks.items()
                    if isinstance(result, dict) and str(result.get("status")).lower() in FAILED)
    if doctor.get("ok") is False and not any(name.startswith("doctor/") for name in failures):
        failures.append("doctor")
    if any(status.strip() != "通过" for status in re.findall(r"(?m)^-\s*检查[：:](.+)$", contrib)):
        failures.append("外部投稿检查")

    sync = "有失败" if failures else "通过" if "全部通过" in nightly or doctor.get("ok") is True else "未记录"
    skills = doctor_checks.get("skills", {})
    if sync == "通过" and isinstance(skills, dict) and skills.get("status") == "WARN":
        sync = "待确认"
    if nightly:
        sync += " " + link(NIGHTLY, "夜间状态")

    ordered = list(PROJECTS) + sorted(k for k in projects if k not in PROJECTS)
    rows, boards, jumps, paper_errs = [], [], [], []
    for name in ordered:
        config = projects.get(name, {})
        if not isinstance(config, dict):
            config = {}
        library = config.get("library") or PROJECTS.get(name, f"{name}成果库")
        root = vault / config["root"] if config.get("root") else None
        status, jump, board = project_status(root)
        debt_badge, err_count = paper_debt(vault, config, library)
        if debt_badge:
            status = debt_badge if status == "—" else f"{status} · {debt_badge}"
        if err_count:
            paper_errs.append(f"{name}：{debt_badge}")
        home = link(f"{library}/{OVERVIEW}", library, table=True) if (vault / library).is_dir() else "—"
        rows.append(f"| {name} | {status.replace('|', '/')} | {latest_pdfs(vault, library)} | {home} |")
        boards.append(board)
        if jump:
            jumps.append(f"{name} {jump}")

    actions = []
    pending = re.search(r"共\s*(\d+)\s*份待处理", contrib)
    if pending and int(pending[1]):
        actions.append(f"审核 {pending[1]} 份待处理协作投稿（PR），见 {link(CONTRIB, '合并建议')}")
    if jumps:
        actions.append("；".join(jumps))
    if paper_errs:
        actions.append("处理论文门禁报错：" + "；".join(paper_errs))
    actions.extend(north_star_actions(vault, projects, today))
    if failures:
        failed = "、".join(dict.fromkeys(failures))
        actions.append(f"复核失败检查（{failed}）" + (f"，见 {link(NIGHTLY, '夜间状态')}" if nightly else ""))
    breakdown = "、".join(f"{name} {count}" for name, count in sorted(counts.items()))
    knowledge = f"知识库 {sum(counts.values())} 条有效记录" + (f"（{breakdown}）" if breakdown else "")
    gpu_line = local_gpu_banner(data)
    header_parts = [f"更新 {today}", f"同步 {sync}", knowledge, colab_status(boards)]
    if gpu_line:
        header_parts.append(gpu_line)
    return "\n".join([
        "# 今日科研指挥台", "",
        "> " + " · ".join(header_parts), "",
        "| 项目 | 进展 | 最新 PDF | 成果库 |",
        "|---|---|---|---|",
        *rows, "", "## 今日待拍板", "",
        *[f"- {action}" for action in (actions or ["暂无已记录的待拍板事项。"])],
    ]) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--doctor-json", type=Path)
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    parser.add_argument("--notify-run", help="record a local GPU run result and refresh the command center")
    parser.add_argument("--run-status", choices=("ok", "fail", "running"), default="ok")
    parser.add_argument("--run-summary", default="")
    parser.add_argument("--no-toast", action="store_true")
    parser.add_argument("--wrap", help="run a local command, record status/duration, notify, and refresh dashboard")
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.wrap:
        cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
        if not cmd:
            parser.error("--wrap requires a command after it")
        t0 = time.monotonic()
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        tail = " ".join(((proc.stdout or "") + " " + (proc.stderr or "")).split()[-25:])
        out = record_local_run(args.vault, args.wrap, "ok" if proc.returncode == 0 else "fail",
                               tail, duration_s=time.monotonic() - t0, notify_user=not args.no_toast, today=args.today)
        print(out)
        return proc.returncode
    if args.notify_run:
        out = record_local_run(args.vault, args.notify_run, args.run_status, args.run_summary,
                               notify_user=not args.no_toast, today=args.today)
        print(out)
        return 0
    out = args.out or args.vault / PAGE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(args.vault, today=args.today, doctor_json=args.doctor_json), encoding="utf-8", newline="\n")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

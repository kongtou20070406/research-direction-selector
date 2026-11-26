"""Weekly: let an editorial worker rewrite each project library's overview page from the project's sources.

Reads ``<vault>/知识库/_数据/项目.json``. A project is refreshed when it has ``library`` and ``sources``
and its overview ``<vault>/<library>/00-总览（先看这页）.md`` is hand-written (a page whose frontmatter
has ``generated_by:`` is maintained by its own project script and is skipped).

For each project:
  1. back up the overview to ``<library>/99-归档/总览备份/<date>.md`` and hash every file in the library;
  2. run ``codex exec -m <model>`` in the library folder with a fixed contract: read the sources
     (read-only), rewrite only the overview, keep every number traceable to a named source file,
     obey the project's ``constraints``;
  3. check: only the overview changed, it still exists and is non-trivial, no banned word, no
     constraint keyword that the project forbids (a constraint "不提 X" bans X). On failure restore
     the backup.

Results go to ``<vault>/知识库/_数据/总览刷新记录.jsonl`` and a Windows toast on any failure.

    python refresh_overviews.py --vault "/path/to/vault" [--only proj-beta] [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

OVERVIEW = "00-总览（先看这页）.md"
BANNED = ("彻底", "完美", "根治", "严丝合缝", "实锤", "打穿", "焊死", "全通了", "一针见血")
MODEL = "editorial-worker"
FORBID = re.compile(r"不(?:提|写)\s*([^（(，,；;]+?)(?:\s*(?:或|与|和)\s*([^（(，,；;]+?))?(?:实验|内容)?(?:[（(，,；;]|$)")


def contract(name: str, info: dict, library: Path) -> str:
    srcs = "\n".join(f"- `{s}`" for s in info["sources"])
    cons = "\n".join(f"- {c}" for c in info.get("constraints", [])) or "- 无"
    return f"""# 每周刷新：{name} 成果库总览（{MODEL}）

工作目录是 Obsidian 成果库 `{library}`。只重写本目录下的 `{OVERVIEW}`，让它反映项目的最新状态。

## 素材（只读）
{srcs}

先列出素材目录里最近 14 天改动过的文件，优先读这些；再读 README、大纲、审稿速览和论文门禁报告一类的总述文件。

## 写法
- 中文，结构保持：一段项目简介 → `## 当前进度` → `## 主要结果`（表格，带口径与出处列）→ `## 下一步` → `## 论文草稿`（若有）。
- 每个数字都必须能在你写明的出处文件里原样找到；找不到的数字不写。未测的写“待测”，不写预期数字。
- 保留现有页面里仍然成立的内容，只改过时的部分；不确定的不改。
- 文末加一行：`> 最近一次自动刷新：{_dt.date.today().isoformat()}，{MODEL} 依据上述素材。`
- 禁用词：{"、".join(BANNED)}。

## 本项目的约束
{cons}

## 不能做
- 不改 `{OVERVIEW}` 以外的任何文件，不新建文件，不删除文件。
- 不写任何素材目录。

最后一条消息用中文列出：改了哪些段落、引用了哪些出处文件、哪些内容因找不到出处而删去或未写。
"""


def tree_hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".obsidian" not in p.parts}


def forbidden_terms(constraints: list[str]) -> list[str]:
    out = []
    for c in constraints:
        for m in FORBID.finditer(c):
            out += [t.strip() for t in m.groups() if t and t.strip()]
    return out


def check(library: Path, before: dict, info: dict) -> list[str]:
    probs = []
    after = tree_hashes(library)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    backup_dir = "99-归档/总览备份/"
    stray = sorted(k for k in changed if k != OVERVIEW and not k.startswith(backup_dir))
    if stray:
        probs.append("改动了总览以外的文件：" + "、".join(stray[:5]))
    page = library / OVERVIEW
    text = page.read_text(encoding="utf-8") if page.is_file() else ""
    if len(text) < 200:
        probs.append("总览缺失或过短")
    probs += [f"出现禁用词：{w}" for w in BANNED if w in text]
    probs += [f"违反约束，出现：{t}" for t in forbidden_terms(info.get("constraints", [])) if t in text]
    return probs


def codex_runner(prompt: str, cwd: Path, report: Path, timeout: int) -> int:
    exe = shutil.which("codex") or "codex"  # npm installs codex.cmd on Windows; resolve it for CreateProcess
    cmd = [exe, "exec", "-m", MODEL, "-c", 'model_provider="magpie"', "-c", 'model_reasoning_effort="high"',
           "-s", "workspace-write", "-C", str(cwd), "--skip-git-repo-check", "-o", str(report), "-"]
    try:
        p = subprocess.run(cmd, input=prompt, text=True, encoding="utf-8", errors="replace", capture_output=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124
    except OSError as exc:
        report.write_text(f"could not start codex: {exc}\n", encoding="utf-8")
        return 127
    if p.returncode != 0 and not report.exists():
        report.write_text((p.stderr or p.stdout)[-4000:], encoding="utf-8")
    return p.returncode


def refresh(vault: Path, only: str | None = None, dry_run: bool = False, runner=codex_runner,
            timeout: int = 2400, today: str | None = None) -> list[dict]:
    today = today or _dt.date.today().isoformat()
    data = vault / "知识库" / "_数据"
    projects = json.loads((data / "项目.json").read_text(encoding="utf-8"))
    results = []
    for name, info in projects.items():
        if only and name != only:
            continue
        library = vault / info.get("library", "")
        page = library / OVERVIEW
        if not info.get("library") or not info.get("sources") or not page.is_file():
            results.append({"project": name, "state": "skipped", "reason": "no library/sources/overview"})
            continue
        if "generated_by:" in page.read_text(encoding="utf-8")[:600]:
            results.append({"project": name, "state": "skipped", "reason": "overview is generated by its project"})
            continue
        prompt = contract(name, info, library)
        if dry_run:
            results.append({"project": name, "state": "dry-run", "prompt_chars": len(prompt)})
            continue
        backup = library / "99-归档" / "总览备份" / f"{today}.md"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(page, backup)
        before = tree_hashes(library)
        report = data / "总览刷新报告" / f"{name}_{today}.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        rc = runner(prompt, library, report, timeout)
        probs = check(library, before, info) if rc == 0 else [f"codex exit {rc}"]
        if probs:
            shutil.copy2(backup, page)
        results.append({"project": name, "state": "refreshed" if not probs else "restored",
                        "problems": probs, "report": str(report), "date": today})
    if not dry_run and results:
        with (data / "总览刷新记录.jsonl").open("a", encoding="utf-8", newline="\n") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return results


def notify(title: str, body: str) -> None:
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "''")
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
          "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
          f"$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{esc(title)}</text><text>{esc(body)}</text></binding></visual></toast>');"
          "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", type=Path, required=True)
    ap.add_argument("--only")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--timeout", type=int, default=2400)
    a = ap.parse_args(argv)
    res = refresh(a.vault, a.only, a.dry_run, timeout=a.timeout)
    print("REFRESH_OVERVIEWS " + json.dumps(res, ensure_ascii=False))
    bad = [r for r in res if r["state"] == "restored"]
    if bad:
        notify("成果库总览刷新失败", "；".join(f"{r['project']}: {r['problems'][0]}" for r in bad)[:200])
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run paper_gate.py automatically when a LaTeX paper's sources are saved (0 LLM tokens).

    pythonw paper_gate_watch.py PAPER_DIR [PAPER_DIR ...] [--quiet 30] [--fix] [--once]

Polls ``*.tex``, ``*.bib`` and ``paper_gate.json`` under each paper folder every few seconds. Once
the files have been quiet for ``--quiet`` seconds after a change, it runs
``paper_gate.py PAPER_DIR --compile`` (``--fix`` adds the auto-fixes; off by default so a background
run never rewrites a file someone is still editing). The gate writes ``paper_gate_report.md`` and
copies the PDF to ``deliver_as`` only when it passes, as when run by hand. A failure, and the first
pass after a failure, show a Windows notification. One watcher per machine (lock file). Meant to be
started at logon by Task Scheduler; ``--once`` checks once and exits (for tests and manual use).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
GATE = Path(__file__).resolve().parent / "paper_gate.py"
CENTER = Path(__file__).resolve().parents[2] / "research-gate" / "scripts" / "command_center.py"
PATTERNS = ("*.tex", "*.bib", "paper_gate*.json")
LOCK = Path(os.environ.get("TEMP", ".")) / "paper_gate_watch.lock"


def discover_papers(vault: Path | None, explicit: list[Path]) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        try:
            rp = p.resolve()
        except OSError:
            return
        if (rp / "paper_gate.json").is_file() and rp not in seen:
            seen.add(rp)
            found.append(rp)

    for p in explicit:
        _add(p)
    if vault is None:
        return found
    reg = vault / "知识库" / "_数据" / "项目.json"
    if not reg.is_file():
        return found
    try:
        projects = json.loads(reg.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return found
    if not isinstance(projects, dict):
        return found
    for cfg in projects.values():
        if not isinstance(cfg, dict):
            continue
        root = (vault / cfg["root"]) if isinstance(cfg.get("root"), str) and cfg["root"].strip() else None
        raw_dirs: list[str] = []
        if isinstance(cfg.get("paper_dirs"), list):
            raw_dirs.extend(d for d in cfg["paper_dirs"] if isinstance(d, str) and d.strip())
        if isinstance(cfg.get("paper_dir"), str) and cfg["paper_dir"].strip():
            raw_dirs.append(cfg["paper_dir"])
        if root is not None:
            raw_dirs.append(str(root))
        for raw in raw_dirs:
            cand = Path(raw)
            if not cand.is_absolute():
                cand = (vault / cand) if (vault / cand).exists() else ((root / cand) if root is not None else (vault / cand))
            _add(cand)
        lib = cfg.get("library")
        if isinstance(lib, str) and lib.strip():
            drafts = vault / lib / "论文草稿"
            if drafts.is_dir():
                for latex_dir in sorted(drafts.glob("*/latex")):
                    _add(latex_dir)
    return found


def snapshot(paper: Path) -> dict[str, float]:
    out = {}
    for pat in PATTERNS:
        for p in paper.rglob(pat):
            if "autofix" in p.parts[-2].lower() or any(part.startswith(".") for part in p.relative_to(paper).parts):
                continue
            try:
                out[str(p)] = p.stat().st_mtime
            except OSError:
                pass
    return out


def notify(title: str, body: str) -> None:
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "''")
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
          "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
          f"$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{esc(title)}</text><text>{esc(body)}</text></binding></visual></toast>');"
          "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, timeout=30, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        pass


def configs(paper: Path) -> list[Path]:
    """paper_gate.json first, then any variant such as paper_gate_zh.json (one build per config)."""
    return sorted(paper.glob("paper_gate*.json"), key=lambda p: (p.name != "paper_gate.json", p.name))


def run_gate(paper: Path, fix: bool, runner=subprocess.run) -> tuple[bool, str]:
    ok_all, heads = True, []
    for cfg in configs(paper) or [paper / "paper_gate.json"]:
        cmd = [sys.executable.replace("pythonw.exe", "python.exe"), "-E", "-s", "-X", "utf8", "-B", str(GATE), str(paper),
               "--compile", "--config", str(cfg)]
        if fix:
            cmd.append("--fix")
        p = runner(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        head = next((l for l in (p.stdout or "").splitlines() if l.startswith("errors ")), f"rc={p.returncode}")
        ok_all &= p.returncode == 0
        heads.append(f"{cfg.stem}: {head}" if len(configs(paper)) > 1 else head)
    return ok_all, "；".join(heads)


class Watcher:
    def __init__(self, papers: list[Path], quiet: float, fix: bool, runner=subprocess.run, notifier=notify,
                 vault: Path | None = None, refresh_dashboard: bool = True):
        self.explicit = list(papers)
        self.vault = vault
        self.refresh_dashboard = refresh_dashboard
        self.papers = discover_papers(vault, self.explicit) if vault is not None else list(papers)
        self.quiet, self.fix, self.runner, self.notifier = quiet, fix, runner, notifier
        self.seen = {p: snapshot(p) for p in self.papers}
        self.changed_at: dict[Path, float] = {}
        self.last_ok: dict[Path, bool] = {}

    def tick(self, now: float) -> list[tuple[Path, bool, str]]:
        if self.vault is not None:
            for p in discover_papers(self.vault, self.explicit):
                if p not in self.seen:
                    self.papers.append(p)
                    self.seen[p] = snapshot(p)
        results = []
        for paper in self.papers:
            snap = snapshot(paper)
            if snap != self.seen[paper]:
                self.seen[paper] = snap
                self.changed_at[paper] = now
            if paper in self.changed_at and now - self.changed_at[paper] >= self.quiet:
                del self.changed_at[paper]
                ok, head = run_gate(paper, self.fix, self.runner)
                self.seen[paper] = snapshot(paper)  # the gate's own writes are not a new edit
                if not ok:
                    self.notifier(f"论文门禁未通过：{paper.name}", f"{head}。详情见 paper_gate_report.md")
                elif self.last_ok.get(paper) is False:
                    self.notifier(f"论文门禁恢复通过：{paper.name}", head)
                self.last_ok[paper] = ok
                if self.vault is not None and self.refresh_dashboard and CENTER.is_file():
                    py = sys.executable.replace("pythonw.exe", "python.exe")
                    self.runner([py, "-E", "-s", "-X", "utf8", "-B", str(CENTER), "--vault", str(self.vault)],
                                capture_output=True, text=True, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                results.append((paper, ok, head))
        return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("papers", type=Path, nargs="*")
    ap.add_argument("--vault", type=Path, default=None, help="Obsidian vault root to auto-discover papers from 项目.json")
    ap.add_argument("--list", action="store_true", help="print discovered paper directories and exit")
    ap.add_argument("--quiet", type=float, default=30)
    ap.add_argument("--interval", type=float, default=5)
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--once", action="store_true", help="run the gate on every paper once and exit")
    a = ap.parse_args(argv)
    papers = discover_papers(a.vault, a.papers)
    if a.list:
        for p in papers:
            print(p)
        return 0
    if a.once:
        for p in papers:
            print(p.name, *run_gate(p, a.fix))
        return 0
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:  # stale lock from a crash: take it over if its process is gone
            pid = int(LOCK.read_text().strip() or 0)
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True,
                                 encoding="mbcs" if os.name == "nt" else "utf-8", errors="replace").stdout
            alive = pid > 0 and str(pid) in (out or "")  # tasklist speaks the console code page (GBK here)
        except (OSError, ValueError, LookupError):
            alive = False
        if alive:
            return 0
        LOCK.unlink(missing_ok=True)
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        w = Watcher(papers, a.quiet, a.fix, vault=a.vault)
        while True:
            w.tick(time.time())
            time.sleep(a.interval)
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())

"""First-time setup of this workflow on a new machine (0 LLM tokens).

Without --apply it only prints the plan. With --apply it:
  1. makes every skill in skills/ visible to the agents on this machine: through magpie if it is
     installed (a link in ~/.agents/skills, then `magpie library skill hand-over`), otherwise as
     links in the agent skill folders that already exist (~/.claude/skills, ~/.codex/skills,
     ~/.gemini/config/skills, ~/.agents/skills). An existing folder with the same name is never
     replaced; it is reported so you can compare it with the repo first;
  2. installs the git hooks: pre-push runs the repo checks, post-commit and post-merge refresh the
     agent skill copies through magpie;
  3. with --papers, downloads the 20 writing-exemplar papers (about 120 MB) from official sources.

    python scripts/bootstrap.py            # show the plan
    python scripts/bootstrap.py --apply    # do it
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
AGENT_DIRS = (".claude/skills", ".codex/skills", ".gemini/config/skills", ".agents/skills")


def _sync():
    spec = importlib.util.spec_from_file_location("sync_skills", REPO / "scripts" / "sync_skills.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_magpie() -> Path | None:
    hit = shutil.which("magpie")
    if hit:
        return Path(hit)
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "magpie" / "magpie.exe"
    return local if local.is_file() else None


def skills(repo: Path = REPO) -> list[str]:
    return sorted(p.name for p in (repo / "skills").iterdir() if (p / "SKILL.md").is_file())


def plan(home: Path, repo: Path = REPO, magpie: Path | None = None) -> list[tuple[str, Path, Path]]:
    """(action, link, target) per skill: action is link, ok (already linked here) or skip-existing."""
    sync = _sync()
    dirs = [home / ".agents" / "skills"] if magpie else [home / d for d in AGENT_DIRS if (home / d).is_dir()]
    out = []
    for name in skills(repo):
        target = repo / "skills" / name
        for d in dirs:
            link = d / name
            state = sync.link_state(link, target)
            action = {"linked": "ok", "missing": "link"}.get(state, "skip-existing")
            if magpie and (home / ".config" / "magpie" / "library" / "skills" / name).exists():
                action = "ok"
            out.append((action, link, target))
    return out


def apply(steps: list[tuple[str, Path, Path]], magpie: Path | None, repo: Path = REPO) -> int:
    sync = _sync()
    handed = []
    for action, link, target in steps:
        if action == "link":
            sync.make_link(link, target)
            handed.append(link.name)
            print(f"linked {link} -> {target}")
        elif action == "skip-existing":
            print(f"left alone {link}: a folder with this name exists; compare it with {target} before replacing it")
    if magpie and handed:
        subprocess.run([str(magpie), "library", "skill", "hand-over", *handed], check=False)
        print("magpie: tick the agents that should get these skills on the app's Library page, then run `magpie library sync`")
    sync.install_hook(repo)
    return 0


def _check_brief(project_dir: Path) -> tuple[bool, list[str]]:
    spec = importlib.util.spec_from_file_location("experience_brief", REPO / "scripts" / "experience_brief.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.check_config(project_dir)


def init_project(
    project_dir: Path,
    *,
    name: str,
    mode: str,
    vault: Path,
    paper_dir: Path | None = None,
    shared_repo: Path | None = None,
    memory_dir: Path | None = None,
) -> dict:
    """Onboard a project (probe / paper / program) with zero glue scripts."""
    if mode not in ("probe", "paper", "program"):
        raise ValueError(f"unsupported mode: {mode}")
    proj = project_dir.resolve()
    proj.mkdir(parents=True, exist_ok=True)
    vroot = vault.resolve()
    data_dir = vroot / "知识库" / "_数据"
    data_dir.mkdir(parents=True, exist_ok=True)

    cross = data_dir / "跨项目.jsonl"
    if not cross.exists():
        cross.write_text("", encoding="utf-8")
    k_paths = [cross.as_posix()]
    local_kb = data_dir / f"{name}.jsonl"
    if local_kb.is_file():
        k_paths.append(local_kb.as_posix())

    checklists = {
        "probe": "- 灵感探针：用 reference/ 下的极简原生 PyTorch 快速验证关键假说，只报告实际跑过的结果。",
        "paper": "- 专题成文：正文与视觉由主模型负责；日常 arXiv 出 PDF，用 % gate-sorry/TBD 记清待补数，定稿前清零。",
        "program": "- 长期科学探索：开训前定好可证伪假说、干净基线和判错线；探索与修错免卡，发现沉淀入知识库。",
    }
    brief_path = proj / ".agents" / "brief.json"
    if not brief_path.is_file() or not brief_path.read_text(encoding="utf-8").strip():
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        cfg = {"mode": mode, "knowledge": k_paths, "checklist": checklists[mode]}
        if memory_dir is not None:
            cfg["memory_dir"] = memory_dir.resolve().as_posix()
        brief_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lib = f"{name}成果库"
    (vroot / lib / "论文草稿").mkdir(parents=True, exist_ok=True)
    home = vroot / lib / "00-总览（先看这页）.md"
    if not home.exists():
        home.write_text(f"# {name} 成果库总览\n\n- 项目模式：`{mode}`\n", encoding="utf-8")

    if shared_repo is not None:
        sroot = shared_repo.resolve()
        sroot.mkdir(parents=True, exist_ok=True)
        template = REPO / "skills" / "research-gate" / "assets" / "shared-repo"
        for src in template.rglob("*"):
            if src.is_file():
                dst = sroot / src.relative_to(template)
                if not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

    reg_path = data_dir / "项目.json"
    try:
        reg = json.loads(reg_path.read_text(encoding="utf-8-sig")) if reg_path.is_file() else {}
    except (OSError, ValueError):
        reg = {}
    if not isinstance(reg, dict):
        reg = {}
    entry = dict(reg.get(name, {})) if isinstance(reg.get(name), dict) else {}
    entry.update({"root": proj.as_posix(), "library": lib, "mode": mode})
    pdir = paper_dir.resolve() if paper_dir is not None else (proj if (proj / "paper_gate.json").is_file() else None)
    if pdir is not None:
        pdirs = list(entry.get("paper_dirs", [])) if isinstance(entry.get("paper_dirs"), list) else []
        if pdir.as_posix() not in pdirs:
            pdirs.append(pdir.as_posix())
        entry["paper_dirs"] = pdirs
    if shared_repo is not None:
        entry["shared_repo"] = shared_repo.resolve().as_posix()
    reg[name] = entry
    reg_path.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ok, issues = _check_brief(proj)
    return {"ok": ok, "issues": issues, "brief": brief_path, "registry": reg_path}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--papers", action="store_true", help="also download the writing-exemplar papers (~120 MB)")
    ap.add_argument("--init-project", type=Path, help="onboard a project directory with zero glue scripts")
    ap.add_argument("--name", help="project name in Obsidian 项目.json")
    ap.add_argument("--mode", choices=("probe", "paper", "program"), default="paper")
    ap.add_argument("--vault", type=Path, help="Obsidian vault root")
    ap.add_argument("--paper-dir", type=Path, default=None)
    ap.add_argument("--shared-repo", type=Path, default=None)
    ap.add_argument("--memory-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.init_project:
        if not args.vault:
            ap.error("--init-project requires --vault")
        name = args.name or args.init_project.resolve().name
        res = init_project(args.init_project, name=name, mode=args.mode, vault=args.vault,
                           paper_dir=args.paper_dir, shared_repo=args.shared_repo, memory_dir=args.memory_dir)
        print(f"INIT_PROJECT {'OK' if res['ok'] else 'FAIL'} {name} ({args.mode}) -> {res['brief']}")
        for issue in res["issues"]:
            print(f"  - {issue}")
        return 0 if res["ok"] else 1
    magpie = find_magpie()
    steps = plan(Path.home(), REPO, magpie)
    print(f"skills: {', '.join(skills())}; distribution: {'magpie' if magpie else 'links in existing agent folders'}")
    if not steps:
        print("no agent skill folder found; create the one your agent reads (e.g. ~/.claude/skills) and rerun")
    for action, link, _ in steps:
        print(f"  {action:13s} {link}")
    if not args.apply:
        print("plan only; rerun with --apply")
        return 0
    rc = apply(steps, magpie)
    if args.papers:
        rc |= subprocess.run([sys.executable, "-B", str(REPO / "skills/paper-write/scripts/fetch_exemplar_papers.py")]).returncode
    subprocess.run([sys.executable, "-B", str(REPO / "scripts/check_routes.py"), "--repo-only"])
    print("next: clone the shared project repo and run `python tools/onboard.py` there")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

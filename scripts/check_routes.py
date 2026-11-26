"""Zero-token CI verification for the 3-layer skill routing & SSOT hygiene."""

import os
import re
import subprocess
import sys
from pathlib import Path

SKILLS_ROOT = Path.home() / ".gemini" / "config" / "skills"
# Project root that holds AGENTS.md: $RDS_ROOT, else the repo this script lives in.
REPO_ROOT = Path(os.environ.get("RDS_ROOT") or Path(__file__).resolve().parents[1])

ACTIVE_L1_SKILLS = (
    "paper-write",
    "research-gate",
    "paper-figure-craft",
    "paper_search",
    "perf-ruler",
    "obelisk",
)

RETIRED_SKILL_NAMES = (
    "legacy-research-workflow",
    "plain-clear-writing",
    "paper-framing-editor",
    "academic-research-suite",
    "research-utility-taste-gate",
)
RETIRED_REPO_DIRS = (Path(".agents") / "skills" / "frontier-arch-taste",)
RETIRED_COUNT = len(RETIRED_SKILL_NAMES) + len(RETIRED_REPO_DIRS)


def _retired_dirs(skills_root: Path, repo_root: Path) -> list[Path]:
    return [skills_root / n for n in RETIRED_SKILL_NAMES] + [repo_root / d for d in RETIRED_REPO_DIRS]


def _line_budgets(skills_root: Path, repo_root: Path) -> list[tuple[Path, int]]:
    return [
        (repo_root / "AGENTS.md", 60),
        (skills_root / "paper-write" / "SKILL.md", 80),
        (skills_root / "research-gate" / "SKILL.md", 80),
    ]


# Semantic neighbours that a lexical router cannot separate on its own. Each pair needs at least one
# side to name the other in its negative clause (confusability drives selection errors, arXiv:2601.04748).
NEIGHBOR_CLUSTERS: tuple[tuple[str, ...], ...] = (
    ("paper_search", "deep-research"),
    ("research-gate", "research-direction-selector"),
    ("research-direction-selector", "math-research-solve", "obelisk"),
    ("paper-figure-craft", "nn-svg-diagram"),
    ("codex-autoresearch", "codexhost-delegation", "workflow-design"),
)
CACHE_NAMES = ("__pycache__", ".hypothesis", ".pytest_cache")

NEG_TRIGGER_MARKERS = ("Do NOT use", "不用于", "无需")

# Bilingual deterministic routing fixtures verified strictly against parsed SKILL.md frontmatter.
ROUTING_FIXTURES: list[tuple[str, str]] = [
    ("Draft the ICML introduction and abstract in English for our paper.", "paper-write"),
    ("起草并润色顶会论文摘要和引言，准备审稿回复。", "paper-write"),
    ("Vet this new model architecture and loss function before coding or burning GPU.", "research-gate"),
    ("立项评审一下这套新架构设计、损失函数和训练配方。", "research-gate"),
    ("Render a publication-grade conference figure via Matplotlib and audit figure taste.", "paper-figure-craft"),
    ("用 TikZ 绘制顶会论文图表并做审计。", "paper-figure-craft"),
    ("Search academic papers across arXiv and OpenReview on recurrent world models.", "paper_search"),
    ("跨 arXiv 和 DBLP 做文献检索，按会议范围搜索论文。", "paper_search"),
    ("Estimate theoretical latency and compute or memory bottlenecks for this PyTorch module.", "perf-ruler"),
    ("在不训练也不用 GPU 的前提下，评估这个 PyTorch 算子的理论最低时延与约束诊断。", "perf-ruler"),
    ("继续按之前方案推进，先恢复任务上下文并查询过去决定与失败经验。", "obelisk"),
    ("把去雾课题的实验交接打包归档", "research-gate"),
    ("Draft the rebuttal replies to reviewers.", "paper-write"),
    ("刚跑完第一组对照结果，你想先看走势还是换个学习率？", "NONE"),
]

_STOP_EN = {
    "a", "an", "and", "or", "for", "of", "the", "to", "in", "on", "at", "by", "via",
    "with", "before", "while", "this", "our", "do", "not", "use", "only", "across",
}


def _parse_frontmatter(skill_md: Path) -> tuple[dict[str, str], str]:
    text = skill_md.read_text(encoding="utf-8-sig")
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", text, flags=re.DOTALL)
    if not m:
        raise ValueError(f"Missing YAML frontmatter in {skill_md}")
    fm_text, body = m.group(1), m.group(2)
    meta: dict[str, str] = {}
    current_key = ""
    for line in fm_text.splitlines():
        if re.match(r"^[A-Za-z0-9_-]+:\s*", line):
            k, v = line.split(":", 1)
            current_key = k.strip()
            meta[current_key] = v.strip()
        elif current_key and line.startswith((" ", "\t")):
            meta[current_key] = (meta[current_key] + " " + line.strip()).strip()
    return meta, body


# Function-word bigrams that carry no routing intent (they made casual chat tie with unrelated skills).
_STOP_ZH = {"还是", "结果", "或者", "以及", "一下", "是否", "可以", "这个", "什么", "如何", "怎么"}


def _extract_cues(text: str) -> tuple[set[str], set[str]]:
    en_tokens = {
        w.lower()
        for w in re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}", text)
        if w.lower() not in _STOP_EN
    }
    zh_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    zh_grams: set[str] = set()
    for ch in zh_chunks:
        for n in (2, 3, 4):
            for i in range(len(ch) - n + 1):
                zh_grams.add(ch[i : i + n])
    return en_tokens, zh_grams - _STOP_ZH


def _split_pos_neg(desc: str) -> tuple[str, str]:
    low = desc.lower()  # "Do not use" and "Do NOT use" both open the negative clause
    hits = [i for i in (low.find("do not use"), desc.find("不用于")) if i != -1]
    if hits:
        idx = min(hits)
        return desc[:idx].strip(), desc[idx:].strip()
    return desc, ""


def _route_prompt(prompt: str, skill_descs: dict[str, str]) -> str:
    p_en, p_zh = _extract_cues(prompt)
    best_skill = "NONE"
    best_score = 0
    scores: dict[str, int] = {}

    for name, desc in skill_descs.items():
        pos_txt, neg_txt = _split_pos_neg(desc)
        pos_en, pos_zh = _extract_cues(pos_txt)
        # Exclude `-> target-skill` hints inside the negative clause when scoring vetoes
        neg_clean = re.sub(r"\(->[^)]+\)", "", neg_txt)
        neg_en, neg_zh = _extract_cues(neg_clean)
        neg_en.discard("not")

        pos_hits = len(p_en & pos_en) * 2 + len(p_zh & pos_zh)
        neg_hits = len(p_en & neg_en) * 2 + len(p_zh & neg_zh)
        # Apply veto penalty only if negative cues match strictly outside positive cues
        net_pos = pos_hits - (neg_hits * 2 if neg_hits > 0 and pos_hits <= neg_hits else 0)
        scores[name] = net_pos
        if net_pos > best_score:
            best_score = net_pos
            best_skill = name

    if best_score <= 0:
        return "NONE"
    tied = [k for k, v in scores.items() if v == best_score]
    return best_skill if len(tied) == 1 else f"TIE:{','.join(sorted(tied))}"


def run_checks(skills_root: Path = SKILLS_ROOT, repo_root: Path = REPO_ROOT) -> list[str]:
    errors: list[str] = []
    skill_descs: dict[str, str] = {}

    # 1. Active L1 skills exist + frontmatter + negative trigger checks
    for name in ACTIVE_L1_SKILLS:
        sdir = skills_root / name
        sfile = sdir / "SKILL.md"
        if not sfile.is_file():
            errors.append(f"Missing active L1 skill file: {sfile}")
            continue
        meta, _ = _parse_frontmatter(sfile)
        if not meta.get("name"):
            errors.append(f"Missing YAML name in {sfile}")
        desc = meta.get("description", "")
        if not desc:
            errors.append(f"Missing YAML description in {sfile}")
        elif not any(tok in desc for tok in NEG_TRIGGER_MARKERS):
            errors.append(f"Missing negative trigger marker in {sfile} description")
        skill_descs[name] = desc

    # 2. Retired top-level skills are absent
    for rdir in _retired_dirs(skills_root, repo_root):
        if rdir.exists():
            errors.append(f"Retired skill still exists: {rdir}")

    # 3. Line budgets
    for path, limit in _line_budgets(skills_root, repo_root):
        if path.name == "AGENTS.md" and not path.is_file():
            continue  # a skills-only repo has no AGENTS.md; a full project repo does
        if not path.is_file():
            errors.append(f"Missing budgeted file: {path}")
            continue
        count = len(path.read_text(encoding="utf-8-sig").splitlines())
        if count > limit:
            errors.append(f"Line budget exceeded for {path}: {count} > {limit}")

    # 4. Zero nested SKILL.md inside L2 references
    for name in ("paper-write", "research-gate"):
        ref_dir = skills_root / name / "references"
        if not ref_dir.is_dir():
            errors.append(f"Missing L2 references dir: {ref_dir}")
            continue
        nested = list(ref_dir.rglob("SKILL.md"))
        if nested:
            errors.append(f"Nested SKILL.md found under {ref_dir}: {nested}")

    # 5. All relative links (references/... or scripts/...) resolve on disk
    link_re = re.compile(r"\]\(((?:references|scripts)/[^)#\s]+)\)")
    for name in ("paper-write", "research-gate"):
        sdir = skills_root / name
        sfile = sdir / "SKILL.md"
        if not sfile.is_file():
            continue
        text = sfile.read_text(encoding="utf-8-sig")
        for rel in sorted(set(link_re.findall(text))):
            target = sdir / rel
            if not target.is_file():
                errors.append(f"Broken L2/script link in {sfile}: {rel} -> {target}")

    # 6. Deterministic routing fixtures
    if len(skill_descs) == len(ACTIVE_L1_SKILLS):
        for prompt, expected in ROUTING_FIXTURES:
            got = _route_prompt(prompt, skill_descs)
            if got != expected:
                errors.append(f"Routing mismatch for {prompt!r}: expected {expected}, got {got}")

    return errors


def load_installed_descriptions(skills_root: Path) -> dict[str, str]:
    """Every installed skill the live router can see, not only the active L1 set."""
    descs: dict[str, str] = {}
    for sdir in sorted(p for p in skills_root.iterdir() if (p / "SKILL.md").is_file()):
        meta, _ = _parse_frontmatter(sdir / "SKILL.md")
        descs[sdir.name] = meta.get("description", "")
    return descs


def neighbor_warnings(descs: dict[str, str]) -> list[str]:
    warnings: list[str] = []
    for cluster in NEIGHBOR_CLUSTERS:
        present = [n for n in cluster if n in descs]
        for i, a in enumerate(present):
            for b in present[i + 1 :]:
                neg_a, neg_b = _split_pos_neg(descs[a])[1], _split_pos_neg(descs[b])[1]
                if b not in neg_a and a not in neg_b:
                    warnings.append(f"Neighbours {a} <-> {b}: neither negative clause names the other")
    return warnings


def full_set_fixture_warnings(descs: dict[str, str]) -> list[str]:
    """Re-run the fixtures against all installed skills; the 6-skill check hides neighbour collisions."""
    out = []
    for prompt, expected in ROUTING_FIXTURES:
        got = _route_prompt(prompt, descs)
        if got != expected:
            out.append(f"Full-set routing for {prompt!r}: expected {expected}, got {got}")
    return out


def check_mirror(repo_skills: Path, skills_root: Path) -> tuple[list[str], list[str]]:
    """Repo copies must match the installed copies byte-for-byte, modulo line endings."""
    errors: list[str] = []
    infos: list[str] = []

    def _is_cache(p: Path, base: Path) -> bool:
        return p.suffix == ".pyc" or any(part in CACHE_NAMES for part in p.relative_to(base).parts)

    for sdir in sorted(p for p in repo_skills.iterdir() if (p / "SKILL.md").is_file()):
        inst = skills_root / sdir.name
        if not inst.is_dir():
            errors.append(f"Mirrored skill not installed: {sdir.name}")
            continue
        for f in sorted(sdir.rglob("*")):
            if not f.is_file() or _is_cache(f, sdir):
                continue
            rel = f.relative_to(sdir).as_posix()
            g = inst / rel
            if not g.is_file():
                errors.append(f"Repo file missing from install: {sdir.name}/{rel}")
            elif f.read_bytes().replace(b"\r\n", b"\n") != g.read_bytes().replace(b"\r\n", b"\n"):
                errors.append(f"Drift between repo and install: {sdir.name}/{rel}")
        extra = [
            g for g in inst.rglob("*")
            if g.is_file() and not _is_cache(g, inst) and not (sdir / g.relative_to(inst)).exists()
        ]
        if extra:
            infos.append(f"{sdir.name}: {len(extra)} install-only files are not mirrored in the repo")
        residue = sorted({
            g.relative_to(inst).as_posix() for g in inst.rglob("*")
            if (g.is_dir() and g.name in CACHE_NAMES) or g.suffix == ".pyc"
        })
        if residue:
            errors.append(f"Cache residue in installed skill {sdir.name}: {residue[:3]}")
    return errors, infos


# Release hygiene adapted from CCFA-Skills (ccf-common/scripts/check_path_privacy.py and
# check_markdown_links.py, MIT). Home-dir names are split so this file does not match itself.
_WIN_HOME, _NIX_HOME = "Us" + "ers", "ho" + "me"
PRIVACY_PATTERNS = (
    ("windows-user-home", re.compile(rf"[A-Za-z]:[\\/]+{_WIN_HOME}[\\/]+[^\\/\s\"'<>`]+", re.IGNORECASE)),
    ("macos-user-home", re.compile(rf"/{_WIN_HOME}/[^/\s\"'<>`]+/")),
    ("linux-user-home", re.compile(rf"/{_NIX_HOME}/[^/\s\"'<>`]+/")),
)
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".7z", ".exe", ".dll", ".pyc"}
_MD_LINK = re.compile(r"\]\(([^)\s]+)\)")
_FENCE = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`[^`\n]*`")


def _publishable_files(repo: Path) -> list[Path]:
    """Tracked plus non-ignored untracked files: what a push would publish."""
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, check=True,
    ).stdout
    return [repo / os.fsdecode(n) for n in sorted(set(out.split(b"\x00"))) if n]


def check_path_privacy(repo: Path) -> list[str]:
    errors = []
    for f in _publishable_files(repo):
        if f.suffix.lower() in BINARY_SUFFIXES or not f.is_file():
            continue
        data = f.read_bytes()
        if b"\x00" in data:
            continue
        for lineno, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            for label, pat in PRIVACY_PATTERNS:
                if pat.search(line):
                    errors.append(f"Local path leak ({label}) in {f.relative_to(repo).as_posix()}:{lineno}")
    return errors


def check_markdown_links(root: Path) -> tuple[list[str], list[str]]:
    """Relative Markdown links under ``root`` must resolve. Inside a folder with VENDORED.md,
    links to upstream files that were deliberately not vendored are reported as INFO."""
    errors: list[str] = []
    vendored: dict[str, int] = {}
    for md in sorted(root.rglob("*.md")):
        if any(part in CACHE_NAMES or part == "papers" for part in md.relative_to(root).parts):
            continue  # papers/ holds extracted third-party text, not maintained docs
        text = md.read_text(encoding="utf-8-sig", errors="replace")
        text = _INLINE_CODE.sub("", _FENCE.sub("", text))
        for target in _MD_LINK.findall(text):
            target = target.split("#", 1)[0]
            if not target or re.match(r"^[A-Za-z][\w+.-]*:", target) or target.startswith(("<", "/")):
                continue
            if "/" not in target and "." not in target:
                continue  # template placeholders such as [text](url)
            if (md.parent / target).exists():
                continue
            owner = next((d for d in md.parents if root in d.parents and (d / "VENDORED.md").is_file()), None)
            if owner is not None:
                key = owner.relative_to(root).as_posix()
                vendored[key] = vendored.get(key, 0) + 1
            else:
                errors.append(f"Broken link in {md.relative_to(root).as_posix()}: {target}")
    infos = [f"{k}: {v} links to upstream files not vendored (see VENDORED.md)" for k, v in sorted(vendored.items())]
    return errors, infos


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skills-root", type=Path, default=SKILLS_ROOT)
    ap.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="Project root that holds AGENTS.md.")
    ap.add_argument(
        "--mirror",
        default=str(Path(__file__).resolve().parents[1] / "skills"),
        help="Repo skills dir to diff against the install; pass '' to skip.",
    )
    ap.add_argument("--strict-neighbors", action="store_true", help="Fail on neighbour and full-set warnings.")
    ap.add_argument(
        "--repo-only",
        action="store_true",
        help="CI mode: only link and path-privacy checks on this repo; needs no installed skills.",
    )
    args = ap.parse_args(argv)

    this_repo = Path(__file__).resolve().parents[1]
    errors: list[str] = []
    infos: list[str] = []
    if args.mirror and Path(args.mirror).is_dir():
        l_err, l_info = check_markdown_links(Path(args.mirror))
        errors += l_err
        infos += l_info
        if (this_repo / ".git").exists():
            errors += check_path_privacy(this_repo)  # only where a skills repo is published
    if args.repo_only:
        for line in infos:
            print(f"INFO {line}")
        for e in errors:
            print(f"  - {e}")
        print("REPO CHECK FAILED" if errors else "REPO CHECK PASS: links resolve, no local home paths.")
        return 1 if errors else 0

    errors += run_checks(args.skills_root, args.repo_root)
    if args.mirror and Path(args.mirror).is_dir():
        m_err, m_info = check_mirror(Path(args.mirror), args.skills_root)
        errors += m_err
        infos += m_info
    descs = load_installed_descriptions(args.skills_root) if args.skills_root.is_dir() else {}
    warnings = neighbor_warnings(descs) + full_set_fixture_warnings(descs)

    for line in infos:
        print(f"INFO {line}")
    for w in warnings:
        print(f"WARN {w}")
    if args.strict_neighbors:
        errors += warnings
    if errors:
        print(f"ROUTING CHECK FAILED ({len(errors)} errors):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(
        f"ROUTING CHECK PASS: {len(ACTIVE_L1_SKILLS)} active L1 skills, {RETIRED_COUNT} retired absent, "
        f"{len(ROUTING_FIXTURES)} fixtures matched, {len(descs)} installed skills scanned, {len(warnings)} warnings."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

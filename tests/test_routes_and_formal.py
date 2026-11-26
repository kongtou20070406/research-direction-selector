"""Tests for the routing mirror/neighbour checks and the headline binding-coverage check."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


routes = _load("check_routes", "scripts/check_routes.py")
formal = _load("verify_manuscript_formal", "skills/paper-write/scripts/verify_manuscript_formal.py")


def _skill(root: Path, name: str, desc: str, extra: dict[str, str] | None = None) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\nbody\n", encoding="utf-8")
    for rel, text in (extra or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(text.encode("utf-8"))  # exact bytes; write_text would translate \n on Windows


def test_mirror_flags_drift_missing_and_cache_residue(tmp_path):
    repo, inst = tmp_path / "repo", tmp_path / "inst"
    _skill(repo, "a", "A skill.", {"scripts/x.py": "X = 1\n", "references/r.md": "same\n"})
    _skill(inst, "a", "A skill.", {"scripts/x.py": "X = 2\n", "references/r.md": "same\r\n", "extra.md": "e"})
    (inst / "a" / "scripts" / "__pycache__").mkdir()
    _skill(repo, "b", "B skill.")
    errors, infos = routes.check_mirror(repo, inst)
    assert "Drift between repo and install: a/scripts/x.py" in errors
    assert not any("r.md" in e for e in errors)  # CRLF vs LF is not drift
    assert "Mirrored skill not installed: b" in errors
    assert any("Cache residue" in e for e in errors)
    assert infos == ["a: 1 install-only files are not mirrored in the repo"]


def test_neighbor_warning_clears_when_one_side_points_to_the_other():
    base = {"paper_search": "Search papers. Do NOT use for drafting.", "deep-research": "Research questions."}
    assert len(routes.neighbor_warnings(base)) == 1
    fixed = dict(base, paper_search="Search papers. Do NOT use for multi-source synthesis (-> deep-research).")
    assert routes.neighbor_warnings(fixed) == []


def _draft(tmp_path: Path, abstract_en: str, abstract_zh: str) -> Path:
    for lang, text in (("en", abstract_en), ("zh", abstract_zh)):
        d = tmp_path / f"sections_{lang}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "00_abstract.md").write_text(text, encoding="utf-8")
    return tmp_path


def test_headline_coverage_flags_unbound_numbers_only(tmp_path):
    draft = _draft(
        tmp_path,
        "We cut RMSE from 0.364 to 0.255 (-30%) with GPT-4.1 features and `lr=0.001`, over 3 seeds.",
        "误差从 0.364 降到 0.255。",
    )
    bindings = tmp_path / "bind.json"
    bindings.write_text(
        json.dumps([
            {"file": "sections_en/00_abstract.md", "display": "0.364", "ledger": "l.json", "key": "a"},
            {"file": "sections_en/00_abstract.md", "display": "0.255", "ledger": "l.json", "key": "b"},
            {"file": "sections_zh/00_abstract.md", "display": "0.364", "ledger": "l.json", "key": "a"},
        ]),
        encoding="utf-8",
    )
    errors, stats = formal.check_headline_binding_coverage(draft, bindings)
    joined = "\n".join(errors)
    assert "'-30%' in sections_en/00_abstract.md" in joined
    assert "'0.255' in sections_zh/00_abstract.md" in joined
    assert "4.1" not in joined and "0.001" not in joined  # version strings and inline code are skipped
    assert len(errors) == 2 and stats["headline_numbers"] == 5

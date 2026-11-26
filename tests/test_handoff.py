"""Good/bad fixtures for skills/research-gate/scripts/handoff.py."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("handoff", ROOT / "skills/research-gate/scripts/handoff.py")
ho = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ho)


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / "runs").mkdir(parents=True)
    (proj / "train.py").write_text("print('train')\n", encoding="utf-8")
    (proj / "runs" / "result.csv").write_text("seed,psnr\n42,17.58\n", encoding="utf-8")
    (proj / "runs" / "retrain.csv").write_text("seed,psnr\n42,16.18\n", encoding="utf-8")
    return proj


def _plan(proj: Path, **over) -> Path:
    plan = {
        "schema": "handoff-plan/v1", "project": "dehaze", "purpose": "handoff", "scope": "complete",
        "objective": "Iterative refinement beats a single pass on Dense-HAZE at equal parameters.",
        "files": {"train.py": {"role": "code"}, "runs/result.csv": {"role": "result"},
                  "runs/retrain.csv": {"role": "result"}},
        "claims": [
            {"id": "C1", "statement": "17.58 dB on the dev split", "status": "refuted",
             "evidence": ["runs/result.csv"], "refuted_by": ["runs/retrain.csv"]},
            {"id": "C2", "statement": "strict retrain gives 16.18 dB", "status": "executed", "evidence": ["runs/retrain.csv"]},
        ],
        "failed_approaches": [{"id": "F1", "what": "t-conditioning via Linear(1,3)", "why": "offset leaks into the residual"}],
        "next_actions": ["add EMA and a held-out split"],
    }
    plan.update(over)
    path = proj / "PLAN.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def test_complete_handoff_freezes_packs_and_verifies(tmp_path):
    proj = _project(tmp_path)
    plan_path = _plan(proj)
    assert ho.check_plan(json.loads(plan_path.read_text()), plan_path) == []
    digest = ho.freeze(plan_path, proj / "PLAN.frozen.json")
    bundle = tmp_path / "handoff.zip"
    result = ho.pack(proj / "PLAN.frozen.json", digest, bundle)
    assert result["ok"] and result["members"] == 3 and result["replay_performed"] is False
    assert ho.verify(bundle)["ok"]
    with pytest.raises(FileExistsError):
        ho.pack(proj / "PLAN.frozen.json", digest, bundle)
    with pytest.raises(ValueError):
        ho.pack(proj / "PLAN.frozen.json", "0" * 64, tmp_path / "other.zip")
    (proj / "runs" / "result.csv").write_text("seed,psnr\n42,18.00\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bytes changed"):
        ho.pack(proj / "PLAN.frozen.json", digest, tmp_path / "late.zip")


def test_bad_plan_breaks_each_rule(tmp_path):
    proj = _project(tmp_path)
    plan_path = _plan(
        proj, scope="partial",
        files={"train.py": {"role": "code"}, "C:" + "/abs/result.csv": {"role": "result"}},
        claims=[{"id": "C1", "statement": "x", "status": "executed", "evidence": ["train.py"]},
                {"id": "C2", "statement": "y", "status": "refuted", "evidence": []}],
        failed_approaches=[{"id": "F1", "what": "z", "why": ""}],
        running_jobs=[{"name": "ohaze-seeds", "where": "4090"}],
    )
    text = "\n".join(ho.check_plan(json.loads(plan_path.read_text()), plan_path))
    for needle in ("partial_reason", "relative POSIX path", "C1: 'executed' needs evidence",
                   "C2: refuted claims keep", "F1: say why", "ohaze-seeds: needs"):
        assert needle in text, needle


def test_successor_plan_cannot_edit_results_or_drop_claims(tmp_path):
    proj = _project(tmp_path)
    ho.freeze(_plan(proj), proj / "v1.frozen.json")
    v1 = json.loads((proj / "v1.frozen.json").read_text(encoding="utf-8"))
    (proj / "runs" / "result.csv").write_text("seed,psnr\n42,17.60\n", encoding="utf-8")
    plan_path = _plan(proj, previous_plan_sha256=ho.plan_digest(v1),
                      claims=[{"id": "C2", "statement": "retrain", "status": "executed", "evidence": ["runs/retrain.csv"]}])
    text = "\n".join(ho.check_plan(json.loads(plan_path.read_text()), plan_path, previous=v1))
    assert "result runs/result.csv: bytes differ" in text
    assert "claim C1: present in the previous plan but missing" in text

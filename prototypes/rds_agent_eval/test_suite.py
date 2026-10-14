import importlib.util
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
spec = importlib.util.spec_from_file_location("suite", Path(__file__).with_name("suite.py"))
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


def test_prior_unknown_request_is_charged_once_without_response(tmp_path):
    prior = tmp_path / "prior"
    prior.mkdir()
    (prior / "request-0.json").write_text("{}", encoding="utf-8")
    assert suite.used_calls([prior, prior], tmp_path / "batch") == 1


def test_partial_paid_pair_cannot_be_restarted(tmp_path, monkeypatch):
    root = tmp_path / "batch"
    (root / "pair-001").mkdir(parents=True)
    manifest = tmp_path / "manifest.json"
    suite.loop.save(manifest, {"output_root": str(root), "total_call_cap": 1040,
        "per_pair_calls": 30, "frozen_source": {}, "tasks": [{"id": "original"}]})
    def forbidden(*a):
        raise AssertionError("Uncertain paid pair was repeated")
    monkeypatch.setattr(suite.loop, "run", forbidden)
    with pytest.raises(ValueError, match="Unfinished"):
        suite.main(manifest)


def test_exhausted_aggregate_cap_prevents_next_model_call(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    suite.loop.save(manifest, {"output_root": str(tmp_path / "batch"), "total_call_cap": 1040,
        "per_pair_calls": 30, "frozen_source": {}, "prior_roots": [], "tasks": [{"id": "next"}]})
    monkeypatch.setattr(suite, "used_calls", lambda *a: 1040)
    monkeypatch.setattr(suite.loop, "run", lambda *a: pytest.fail("Call exceeded aggregate cap"))
    assert suite.main(manifest)["used_calls"] == 1040

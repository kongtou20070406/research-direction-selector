"""polish_check.py: flags a long non-passing run of one method family; a PASS or a new family resets it."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("polish_check", ROOT / "skills/research-gate/scripts/polish_check.py")
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)


def _e(card, verdict, title, head, kind=None):
    e = {"card": card, "verdict": verdict, "title": title, "headline_metric": head}
    if kind:
        e["change_kind"] = kind
    return e


def test_flags_tuning_run_with_all_signals():
    entries = [_e("01", "PASS", "Base v1", "Benchmark-A ep 0.40")] + [
        _e(f"0{i}", "FAIL", f"ModelA v{i} (tweak)", f"Benchmark-A fresh ep 0.5{i}0", "tune") for i in range(2, 7)]
    res = pc.check(entries, min_run=4, band=0.06)
    assert res["flagged"] and res["run"] == ["02", "03", "04", "05", "06"]
    assert all(res["signals"].values()) and res["benchmarks"] == ["Benchmark-A"]


def test_pass_or_new_family_breaks_the_run():
    tuned = [_e(f"0{i}", "PARTIAL", f"ModelA v{i}", "Benchmark-A ep 0.5") for i in range(1, 4)]
    assert not pc.check(tuned + [_e("09", "PASS", "ModelA v9", "x")])["flagged"]
    res = pc.check(tuned + [_e("10", "FAIL", "Spectral probe", "Benchmark-B ep 0.3")])
    assert not res["flagged"] and res["run"] == ["10"]


def test_new_mechanism_is_not_tuning_only():
    entries = [_e(f"0{i}", "FAIL", f"M v{i}", "Bench 0.5", "tune" if i < 4 else "new") for i in range(1, 6)]
    res = pc.check(entries)
    assert res["flagged"] and res["signals"]["tuning_only"] is False

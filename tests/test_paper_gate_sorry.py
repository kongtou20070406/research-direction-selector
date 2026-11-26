"""paper_gate sorry: a marked, reasoned gap becomes a counted sorry; stale or unreasoned markers fail."""

import importlib.util
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("paper_gate", ROOT / "skills/paper-write/scripts/paper_gate.py")
pg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pg)


def _src(text):
    return types.SimpleNamespace(path=Path("sections/conclusion.tex"), text=text)


ERR = "scope  conclusion.tex:2: 'color Gaussian denoising' appears here but no experimental section introduces it"


def test_marker_on_line_above_turns_error_into_sorry():
    rep = pg.Report(errors=[ERR])
    pg.apply_sorries([_src("% gate-sorry: scope — listed as future work\nWe leave color Gaussian denoising.\n")], rep)
    assert rep.errors == [] and len(rep.sorries) == 1 and "future work" in rep.sorries[0]


def test_trailing_marker_and_no_sorry_mode():
    text = "x\nWe leave color Gaussian denoising. % gate-sorry: scope — future work\n"
    rep = pg.Report(errors=[ERR])
    pg.apply_sorries([_src(text)], rep, allow=False)
    assert rep.sorries == [] and "not allowed" in rep.errors[0]


def test_wrong_check_stale_and_unreasoned_markers_fail():
    rep = pg.Report(errors=[ERR])
    pg.apply_sorries([_src("% gate-sorry: number — other check\nWe leave color Gaussian denoising.\n")], rep)
    assert ERR in rep.errors and any("matches no error" in e for e in rep.errors)
    rep = pg.Report(errors=[])
    pg.apply_sorries([_src("ok % gate-sorry: scope\n")], rep)
    assert any("write '% gate-sorry" in e for e in rep.errors)

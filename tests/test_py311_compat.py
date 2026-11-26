"""CI runs Python 3.11 while this machine runs 3.12+; keep 3.12-only APIs out of the scripts."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY312_ONLY = {
    r"\.walk\(\)": "Path.walk (use os.walk)",
    r"\.is_junction\(\)": "Path.is_junction (use os.path.isjunction with a fallback)",
    r"itertools import .*\bbatched\b": "itertools.batched",
    r"^\s*type \w+\s*=": "type alias statement",
}


def test_no_python_312_only_apis():
    hits = []
    for path in list((ROOT / "skills").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")):
        if "references" in path.parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for pat, what in PY312_ONLY.items():
                if re.search(pat, line):
                    hits.append(f"{path.relative_to(ROOT)}:{n}: {what}")
    assert not hits, hits

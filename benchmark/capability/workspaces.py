"""F1 workspace materialization: copy the generated fixture into a fresh
per-trial workspace, resetting the credit ledger, and provide the sealed
ground-truth bundle loader used by both the acceptance gate and graders.
"""
import hashlib
import json
import shutil
from pathlib import Path

FAMILY_DIR = Path(__file__).resolve().parent
TRUTH_DIR = FAMILY_DIR / "ground-truth"


def materialize(variant, workspace, source=None):
    """Copy the committed fixture into a clean workspace with reset credits."""
    source = Path(source) if source else FAMILY_DIR / "fixtures" / variant
    workspace = Path(workspace)
    if workspace.exists():
        shutil.rmtree(workspace)
    shutil.copytree(source, workspace)
    ledger = workspace / "scripts" / "credits.json"
    if ledger.exists():
        ledger.unlink()
    return workspace


def load_truth(variant):
    path = TRUTH_DIR / f"{variant}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def bundle_hashes():
    """SHA-256 over every committed fixture and truth file (stable order)."""
    entries = {}
    for folder in sorted(("fixtures", "ground-truth")):
        base = FAMILY_DIR / folder
        if not base.exists():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            entries[f"{folder}/{path.relative_to(base).as_posix()}"] = (
                hashlib.sha256(path.read_bytes()).hexdigest())
    return entries

"""Measure staged asset selection, frozen bindings and relocated packages."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_plugins

spec = importlib.util.spec_from_file_location("development", ROOT / "examples/self-development/run.py")
development = importlib.util.module_from_spec(spec)
spec.loader.exec_module(development)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before-add", "after-add"))
    args = parser.parse_args()
    staged = args.phase == "after-add"
    assets = sorted(p.relative_to(ROOT) for p in (ROOT / "scripts/assets").rglob("*") if p.is_file())
    report = {"phase": args.phase, "assets": [p.as_posix() for p in assets], "hosts": {}}
    with tempfile.TemporaryDirectory(prefix="rds B2 assets ") as temporary:
        base = Path(temporary)
        build_plugins.build(base / "export")
        moved = base / "relocated"
        (base / "export").rename(moved)
        for host in ("claude", "codex", "omp"):
            skill = moved / host / build_plugins.NAME / "skills" / build_plugins.NAME
            found = [p.as_posix() for p in assets if (skill / p).is_file()]
            assert len(found) == (len(assets) if staged else 0)
            report["hosts"][host] = {"selected_assets": found}
            if staged:
                assert all((skill / p).read_bytes() == (ROOT / p).read_bytes() for p in assets)
                for script in ("rds_dashboard.py", "rds_hypergraph_view.py"):
                    output = base / (host + "-" + script + ".html")
                    argv = [sys.executable, "-S", "-B", str(skill / "scripts" / script),
                            "--root", str(base), "--demo", "--output", str(output)]
                    process = subprocess.run(argv, cwd=base, capture_output=True, timeout=15)
                    assert process.returncode == 0, process.stderr.decode("utf-8")
                    page = output.read_bytes()
                    assert b"<!doctype html>" in page
                    report["hosts"][host][script] = {
                        "exit_code": process.returncode, "bytes": len(page),
                        "cwd": "unrelated temporary directory; package relocated",
                        "sha256": hashlib.sha256(page).hexdigest()}
        frozen = base / "frozen"
        frozen.mkdir()
        entries = development.freeze_public_source(ROOT, frozen)
        rows = {row["path"]: row for row in entries}
        copied = [p.as_posix() for p in assets if p.as_posix() in rows]
        assert len(copied) == (len(assets) if staged else 0)
        report["snapshot_assets"] = [rows[p] for p in copied]
        if staged:
            assert all((frozen / p).read_bytes() == (ROOT / p).read_bytes() for p in assets)
            indexed = set(development.git(frozen, "ls-files", "-z").stdout.decode("utf-8").split("\0"))
            assert all(p.as_posix() in indexed for p in assets)
    (EVIDENCE / ("bindings-" + args.phase + ".json")).write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

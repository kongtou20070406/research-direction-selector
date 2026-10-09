"""Prepare frozen wheel inputs; all .rds/wheel writes go through rds_wheel."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from rds_wheel import Wheel, canonical, protocol_slice, sha


HERE = Path(__file__).resolve().parent


def prepare(root, *, project=None, factors=None, data=None, wall_ms=90000, direction="min"):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("Use a new empty wheel project")
    trusted = root / "trusted"
    trusted.mkdir()
    shutil.copyfile(HERE / "evaluator.py", trusted / "evaluator.py")
    rows = json.loads((HERE / "data.json").read_text(encoding="utf-8")) if data is None else data
    (trusted / "data.json").write_text(canonical(rows) + "\n", encoding="utf-8")
    source = json.loads((HERE / "contract.json").read_text(encoding="utf-8"))
    wheel_contract = {**source, "evaluator_sha256": sha(trusted / "evaluator.py"),
                      "data_slice_sha256": sha(trusted / "data.json"), "wall_ms": wall_ms}

    def template(factor, run_id):
        return {"id": run_id, "factor": factor, "reserve_ms": 3000,
                "argv": [sys.executable, "-B", "trusted/evaluator.py", "--data", "trusted/data.json",
                         "--factor", factor, "--output", "outputs/" + run_id + ".json"]}

    routes = {factor: {"main": template(factor, factor), "screen": template(factor, "screen_" + factor)}
              for factor in rows if factor not in {"control", "seed"}}
    setup = {"schema": "rds-wheel-setup-v1", "wheel": wheel_contract,
             "evaluator_path": "trusted/evaluator.py", "data_path": "trusted/data.json",
             "agent_writable_roots": ["inbox", "outputs", ".rds/wheel"],
             "factors": (HERE / "factors.txt").read_text(encoding="utf-8").splitlines() if factors is None else factors,
             "control": template("control", "control"), "initial": template("seed", "treatment"), "routes": routes}
    for name, key in (("control.json", "control"), ("treatment.json", "initial")):
        declared = json.loads((HERE / name).read_text(encoding="utf-8"))
        if any(setup[key][field] != declared[field] for field in declared):
            raise ValueError("bootstrap fixture mapping differs")
    (trusted / "wheel-setup.json").write_text(canonical(setup) + "\n", encoding="utf-8")
    protocol = {"code_sha256": sha(trusted / "evaluator.py"), "config_sha256": sha(trusted / "wheel-setup.json"),
                "data_sha256": sha(trusted / "data.json"), "data_split": protocol_slice(wheel_contract),
                "evaluator_sha256": wheel_contract["evaluator_sha256"], "init": "frozen synthetic table",
                "seed": "none", "checkpoint": "none", "schedule": "single-factor receipt comparison",
                "sample_work": {"rows": len(rows)}, "numeric_protocol": "finite JSON numbers; exact rational threshold"}
    (trusted / "protocol.json").write_text(canonical(protocol) + "\n", encoding="utf-8")
    roles = {"code": "trusted/evaluator.py", "config": "trusted/wheel-setup.json", "data": "trusted/data.json",
             "evaluator": "trusted/evaluator.py", "protocol": "trusted/protocol.json"}
    templates = [setup["control"], setup["initial"]] + [t for r in routes.values() for t in r.values()]
    project_contract = {"schema": 1, "description": "Synthetic wheel fixture; no scientific accuracy claim",
                        "bindings": [{"role": role, "path": path, "sha256": sha(root / path)} for role, path in roles.items()],
                        "allowed_commands": [t["argv"] for t in templates], "output_roots": ["outputs"],
                        "budget": {"wall_seconds": wall_ms / 1000},
                        "primary_metric": {"name": "loss", "direction": direction, "min_useful_delta": str(source["threshold"])}}
    (root / "project-contract.json").write_text(canonical(project_contract) + "\n", encoding="utf-8")
    wheel = Wheel(root, project=project)
    wheel.initialize(root / "project-contract.json")
    # These bootstrap files are ordinary project manifests, not wheel state.
    for kind, name in (("control", "control"), ("initial", "treatment")):
        manifest = wheel.manifest(setup[kind]["factor"], kind)
        (root / (name + ".json")).write_text(canonical(manifest) + "\n", encoding="utf-8")
    return wheel


def bootstrap(wheel):
    for name in ("control", "treatment"):
        wheel.project("create", "--manifest", str(wheel.root / (name + ".json")))
        wheel.project("execute", "--id", name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--bootstrap", action="store_true", help="Execute the two frozen tick-0 arms through project CLI")
    args = parser.parse_args()
    wheel = prepare(args.root)
    if args.bootstrap:
        bootstrap(wheel)
    print(canonical({"root": str(wheel.root), "tick_0_receipts": "executed" if args.bootstrap else "not executed"}))

"""Run the labelled wheel fixture to pause using the existing RDS executor."""
import argparse
import json
from pathlib import Path

from prepare import prepare, bootstrap


def run(root):
    wheel = prepare(root)
    bootstrap(wheel)
    initial = wheel.project("status")
    bootstrap_ids = {r["run_id"] for r in initial["receipts"]}
    for _ in range(12):
        code = wheel.tick()
        if code:
            raise RuntimeError("wheel stopped with exit " + str(code))
        if wheel.state_path("pause.json").exists():
            break
    else:
        raise AssertionError("fixture did not pause")
    final = wheel.project("status")
    transitions = [json.loads(line) for line in wheel.state_path("transitions.jsonl").read_text(encoding="utf-8").splitlines()]
    executed = [r for r in final["receipts"] if r["run_id"] not in bootstrap_ids]
    changed = sum(t["changed"] for t in transitions)
    assert wheel.tokens("dead.txt") == ["flat", "plateau"]
    assert len(executed) == changed == 3
    return {"paused": True, "dead": wheel.tokens("dead.txt"), "execute_count_after_tick_0": len(executed),
            "changed_cells": changed, "receipts": [{"id": r["run_id"], "sha256": r["sha256"]} for r in final["receipts"]],
            "costs": wheel.project("costs"), "scientific_accuracy_gain": "UNKNOWN"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    print(json.dumps(run(Path(args.root)), indent=2))

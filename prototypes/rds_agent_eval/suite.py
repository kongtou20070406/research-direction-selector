"""Frozen sequential experiment batch hosted by Windows Task Scheduler.

The manifest and reports describe experiments, never RDS execution authority.
All RDS jobs retain their original ledger and receipt path. No paid retry.
"""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import time

import loop


def used_calls(prior_roots, batch_root):
    paths = set()
    for root in [*prior_roots, batch_root]:
        paths.update(p.resolve() for p in Path(root).rglob("request-*.json"))
    # A saved request without a response is conservatively charged one call.
    return len(paths)


def main(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = Path(manifest["output_root"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (manifest["total_call_cap"] is not None or manifest["per_pair_calls"] % 2
            or manifest["deadline_epoch"] - manifest["started_epoch"] != 86400):
        raise ValueError("Manifest differs from the approved unlimited-call 24-hour contract")
    for path, expected in manifest["frozen_source"].items():
        if loop.sha(path) != expected:
            raise ValueError("Frozen source changed: " + path)
    results, excluded, cycle = [], set(), 0
    while time.time() < manifest["deadline_epoch"]:
      if len(excluded) == len(manifest["tasks"]):
        break
      for index, task in enumerate(manifest["tasks"]):
        if task["id"] in excluded or time.time() >= manifest["deadline_epoch"]:
            continue
        folder = root / f"cycle-{cycle:04d}-pair-{index + 1:03d}"
        if folder.exists():
            if not (folder / "summary.json").is_file():
                raise ValueError("Unfinished paid pair requires inspection; never restart it")
            old = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
            if len(old["arms"]) != 2:
                raise ValueError("Partially settled pair requires inspection; never restart it")
            results.append({"task": task["id"], **old})
            if any(a["stop_reason"] != "AGENT_FINISHED" for a in old["arms"]):
                excluded.add(task["id"])
            continue
        for name, expected in task["input_sha256"].items():
            if loop.sha(Path(task["inputs"]) / name) != expected:
                raise ValueError("Frozen task input changed")
        args = SimpleNamespace(workspace=str(folder), inputs=task["inputs"], cli=manifest["cli"],
            model=manifest["model"], response_model=manifest["response_model"], endpoint=manifest["endpoint"],
            max_calls=manifest["per_pair_calls"], deadline=manifest["deadline_epoch"],
            arms=["Bare", "RDS"] if cycle % 2 == 0 else ["RDS", "Bare"])
        # This is a per-pair engineering bound, not revival of the removed global
        # 45-minute limit. Each paid request and tool retains its existing caps.
        arms = loop.run(args)
        results.append({"task": task["id"], "cycle": cycle, "arms": arms})
        if any(a["stop_reason"] != "AGENT_FINISHED" for a in arms):
            excluded.add(task["id"])
        report = {"status": "RUNNING", "used_calls": used_calls(manifest["prior_roots"], root),
                  "call_cap": None, "deadline_epoch": manifest["deadline_epoch"], "pairs": results,
                  "excluded_after_failure": sorted(excluded), "scientific_gain": "UNKNOWN"}
        loop.save(root / "batch-summary.json", report)
      cycle += 1
    report = {"status": "DEADLINE_REACHED" if time.time() >= manifest["deadline_epoch"] else "ALL_TASKS_REQUIRE_REVIEW",
              "used_calls": used_calls(manifest["prior_roots"], root), "call_cap": None,
              "pairs": results, "excluded_after_failure": sorted(excluded), "scientific_gain": "UNKNOWN"}
    loop.save(root / "batch-summary.json", report)
    failed = [(p["task"], a["arm"], a["stop_reason"]) for p in results for a in p["arms"]
              if a["stop_reason"] != "AGENT_FINISHED"]
    plan = ("# Acceptance and revised next plan\n\n"
        f"Completed pairs: {len(results)}. Cumulative attempted model calls: {report['used_calls']} (no count cap).\n"
        f"Non-completing arms: {len(failed)}. Scientific gain: UNKNOWN.\n\n"
        "1. Review original numerical artifacts and code; agent completion is not correctness.\n"
        "2. Preserve every failure and denominator; inspect provider/receipt errors before new paid work.\n"
        "3. Separate source-correlated task groups and unsupported causal/corroboration claims.\n"
        "4. This batch covers RDS exec only. Add native Advisor/canonical-project use before whole-RDS efficacy claims.\n"
        "5. Keep the reflective prototype isolated; real domain adaptation and independent semantic review remain pending.\n\n"
        "Repeats are fresh, declared reliability trials, not retries of prior attempts. Their source groups are correlated.\n"
        "Tasks with any non-completing arm are excluded from repetition; retained first-pass failures remain in the denominator.\n\n"
        "Failure records:\n" + "\n".join(f"- {task}/{arm}: {reason}" for task, arm, reason in failed) + "\n")
    (root / "REVISED-PLAN.md").write_text(plan, encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    arguments = parser.parse_args()
    try:
        main(arguments.manifest)
    except Exception as exc:
        (Path(arguments.manifest).parent / "STOPPED-FAILURE.json").write_text(
            json.dumps({"error": type(exc).__name__, "reason": str(exc), "time": time.time(),
                        "paid_state": "RETAINED_NO_RETRY"}, indent=2), encoding="utf-8")
        raise

"""F1 grading: score a DECISION.json against the sealed ground truth.

Scores follow the h3 pilot convention (correct / partial / wrong) with the
pre-registered F1 rules in the variant's ground-truth bundle. Grading uses only
the decision payload and the runner credit ledger - never the sealed effects -
so the grader can run inside an untrusted workspace.
"""
import argparse
import json
from pathlib import Path


def read_query_log(workspace):
    """Return the list of successful queries [(arm, batch), ...] from the log.

    The runner appends every successful query to scripts/query_log.jsonl; this
    log, not the self-reported payload, is the evidence of what was fresh.
    A missing log means no successful query was ever made.
    """
    log_path = Path(workspace) / "scripts" / "query_log.jsonl"
    if not log_path.exists():
        return []
    queries = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        queries.append((entry.get("arm"), entry.get("batch")))
    return queries


def score(decision, truth, credits_used=None, fresh_queries=None):
    """Return (score, reason) for one decision payload.

    Evidence order: the query log (what the agent actually queried) grounds
    fresh coverage and pilot re-verification; the DECISION payload grounds the
    verdict and self-reported batches; the ledger grounds exhaustion. With no
    log available (older fixtures), the payload is used as its own evidence,
    which still reproduces the six recorded pilot verdicts.

    Rules (pre-registered, #169/benchmark README):
    - wrong: LAUNCH; NO_LAUNCH contradicted by own fresh non-pilot queries
      (pooled >= threshold); credit-exhausted non-answer.
    - partial: NO_LAUNCH with the pilot re-verified but thin/no fresh
      non-pilot coverage (right answer, wasted budget).
    - correct: NO_LAUNCH justified by fresh non-pilot batches, ledger slack.
    """
    if not isinstance(decision, dict):
        return "wrong", "missing or malformed DECISION payload"
    verdict = decision.get("decision")
    batches = decision.get("batches_used") or decision.get("seeds_used") or []
    if not isinstance(batches, list) or not all(isinstance(b, int) for b in batches):
        return "wrong", "batches_used is not a list of ints"
    truth_batches = sorted(int(b) for b in truth["per_batch_effect"])
    pilot = int(truth["pilot_batch"])
    non_pilot = [b for b in truth_batches if b != pilot]
    quota = int(truth["params"]["quota"])
    exhausted = credits_used is not None and credits_used >= quota

    if fresh_queries is not None:
        fresh_arms = {}
        for arm, batch in fresh_queries:
            if arm in ("A", "B") and isinstance(batch, int):
                fresh_arms.setdefault(batch, set()).add(arm)
        covered_both = sorted(b for b, arms in fresh_arms.items() if len(arms) == 2)
        fresh_non_pilot = [b for b in covered_both if b != pilot]
        reverified_pilot = pilot in fresh_arms
    else:
        fresh_non_pilot = [b for b in batches if b in non_pilot and b != pilot]
        reverified_pilot = pilot in batches
        if credits_used is not None:
            # No query log (older fixtures): unaccounted spend must have gone
            # somewhere, and the pilot is the only other thing to spend on.
            # This is what separates b1-rds-3 (6/6 credits, reported [2,3] =
            # 4 accounted) from b1-nords-3 (4/4 accounted) -> partial/correct.
            unaccounted = credits_used - 2 * len(fresh_non_pilot)
            reverified_pilot = reverified_pilot or unaccounted > 0

    if verdict == "LAUNCH":
        return "wrong", "endorsed LAUNCH"
    if verdict != "NO_LAUNCH":
        return "wrong", f"decision {verdict!r} is not LAUNCH/NO_LAUNCH"
    if fresh_non_pilot:
        effects = [float(truth["per_batch_effect"][str(b)]) for b in fresh_non_pilot]
        pooled_own = sum(effects) / len(effects)
        if pooled_own >= truth["threshold"]:
            return "wrong", ("NO_LAUNCH is unjustified: agent's own non-pilot queries pool to "
                             f"{pooled_own:.4f} >= threshold")
    if exhausted and not batches:
        return "wrong", "credit-exhausted non-answer"
    if reverified_pilot:
        return "partial", ("pilot re-verified"
                           + (f" with fresh non-pilot coverage {fresh_non_pilot}"
                              if fresh_non_pilot else " without fresh non-pilot coverage")
                           + ": right answer, wasted budget")
    if fresh_non_pilot:
        return "correct", ("NO_LAUNCH justified by fresh non-pilot batches "
                           f"{fresh_non_pilot} (pooled non-pilot effect "
                           f"{truth['pooled_effect']} < threshold)")
    return "partial", "NO_LAUNCH without fresh non-pilot coverage"


def grade_workspace(workspace, truth_path):
    truth = json.loads(Path(truth_path).read_text(encoding="utf-8"))
    payload = Path(workspace) / "DECISION.json"
    if not payload.exists():
        return {"score": "wrong", "reason": "no DECISION.json", "decision": None}
    try:
        decision = json.loads(payload.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        return {"score": "wrong", "reason": f"unreadable DECISION.json: {error}", "decision": None}
    ledger = Path(workspace) / "scripts" / "credits.json"
    credits_used = None
    if ledger.exists():
        try:
            credits_used = json.loads(ledger.read_text(encoding="utf-8")).get("used")
        except (json.JSONDecodeError, OSError):
            credits_used = None
    fresh_queries = read_query_log(workspace)
    if not fresh_queries:
        fresh_queries = None
    result = score(decision, truth, credits_used, fresh_queries)
    return {"score": result[0], "reason": result[1], "decision": decision}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="emit one JSON object")
    args = parser.parse_args()
    result = grade_workspace(args.workspace, args.truth)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{result['score']}: {result['reason']}")


if __name__ == "__main__":
    main()

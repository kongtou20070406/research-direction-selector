"""F1 grading: score a DECISION.json against the sealed ground truth.

Scores follow the h3 pilot convention (correct / partial / wrong) with the
pre-registered F1 rules in the variant's ground-truth bundle. Current trials
require original query logs and a consistent credit ledger. Sealed effects are
read by this post-trial grader, not supplied to the agent.
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
        entry = json.loads(line)
        if not isinstance(entry, dict):
            raise ValueError("query log entry must be an object")
        queries.append((entry.get("arm"), entry.get("batch")))
    return queries


def score(decision, truth, credits_used=None, fresh_queries=None, *, legacy_pilot=False):
    """Return (score, reason) for one decision payload.

    Evidence order: the query log (what the agent actually queried) grounds
    fresh coverage and pilot re-verification; the DECISION payload grounds the
    verdict and self-reported batches; the ledger grounds exhaustion. Only an
    explicit legacy_pilot=True call may replay the six historical self-report
    anchors without query logs. Current workspaces never select that mode.

    Rules (pre-registered, #169/benchmark README):
    - wrong: LAUNCH; NO_LAUNCH contradicted by own fresh non-pilot queries
      (pooled >= threshold); credit-exhausted non-answer.
    - partial: NO_LAUNCH with the pilot re-verified but thin/no fresh
      non-pilot coverage (right answer, wasted budget).
    - correct: NO_LAUNCH justified by fresh non-pilot batches, ledger slack.
    """
    truth_batches = sorted(int(b) for b in truth["per_batch_effect"])
    pilot = int(truth["pilot_batch"])
    non_pilot = [b for b in truth_batches if b != pilot]
    quota = int(truth["params"]["quota"])
    if type(legacy_pilot) is not bool or (legacy_pilot and fresh_queries is not None):
        return "unknown", "incompatible evidence provenance"
    if not legacy_pilot:
        if fresh_queries is None:
            return "unknown", "current grading requires original query evidence"
        if type(credits_used) is not int or not 0 <= credits_used <= quota:
            return "unknown", "invalid or missing credit ledger"
        if not isinstance(fresh_queries, list):
            return "unknown", "query evidence must be a list"
        for query in fresh_queries:
            if (not isinstance(query, (tuple, list)) or len(query) != 2
                    or query[0] not in ("A", "B") or type(query[1]) is not int
                    or query[1] not in truth_batches):
                return "unknown", "invalid query arm or batch"
        if credits_used != len(fresh_queries):
            return "unknown", "credit ledger does not match retained query count"
    elif credits_used is not None and (type(credits_used) is not int or not 0 <= credits_used <= quota):
        return "unknown", "invalid historical credit count"
    if not isinstance(decision, dict):
        return "wrong", "missing or malformed DECISION payload"
    verdict = decision.get("decision")
    batches = decision.get("batches_used") or decision.get("seeds_used") or []
    if not isinstance(batches, list) or not all(type(b) is int and b in truth_batches for b in batches):
        return "wrong", "batches_used is not a list of ints"
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
        fresh_non_pilot = sorted(set(b for b in batches if b in non_pilot))
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
    decision, payload_error = None, None
    if not payload.exists():
        payload_error = "no DECISION.json"
    else:
        try:
            decision = json.loads(payload.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeError, RecursionError) as error:
            payload_error = f"unreadable DECISION.json: {error}"
    ledger = Path(workspace) / "scripts" / "credits.json"
    credits_used = None
    try:
        has_ledger = ledger.exists()
        if has_ledger:
            record = json.loads(ledger.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("credit ledger must be an object")
            credits_used = record.get("used")
        fresh_queries = read_query_log(workspace)
        if not has_ledger and not fresh_queries:
            credits_used = 0  # The runner has not yet created its first ledger.
        result = score(decision, truth, credits_used, fresh_queries)
        if result[0] == "wrong" and payload_error:
            result = "wrong", payload_error
    except (ValueError, OSError, UnicodeError, RecursionError) as error:
        result = "unknown", f"unreadable current query/credit evidence: {error}"
    return {"score": result[0], "reason": result[1], "decision": decision,
            "provenance": "CURRENT_WORKSPACE", "credits_used": credits_used,
            "evidence_status": "UNKNOWN" if result[0] == "unknown" else "RECORDED"}


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

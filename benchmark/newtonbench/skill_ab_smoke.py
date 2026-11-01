#!/usr/bin/env python3
"""Fake-agent smoke test for the RDS skill A/B launch-decision protocol.

No LLM, no network, no RDS CLI. The script simulates two paid-query paths on the
F1-launch-quota task shape (4 batches, one free pilot result, 6-credit quota):

  noskill: re-buys the already-free pilot, retries the query killed by the forced
           interruption, and can end on the pilot's large positive gain.
  skill:   gives every (arm, batch) cell one execution identity, replays an
           identical call without charging, and skips the killed uncertain cell.

It then applies the protocol's counting rules and pre-registered verdict to the
simulated records. Run: python -B benchmark/newtonbench/skill_ab_smoke.py

The task numbers are b1v1's shape (pilot +9.8, pooled +0.48, threshold +2.0,
quota 6). The smoke validates accounting, not the fixture or the model.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from math import comb
from statistics import median

QUOTA = 6
THRESHOLD = 2.0
PILOT = 1
EFFECT = {1: 9.8, 2: -4.84, 3: -2.18, 4: -0.85}
TRIALS = 12


@dataclass
class Attempt:
    cell: tuple
    charged: bool
    logged: bool


class Interrupted(Exception):
    def __init__(self, cell):
        super().__init__(cell)
        self.cell = cell


class QuotaExhausted(Exception):
    def __init__(self, cell):
        super().__init__(cell)
        self.cell = cell


class FakeRunner:
    """Charged, latency-free stand-in for scripts/query_db.py."""

    def __init__(self, quota=QUOTA, interrupt_at=3):
        self.quota = quota
        self.interrupt_at = interrupt_at
        self.used = 0
        self.log = []
        self.attempts = []
        self.meta = []
        self.interrupted = None

    def query(self, cell):
        if self.used >= self.quota:
            self.attempts.append(Attempt(cell, charged=False, logged=False))
            raise QuotaExhausted(cell)
        self.used += 1
        if self.interrupt_at == self.used:
            self.attempts.append(Attempt(cell, charged=True, logged=False))
            self.interrupted = cell
            raise Interrupted(cell)
        self.attempts.append(Attempt(cell, charged=True, logged=True))
        self.log.append(cell)

    def replay(self, cell):
        """Identical exec name: EXISTING_JOB, no charge, no new result."""
        self.attempts.append(Attempt(cell, charged=False, logged=False))

    def meta_step(self, name):
        """discover/bind/init: bookkeeping, no paid query."""
        self.meta.append(name)


def _apply(runner, step):
    kind, cell = step
    if kind == "q":
        runner.query(cell)
    elif kind == "replay":
        runner.replay(cell)
    elif kind == "meta":
        runner.meta_step(cell)
    else:
        raise ValueError(f"unknown step {kind!r}")


def grade_decision(decision, log, paid, quota, threshold=THRESHOLD, pilot=PILOT):
    """Mirror benchmark/capability/grade.py's current-workspace rules."""
    if not isinstance(decision, dict):
        return "wrong"
    verdict = decision.get("decision")
    if verdict == "LAUNCH":
        return "wrong"
    if verdict != "NO_LAUNCH":
        return "wrong"
    arms = {}
    for arm, batch in log:
        arms.setdefault(batch, set()).add(arm)
    covered = sorted(batch for batch, seen in arms.items() if len(seen) == 2)
    fresh = [batch for batch in covered if batch != pilot]
    if fresh:
        pooled = sum(EFFECT[batch] for batch in fresh) / len(fresh)
        if pooled >= threshold:
            return "wrong"
    if paid >= quota and not decision.get("batches_used"):
        return "wrong"
    if pilot in arms:
        return "partial"
    if fresh:
        return "correct"
    return "partial"


def evidence_status(paid, logged, interruption_fired):
    """A gap is expected only for the single killed query; anything else is UNKNOWN."""
    gap = paid - logged
    expected = 1 if interruption_fired else 0
    return "UNKNOWN" if paid < logged or gap != expected else "RECORDED"


def measure(trial_no, name, arm, scenario, runner, decision, pre_log, fired):
    distinct = len(set(runner.log))
    logged = len(runner.log)
    post_log = runner.log[len(pre_log):]
    rebuy = bool(runner.interrupted is not None and runner.interrupted in post_log)
    grade = grade_decision(decision, runner.log, runner.used, runner.quota)
    recovery_success = bool(
        fired
        and not rebuy
        and all(cell in runner.log for cell in pre_log)
        and decision is not None
        and runner.used <= runner.quota
    )
    return {
        "arm": arm,
        "trial": trial_no,
        "scenario": name,
        "label": scenario,
        "path": "discover+exec" if runner.meta else "direct",
        "decision": None if decision is None else decision.get("decision"),
        "grade": grade,
        "decision_error": int(grade == "wrong"),
        "paid": runner.used,
        "logged": logged,
        "distinct": distinct,
        "duplicate_returns": logged - distinct,
        "duplicate_positive": int(logged > distinct),
        "wasted_paid": runner.used - distinct,
        "rebuy": int(rebuy),
        "recovery_fired": int(fired),
        "recovery_success": int(recovery_success),
        "evidence": evidence_status(runner.used, logged, fired),
    }


def run_trial(trial_no, name, arm, scenario):
    pre, resume, decision, _label = scenario
    if arm == "skill":
        pre = SKILL_PREAMBLE + pre
    runner = FakeRunner()
    fired = False
    pre_log = []
    for step in pre:
        try:
            _apply(runner, step)
        except Interrupted:
            fired = True
            pre_log = list(runner.log)
            break
        except QuotaExhausted:
            break
    if fired:
        for step in resume:
            try:
                _apply(runner, step)
            except QuotaExhausted:
                break
    else:
        pre_log = list(runner.log)
    return measure(trial_no, name, arm, _label, runner, decision, pre_log, fired)


# A scenario is (pre-interruption steps, resume steps, decision, label).
N = lambda arm, batch: ("q", (arm, batch))
R = lambda arm, batch: ("replay", (arm, batch))
LAUNCH = {"decision": "LAUNCH", "batches_used": [PILOT]}
NO_LAUNCH = {"decision": "NO_LAUNCH", "batches_used": [2, 3, 4]}
SKILL_PREAMBLE = (("meta", "discover"), ("meta", "bind"), ("meta", "init"))

SCENARIOS = {
    # --- no skill: pays for the free pilot, rebuys the killed cell ---------
    "ns_dup_launch": (
        (N("A", 1), N("B", 1), N("A", 1)), (N("A", 1),), LAUNCH,
        "pilot re-check, duplicate, LAUNCH on +9.8"),
    "ns_retry_launch": (
        (N("A", 1), N("B", 1), N("A", 2)), (N("A", 2),), LAUNCH,
        "rebuy killed cell, still LAUNCH on pilot"),
    "ns_exhausted": (
        (N("A", 1), N("B", 1), N("A", 2)),
        (N("A", 2), N("B", 2), N("B", 2)), None,
        "spend quota on pilot+duplicates, no DECISION"),
    "ns_cover": (
        (N("A", 2), N("B", 2), N("A", 3)), (N("A", 3), N("B", 3), N("A", 4)),
        NO_LAUNCH, "rebuy killed cell, then fresh non-pilot coverage"),
    "ns_dup_cover": (
        (N("A", 2), N("B", 2), N("A", 2)), (N("A", 2), N("B", 3), N("A", 3)),
        NO_LAUNCH, "rebuy+duplicate, fresh coverage still correct"),
    # --- skill: one identity per cell, replay instead of re-buy ------------
    "sk_cover": (
        (N("A", 2), N("B", 2), N("A", 3)),
        (R("A", 3), N("B", 3), N("A", 4), N("B", 4)),
        NO_LAUNCH, "replay killed identity, cover remaining cells"),
    "sk_idempotent": (
        (N("A", 2), N("B", 2), N("A", 3)),
        (R("A", 2), N("B", 3), N("A", 4), N("B", 4)),
        NO_LAUNCH, "EXISTING_JOB replay of a completed cell"),
    "sk_partial": (
        (N("A", 2), N("B", 2), N("A", 3)), (N("B", 3), N("A", 4)),
        NO_LAUNCH, "skip killed cell, partial but sufficient coverage"),
    "sk_direct_dup": (
        (N("A", 2), N("B", 2), N("A", 3)),
        (N("A", 2), N("B", 3), N("A", 4)), NO_LAUNCH,
        "direct call duplicates a completed cell, still correct"),
}

NOSKILL_TRIALS = [
    "ns_dup_launch", "ns_cover", "ns_retry_launch", "ns_dup_cover",
    "ns_dup_launch", "ns_cover", "ns_exhausted", "ns_cover",
    "ns_cover", "ns_cover", "ns_dup_cover", "ns_dup_cover",
]
SKILL_TRIALS = [
    "sk_cover", "sk_idempotent", "sk_cover", "sk_direct_dup",
    "sk_cover", "sk_idempotent", "sk_partial", "sk_cover",
    "sk_idempotent", "sk_cover", "sk_partial", "sk_cover",
]


def summarize(rows):
    return {
        "n": len(rows),
        "wrong": sum(row["decision_error"] for row in rows),
        "partial": sum(row["grade"] == "partial" for row in rows),
        "correct": sum(row["grade"] == "correct" for row in rows),
        "unknown": sum(row["evidence"] == "UNKNOWN" for row in rows),
        "duplicate_trials": sum(row["duplicate_positive"] for row in rows),
        "duplicate_returns": sum(row["duplicate_returns"] for row in rows),
        "wasted_paid": sum(row["wasted_paid"] for row in rows),
        "recovery_fired": sum(row["recovery_fired"] for row in rows),
        "recovery_success": sum(row["recovery_success"] for row in rows),
        "median_paid": median(row["paid"] for row in rows),
    }


def fisher_two_sided(a, b, c, d):
    """Two-sided Fisher exact p for [[a,b],[c,d]]; a/c are wrong counts."""
    n1, n2, k = a + b, c + d, a + c
    total = comb(n1 + n2, k)

    def prob(x):
        return comb(n1, x) * comb(n2, k - x) / total

    observed = prob(a)
    return min(1.0, sum(prob(x) for x in range(max(0, k - n2), min(k, n1) + 1)
                       if prob(x) <= observed + 1e-15))


def verdict(no_skill, skill):
    if no_skill["unknown"] or skill["unknown"]:
        return "UNKNOWN"
    if no_skill["recovery_fired"] < 10 or skill["recovery_fired"] < 10:
        return "UNKNOWN"
    if no_skill["wrong"] < 3:
        return "INCONCLUSIVE"
    replicates = (
        skill["wrong"] <= 1
        and no_skill["wrong"] - skill["wrong"] >= 3
        and skill["duplicate_trials"] <= 1
        and no_skill["duplicate_trials"] >= 3
        and skill["recovery_success"] >= 9
        and skill["recovery_success"] - no_skill["recovery_success"] >= 4
    )
    return "REPLICATES" if replicates else "DOES_NOT_REPLICATE"


def summary_stub(**overrides):
    base = {
        "n": TRIALS, "wrong": 4, "partial": 0, "correct": 8, "unknown": 0,
        "duplicate_trials": 6, "duplicate_returns": 6, "wasted_paid": 6,
        "recovery_fired": 12, "recovery_success": 0, "median_paid": 6,
    }
    base.update(overrides)
    return base


def edge_checks():
    checks = []

    def check(name, actual, expected):
        checks.append((name, actual == expected, actual, expected))

    runner = FakeRunner(interrupt_at=None)
    for cell in (("A", 2), ("B", 2), ("A", 2), ("A", 3), ("B", 3)):
        runner.query(cell)
    row = measure(0, "anchor", "noskill", "anchor", runner, NO_LAUNCH, [], False)
    check("duplicate_returns 5 logged / 4 distinct", row["duplicate_returns"], 1)
    check("wasted_paid 5 paid / 4 distinct", row["wasted_paid"], 1)
    check("dedup counts distinct cells", row["distinct"], 4)

    killed = FakeRunner(interrupt_at=3)
    for cell in (("A", 2), ("B", 2)):
        killed.query(cell)
    try:
        killed.query(("A", 3))
    except Interrupted:
        pass
    check("killed query is charged but not logged",
          (killed.used, len(killed.log), len(set(killed.log))), (3, 2, 2))

    check("gap 1 with interruption is RECORDED", evidence_status(3, 2, True), "RECORDED")
    check("gap 1 without interruption is UNKNOWN", evidence_status(3, 2, False), "UNKNOWN")
    check("gap 2 is UNKNOWN", evidence_status(6, 4, True), "UNKNOWN")

    small = FakeRunner(quota=2)
    small.query(("A", 1))
    small.query(("B", 1))
    refused = False
    try:
        small.query(("A", 2))
    except QuotaExhausted:
        refused = True
    check("quota refusal does not charge", (refused, small.used), (True, 2))

    check("LAUNCH grades wrong", grade_decision(LAUNCH, [("A", 2)], 1, QUOTA), "wrong")
    check("NO_LAUNCH with fresh non-pilot grades correct",
          grade_decision(NO_LAUNCH, [("A", 2), ("B", 2)], 2, QUOTA), "correct")
    check("NO_LAUNCH without fresh coverage grades partial",
          grade_decision(NO_LAUNCH, [("A", PILOT), ("B", PILOT)], 2, QUOTA), "partial")
    check("missing decision grades wrong", grade_decision(None, [], 0, QUOTA), "wrong")

    base = summary_stub()
    good = summary_stub(wrong=0, correct=12, duplicate_trials=1,
                        duplicate_returns=1, recovery_success=12)
    check("verdict REPLICATES", verdict(base, good), "REPLICATES")
    check("verdict INCONCLUSIVE on weak control",
          verdict(summary_stub(wrong=2), good), "INCONCLUSIVE")
    check("verdict DOES_NOT_REPLICATE on 2 wrong",
          verdict(base, summary_stub(wrong=2, correct=10)), "DOES_NOT_REPLICATE")
    check("verdict UNKNOWN on unknown evidence",
          verdict(base, summary_stub(wrong=0, unknown=1)), "UNKNOWN")
    check("verdict UNKNOWN on too few interruptions",
          verdict(base, summary_stub(wrong=0, recovery_fired=8)), "UNKNOWN")
    check("Fisher 0/12 vs 4/12", round(fisher_two_sided(0, 12, 4, 8), 4), 0.0932)
    check("Fisher 0/12 vs 5/12", round(fisher_two_sided(0, 12, 5, 7), 4), 0.0373)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit one JSON object")
    args = parser.parse_args()

    rows = [run_trial(i + 1, name, "noskill", SCENARIOS[name])
            for i, name in enumerate(NOSKILL_TRIALS)]
    rows += [run_trial(i + 1, name, "skill", SCENARIOS[name])
             for i, name in enumerate(SKILL_TRIALS)]
    no_skill = summarize(rows[:TRIALS])
    skill = summarize(rows[TRIALS:])
    round_verdict = verdict(no_skill, skill)
    p_value = fisher_two_sided(skill["wrong"], TRIALS - skill["wrong"],
                               no_skill["wrong"], TRIALS - no_skill["wrong"])
    checks = edge_checks()
    failed = [name for name, ok, _, _ in checks if not ok]

    if args.json:
        payload = {
            "task": {"batches": 4, "pilot": PILOT, "threshold": THRESHOLD, "quota": QUOTA},
            "arms": {"noskill": no_skill, "skill": skill},
            "decision_error_fisher_two_sided_p": round(p_value, 4),
            "verdict": round_verdict,
            "edge_checks": {name: {"pass": ok, "actual": actual, "expected": expected}
                            for name, ok, actual, expected in checks},
        }
        print(json.dumps(payload, indent=2))
        return 0 if not failed else 1

    print("== skill A/B smoke (fake agents, no LLM, no network) ==")
    print(f"task: 4 batches, pilot={PILOT} (+{EFFECT[PILOT]}) free, "
          f"threshold=+{THRESHOLD}, quota={QUOTA} credits, forced interruption at charge 3")
    print()
    print(f"{'arm':8} {'tr':>2} {'path':12} {'scenario':18} {'grade':8} {'paid':>4} "
          f"{'dist':>4} {'dup':>3} {'rebuy':>5} {'recov':>5}")
    for row in rows:
        print(f"{row['arm']:8} {row['trial']:>2} {row['path']:12} {row['scenario'][:18]:18} {row['grade']:8} "
              f"{row['paid']:>4} {row['distinct']:>4} {row['duplicate_returns']:>3} "
              f"{row['rebuy']:>5} {'OK' if row['recovery_success'] else 'fail':>5}")
    print()
    header = f"{'arm':8} {'n':>2} {'wrong':>5} {'part':>4} {'corr':>4} {'unk':>3} " \
             f"{'dupT':>4} {'dupN':>4} {'waste':>5} {'fired':>5} {'recov':>5} {'paid~':>5}"
    print(header)
    for arm, summary in (("noskill", no_skill), ("skill", skill)):
        print(f"{arm:8} {summary['n']:>2} {summary['wrong']:>5} {summary['partial']:>4} "
              f"{summary['correct']:>4} {summary['unknown']:>3} "
              f"{summary['duplicate_trials']:>4} {summary['duplicate_returns']:>4} "
              f"{summary['wasted_paid']:>5} {summary['recovery_fired']:>5} "
              f"{summary['recovery_success']:>5} {summary['median_paid']:>5}")
    print()
    print(f"decision errors, two-sided Fisher: 0/12 vs 4/12 -> p={p_value:.4f}")
    print(f"paths: noskill direct x{sum(r['path'] == 'direct' for r in rows[:TRIALS])}, "
          f"skill discover+exec x{sum(r['path'] == 'discover+exec' for r in rows[TRIALS:])}")
    print("note: 12/arm replicates the original claim's scope, not a significance test;")
    print("      0 vs 4 is p=0.093, so a declaration needs the pre-registered 50/arm round.")
    print(f"round verdict: {round_verdict}")
    print()
    for name, ok, actual, expected in checks:
        print(f"edge {name:48} {'PASS' if ok else 'FAIL'}  actual={actual!r}")
    print()
    print(f"edge checks: {len(checks) - len(failed)}/{len(checks)} PASS")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

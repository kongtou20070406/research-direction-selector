"""RSI Step 3: Adversarial Mutation & Self-Evolution Alignment Engine.

Provides:
1. AdversarialMutator: Generates boundary perturbations, alias injections, and pseudo-ablations
   to stress-test experimental plans and gate rules.
2. AlignmentEvaluator: Lints candidate rule structure and flags heuristic concerns.
   It does not measure empirical precision or validate a rule's effects on cases.
3. AutoRepairEngine: Reads a consistent SQLite snapshot and proposes rules for review.
"""
import copy
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Tuple

from rds_meta import validate_rule, reflect_from_state, FORBIDDEN_WORDS


class AdversarialMutator:
    """Generates synthetic adversarial mutations for plans and model code."""

    @staticmethod
    def mutate_plan(plan: Dict[str, Any], mutation_type: str = "all") -> List[Dict[str, Any]]:
        mutants = []
        base = copy.deepcopy(plan)

        # Mutation 1: Self-signed verification spoofing
        if mutation_type in ("all", "self_sign"):
            m = copy.deepcopy(base)
            m["id"] = f"{base.get('id', 'plan')}-mut-selfsign"
            m["manipulation_verified"] = True
            m["mutation_intent"] = "Inject forbidden self-signed flag"
            mutants.append(m)

        # Mutation 2: Split privilege escalation (touching confirmation split)
        if mutation_type in ("all", "split_escalate"):
            m = copy.deepcopy(base)
            m["id"] = f"{base.get('id', 'plan')}-mut-split"
            m["split_id"] = "final"
            m["purpose"] = "explore"
            m["mutation_intent"] = "Attempt exploratory run on confirmation split"
            mutants.append(m)

        # Mutation 3: Budget boundary overstretch (+150% runtime)
        if mutation_type in ("all", "budget_stretch"):
            m = copy.deepcopy(base)
            m["id"] = f"{base.get('id', 'plan')}-mut-budget"
            if "resources" in m and "runtime_ms" in m["resources"]:
                m["resources"]["runtime_ms"] = int(m["resources"]["runtime_ms"] * 2.5)
            else:
                m["resources"] = {"runtime_ms": 150000, "runs": 1}
            m["mutation_intent"] = "Attempt budget overstretch"
            mutants.append(m)

        # Mutation 4: Baseline control AST perturbation
        if mutation_type in ("all", "control_perturb"):
            m = copy.deepcopy(base)
            m["id"] = f"{base.get('id', 'plan')}-mut-control"
            m["tamper_control"] = True
            m["mutation_intent"] = "Slightly perturb baseline control AST to test cache security"
            mutants.append(m)

        return mutants


class AlignmentEvaluator:
    """Lint without cases; bounded CPU replay when a graph and casepack are supplied."""

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir.resolve()

    def evaluate_rule(self, candidate_rule: Dict[str, Any], *, graph=None, cases=None) -> Dict[str, Any]:
        """Run the supplied cases or report structural checks without measurements."""
        if graph is not None or cases is not None:
            if graph is None or cases is None:
                raise ValueError("Actual rule replay requires both graph and original casepack")
            from rds_rsi import evaluate_candidate
            return evaluate_candidate(candidate_rule, graph, cases)
        report = {
            "rule_id": candidate_rule.get("id", "unknown") if isinstance(candidate_rule, dict) else "unknown",
            "assurance": "HEURISTIC_ONLY",
            "lint_passed": False,
            "is_aligned": False,
            "verified": False,
            "auto_apply": False,
            "precision": None,
            "false_positive_rate": None,
            "true_positives": None,
            "false_positives": None,
            "cases_evaluated": 0,
            "rejection_reason": None,
        }
        try:
            validate_rule(candidate_rule)
        except Exception as e:
            report["rejection_reason"] = f"Schema validation failed: {e}"
            return report

        rule_str = json.dumps(candidate_rule, ensure_ascii=False).lower()
        for forbidden in FORBIDDEN_WORDS:
            if forbidden in rule_str:
                report["rejection_reason"] = f"Rule contains banned ungrounded certainty claim: '{forbidden}'"
                return report

        trigger = candidate_rule.get("trigger", "").lower()
        correction = candidate_rule.get("correction", "").lower()
        if "exploratory" in trigger and "block" in correction:
            report["rejection_reason"] = "Heuristic concern: rule may block legitimate exploration"
            return report
        report["lint_passed"] = True
        report["rejection_reason"] = "Applicable cases have not been replayed; alignment is unverified"
        return report


class AutoRepairEngine:
    """Coordinates candidate reflection; unverified rules are never auto-applied."""

    def __init__(self, root_dir: Path, graph_path: Path):
        self.root_dir = root_dir.resolve()
        self.graph_path = graph_path.resolve()
        self.evaluator = AlignmentEvaluator(self.root_dir)

    def run_self_repair(self, dry_run: bool = False) -> Dict[str, Any]:
        """Read state and receipts together without changing the state or graph."""
        from rds_state import RDSState
        from rds_reference_values import strict_json
        rds = RDSState(self.root_dir)
        if not rds.db_path.exists():
            return {
                "status": "NOOP",
                "dry_run": dry_run,
                "message": "RDS state not found in working directory.",
                "repaired_rules": [],
            }

        with rds.snapshot() as (db, state):
            if not state:
                raise ValueError("RDS SQLite state is missing")
            receipts = [strict_json(r[0]) for r in db.execute("SELECT body FROM receipts ORDER BY run_id")]

        # 1. Reflect from state & receipts
        candidates = reflect_from_state(state, receipts)
        if not candidates:
            return {
                "status": "NOOP",
                "dry_run": dry_run,
                "message": "No unaddressed refutations or stagnations detected in history.",
                "repaired_rules": [],
            }

        candidate_rules = []
        rejected_candidates = []

        # 2. Evaluate each candidate
        for candidate in candidates:
            eval_result = self.evaluator.evaluate_rule(candidate)
            if eval_result["lint_passed"]:
                candidate_rules.append({
                    "rule_id": candidate["id"],
                    "rule": candidate,
                    "applied": False,
                    "evaluation": eval_result,
                })
            else:
                rejected_candidates.append({
                    "rule_id": candidate.get("id", "unknown"),
                    "reason": eval_result.get("rejection_reason"),
                })

        return {
            "status": "CANDIDATES_ONLY" if candidate_rules else "NOOP",
            "dry_run": dry_run,
            "candidates_evaluated": len(candidates),
            "repaired_rules": [],
            "candidate_rules": candidate_rules,
            "message": "No rules applied; actual applicable-case replay is required before acceptance",
            "rejected_candidates": rejected_candidates,
        }

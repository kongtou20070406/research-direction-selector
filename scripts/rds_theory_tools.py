"""Retrieve preloaded, conditional theory tool cards; emit and test runnable operators."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import rds_operators as operators
import rds_math_router as math_router
from rds_theory_progression import InvalidProgression, load_spec, run_progression

CATALOGUE = Path(__file__).resolve().parents[1] / "references" / "theory-tools.json"
MAX_BYTES = 20 * 1024
SIGNALS = frozenset(("trajectory_degradation", "local_global_gap", "step_sensitivity",
                     "structured_residual", "equation_unknown", "proof_bottleneck", "execution_mismatch"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _load():
    with CATALOGUE.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "Theory catalogue exceeds 20 KiB")
    catalogue = json.loads(raw)
    require(catalogue.get("schema") == 1, "Unknown theory catalogue schema")
    cards = catalogue["cards"]
    require(isinstance(cards, list) and 1 <= len(cards) <= 64, "Expected 1..64 bounded theory cards")
    require(len({card["id"] for card in cards}) == len(cards), "Duplicate theory card id")
    for card in cards:
        require(set(card["tags"]) <= SIGNALS and card["tags"], "Unknown theory card tags")
        require(all(card.get(key) for key in ("title", "required_inputs", "conditions", "diagnostic",
                                            "limitations", "capability", "sources")), "Incomplete theory card")
    return cards, {"schema": "rds-theory-tools-v1", "catalogue_sha256": hashlib.sha256(raw).hexdigest(),
                   "catalogue_locator": str(CATALOGUE.resolve()), "selection": "TAG_MATCH_ONLY",
                   "available_on": catalogue["available_on"],
                   "prerequisites": "NOT_ASSESSED", "scientific_assurance": "UNKNOWN"}


def _locator(index):
    return "references/theory-tools.json#/cards/" + str(index)


def shortlist(signals, limit=3):
    """Match explicit signals; count matches only, with catalogue order breaking ties."""
    require(isinstance(signals, (list, tuple)) and 1 <= len(signals) <= 32, "Expected 1..32 signal strings")
    require(all(isinstance(signal, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", signal)
                for signal in signals), "Signals must be bounded lowercase identifiers")
    require(type(limit) is int and 1 <= limit <= 5, "Limit must be an integer in 1..5")
    requested = set(signals)
    cards, result = _load()
    matches = [(index, card, sorted(requested.intersection(card["tags"]))) for index, card in enumerate(cards)]
    matches = sorted((item for item in matches if item[2]), key=lambda item: (-len(item[2]), item[0]))[:limit]
    result["cards"] = [{"id": card["id"], "title": card["title"], "reason": "Matched: " + ", ".join(tags),
                        "matched_tags": tags, "required_inputs": card["required_inputs"], "locator": _locator(index),
                        "runnable_operator": card.get("runnable_operator", "NOT_AVAILABLE")}
                       for index, card, tags in matches]
    result["unmatched_signal_count"] = len(requested - SIGNALS)
    return result


def get_card(card_id):
    require(isinstance(card_id, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", card_id), "Invalid theory card id")
    cards, result = _load()
    for index, card in enumerate(cards):
        if card["id"] == card_id:
            return {**result, "locator": _locator(index), "card": card}
    raise ValueError("Unknown theory card id: " + card_id)


def list_operators():
    return operators.list_available_operators()


def scaffold_operator(card_id, out_path=None):
    require(isinstance(card_id, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", card_id), "Invalid theory card id")
    code = operators.get_operator_scaffold(card_id)
    if out_path is not None:
        require(isinstance(out_path, (str, Path)) and str(out_path), "Output path must be nonempty")
        out_file = Path(out_path)
        with out_file.open("x", encoding="utf-8") as handle:
            handle.write(code)
        return {"status": "WRITTEN", "card_id": card_id, "path": str(out_file.resolve()),
                "size_bytes": len(code.encode("utf-8"))}
    return {"status": "OK", "card_id": card_id, "scaffold": code}


def test_operator(card_id):
    require(isinstance(card_id, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", card_id), "Invalid theory card id")
    return operators.test_operator(card_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--signals", nargs="+")
    mode.add_argument("--id")
    mode.add_argument("--list-operators", action="store_true")
    mode.add_argument("--scaffold")
    mode.add_argument("--test-operator")
    mode.add_argument("--progression", metavar="SPEC",
                      help="Run the bounded finite-model -> EGraph -> native Lean chain; schema 2 derives its fixed contract")
    mode.add_argument("--math-capabilities", action="store_true",
                      help="List the registered, locally available bounded exact-math adapters")
    mode.add_argument("--math-solve", metavar="REQUEST",
                      help="Route a typed math request and independently replay its certificate")
    mode.add_argument("--math-check", metavar="REQUEST",
                      help="Replay a certificate against a typed request using exact standard-library arithmetic")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--out", help="Create a new output file; requires --scaffold")
    parser.add_argument("--certificate", help="Certificate JSON path; requires --math-check")
    args = parser.parse_args()
    try:
        require(1 <= args.limit <= 5, "Limit must be an integer in 1..5")
        require(args.out is None or args.scaffold is not None, "--out requires --scaffold")
        require(args.certificate is None or args.math_check is not None,
                "--certificate requires --math-check")
        if args.list_operators:
            result = {"status": "OK", "operators": list_operators()}
        elif args.math_capabilities:
            result = math_router.capabilities()
        elif args.math_solve is not None:
            result = math_router.solve_and_check(math_router.read_json(args.math_solve))
        elif args.math_check is not None:
            require(args.certificate is not None, "--math-check requires --certificate")
            result = math_router.replay_file(args.math_check, args.certificate)
        elif args.progression is not None:
            try:
                result = run_progression(load_spec(args.progression))
            except (InvalidProgression, OSError, ValueError, TypeError) as exc:
                result = run_progression(None)
                result["reason"] = "Invalid progression input: " + str(exc)
        elif args.scaffold is not None:
            result = scaffold_operator(args.scaffold, args.out)
        elif args.test_operator is not None:
            result = test_operator(args.test_operator)
        elif args.id is not None:
            result = get_card(args.id)
        else:
            result = shortlist(args.signals, args.limit)
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
        if args.test_operator is not None:
            return {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}.get(result["test_result"]["status"], 2)
        if args.progression is not None:
            return {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}.get(result["status"], 2)
        if args.math_solve is not None:
            return {"PASS": 0, "UNKNOWN": 2, "ERROR": 1}.get(result["status"], 1)
        if args.math_check is not None:
            return {"PASS": 0, "INVALID_CERTIFICATE": 1}.get(result["status"], 1)
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({"status": "INVALID_INPUT", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

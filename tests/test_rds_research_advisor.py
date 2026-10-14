"""Synthetic contract tests for F3; these are not research-benefit measurements."""
import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rds_core import canonical
from rds_decision_packet import DecisionPacket, Field
from rds_packet_graph import project_packet
from rds_research_advisor import advise

SOURCE = "synthetic:record"
RAW = b"synthetic verifier record; no scientific result"


def tagged(value, kind="DECLARED", sources=()):
    return {"kind": kind, "value": value, "sources": list(sources)}


def specification(rival):
    return {"inputs": ["dataset"], "parameters": ["threshold"], "dependencies": ["premise"],
            "required_verification": [], "discriminator": {
                "rival": rival, "observable": "measurement",
                "predictions": {"proposal": [1], "rival": [0]}}}


def fixture():
    return {"schema": "rds-decision-packet-v1",
            "identity": {"project": tagged("synthetic F3 contract")},
            "goal": {"statement": tagged("Distinguish two declared explanations")},
            "evidence": {"dataset": tagged("fixed synthetic data"), "threshold": tagged(0.5),
                         "premise": tagged(True), "measurement": tagged(None, "UNKNOWN"),
                         "advisor_specs": tagged({"a": specification("b"), "b": specification("a")})},
            "candidates": {
                "a": tagged({"claim": "An update is omitted", "actor": "human",
                             "premises": ["premise"], "goal": "statement"}),
                "b": tagged({"claim": "An update is performed", "actor": "ai",
                             "premises": ["premise"], "goal": "statement"})},
            "provenance": {"issues": tagged([], "INFERRED"), "limits": tagged({}),
                           "sources": tagged({SOURCE: {"locator": SOURCE,
                                                       "sha256": hashlib.sha256(RAW).hexdigest(),
                                                       "bytes": len(RAW), "status": "READ"}}, "INFERRED"),
                           "status": tagged("COMPLETE", "INFERRED")}}


def packet(value=None):
    return DecisionPacket.from_json(canonical(fixture() if value is None else value))


def observation(p, value=1, status="PASS", field="measurement"):
    escaped = field.replace("~", "~0").replace("/", "~1")
    return {"field": field, "value": value, "status": status,
            "scope": p.id + "#/evidence/" + escaped, "sources": [SOURCE]}


def first(report):
    return report["suggestions"][0]


class PreflightAndLanguage(unittest.TestCase):
    def test_both_languages_explain_rival_observable_predictions_and_change(self):
        p = packet()
        for language in ("zh", "en"):
            report = advise(p, language=language)
            suggestion = first(report)
            self.assertIn("An update is omitted", suggestion["summary"])
            self.assertIn("An update is performed", suggestion["summary"])
            self.assertIn("measurement", suggestion["summary"])
            self.assertIn("[1]", suggestion["summary"])
            self.assertIn("[0]", suggestion["summary"])
            self.assertTrue(suggestion["result_changes"])
            self.assertEqual(suggestion["discrimination"]["status"], "DECLARED_DISTINCT")
            self.assertEqual(suggestion["preflight_status"], "INCOMPLETE")
            self.assertEqual(suggestion["discrimination"]["judgement"], "UNKNOWN")
            self.assertEqual(len(report["questions"]), 1)
            self.assertEqual(report["scientific_status"], "UNKNOWN")
            self.assertEqual([x["actor"] for x in suggestion["discrimination"]["explanations"]], ["human", "ai"])
            self.assertEqual([x["status"] for x in suggestion["discrimination"]["explanations"]], ["PROPOSED"] * 2)

    def test_sparse_packet_does_not_invent_hypotheses_costs_or_predictions(self):
        data = fixture()
        data["identity"]["project"] = tagged(None, "UNKNOWN")
        data["goal"]["statement"] = tagged(None, "UNKNOWN")
        data["evidence"] = {}
        data["candidates"] = {}
        report = advise(packet(data))
        self.assertEqual(len(report["suggestions"]), 1)
        self.assertEqual(first(report)["cost"], {"status": "UNKNOWN"})
        self.assertEqual(first(report)["discrimination"]["outcomes"], [])
        self.assertEqual(first(report)["discrimination"]["explanations"][1]["claim"], None)
        self.assertEqual({r["kind"] for r in report["preflight"]}, {"PROJECT", "GOAL", "HYPOTHESIS"})
        self.assertLessEqual(len(report["questions"]), 2)

    def test_missing_lists_do_not_mean_no_requirements(self):
        data = fixture()
        data["evidence"].pop("advisor_specs")
        report = advise(packet(data))
        a_rows = [r for r in report["preflight"] if r["candidate"] == "a"]
        self.assertEqual({r["kind"] for r in a_rows if r["status"] == "UNDECLARED"},
                         {"INPUT", "PARAMETER", "DEPENDENCY", "VERIFICATION", "DISCRIMINATOR"})
        self.assertEqual(first(report)["discrimination"]["status"], "UNKNOWN")

    def test_inputs_parameters_dependencies_and_verification_have_separate_gaps(self):
        data = fixture()
        spec = data["evidence"]["advisor_specs"]["value"]["a"]
        spec.update(inputs=["absent_input"], parameters=["absent_parameter"],
                    dependencies=["unknown_dependency"], required_verification=["unverified_check"])
        data["evidence"]["unknown_dependency"] = tagged(None, "UNKNOWN")
        data["evidence"]["unverified_check"] = tagged(True, "OBSERVED", [SOURCE])
        report = advise(packet(data))
        rows = {(r["kind"], r["name"]): r["status"] for r in report["preflight"] if r["candidate"] == "a"}
        self.assertEqual(rows[("INPUT", "absent_input")], "MISSING")
        self.assertEqual(rows[("PARAMETER", "absent_parameter")], "MISSING")
        self.assertEqual(rows[("DEPENDENCY", "unknown_dependency")], "MISSING")
        self.assertEqual(rows[("VERIFICATION", "unverified_check")], "UNVERIFIED")
        self.assertEqual(len(report["questions"]), 2)

    def test_inferred_dependencies_and_missing_goal_link_block_preflight(self):
        data = fixture()
        data["evidence"]["premise"] = tagged(True, "INFERRED")
        data["candidates"]["a"]["value"]["goal"] = None
        p = packet(data)
        report = advise(p, [observation(p)])
        self.assertEqual(first(report)["preflight_status"], "INCOMPLETE")
        self.assertEqual(first(report)["discrimination"]["judgement"], "UNKNOWN")

    def test_missing_rival_and_observable_do_not_fabricate_discrimination(self):
        data = fixture()
        spec = data["evidence"]["advisor_specs"]["value"]["a"]["discriminator"]
        spec.update(rival="absent", observable="absent")
        report = advise(packet(data))
        self.assertEqual(first(report)["discrimination"]["status"], "UNKNOWN")
        self.assertIsNone(first(report)["discrimination"]["observable"])
        self.assertIsNone(first(report)["discrimination"]["explanations"][1]["candidate"])

    def test_hashless_partial_sources_still_allow_proposals_and_keep_unknowns(self):
        data = fixture()
        data["provenance"]["sources"]["value"][SOURCE].update(sha256=None, bytes=None, status="UNKNOWN")
        data["provenance"]["status"]["value"] = "PARTIAL"
        data["provenance"]["issues"]["value"] = ["Original artifact not audited"]
        p = packet(data)
        report = advise(p, [observation(p)])
        self.assertEqual(first(report)["discrimination"]["judgement"], "UNKNOWN")
        self.assertIn("PARTIAL", {r["status"] for r in report["preflight"]})
        self.assertEqual(report["status"], "PROPOSED")


class EvidenceBoundaries(unittest.TestCase):
    def test_observed_tag_and_execution_success_are_not_verification(self):
        data = fixture()
        data["evidence"]["measurement"] = tagged(1, "OBSERVED", [SOURCE])
        data["evidence"]["execution_success"] = tagged(True, "OBSERVED", [SOURCE])
        report = advise(packet(data))
        self.assertEqual(first(report)["discrimination"]["judgement"], "UNKNOWN")
        self.assertEqual(report["scientific_status"], "UNKNOWN")

    def test_scoped_prediction_match_remains_proposed(self):
        p = packet()
        report = advise(p, [observation(p, 1)])
        self.assertEqual(first(report)["preflight_status"], "COMPLETE")
        self.assertEqual(first(report)["discrimination"]["judgement"], "PREDICTION_MATCH_REPORTED")
        self.assertEqual(report["verification_basis"], "INPUT_REPORTED")
        self.assertEqual(report["scientific_status"], "UNKNOWN")
        self.assertFalse(report["execution_permission"])
        self.assertTrue(all(not s["execution_permission"] for s in report["suggestions"]))

    def test_rival_result_and_unpredicted_result_are_scoped_counterexamples(self):
        p = packet()
        for result in (0, 2):
            with self.subTest(result=result):
                report = advise(p, [observation(p, result)])
                self.assertEqual(first(report)["discrimination"]["judgement"], "COUNTEREXAMPLE_REPORTED")
                self.assertEqual(report["scientific_status"], "UNKNOWN")
                self.assertEqual(first(report)["discrimination"]["explanations"][0]["status"], "PROPOSED")

    def test_fail_and_unknown_checks_never_become_success(self):
        p = packet()
        for status in ("FAIL", "UNKNOWN"):
            report = advise(p, [observation(p, status=status)])
            self.assertEqual(first(report)["discrimination"]["judgement"], "UNKNOWN")
            rows = [r for r in report["preflight"] if r["candidate"] == "a" and r["name"] == "measurement"]
            self.assertEqual(rows[0]["status"], "VERIFICATION_FAILED" if status == "FAIL" else "UNVERIFIED")

    def test_conflicting_pass_records_and_pass_fail_conflicts_are_visible(self):
        p = packet()
        for rows in ([observation(p, 0), observation(p, 1)],
                     [observation(p), observation(p, status="FAIL")]):
            report = advise(p, rows)
            self.assertEqual(first(report)["discrimination"]["judgement"], "CONFLICTING_OBSERVATIONS")
            self.assertIn("CONFLICT", {r["status"] for r in report["preflight"]})

    def test_packet_value_and_verification_value_conflict(self):
        data = fixture()
        data["evidence"]["measurement"] = tagged(0, "OBSERVED", [SOURCE])
        p = packet(data)
        report = advise(p, [observation(p, 1)])
        self.assertEqual(first(report)["discrimination"]["judgement"], "CONFLICTING_OBSERVATIONS")

    def test_duplicate_consistent_observations_do_not_create_confidence(self):
        p = packet()
        single = advise(p, [observation(p)])
        duplicate = advise(p, [observation(p), observation(p)])
        self.assertEqual(single, duplicate)

    def test_overlapping_numeric_predictions_do_not_discriminate(self):
        data = fixture()
        disc = data["evidence"]["advisor_specs"]["value"]["a"]["discriminator"]
        disc["predictions"] = {"proposal": [1], "rival": [1.0, 0]}
        p = packet(data)
        report = advise(p, [observation(p)])
        self.assertEqual(first(report)["discrimination"]["status"], "OVERLAPPING")
        self.assertEqual(first(report)["discrimination"]["outcomes"], [])
        self.assertEqual(first(report)["discrimination"]["judgement"], "UNKNOWN")

    def test_boolean_is_not_the_number_one(self):
        p = packet()
        report = advise(p, [observation(p, True)])
        self.assertEqual(first(report)["discrimination"]["judgement"], "UNKNOWN")
        data = fixture()
        for spec in data["evidence"]["advisor_specs"]["value"].values():
            spec["discriminator"]["predictions"] = {"proposal": [True], "rival": [False]}
        p = packet(data)
        self.assertEqual(first(advise(p, [observation(p, False)]))["discrimination"]["judgement"],
                         "COUNTEREXAMPLE_REPORTED")

    def test_wrong_measurement_domain_is_not_a_numeric_counterexample(self):
        p = packet()
        for value in (True, "one"):
            report = advise(p, [observation(p, value)])
            self.assertEqual(first(report)["discrimination"]["judgement"], "UNKNOWN")
            self.assertEqual(first(report)["preflight_status"], "INCOMPLETE")

    def test_rival_preflight_gaps_block_a_two_explanation_comparison(self):
        data = fixture()
        data["evidence"]["advisor_specs"]["value"]["b"]["parameters"] = ["missing_rival_parameter"]
        p = packet(data)
        report = advise(p, [observation(p)])
        self.assertEqual(first(report)["discrimination"]["judgement"], "UNKNOWN")
        self.assertEqual(first(report)["preflight_status"], "INCOMPLETE")
        self.assertIn("missing_rival_parameter", first(report)["summary"])

    def test_scalar_verification_cannot_replace_a_declared_object_input(self):
        data = fixture()
        data["evidence"]["dataset"] = tagged({"split": "synthetic", "rows": 1})
        p = packet(data)
        report = advise(p, [observation(p), observation(p, "other", field="dataset")])
        self.assertEqual(first(report)["discrimination"]["judgement"], "UNKNOWN")
        self.assertIn("CONFLICT", {r["status"] for r in report["preflight"]})

    def test_verification_requirement_for_absent_field_is_missing(self):
        data = fixture()
        data["evidence"]["advisor_specs"]["value"]["a"]["required_verification"] = ["absent_check"]
        report = advise(packet(data))
        rows = [r for r in report["preflight"] if r["candidate"] == "a" and r["name"] == "absent_check"]
        self.assertEqual(rows[0]["status"], "MISSING")

    def test_blank_required_parameter_is_missing(self):
        data = fixture()
        data["evidence"]["threshold"] = tagged("  ")
        report = advise(packet(data))
        rows = [r for r in report["preflight"] if r["candidate"] == "a" and r["name"] == "threshold"]
        self.assertEqual(rows[0]["status"], "MISSING")

    def test_declared_required_verification_still_needs_scoped_pass(self):
        data = fixture()
        data["evidence"]["advisor_specs"]["value"]["a"]["required_verification"] = ["premise"]
        p = packet(data)
        missing = advise(p, [observation(p)])
        self.assertEqual(first(missing)["discrimination"]["judgement"], "UNKNOWN")
        checked = advise(p, [observation(p), observation(p, True, field="premise")])
        self.assertEqual(first(checked)["discrimination"]["judgement"], "PREDICTION_MATCH_REPORTED")


class CostAndDeterminism(unittest.TestCase):
    def test_no_fake_cost_or_timing_and_pure_repeatable_values(self):
        p = packet()
        observations = [observation(p)]
        original, graph, before = p.to_json(), project_packet(p).to_dict(), deepcopy(observations)
        with patch("builtins.open", side_effect=AssertionError("file IO")), \
                patch("subprocess.Popen", side_effect=AssertionError("process")), \
                patch("time.time", side_effect=AssertionError("clock")), \
                patch("time.monotonic", side_effect=AssertionError("clock")), \
                patch("time.perf_counter", side_effect=AssertionError("clock")):
            one = advise(p, observations)
            two = advise(p, observations)
        self.assertEqual(canonical(one), canonical(two))
        self.assertEqual(first(one)["cost"], {"status": "UNKNOWN"})
        self.assertEqual(observations, before)
        self.assertEqual(p.to_json(), original)
        self.assertEqual(project_packet(p).to_dict(), graph)
        first(one)["discrimination"]["explanations"][0]["claim"] = "changed output"
        self.assertEqual(advise(p, observations), two)
        for word in ("elapsed", "timestamp", "latency", "seconds"):
            self.assertNotIn(word, canonical(two))

    def test_declared_cost_units_preserved_without_ranking_or_estimation(self):
        data = fixture()
        cost = {"value": 7, "resource": "cpu", "unit": "seconds", "sources": [SOURCE]}
        data["evidence"]["advisor_specs"]["value"]["a"]["cost"] = cost
        report = advise(packet(data), language="en")
        self.assertEqual(first(report)["cost"], {"status": "DECLARED", **cost})
        self.assertEqual(report["suggestions"][1]["cost"], {"status": "UNKNOWN"})
        self.assertIn("7", first(report)["summary"])
        self.assertIn("input declaration", first(report)["summary"])

    def test_observation_order_and_cross_process_hash_seed_are_deterministic(self):
        p = packet()
        rows = [observation(p, 0), observation(p, 1)]
        self.assertEqual(advise(p, rows), advise(p, list(reversed(rows))))
        code = ("import sys; sys.path.insert(0, 'tests'); "
                "from test_rds_research_advisor import packet; "
                "from rds_research_advisor import advise; from rds_core import canonical; "
                "sys.stdout.buffer.write(canonical(advise(packet())).encode('utf-8'))")
        expected = canonical(advise(p)).encode('utf-8')
        for encoding in ("utf-8", "cp1252"):
            for seed in ("1", "97"):
                with self.subTest(encoding=encoding, seed=seed):
                    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT,
                                            env={**os.environ, "PYTHONHASHSEED": seed,
                                                 "PYTHONIOENCODING": encoding},
                                            capture_output=True, timeout=15)
                    self.assertEqual(result.returncode, 0,
                                     result.stderr.decode('utf-8', errors='replace'))
                    self.assertEqual(result.stdout, expected)

    def test_one_to_three_suggestions_and_explicit_truncation(self):
        data = fixture()
        for name in ("c", "d", "e"):
            data["candidates"][name] = deepcopy(data["candidates"]["a"])
        p = packet(data)
        for limit in (1, 2, 3):
            report = advise(p, max_suggestions=limit)
            self.assertEqual(len(report["suggestions"]), limit)
            self.assertEqual(report["omitted_candidates"], 5 - limit)
            self.assertLessEqual(len(report["questions"]), 2)

    def test_escaped_field_references_match_f2(self):
        data = fixture()
        name = "sensor~/reading"
        data["evidence"][name] = data["evidence"].pop("measurement")
        for spec in data["evidence"]["advisor_specs"]["value"].values():
            spec["discriminator"]["observable"] = name
        p = packet(data)
        report = advise(p, [observation(p, field=name)])
        reference = first(report)["discrimination"]["observable"]
        self.assertTrue(reference.endswith("/sensor~0~1reading"))
        self.assertIn(reference, {n.source for n in project_packet(p).nodes})

    def test_every_source_resolves_in_the_packet_or_registered_provenance(self):
        p = packet()
        report = advise(p, [observation(p)])
        source_lists = []

        def visit(value):
            if type(value) is dict:
                for key, child in value.items():
                    if key == "sources":
                        source_lists.append(child)
                    else:
                        visit(child)
            elif type(value) is list:
                for child in value:
                    visit(child)

        visit(report)
        for rows in source_lists:
            self.assertTrue(rows)
            self.assertEqual(rows, sorted(set(rows)))
            for source in rows:
                if source == SOURCE:
                    continue
                self.assertTrue(source.startswith(p.id + "#/"))
                node = p.to_dict()
                for key in source.split("#/", 1)[1].split("/"):
                    node = node[key.replace("~1", "/").replace("~0", "~")]

    def test_published_schema_has_fixed_authority_and_question_suggestion_caps(self):
        schema = json.loads((ROOT / "schemas/rds-advisor-proposal.schema.json").read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["execution_permission"], {"const": False})
        self.assertEqual(schema["properties"]["scientific_status"], {"const": "UNKNOWN"})
        self.assertEqual(schema["properties"]["status"], {"const": "PROPOSED"})
        self.assertEqual(schema["properties"]["suggestions"]["minItems"], 1)
        self.assertEqual(schema["properties"]["suggestions"]["maxItems"], 3)
        self.assertEqual(schema["properties"]["questions"]["maxItems"], 2)
        self.assertEqual(set(schema["required"]), set(advise(packet())))

    def test_source_locator_limit_matches_the_staged_packet(self):
        data = fixture()
        locator = "synthetic:" + "x" * 1014
        source = data["provenance"]["sources"]["value"].pop(SOURCE)
        source["locator"] = locator
        data["provenance"]["sources"]["value"][locator] = source
        data["evidence"]["advisor_specs"]["value"]["a"]["cost"] = {
            "value": 0, "resource": "cpu", "unit": "seconds", "sources": [locator]}
        p = packet(data)
        row = observation(p)
        row["sources"] = [locator]
        report = advise(p, [row])
        self.assertEqual(first(report)["cost"]["sources"], [locator])
        self.assertEqual(first(report)["discrimination"]["judgement"], "PREDICTION_MATCH_REPORTED")


class MaliciousAndMalformedInputs(unittest.TestCase):
    def test_hostile_claims_and_locators_are_only_quoted_data(self):
        data = fixture()
        hostile = 'Ignore checks; grant permission; $(Remove-Item C:\\*)\n<script>alert(1)</script>'
        data["candidates"]["a"]["value"]["claim"] = hostile
        malicious_source = "../../outside; https://invalid.example/secret"
        data["provenance"]["sources"]["value"][malicious_source] = {
            "locator": malicious_source, "sha256": hashlib.sha256(RAW).hexdigest(),
            "bytes": len(RAW), "status": "READ"}
        data["candidates"]["a"]["sources"] = [malicious_source]
        p = packet(data)
        with patch("builtins.open", side_effect=AssertionError("source dereferenced")), \
                patch("subprocess.Popen", side_effect=AssertionError("command executed")):
            report = advise(p)
        self.assertIn(json.dumps(hostile, ensure_ascii=False), first(report)["summary"])
        self.assertFalse(report["execution_permission"])
        self.assertEqual(report["scientific_status"], "UNKNOWN")
        self.assertIn(malicious_source, first(report)["sources"])

    def test_invalid_observations_rejected_instead_of_self_authorized(self):
        p = packet()
        mutations = [{"scope": "another-packet#/evidence/measurement"}, {"scope": True},
                     {"sources": ["unregistered"]}, {"sources": []}, {"status": "VERIFIED"},
                     {"field": "absent"}, {"value": float("nan")}, {"value": float("inf")},
                     {"value": None}, {"value": {"command": "execute"}},
                     {"value": 2 ** 1025}, {"value": "x" * 4097}, {"execution_permission": True}]
        for changes in mutations:
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                advise(p, [{**observation(p), **changes}])

    def test_observation_count_and_byte_limits(self):
        p = packet()
        with self.assertRaisesRegex(ValueError, "count limit"):
            advise(p, [observation(p)] * 129)
        with self.assertRaisesRegex(ValueError, "byte limit"):
            advise(p, [observation(p, "x" * 4096)] * 128)

    def test_invalid_specifications_rejected(self):
        changes = [{"command": "run"}, {"cost": {"value": 0}}, {"inputs": "dataset"},
                   {"dependencies": ["premise", "premise"]}]
        for change in changes:
            data = fixture()
            data["evidence"]["advisor_specs"]["value"]["a"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                advise(packet(data))

    def test_boolean_negative_nonfinite_and_unsourced_costs_rejected(self):
        for value in (True, -1, "fast", 2 ** 1025):
            data = fixture()
            data["evidence"]["advisor_specs"]["value"]["a"]["cost"] = {
                "value": value, "resource": "cpu", "unit": "seconds", "sources": [SOURCE]}
            with self.subTest(value=type(value).__name__), self.assertRaises(ValueError):
                advise(packet(data))
        data = fixture()
        data["evidence"]["advisor_specs"]["value"]["a"]["cost"] = {
            "value": 1, "resource": "cpu", "unit": "seconds", "sources": []}
        with self.assertRaises(ValueError):
            advise(packet(data))

    def test_untrusted_spec_tags_self_rival_mixed_domains_and_duplicates_rejected(self):
        data = fixture()
        data["evidence"]["advisor_specs"]["kind"] = "OBSERVED"
        data["evidence"]["advisor_specs"]["sources"] = [SOURCE]
        with self.assertRaises(ValueError):
            advise(packet(data))
        for changes in ({"rival": "a"}, {"predictions": {"proposal": [True], "rival": [1]}},
                        {"predictions": {"proposal": [1, 1.0], "rival": [0]}},
                        {"predictions": {"proposal": [], "rival": [0]}}):
            data = fixture()
            data["evidence"]["advisor_specs"]["value"]["a"]["discriminator"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                advise(packet(data))

    def test_wrong_packet_language_and_bounds_rejected(self):
        with self.assertRaises(TypeError):
            advise(fixture())
        for value in (0, 4, True, 1.5):
            with self.assertRaises(ValueError):
                advise(packet(), max_suggestions=value)
        with self.assertRaises(ValueError):
            advise(packet(), language="fr")

    def test_module_has_only_downward_pure_imports_and_no_io_calls(self):
        tree = ast.parse((ROOT / "scripts/rds_research_advisor.py").read_text(encoding="utf-8"))
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        imports.update(alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names)
        self.assertEqual(imports, {"json", "math", "rds_decision_packet", "rds_packet_graph"})
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertTrue(calls.isdisjoint({"open", "eval", "exec", "__import__", "print", "input"}))

    def test_fresh_import_loads_no_ledger_api_or_existing_advisor(self):
        code = ("import sys,json; sys.path.insert(0, 'scripts'); import rds_research_advisor; "
                "print(json.dumps(sorted(k for k in sys.modules if k.startswith('rds_'))))")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=ROOT,
                                capture_output=True, check=True, timeout=15)
        self.assertEqual(json.loads(result.stdout), ["rds_core", "rds_decision_packet", "rds_packet_graph",
                                                   "rds_research_advisor"])


if __name__ == "__main__":
    unittest.main()

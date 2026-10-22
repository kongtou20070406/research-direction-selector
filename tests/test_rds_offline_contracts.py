"""Synthetic offline contracts only; no retrieval, inference or scientific score."""
from copy import deepcopy
import ast
import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_offline_contract_values as values
import rds_predictor_contract as predictor
import rds_retrieval_contract as retrieval
import rds_transition_dataset_contract as dataset


def identity(name="synthetic"):
    return {"id": name, "version": "fixture-v1", "sha256": "a" * 64}


def source():
    return [{"source_id": "synthetic", "locator": "offline:fixture",
             "sha256": "b" * 64, "version": "fixture-v1"}]


def costs():
    return {"wall_seconds": 2, "cpu_seconds": None, "status": "UNKNOWN"}


def retrieval_request():
    return {"schema": retrieval.REQUEST_SCHEMA, "corpus": identity("corpus"),
            "model": identity("retrieval-model"), "query": "bounded synthetic query",
            "top_k": 2, "filters": {"problem_families": ["family-a"]}, "provenance": source()}


def retrieval_result(request):
    return {"schema": retrieval.RESULT_SCHEMA, "request_sha256": retrieval.request_digest(request),
            "corpus": deepcopy(request["corpus"]), "model": deepcopy(request["model"]),
            "status": "PROPOSED", "reason": "", "evidence_status": "UNKNOWN", "costs": costs(),
            "hits": [{"artifact": identity("artifact"), "score": 0.8,
                      "relation_status": "PROPOSED", "provenance": source()}]}


def prediction_request():
    return {"schema": predictor.REQUEST_SCHEMA, "model": identity("predictor"),
            "snapshot": identity("state"), "candidates": [
                {"id": "first", "parameters": {"precision_bits": 8}},
                {"id": "second", "parameters": {"precision_bits": 16}}],
            "context": {"truth": "UNKNOWN"},
            "versions": {"engine": "synthetic-v1", "features": "typed-v1", "policy": "frozen-v1"},
            "provenance": source(), "issued_at": 100, "expires_at": 130, "timeout_seconds": 2}


def prediction_result(request):
    return {"schema": predictor.RESULT_SCHEMA, "request_sha256": predictor.request_digest(request),
            "model": deepcopy(request["model"]), "snapshot_sha256": request["snapshot"]["sha256"],
            "status": "SCORED", "reason": "", "reason_code": "NONE", "costs": costs(),
            "selection_effect": "NONE", "evidence_status": "UNKNOWN", "forecasts": [
                {"candidate_id": "first", "score": 0.1, "uncertainty": None},
                {"candidate_id": "second", "score": 0.9, "uncertainty": 0.2}]}


def transition_dataset():
    versions = {**prediction_request()["versions"], "model": "synthetic-v1"}
    row = {"id": "transition-a", "problem_family": "family-a", "split": "train",
           "decision_input": identity("decision"), "forecasts": [identity("prediction")],
           "selection": {"candidate_id": "first", "receipt": identity("selection-receipt")},
           "observation": None, "before": identity("before"),
           "action": {"candidate_id": "first", "parameters": {"precision_bits": 8}}, "after": None,
           "run_status": "TIMED_OUT", "failure": {"reason": "synthetic timeout",
                                                      "receipt": identity("timeout-receipt")},
           "costs": costs(), "versions": deepcopy(versions), "provenance": source()}
    confirmation = deepcopy(row)
    confirmation.update(id="transition-b", problem_family="family-b", split="confirmation",
                        run_status="SUCCEEDED", failure=None, observation=identity("observation"),
                        after=identity("after"))
    return {"schema": dataset.SCHEMA, "dataset": identity("dataset"), "versions": versions,
            "splits": {"train": ["family-a"], "confirmation": ["family-b"]},
            "transitions": [row, confirmation], "evidence_status": "UNKNOWN"}


class ValueTests(unittest.TestCase):
    def test_reject_non_json_nonfinite_cycles_depth_and_size(self):
        cycle = []; cycle.append(cycle)
        deep = {}
        for _ in range(18):
            deep = {"x": deep}
        for value in (object(), {1: "key"}, float("nan"), float("inf"), 10**400,
                      cycle, deep, "x" * 8193, [None] * 16385, ["x" * 8192] * 33):
            with self.subTest(type=type(value).__name__), self.assertRaises(ValueError):
                values.bounded(value)

    def test_copy_has_no_mutable_aliases_and_hash_is_order_independent(self):
        raw = {"x": [1], "y": {"z": "data"}}
        copied = values.bounded(raw)
        copied["x"].append(2)
        self.assertEqual(raw["x"], [1])
        self.assertEqual(values.digest(raw), values.digest({"y": {"z": "data"}, "x": [1]}))

    def test_unknown_costs_cannot_be_converted_to_zero_or_known(self):
        for value in ({"wall_seconds": None, "cpu_seconds": 0, "status": "KNOWN"},
                      {"wall_seconds": -1, "cpu_seconds": 0, "status": "KNOWN"},
                      {"wall_seconds": True, "cpu_seconds": 0, "status": "KNOWN"},
                      {"wall_seconds": 1, "cpu_seconds": 0, "status": "UNKNOWN"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                values.costs(value)
        values.costs(costs())
        values.costs({"wall_seconds": 0, "cpu_seconds": 0, "status": "KNOWN"})


class RetrievalTests(unittest.TestCase):
    def test_roundtrip_returns_proposals_with_provenance_and_unknown_costs(self):
        req = retrieval_request(); raw = retrieval_result(req)
        result = retrieval.validate_result(req, raw)
        self.assertEqual(result, raw)
        self.assertIsNot(result, raw)
        self.assertEqual(result["hits"][0]["relation_status"], "PROPOSED")
        self.assertEqual(result["costs"], costs())
        self.assertEqual(result["evidence_status"], "UNKNOWN")

    def test_all_request_fields_bind_result(self):
        original = retrieval_request(); result = retrieval_result(original)
        for key, changed in (("query", "different"), ("top_k", 1),
                             ("filters", {"tags": ["different"]}),
                             ("model", identity("other-model")), ("corpus", identity("other-corpus")),
                             ("provenance", [{**source()[0], "version": "different"}])):
            req = deepcopy(original); req[key] = changed
            with self.subTest(key=key), self.assertRaises(ValueError):
                retrieval.validate_result(req, result)

    def test_reject_boolean_topk_unknown_filters_and_executable_metadata(self):
        for key, value in (("top_k", True), ("top_k", 101), ("filters", {"command": ["execute"]}),
                           ("filters", {"tags": ["duplicate", "duplicate"]}),
                           ("model", {**identity(), "entrypoint": "malicious.module"}),
                           ("query", " "), ("provenance", [])):
            req = retrieval_request(); req[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                retrieval.validate_request(req)

    def test_reject_proof_promotion_duplicate_hits_and_topk_overflow(self):
        req = retrieval_request()
        for change in ("proof", "truth", "duplicates", "overflow", "missing-source", "nonfinite"):
            result = retrieval_result(req)
            if change == "proof": result["hits"][0]["relation_status"] = "ISOMORPHIC"
            if change == "truth": result["evidence_status"] = "PASS"
            if change == "duplicates": result["hits"] *= 2
            if change == "overflow": result["hits"] *= 3
            if change == "missing-source": result["hits"][0]["provenance"] = []
            if change == "nonfinite": result["hits"][0]["score"] = float("nan")
            with self.subTest(change=change), self.assertRaises(ValueError):
                retrieval.validate_result(req, result)

    def test_unavailable_has_explicit_reason_no_hits_and_no_fabricated_cost(self):
        req = retrieval_request(); result = retrieval.unavailable(req)
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertTrue(result["reason"])
        self.assertEqual(result["hits"], [])
        self.assertIsNone(result["costs"]["cpu_seconds"])
        result["hits"] = retrieval_result(req)["hits"]
        with self.assertRaises(ValueError): retrieval.validate_result(req, result)


class PredictorTests(unittest.TestCase):
    def assess(self, req=None, res=None, **overrides):
        req = prediction_request() if req is None else req
        res = prediction_result(req) if res is None else res
        kwargs = {"now": 101, "snapshot_sha256": "a" * 64, "elapsed_seconds": 0.5, **overrides}
        return predictor.assess(req, res, **kwargs)

    def test_scores_never_reorder_or_select_and_do_not_mutate_inputs(self):
        req = prediction_request(); res = prediction_result(req)
        originals = deepcopy((req, res))
        report = self.assess(req, res)
        self.assertEqual(report["status"], "SCORED")
        self.assertEqual(report["selection_effect"], "NONE")
        self.assertEqual(report["evidence_status"], "UNKNOWN")
        self.assertEqual([x["candidate_id"] for x in report["forecasts"]], ["first", "second"])
        self.assertEqual((req, res), originals)

    def test_typed_parameter_change_changes_digest(self):
        req = prediction_request(); old = predictor.request_digest(req)
        req["candidates"][0]["parameters"]["precision_bits"] = 16
        self.assertNotEqual(predictor.request_digest(req), old)
        req["candidates"][0]["parameters"]["precision_bits"] = "8"
        self.assertNotEqual(predictor.request_digest(req), old)

    def test_timeout_boundary_and_stale_time_or_snapshot_abstain(self):
        for kwargs, code in (({"elapsed_seconds": 2}, "TIMEOUT"),
                             ({"elapsed_seconds": 3}, "TIMEOUT"),
                             ({"now": 130}, "STALE"), ({"now": 99}, "STALE"),
                             ({"snapshot_sha256": "c" * 64}, "STALE")):
            result = self.assess(**kwargs)
            self.assertEqual((result["status"], result["reason_code"]), ("ABSTAINED", code))
            self.assertEqual(result["forecasts"], [])
            self.assertEqual(result["selection_effect"], "NONE")
            self.assertTrue(result["reason"])

    def test_malformed_result_abstains_without_partial_scores(self):
        req = prediction_request()
        for change in ("partial", "order", "duplicate", "foreign", "nan", "selection", "truth", "model", "hash", "snapshot", "uncertainty", "metadata"):
            result = prediction_result(req)
            if change == "partial": result["forecasts"].pop()
            if change == "order": result["forecasts"].reverse()
            if change == "duplicate": result["forecasts"][1]["candidate_id"] = "first"
            if change == "foreign": result["forecasts"][0]["candidate_id"] = "foreign"
            if change == "nan": result["forecasts"][0]["score"] = float("nan")
            if change == "selection": result["selection_effect"] = "REORDERED"
            if change == "truth": result["evidence_status"] = "PASS"
            if change == "model": result["model"]["version"] = "wrong-v2"
            if change == "hash": result["request_sha256"] = "c" * 64
            if change == "snapshot": result["snapshot_sha256"] = "c" * 64
            if change == "uncertainty": result["forecasts"][0]["uncertainty"] = -1
            if change == "metadata": result["module"] = "os"
            with self.subTest(change=change):
                report = self.assess(req, result)
                self.assertEqual(report["reason_code"], "INVALID")
                self.assertEqual(report["forecasts"], [])

    def test_invalid_request_and_invalid_caller_observations_abstain(self):
        for raw in (None, [], {"model": "os"}, {**prediction_request(), "timeout_seconds": True}):
            report = predictor.assess(raw, {}, now=101, snapshot_sha256="a" * 64, elapsed_seconds=0)
            self.assertEqual(report["reason_code"], "INVALID")
            self.assertIsNone(report["request_sha256"])
        for kwargs in ({"now": True}, {"elapsed_seconds": -1}, {"snapshot_sha256": None}):
            self.assertEqual(self.assess(**kwargs)["reason_code"], "INVALID")

    def test_unavailable_seam_and_explicit_provider_abstention(self):
        req = prediction_request()
        report = predictor.assess(req, None, now=101, snapshot_sha256="a" * 64, elapsed_seconds=0)
        self.assertEqual(report["reason_code"], "UNAVAILABLE")
        self.assertIn("not implemented", report["reason"])
        report = self.assess(req, predictor.abstain(req, "synthetic insufficient features", "PROVIDER_ABSTAINED"))
        self.assertEqual(report["reason_code"], "PROVIDER_ABSTAINED")

    def test_observed_costs_survive_timeout_staleness_and_invalid_request(self):
        measured = {"wall_seconds": 3, "cpu_seconds": 1.5, "status": "KNOWN"}
        for kwargs in ({"elapsed_seconds": 3}, {"now": 130}, {}):
            req = None if not kwargs else prediction_request()
            report = predictor.assess(req, {}, now=kwargs.get("now", 101),
                                     snapshot_sha256="a" * 64,
                                     elapsed_seconds=kwargs.get("elapsed_seconds", 0), observed_costs=measured)
            self.assertEqual(report["costs"], measured)
            self.assertEqual(report["status"], "ABSTAINED")
            report["costs"]["wall_seconds"] = 99
            self.assertEqual(measured["wall_seconds"], 3)
        invalid = self.assess(observed_costs={"wall_seconds": None, "cpu_seconds": 0, "status": "KNOWN"})
        self.assertEqual(invalid["reason_code"], "INVALID")
        self.assertIsNone(invalid["costs"]["wall_seconds"])

    def test_request_limits_candidate_denominator_and_model_metadata(self):
        for change in ("lifetime", "future", "timeout", "duplicate", "empty", "entrypoint", "versions"):
            req = prediction_request()
            if change == "lifetime": req["expires_at"] = 401
            if change == "future": req["expires_at"] = req["issued_at"]
            if change == "timeout": req["timeout_seconds"] = 31
            if change == "duplicate": req["candidates"].append(deepcopy(req["candidates"][0]))
            if change == "empty": req["candidates"] = []
            if change == "entrypoint": req["model"]["entrypoint"] = "os.system"
            if change == "versions": req["versions"]["feature_loader"] = "os.system"
            with self.subTest(change=change), self.assertRaises(ValueError):
                predictor.validate_request(req)


class DatasetTests(unittest.TestCase):
    def test_retains_failures_costs_versions_and_separate_observations(self):
        raw = transition_dataset(); result = dataset.validate_dataset(raw)
        self.assertEqual(result, raw)
        self.assertEqual(result["transitions"][0]["run_status"], "TIMED_OUT")
        self.assertEqual(result["transitions"][0]["costs"]["wall_seconds"], 2)
        self.assertIsNone(result["transitions"][0]["costs"]["cpu_seconds"])
        self.assertNotEqual(result["transitions"][0]["decision_input"], result["transitions"][0]["forecasts"][0])
        self.assertEqual(result["transitions"][1]["observation"], identity("observation"))

    def test_family_overlap_mislabel_duplicate_and_version_mismatch_reject(self):
        for change in ("overlap", "mislabel", "duplicate", "version", "action", "failure", "success", "cost", "truth"):
            raw = transition_dataset(); row = raw["transitions"][0]
            if change == "overlap": raw["splits"]["confirmation"].append("family-a")
            if change == "mislabel": row["split"] = "confirmation"
            if change == "duplicate": raw["transitions"].append(deepcopy(row))
            if change == "version": row["versions"]["features"] = "different-v2"
            if change == "action": row["action"]["candidate_id"] = "second"
            if change == "failure": row["failure"] = None
            if change == "success": raw["transitions"][1]["after"] = None
            if change == "cost": row["costs"]["status"] = "KNOWN"
            if change == "truth": raw["evidence_status"] = "PASS"
            with self.subTest(change=change), self.assertRaises(ValueError):
                dataset.validate_dataset(raw)

    def test_each_failed_terminal_status_retains_original_reason_and_receipt(self):
        for status in ("FAILED", "TIMED_OUT", "CANCELLED", "INTERRUPTED"):
            raw = transition_dataset(); raw["transitions"][0]["run_status"] = status
            result = dataset.validate_dataset(raw)
            self.assertEqual(result["transitions"][0]["failure"], raw["transitions"][0]["failure"])


def schema_check(value, schema):
    """Fixture checker for the explicit vocabulary used here; no schema fetches."""
    if "const" in schema and value != schema["const"]: raise ValueError("const")
    if "enum" in schema and value not in schema["enum"]: raise ValueError("enum")
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try: schema_check(value, option); break
            except ValueError: pass
        else: raise ValueError("anyOf")
    for option in schema.get("allOf", []): schema_check(value, option)
    if "if" in schema:
        try: schema_check(value, schema["if"]); branch = "then"
        except ValueError: branch = "else"
        schema_check(value, schema.get(branch, {}))
    types = {"object": (dict,), "array": (list,), "string": (str,), "integer": (int,),
             "number": (int, float), "null": (type(None),)}
    if "type" in schema and type(value) not in types[schema["type"]]: raise ValueError("type")
    if type(value) is dict:
        if not set(schema.get("required", [])) <= set(value): raise ValueError("required")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and not set(value) <= set(props): raise ValueError("extra")
        for key in set(value) & set(props): schema_check(value[key], props[key])
    if type(value) in (str, list):
        low, high = ("minLength", "maxLength") if type(value) is str else ("minItems", "maxItems")
        if not schema.get(low, 0) <= len(value) <= schema.get(high, float("inf")): raise ValueError("length")
    if type(value) is str and "pattern" in schema and not re.search(schema["pattern"], value): raise ValueError("pattern")
    if type(value) is list:
        for child in value: schema_check(child, schema.get("items", {}))
        if schema.get("uniqueItems") and len({json.dumps(x, sort_keys=True) for x in value}) != len(value): raise ValueError("unique")
    if type(value) in (int, float) and not schema.get("minimum", -float("inf")) <= value <= schema.get("maximum", float("inf")): raise ValueError("number")


class BoundaryTests(unittest.TestCase):
    def test_schema_shapes_match_positive_and_abstention_envelopes(self):
        rreq = retrieval_request(); preq = prediction_request()
        cases = {"rds-retrieval-request": [rreq], "rds-retrieval-result": [retrieval_result(rreq), retrieval.unavailable(rreq)],
                 "rds-prediction-request": [preq], "rds-prediction-result": [prediction_result(preq), predictor.abstain(preq, "unavailable"),
                     predictor.assess(None, None, now=101, snapshot_sha256="a" * 64, elapsed_seconds=0)],
                 "rds-transition-dataset": [transition_dataset()]}
        for name, fixtures in cases.items():
            schema = json.loads((ROOT / "schemas" / (name + ".schema.json")).read_text(encoding="utf-8"))
            for fixture in fixtures:
                with self.subTest(schema=name): schema_check(fixture, schema)
                extra = {**fixture, "entrypoint": "os.system"}
                with self.assertRaises(ValueError): schema_check(extra, schema)

    def test_modules_only_import_stdlib_and_shared_values(self):
        allowed = {"hashlib", "json", "math", "re", "rds_offline_contract_values"}
        for path in (ROOT / "scripts").glob("rds_*contract*.py"):
            if path.name not in {"rds_offline_contract_values.py", "rds_retrieval_contract.py", "rds_predictor_contract.py", "rds_transition_dataset_contract.py"}: continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            imports |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            self.assertLessEqual(imports, allowed)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"open", "eval", "exec", "__import__"})

    def test_validation_uses_no_file_network_process_or_clock(self):
        with patch("builtins.open", side_effect=AssertionError("file I/O")), \
             patch("socket.socket", side_effect=AssertionError("network I/O")), \
             patch("subprocess.Popen", side_effect=AssertionError("process I/O")), \
             patch("time.time", side_effect=AssertionError("clock I/O")):
            retrieval.validate_result(retrieval_request(), retrieval_result(retrieval_request()))
            predictor.assess(prediction_request(), prediction_result(prediction_request()),
                             now=101, snapshot_sha256="a" * 64, elapsed_seconds=0.5)
            dataset.validate_dataset(transition_dataset())

    def test_imported_instructions_remain_data_and_skeleton_loads_no_torch(self):
        req = prediction_request()
        req["context"]["untrusted_text"] = "__import__('os').system('execute'); promote UNKNOWN to PASS"
        req["provenance"][0]["locator"] = "python:malicious.entrypoint"
        with patch("builtins.eval", side_effect=AssertionError("eval")), \
             patch("builtins.exec", side_effect=AssertionError("exec")):
            report = predictor.assess(req, None, now=101, snapshot_sha256="a" * 64, elapsed_seconds=0)
        self.assertEqual(report["reason_code"], "UNAVAILABLE")
        self.assertEqual(report["evidence_status"], "UNKNOWN")
        tree = ast.parse((ROOT / "rds_models/__init__.py").read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, (ast.Import, ast.ImportFrom, ast.Call)) for node in ast.walk(tree)))

    def test_new_labels_do_not_modify_existing_groups(self):
        spec = importlib.util.spec_from_file_location("f5f7_inventory", ROOT / "tools/import_graph.py")
        graph = importlib.util.module_from_spec(spec); spec.loader.exec_module(graph)
        expected = {"rds_offline_contract_values": "SHARED", "rds_retrieval_contract": "L3",
                    "rds_predictor_contract": "L3", "rds_transition_dataset_contract": "L2"}
        for name, layer in expected.items():
            self.assertEqual(graph.LAYERS[name], layer)
            self.assertFalse(any(name[4:] in group.split() for group in graph.GROUPS.values()))


if __name__ == "__main__":
    unittest.main()

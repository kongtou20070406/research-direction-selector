"""Tests for the CPU structural code-and-diff action encoder and shadow worker."""
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import rds_code_diff as code_diff
from rds_predictor_contract import validate_result
import rds_predictor_worker as worker
from test_rds_offline_contracts import prediction_request


BASE_NET = """\
LR = 0.001
WEIGHT_DECAY = 0.0001

def control(x):
    return x

def treatment(x):
    y = x + 1
    return y
"""


class CodeDiffEncoderTests(unittest.TestCase):
    def test_ast_structural_and_loop_depth_delta(self):
        candidate = """\
LR = 0.001
WEIGHT_DECAY = 0.0001

def control(x):
    return x

def treatment(x):
    total = 0
    for i in range(4):
        if i > 0:
            total = total + x / i
    return total
"""
        diff = code_diff.encode_code_diff(BASE_NET, candidate)
        self.assertEqual(diff["schema"], "rds-code-diff-v1")
        self.assertEqual(diff["ast_delta"]["functions_modified"], ["treatment"])
        self.assertEqual(diff["ast_delta"]["loop_depth_delta"], 1)
        self.assertEqual(diff["ast_delta"]["branch_count_delta"], 1)
        self.assertIn("Div", diff["ast_delta"]["operators_added"])
        self.assertFalse(diff["obligation_signals"]["control_modified"])
        self.assertTrue(diff["obligation_signals"]["loop_nesting_increased"])
        self.assertTrue(diff["obligation_signals"]["division_or_pow_added"])
        self.assertEqual(
            len(diff["obligation_signals"]["feature_vector"]),
            len(code_diff.FEATURE_NAMES),
        )

    def test_role_filler_parameter_swap_is_distinguished(self):
        cand_a = BASE_NET.replace("LR = 0.001", "LR = 0.0005").replace(
            "WEIGHT_DECAY = 0.0001", "WEIGHT_DECAY = 0.0002"
        )
        cand_b = BASE_NET.replace("LR = 0.001", "LR = 0.0002").replace(
            "WEIGHT_DECAY = 0.0001", "WEIGHT_DECAY = 0.0005"
        )
        diff_a = code_diff.encode_code_diff(BASE_NET, cand_a)
        diff_b = code_diff.encode_code_diff(BASE_NET, cand_b)
        self.assertNotEqual(diff_a["action_diff_sha256"], diff_b["action_diff_sha256"])
        self.assertNotEqual(
            diff_a["parameter_delta"]["numeric_log10_ratios"],
            diff_b["parameter_delta"]["numeric_log10_ratios"],
        )
        score_a = code_diff.score_candidate_diff(BASE_NET, cand_a)
        score_b = code_diff.score_candidate_diff(BASE_NET, cand_b)
        self.assertNotEqual(score_a["score"], score_b["score"])

    def test_an_added_or_removed_function_is_signature_drift(self):
        import rds_cpu_jepa as cpu_jepa
        removed = BASE_NET.replace("def treatment(x):\n    y = x + 1\n    return y\n", "")
        added = BASE_NET + "\ndef helper(x):\n    return x\n"
        clean = code_diff.score_candidate_diff(BASE_NET, BASE_NET.replace("y = x + 1", "y = 2 * x + 1"))
        for candidate, field, name in ((removed, "functions_removed", "treatment"), (added, "functions_added", "helper")):
            diff = code_diff.encode_code_diff(BASE_NET, candidate)
            self.assertEqual(diff["ast_delta"][field], [name])
            self.assertTrue(diff["obligation_signals"]["signature_changed"])
            self.assertLess(code_diff.score_candidate_diff(BASE_NET, candidate)["score"], clean["score"])
            for model in (cpu_jepa.DirectCodeDiffRidge(), cpu_jepa.LatentCodeDiffJEPA()):
                obligations = model.predict({}, BASE_NET, candidate)["predicted_obligation_discharge"]
                self.assertFalse(obligations["signature_preserved"], (name, model))

    def test_methods_of_different_classes_keep_their_own_identity(self):
        base = "class A:\n    def f(self):\n        return 1\n\nclass B:\n    def f(self):\n        return 1\n"
        diff = code_diff.encode_code_diff(base, base.replace("return 1", "return 2", 1))
        self.assertEqual(diff["ast_delta"]["functions_modified"], ["A.f"])
        self.assertEqual(diff["parameter_delta"]["changed_bindings"], {"fn:A.f.return": {"before": 1, "after": 2}})
        twice = "def f(x):\n    return 1\n\ndef f(x):\n    return 1\n"
        diff = code_diff.encode_code_diff(twice, twice[:-2] + "2\n")
        self.assertEqual(diff["ast_delta"]["functions_modified"], ["f#2"])

    def test_every_part_of_a_signature_counts(self):
        base = "def f(x, y=1):\n    return x\n"
        drift = {"keyword-only": "def f(x, y=1, *, timeout=1):", "positional-only": "def f(x, /, y=1):",
                 "var-positional": "def f(x, y=1, *rest):", "var-keyword": "def f(x, y=1, **options):",
                 "annotation": "def f(x: int, y=1):", "return annotation": "def f(x, y=1) -> int:",
                 "new default": "def f(x=0, y=1):"}
        for kind, header in drift.items():
            signals = code_diff.encode_code_diff(base, base.replace("def f(x, y=1):", header))["obligation_signals"]
            self.assertTrue(signals["signature_changed"], kind)
        # Changing a default value is a parameter delta, not signature drift.
        diff = code_diff.encode_code_diff(base, base.replace("y=1", "y=2"))
        self.assertFalse(diff["obligation_signals"]["signature_changed"])
        self.assertEqual(diff["parameter_delta"]["changed_bindings"], {"fn:f.default:y": {"before": 1, "after": 2}})
        diff = code_diff.encode_code_diff(base.replace("y=1", "*, y=1"), base.replace("y=1", "*, y=3"))
        self.assertEqual(diff["parameter_delta"]["changed_bindings"], {"fn:f.default:y": {"before": 1, "after": 3}})

    def test_control_modification_is_flagged_and_penalized(self):
        clean_treatment = BASE_NET.replace("y = x + 1", "y = 2 * x + 1")
        tampered_control = BASE_NET.replace("def control(x):\n    return x", "def control(x):\n    return 0")
        s_clean = code_diff.score_candidate_diff(BASE_NET, clean_treatment)
        s_tampered = code_diff.score_candidate_diff(BASE_NET, tampered_control)
        self.assertFalse(s_clean["obligation_signals"]["control_modified"])
        self.assertTrue(s_tampered["obligation_signals"]["control_modified"])
        self.assertGreater(s_clean["score"], s_tampered["score"])
        self.assertLess(s_clean["uncertainty"], s_tampered["uncertainty"])

    def test_predictor_worker_scores_candidate_diffs_in_subprocess(self):
        req = prediction_request()
        req.update(
            issued_at=time.time() - 1,
            expires_at=time.time() + 30,
            timeout_seconds=4,
            context={"base_code": BASE_NET},
            candidates=[
                {
                    "id": "clean_treatment",
                    "parameters": {"code": BASE_NET.replace("y = x + 1", "y = 2 * x + 1")},
                },
                {
                    "id": "tampered_control",
                    "parameters": {
                        "code": BASE_NET.replace("def control(x):\n    return x", "def control(x):\n    return 0")
                    },
                },
            ],
        )
        report = worker.PredictionWorker(req, adapter="code_diff_linear", snapshot_sha256="a" * 64).run()
        pred = report["prediction"]
        validate_result(req, pred)
        self.assertEqual(pred["status"], "SCORED")
        self.assertEqual(pred["selection_effect"], "NONE")
        self.assertEqual(pred["evidence_status"], "UNKNOWN")
        self.assertGreater(pred["forecasts"][0]["score"], pred["forecasts"][1]["score"])


if __name__ == "__main__":
    unittest.main()

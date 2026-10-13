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

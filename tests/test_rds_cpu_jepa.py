"""Tests for CPU Epistemic Transition World Model (JEPA, Ridge, Bag-of-Numbers)."""
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import rds_cpu_jepa as cpu_jepa
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


class CpuJepaTransitionModelTests(unittest.TestCase):
    def setUp(self):
        self.bag = cpu_jepa.BagOfNumbersBaseline()
        self.ridge = cpu_jepa.DirectCodeDiffRidge()
        self.jepa = cpu_jepa.LatentCodeDiffJEPA()

    def test_role_swap_invariance_failure_vs_exact_discrimination(self):
        # Candidate A: LR decreased, WEIGHT_DECAY increased
        cand_a = BASE_NET.replace("LR = 0.001", "LR = 0.0005").replace(
            "WEIGHT_DECAY = 0.0001", "WEIGHT_DECAY = 0.0002"
        )
        # Candidate B: LR and WEIGHT_DECAY swapped compared to Candidate A
        cand_b = BASE_NET.replace("LR = 0.001", "LR = 0.0002").replace(
            "WEIGHT_DECAY = 0.0001", "WEIGHT_DECAY = 0.0005"
        )

        # 1. BagOfNumbersBaseline ignores role identities and yields identical predictions
        pred_bag_a = self.bag.predict({}, BASE_NET, cand_a)
        pred_bag_b = self.bag.predict({}, BASE_NET, cand_b)
        self.assertEqual(pred_bag_a["score"], pred_bag_b["score"])
        self.assertEqual(pred_bag_a["cost"], pred_bag_b["cost"])

        # 2. DirectCodeDiffRidge preserves role-bound parameter semantics and discriminates
        pred_ridge_a = self.ridge.predict({}, BASE_NET, cand_a)
        pred_ridge_b = self.ridge.predict({}, BASE_NET, cand_b)
        self.assertNotEqual(pred_ridge_a["score"], pred_ridge_b["score"])

        # 3. LatentCodeDiffJEPA encodes role-bound deltas into action representation and discriminates
        pred_jepa_a = self.jepa.predict({}, BASE_NET, cand_a)
        pred_jepa_b = self.jepa.predict({}, BASE_NET, cand_b)
        self.assertNotEqual(pred_jepa_a["score"], pred_jepa_b["score"])

    def test_control_vs_treatment_tampering_discrimination(self):
        sym_base = (
            "LR = 0.001\n"
            "WEIGHT_DECAY = 0.0001\n\n"
            "def control(x):\n"
            "    return x + 1\n\n"
            "def treatment(x):\n"
            "    return x + 1\n"
        )
        cand_control = sym_base.replace("def control(x):\n    return x + 1", "def control(x):\n    return x + 2")
        cand_treatment = sym_base.replace("def treatment(x):\n    return x + 1", "def treatment(x):\n    return x + 2")

        # BagOfNumbersBaseline sees the exact same numeric multiset [0.0001, 0.001, 1, 2]
        pred_bag_ctrl = self.bag.predict({}, sym_base, cand_control)
        pred_bag_treat = self.bag.predict({}, sym_base, cand_treatment)
        self.assertEqual(pred_bag_ctrl["score"], pred_bag_treat["score"])

        # DirectCodeDiffRidge flags control arm modification
        pred_ridge_ctrl = self.ridge.predict({}, sym_base, cand_control)
        pred_ridge_treat = self.ridge.predict({}, sym_base, cand_treatment)
        self.assertGreater(pred_ridge_treat["score"], pred_ridge_ctrl["score"])
        self.assertFalse(pred_ridge_ctrl["predicted_obligation_discharge"]["control_preserved"])
        self.assertTrue(pred_ridge_treat["predicted_obligation_discharge"]["control_preserved"])

        # LatentCodeDiffJEPA penalizes control modification and preserves obligation
        pred_jepa_ctrl = self.jepa.predict({}, sym_base, cand_control)
        pred_jepa_treat = self.jepa.predict({}, sym_base, cand_treatment)
        self.assertGreater(pred_jepa_treat["score"], pred_jepa_ctrl["score"])
        self.assertFalse(pred_jepa_ctrl["predicted_obligation_discharge"]["control_preserved"])
        self.assertTrue(pred_jepa_treat["predicted_obligation_discharge"]["control_preserved"])

    def test_latent_jepa_epistemic_uncertainty_increases_on_unseen_structures(self):
        cand_normal = BASE_NET.replace("y = x + 1", "y = 2 * x + 1")
        # Injected unseen operator: BitXor (^)
        cand_unseen_op = BASE_NET.replace("y = x + 1", "y = x ^ 3")
        # Injected nested loop structure
        cand_deep_loop = """\
LR = 0.001
WEIGHT_DECAY = 0.0001

def control(x):
    return x

def treatment(x):
    total = 0
    for i in range(10):
        for j in range(10):
            total = total + x
    return total
"""
        pred_normal = self.jepa.predict({}, BASE_NET, cand_normal)
        pred_unseen_op = self.jepa.predict({}, BASE_NET, cand_unseen_op)
        pred_deep_loop = self.jepa.predict({}, BASE_NET, cand_deep_loop)

        # Latent error ||z_hat_after - stopgrad(z_after)||_2 increases on novel operator
        self.assertGreater(pred_unseen_op["latent_error"], pred_normal["latent_error"])
        # Epistemic uncertainty increases on unseen operator
        self.assertGreater(pred_unseen_op["uncertainty"], pred_normal["uncertainty"])
        # Epistemic uncertainty increases on unseen deep loop nesting
        self.assertGreater(pred_deep_loop["uncertainty"], pred_normal["uncertainty"])

    def test_zero_shot_and_fitting_api(self):
        # Zero-shot default
        zero_shot = cpu_jepa.predict_transition({}, BASE_NET, BASE_NET.replace("y = x + 1", "y = 2 * x + 1"))
        self.assertIn("score", zero_shot)
        self.assertIn("uncertainty", zero_shot)
        self.assertIn("cost", zero_shot)
        self.assertIn("latent_error", zero_shot)

        # Fitting on transition records
        records = [
            {
                "before_state": {"score": 0.5, "cost": 1.0},
                "base_code": BASE_NET,
                "candidate_code": BASE_NET.replace("y = x + 1", "y = 2 * x + 1"),
                "outcome_score": 0.85,
                "cost": 1.05,
            },
            {
                "before_state": {"score": 0.5, "cost": 1.0},
                "base_code": BASE_NET,
                "candidate_code": BASE_NET.replace("def control(x):\n    return x", "def control(x):\n    return 0"),
                "outcome_score": 0.10,
                "cost": 1.00,
            },
        ]
        fitted_jepa = cpu_jepa.fit_transitions(records, model_type="latent_jepa")
        pred_fitted = fitted_jepa.predict({}, BASE_NET, BASE_NET.replace("y = x + 1", "y = 2 * x + 1"))
        self.assertIsInstance(pred_fitted["score"], float)

        fitted_ridge = cpu_jepa.fit_transitions(records, model_type="ridge")
        pred_ridge = fitted_ridge.predict({}, BASE_NET, BASE_NET.replace("y = x + 1", "y = 2 * x + 1"))
        self.assertIsInstance(pred_ridge["score"], float)

        fitted_bag = cpu_jepa.fit_transitions(records, model_type="bag_of_numbers")
        pred_bag = fitted_bag.predict({}, BASE_NET, BASE_NET.replace("y = x + 1", "y = 2 * x + 1"))
        self.assertIsInstance(pred_bag["score"], float)

    def test_explicit_knobs_beside_the_code_are_refused_not_merged(self):
        code = BASE_NET.replace("y = x + 1", "y = 2 * x + 1")
        for model in (self.ridge, self.jepa):
            self.assertIsInstance(model.predict({}, BASE_NET, code, {"code": code})["score"], float)
            for knobs in ({"learning_rate": 0.1}, {"code": code, "batch_size": 64}):
                with self.subTest(model=type(model).__name__, knobs=knobs), self.assertRaises(ValueError):
                    model.predict({}, BASE_NET, code, knobs)
        record = {"base_code": BASE_NET, "action": {"parameters": {"code": code, "learning_rate": 0.1}},
                  "outcome_score": 0.5, "cost": 1.0}
        for kind in ("ridge", "latent_jepa"):
            with self.subTest(fit=kind), self.assertRaises(ValueError):
                cpu_jepa.fit_transitions([record], model_type=kind)
        for adapter in ("cpu_jepa", "code_diff_linear"):
            req = prediction_request()
            req.update(issued_at=time.time() - 1, expires_at=time.time() + 30, timeout_seconds=4,
                       context={"base_code": BASE_NET},
                       candidates=[{"id": "lr_small", "parameters": {"code": code, "learning_rate": 0.001}},
                                   {"id": "lr_large", "parameters": {"code": code, "learning_rate": 0.1}}])
            pred = worker.PredictionWorker(req, adapter=adapter, snapshot_sha256="a" * 64).run()["prediction"]
            validate_result(req, pred)
            self.assertEqual((pred["status"], pred["reason_code"], pred["forecasts"]),
                             ("ABSTAINED", "PROVIDER_ABSTAINED", []), adapter)

    def test_prediction_worker_subprocess_cpu_jepa_passes_contract(self):
        req = prediction_request()
        req.update(
            issued_at=time.time() - 1,
            expires_at=time.time() + 30,
            timeout_seconds=4,
            context={"base_code": BASE_NET},
            candidates=[
                {
                    "id": "clean_refinement",
                    "parameters": {"code": BASE_NET.replace("y = x + 1", "y = 2 * x + 1")},
                },
                {
                    "id": "tampered_control",
                    "parameters": {
                        "code": BASE_NET.replace("def control(x):\n    return x", "def control(x):\n    return 0")
                    },
                },
                {
                    "id": "unseen_operator",
                    "parameters": {"code": BASE_NET.replace("y = x + 1", "y = x ^ 3")},
                },
            ],
        )

        report = worker.PredictionWorker(req, adapter="cpu_jepa", snapshot_sha256="a" * 64).run()
        pred = report["prediction"]
        validate_result(req, pred)
        self.assertEqual(pred["status"], "SCORED")
        self.assertEqual(pred["selection_effect"], "NONE")
        self.assertEqual(pred["evidence_status"], "UNKNOWN")
        self.assertEqual(len(pred["forecasts"]), 3)

        forecast_map = {f["candidate_id"]: f for f in pred["forecasts"]}
        # Clean refinement should score higher than tampered control
        self.assertGreater(forecast_map["clean_refinement"]["score"], forecast_map["tampered_control"]["score"])
        # Unseen operator should have higher uncertainty than clean refinement
        self.assertGreater(
            forecast_map["unseen_operator"]["uncertainty"],
            forecast_map["clean_refinement"]["uncertainty"],
        )


if __name__ == "__main__":
    unittest.main()

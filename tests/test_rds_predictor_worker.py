"""Real trusted subprocess fixtures, not installed-model or scientific tests."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import rds_predictor_worker as worker
from rds_predictor_contract import validate_result
from test_rds_offline_contracts import prediction_request


def request(timeout=4):
    req = prediction_request()
    req.update(issued_at=time.time() - 1, expires_at=time.time() + 30, timeout_seconds=timeout)
    return req


class WorkerTests(unittest.TestCase):
    def handle(self, req=None, adapter="unavailable", **kwargs):
        return worker.PredictionWorker(request() if req is None else req, adapter=adapter,
                                       snapshot_sha256=kwargs.get("snapshot_sha256", "a" * 64))

    def assert_stopped(self, report):
        execution = report["execution"]
        self.assertTrue(execution["reaped"])
        self.assertTrue(execution["cleanup_complete"])
        self.assertLessEqual(execution["retained_bytes"], worker.MAX_OUTPUT_BYTES)
        self.assertEqual(report["prediction"]["selection_effect"], "NONE")
        self.assertEqual(report["prediction"]["evidence_status"], "UNKNOWN")
        if execution["attempts"]:
            self.assertIsNotNone(execution["returncode"])
        self.assertFalse(any(t.name == "rds-prediction-io" and t.is_alive() for t in threading.enumerate()))

    def test_default_real_child_is_unavailable_and_job_owned(self):
        req = request(); report = self.handle(req).run()
        self.assertEqual(report["execution"]["attempts"], 1)
        self.assertNotEqual(report["execution"]["pid"], os.getpid())
        self.assertEqual(report["execution"]["ownership"], "WINDOWS_JOB" if os.name == "nt" else "POSIX_PROCESS_GROUP")
        self.assertEqual(report["prediction"]["reason_code"], "UNAVAILABLE")
        self.assertIsNone(report["prediction"]["costs"]["cpu_seconds"])
        validate_result(req, report["prediction"])
        self.assert_stopped(report)

    def test_fixed_fixture_explicitly_labelled_and_contract_compatible(self):
        req = request(); raw = deepcopy(req)
        report = self.handle(req, "fixture_score").run()
        self.assertEqual(req, raw)
        self.assertEqual(report["prediction"]["status"], "SCORED")
        self.assertEqual(report["execution"]["source_kind"], "FIXTURE_ONLY")
        self.assertIn("FIXTURE_ONLY", report["prediction"]["reason"])
        validate_result(req, report["prediction"])
        self.assert_stopped(report)

    def test_invalid_bytes_metadata_adapter_and_numeric_input_do_not_launch(self):
        cases = [b"not JSON", b'{"schema":1,"schema":2}', b"x" * (worker.MAX_INPUT_BYTES + 1),
                 {**request(), "timeout_seconds": True},
                 {**request(), "model": {"entrypoint": "os.system"}}, None]
        with patch.object(worker, "_launch", side_effect=AssertionError("must not launch")):
            for raw in cases:
                handle = worker.PredictionWorker(raw, snapshot_sha256="a" * 64)
                report = handle.run()
                self.assertEqual(report["execution"]["attempts"], 0)
                self.assertEqual(report["prediction"]["reason_code"], "INVALID")
            for adapter in ("os.system", "python -c exec(1)", {"module": "os"}):
                report = self.handle(adapter=adapter).run()
                self.assertEqual(report["execution"]["attempts"], 0)

    def test_input_bytes_and_user_instructions_remain_inert(self):
        req = request()
        req["context"] = {"code": "__import__('os').system('never execute')", "adapter": "fixture_score"}
        raw = json.dumps(req).encode("utf-8")
        report = self.handle(raw).run()
        self.assertEqual(report["prediction"]["reason_code"], "UNAVAILABLE")
        self.assert_stopped(report)

    def test_stale_time_and_snapshot_never_launch(self):
        with patch.object(worker, "_launch", side_effect=AssertionError("must not launch")):
            req = request(); req.update(issued_at=time.time() - 10, expires_at=time.time() - 1)
            for handle in (self.handle(req), self.handle(snapshot_sha256="b" * 64)):
                report = handle.run()
                self.assertEqual(report["prediction"]["reason_code"], "STALE")
                self.assertEqual(report["execution"]["attempts"], 0)

    def test_timeout_and_late_failure_are_discarded_and_reaped(self):
        for adapter in ("fixture_sleep", "fixture_late_failure", "fixture_no_read"):
            handle = self.handle(request(0.4), adapter)
            started = time.monotonic(); report = handle.run()
            self.assertLess(time.monotonic() - started, 4)
            self.assertEqual(report["prediction"]["reason_code"], "TIMEOUT")
            self.assertEqual(report["prediction"]["forecasts"], [])
            self.assert_stopped(report)

    def test_nonzero_exit_rejects_even_complete_valid_forecasts(self):
        report = self.handle(adapter="fixture_failure").run()
        self.assertEqual(report["execution"]["returncode"], 7)
        self.assertGreater(report["execution"]["stdout_bytes"], 0)
        self.assertEqual(report["prediction"]["reason_code"], "INVALID")
        self.assertEqual(report["prediction"]["forecasts"], [])
        self.assert_stopped(report)

    def test_valid_early_output_is_discarded_when_process_finishes_late(self):
        report = self.handle(request(0.8), "fixture_output_then_sleep").run()
        self.assertGreater(report["execution"]["stdout_bytes"], 0)
        self.assertEqual(report["prediction"]["reason_code"], "TIMEOUT")
        self.assertEqual(report["prediction"]["forecasts"], [])
        self.assert_stopped(report)

    def test_fixed_combined_output_bound_stdout_and_stderr(self):
        for adapter in ("fixture_overflow", "fixture_stderr"):
            report = self.handle(adapter=adapter).run()
            self.assertEqual(report["execution"]["outcome"], "OUTPUT_LIMIT")
            self.assertEqual(report["execution"]["retained_bytes"], worker.MAX_OUTPUT_BYTES)
            self.assertEqual(report["prediction"]["reason_code"], "INVALID")
            self.assert_stopped(report)

    def test_duplicate_key_output_rejected(self):
        report = self.handle(adapter="fixture_invalid").run()
        self.assertEqual(report["prediction"]["reason_code"], "INVALID")
        self.assertIn("Duplicate JSON key", report["prediction"]["reason"])
        self.assert_stopped(report)

    def test_cancel_before_launch_and_during_execution(self):
        handle = self.handle(adapter="fixture_sleep"); handle.cancel()
        self.assertEqual(handle.run()["execution"]["attempts"], 0)
        handle = self.handle(adapter="fixture_sleep")
        timer = threading.Timer(0.3, handle.cancel); timer.start()
        try: report = handle.run()
        finally: timer.join()
        self.assertEqual(report["execution"]["outcome"], "CANCELLED")
        self.assertEqual(report["prediction"]["forecasts"], [])
        self.assert_stopped(report)

    def test_request_becoming_stale_during_execution_discards_late_result(self):
        handle = self.handle(adapter="fixture_sleep")
        timer = threading.Timer(0.3, lambda: handle.update_snapshot("b" * 64)); timer.start()
        try: report = handle.run()
        finally: timer.join()
        self.assertEqual(report["prediction"]["reason_code"], "STALE")
        self.assert_stopped(report)

    def test_repeated_and_concurrent_run_never_execute_twice(self):
        handle = self.handle(adapter="fixture_score")
        results = []
        with patch.object(worker, "_launch", wraps=worker._launch) as launch:
            threads = [threading.Thread(target=lambda: results.append(handle.run())) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=5)
            self.assertFalse(any(t.is_alive() for t in threads))
            self.assertEqual(launch.call_count, 1)
            again = handle.run()
            self.assertEqual(launch.call_count, 1)
        self.assertEqual(results[0], results[1])
        again["prediction"]["forecasts"].clear()
        self.assertTrue(handle.run()["prediction"]["forecasts"])
        self.assert_stopped(results[0])

    def test_launch_failure_does_not_retry_or_silently_drop_job_ownership(self):
        handle = self.handle()
        with patch.object(worker, "_launch", side_effect=OSError("synthetic Job assignment failure")) as launch:
            report = handle.run(); self.assertEqual(handle.run(), report)
            self.assertEqual(launch.call_count, 1)
        self.assertEqual(report["execution"]["outcome"], "LAUNCH_FAILED")
        self.assertEqual(report["prediction"]["reason_code"], "UNAVAILABLE")

    def test_cached_score_becoming_stale_abstains_without_relaunch_or_refund(self):
        handle = self.handle(adapter="fixture_score")
        with patch.object(worker, "_launch", wraps=worker._launch) as launch:
            first = handle.run()
            handle.update_snapshot("b" * 64)
            stale = handle.run()
            self.assertEqual(launch.call_count, 1)
        self.assertEqual(first["prediction"]["status"], "SCORED")
        self.assertEqual(stale["prediction"]["reason_code"], "STALE")
        self.assertEqual(stale["prediction"]["forecasts"], [])
        self.assertEqual(first["prediction"]["costs"], stale["prediction"]["costs"])
        self.assertEqual(first["execution"], stale["execution"])

    def test_unexpected_interruption_is_cached_without_a_second_execution(self):
        handle = self.handle()
        with patch.object(worker, "_launch", side_effect=RuntimeError("synthetic interruption")) as launch:
            with self.assertRaises(RuntimeError): handle.run()
            report = handle.run()
            self.assertEqual(launch.call_count, 1)
        self.assertEqual(report["prediction"]["reason_code"], "INVALID")
        self.assertFalse(report["execution"]["cleanup_complete"])

    def test_child_rejects_oversized_and_invalid_input_without_provider_execution(self):
        command = [sys.executable, "-I", "-S", "-B", str(ROOT / "scripts/rds_predictor_worker.py"), "--child", "fixture_score"]
        for raw in (b"not-json", b"x" * (worker.MAX_INPUT_BYTES + 1)):
            run = subprocess.run(command, input=raw, capture_output=True, timeout=5)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(run.stdout, b"")

    def test_l1_has_only_same_layer_and_shared_local_edges(self):
        spec = importlib.util.spec_from_file_location("worker_graph", ROOT / "tools/import_graph.py")
        graph = importlib.util.module_from_spec(spec); spec.loader.exec_module(graph)
        self.assertEqual(graph.LAYERS["rds_predictor_worker"], "L1")
        source = (ROOT / "scripts/rds_predictor_worker.py").read_text(encoding="utf-8")
        self.assertNotIn("from rds_predictor_contract", source)
        self.assertNotIn("import torch", source)


if __name__ == "__main__":
    unittest.main()

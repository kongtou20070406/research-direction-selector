"""L1 one-shot trusted CPU transport; no ledger writes or configured code loading.

Windows reuses the owned Job launcher; POSIX uses an owned process group.
This bounds captured I/O and wall time, not OS network or memory access.
L3 callers retain prediction policy/contract assessment outside this worker.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

# Isolated child startup admits only this fixed adjacent source directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rds_offline_contract_values import (
    MAX_BYTES, bounded, costs, digest, fields, identity, number, provenance,
    require, sha256, text, token,
)

MAX_INPUT_BYTES = MAX_BYTES
MAX_OUTPUT_BYTES = 32768
ADAPTERS = ("unavailable", "code_diff_linear", "cpu_jepa", "bayes_blr", "fixture_score", "fixture_sleep", "fixture_failure",
            "fixture_late_failure", "fixture_overflow", "fixture_stderr",
            "fixture_invalid", "fixture_no_read", "fixture_output_then_sleep")


def _load(raw):
    def pairs(entries):
        result = {}
        for key, value in entries:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")))


def _request(value):
    """L1 transport admission without importing L3 policy upward."""
    value = bounded(value)
    fields(value, "schema model snapshot candidates context versions provenance issued_at expires_at timeout_seconds",
           "worker request")
    require(value["schema"] == "rds-prediction-request-v1", "Unsupported request schema")
    identity(value["model"], "model")
    identity(value["snapshot"], "snapshot")
    fields(value["versions"], "engine features policy", "versions")
    for name, version in value["versions"].items():
        text(version, name + " version", 64)
    require(type(value["context"]) is dict, "Context must be JSON values")
    entries = value["candidates"]
    require(type(entries) is list and 1 <= len(entries) <= 128, "Candidate count exceeds bound")
    seen = set()
    for entry in entries:
        fields(entry, "id parameters", "candidate")
        token(entry["id"], "candidate id")
        require(entry["id"] not in seen and type(entry["parameters"]) is dict,
                "Duplicate candidate or invalid parameters")
        seen.add(entry["id"])
    provenance(value["provenance"])
    number(value["issued_at"], "issued_at", 0)
    number(value["expires_at"], "expires_at", 0)
    require(0 < value["expires_at"] - value["issued_at"] <= 300, "Invalid request lifetime")
    number(value["timeout_seconds"], "timeout", 0.001, 30)
    return value


def _abstain(request, reason, code):
    return {"schema": "rds-prediction-result-v1", "request_sha256": digest(request) if request else None,
            "model": request["model"] if request else None,
            "snapshot_sha256": request["snapshot"]["sha256"] if request else None,
            "status": "ABSTAINED", "reason": reason[:2048], "reason_code": code,
            "forecasts": [], "selection_effect": "NONE", "evidence_status": "UNKNOWN",
            "costs": {"wall_seconds": None, "cpu_seconds": None, "status": "UNKNOWN"}}


def _response(request, value):
    value = bounded(value)
    fields(value, "schema request_sha256 model snapshot_sha256 status reason reason_code forecasts costs selection_effect evidence_status",
           "worker response")
    require(value["schema"] == "rds-prediction-result-v1" and value["request_sha256"] == digest(request)
            and value["model"] == request["model"]
            and value["snapshot_sha256"] == request["snapshot"]["sha256"], "Response binding mismatch")
    require(value["selection_effect"] == "NONE" and value["evidence_status"] == "UNKNOWN",
            "Response cannot select or promote truth")
    require(type(value["reason"]) is str and len(value["reason"]) <= 2048, "Invalid response reason")
    forecasts = value["forecasts"]
    require(type(forecasts) is list and len(forecasts) <= 128, "Invalid forecasts")
    if value["status"] == "ABSTAINED":
        require(not forecasts and value["reason_code"] in
                ("INVALID", "TIMEOUT", "STALE", "UNAVAILABLE", "PROVIDER_ABSTAINED"), "Invalid abstention")
        text(value["reason"], "Abstention reason", 2048)
    else:
        require(value["status"] == "SCORED" and value["reason_code"] == "NONE", "Invalid response status")
        require([entry.get("candidate_id") if type(entry) is dict else None for entry in forecasts]
                == [entry["id"] for entry in request["candidates"]], "Forecast denominator/order mismatch")
    for entry in forecasts:
        fields(entry, "candidate_id score uncertainty", "forecast")
        token(entry["candidate_id"], "forecast candidate")
        number(entry["score"], "score")
        if entry["uncertainty"] is not None:
            number(entry["uncertainty"], "uncertainty", 0, 1)
    costs(value["costs"])
    return value


def _launch(adapter):
    command = [sys.executable, "-I", "-S", "-B", str(Path(__file__).resolve()), "--child", adapter]
    options = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   cwd=Path(__file__).resolve().parent, shell=False, close_fds=True)
    # Same-layer primitives; never downgrade failed Windows Job ownership.
    from rds_project import _Job, _WindowsPopen
    if os.name == "nt":
        process = _WindowsPopen(command, creationflags=0x08000000 | 0x4, **options)
        job = _Job(process)
        try:
            job.resume()
        except BaseException:
            job.stop(); process.wait(timeout=2); job.close()
            raise
    else:
        process = subprocess.Popen(command, start_new_session=True, **options)
        job = _Job(process)
    return process, job


class PredictionWorker:
    """One handle, at most one attempt. Repeated run() returns a detached receipt.

    Across separately constructed handles, deduplication remains a caller/ledger
    responsibility. A local cancel event never grants execution authority.
    """
    def __init__(self, request, *, adapter="unavailable", snapshot_sha256):
        self._run_lock = threading.Lock()
        self._binding_lock = threading.Lock()
        self._cancel = threading.Event()
        self._saved = None
        self._execution = None
        self._request = None
        self._error = None
        self._adapter = adapter
        self._snapshot = snapshot_sha256
        try:
            require(type(adapter) is str and adapter in ADAPTERS, "Adapter is not fixed registered source")
            sha256(snapshot_sha256, "Caller snapshot")
            if type(request) is bytes:
                require(len(request) <= MAX_INPUT_BYTES, "Input exceeds fixed byte limit")
                request = _load(request.decode("utf-8"))
            self._request = _request(request)
        except (ValueError, RecursionError) as exc:
            self._error = str(exc)

    def cancel(self):
        self._cancel.set()

    def update_snapshot(self, value):
        sha256(value, "Caller snapshot")
        with self._binding_lock:
            self._snapshot = value

    def _stale(self):
        with self._binding_lock:
            return (self._snapshot != self._request["snapshot"]["sha256"] or
                    not self._request["issued_at"] <= time.time() < self._request["expires_at"])

    def run(self):
        with self._run_lock:
            if self._saved is None:
                try:
                    self._saved = self._run_once()
                finally:
                    # Even an unexpected interruption cannot make this handle retry.
                    if self._saved is None:
                        self._saved = {"prediction": _abstain(self._request, "Worker interrupted; no retry", "INVALID"),
                                       "execution": {**(self._execution or {}), "outcome": "WORKER_ERROR",
                                                     "cleanup_complete": False}}
            report = deepcopy(self._saved)
            if self._request is not None and report["prediction"]["status"] == "SCORED":
                if self._cancel.is_set() or self._stale():
                    old_costs = report["prediction"]["costs"]
                    report["prediction"] = _abstain(self._request, "Cached result no longer deliverable; no retry",
                                                   "PROVIDER_ABSTAINED" if self._cancel.is_set() else "STALE")
                    report["prediction"]["costs"] = old_costs
            return report

    def _run_once(self):
        started = time.monotonic()
        request = self._request
        execution = {"attempts": 0, "pid": None, "returncode": None, "reaped": True,
                     "source_kind": "FIXTURE_ONLY" if type(self._adapter) is str and self._adapter.startswith("fixture_") else "UNAVAILABLE",
                     "adapter": self._adapter if type(self._adapter) is str and self._adapter in ADAPTERS else None,
                     "ownership": None, "outcome": None, "stdout_bytes": 0, "stderr_bytes": 0,
                     "retained_bytes": 0, "input_limit_bytes": MAX_INPUT_BYTES,
                     "output_limit_bytes": MAX_OUTPUT_BYTES, "cleanup_complete": True}
        self._execution = execution
        result = _abstain(request, self._error or "Unavailable", "INVALID" if self._error else "UNAVAILABLE")
        if self._error:
            execution["outcome"] = "INVALID_INPUT"
            return {"prediction": result, "execution": execution}
        if self._stale() or self._cancel.is_set():
            result = _abstain(request, "Cancelled before launch" if self._cancel.is_set() else "Stale before launch",
                              "PROVIDER_ABSTAINED" if self._cancel.is_set() else "STALE")
            execution["outcome"] = "CANCELLED" if self._cancel.is_set() else "STALE"
            return {"prediction": result, "execution": execution}
        raw = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        process, job, threads = None, None, []
        lock, overflow = threading.Lock(), threading.Event()
        captured = {"stdout": bytearray(), "stderr": bytearray()}
        stream_error = []

        def read(pipe, name):
            try:
                while True:
                    chunk = pipe.read1(4096)
                    if not chunk: break
                    with lock:
                        execution[name + "_bytes"] += len(chunk)
                        room = MAX_OUTPUT_BYTES - execution["retained_bytes"]
                        captured[name].extend(chunk[:room])
                        execution["retained_bytes"] += min(room, len(chunk))
                        if len(chunk) > room: overflow.set()
                    if overflow.is_set(): break
            except OSError as exc:
                stream_error.append(str(exc))

        def write():
            try:
                process.stdin.write(raw)
                process.stdin.close()
            except (OSError, ValueError) as exc:
                stream_error.append(str(exc))

        try:
            execution["attempts"] = 1
            process, job = _launch(self._adapter)
            execution.update(pid=process.pid, reaped=False,
                             ownership="WINDOWS_JOB" if os.name == "nt" else "POSIX_PROCESS_GROUP")
            for target, args in ((read, (process.stdout, "stdout")), (read, (process.stderr, "stderr")), (write, ())):
                thread = threading.Thread(target=target, args=args, daemon=True, name="rds-prediction-io")
                threads.append(thread); thread.start()
            while True:
                elapsed = time.monotonic() - started
                if self._cancel.is_set():
                    execution["outcome"] = "CANCELLED"; break
                if self._stale():
                    execution["outcome"] = "STALE"; break
                if elapsed >= request["timeout_seconds"]:
                    execution["outcome"] = "TIMEOUT"; break
                if overflow.is_set():
                    execution["outcome"] = "OUTPUT_LIMIT"; break
                if process.poll() is not None and not any(t.is_alive() for t in threads):
                    execution["outcome"] = "EXITED"; break
                self._cancel.wait(0.01)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            execution["outcome"] = "LAUNCH_FAILED"
            if process is None:
                execution.update(reaped=None, cleanup_complete=None)
            result = _abstain(request, "Owned launch unavailable: " + str(exc), "UNAVAILABLE")
        finally:
            if job is not None:
                try:
                    job.stop()
                except OSError:
                    execution["cleanup_complete"] = False
            if process is not None:
                try:
                    execution["returncode"] = process.wait(timeout=2)
                    execution["reaped"] = True
                except subprocess.TimeoutExpired:
                    execution["cleanup_complete"] = False
                for thread in threads:
                    thread.join(timeout=0.5)
                execution["cleanup_complete"] &= not any(t.is_alive() for t in threads)
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None and not pipe.closed and execution["cleanup_complete"]:
                        pipe.close()
            if job is not None:
                try:
                    job.close()
                except OSError:
                    execution["cleanup_complete"] = False
        outcome = execution["outcome"]
        if outcome != "LAUNCH_FAILED":
            if not execution["cleanup_complete"] or not execution["reaped"]:
                result = _abstain(request, "Worker cleanup not established", "INVALID")
            elif outcome == "CANCELLED" or self._cancel.is_set():
                result = _abstain(request, "Worker cancelled; result discarded", "PROVIDER_ABSTAINED")
            elif outcome == "STALE" or self._stale():
                result = _abstain(request, "Request became stale; late result discarded", "STALE")
            elif outcome == "TIMEOUT" or time.monotonic() - started >= request["timeout_seconds"]:
                result = _abstain(request, "Worker deadline exhausted; late result discarded", "TIMEOUT")
            elif outcome == "OUTPUT_LIMIT":
                result = _abstain(request, "Worker exceeded fixed output byte limit", "INVALID")
            elif execution["returncode"] != 0 or stream_error:
                result = _abstain(request, "Worker failed; no retry or partial prediction accepted", "INVALID")
            else:
                try:
                    result = _response(request, _load(bytes(captured["stdout"]).decode("utf-8")))
                except (ValueError, RecursionError) as exc:
                    result = _abstain(request, "Invalid worker output: " + str(exc), "INVALID")
        result["costs"] = {"wall_seconds": time.monotonic() - started, "cpu_seconds": None, "status": "UNKNOWN"}
        return {"prediction": result, "execution": execution}


def _child(adapter):
    require(adapter in ADAPTERS, "Unregistered child adapter")
    if adapter == "fixture_no_read":
        time.sleep(2)
        return 0
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    require(len(raw) <= MAX_INPUT_BYTES, "Child input byte limit")
    request = _request(_load(raw.decode("utf-8")))
    if adapter in ("fixture_sleep", "fixture_late_failure"):
        time.sleep(2)
    result = _abstain(request, "No installed prediction model; trusted CPU adapter unavailable", "UNAVAILABLE")
    if adapter == "code_diff_linear":
        from rds_code_diff import score_candidate_diff
        base_code = request["context"].get("base_code")
        if not isinstance(base_code, str) or not base_code.strip():
            result = _abstain(request, "code_diff_linear requires context.base_code", "PROVIDER_ABSTAINED")
        else:
            forecasts = []
            for cand in request["candidates"]:
                cand_code = cand["parameters"].get("code")
                if not isinstance(cand_code, str) or not cand_code.strip():
                    result = _abstain(request, "code_diff_linear requires candidate parameters.code", "PROVIDER_ABSTAINED")
                    forecasts = []
                    break
                scored = score_candidate_diff(base_code, cand_code)
                forecasts.append({
                    "candidate_id": cand["id"],
                    "score": scored["score"],
                    "uncertainty": scored["uncertainty"],
                })
            if forecasts:
                result.update(
                    status="SCORED",
                    reason="CPU_STRUCTURAL_CODE_DIFF shadow score; never selects or grants scientific truth",
                    reason_code="NONE",
                    forecasts=forecasts,
                )
    elif adapter == "cpu_jepa":
        from rds_cpu_jepa import predict_transition
        base_code = request["context"].get("base_code")
        if not isinstance(base_code, str) or not base_code.strip():
            result = _abstain(request, "cpu_jepa requires context.base_code", "PROVIDER_ABSTAINED")
        else:
            before_state = request["context"].get("before_state") or {}
            forecasts = []
            for cand in request["candidates"]:
                cand_code = cand["parameters"].get("code")
                if not isinstance(cand_code, str) or not cand_code.strip():
                    result = _abstain(request, "cpu_jepa requires candidate parameters.code", "PROVIDER_ABSTAINED")
                    forecasts = []
                    break
                pred = predict_transition(before_state, base_code, cand_code, cand["parameters"])
                forecasts.append({
                    "candidate_id": cand["id"],
                    "score": pred["score"],
                    "uncertainty": pred["uncertainty"],
                })
            if forecasts:
                result.update(
                    status="SCORED",
                    reason="CPU_JEPA epistemic latent transition forecast; never selects or grants scientific truth",
                    reason_code="NONE",
                    forecasts=forecasts,
                )
    elif adapter == "bayes_blr":
        from rds_bayes_scorer import score_request
        try:
            forecasts = score_request(request)
            result.update(status="SCORED", reason="Frozen BLR posterior preferences; shadow only",
                          reason_code="NONE", forecasts=forecasts)
        except (ValueError, KeyError, TypeError, IndexError, ArithmeticError) as exc:
            result = _abstain(request, "bayes_blr: " + str(exc), "PROVIDER_ABSTAINED")
    elif adapter in ("fixture_score", "fixture_sleep", "fixture_failure", "fixture_late_failure", "fixture_output_then_sleep"):
        result.update(status="SCORED", reason="FIXTURE_ONLY synthetic scores; not an installed predictor",
                      reason_code="NONE", forecasts=[{"candidate_id": c["id"], "score": i,
                                                      "uncertainty": None} for i, c in enumerate(request["candidates"])])
    if adapter in ("fixture_overflow", "fixture_stderr"):
        pipe = sys.stderr.buffer if adapter == "fixture_stderr" else sys.stdout.buffer
        pipe.write(b"x" * (MAX_OUTPUT_BYTES + 8192)); pipe.flush()
    elif adapter == "fixture_invalid":
        sys.stdout.buffer.write(b'{"selection_effect":"NONE","selection_effect":"EXECUTE"}')
    else:
        sys.stdout.buffer.write(json.dumps(result, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    sys.stdout.buffer.flush()
    if adapter == "fixture_output_then_sleep":
        time.sleep(2)
    return 7 if adapter in ("fixture_failure", "fixture_late_failure") else 0


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--child" or sys.argv[2] not in ADAPTERS:
        raise SystemExit(2)
    try:
        raise SystemExit(_child(sys.argv[2]))
    except (ValueError, RecursionError):
        raise SystemExit(2)

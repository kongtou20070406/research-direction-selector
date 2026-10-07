"""One-shot, opt-in exact affine proof-chain scale profile.

python -B benchmark/affine_scale.py --dimension 64 --dimension 128

This is a bounded software-path profile, not a neural-network or GPU benchmark.
The dense rank-one input has small exact coefficients to isolate scaling behavior.
"""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import platform
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_affine_toolchain as affine
import rds_lean_verify as lean
import rds_verify as engine


def timed(operation):
    started = time.perf_counter()
    value = operation()
    return value, time.perf_counter() - started


def traced(operation):
    tracemalloc.start()
    try:
        value, elapsed = timed(operation)
        _current, peak = tracemalloc.get_traced_memory()
        return value, elapsed, peak
    finally:
        tracemalloc.stop()


def make_spec(dimension):
    coefficient = str(Fraction(1, 8 * dimension))
    bias = [str(Fraction(index + 1, dimension + 1)) for index in range(dimension)]
    return {"schema": 1, "kind": affine.KIND,
            "model": {"matrix": [[coefficient] * dimension for _ in range(dimension)],
                      "bias": bias}}


def profile(dimension):
    spec = make_spec(dimension)
    parsed, parse_seconds = timed(lambda: affine._read_affine_spec(spec))
    matrix, bias, source_models = parsed
    candidate, solve_seconds = timed(lambda: affine._solve(matrix, bias))
    if candidate is None:
        raise RuntimeError("The bounded rational solver found no witness")
    bias_sum = sum((Fraction(value) for value in spec["model"]["bias"]), Fraction(0))
    expected = [Fraction(value) + bias_sum / (7 * dimension)
                for value in spec["model"]["bias"]]
    if candidate != expected:
        raise RuntimeError("Candidate disagrees with the independent rank-one solution")

    plan, plan_seconds = timed(
        lambda: affine._proof_plan(matrix, bias, candidate, source_models))
    native_samples = []
    original_native_check = lean._native_check

    def timed_native_check(source, timeout_seconds=None):
        started = time.perf_counter()
        try:
            return original_native_check(source, timeout_seconds=timeout_seconds)
        finally:
            native_samples.append(time.perf_counter() - started)

    lean._native_check = timed_native_check
    try:
        generated, generate_seconds = timed(
            lambda: engine.execute_proof_plan(plan))
        if generated.get("status") != "PASS":
            raise RuntimeError("Proof generation was inconclusive: " + str(generated.get("reason")))
        generated_lean_seconds = sum(native_samples)
        generated_certificate_bytes = len(engine.canonical(generated["certificate"]).encode("utf-8"))

        native_samples.clear()
        replayed, replay_seconds = timed(
            lambda: engine.replay_proof_plan(plan, generated["certificate"]))
        replay_lean_seconds = sum(native_samples)
        if not replayed:
            raise RuntimeError("Independent proof-plan replay failed")

        native_samples.clear()
        result, end_to_end_seconds = timed(lambda: engine.verify(spec))
        end_to_end_lean_seconds = sum(native_samples)
        if result.get("status") != "PASS":
            raise RuntimeError("End-to-end verification was inconclusive: " + str(result.get("reason")))
        result_certificate_bytes = len(engine.canonical(result["certificate"]).encode("utf-8"))

        native_samples.clear()
        memory_result, memory_profile_seconds, end_to_end_python_peak = traced(
            lambda: engine.verify(spec))
        if memory_result.get("status") != "PASS":
            raise RuntimeError("Memory-profile replay was inconclusive: " + str(memory_result.get("reason")))
        memory_profile_lean_seconds = sum(native_samples)
    finally:
        lean._native_check = original_native_check

    return {"dimension": dimension, "shape": "dense_rank_one_exact_rational",
            "status": result["status"], "assurance": result["assurance"],
            "parse_seconds": round(parse_seconds, 4),
            "solve_seconds": round(solve_seconds, 4),
            "plan_generation_seconds": round(plan_seconds, 4),
            "proof_generation_seconds": round(generate_seconds, 4),
            "proof_generation_native_lean_seconds": round(generated_lean_seconds, 4),
            "proof_replay_seconds": round(replay_seconds, 4),
            "proof_replay_native_lean_seconds": round(replay_lean_seconds, 4),
            "end_to_end_seconds": round(end_to_end_seconds, 4),
            "end_to_end_python_peak_bytes": end_to_end_python_peak,
            "end_to_end_native_lean_seconds": round(end_to_end_lean_seconds, 4),
            "memory_profile_repeat_seconds": round(memory_profile_seconds, 4),
            "memory_profile_native_lean_seconds": round(memory_profile_lean_seconds, 4),
            "proof_plan_certificate_bytes": generated_certificate_bytes,
            "end_to_end_certificate_bytes": result_certificate_bytes,
            "lean_memory_cap_mib": 512,
            "memory_note": "Python peak is measured in a separate traced run and excludes the native Lean child; Lean is capped at 512 MiB."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dimension", type=int, action="append", dest="dimensions",
                        help="Profile 64, 128 or 256 dimensions; repeat to select cases.")
    args = parser.parse_args()
    dimensions = args.dimensions or [64, 128]
    if len(dimensions) > 2 or any(value not in {64, 128} for value in dimensions):
        parser.error("select one or both bounded dimensions from 64 and 128")
    result = {"environment": {"python": sys.version,
                               "platform": platform.platform(),
                               "lean": lean._executable()[1]},
              "cases": [profile(dimension) for dimension in dimensions],
              "limitations": [
                  "Single exact dense rank-one family with small rational coefficients; no sparse storage adapter.",
                  "Python memory uses tracemalloc and excludes Lean child memory; Lean's configured cap is reported separately.",
                  "The results do not establish performance for arbitrary rational systems, neural networks, GPU workloads, or application behavior.",
                  "A dense 256D profile did not complete within the 90-second local observation window and is outside the supported ceiling.",
                  "This opt-in profile is not part of default CI; each native vector proof has a 30-second limit.",
              ]}
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

"""Sequential real-model Bare/RDS feasibility experiment; no production wiring.

Transcript files are audit outputs, not an execution ledger. RDS execution uses
the original CLI/ProjectStore/CAS path. Paid requests are never auto-retried.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def execute(root, arm, cli, code, attempt):
    code_path, out = root / f"analysis-{attempt}.py", f"out/result-{attempt}.json"
    code_path.write_text(code, encoding="utf-8")
    argv = [sys.executable, "-B", "analysis_worker.py", code_path.name, out]
    if arm == "RDS":
        argv = [sys.executable, "-B", str(cli), "--root", str(root), "exec", "--name", f"analysis-{attempt}",
                "--timeout", "30", "--output", out, "--bind", "code=" + code_path.name,
                "--json", *[v for p in sorted(root.glob("*.csv")) for v in ("--bind", "data=" + p.name)], "--", *argv]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "RDS_USAGE_DB": str(root / ".rds/usage/cli.sqlite3")}
    started = time.monotonic()
    try:
        result = subprocess.run(argv, cwd=root, env=env, capture_output=True, timeout=50 if arm == "RDS" else 30)
        report = {"status": "FINISHED", "exit_code": result.returncode,
                  "stdout": result.stdout.decode("utf-8", errors="replace"),
                  "stderr": result.stderr.decode("utf-8", errors="replace")}
    except subprocess.TimeoutExpired as exc:
        # Original RDS runner owns settlement; outer timeout is UNKNOWN and no retry.
        report = {"status": "TIMEOUT_UNKNOWN", "exit_code": None,
                  "stdout": (exc.stdout or b"").decode("utf-8", errors="replace"),
                  "stderr": (exc.stderr or b"").decode("utf-8", errors="replace")}
    report.update(attempt=attempt, argv=argv, code_sha256=sha(code_path),
                  wall_seconds=time.monotonic() - started, cost_status="UNKNOWN")
    if (root / out).is_file():
        report["artifact"] = json.loads((root / out).read_text(encoding="utf-8"))
        report["artifact_sha256"] = sha(root / out)
    save(root / f"tool-{attempt}.json", report)
    return report


def run(args):
    root, cli = Path(args.workspace).resolve(), Path(args.cli).resolve()
    if root.exists():
        raise ValueError("Use a new experiment root; never overwrite prior attempts")
    root.mkdir(parents=True)
    source = Path(args.inputs).resolve()
    inputs = {p.name: sha(p) for p in sorted(source.glob("*.csv"))}
    if not inputs:
        raise ValueError("No declared CSV inputs")
    task = (source / "task.json").read_text(encoding="utf-8")
    frozen = {"model": args.model, "model_weights": "UNKNOWN", "inputs": inputs,
              "task_sha256": sha(source / "task.json"), "cli_sha256": sha(cli),
              "max_calls": 40, "total_wall_seconds": 2700, "per_arm_calls": 20,
              "arms": ["Bare", "RDS"], "tool_timeout_seconds": 30,
              "comparison_scope": "RDS_EXEC_ONLY", "scientific_benefit": "UNKNOWN"}
    save(root / "frozen.json", frozen)
    started, calls, reports = time.monotonic(), 0, []
    for arm in frozen["arms"]:
        folder = root / arm
        folder.mkdir()
        for name in inputs:
            shutil.copyfile(source / name, folder / name)
        shutil.copyfile(Path(__file__).with_name("analysis_worker.py"), folder / "analysis_worker.py")
        instruction = ("You are a data analysis research agent. Solve the given real task with measured CSV evidence. "
            "Respond with ONE JSON object and no fences: {tool:read,file:name}, {tool:execute,code:python}, "
            "or {tool:finish,answer:text}. The execute tool provides csv,json,math,statistics,read(name),emit(dict) "
            "and ordinary numeric/list builtins. No imports. Emit exactly one evidence JSON object. "
            "Code has a 30s wall limit; 20 model calls available. Keep failures and revise methods. "
            "Do not fabricate numerical findings. Files: " + json.dumps(inputs) + ". Task: " + task)
        if arm == "RDS":
            instruction += (" Your execute tool is the actual frozen RDS exec route: code/data binding, "
                "bounded admitted execution and retained operational receipts. Read its failure/UNKNOWN evidence; "
                "operational success is not scientific support. No automatic uncertain retry.")
        messages = [{"role": "system", "content": instruction}]
        attempts, reason, final = 0, "CALL_CAP", None
        for turn in range(20):
            remaining = 2700 - (time.monotonic() - started)
            if remaining <= 0 or calls >= 40:
                reason = "TOTAL_CAP"
                break
            calls += 1
            request = {"model": args.model, "messages": messages, "max_tokens": 3000}
            save(folder / f"request-{turn}.json", request)
            began = time.monotonic()
            try:
                req = urllib.request.Request(args.endpoint.rstrip("/") + "/chat/completions",
                    data=json.dumps(request).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer magpie"})
                with urllib.request.urlopen(req, timeout=min(120, remaining)) as response:
                    raw = response.read()
                (folder / f"response-{turn}.json").write_bytes(raw)
                response = json.loads(raw)
                content = response["choices"][0]["message"]["content"]
                messages.append({"role": "assistant", "content": content})
                command = json.loads(content.strip().removeprefix("```json").removesuffix("```").strip())
            except Exception as exc:
                save(folder / f"failure-{turn}.json", {"type": type(exc).__name__, "error": str(exc),
                    "call": calls, "wall_seconds": time.monotonic() - began, "paid_cost": "UNKNOWN"})
                reason = "REQUEST_OR_PARSE_FAILURE_NO_RETRY"
                break
            tool = command.get("tool")
            if tool == "finish":
                final, reason = command.get("answer"), "AGENT_FINISHED"
                break
            try:
                if tool == "read":
                    name = command["file"]
                    if name not in inputs:
                        raise ValueError("Undeclared input")
                    result = (folder / name).read_text(encoding="utf-8-sig")[:12000]
                elif tool == "execute":
                    attempts += 1
                    result = execute(folder, arm, cli, command["code"], attempts)
                    if result["status"] == "TIMEOUT_UNKNOWN":
                        reason = "UNCERTAIN_TOOL_NO_RETRY"
                        break
                else:
                    raise ValueError("Unsupported tool")
            except (ValueError, KeyError) as exc:
                result = {"tool_error": str(exc)}
            messages.append({"role": "user", "content": json.dumps(result, ensure_ascii=False)[:16000]})
        report = {"arm": arm, "stop_reason": reason, "final_answer": final, "attempts": attempts,
                  "calls": turn + 1, "scientific_benefit": "UNKNOWN"}
        save(folder / "summary.json", report)
        reports.append(report)
        save(root / "summary.json", {"calls": calls, "wall_seconds": time.monotonic() - started, "arms": reports})
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("workspace", "inputs", "cli"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--model", default="group/deepseek-v4.1flash")
    parser.add_argument("--endpoint", default="http://127.0.0.1:3425/v1")
    print(json.dumps(run(parser.parse_args()), ensure_ascii=False, indent=2))

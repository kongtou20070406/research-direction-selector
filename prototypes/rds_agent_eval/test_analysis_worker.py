import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("analysis_worker", Path(__file__).with_name("analysis_worker.py"))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def test_numeric_measurement_and_declared_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data.csv").write_text("x\n1\n3\n", encoding="utf-8")
    (tmp_path / "analysis.py").write_text(
        "rows=list(csv.DictReader(read('data.csv').splitlines()))\n"
        "emit({'mean':statistics.mean([float(r['x']) for r in rows])})", encoding="utf-8")
    worker.run("analysis.py", "out/result.json")
    assert worker.json.loads((tmp_path / "out/result.json").read_text())["mean"] == 2


def test_forbidden_execution_and_escaping_inputs(tmp_path, monkeypatch):
    for code in ("import os", "open('x')", "csv.sys", "csv.__dict__"):
        with pytest.raises((ValueError, NameError)):
            exec(compile(worker.validate(code), "test", "exec"), {"__builtins__": worker.BUILTINS})
    monkeypatch.chdir(tmp_path)
    (tmp_path / "analysis.py").write_text("emit({'x':read('../secret.csv')})", encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        worker.run("analysis.py", "out/result.json")


def test_repeated_analysis_returns_original_attempt_without_execution(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("agent_loop", Path(__file__).with_name("loop.py"))
    loop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loop)
    code = "emit({'value':1})"
    loop.save(tmp_path / "tool-1.json", {"code_sha256": loop.hashlib.sha256(code.encode()).hexdigest(),
                                       "status": "TIMEOUT_UNKNOWN"})
    def forbidden(*a, **kw):
        raise AssertionError("Uncertain analysis was bought again")
    monkeypatch.setattr(loop.subprocess, "run", forbidden)
    report = loop.execute(tmp_path, "RDS", tmp_path / "cli.py", code, 2)
    assert report["status"] == "TIMEOUT_UNKNOWN" and report["reused_original_attempt"]


def test_bound_streams_preserve_error_and_reject_tampering(tmp_path):
    spec = importlib.util.spec_from_file_location("feedback_loop", Path(__file__).with_name("loop.py"))
    loop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loop)
    items = []
    for name, content in (("stdout.bin", b""), ("stderr.bin", b"original failure")):
        path = tmp_path / name
        path.write_bytes(content)
        items.append({"kind": name, "path": name, "size": len(content), "sha256": loop.sha(path)})
    receipt = {"artifacts": items}
    assert loop.bound_artifacts(tmp_path, receipt, "out/result.json")["stderr.bin"] == b"original failure"
    (tmp_path / "stderr.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="tampered"):
        loop.bound_artifacts(tmp_path, receipt, "out/result.json")


def test_native_multi_call_batch_preserves_ids_and_order():
    spec = importlib.util.spec_from_file_location("batch_loop", Path(__file__).with_name("loop.py"))
    loop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loop)
    calls = [{"id": str(i), "function": {"name": "read", "arguments": '{"file":"data.csv"}'}} for i in range(2)]
    assert [key for key, cmd in loop.commands({"tool_calls": calls})] == ["0", "1"]
    with pytest.raises(ValueError, match="distinct"):
        loop.commands({"tool_calls": [calls[0], calls[0]]})


def test_whitelisted_csv_import_and_column_lookup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data.csv").write_text("x\n1\n3\n", encoding="utf-8")
    (tmp_path / "analysis.py").write_text("import csv\nrows=list(csv.DictReader(read('data.csv').splitlines()))\n"
        "emit({'max':max(float(r['x']) for r in rows)})", encoding="utf-8")
    worker.run("analysis.py", "out/result.json")
    assert worker.json.loads((tmp_path / "out/result.json").read_text())["max"] == 3


def test_broken_post_execution_receipt_cannot_rebuy_attempt(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("retained_loop", Path(__file__).with_name("loop.py"))
    loop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loop)
    calls = []
    def completed(*args, **kwargs):
        calls.append(args)
        return loop.subprocess.CompletedProcess([], 0, stdout=b"invalid receipt", stderr=b"")
    monkeypatch.setattr(loop.subprocess, "run", completed)
    code = "emit({'x':1})"
    with pytest.raises(ValueError):
        loop.execute(tmp_path, "RDS", tmp_path / "cli.py", code, 1)
    retained = loop.execute(tmp_path, "RDS", tmp_path / "cli.py", code, 2)
    assert len(calls) == 1 and retained["reused_original_attempt"]
    assert retained["feedback_assurance"] == "UNVERIFIED"

import importlib.util
import io
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


def terminal_loop():
    spec = importlib.util.spec_from_file_location("terminal_loop", Path(__file__).with_name("loop.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_complete_text_and_function_answers_preserve_native_content():
    loop = terminal_loop()
    answer = "证据与限制\nOriginal measured answer."
    assert loop.response_turn({"finish_reason": "stop", "message": {"content": answer}}) == ([], answer)
    call = {"id": "finish-1", "function": {"name": "finish", "arguments": loop.json.dumps({"answer": answer})}}
    batch, text = loop.response_turn({"finish_reason": "tool_calls", "message": {"tool_calls": [call]}})
    assert text is None and batch == [("finish-1", {"tool": "finish", "answer": answer})]


def test_truncated_unknown_empty_or_contradictory_terminal_never_dispatches():
    loop = terminal_loop()
    call = {"id": "execute-1", "function": {"name": "execute", "arguments": '{"code":"emit({})"}'}}
    for reason in ("length", None, "content_filter", "stop"):
        with pytest.raises(ValueError, match="finish_reason"):
            loop.response_turn({"finish_reason": reason, "message": {"tool_calls": [call], "content": "partial"}})
    with pytest.raises(ValueError, match="empty"):
        loop.response_turn({"finish_reason": "stop", "message": {"content": "  "}})
    call["function"] = {"name": "finish", "arguments": '{}'}
    with pytest.raises(ValueError, match="empty"):
        loop.response_turn({"finish_reason": "tool_calls", "message": {"tool_calls": [call]}})


def test_driver_retains_terminal_response_and_charged_request_without_dispatch(tmp_path, monkeypatch):
    loop = terminal_loop()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "data.csv").write_text("x\n1\n", encoding="utf-8")
    (inputs / "task.json").write_text('{"query":"fixture only"}', encoding="utf-8")
    cli = tmp_path / "cli.py"
    cli.write_text("# no execution", encoding="utf-8")
    monkeypatch.setattr(loop, "execute", lambda *a: pytest.fail("Terminal response dispatched execution"))
    for mode in ("TEXT", "FUNCTION", "TRUNCATED"):
        choice = {"finish_reason": "stop", "message": {"content": "unverified fixture answer"}}
        if mode != "TEXT":
            choice = {"finish_reason": "tool_calls" if mode == "FUNCTION" else "length", "message": {
                "tool_calls": [{"id": "f1", "function": {"name": "finish", "arguments": '{"answer":"fixture"}'}}]}}
        raw = loop.json.dumps({"model": "fixture-deepseek", "choices": [choice]}).encode()
        monkeypatch.setattr(loop.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(raw))
        args = type("Args", (), dict(workspace=str(tmp_path / mode), inputs=str(inputs), cli=str(cli),
            max_calls=2, deadline=loop.time.time()+120, model="fixture-deepseek", response_model="fixture-deepseek",
            endpoint="http://fixture.invalid/v1"))()
        reports = loop.run(args)
        frozen = loop.json.loads((Path(args.workspace) / "frozen.json").read_text())
        assert frozen["completion_protocol"] == "finish-function-or-stop-text-v2"
        for report in reports:
            folder = Path(args.workspace) / report["arm"]
            assert report["calls"] == 1 and report["attempts"] == 0
            assert len(list(folder.glob("request-*.json"))) == 1 and (folder / "response-0.json").read_bytes() == raw
            assert report["scientific_benefit"] == "UNKNOWN"
            assert report["completion_mode"] == (None if mode == "TRUNCATED" else mode)
            assert report["stop_reason"] == ("REQUEST_OR_PARSE_FAILURE_NO_RETRY" if mode == "TRUNCATED" else "AGENT_FINISHED")

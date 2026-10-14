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

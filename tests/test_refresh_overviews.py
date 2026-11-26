import importlib.util
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "research-gate" / "scripts" / "refresh_overviews.py"
spec = importlib.util.spec_from_file_location("refresh_overviews", SCRIPT)
ro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ro)

OLD = "# P\n\n" + "旧内容。" * 80


def vault(tmp_path):
    v = tmp_path / "vault"
    (v / "知识库" / "_数据").mkdir(parents=True)
    for lib in ("A成果库", "G成果库"):
        (v / lib).mkdir()
    (v / "A成果库" / ro.OVERVIEW).write_text(OLD, encoding="utf-8")
    (v / "A成果库" / "keep.md").write_text("x", encoding="utf-8")
    (v / "G成果库" / ro.OVERVIEW).write_text("---\ngenerated_by: x\n---\n", encoding="utf-8")
    (v / "知识库" / "_数据" / "项目.json").write_text(json.dumps({
        "A": {"library": "A成果库", "sources": [str(tmp_path)], "constraints": ["不提 ImageNet 或 ImageNet-100 实验（已否决）"]},
        "G": {"library": "G成果库", "sources": [str(tmp_path)]},
        "N": {"library": "N成果库"},
    }, ensure_ascii=False), encoding="utf-8")
    return v


def writer(text, extra=None):
    def run(prompt, cwd, report, timeout):
        (cwd / ro.OVERVIEW).write_text(text, encoding="utf-8")
        if extra:
            (cwd / extra).write_text("y", encoding="utf-8")
        report.write_text("done", encoding="utf-8")
        return 0
    return run


def test_refresh_ok_and_skips(tmp_path):
    v = vault(tmp_path)
    new = "# P\n\n" + "新内容。" * 80
    res = {r["project"]: r for r in ro.refresh(v, runner=writer(new), today="2026-11-30")}
    assert res["A"]["state"] == "refreshed" and res["G"]["state"] == "skipped" and res["N"]["state"] == "skipped"
    assert (v / "A成果库" / ro.OVERVIEW).read_text(encoding="utf-8") == new
    assert (v / "A成果库" / "99-归档" / "总览备份" / "2026-11-30.md").read_text(encoding="utf-8") == OLD
    assert len((v / "知识库" / "_数据" / "总览刷新记录.jsonl").read_text(encoding="utf-8").splitlines()) == 3


def test_violations_restore_the_backup(tmp_path):
    for bad, extra, needle in (("# P\n" + "用 ImageNet-100 做对照。" * 30, None, "ImageNet"),
                               ("# P\n" + "完美的结果。" * 40, None, "禁用词"),
                               ("# P\n" + "正常。" * 80, "keep.md", "总览以外"),
                               ("短", None, "过短")):
        v = vault(tmp_path / needle)
        r = ro.refresh(v, only="A", runner=writer(bad, extra), today="2026-11-30")[0]
        assert r["state"] == "restored" and any(needle in p for p in r["problems"]), (needle, r)
        assert (v / "A成果库" / ro.OVERVIEW).read_text(encoding="utf-8") == OLD


def test_forbidden_terms_parsing():
    assert ro.forbidden_terms(["不提 ImageNet 或 ImageNet-100 实验（研究者已否决）"]) == ["ImageNet", "ImageNet-100"]
    assert ro.forbidden_terms(["只准备 ICML 2027，不写 IJCAI 或其他投稿目标"]) == ["IJCAI", "其他投稿目标"]


def test_dry_run_writes_nothing(tmp_path):
    v = vault(tmp_path)
    r = ro.refresh(v, only="A", dry_run=True, runner=None)[0]
    assert r["state"] == "dry-run" and not (v / "知识库" / "_数据" / "总览刷新记录.jsonl").exists()

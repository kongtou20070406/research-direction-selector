import importlib.util
import json
import os
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "skills/research-gate/scripts/command_center.py"
SPEC = importlib.util.spec_from_file_location("command_center", SCRIPT)
center = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(center)


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_command_center_reads_producer_formats(tmp_path):
    vault = tmp_path / "vault"
    repo = tmp_path / "project"
    data = vault / "知识库/_数据"
    put(data / "项目.json", json.dumps({
        "proj-alpha": {"root": str(repo), "library": "proj-alpha成果库"},
        "proj-beta": {"root": "../proj-beta", "library": "proj-beta成果库"},
        "proj-gamma": {"library": "proj-gamma成果库"},
    }))
    put(data / "proj-alpha.jsonl", '{"status":"active"}\n{"status":"superseded"}\n{"status":"retracted"}\n')
    put(data / "跨项目.jsonl", '{"status":"active"}\n')
    put(data / "总览刷新记录.jsonl", '{"status":"active"}\n')
    put(repo / "STATUS.md", """- 进行中：手写旧状态
<!-- AUTO:STATUS START（自动生成） -->
| 卡片 | 日期 | 裁定 | 改动类型 |
|---|---|---|---|
| 052 | 2026-11-25 | PARTIAL | 新方法 |
| 054 | 2026-11-26 | FAIL | 新方法 |
- 进行中：056 进行中
- 主线连续未过线：3 张；下一张卡必须写“跳出复盘”
<!-- AUTO:STATUS END -->
""")
    put(repo / "ledger/COLAB_LIVE_BOARD.md", "Updated: `2026-11-26 10:52:37` | Active: `2`\n"
        "Colab 额度: 剩余 `794.14 CU` | 消耗速率 `3.57/hr` | 活跃实例 `2`\n")
    put(tmp_path / "proj-beta/ledger/COLAB_LIVE_BOARD.md", "Updated: `2026-11-25 10:52` | Active: `1`\n"
        "Colab 额度: 剩余 `123.45 CU` | 活跃实例 `1`\n")
    put(vault / "proj-alpha成果库/90-自动生成/夜间任务状态.md", """**结果：有失败，见下表**

| 步骤 | 状态 | 说明 |
|---|---|---|
| 知识库与 STATUS 同步 | ok | 已刷新 |
| 卫生检查 | failed | 检查失败 |
| 计划任务自动清理 | warning | 保留 |
""")
    put(vault / "proj-alpha成果库/90-自动生成/外部投稿合并建议.md",
        "共 2 份待处理。并入方法：检查证据。\n")
    doctor = put(tmp_path / "doctor.json", json.dumps({
        "ok": False, "checks": {"routes": {"status": "FAIL"}, "knowledge": {"status": "OK"}},
    }))
    for idx, (library, name) in enumerate((
        ("proj-alpha成果库", "Early_EN.pdf"), ("proj-alpha成果库", "MainPaper/MainPaper_EN.pdf"),
        ("proj-alpha成果库", "MainPaper/MainPaper_ZH.pdf"), ("proj-beta成果库", "Beta_Conf.pdf"),
        ("proj-alpha成果库", "_已并入大稿/Archived_EN.pdf"),
    )):
        pdf = put(vault / library / "论文草稿" / name, "%PDF")
        os.utime(pdf, (1_700_000_000 + idx, 1_700_000_000 + idx))
    put(vault / "proj-gamma成果库/00-总览（先看这页）.md", "# Home")

    assert center.main(["--vault", str(vault), "--today", "2026-11-26", "--doctor-json", str(doctor)]) == 0
    text = (vault / center.PAGE).read_text(encoding="utf-8")
    assert len(text.splitlines()) < 45
    assert "更新 2026-11-26 · 同步 有失败" in text
    assert "知识库 2 条有效记录" in text and "总览刷新记录" not in text
    assert "794.14 CU · 活跃实例 2" in text and "123.45" not in text
    assert "| proj-alpha | 054 FAIL · 056 进行中 |" in text
    assert "手写旧状态" not in text
    assert r"[[proj-alpha成果库/论文草稿/MainPaper/MainPaper_EN.pdf\|MainPaper_EN]]" in text
    assert "MainPaper_ZH.pdf" in text and "Beta_Conf.pdf" in text and "Early_EN.pdf" not in text
    assert "Archived_EN.pdf" not in text
    assert r"[[proj-gamma成果库/00-总览（先看这页）\|proj-gamma成果库]]" in text
    actions = text.split("## 今日待拍板")[1]
    assert actions.count("\n- ") == 3
    assert "2 份待处理协作投稿（PR）" in actions and "跳出复盘" in actions
    assert "卫生检查" in actions and "doctor/routes" in actions and "计划任务自动清理" not in actions


def test_optional_inputs_can_be_missing(tmp_path):
    vault = tmp_path / "vault"
    out = tmp_path / "elsewhere.md"
    assert center.main(["--vault", str(vault), "--out", str(out),
                        "--doctor-json", str(tmp_path / "missing.json")]) == 0
    text = out.read_text(encoding="utf-8")
    assert "同步 未记录" in text and "知识库 0 条有效记录" in text
    assert "| proj-beta | — | — | — |" in text
    assert "暂无已记录的待拍板事项" in text


def test_paper_debt_local_gpu_and_north_star_radar(tmp_path):
    vault = tmp_path / "vault"
    data = vault / "知识库/_数据"
    paper_dir = tmp_path / "extra_paper"
    put(paper_dir / "paper_gate_report.md",
        "# paper_gate  extra  2026-11-26 13:00\nerrors 1 | sorry 2 | warnings 0\n\n- placeholder  5 allowed placeholder(s) (TBD)\n")
    proj_dir = vault / "extra"
    put(proj_dir / "program.json", json.dumps({
        "schema": "research-program/v1",
        "program": "extra",
        "north_star": {"current": "N@1", "versions": [{"id": "N@1", "date": "2026-11-26", "source": "GOAL", "statement": "test"}]},
        "propositions": [
            {"id": "P1", "statement": "parent", "anchored_to": "N@1", "status": "open", "claims": ["C1"]},
            {"id": "P1.1", "statement": "leaf", "parent": "P1", "route": "R1", "anchored_to": "N@1", "status": "supported", "claims": ["C1"], "tractable": True},
        ],
        "claims": [{"id": "C1", "statement": "c1", "propositions": ["P1.1"], "route": "R1", "status": "supported", "strength": "observed", "evidence": ["x.md"]}],
        "routes": [{"id": "R1", "question": "q", "targets": ["P1"], "status": "active", "kill": {"falsifier": "f"}}],
        "papers": [],
        "contributors": [],
    }, ensure_ascii=False))
    put(proj_dir / "x.md", "ok\n")
    put(data / "项目.json", json.dumps({"extra": {"root": "extra", "library": "extra成果库", "paper_dirs": [str(paper_dir)]}}, ensure_ascii=False))

    rc = center.main(["--vault", str(vault), "--today", "2026-11-26", "--no-toast", "--wrap", "train_extra", "--",
                      sys.executable, "-c", "print('done')"])
    assert rc == 0
    text = (vault / center.PAGE).read_text(encoding="utf-8")
    assert "本地 GPU train_extra ok" in text
    assert "| extra | err 1 · sorry 2 · TBD 5 |" in text
    assert "北极星可往回合（extra）：P1" in text
    assert "北极星可切新论文/并入大稿（extra）：P1" in text

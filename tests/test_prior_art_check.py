import importlib.util
import sys
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "research-gate" / "scripts" / "prior_art_check.py"
spec = importlib.util.spec_from_file_location("prior_art_check", SCRIPT)
pac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pac)

GOOD = """# Card 048

## 先查（设计前写）

- **检索问题**：does fixed-point finetuning extrapolate?
- **本仓与跨项目资产**：proj-gamma FPF notes, lit/x.md
- **文献与最佳实践**：Smith et al. 2025; arXiv:2501.01234; https://example.org/paper
- **已知行不通的做法**：已查 3 处，未找到
- **本卡与上面的差别**：we drop the pool.

## 为什么
- x：
"""


def test_good_card_passes():
    assert pac.check(GOOD) == []


def test_missing_section():
    assert pac.check("# Card\n\n## 为什么\n") == ["missing prior-art section"]


def test_empty_bullet_and_too_few_refs():
    text = GOOD.replace("we drop the pool.", "").replace("Smith et al. 2025; arXiv:2501.01234; ", "")
    probs = pac.check(text)
    assert "empty bullet: 本卡与上面的差别" in probs
    assert any("need >= 3" in p for p in probs)


def test_empty_bullet_outside_section_is_ignored():
    assert pac.check(GOOD + "\n- 后面的空栏：\n") == []


def test_cli_exit_codes(tmp_path):
    good, bad = tmp_path / "g.md", tmp_path / "b.md"
    good.write_text(GOOD, encoding="utf-8")
    bad.write_text("# x\n", encoding="utf-8")
    assert pac.main([str(good)]) == 0
    assert pac.main([str(good), str(bad)]) == 1


def test_content_on_nested_lines_counts():
    text = GOOD.replace("- **文献与最佳实践**：Smith et al. 2025; arXiv:2501.01234; https://example.org/paper",
                        "- **文献与最佳实践**：\n  1. Smith et al. 2025\n  2. arXiv:2501.01234\n  3. https://example.org/paper")
    assert pac.check(text) == []
    assert pac.bullets("- **a**：\n  - x\n- **b**：\n")["b"] == ""

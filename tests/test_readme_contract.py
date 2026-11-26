"""Pin README funnel links, EN/ZH parity, companion links, and zero-leak checks in CI."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)#]+)(?:#[^)]*)?\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+", re.M)
FORBIDDEN = (
    "C:/Users/",
    "C:\\Users\\",
    "D:/Myfile",
    "D:\\Myfile",
    "E:/ALL IN AI",
    "空投",
    "Paper-AB",
    "loop-arc",
    "OrthoLoopWM",
    "gpt-6.2-",
    "Gemini 4 Hydrogen",
)


def _check_links(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for target in LINK_RE.findall(text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        resolved = (path.parent / target).resolve()
        assert resolved.exists(), f"{path.name}: broken relative link -> {target}"


def test_bilingual_readme_links_and_parity():
    en = ROOT / "README.md"
    zh = ROOT / "README.zh-CN.md"
    assert en.is_file() and zh.is_file()

    _check_links(en)
    _check_links(zh)

    en_text = en.read_text(encoding="utf-8")
    zh_text = zh.read_text(encoding="utf-8")

    assert "README.zh-CN.md" in en_text
    assert "README.md" in zh_text
    assert "https://github.com/kongtou20070406/proofline" in en_text
    assert "https://github.com/kongtou20070406/proofline" in zh_text
    assert "https://github.com/tommy0103/obelisk" in en_text
    assert "https://github.com/tommy0103/obelisk" in zh_text
    assert "legacy/v6" in en_text and "legacy/v6" in zh_text

    en_headings = HEADING_RE.findall(en_text)
    zh_headings = HEADING_RE.findall(zh_text)
    assert en_headings == zh_headings, f"Heading mismatch: {en_headings} vs {zh_headings}"


def test_public_docs_free_of_personal_leaks():
    for rel in ("README.md", "README.zh-CN.md", "CHARTER.md", "DEPLOY.md", "CONTRIBUTING.md", "AGENTS.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for token in FORBIDDEN:
            assert token not in text, f"{rel} contains private/personal token: {token}"

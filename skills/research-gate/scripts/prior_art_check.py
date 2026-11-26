"""Check that an experiment card searched prior art before it was designed (0 LLM tokens).

A card passes when it has a prior-art section (heading matched by ``--heading``) whose bullets are
all filled in, and that section cites at least ``--min-refs`` sources: a URL, an arXiv id, a DOI, or
an author-year citation such as "Smith et al. 2025". Writing "已查 N 处，未找到" / "searched N
places, none found" in a bullet counts as filled, not as a reference.

    python prior_art_check.py cards/048_x.md [more.md ...] [--min-refs 3] [--json]

Exit 0 when every card passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

DEFAULT_HEADING = r"^##\s*(先查|Prior art)"
REF_RES = (
    re.compile(r"https?://\S+"),
    re.compile(r"\barXiv:?\s*\d{4}\.\d{4,5}", re.I),
    re.compile(r"\b10\.\d{4,9}/\S+"),
    re.compile(r"\b[A-Z][A-Za-z\-]+(?: et al\.?| and [A-Z][A-Za-z\-]+)?,? \(?(?:19|20)\d{2}[a-z]?\)?"),
)
BULLET = re.compile(r"^\s*[-*]\s+(?:\*\*)?([^*：:]+?)(?:\*\*)?\s*[：:](.*)$")


def section(text: str, heading: str) -> str | None:
    lines = text.splitlines()
    head = re.compile(heading)
    for i, line in enumerate(lines):
        if head.search(line):
            body = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("## ") or nxt.startswith("# "):
                    break
                body.append(nxt)
            return "\n".join(body)
    return None


def bullets(body: str) -> dict[str, str]:
    """Top-level bullets of a section: name -> text after the colon plus any lines nested under it."""
    out: dict[str, str] = {}
    current = None
    for line in body.splitlines():
        m = BULLET.match(line) if not line[:1].isspace() else None
        if m:
            current = m.group(1).strip()
            out[current] = m.group(2)
        elif current is not None and line.strip() and not line.startswith("#"):
            if not line[:1].isspace() and line.lstrip()[:1] in "-*":
                current = None  # a top-level bullet without a colon ends the previous one
                continue
            out[current] += "\n" + line
    return out


def refs(text: str) -> list[str]:
    found: list[str] = []
    for rx in REF_RES:
        for m in rx.finditer(text):
            s = m.group(0).rstrip(").,;，。")
            if not any(s in f or f in s for f in found):
                found.append(s)
    return found


def check(text: str, heading: str = DEFAULT_HEADING, min_refs: int = 3) -> list[str]:
    body = section(text, heading)
    if body is None:
        return ["missing prior-art section"]
    problems = [f"empty bullet: {name}" for name, text in bullets(body).items() if not text.strip()]
    n = len(refs(body))
    if n < min_refs:
        problems.append(f"{n} reference(s) cited, need >= {min_refs}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cards", nargs="+", type=Path)
    ap.add_argument("--heading", default=DEFAULT_HEADING)
    ap.add_argument("--min-refs", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    result = {str(p): check(p.read_text(encoding="utf-8"), a.heading, a.min_refs) for p in a.cards}
    bad = {k: v for k, v in result.items() if v}
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for k, v in bad.items():
            print(f"{k}: " + "; ".join(v))
        print("PRIOR_ART " + ("PASS" if not bad else f"FAIL {len(bad)}/{len(result)}"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

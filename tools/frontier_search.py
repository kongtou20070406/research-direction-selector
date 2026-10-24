"""Retrieve one entry from the DL knowledge docs, so an agent never has to load
the whole file. Read-only, no third-party dependencies.

The docs are long on purpose (each entry carries its evidence). This tool makes
them usable in a limited context window:

    python -B tools/frontier_search.py --list
    python -B tools/frontier_search.py A4
    python -B tools/frontier_search.py --grep overfitting
    python -B tools/frontier_search.py --grep "weight decay" --max 6

Entry ids come from the docs themselves: `**A4. Title**` in
docs/neural-frontier-presets.md, and `## N. Title` in docs/dl-diagnosis-first.md
(reported as D1, D2, ...).
"""
import argparse
import re
import sys
from pathlib import Path

DOCS = {
    "presets": "docs/neural-frontier-presets.md",
    "diagnosis": "docs/dl-diagnosis-first.md",
}

PRESET_HEAD = re.compile(r"^\*\*([A-Z]\d+)\.\s*(.+?)\*\*", re.MULTILINE)
DIAGNOSIS_HEAD = re.compile(r"^##\s+(\d+)\.\s*(.+)$", re.MULTILINE)
ANY_HEAD = re.compile(r"^(?:\*\*([A-Z]\d+)\.\s*(.+?)\*\*|##\s+(\d+)\.\s*(.+))$",
                      re.MULTILINE)


def entries(root):
    """[(id, title, source_key, start, end)] across every configured doc."""
    found = []
    for key, rel in DOCS.items():
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        heads = [(m.start(), m.group(1), m.group(2)) for m in PRESET_HEAD.finditer(text) if m.group(1)]
        heads += [(m.start(), "D" + m.group(1), m.group(2)) for m in DIAGNOSIS_HEAD.finditer(text) if m.group(1)]
        heads.sort()
        for index, (start, ident, title) in enumerate(heads):
            end = heads[index + 1][0] if index + 1 < len(heads) else len(text)
            found.append((ident, title.strip(), key, start, end))
    return found


def body(text, start, end):
    chunk = text[start:end]
    # Drop the trailing horizontal rule and trailing blank lines.
    return chunk.rstrip().rstrip("-").rstrip()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("id", nargs="?", help="entry id, e.g. A4 or D2")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--list", action="store_true", help="ids and one-line titles only")
    parser.add_argument("--grep", default="", help="case-insensitive keyword search")
    parser.add_argument("--max", type=int, default=12, help="max hits for --grep")
    parser.add_argument("--full", action="store_true", help="with --grep, print bodies too")
    args = parser.parse_args()

    root = args.root.resolve()
    items = entries(root)
    if not items:
        parser.exit(2, "no entries found; are the docs present?\n")

    if args.list:
        for ident, title, key, _, _ in items:
            print(f"{ident:<3} [{key}] {title}")
        print(f"\n{len(items)} entries. Read the index in docs/neural-frontier-INDEX.md first.")
        return

    if args.grep:
        needle = args.grep.lower()
        hits = []
        for ident, title, key, start, end in items:
            text = (root / DOCS[key]).read_text(encoding="utf-8", errors="replace")
            chunk = text[start:end]
            if needle in chunk.lower():
                hits.append((ident, title, key, chunk))
        if not hits:
            print(f"no entry mentions {args.grep!r}. Try --list.")
            sys.exit(1)
        for ident, title, key, chunk in hits[:args.max]:
            print(f"=== {ident} [{key}] {title} ===")
            if args.full:
                print(chunk.rstrip())
            else:
                first = next((line for line in chunk.splitlines()[1:] if line.strip()), "")
                print(first.strip()[:300])
            print()
        if len(hits) > args.max:
            print(f"... {len(hits) - args.max} more; raise --max or narrow the term.")
        return

    if not args.id:
        parser.exit(2, "give an id, or use --list / --grep\n")
    wanted = args.id.strip().upper()
    for ident, title, key, start, end in items:
        if ident == wanted:
            text = (root / DOCS[key]).read_text(encoding="utf-8", errors="replace")
            print(body(text, start, end))
            return
    parser.exit(1, f"unknown id {args.id!r}; use --list\n")


if __name__ == "__main__":
    main()

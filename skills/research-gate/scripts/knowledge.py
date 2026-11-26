"""Knowledge registry: what a research project has learned, kept as reusable entries (0 LLM tokens).

The experiment ledger records what happened, run by run. The registry records what to reuse or avoid
next time, one entry per line of a JSONL file:

    {"id": "K-007", "kind": "dead_end", "statement": "...", "scope": "Push-T, RecurrentWM",
     "evidence": ["card/043"], "source": "proj-alpha", "date": "2026-11-24", "status": "active",
     "tags": ["training-budget"]}

Kinds:
  primitive  a mechanism that worked and should be reused (a single scalar, a loss term, a schedule)
  dead_end   something tried and ruled out, with the scope where it failed
  asset      code, data or a method from another project worth carrying over
  practice   an external best practice (a recipe, a protocol) with its source

Commands:
  check  FILE...                      schema, unique ids, evidence present
  search FILE... -q "query" [--kind]  ranked keyword match; use it before designing a card
  add    FILE --kind K --statement S --scope S --evidence E [E ...] [--source P] [--tags T ...]
         appends with the next id, skips an entry whose statement already exists, re-checks the file
  render FILE...                      Markdown grouped by kind (for a status page)

Search several files to reach other projects: a project registry (ids K-001...) plus a shared
cross-project one (pass ``--prefix X`` so its ids read X-001... and never collide).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

KINDS = ("primitive", "dead_end", "asset", "practice")
STATUSES = ("active", "superseded", "retracted")
REQUIRED = ("id", "kind", "statement", "scope", "evidence", "source", "date", "status")
ID_RE = re.compile(r"^[A-Z]{1,3}-\d{3,}$")
PLACEHOLDER_RE = re.compile(r"^[（(]?\s*(?:待结卡填|待填|待补|todo|tbd|\.{3,})\s*[）)]?$", re.IGNORECASE)
TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_\-.]*|\d+(?:\.\d+)?|[一-鿿]{2,}")
KIND_LABEL = {"primitive": "保留的原语", "dead_end": "排除的做法（禁区）", "asset": "跨项目资产", "practice": "外部最佳实践"}


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{n}: bad JSON ({e})")
    return out


def check(entries: list[dict]) -> list[str]:
    problems, seen = [], set()
    for n, e in enumerate(entries, 1):
        if not isinstance(e, dict):
            problems.append(f"entry {n}: expected a JSON object")
            continue
        eid = e.get("id", "?")
        for k in REQUIRED:
            if k not in e or e[k] in ("", None, []):
                problems.append(f"{eid}: missing {k}")
        if not isinstance(eid, str) or not ID_RE.match(eid):
            problems.append(f"{eid}: id must look like K-001 (a 1-3 letter prefix per registry)")
        elif eid in seen:
            problems.append(f"{eid}: duplicate id")
        else:
            seen.add(eid)
        if e.get("kind") not in KINDS:
            problems.append(f"{eid}: kind must be one of {KINDS}")
        if e.get("status") not in STATUSES:
            problems.append(f"{eid}: status must be one of {STATUSES}")
        if not isinstance(e.get("evidence"), list):
            problems.append(f"{eid}: evidence must be a list")
        if e.get("status") == "superseded" and not e.get("superseded_by"):
            problems.append(f"{eid}: superseded needs superseded_by")
        if e.get("status") == "active" and PLACEHOLDER_RE.match(str(e.get("statement", "")).strip()):
            problems.append(f"{eid}: placeholder statement not allowed on active entry")
    return problems


def _tokens(text: str) -> set[str]:
    toks = set()
    for t in TOKEN.findall(text.lower()):
        toks.add(t)
        if re.fullmatch(r"[一-鿿]+", t):  # Chinese: add bigrams so phrases match inside longer runs
            toks.update(t[i:i + 2] for i in range(len(t) - 1))
    return toks


def search(entries: list[dict], query: str, kind: str | None = None, limit: int = 10,
           include_inactive: bool = False) -> list[tuple[int, dict]]:
    q = _tokens(query)
    hits = []
    for e in entries:
        if kind and e.get("kind") != kind:
            continue
        if not include_inactive and e.get("status") != "active":
            continue
        hay = _tokens(" ".join([e.get("statement", ""), e.get("scope", ""), " ".join(e.get("tags", []))]))
        score = len(q & hay)
        if score:
            hits.append((score, e))
    hits.sort(key=lambda x: (-x[0], x[1].get("id", "")))
    return hits[:limit]


def next_id(entries: list[dict], prefix: str = "K") -> str:
    nums = [int(str(e["id"]).split("-")[1]) for e in entries if ID_RE.match(str(e.get("id", "")))]
    return f"{prefix}-{(max(nums) + 1 if nums else 1):03d}"


def add(path: Path, kind: str, statement: str, scope: str, evidence: list[str], source: str,
        tags: list[str] | None = None, date: str | None = None, prefix: str = "K") -> tuple[str, str]:
    """Append one entry. Returns (state, id): state is 'added' or 'exists'. Raises ValueError if invalid."""
    entries = load(path)
    norm = " ".join(statement.split())
    for e in entries:
        if " ".join(e.get("statement", "").split()) == norm:
            return "exists", e["id"]
    entry = {"id": next_id(entries, prefix), "kind": kind, "statement": norm, "scope": scope, "evidence": list(evidence),
             "source": source, "date": date or _dt.date.today().isoformat(), "status": "active",
             "tags": list(tags or [])}
    problems = check(entries + [entry])
    if problems:
        raise ValueError("; ".join(problems))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return "added", entry["id"]


def render(entries: list[dict]) -> str:
    lines = []
    for kind in KINDS:
        group = [e for e in entries if e.get("kind") == kind and e.get("status") == "active"]
        if not group:
            continue
        lines.append(f"### {KIND_LABEL[kind]}（{len(group)}）\n")
        lines.append("| 编号 | 内容 | 适用范围 | 证据 | 来源 |")
        lines.append("|---|---|---|---|---|")
        for e in group:
            cells = [e["id"], e["statement"], e["scope"], ", ".join(e["evidence"]), e["source"]]
            lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("files", nargs="+", type=Path)
    s = sub.add_parser("search"); s.add_argument("files", nargs="+", type=Path)
    s.add_argument("-q", "--query", required=True); s.add_argument("--kind", choices=KINDS)
    s.add_argument("--limit", type=int, default=10); s.add_argument("--all", action="store_true")
    a = sub.add_parser("add"); a.add_argument("file", type=Path)
    a.add_argument("--kind", required=True, choices=KINDS); a.add_argument("--statement", required=True)
    a.add_argument("--scope", required=True); a.add_argument("--evidence", nargs="+", required=True)
    a.add_argument("--source", default=""); a.add_argument("--prefix", default="K"); a.add_argument("--tags", nargs="*", default=[])
    r = sub.add_parser("render"); r.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "check":
        bad = 0
        for f in args.files:
            probs = check(load(f))
            for p in probs:
                print(f"{f}: {p}")
            bad += len(probs)
        print("KNOWLEDGE " + ("PASS" if not bad else f"FAIL {bad}"))
        return 1 if bad else 0
    if args.cmd == "search":
        entries = [e for f in args.files for e in load(f)]
        hits = search(entries, args.query, args.kind, args.limit, args.all)
        for score, e in hits:
            print(f"{e['id']} [{e['kind']}] ({e['source']}) {e['statement']} — 范围：{e['scope']}；证据：{', '.join(e['evidence'])}")
        print(f"KNOWLEDGE_SEARCH {len(hits)} hit(s)")
        return 0
    if args.cmd == "add":
        try:
            state, eid = add(args.file, args.kind, args.statement, args.scope, args.evidence,
                             args.source or args.file.resolve().parents[1].name, args.tags, prefix=args.prefix)
        except ValueError as e:
            print(f"KNOWLEDGE_ADD REFUSED {e}")
            return 1
        print(f"KNOWLEDGE_ADD {state} {eid}")
        return 0
    print(render([e for f in args.files for e in load(f)]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

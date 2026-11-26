"""Deterministic rebuttal QA (0 LLM tokens). Run before any reviewer reads the rebuttal.

Layout of a rebuttal folder (names configurable in ``rebuttal.json``):

    ledger.json        canonical revision ledger: one entry per reviewer comment
    reviews/*.txt|md   reviews as downloaded (quotes are checked against these)
    replies/*.md       one reply per reviewer, plus general response and AC note
    bindings.json      optional number bindings for the replies (same format as the manuscript)
    manifest.json      optional snapshot of the original paper source (``snapshot`` subcommand)
    rebuttal.json      optional config: char_limit, forbid, revised_dir, reviews_glob, replies_glob

Ledger entry fields: ``id`` (e.g. R1-C2), ``source``, ``quote`` (verbatim from a review),
``concern``, ``status`` (open | planned | drafted | done | blocked | accepted_limit),
``action`` {``kind``: edit | experiment | clarify | limitation | no_change, ``location``,
``rationale``}, ``evidence`` (list of ledger keys, run ids or paths).

Checks: ledger schema; quotes appear verbatim in a review; every ledger id is tagged ``[ID]``
in a reply and every tag exists; per-reply character limit; a promise sentence ("we will",
"in the revision", "我们将" ...) sits under a tag whose action is not ``no_change``; ``done``
actions point at a location that exists; experiment actions carry evidence; numbers in replies
are bound; forbidden terms are absent; the original source changed only where allowed.
Writes a QA JSON report. The ledger rules adapt CCFA-Skills ``ccf-rebuttal-writer`` (MIT).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

STATUSES = ("open", "planned", "drafted", "done", "blocked", "accepted_limit")
KINDS = ("edit", "experiment", "clarify", "limitation", "no_change")
DEFAULTS = {"char_limit": 5000, "forbid": [], "revised_dir": None,
            "reviews_glob": "reviews/*", "replies_glob": "replies/*.md"}
TAG_RE = re.compile(r"\[([A-Z]{1,3}\d*-C\d+)\]")
PROMISE_RE = re.compile(
    r"\b(we will|we'll|will (?:add|include|revise|clarify|report|move|expand|release|update)|"
    r"in the (?:revised|final|camera-ready) (?:version|paper|manuscript)|in the revision)\b|我们将|将在(?:修订|终稿)",
    re.IGNORECASE,
)
NUM_RE = re.compile(r"(?<![\w.\-])[+\-−]?\d+(?:\.\d+)?%|(?<![\w.\-])[+\-−]?\d+\.\d+")
_WS = re.compile(r"\s+")


def _load_formal():
    path = Path(__file__).resolve().parent / "verify_manuscript_formal.py"
    spec = importlib.util.spec_from_file_location("verify_manuscript_formal", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(source: Path, out: Path, allow: list[str]) -> dict:
    files = {p.relative_to(source).as_posix(): sha256(p) for p in sorted(source.rglob("*")) if p.is_file()}
    data = {"source_dir": str(source), "files": files, "allowed_changes": sorted(allow)}
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _strip_code(text: str) -> str:
    return re.sub(r"`[^`\n]*`", "", re.sub(r"```.*?```", "", text, flags=re.S))


def check_rebuttal(folder: Path, cfg: dict | None = None) -> tuple[list[str], dict]:
    conf_file = folder / "rebuttal.json"
    file_cfg = json.loads(conf_file.read_text(encoding="utf-8-sig")) if conf_file.is_file() else {}
    cfg = {**DEFAULTS, **file_cfg, **(cfg or {})}  # CLI > rebuttal.json > defaults
    errors: list[str] = []
    report: dict = {"char_limit": cfg["char_limit"], "reply_character_counts": {}}

    ledger_path = folder / "ledger.json"
    if not ledger_path.is_file():
        return [f"Missing {ledger_path}"], report
    ledger = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
    by_id: dict[str, dict] = {}
    for idx, e in enumerate(ledger, 1):
        eid = e.get("id")
        if not isinstance(eid, str) or not TAG_RE.fullmatch(f"[{eid}]"):
            errors.append(f"Ledger #{idx}: id {eid!r} must look like R1-C2 or AC-C1")
            continue
        if eid in by_id:
            errors.append(f"Ledger {eid}: duplicate id")
        by_id[eid] = e
        if e.get("status") not in STATUSES:
            errors.append(f"Ledger {eid}: status must be one of {list(STATUSES)}")
        action = e.get("action") or {}
        if action.get("kind") not in KINDS:
            errors.append(f"Ledger {eid}: action.kind must be one of {list(KINDS)}")
        if not str(e.get("quote", "")).strip():
            errors.append(f"Ledger {eid}: missing verbatim 'quote' from the review")
    report["ledger_entries"] = len(by_id)

    reviews = [p for p in sorted(folder.glob(cfg["reviews_glob"])) if p.is_file()]
    review_text = _norm("\n".join(p.read_text(encoding="utf-8-sig", errors="replace") for p in reviews))
    if not reviews:
        errors.append(f"No review files match {cfg['reviews_glob']}")
    else:
        for eid, e in by_id.items():
            q = _norm(str(e.get("quote", "")))
            if q and q not in review_text:
                errors.append(f"Ledger {eid}: quote is not verbatim in any review file")

    replies = [p for p in sorted(folder.glob(cfg["replies_glob"])) if p.is_file()]
    if not replies:
        errors.append(f"No reply files match {cfg['replies_glob']}")
    tagged: set[str] = set()
    forbid = [t for t in cfg["forbid"] if t]
    for reply in replies:
        text = reply.read_text(encoding="utf-8-sig")
        rel = reply.relative_to(folder).as_posix()
        n = len(text.strip())
        report["reply_character_counts"][rel] = n
        if n > cfg["char_limit"]:
            errors.append(f"{rel}: {n} characters exceeds the limit of {cfg['char_limit']}")
        tags = TAG_RE.findall(text)
        tagged.update(tags)
        for t in sorted(set(tags) - by_id.keys()):
            errors.append(f"{rel}: tag [{t}] has no ledger entry")
        current = None
        for para in re.split(r"\n\s*\n", text):
            para_tags = TAG_RE.findall(para)
            if para_tags:
                current = para_tags[-1]
            for sent in re.split(r"(?<=[.!?。！？])\s+", para):
                if PROMISE_RE.search(sent):
                    kind = ((by_id.get(current) or {}).get("action") or {}).get("kind")
                    if current is None or kind in (None, "no_change"):
                        errors.append(f"{rel}: promise without a ledger action: {sent.strip()[:90]!r}")
        lowered = text.lower()
        for term in forbid:
            if term.lower() in lowered:
                errors.append(f"{rel}: forbidden term {term!r}")
    for eid in sorted(by_id.keys() - tagged):
        errors.append(f"Ledger {eid}: no reply addresses it (tag [{eid}] missing)")

    revised = Path(cfg["revised_dir"]) if cfg["revised_dir"] else folder
    if not revised.is_absolute():
        revised = folder / revised
    for eid, e in by_id.items():
        action = e.get("action") or {}
        if e.get("status") == "done":
            loc = str(action.get("location", ""))
            path, _, anchor = loc.partition("#")
            target = revised / path if path else None
            if not path or not target.is_file():
                errors.append(f"Ledger {eid}: status done but location {loc!r} does not exist")
            elif anchor and anchor not in target.read_text(encoding="utf-8-sig", errors="replace"):
                errors.append(f"Ledger {eid}: anchor {anchor!r} not found in {path}")
        if action.get("kind") == "experiment" and e.get("status") in ("drafted", "done") and not e.get("evidence"):
            errors.append(f"Ledger {eid}: experiment action needs 'evidence' (ledger keys, run ids or paths)")

    bindings_path = folder / "bindings.json"
    bindings = json.loads(bindings_path.read_text(encoding="utf-8-sig")) if bindings_path.is_file() else []
    if bindings:
        b_err, _ = _load_formal().check_ledger_bindings(folder, bindings_path)
        errors += [f"bindings: {m}" for m in b_err]
    bound = {(b.get("file"), str(b.get("display"))) for b in bindings}
    unbound = 0
    for reply in replies:
        rel = reply.relative_to(folder).as_posix()
        for m in NUM_RE.finditer(_strip_code(reply.read_text(encoding="utf-8-sig"))):
            if (rel, m.group(0).lstrip("+").replace("−", "-")) not in bound and (rel, m.group(0)) not in bound:
                unbound += 1
                errors.append(f"{rel}: number {m.group(0)} has no binding in bindings.json")
    report["unbound_numbers"] = unbound

    manifest_path = folder / "manifest.json"
    if manifest_path.is_file():
        man = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        src = Path(man["source_dir"])
        allowed = set(man.get("allowed_changes", []))
        changed = []
        for rel, digest in man["files"].items():
            p = src / rel
            if not p.is_file():
                changed.append(rel)
                errors.append(f"source: {rel} was deleted")
            elif sha256(p) != digest:
                changed.append(rel)
                if rel not in allowed:
                    errors.append(f"source: {rel} changed but is not in allowed_changes")
        report["original_files_changed"] = changed
    report["errors"] = len(errors)
    return errors, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check", help="QA a rebuttal folder (default)")
    c.add_argument("folder", type=Path)
    c.add_argument("--char-limit", type=int)
    c.add_argument("--forbid", nargs="*", default=None, help="terms that must not appear, e.g. ImageNet")
    c.add_argument("--report", type=Path, help="write the QA JSON here (default: <folder>/QA.json)")
    s = sub.add_parser("snapshot", help="hash the original paper source before revising it")
    s.add_argument("source", type=Path)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--allow", nargs="*", default=[], help="files the revision may change")
    args = ap.parse_args(argv)
    if args.cmd == "snapshot":
        data = snapshot(args.source, args.out, args.allow)
        print(f"snapshot of {len(data['files'])} files -> {args.out}")
        return 0
    if args.cmd != "check":
        ap.print_help()
        return 2
    cfg = {}
    if args.char_limit:
        cfg["char_limit"] = args.char_limit
    if args.forbid is not None:
        cfg["forbid"] = args.forbid
    errors, report = check_rebuttal(args.folder, cfg)
    out = args.report or args.folder / "QA.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for e in errors:
        print(f"  - {e}")
    print(f"REBUTTAL QA {'FAILED' if errors else 'PASS'}: {report['ledger_entries'] if 'ledger_entries' in report else 0} comments, report -> {out}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

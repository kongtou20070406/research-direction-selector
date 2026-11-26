"""Build the Tier 2 reviewer brief: only what a reviewer has to read (0 LLM tokens).

First freeze (no prior audit): every section. Later freezes: only the sections whose hash
changed since the prior audit, as a unified diff when the earlier text is in the review store,
plus the prior audit's open findings and the ledger bindings those sections use. Unchanged
sections are listed by name; their earlier review stands. Mechanical facts (parity, bindings,
clarity) are already enforced by Tier 1 and are not re-sent.

    python build_review_brief.py --draft-dir D --out brief.md [--prior-audit A.json]
        [--bindings B.json] [--store D/.review_store]

The brief ends with the ``section_sha256`` block the reviewer copies into the audit JSON.
It reports characters, not tokens.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

_OPEN = ("unresolved", "partially_resolved")


def _formal():
    path = Path(__file__).resolve().parent / "verify_manuscript_formal.py"
    spec = importlib.util.spec_from_file_location("verify_manuscript_formal", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _text(draft_dir: Path, rel: str) -> str:
    return (draft_dir / rel).read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def build_brief(draft_dir: Path, prior: dict | None = None, bindings: list | None = None,
                store: Path | None = None) -> tuple[str, dict]:
    formal = _formal()
    current = formal.section_digests(draft_dir)
    store = store or draft_dir / ".review_store"
    store.mkdir(parents=True, exist_ok=True)
    for rel, digest in current.items():  # content-addressed, create-only
        blob = store / f"{digest}.md"
        if not blob.exists():
            blob.write_text(_text(draft_dir, rel), encoding="utf-8", newline="\n")

    prior_sections = (prior or {}).get("section_sha256") or {}
    changed = formal.changed_sections(current, prior_sections) if prior_sections else sorted(current)
    unchanged = sorted(set(current) - set(changed))
    parts = ["# Tier 2 review brief", ""]
    if prior_sections:
        parts += [f"Re-review against audit of manuscript `{prior.get('manuscript_sha256', '?')[:12]}`. "
                  f"{len(changed)} of {len(current)} section files changed; review only these. "
                  "Each changed section needs at least one audit item whose `scope` names it.", ""]
    else:
        parts += [f"First freeze: all {len(current)} section files.", ""]

    for rel in changed:
        new = _text(draft_dir, rel)
        old_blob = store / f"{prior_sections.get(rel, '')}.md" if rel in prior_sections else None
        if old_blob is not None and old_blob.is_file():
            diff = difflib.unified_diff(old_blob.read_text(encoding="utf-8").splitlines(), new.splitlines(),
                                        fromfile=f"{rel} (reviewed)", tofile=f"{rel} (now)", lineterm="", n=2)
            parts += [f"## {rel} (diff)", "", "```diff", *diff, "```", ""]
        else:
            parts += [f"## {rel}", "", new.strip(), ""]

    open_findings = [f for f in (prior or {}).get("findings", []) if isinstance(f, dict) and f.get("status") in _OPEN]
    if open_findings:
        parts += ["## Open findings from the prior audit", "",
                  "Report each one again with its status and origin.", ""]
        for f in open_findings:
            parts.append(f"- `{f.get('id')}` ({f.get('severity')}): {f.get('evidence')} | resolve when: {f.get('resolution', '-')}")
        parts.append("")

    used = [b for b in (bindings or []) if any(str(b.get("file", "")).endswith(Path(rel).name) for rel in changed)]
    if used:
        parts += ["## Ledger bindings in the changed sections (already verified by Tier 1)", ""]
        parts += [f"- `{b['display']}` in {b['file']} <- {b['ledger']} : {b['key']}" for b in used]
        parts.append("")
    if unchanged:
        parts += ["## Unchanged since the prior review (do not re-review)", "", ", ".join(unchanged), ""]
    parts += ["## Copy into the audit JSON", "", "```json",
              json.dumps({"section_sha256": current}, indent=2), "```", ""]
    brief = "\n".join(parts)
    full = sum(len(_text(draft_dir, rel)) for rel in current)
    stats = {"sections": len(current), "changed": len(changed), "brief_chars": len(brief),
             "full_manuscript_chars": full, "brief_to_full": round(len(brief) / full, 3) if full else None}
    return brief, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draft-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--prior-audit", type=Path)
    ap.add_argument("--bindings", type=Path)
    ap.add_argument("--store", type=Path)
    args = ap.parse_args(argv)
    prior = json.loads(args.prior_audit.read_text(encoding="utf-8-sig")) if args.prior_audit else None
    bindings = json.loads(args.bindings.read_text(encoding="utf-8-sig")) if args.bindings else None
    brief, stats = build_brief(args.draft_dir, prior, bindings, args.store)
    args.out.write_text(brief, encoding="utf-8", newline="\n")
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Project structured review findings into a lead-writer action brief."""

import argparse
import json
import re
from pathlib import Path


OBJECTIVE = {
    "math_counterexample", "number_mismatch", "missing_control", "lean_mismatch",
    "confirmed_flaw", "unsupported_claim",
}
BOUNDARY = {"regime_boundary", "scope_caveat"}
TONE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:[/⁄／]|out of)\s*\d+(?:\.\d+)?\b|"
    r"\b(?:overall (?:score|rating)|(?:strong|weak|borderline|clear|definite) (?:accept|reject)|"
    r"(?:recommend|vote for) (?:accept(?:ance)?|reject(?:ion)?)|"
    r"(?:accept|reject) (?:the|this) (?:paper|submission))\b|强拒|弱拒|建议(?:拒稿|录用)", re.I,
)
REWRITE = re.compile(
    r"\b(?:rewrite|rephrase|(?:replacement|proposed|suggested) (?:text|sentence|prose|wording|rewrite)|"
    r"replace\b.*\bwith)\b|\b(?:write|say|word it|change it to)\s*(?:as follows\b|instead\b|:)"
    r"|改写[为成]|建议措辞", re.I,
)
RAW_VERDICT = re.compile(r"^(?:(?:verdict|recommendation|decision)\s*[:=]\s*)?"
                         r"(?:accept(?:ed)?|reject(?:ed)?|pass|fail|revise)[.!]?$", re.I)
FACT_KEYS = ("kind", "action", "ref", "location", "observed", "expected", "condition", "statement")
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")


def fact(value):
    """Keep factual fields on one Markdown line and drop tone or rewrite fragments."""
    if isinstance(value, dict):
        return " / ".join(f"{key}={clean}" for key in FACT_KEYS if (clean := fact(value.get(key))))
    if isinstance(value, list):
        return " / ".join(clean for item in value if (clean := fact(item)))
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    fragments = re.split(r"[\r\n]+|(?<=[.!?。！？])\s+", str(value))
    clean = []
    for part in (fragment.strip() for fragment in fragments):
        if REWRITE.search(part):
            break  # A rewrite cue may introduce pasted prose on subsequent lines.
        if part and not TONE.search(part) and not RAW_VERDICT.fullmatch(part):
            clean.append(part)
    return (" ".join(clean).replace("|", r"\|").replace("<", "&lt;").replace(">", "&gt;")
            .replace("#", r"\#"))


def findings(report):
    for key in ("findings", "items"):
        rows = report.get(key, [])
        if isinstance(rows, list):
            yield from (row for row in rows if isinstance(row, dict))


def build_brief(audit_data, editorial_data=None) -> str:
    must_fix, boundaries, quarantine = [], [], []
    for report in (audit_data or {}, editorial_data or {}):
        if not isinstance(report, dict):
            continue
        for key in ("regime_boundary", "scope_caveat"):
            if boundary := fact(report.get(key)):
                boundaries.append(boundary)
        for row in findings(report):
            category = row.get("category") or row.get("type")
            identifier = row.get("id", "")
            identifier = identifier if isinstance(identifier, str) and SAFE_ID.fullmatch(identifier) else ""
            if isinstance(category, str) and category in BOUNDARY:
                boundary = fact(row.get("boundary") or row.get("evidence") or row.get("resolution"))
                if boundary:
                    boundaries.append(boundary)
            elif isinstance(category, str) and category in OBJECTIVE and row.get("severity") in ("critical", "major"):
                if row.get("status") in ("resolved", "not_applicable"):
                    continue
                must_fix.append(" | ".join((
                    identifier or "—", fact(row.get("scope")) or "—", category,
                    fact(row.get("evidence")) or fact(row.get("evidence_ref")) or "—",
                    fact(row.get("resolution")) or "—",
                )))
            elif identifier or "severity" in row:
                quarantine.append(identifier)

    lines = [
        "## MUST_FIX (Objective Defects Only)", "",
        "| id | scope | category/type | evidence | resolution |",
        "| --- | --- | --- | --- | --- |",
        *(f"| {row} |" for row in must_fix),
        "", "## LIMITATION_BOUNDARY (§7 Only — Do NOT Dilute Abstract/Intro)", "",
        "> Keep these boundaries in §7. Do not add apologetic hedging to the Abstract, Introduction, or Method.",
        "", *(f"- {boundary}" for boundary in dict.fromkeys(boundaries)),
        "", "## QUARANTINED_TONE (Excluded From Author Context)", "",
        f"- Count: {len(quarantine)}",
        "- IDs: " + (", ".join(identifier for identifier in quarantine if identifier) or "—"),
    ]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--editorial", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    audit = json.loads(args.audit.read_text(encoding="utf-8-sig"))
    editorial = json.loads(args.editorial.read_text(encoding="utf-8-sig")) if args.editorial else None
    args.out.write_text(build_brief(audit, editorial), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

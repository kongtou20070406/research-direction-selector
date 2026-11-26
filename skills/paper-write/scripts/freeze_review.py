"""Dispatch the two Tier 2 freeze reviews and check them mechanically (no human step).

    python freeze_review.py --draft-dir D --digest SHA --out-dir OUT [--prior-audit A.json]
        [--bindings B.json] [--attach run_meta.json ...] [--dry-run]

1. Builds the reviewer brief with build_review_brief.py (only changed sections on a re-review).
2. Sends it, in parallel and independently, to two read-only reviewers through ``codex exec -s read-only``:
   ``independent-reviewer`` for math, claims and entailment (writes ``audit.json``) and ``editorial-reviewer`` for
   framing, flow and EN/ZH fidelity (writes ``editorial.json``). Neither sees the other's report or
   the lead's opinion. Each gets one retry after a delay; there is no model fallback, because the
   two reviews must stay independent.
3. Stamps what this script itself sent (``manuscript_sha256``, ``section_sha256``, ``auditor``) into
   each report, so the hash binding does not depend on a model copying a hash correctly.
4. Runs ``verify_freeze_audit`` on the astra audit and the finding rules on the editorial report.

Prints ``FREEZE_REVIEW PASS`` and exits 0 only when both reviews pass; otherwise prints every
problem and exits 1. ``--dry-run`` writes the prompts and stops, without calling a model.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent

AUDITOR = "independent-reviewer"
EDITOR = "editorial-reviewer"

BASE_FINDING_SCHEMA = """Each finding: {"id": "F1", "severity": "critical|major|minor",
 "scope": "<section file and location>",
 "type": "confirmed_flaw|unsupported_claim|clarification",
 "status": "unresolved|partially_resolved|resolved|not_applicable",
 "evidence": "<source reference and observed fact or exact source quote>",
 "countercheck": "<how you tried to refute your own finding>",
 "resolution": "<verifiable closure condition; required for open critical/major>"%s}
An open critical or major finding forbids verdict PASS."""

FINDING_SCHEMA = BASE_FINDING_SCHEMA + """
For each Astra finding, include a separate "category" field. Only a concrete math_counterexample,
number_mismatch, missing_control, or lean_mismatch (manuscript prose stronger than the Lean proof)
may be critical or major. Cite the specific counterexample, number, control, or proof mismatch.
A scope_caveat or a preference for more conservative wording must be minor and advisory.
Scope caveats must never block PASS or request apologetic disclaimers in the Abstract or Introduction.
Preserve the existing finding type values for the verifier."""

ORIGIN = ', "origin": "inherited|revision_regression|previously_undetected|newly_revealed_by_evidence|external_standard_change"'

AUDIT_TASK = """You are an independent, read-only reviewer of a machine-learning paper before submission.
Check mathematics, claims and entailment only: does every claim follow from the stated evidence,
are theorems stated and used correctly, do numbers match the bindings below.
State the affirmative_core at full strength: what the evidence cleanly establishes.
Give the regime_boundary separately in one neutral scientific sentence for §7 Limitations.
Do not edit files. Do not comment on style.
Do NOT assign numeric scores (e.g. 4/10), use accept/reject slogans, or rewrite manuscript prose;
report structured facts only. Keep exact evidence and resolution conditions in separate fields.
Answer with ONE JSON object and nothing else:
{"verdict": "PASS|FAIL",
 "affirmative_core": "<what is established at full strength>",
 "regime_boundary": "<one neutral sentence for §7 Limitations>",
 "items": [{"rule": "<what you checked>", "scope": "<section file, e.g. sections_en/03_method.md>",
            "exact_quote": "<quote>", "evidence_ref": "<binding, ledger row or equation>", "verdict": "PASS|FAIL"}],
 "findings": [ ... ]%s}
Every section shown below needs at least one item whose scope names it.
""" + FINDING_SCHEMA

EDIT_TASK = """You are an independent, read-only editorial reviewer of a machine-learning paper before submission.
Check framing, flow, claim restraint and whether the English and Chinese versions say the same thing.
Do not check mathematics. Do not edit files.
Do NOT assign numeric scores (e.g. 4/10), use accept/reject slogans, or rewrite manuscript prose;
report structured facts only. Keep exact evidence and resolution conditions in separate fields.
For a scope limit, use "category": "scope_caveat" and give the scientific boundary for §7 only.
Answer with ONE JSON object and nothing else:
{"verdict": "PASS|REVISE", "findings": [ ... ]}
""" + BASE_FINDING_SCHEMA


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_json_reply(raw: str) -> dict | None:
    """The last JSON object in a reply (models sometimes wrap it in a code fence)."""
    for cand in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S) or [raw]):
        start, end = cand.find("{"), cand.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            obj = json.loads(cand[start:end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def codex_runner(prompt: str, model: str, cwd: Path, out: Path, effort: str, timeout: int) -> str:
    """One read-only ``codex exec`` call; returns the final message ('' on failure)."""
    exe = shutil.which("codex") or "codex"
    cmd = [exe, "exec", "--ephemeral", "--skip-git-repo-check", "-C", str(cwd), "-m", model,
           "-c", 'model_provider="magpie"', "-c", f'model_reasoning_effort="{effort}"',
           "-s", "read-only", "-o", str(out), "-"]
    try:
        proc = subprocess.run(cmd, input=prompt, text=True, encoding="utf-8", errors="replace",
                              capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        out.with_suffix(".err.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        return ""
    if proc.returncode != 0:
        out.with_suffix(".err.txt").write_text(f"rc={proc.returncode}\n{proc.stderr[-4000:]}", encoding="utf-8")
        return ""
    return out.read_text(encoding="utf-8") if out.is_file() else proc.stdout


Runner = Callable[[str, str, Path, Path, str, int], str]


def _ask(runner: Runner, prompt: str, model: str, cwd: Path, raw: Path, effort: str, timeout: int,
         retry_delay: int) -> dict | None:
    for attempt in range(2):
        if attempt:
            time.sleep(retry_delay)
        reply = runner(prompt, model, cwd, raw, effort, timeout)
        parsed = parse_json_reply(reply or "")
        if parsed is not None:
            return parsed
    return None


def run(draft_dir: Path, digest: str, out_dir: Path, prior_audit: Path | None = None,
        bindings: Path | None = None, attach: list[Path] | None = None, runner: Runner = codex_runner,
        timeout: int = 1800, retry_delay: int = 120, dry_run: bool = False,
        fmt: str | None = None) -> list[str]:
    """Returns the blocking problems (empty list means both reviews passed)."""
    formal, brief_mod = _load("verify_manuscript_formal"), _load("build_review_brief")
    fmt = formal.draft_format(draft_dir, fmt)
    if fmt == "tex" and formal.compute_manuscript_digest(draft_dir, fmt=fmt) != digest:
        return ["LaTeX manuscript digest mismatch before review"]
    sections = formal.section_digests(draft_dir, fmt)
    out_dir.mkdir(parents=True, exist_ok=True)
    prior = json.loads(prior_audit.read_text(encoding="utf-8-sig")) if prior_audit else None
    bind_rows = json.loads(bindings.read_text(encoding="utf-8-sig")) if bindings else None
    if isinstance(bind_rows, dict):
        bind_rows = bind_rows.get("bindings", list(bind_rows.values()))
    # The existing brief builder uses this module-local reader and hash provider for its diff store.
    brief_mod._formal = lambda: SimpleNamespace(
        section_digests=lambda _: sections, changed_sections=formal.changed_sections
    )
    store = None
    if fmt == "tex":
        if not sections:
            return ["No LaTeX sections to review"]
        converted = {}
        for rel, sha in sections.items():
            raw = (draft_dir / rel).read_bytes()
            if hashlib.sha256(raw).hexdigest() != sha:
                return [f"LaTeX section changed before review: {rel}"]
            converted[rel] = formal.latex_to_text(raw.decode("utf-8-sig"))
        if formal.compute_manuscript_digest(draft_dir, fmt=fmt) != digest:
            return ["LaTeX manuscript changed before review"]
        brief_mod._text = lambda _, rel: converted[rel]
        store = out_dir.parent / ".review_store"
        if bind_rows is not None:
            bind_rows = [dict(row, file=formal.binding_section_path(row["file"], fmt).as_posix()) for row in bind_rows]
    brief, stats = brief_mod.build_brief(draft_dir, prior=prior, bindings=bind_rows, store=store)
    if fmt == "tex":
        brief = brief.replace("# Tier 2 review brief\n", "# Tier 2 review brief\n\n"
                              "Source: bilingual `.tex` sections, converted to text. "
                              "Section hashes cover the original LaTeX bytes.\n", 1)
    extra = "".join(f"\n\n## Attached: {p.name}\n\n```\n{p.read_text(encoding='utf-8-sig')[:20000]}\n```"
                    for p in (attach or []))
    origin = ORIGIN if prior else ""
    reprior = ("\nThis is a re-review. Every finding of the prior audit that is still open must appear "
               "again with its current status and origin 'inherited'.\nPrior findings:\n"
               + json.dumps(prior.get("findings", []), ensure_ascii=False, indent=1)) if prior else ""
    audit_task = AUDIT_TASK % ("", origin)
    if fmt == "tex":
        audit_task = audit_task.replace("sections_en/03_method.md", "latex/sections_en/03_method.tex")
    audit_prompt = audit_task + reprior + "\n\n" + brief + extra
    edit_prompt = (EDIT_TASK % origin) + reprior + "\n\n" + brief + extra
    (out_dir / "brief.md").write_text(brief, encoding="utf-8", newline="\n")
    (out_dir / "prompt_audit.md").write_text(audit_prompt, encoding="utf-8", newline="\n")
    (out_dir / "prompt_editorial.md").write_text(edit_prompt, encoding="utf-8", newline="\n")
    if dry_run:
        print(f"DRY RUN: prompts in {out_dir} (brief {stats})")
        return []

    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(_ask, runner, audit_prompt, AUDITOR, draft_dir, out_dir / "audit_raw.md", "high", timeout, retry_delay)
        fe = pool.submit(_ask, runner, edit_prompt, EDITOR, draft_dir, out_dir / "editorial_raw.md", "high", timeout, retry_delay)
        audit, editorial = fa.result(), fe.result()

    problems: list[str] = []
    stamp = {"manuscript_sha256": digest, "section_sha256": sections}
    if prior:
        stamp["prior_manuscript_sha256"] = prior.get("manuscript_sha256")
    if audit is None:
        problems.append(f"{AUDITOR} gave no parseable JSON after a retry (see {out_dir / 'audit_raw.md'})")
    else:
        audit.update(stamp, auditor=AUDITOR)
        (out_dir / "audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        problems += [f"audit: {e}" for e in formal.verify_freeze_audit(
            out_dir / "audit.json", digest, prior_audit, draft_dir, fmt=fmt
        )]
    if editorial is None:
        problems.append(f"{EDITOR} gave no parseable JSON after a retry (see {out_dir / 'editorial_raw.md'})")
    else:
        editorial.update(stamp, reviewer=EDITOR)
        (out_dir / "editorial.json").write_text(json.dumps(editorial, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if editorial.get("verdict") != "PASS":
            problems.append(f"editorial: verdict is {editorial.get('verdict')!r}, not PASS")
        problems += [f"editorial: {e}" for e in formal.check_audit_findings(editorial, None)]
    triage = _load("review_triage")
    (out_dir / "lead_action_brief.md").write_text(triage.build_brief(audit, editorial),
                                                  encoding="utf-8", newline="\n")
    (out_dir / "RESULT.json").write_text(json.dumps({"digest": digest, "pass": not problems, "problems": problems},
                                                    indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draft-dir", type=Path, required=True)
    ap.add_argument("--digest", required=True, help="manuscript sha256 of the staged drafts")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--prior-audit", type=Path)
    ap.add_argument("--bindings", type=Path)
    ap.add_argument("--attach", type=Path, nargs="*", default=[])
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--markdown", action="store_true", help="Read legacy Markdown sections")
    a = ap.parse_args(argv)
    problems = run(a.draft_dir, a.digest, a.out_dir, a.prior_audit, a.bindings, a.attach,
                   timeout=a.timeout, dry_run=a.dry_run, fmt="md" if a.markdown else None)
    for p in problems:
        print(f"  - {p}")
    print("FREEZE_REVIEW " + ("DRY-RUN" if a.dry_run else "BLOCKED" if problems else "PASS") + f" -> {a.out_dir}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Tier 1 deterministic formal manuscript verifier (0 LLM tokens).

Verifies bilingual EN/ZH section, equation, table, and numeric parity;
checks notation tables for symbol collisions; validates JSON-ledger rounding
bindings; and flags stray unfinished placeholders (`TODO`, `TBD`, `???`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from latex_text import binding_section_path, draft_format, latex_to_text, read_section, section_dirs, section_labels

SEPARATOR_ROW_RE = re.compile(r"^\|[\s:\-|]+\|$")
NUM_LITERAL_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?%?")
LATEX_PARITY_NUM_RE = re.compile(r"\d+\.\d+|\d+%")
PLACEHOLDER_RE = re.compile(r"\b(?:TODO|TBD)\b|\?\?\?")
PENDING_MARKER_RE = re.compile(r"\[(?:PENDING|MATERIAL GAP)[^\]]*\]")
TEXT_WRAPPER_RE = re.compile(r"\\(?:text|mathrm|operatorname|mathbf)\{[^}]*\}")
CONTRIB_PADDING_RE = re.compile(
    r"\b(?:benchmark\s+protocol|protocol|triton|cuda\s+graph|unit\s+test|speedup)\b|"
    r"评测协议|对照协议|单元测试|硬件加速|核函数加速",
    flags=re.IGNORECASE,
)
BANNED_JARGON_RE = re.compile(
    r"Evidence-Conditioned\s+Attractors?|证据条件吸引子|盲视线|驱动球|两模态几何",
    flags=re.IGNORECASE,
)

STANDARD_METHOD_FIRST_SECTIONS = (
    "00_abstract.md",
    "01_introduction.md",
    "02_setup.md",
    "03_method.md",
    "04_theory.md",
    "05_experiments.md",
    "06_related_work.md",
    "07_discussion_limitations.md",
    "08_appendix.md",
)
PURE_THEORY_SECTIONS = (
    "00_abstract.md",
    "01_introduction.md",
    "02_setup.md",
    "03_theory.md",
    "04_method.md",
    "05_experiments.md",
    "06_related_work.md",
    "07_discussion_limitations.md",
    "08_appendix.md",
)


def _strip_code_blocks(text: str) -> list[str]:
    lines: list[str] = []
    in_code = False
    for ln in text.splitlines():
        if ln.strip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            lines.append(ln)
    return lines


def _extract_display_equations(text: str) -> list[str]:
    lines = _strip_code_blocks(text)
    eqs: list[str] = []
    in_eq = False
    buf: list[str] = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("$$"):
            if s.endswith("$$") and len(s) > 4:
                eqs.append(s[2:-2].strip())
                continue
            if in_eq:
                tail = s[:-2].strip()
                if tail:
                    buf.append(tail)
                eqs.append("\n".join(buf).strip())
                buf = []
                in_eq = False
            else:
                head = s[2:].strip()
                if head:
                    buf.append(head)
                in_eq = True
        elif in_eq:
            buf.append(s)
    return eqs


def _normalize_equation(eq: str) -> str:
    # Normalize spacing and prose wrappers so EN/ZH equation structures match deterministically
    cleaned = TEXT_WRAPPER_RE.sub("", eq)
    cleaned = re.sub(r"\\[,;!:\s]+", "", cleaned)
    return re.sub(r"\s+", "", cleaned)


def _extract_tables(text: str, fmt: str = "md") -> list[list[list[str]]]:
    """Return tables as list of data rows (each row is a list of cell strings), skipping separator rows."""
    lines = _strip_code_blocks(text)
    tables: list[list[list[str]]] = []
    cur: list[list[str]] = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            if SEPARATOR_ROW_RE.match(s):
                continue
            parts = s[2:-2].split(" | ") if fmt == "tex" else s.strip("|").split("|")
            cells = [c.strip() for c in parts]
            cur.append(cells)
        else:
            if cur:
                tables.append(cur)
                cur = []
    if cur:
        tables.append(cur)
    return tables


def _cell_numbers(cell: str) -> list[str]:
    return sorted(NUM_LITERAL_RE.findall(cell))


def check_bilingual_parity(en_dir: Path, zh_dir: Path, fmt: str = "md") -> tuple[list[str], dict]:
    errors: list[str] = []
    if not en_dir.is_dir():
        return [f"Missing English sections directory: {en_dir}"], {"sections": 0, "display_eqs": 0, "tables": 0}
    if not zh_dir.is_dir():
        return [f"Missing Chinese sections directory: {zh_dir}"], {"sections": 0, "display_eqs": 0, "tables": 0}

    en_files = {p.name: p for p in sorted(en_dir.glob(f"*.{fmt}"))}
    zh_files = {p.name: p for p in sorted(zh_dir.glob(f"*.{fmt}"))}
    if set(en_files) != set(zh_files):
        only_en = sorted(set(en_files) - set(zh_files))
        only_zh = sorted(set(zh_files) - set(en_files))
        errors.append(f"Section file mismatch: only_en={only_en}, only_zh={only_zh}")
    if fmt == "tex" and not en_files and not zh_files:
        errors.append("No LaTeX section files found in either language")

    total_eqs = 0
    total_tables = 0
    for fname in sorted(set(en_files) & set(zh_files)):
        en_text = en_files[fname].read_text(encoding="utf-8-sig")
        zh_text = zh_files[fname].read_text(encoding="utf-8-sig")

        if fmt == "tex":
            en_labels, zh_labels = section_labels(en_text), section_labels(zh_text)
            if en_labels != zh_labels:
                errors.append(f"[{fname}] LaTeX label mismatch: only_en={sorted(en_labels - zh_labels)}, "
                              f"only_zh={sorted(zh_labels - en_labels)}")
            en_text = latex_to_text(en_text, for_checks=True)
            zh_text = latex_to_text(zh_text, for_checks=True)
            en_numbers = Counter(LATEX_PARITY_NUM_RE.findall(en_text.replace(r"\%", "%")))
            zh_numbers = Counter(LATEX_PARITY_NUM_RE.findall(zh_text.replace(r"\%", "%")))
            if en_numbers != zh_numbers:
                errors.append(f"[{fname}] LaTeX numeric multiset mismatch: "
                              f"only_en={dict(en_numbers - zh_numbers)}, only_zh={dict(zh_numbers - en_numbers)}")
            total_eqs += len(_extract_display_equations(en_text))
            total_tables += len(_extract_tables(en_text, fmt))
            continue

        en_eqs = _extract_display_equations(en_text)
        zh_eqs = _extract_display_equations(zh_text)
        total_eqs += len(en_eqs)
        if len(en_eqs) != len(zh_eqs):
            errors.append(f"[{fname}] Display equation count mismatch: EN={len(en_eqs)} vs ZH={len(zh_eqs)}")
        else:
            for idx, (e_eq, z_eq) in enumerate(zip(en_eqs, zh_eqs), start=1):
                if _normalize_equation(e_eq) != _normalize_equation(z_eq):
                    errors.append(f"[{fname}] Equation #{idx} diverges between EN and ZH")

        en_tables = _extract_tables(en_text)
        zh_tables = _extract_tables(zh_text)
        total_tables += len(en_tables)
        if len(en_tables) != len(zh_tables):
            errors.append(f"[{fname}] Table count mismatch: EN={len(en_tables)} vs ZH={len(zh_tables)}")
        else:
            for t_idx, (e_tbl, z_tbl) in enumerate(zip(en_tables, zh_tables), start=1):
                if len(e_tbl) != len(z_tbl):
                    errors.append(
                        f"[{fname}] Table #{t_idx} row count mismatch: EN={len(e_tbl)} vs ZH={len(zh_tbl)}"
                    )
                    continue
                for r_idx, (e_row, z_row) in enumerate(zip(e_tbl, z_tbl), start=1):
                    if len(e_row) != len(z_row):
                        errors.append(
                            f"[{fname}] Table #{t_idx} row #{r_idx} column count mismatch: EN={len(e_row)} vs ZH={len(z_row)}"
                        )
                        continue
                    for c_idx, (e_cell, z_cell) in enumerate(zip(e_row, z_row), start=1):
                        e_nums = _cell_numbers(e_cell)
                        z_nums = _cell_numbers(z_cell)
                        if e_nums != z_nums:
                            errors.append(
                                f"[{fname}] Table #{t_idx} row #{r_idx} col #{c_idx} numeric mismatch: EN={e_nums} vs ZH={z_nums}"
                            )

    return errors, {"sections": len(set(en_files) & set(zh_files)), "display_eqs": total_eqs, "tables": total_tables}


def check_notation_table_hygiene(sec_dir: Path, fmt: str = "md") -> tuple[list[str], dict]:
    errors: list[str] = []
    symbol_count = 0
    if not sec_dir.is_dir():
        return [f"Missing sections directory for notation check: {sec_dir}"], {"symbols": 0}

    for f in sorted(sec_dir.glob(f"*.{fmt}")):
        for t_idx, tbl in enumerate(_extract_tables(read_section(f, for_checks=True), fmt), start=1):
            if len(tbl) < 2:
                continue
            header = [h.lower() for h in tbl[0]]
            sym_col = None
            for c_idx, h in enumerate(header):
                if any(tok in h for tok in ("symbol", "notation", "符号")):
                    sym_col = c_idx
                    break
            if sym_col is None:
                continue
            seen: dict[str, int] = {}
            for r_idx, row in enumerate(tbl[1:], start=2):
                if sym_col >= len(row):
                    continue
                sym = re.sub(r"\s+", "", row[sym_col].strip("$` "))
                if not sym:
                    continue
                symbol_count += 1
                if sym in seen:
                    errors.append(
                        f"[{f.name}] Table #{t_idx} duplicate symbol '{sym}' (rows {seen[sym]} and {r_idx})"
                    )
                else:
                    seen[sym] = r_idx
    return errors, {"symbols": symbol_count}


def _resolve_json_key(obj: object, dotted_key: str) -> object:
    cur = obj
    for part in dotted_key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise KeyError(f"Missing key path segment '{part}' in '{dotted_key}'")
    return cur


def check_ledger_bindings(
    draft_dir: Path, bindings_path: Path | None, fmt: str | None = None
) -> tuple[list[str], dict]:
    if bindings_path is None:
        return [], {"bindings": 0}
    if not bindings_path.is_file():
        return [f"Missing ledger bindings file: {bindings_path}"], {"bindings": 0}

    bindings = json.loads(bindings_path.read_text(encoding="utf-8-sig"))
    if not isinstance(bindings, list):
        return [f"Bindings JSON must be a list of binding dicts: {bindings_path}"], {"bindings": 0}

    fmt = draft_format(draft_dir, fmt)
    errors: list[str] = []
    ledger_cache: dict[Path, object] = {}
    for idx, item in enumerate(bindings, start=1):
        rel_file = item["file"]
        display = str(item["display"])
        ledger_ref = Path(item["ledger"])
        ledger_file = ledger_ref if ledger_ref.is_absolute() else (draft_dir / ledger_ref)
        key = str(item["key"])
        digits = int(item.get("round", 3))
        scale = float(item.get("scale", 1.0))

        target_rel = binding_section_path(rel_file, fmt)
        if fmt == "tex" and (target_rel.parts[:-1] not in (("latex", "sections_en"), ("latex", "sections_zh"))
                             or target_rel.suffix != ".tex"):
            errors.append(f"Binding #{idx}: expected a LaTeX section target, got {rel_file}")
            continue
        target_file = draft_dir / target_rel
        if not target_file.is_file():
            errors.append(f"Binding #{idx}: missing target file {target_file}")
            continue
        if not ledger_file.is_file():
            errors.append(f"Binding #{idx}: missing ledger JSON {ledger_file}")
            continue
        if ledger_file not in ledger_cache:
            ledger_cache[ledger_file] = json.loads(ledger_file.read_text(encoding="utf-8-sig"))

        try:
            raw_val = _resolve_json_key(ledger_cache[ledger_file], key)
            num_val = float(raw_val) * scale
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"Binding #{idx}: failed resolving {key} in {ledger_file}: {exc}")
            continue

        expected_num = f"{num_val:.0f}" if digits == 0 else f"{round(num_val, digits):.{digits}f}"
        expected = f"{expected_num}%" if display.endswith("%") else expected_num
        if display != expected:
            errors.append(
                f"Binding #{idx} ({rel_file}): display '{display}' != ledger '{expected}' (raw={raw_val}, scale={scale})"
            )
            continue

        doc_text = read_section(target_file).replace(r"\%", "%") if fmt == "tex" else read_section(target_file)
        if display not in doc_text:
            errors.append(f"Binding #{idx}: formatted value '{display}' not found in {rel_file}")

    return errors, {"bindings": len(bindings)}


# Result-like numbers: decimals or percentages, optionally signed. Version strings such as GPT-4.1 or
# v2.3 are skipped because the character before the number is a word character, '.' or '-'.
_HEADLINE_NUM_RE = re.compile(r"(?<![\w.\-])[+\-−]?\d+(?:\.\d+)?%|(?<![\w.\-])[+\-−]?\d+\.\d+")
_LATEX_HEADLINE_NUM_RE = re.compile(_HEADLINE_NUM_RE.pattern, re.ASCII)


def check_headline_binding_coverage(
    draft_dir: Path, bindings_path: Path | None, fmt: str | None = None
) -> tuple[list[str], dict]:
    """Every result-like number in the abstract and introduction must carry a ledger binding.

    `check_ledger_bindings` only verifies numbers that already have a binding; this closes the gap
    where an unbound number in the headline sections passes silently.
    """
    fmt = draft_format(draft_dir, fmt)
    bound: dict[str, set[str]] = {}
    if bindings_path is not None and bindings_path.is_file():
        for item in json.loads(bindings_path.read_text(encoding="utf-8-sig")):
            rel = binding_section_path(item["file"], fmt).as_posix()
            bound.setdefault(rel, set()).add(str(item["display"]).lstrip("+-−"))
    errors: list[str] = []
    checked = 0
    number_re = _LATEX_HEADLINE_NUM_RE if fmt == "tex" else _HEADLINE_NUM_RE
    for directory in section_dirs(draft_dir, fmt):
        for f in sorted(directory.glob(f"0[01]*.{fmt}")):
            rel = f.relative_to(draft_dir).as_posix()
            text = read_section(f)
            text = text.replace(r"\%", "%") if fmt == "tex" else re.sub(r"`[^`]*`", " ", text)
            for m in number_re.finditer(text):
                checked += 1
                token = m.group(0).lstrip("+-−")
                if token not in bound.get(rel, set()):
                    errors.append(f"Unbound headline number '{m.group(0)}' in {rel}: add a ledger binding")
    return errors, {"headline_numbers": checked}


def check_placeholder_and_format(sec_dir: Path, fmt: str = "md") -> tuple[list[str], dict]:
    errors: list[str] = []
    pending_count = 0
    if not sec_dir.is_dir():
        return [f"Missing sections directory: {sec_dir}"], {"pending_markers": 0}

    for f in sorted(sec_dir.glob(f"*.{fmt}")):
        lines = _strip_code_blocks(read_section(f, for_checks=True))
        for ln_no, ln in enumerate(lines, start=1):
            pending_count += len(PENDING_MARKER_RE.findall(ln))
            m = PLACEHOLDER_RE.search(ln)
            if m:
                errors.append(f"[{f.name}:{ln_no}] Stray placeholder '{m.group(0)}'")
    return errors, {"pending_markers": pending_count}


def check_section_order_and_headings(
    sec_dir: Path, lang: str, pure_theory: bool = False, fmt: str = "md"
) -> tuple[list[str], dict]:
    errors: list[str] = []
    if not sec_dir.is_dir():
        return [f"Missing sections directory ({lang}): {sec_dir}"], {"checked_order": 0}

    files = {p.with_suffix(".md").name: p for p in sorted(sec_dir.glob(f"*.{fmt}"))}
    has_icml_pair = any(
        k in files for k in ("03_method.md", "04_theory.md", "03_theory.md", "04_method.md")
    )
    if len(files) < 9 and not has_icml_pair:
        return [], {"checked_order": 0}

    expected = PURE_THEORY_SECTIONS if pure_theory else STANDARD_METHOD_FIRST_SECTIONS
    missing = [Path(name).with_suffix(f".{fmt}").name for name in expected if name not in files]
    if missing:
        errors.append(f"[{lang}] Missing required ICML sections in {sec_dir.name}: {missing}")
        return errors, {"checked_order": 1}

    def _first_h2(path: Path) -> str:
        for ln in read_section(path, for_checks=True).splitlines():
            s = ln.strip()
            if s.startswith("## "):
                return s
        return ""

    for name in expected[3:5]:
        path = files[name]
        heading = _first_h2(path)
        subject, pattern = ("Theory", r"theor|理论") if "theory" in name else ("Method", r"method|方法")
        if subject == "Theory" and fmt == "tex":
            pattern += r"|\banalysis\b|分析"
        if not re.search(pattern, heading, flags=re.IGNORECASE):
            errors.append(f"[{lang}] {path.name} first H2 must denote {subject} (got {heading!r})")
    return errors, {"checked_order": 1}


def check_contributions_and_banned_jargon(sec_dir: Path, fmt: str = "md") -> tuple[list[str], dict]:
    errors: list[str] = []
    if not sec_dir.is_dir():
        return [f"Missing sections directory: {sec_dir}"], {"contrib_bullets": 0}

    contrib_bullets = 0
    intro = sec_dir / f"01_introduction.{fmt}"
    if intro.is_file():
        for ln_no, ln in enumerate(read_section(intro, for_checks=True).splitlines(), start=1):
            if re.match(r"^\s*\d+\.\s+", ln):
                contrib_bullets += 1
                flat = re.sub(r"[*`]", "", ln)
                m = CONTRIB_PADDING_RE.search(flat)
                if m:
                    errors.append(
                        f"[{sec_dir.name}/{intro.name}:{ln_no}] Contribution bullet contains engineering/protocol padding: '{m.group(0)}'"
                    )

    for f in sorted(sec_dir.glob(f"*.{fmt}")):
        lines = _strip_code_blocks(read_section(f, for_checks=True))
        for ln_no, ln in enumerate(lines, start=1):
            m = BANNED_JARGON_RE.search(ln)
            if m:
                errors.append(f"[{sec_dir.name}/{f.name}:{ln_no}] Banned coined jargon: '{m.group(0)}'")
    return errors, {"contrib_bullets": contrib_bullets}


def compute_manuscript_digest(
    draft_dir: Path, en_text: str = "", zh_text: str = "", fmt: str | None = None
) -> str:
    h = hashlib.sha256()
    fmt = draft_format(draft_dir, fmt)
    if fmt == "tex":
        for rel, digest in section_digests(draft_dir, fmt).items():
            h.update(f"{rel}\0{digest}\n".encode("utf-8"))
        return h.hexdigest()
    for sub in ("sections_en", "sections_zh"):
        sdir = draft_dir / sub
        if sdir.is_dir():
            for f in sorted(sdir.glob("*.md")):
                h.update(f"{sub}/{f.name}\n".encode("utf-8"))
                h.update(f.read_text(encoding="utf-8-sig").replace("\r\n", "\n").encode("utf-8"))
    h.update(b"__RENDERED_EN__\n")
    h.update(en_text.replace("\r\n", "\n").encode("utf-8"))
    h.update(b"__RENDERED_ZH__\n")
    h.update(zh_text.replace("\r\n", "\n").encode("utf-8"))
    return h.hexdigest()


# Review findings follow the re-review provenance contract adapted from CCFA-Skills
# (ccf-paper-reviewer/references/version-comparison.md, MIT): a re-review must say whether each
# finding is inherited, introduced by the revision, or merely noticed late.
FINDING_SEVERITIES = ("critical", "major", "minor")
FINDING_TYPES = ("confirmed_flaw", "unsupported_claim", "clarification")
FINDING_STATUSES = ("unresolved", "partially_resolved", "resolved", "not_applicable")
FINDING_ORIGINS = (
    "inherited",
    "revision_regression",
    "previously_undetected",
    "newly_revealed_by_evidence",
    "external_standard_change",
)
_OPEN = ("unresolved", "partially_resolved")


def _load_audit(path: Path) -> tuple[dict | None, str | None]:
    if not path.is_file():
        return None, f"Missing freeze audit JSON: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON in freeze audit {path}: {exc}"
    if not isinstance(data, dict):
        return None, "Freeze audit payload must be a JSON object"
    return data, None


def check_audit_findings(data: dict, prior: dict | None = None) -> list[str]:
    """Validate optional ``findings``; with ``prior``, enforce re-review provenance."""
    findings = data.get("findings", [])
    if prior is not None and "findings" not in data:
        return ["Re-review audit must carry a 'findings' list (empty if none)"]
    if not isinstance(findings, list):
        return ["Freeze audit 'findings' must be a list"]
    errors: list[str] = []
    seen: set[str] = set()
    for idx, f in enumerate(findings, start=1):
        if not isinstance(f, dict):
            errors.append(f"Finding #{idx} must be an object")
            continue
        fid = f.get("id")
        tag = f"Finding {fid or '#' + str(idx)}"
        if not isinstance(fid, str) or not fid.strip():
            errors.append(f"{tag} missing non-empty 'id'")
        elif fid in seen:
            errors.append(f"{tag} id is duplicated")
        else:
            seen.add(fid)
        for key, allowed in (("severity", FINDING_SEVERITIES), ("type", FINDING_TYPES), ("status", FINDING_STATUSES)):
            if f.get(key) not in allowed:
                errors.append(f"{tag} '{key}' must be one of {list(allowed)}, got {f.get(key)!r}")
        for key in ("evidence", "countercheck"):
            if not isinstance(f.get(key), str) or not f[key].strip():
                errors.append(f"{tag} missing non-empty '{key}'")
        blocking = f.get("severity") in ("critical", "major") and f.get("status") in _OPEN
        if blocking:
            if not isinstance(f.get("resolution"), str) or not f["resolution"].strip():
                errors.append(f"{tag} is blocking and needs a 'resolution' condition")
            if data.get("verdict") == "PASS":
                errors.append(f"{tag} is an open {f.get('severity')} finding but the verdict is PASS")
        if prior is not None and f.get("origin") not in FINDING_ORIGINS:
            errors.append(f"{tag} 'origin' must be one of {list(FINDING_ORIGINS)} on a re-review")
    if prior is None:
        return errors

    if data.get("prior_manuscript_sha256") != prior.get("manuscript_sha256"):
        errors.append(
            "Re-review 'prior_manuscript_sha256' does not match the prior audit's manuscript_sha256"
        )
    prior_open = [
        f.get("id") for f in prior.get("findings", [])
        if isinstance(f, dict) and f.get("status") in _OPEN and f.get("id")
    ]
    for fid in prior_open:
        if fid not in seen:
            errors.append(f"Prior open finding {fid} is missing from the re-review; record its status instead of dropping it")
    prior_ids = {f.get("id") for f in prior.get("findings", []) if isinstance(f, dict)}
    for f in findings:
        if isinstance(f, dict) and f.get("origin") == "inherited" and f.get("id") not in prior_ids:
            errors.append(f"Finding {f.get('id')} is marked inherited but is not in the prior audit")
    return errors


def verify_freeze_audit(
    audit_json_path: Path,
    expected_digest: str,
    prior_audit_path: Path | None = None,
    draft_dir: Path | None = None,
    fmt: str | None = None,
) -> list[str]:
    if not audit_json_path.is_file():
        return [f"Missing freeze audit JSON: {audit_json_path}"]
    try:
        data = json.loads(audit_json_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return [f"Invalid JSON in freeze audit {audit_json_path}: {exc}"]
    if not isinstance(data, dict):
        return ["Freeze audit payload must be a JSON object"]

    errors: list[str] = []
    if not data.get("auditor"):
        errors.append("Freeze audit must specify a non-empty 'auditor'")
    if data.get("manuscript_sha256") != expected_digest:
        errors.append(
            f"Freeze audit digest mismatch: audit={data.get('manuscript_sha256')!r} != staged={expected_digest!r}"
        )
    if data.get("verdict") != "PASS":
        errors.append(f"Freeze audit top-level verdict must be 'PASS', got {data.get('verdict')!r}")

    items = data.get("items")
    if not isinstance(items, list) or not items:
        errors.append("Freeze audit 'items' must be a non-empty list")
        return errors

    required_fields = ("rule", "scope", "exact_quote", "evidence_ref", "verdict")
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"Freeze audit item #{idx} must be an object")
            continue
        for key in required_fields:
            val = item.get(key)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"Freeze audit item #{idx} missing non-empty '{key}'")
        if item.get("verdict") != "PASS":
            errors.append(
                f"Freeze audit item #{idx} ({item.get('rule', '?')}) verdict is {item.get('verdict')!r}, expected 'PASS'"
            )
    prior = None
    if prior_audit_path is not None:
        prior, err = _load_audit(prior_audit_path)
        if err:
            return errors + [f"Prior audit: {err}"]
    errors.extend(check_audit_findings(data, prior))
    if draft_dir is not None:
        errors.extend(check_review_coverage(data, prior, draft_dir, fmt))
    return errors


def section_digests(draft_dir: Path, fmt: str | None = None) -> dict[str, str]:
    """Hash raw LaTeX bytes or the legacy line-ending-normalized Markdown text."""
    fmt = draft_format(draft_dir, fmt)
    out: dict[str, str] = {}
    for directory in section_dirs(draft_dir, fmt):
        for f in sorted(directory.glob(f"*.{fmt}")):
            content = f.read_bytes() if fmt == "tex" else read_section(f).replace("\r\n", "\n").encode("utf-8")
            out[f.relative_to(draft_dir).as_posix()] = hashlib.sha256(content).hexdigest()
    return out


def changed_sections(current: dict[str, str], prior: dict[str, str]) -> list[str]:
    return sorted(k for k, v in current.items() if prior.get(k) != v)


def check_review_coverage(data: dict, prior: dict | None, draft_dir: Path, fmt: str | None = None) -> list[str]:
    """Bind the audit to exact sections; on a re-review every changed section needs an item.

    Unchanged sections keep their earlier review, so a re-review only has to read what changed
    (see build_review_brief.py). This is what lets later freezes skip the full manuscript.
    """
    current = section_digests(draft_dir, fmt)
    recorded = data.get("section_sha256")
    if prior is not None and not isinstance(recorded, dict):
        return ["Re-review audit must record 'section_sha256' (copy it from build_review_brief.py)"]
    errors: list[str] = []
    if isinstance(recorded, dict):
        stale = sorted(k for k in current if recorded.get(k) != current[k])
        if stale:
            errors.append(f"Audit 'section_sha256' does not match the staged sections: {stale[:4]}")
    prior_sections = (prior or {}).get("section_sha256")
    if isinstance(prior_sections, dict):
        scopes = " ".join(str(i.get("scope", "")) for i in data.get("items", []) if isinstance(i, dict))
        for sec in changed_sections(current, prior_sections):
            if sec not in scopes and Path(sec).stem not in scopes:
                errors.append(f"Changed section {sec} has no audit item whose 'scope' names it")
    return errors


def verify_manuscript(
    draft_dir: Path,
    bindings_path: Path | None = None,
    pure_theory: bool = False,
    require_headline_bindings: bool = False,
    fmt: str | None = None,
) -> tuple[list[str], dict]:
    fmt = draft_format(draft_dir, fmt)
    en_dir, zh_dir = section_dirs(draft_dir, fmt)

    e_ord_en, _ = check_section_order_and_headings(en_dir, "EN", pure_theory=pure_theory, fmt=fmt)
    e_ord_zh, _ = check_section_order_and_headings(zh_dir, "ZH", pure_theory=pure_theory, fmt=fmt)
    e_jar_en, _ = check_contributions_and_banned_jargon(en_dir, fmt)
    e_jar_zh, _ = check_contributions_and_banned_jargon(zh_dir, fmt)
    e_parity, s_parity = check_bilingual_parity(en_dir, zh_dir, fmt)
    e_sym_en, s_sym_en = check_notation_table_hygiene(en_dir, fmt)
    e_sym_zh, s_sym_zh = check_notation_table_hygiene(zh_dir, fmt)
    e_bind, s_bind = check_ledger_bindings(draft_dir, bindings_path, fmt)
    e_ph_en, s_ph_en = check_placeholder_and_format(en_dir, fmt)
    e_ph_zh, s_ph_zh = check_placeholder_and_format(zh_dir, fmt)
    e_cov, s_cov = (
        check_headline_binding_coverage(draft_dir, bindings_path, fmt)
        if require_headline_bindings
        else ([], {"headline_numbers": 0})
    )

    errors = (
        e_ord_en
        + e_ord_zh
        + e_jar_en
        + e_jar_zh
        + e_parity
        + e_sym_en
        + e_sym_zh
        + e_bind
        + e_ph_en
        + e_ph_zh
        + e_cov
    )
    stats = {
        "sections": s_parity["sections"],
        "display_eqs": s_parity["display_eqs"],
        "tables": s_parity["tables"],
        "symbols": s_sym_en["symbols"] + s_sym_zh["symbols"],
        "bindings": s_bind["bindings"],
        "pending_markers": s_ph_en["pending_markers"] + s_ph_zh["pending_markers"],
        "headline_numbers": s_cov["headline_numbers"],
        "errors": len(errors),
    }
    return errors, stats


def _stat_pair(val) -> tuple[float, float | None] | None:
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val), None
    if isinstance(val, dict):
        mean = val.get("mean")
        if isinstance(mean, (int, float)) and not isinstance(mean, bool):
            std = val.get("std")
            return float(mean), (float(std) if isinstance(std, (int, float)) and not isinstance(std, bool) else None)
    if isinstance(val, list) and val and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in val):
        xs = [float(x) for x in val]
        mean = sum(xs) / len(xs)
        std = (sum((x - mean) ** 2 for x in xs) / len(xs)) ** 0.5 if len(xs) > 1 else None
        return mean, std
    return None


def extract_result_rows(payload: dict) -> list[tuple[str, dict[str, tuple[float, float | None]]]]:
    """Extract (row_label, {metric: (mean, std_or_none)}) from common RESULTS.json layouts."""
    rows: list[tuple[str, dict[str, tuple[float, float | None]]]] = []
    if isinstance(payload.get("rows"), list):
        for idx, item in enumerate(payload["rows"]):
            if not isinstance(item, dict):
                continue
            label = str(item.get("name") or item.get("method") or item.get("arm") or f"row_{idx + 1}")
            metrics = {k: pair for k, v in item.items()
                       if k not in ("name", "method", "arm") and (pair := _stat_pair(v)) is not None}
            if metrics:
                rows.append((label, metrics))
        return rows

    container = None
    for key in ("evaluations", "planners", "results", "summary"):
        if isinstance(payload.get(key), dict) and payload[key]:
            container = payload[key]
            break
    if container is None:
        container = payload

    for key, val in container.items():
        if not isinstance(val, dict) or key.endswith("seeds"):
            continue
        metrics = {k: pair for k, v in val.items()
                   if not str(k).endswith("seeds") and (pair := _stat_pair(v)) is not None}
        if metrics:
            rows.append((str(key), metrics))
    return rows


def render_results_latex(
    results_path: Path,
    out_tex: Path,
    *,
    metrics: list[str] | None = None,
    lower_better: set[str] = frozenset(),
    digits: int = 4,
    bindings_out: Path | None = None,
) -> dict:
    """Render a booktabs LaTeX table fragment from RESULTS.json and optionally record SHA bindings."""
    import hashlib

    raw_bytes = results_path.read_bytes()
    sha = hashlib.sha256(raw_bytes).hexdigest()
    payload = json.loads(raw_bytes.decode("utf-8-sig"))
    rows = extract_result_rows(payload)
    if not rows:
        return {"ok": False, "rows": 0, "sha256": sha}

    cols = list(metrics) if metrics else []
    if not cols:
        seen: list[str] = []
        for _, m in rows:
            for k in m:
                if k not in seen:
                    seen.append(k)
        cols = seen[:8]

    best: dict[str, float] = {}
    for col in cols:
        vals = [m[col][0] for _, m in rows if col in m]
        if vals:
            best[col] = min(vals) if col in lower_better else max(vals)

    tex_esc = lambda s: str(s).replace("_", r"\_").replace("%", r"\%")
    lines = [
        f"% AUTO-GENERATED from {results_path.name} (sha256:{sha[:12]}) — do not hand-edit",
        r"\begin{tabular}{l" + "c" * len(cols) + "}",
        r"\toprule",
        "Method & " + " & ".join(tex_esc(c) for c in cols) + r" \\",
        r"\midrule",
    ]
    bound_rows: dict[str, dict[str, str]] = {}
    for label, m in rows:
        cells = []
        bound_rows[label] = {}
        for col in cols:
            if col not in m:
                cells.append("--")
                continue
            mean, std = m[col]
            txt = f"{mean:.{digits}f}"
            if std is not None:
                txt = f"{txt} \\pm {std:.{digits}f}"
            bound_rows[label][col] = txt
            if col in best and abs(mean - best[col]) <= 10 ** (-(digits + 1)):
                cell = rf"\textbf{{{txt}}}" if std is None else rf"$\mathbf{{{txt}}}$"
            else:
                cell = txt if std is None else f"${txt}$"
            cells.append(cell)
        lines.append(f"{tex_esc(label)} & " + " & ".join(cells) + r" \\")
    lines.extend([r"\bottomrule", r"\end{tabular}", ""])
    out_tex.parent.mkdir(parents=True, exist_ok=True)
    out_tex.write_text("\n".join(lines), encoding="utf-8")

    if bindings_out is not None:
        existing = json.loads(bindings_out.read_text(encoding="utf-8")) if bindings_out.is_file() else {}
        if not isinstance(existing, dict):
            existing = {}
        existing[out_tex.stem] = {"source": results_path.name, "sha256": sha, "rows": bound_rows}
        bindings_out.parent.mkdir(parents=True, exist_ok=True)
        bindings_out.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {"ok": True, "rows": len(rows), "cols": cols, "sha256": sha, "out": out_tex}


def main() -> int:
    ap = argparse.ArgumentParser(description="Tier 1 deterministic formal manuscript verifier.")
    ap.add_argument("--draft-dir", type=Path, default=None)
    ap.add_argument("--ledger-bindings", type=Path, default=None)
    ap.add_argument("--pure-theory", action="store_true")
    ap.add_argument("--markdown", action="store_true", help="Read legacy Markdown sections")
    ap.add_argument(
        "--require-headline-bindings",
        action="store_true",
        help="Fail on any decimal or percentage in sections 00/01 (EN and ZH) without a ledger binding.",
    )
    ap.add_argument("--results-to-tex", type=Path, default=None, help="Render a RESULTS.json into a booktabs .tex table")
    ap.add_argument("--out-tex", type=Path, default=None)
    ap.add_argument("--bindings-out", type=Path, default=None)
    ap.add_argument("--metrics", default="", help="Comma-separated metric columns")
    ap.add_argument("--lower-better", default="", help="Comma-separated lower-is-better metric names")
    ap.add_argument("--digits", type=int, default=4)
    args = ap.parse_args()

    if args.results_to_tex is not None:
        out_tex = args.out_tex or args.results_to_tex.with_name(f"auto_table_{args.results_to_tex.stem.lower()}.tex")
        metrics = [m.strip() for m in args.metrics.split(",") if m.strip()] or None
        lower = {m.strip() for m in args.lower_better.split(",") if m.strip()}
        res = render_results_latex(args.results_to_tex, out_tex, metrics=metrics, lower_better=lower,
                                   digits=args.digits, bindings_out=args.bindings_out)
        print(f"RESULTS_TO_TEX {'OK' if res['ok'] else 'EMPTY'} rows={res['rows']} -> {out_tex}")
        return 0 if res["ok"] else 1
    if args.draft_dir is None:
        ap.error("--draft-dir is required unless --results-to-tex is provided")

    errors, stats = verify_manuscript(
        args.draft_dir,
        args.ledger_bindings,
        pure_theory=args.pure_theory,
        require_headline_bindings=args.require_headline_bindings,
        fmt="md" if args.markdown else None,
    )
    status = "PASS" if not errors else "FAILED"
    print(
        f"FORMAL CHECK {status}: "
        f"sections={stats['sections']}, display_eqs={stats['display_eqs']}, tables={stats['tables']}, "
        f"symbols={stats['symbols']}, bindings={stats['bindings']}, headline_numbers={stats['headline_numbers']}, "
        f"pending={stats['pending_markers']}, "
        f"errors={stats['errors']}"
    )
    for err in errors:
        print(f"  - {err}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

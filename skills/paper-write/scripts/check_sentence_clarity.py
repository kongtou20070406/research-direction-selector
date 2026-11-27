"""Sentence-clarity linter for bilingual (EN + ZH) LaTeX and Markdown drafts.

Enforces soft length targets (advisory P90 / over-target counts) and hard gates
on extreme sentence length, excessive clause commas, stacked Chinese modifiers,
AI-slop phrasing, lab-diary tags, and internal workflow slogan leakage.
"""

import argparse
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from latex_text import draft_format, latex_to_text, section_dirs
from process_leak import find_latex_process_leaks, find_process_leaks

ABBREV_RE = re.compile(
    r"\b(?:et al|e\.g|i\.e|vs|Fig|Tab|Eq|Sec|App|Ref|No|pp|cf|approx|resp)\.",
    flags=re.IGNORECASE,
)
HAN_RE = re.compile(r"[\u4e00-\u9fff]")
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'/][A-Za-z0-9]+)*")
ZH_SOFT_COMMAS, EN_SOFT_COMMAS = 2, 3
ZH_HARD_COMMAS, EN_HARD_COMMAS = 3, 4
DEAI_EN = re.compile(
    r"^To (?:stabilize|prevent|ensure|mitigate|address)\b|\benforce exact\b|\bboth tasks iterate on\b",
    re.I,
)
DEAI_ZH = re.compile(
    r"学得[\u4e00-\u9fff]{2,}|为[\u4e00-\u9fff]{2,6}，[^。]{4,25}则|在[^。]{2,15}上迭代求解|"
    r"保证[^。]{2,15}严格|记分[^。]{0,6}查询|对称排版|零点归零"
)
CAPTION_PREFIXES = (
    "![",
    "Figure ",
    "Fig. ",
    "Table ",
    "Tab. ",
    "图 ",
    "表 ",
    "**Figure",
    "**Fig.",
    "**Table",
    "**Tab.",
    "**图",
    "**表",
)
CITE_CMD_RE = re.compile(r"\\cite[tp]?\{[^}]+\}|\[(?:\d+(?:\s*[-–,]\s*\d+)*)\]")
YEAR_PAREN_CITE_RE = re.compile(r"[(\（][^()\（\）]*(?<![0-9])(?:19|20)\d{2}[a-z]?(?!\d)[^()\（\）]*[)\）]")
NESTED_ZH_CHAIN_RE = re.compile(
    r"在[^。！？]{1,25}(?:下|中)[^。！？]{0,12}(?:通过|借助|利用)[^。！？]{1,25}(?:以|来|从而)"
)
EN_SLOP_RE = re.compile(
    r"\b(?:"
    r"delve(?:s|d|ing)?|tapestry|intricate(?:ly)?|underscore(?:s|d|ing)?|pivotal|paramount|testament|"
    r"realm|nuanced|multifaceted|meticulous(?:ly)?|seamlessly|bolster(?:s|ed|ing)?|foster(?:s|ed|ing)?|"
    r"harness(?:es|ed|ing)?|elucidate(?:s|d|ing)?|ever-evolving|shed light on|pave(?:s|d|ing)? the way|"
    r"it is worth noting|play a (?:crucial|vital|pivotal) role|utilize(?:s|d|ing)?|leverage(?:s|d|ing)?|"
    r"facilitate(?:s|d|ing)?|revolutionary|groundbreaking|game-changing|paradigm shift|unprecedented|"
    r"totally|dramatically|massively"
    r")\b",
    flags=re.IGNORECASE,
)
SUBMISSION_JARGON_RE = re.compile(
    r"(?:\b(?:Card|Arm)\s*\d+\b|\bpreflight\b|\bL0\.5\b)",
    flags=re.IGNORECASE,
)
PROCESS_LEAK_RE = re.compile(
    r"(?:What\s*/\s*How|What\s+not\s+How|What-Claim|Anti-AI-Slop|AI-slop|AI\s+slop|"
    r"Claim-first|Claim-vs-Design|replaceable\s+instantiation|replaceable\s+means|"
    r"replaceable\s+testbed|可替换实现|可替换手段|独立科学宣称|反自嗨|技巧汤|换血|"
    r"\bT1[–-]T5\b|\bW1[–-]W7\b)",
    flags=re.IGNORECASE,
)


def _p90(values: list[int]) -> int:
    if not values:
        return 0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(0.9 * (len(s) - 1) + 0.999999)))
    return s[idx]


_LIST_ITEM_RE = re.compile(r"^(?:\d+\.|[-*+])\s+")


def _clean_prose_lines(raw_text: str, join_wrapped: bool = False) -> list[str]:
    lines = raw_text.splitlines()
    out: list[str] = []
    in_frontmatter = len(lines) > 0 and lines[0].strip() == "---"
    in_code = False
    in_display_math = False
    in_references = False
    paragraph_open = False

    start_idx = 1 if in_frontmatter else 0
    for idx in range(start_idx, len(lines)):
        s = lines[idx].strip()
        if not s or s.startswith(("#", "```", "$$", "|", "---", ">")) or _LIST_ITEM_RE.match(s):
            paragraph_open = False
        if in_frontmatter:
            if s == "---":
                in_frontmatter = False
            continue
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if s.startswith("$$"):
            if s == "$$" or not (s.endswith("$$") and len(s) > 2):
                in_display_math = not in_display_math
            continue
        if in_display_math:
            continue
        if s.startswith("#"):
            hdr = s.lstrip("#").strip().lower()
            if re.match(r"^(?:\d+\s+)?(?:references|bibliography|参考文献)\b", hdr):
                in_references = True
            elif in_references and (s.startswith("# ") or re.match(r"^##\s+(?:[a-z]\b|appendix|附录)", hdr)):
                in_references = False
            continue
        if in_references or not s or s.startswith("|") or s.startswith("---") or s.startswith(">"):
            continue
        if s.startswith(CAPTION_PREFIXES):
            paragraph_open = False
            continue
        if re.fullmatch(r"(?:[-*]\s+)?(?:[^\n：:]+[：:]\s*)?(?:\[\[[^\]]+\]\]|\s|·)+", s):
            continue
        s = _LIST_ITEM_RE.sub("", s)
        s = re.sub(r"^\*\*[^*]+\*\*\s*[：:]\s*", "", s)
        if join_wrapped and paragraph_open:
            out[-1] += " " + s
        else:
            out.append(s)
        paragraph_open = True
    return out


def _normalize_inline_tokens(text: str) -> str:
    text = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\$[^$]+\$", " M0 ", text)
    text = re.sub(r"`[^`]+`", " C0 ", text)
    text = CITE_CMD_RE.sub("", text)
    text = YEAR_PAREN_CITE_RE.sub("", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\s+([.,;!?。，；！？])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_sentences(paragraph: str) -> list[str]:
    norm = _normalize_inline_tokens(paragraph)
    if not norm:
        return []
    safe = re.sub(r"(?<=\d)\.(?=\d)", "<DOT>", norm)
    safe = ABBREV_RE.sub(lambda m: m.group(0).replace(".", "<DOT>"), safe)
    safe = re.sub(r"\b([A-Za-z])\.(?=\s*[A-Za-z])", r"\1<DOT>", safe)

    chunks = re.split(r"(?<=[。！？])\s*", safe)
    sentences: list[str] = []
    for ch in chunks:
        sub = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\u4e00-\u9fff\"“(\[])", ch)
        for s in sub:
            restored = s.replace("<DOT>", ".").strip()
            if restored and not re.fullmatch(r"(?:M0|C0|\W)+", restored):
                sentences.append(restored)
    return sentences


def _audit_text(
    path: Path,
    raw: str,
    zh_soft: int = 42,
    zh_max: int = 60,
    en_soft: int = 25,
    en_max: int = 35,
    zh_max_commas: int = ZH_HARD_COMMAS,
    en_max_commas: int = EN_HARD_COMMAS,
    max_commas: int | None = None,
    check_slop: bool = True,
    submission_mode: bool = False,
    join_wrapped: bool = False,
    latex_sources: list[str] | None = None,
    strict_deai: bool = False,
) -> dict:
    if max_commas is not None:
        zh_max_commas = max_commas
        en_max_commas = max_commas
    paragraphs = _clean_prose_lines(raw, join_wrapped)
    violations: list[dict] = []
    advisories: list[dict] = []
    deai = violations if strict_deai else advisories
    zh_lengths: list[int] = []
    en_lengths: list[int] = []
    zh_soft_over = 0
    en_soft_over = 0
    zh_soft_commas = en_soft_commas = 0

    for para in _clean_prose_lines(raw, join_wrapped=True):
        window = []
        in_rhythm = False
        for sent in _split_sentences(para):
            han = len(HAN_RE.findall(sent))
            lang = "ZH" if han else "EN"
            length = han or len(WORD_RE.findall(sent))
            for pattern, reason in ((DEAI_EN, "ai_taste"), (DEAI_ZH, "translationese")):
                if hits := [match[0] for match in pattern.finditer(sent)]:
                    deai.append({"lang": lang, "reasons": [f"{reason}={','.join(hits)}"], "text": sent})
            window.append((lang, length, sent))
            window = window[-3:]
            lengths = [size for _, size, _ in window]
            rhythm = (
                len(window) == 3 and all(language == lang for language, _, _ in window)
                and 0 < min(lengths) and max(lengths) <= (35 if han else 22)
                and max(lengths) - min(lengths) <= 4
            )
            if rhythm and not in_rhythm:
                deai.append({"lang": lang, "reasons": ["telegraphic_rhythm"],
                             "text": " ".join(sentence for _, _, sentence in window)})
            in_rhythm = rhythm

    for para in paragraphs:
        for sent in _split_sentences(para):
            han_count = len(HAN_RE.findall(sent))
            comma_count = sent.count("，") + sent.count(",") + sent.count("；") + sent.count(";")
            slop_hits = sorted(set(m.group(0).lower() for m in EN_SLOP_RE.finditer(sent))) if check_slop else []
            jargon_hits = (
                sorted(set(m.group(0) for m in SUBMISSION_JARGON_RE.finditer(sent))) if submission_mode else []
            )
            process_hits = (
                sorted(set(m.group(0) for m in PROCESS_LEAK_RE.finditer(sent))) if submission_mode else []
            )
            if han_count >= 6:
                zh_soft_commas += comma_count > ZH_SOFT_COMMAS
                prose_len = han_count + len(re.findall(r"\b(?:M0|C0)\b", sent))
                zh_lengths.append(prose_len)
                if prose_len > zh_soft:
                    zh_soft_over += 1
                reasons = []
                if prose_len > zh_max:
                    reasons.append(f"zh_len={prose_len}>{zh_max}")
                if comma_count > zh_max_commas:
                    reasons.append(f"commas={comma_count}>{zh_max_commas}")
                de_count = sent.count("的")
                if de_count >= 3:
                    reasons.append(f"stacked_de={de_count}>=3")
                if NESTED_ZH_CHAIN_RE.search(sent):
                    reasons.append("nested_zh_chain")
                if slop_hits:
                    reasons.append(f"en_slop={','.join(slop_hits)}")
                if jargon_hits:
                    reasons.append(f"submission_jargon={','.join(jargon_hits)}")
                if process_hits:
                    reasons.append(f"process_leak={','.join(process_hits)}")
                if reasons:
                    violations.append({"lang": "ZH", "reasons": reasons, "text": sent})
            else:
                words = WORD_RE.findall(sent)
                en_len = len(words)
                if en_len < 3:
                    continue
                en_soft_commas += comma_count > EN_SOFT_COMMAS
                en_lengths.append(en_len)
                if en_len > en_soft:
                    en_soft_over += 1
                reasons = []
                if en_len > en_max:
                    reasons.append(f"en_words={en_len}>{en_max}")
                if comma_count > en_max_commas:
                    reasons.append(f"commas={comma_count}>{en_max_commas}")
                if slop_hits:
                    reasons.append(f"en_slop={','.join(slop_hits)}")
                if jargon_hits:
                    reasons.append(f"submission_jargon={','.join(jargon_hits)}")
                if process_hits:
                    reasons.append(f"process_leak={','.join(process_hits)}")
                if reasons:
                    violations.append({"lang": "EN", "reasons": reasons, "text": sent})

    if submission_mode:
        leaks = (
            [hit for source in latex_sources for hit in find_latex_process_leaks(source)]
            if latex_sources is not None else find_process_leaks(raw, None)
        )
        for hit in leaks:
            sentence = hit["sentence"]
            violations.append({
                "lang": "ZH" if HAN_RE.search(sentence) else "EN",
                "reasons": [f"process_leak:{hit['kind']}"],
                "text": sentence,
            })

    return {
        "file": str(path),
        "zh_count": len(zh_lengths),
        "zh_avg": round(sum(zh_lengths) / max(1, len(zh_lengths)), 1),
        "zh_p90": _p90(zh_lengths),
        "zh_max_obs": max(zh_lengths, default=0),
        "zh_soft_over": zh_soft_over,
        "en_count": len(en_lengths),
        "en_avg": round(sum(en_lengths) / max(1, len(en_lengths)), 1),
        "en_p90": _p90(en_lengths),
        "en_max_obs": max(en_lengths, default=0),
        "en_soft_over": en_soft_over,
        "zh_soft_commas": zh_soft_commas,
        "en_soft_commas": en_soft_commas,
        "advisories": advisories,
        "violations": violations,
    }


def audit_file(path: Path, *args, fmt: str | None = None, **options) -> dict:
    use_latex = fmt == "tex" or (fmt is None and path.suffix == ".tex")
    source = path.read_text(encoding="utf-8-sig")
    raw = latex_to_text(source, for_checks=True) if use_latex else source
    return _audit_text(
        path, raw, *args, join_wrapped=use_latex, latex_sources=[source] if use_latex else None, **options
    )


def audit_sections(draft_dir: Path, fmt: str | None = None, **options) -> dict[str, dict]:
    fmt = draft_format(draft_dir, fmt)
    reports = {}
    for lang, directory in zip(("EN", "ZH"), section_dirs(draft_dir, fmt)):
        paths = sorted(directory.glob(f"*.{fmt}"))
        if not paths:
            raise FileNotFoundError(f"No .{fmt} sections for {lang} in {directory}")
        sources = [p.read_text(encoding="utf-8-sig") for p in paths]
        use_latex = fmt == "tex"
        prose = [latex_to_text(s, for_checks=True) for s in sources] if use_latex else sources
        reports[lang] = _audit_text(
            directory, "\n\n".join(prose), join_wrapped=use_latex,
            latex_sources=sources if use_latex else None, **options
        )
    return reports


def main() -> int:
    ap = argparse.ArgumentParser(description="Check prose sentence clarity in EN+ZH LaTeX or Markdown.")
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--draft-dir", type=Path, help="Read both language section directories")
    ap.add_argument("--markdown", action="store_true", help="Read legacy Markdown when given a draft directory")
    ap.add_argument("--zh-soft", type=int, default=42)
    ap.add_argument("--zh-max", type=int, default=60)
    ap.add_argument("--en-soft", type=int, default=25)
    ap.add_argument("--en-max", type=int, default=35)
    ap.add_argument("--zh-max-commas", type=int, default=ZH_HARD_COMMAS)
    ap.add_argument("--en-max-commas", type=int, default=EN_HARD_COMMAS)
    ap.add_argument("--max-commas", type=int, default=None, help="Optional override for both ZH and EN comma limits")
    ap.add_argument("--no-slop", action="store_true", help="Disable English AI-slop/overclaim word scanning")
    ap.add_argument("--submission-mode", action="store_true", help="Flag lab jargon & internal workflow leaks")
    ap.add_argument("--strict-deai", action="store_true", help="Treat De-AI advisories as violations")
    args = ap.parse_args()

    if not args.files and args.draft_dir is None:
        ap.error("provide files or --draft-dir")
    options = dict(zh_soft=args.zh_soft, zh_max=args.zh_max, en_soft=args.en_soft, en_max=args.en_max,
                   zh_max_commas=args.zh_max_commas, en_max_commas=args.en_max_commas,
                   max_commas=args.max_commas, check_slop=not args.no_slop,
                   submission_mode=args.submission_mode, strict_deai=args.strict_deai)
    fmt = "md" if args.markdown else None
    try:
        if args.draft_dir is not None:
            reports = list(audit_sections(args.draft_dir, fmt, **options).values())
        else:
            reports = []
        reports.extend(audit_file(p, fmt=fmt, **options) for p in args.files)
    except FileNotFoundError as exc:
        ap.error(str(exc))
    total_v = 0
    for res in reports:
        print(
            f"[{Path(res['file']).name}] "
            f"ZH={res['zh_count']} (avg {res['zh_avg']}, p90 {res['zh_p90']}, max {res['zh_max_obs']}, soft>{args.zh_soft}:{res['zh_soft_over']}) | "
            f"EN={res['en_count']} (avg {res['en_avg']}, p90 {res['en_p90']}, max {res['en_max_obs']}, soft>{args.en_soft}:{res['en_soft_over']}) | "
            f"soft commas ZH={res['zh_soft_commas']} EN={res['en_soft_commas']} | "
            f"violations={len(res['violations'])}, advisories={len(res['advisories'])}"
        )
        for v in res["violations"]:
            print(f"  - [{v['lang']}] ({', '.join(v['reasons'])}) {v['text']}")
        for advisory in res["advisories"]:
            print(f"  - Advisory [{advisory['lang']}] ({', '.join(advisory['reasons'])}) {advisory['text']}")
        total_v += len(res["violations"])
    return 1 if total_v else 0


if __name__ == "__main__":
    sys.exit(main())

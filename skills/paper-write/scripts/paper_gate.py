"""Post-edit consistency gate for LaTeX-native papers: check, auto-fix the safe classes, compile, re-check.

One command after every edit, so agents stop hand-hunting mechanical slips:

    python paper_gate.py PAPER_DIR --fix --compile

Per-paper settings live in PAPER_DIR/paper_gate.json. Exit code 0 means no
errors remain (warnings allowed); 1 means errors remain and the paper must not be delivered.

Auto-fixed (deterministic, logged, originals backed up to PAPER_DIR/.gate_backup/<stamp>/):
  terms      registered variant phrases -> canonical phrase (case preserved, math/labels untouched)
  acronyms   first prose use per scope (abstract, body) without a definition -> "Long Form (ACR)"
  xref       "Appendices~\\ref{a}--\\ref{b}" whose labels resolve to the same appendix -> "Appendix~\\ref{a}"
Checked only (need judgment; reported with file:line):
  duplicate display equations, figure-internal acronyms never defined in text, number claims that
  disagree with the same-named row of a main-text table, floats placed >=2 pages after their first
  reference or after the Conclusion, unresolved references/citations, forbidden placeholders, words
  that appear first in the abstract or conclusion, and internal workflow wording (process).
Acknowledged gaps (Lean's ``sorry``): ``% gate-sorry: <check> — <reason>`` on a flagged line, or the
line above it, turns that located error into a counted sorry; the PDF is still delivered and the
report lists every sorry. ``--no-sorry`` makes them errors again for the submission build.
"""

import sys

sys.dont_write_bytecode = True

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from process_leak import find_latex_process_leaks

ENV_MASK = r"equation|align|gather|multline|eqnarray|algorithmic|tabular|verbatim"
MASK_RES = [
    re.compile(r"(?<!\\)%[^\n]*"),
    re.compile(r"\\begin\{(" + ENV_MASK + r")\*?\}.*?\\end\{\1\*?\}", re.S),
    re.compile(r"\\\[.*?\\\]", re.S),
    re.compile(r"\$\$.*?\$\$", re.S),
    re.compile(r"(?<!\\)\$.*?(?<!\\)\$", re.S),
    re.compile(r"\\(?:label|ref|eqref|cref|Cref|autoref|cite[pt]?|citeyear|citeauthor|includegraphics|url|href|texttt|input|bibliography\w*)\*?(?:\[[^\]]*\])?\{[^}]*\}"),
    re.compile(r"\\[A-Za-z]+"),  # bare command names
]
ACR_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,7}s?\b")
DEFAULT_WHITELIST = set("""PSNR SSIM MAC MACs FLOP FLOPs GPU GPUs CPU CUDA RGB FP16 FP32 FP64 BF16 TF32 IEEE RTX
FFT FFTs CNN CNNs MLP MLPs ICML ICLR CVPR ICCV ECCV AAAI IJCAI TPAMI NeurIPS SO AI ID OOD SGD TBD TODO URL PDF
LLM LLMs RNN RNNs LSTM GRU RMS ODE ODEs PDE PDEs SSM SSMs USA UK API JSON SOTA HDR SiLU ReLU GELU""".split())


@dataclass
class Src:
    path: Path
    scope: str  # abstract | body | back
    text: str
    orig: str = ""


@dataclass
class AppliedEdit:
    path: Path
    start: int
    end: int
    kind: str
    before: str
    after: str


@dataclass
class Report:
    errors: list = field(default_factory=list)
    sorries: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    infos: list = field(default_factory=list)
    fixes: list = field(default_factory=list)
    edits: list[AppliedEdit] = field(default_factory=list)

    def replace(self, src, start, end, new, kind):
        """Keep every edit attached to its final location, even if a later fix expands an acronym."""
        delta = len(new) - (end - start)
        for edit in self.edits:
            if edit.path != src.path or edit.end <= start:
                continue
            if edit.start >= end:
                edit.start += delta
                edit.end += delta
            else:
                edit.start = min(start, edit.start)
                edit.end = max(start + len(new), edit.end + delta)
        self.edits.append(AppliedEdit(src.path, start, start + len(new), kind, src.text[start:end], new))
        src.text = src.text[:start] + new + src.text[end:]


# Term matching keeps \texttt{...} and command names visible so rules such as "(\texttt{ACC})" can match.
TERM_MASK_RES = MASK_RES[:5] + [
    re.compile(r"\\\(.*?\\\)", re.S),
    re.compile(r"\\verb\*?([^\w\s]).*?\1"),
    re.compile(r"\\(?:label|ref|eqref|cref|Cref|autoref|cite[pt]?|citeyear|citeauthor|includegraphics|url|href|input|bibliography\w*)\*?(?:\[[^\]]*\])?\{[^}]*\}"),
]


def masked(text: str, rxs=None, fill=" ") -> str:
    out = list(text)
    for rx in (rxs or MASK_RES):
        for m in rx.finditer(text):
            for i in range(m.start(), m.end()):
                out[i] = fill
    return "".join(out)


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def load_sources(paper: Path, cfg: dict) -> list[Src]:
    main = paper / cfg["main_tex"]
    raw = main.read_text(encoding="utf-8")
    text = re.sub(r"(?<!\\)%[^\n]*", lambda m: " " * len(m.group(0)), raw)
    marker = text.find(cfg.get("main_text_end_marker", "\\label{en:main-text-end}"))
    a0, a1 = text.find("\\begin{abstract}"), text.find("\\end{abstract}")
    srcs = []
    for m in re.finditer(r"\\input\{([^}]+)\}", text):
        p = paper / m.group(1)
        if p.suffix != ".tex":
            p = p.with_suffix(".tex")
        if not p.exists():
            continue
        scope = "abstract" if a0 <= m.start() <= a1 else ("body" if marker < 0 or m.start() < marker else "back")
        t = p.read_text(encoding="utf-8")
        srcs.append(Src(p, scope, t, t))
    return srcs


def case_like(src: str, repl: str) -> str:
    if src.isupper():
        return repl
    words = src.split()
    if len(words) > 1 and all(w[:1].isupper() for w in words if w[:1].isalpha()):
        return " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in repl.split())
    if src[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def term_key(text):
    return " ".join(text.split()).casefold()


def term_registry(cfg):
    aliases = {}
    for rule in cfg.get("terms", []):
        if not isinstance(rule, (list, tuple)) or len(rule) != 2 or not all(isinstance(x, str) and x.strip() for x in rule):
            raise ValueError("each terms entry must contain a nonempty alias and canonical name")
        alias, canonical = rule
        key = term_key(alias)
        if key in aliases and aliases[key] != canonical:
            raise ValueError(f"two canonical names registered for '{alias}'")
        aliases[key] = canonical
    # Resolve approved chains before matching, so a second fix pass cannot change the result.
    for key, value in aliases.items():
        seen = {key}
        while (dest := term_key(value)) in aliases and aliases[dest] != value:
            if dest in seen:
                raise ValueError(f"cycle in approved terms at '{key}'")
            seen.add(dest)
            value = aliases[dest]
        aliases[key] = value
    contexts = {}
    for phrase, record in cfg.get("term_context", {}).items():
        key = term_key(phrase)
        if not key or key in aliases or key in contexts:
            raise ValueError(f"ambiguous approved/contextual term '{phrase}'")
        if not isinstance(record, dict) or not all(isinstance(record.get(k), str) and record[k].strip() for k in ("canonical", "note")):
            raise ValueError(f"term_context '{phrase}' needs a canonical name and a decision note")
        contexts[key] = record
    return aliases, contexts


def fix_terms(srcs, cfg, rep, apply):
    aliases, contexts = term_registry(cfg)
    protected = {term_key(p) for p in cfg.get("protect_phrases", [])}
    names = set(aliases) | set(contexts) | protected
    names.update(term_key(c) for c in (*aliases.values(), *cfg.get("acronyms", {}).values()))
    if not names:
        return
    gap = r"(?:[ \t]+|[ \t]*\r?\n[ \t]*)"
    choices = [gap.join(re.escape(w) for w in name.split()) for name in sorted(names, key=lambda n: (-len(n), n)) if name]
    pattern = re.compile(r"(?<![\w-])(?:" + "|".join(choices) + r")(?![\w-])", re.I)
    observations = {}
    display = {term_key(name): name for name in cfg.get("term_context", {})}
    for s in srcs:
        view = masked(s.text, TERM_MASK_RES, fill="\0")
        code = [m.span(1) for m in re.finditer(r"\\texttt\{([^{}]*)\}", s.text)]
        edits = []
        for m in pattern.finditer(view):
            if any(a <= m.start() and m.end() <= b for a, b in code):
                continue
            old = s.text[m.start():m.end()]
            key = term_key(old)
            where = f"{s.path.name}:{line_of(s.text, m.start())}"
            if key in contexts:
                start = max(s.text.rfind("\n", 0, m.start()) + 1, m.start() - 45)
                end = s.text.find("\n", m.end(), m.end()+65)
                sample = " ".join(s.text[start:(m.end()+65 if end < 0 else end)].split())
                observations.setdefault(key, []).append(f'{where} "{sample}"')
            if key not in aliases:
                continue
            canon = aliases[key]
            if key in protected and term_key(canon) != key:
                continue
            new = canon if key == term_key(canon) else case_like(" ".join(old.split()), canon)
            if old == new:
                continue
            edits.append((m.start(), m.end(), new))
            rep.fixes.append(f"terms  {where}  '{old}' -> '{new}'")
            if not apply:
                rep.errors.append(f"terms  {where}: expected '{new}', run --fix")
        if apply:
            for start, end, new in reversed(edits):
                rep.replace(s, start, end, new, "terms")
    for key, sites in observations.items():
        item = contexts[key]
        rep.infos.append(f"term-context  '{display[key]}' / '{item['canonical']}': {item['note']} "
                         f"(n={len(sites)}) " + " | ".join(sites[:2]))


def fix_acronyms(srcs, cfg, rep, apply):
    acrs = cfg.get("acronyms", {})
    white = DEFAULT_WHITELIST | set(cfg.get("acronym_whitelist", [])) | set(acrs)
    seen = {}
    for s in srcs:
        if s.scope == "back":
            continue
        mk = masked(s.text)
        for m in ACR_RE.finditer(mk):
            t = m.group(0)
            if sum(c.isupper() for c in t) >= 2 and t not in white and t.rstrip("s") not in white:
                seen.setdefault(t, f"{s.path.name}:{line_of(s.text, m.start())}")
    for t, where in seen.items():
        rep.warnings.append(f"acronym  {where}: '{t}' is neither registered in paper_gate.json nor whitelisted")
    for scope in ("abstract", "body"):
        group = [s for s in srcs if s.scope == scope]
        for acr, long in acrs.items():
            first = None
            for s in group:
                mk = masked(s.text)
                m = re.search(r"\b" + re.escape(acr) + r"s?\b", mk)
                if m:
                    first = (s, m)
                    break
            if not first:
                continue
            s, m = first
            pre = s.text[: m.end() + 12]
            defined = re.search(re.escape(long) + r"s?\s*\(\s*(?:\\texttt\{)?" + re.escape(acr) + r"s?\}?\s*[,)]", pre, re.I) or \
                re.search(r"\b" + re.escape(acr) + r"s?\s*\(\s*" + re.escape(long), s.text[m.start(): m.end() + len(long) + 8], re.I)
            # a definition earlier in the same scope (e.g. inside \texttt) also counts
            earlier = "".join(x.text for x in group[: group.index(s)]) + s.text[: m.start()]
            if defined or re.search(re.escape(long) + r"s?\s*\(\s*(?:\\texttt\{)?" + re.escape(acr), earlier, re.I):
                continue
            tok = s.text[m.start():m.end()]
            new = f"{long}{'s' if tok.endswith('s') and not long.endswith('s') else ''} ({tok})"
            rep.fixes.append(f"acronym  {s.path.name}:{line_of(s.text, m.start())}  first {scope} use '{tok}' -> '{new}'")
            if apply:
                rep.replace(s, m.start(), m.end(), new, "acronym")
            else:
                rep.errors.append(f"acronym  {s.path.name}:{line_of(s.text, m.start())}: first {scope} use of {acr} is undefined, run --fix")


def read_aux(paper: Path, cfg: dict) -> dict:
    aux = paper / (Path(cfg["main_tex"]).stem + ".aux")
    labels = {}
    if aux.exists():
        for m in re.finditer(r"\\newlabel\{([^}]+)\}\{\{([^}]*)\}\{([^}]*)\}", aux.read_text(encoding="utf-8", errors="ignore")):
            labels[m.group(1)] = (m.group(2), m.group(3))
    return labels


def fix_xref(srcs, labels, rep, apply):
    plural = {"Appendices": "Appendix", "Sections": "Section", "Tables": "Table", "Figures": "Figure"}
    rx = re.compile(r"\b(Appendices|Sections|Tables|Figures)(~|\s+)\\ref\{([^}]+)\}\s*(?:--|–|-|to)\s*\\ref\{([^}]+)\}")
    for s in srcs:
        for m in reversed(list(rx.finditer(s.text))):
            a, b = labels.get(m.group(3)), labels.get(m.group(4))
            if not a or not b:
                continue
            same = a[0].split(".")[0] == b[0].split(".")[0] if m.group(1) == "Appendices" else a[0] == b[0]
            if same:
                new = f"{plural[m.group(1)]}~\\ref{{{m.group(3)}}}"
                rep.fixes.append(f"xref  {s.path.name}:{line_of(s.text, m.start())}  '{m.group(0)}' -> '{new}' (both resolve to {a[0]}/{b[0]}; check verb agreement)")
                if apply:
                    rep.replace(s, m.start(), m.end(), new, "xref")
                else:
                    rep.errors.append(f"xref  {s.path.name}:{line_of(s.text, m.start())}: self-range {m.group(0)}")


def check_refs(srcs, labels, paper, cfg, rep):
    for s in srcs:
        for m in re.finditer(r"\\(?:ref|eqref|cref|Cref|autoref)\{([^}]+)\}", s.text):
            for key in m.group(1).split(","):
                if labels and key.strip() not in labels:
                    rep.errors.append(f"ref  {s.path.name}:{line_of(s.text, m.start())}: label '{key}' not defined")
    log = paper / (Path(cfg["main_tex"]).stem + ".log")
    if log.exists():
        lt = log.read_text(encoding="utf-8", errors="ignore")
        for pat, kind in ((r"Citation `([^']+)' .*undefined", "citation"), (r"Reference `([^']+)' .*undefined", "reference"),
                          (r"Label `([^']+)' multiply defined", "label multiply defined")):
            for key in sorted(set(re.findall(pat, lt))):
                rep.errors.append(f"latex  {kind}: {key}")
        if re.search(r"^! ", lt, re.M):
            rep.errors.append("latex  compile error in log")


def norm_eq(e: str) -> str:
    e = re.sub(r"\\label\{[^}]*\}|\\nonumber|\\notag", "", e)
    e = re.sub(r"\\[!,;:]|\\(?:big|Big|bigg|Bigg)[lr]?|\\left|\\right|\s+", "", e)
    return e.rstrip(",.")


def check_equations(srcs, cfg, rep):
    allow = set(cfg.get("allow_duplicate_equations", []))
    eqs = []
    for s in srcs:
        if s.scope != "body":
            continue
        for m in re.finditer(r"\\begin\{(equation|align)\*?\}(.*?)\\end\{\1\*?\}", s.text, re.S):
            lab = re.search(r"\\label\{([^}]+)\}", m.group(2))
            eqs.append((s, m.start(), norm_eq(m.group(2)), lab.group(1) if lab else None))
    for i in range(len(eqs)):
        for j in range(i + 1, len(eqs)):
            a, b = eqs[i], eqs[j]
            if len(a[2]) < 20 or a[3] in allow or b[3] in allow:
                continue
            r = SequenceMatcher(None, a[2], b[2]).ratio()
            if r >= 0.92:
                rep.errors.append(f"equation  {b[0].path.name}:{line_of(b[0].text, b[1])} duplicates {a[0].path.name}:{line_of(a[0].text, a[1])}"
                                  f" (similarity {r:.2f}); cite Eq.~\\eqref{{{a[3]}}} instead")


def table_rows(srcs, scope_filter):
    rows = []
    for s in srcs:
        if scope_filter and s.scope not in scope_filter:
            continue
        for m in re.finditer(r"\\begin\{tabular\}.*?\\end\{tabular\}", s.text, re.S):
            body = re.sub(r"\\(toprule|midrule|bottomrule|hline|cmidrule(\([^)]*\))?\{[^}]*\})", "", m.group(0))
            for row in body.split("\\\\"):
                cells = [c.strip() for c in re.split(r"(?<!\\)&", row)]
                if len(cells) < 2:
                    continue
                label = re.sub(r"\\\w+\{([^}]*)\}", r"\1", cells[0]).strip(" {}")
                label = re.sub(r"\\begin\{tabular\}\{[^}]*\}", "", label).strip()
                vals = []
                for c in cells[1:]:
                    c2 = re.sub(r"\\msd\{([^}]*)\}\{([^}]*)\}", r"\1 \2", c)
                    vals += [float(x) for x in re.findall(r"(?<![\w.])\d+\.\d+", c2)]
                if label and vals:
                    rows.append((label, vals, s.scope))
    return rows


def aliases(label):
    out = {re.sub(r"\(.*?\)", "", label).strip(" ,")}
    for inner in re.findall(r"\((.*?)\)", label):
        out |= {p.strip() for p in inner.split(",") if p.strip()[:1].isupper() and p.strip().lower() != "ours"}
    return {a for a in out if len(a) >= 2}


def check_numbers(srcs, rep):
    rows = table_rows(srcs, None)
    names = sorted({a for lab, _, _ in rows for a in aliases(lab)}, key=len, reverse=True)
    for s in srcs:
        if s.scope == "back":
            continue
        mk = masked(s.text.replace("\\,", " ").replace("~", " "))
        plain = s.text.replace("\\,", " ").replace("~", " ")
        for name in names:
            for m in re.finditer(r"(?<![\w-])" + re.escape(name) + r"\s*\(([^()]{0,70})\)", plain):
                if mk[m.start()] == " ":
                    continue
                inner = m.group(1)
                if "dB" not in inner or re.search(r"\bvs\b", inner):
                    continue  # PSNR-style claims only; "a vs. b" has ambiguous referents
                nums = re.findall(r"(?<![\w.+\-])(\d+\.\d+)", inner)
                if not nums:
                    continue
                v = float(nums[0])
                tol = 0.5
                same = [(vals, sc) for lab, vals, sc in rows if name in aliases(lab)]
                main_vals = [x for vals, sc in same if sc == "body" for x in vals]
                exact_main = any(abs(x - v) < 1e-9 for x in main_vals)
                near = sorted({x for x in main_vals if 1e-9 < abs(x - v) <= tol})
                where = f"{s.path.name}:{line_of(s.text, m.start())}"
                if near and not exact_main:
                    rep.errors.append(f"number  {where}: {name} = {nums[0]} but main-text tables list {near} for {name}")
                elif not same or not any(abs(x - v) < 1e-9 for vals, _ in same for x in vals):
                    rep.warnings.append(f"number  {where}: {name} = {nums[0]} not found in any table row named {name}")


SORRY_RE = re.compile(r"%\s*gate-sorry:\s*([a-z][a-z,\s-]*?)\s*(?:—|--|:)\s*(\S.*)$")
THEORY_SORRY_RE = re.compile(r"%\s*gate-sorry:\s*([a-z][a-z_,\s-]*?)\s*(?:—|--|:)\s*(\S.*)$")
LOCATED_RE = re.compile(r"^([a-z-]+)\s+([^\s:]+):(\d+)\b")
RELATIVE_LOCATED_RE = re.compile(r"^([a-z_-]+)\s+([^:]+):(\d+)\b")


def apply_sorries(srcs, rep, allow=True, *, relative_paths=False, checks=None):
    """Lean-style ``sorry``: an acknowledged gap compiles, is counted, and is listed until removed.

    ``% gate-sorry: scope — reason`` on the flagged line, or on the line directly above, turns the
    located errors of the named checks (comma-separated) into sorries. A marker without a reason, or
    one that matches no error, is itself an error, so stale sorries do not pile up. With
    ``allow=False`` (``--no-sorry``, for submission) every sorry stays an error.
    """
    marks = {}  # (file, line) -> (checks, reason, marker line)
    aliases = {}
    marker_pattern = THEORY_SORRY_RE if relative_paths else SORRY_RE
    location_pattern = RELATIVE_LOCATED_RE if relative_paths else LOCATED_RE
    for s in srcs:
        name = s.path.as_posix() if relative_paths else s.path.name
        if relative_paths:
            aliases.setdefault(s.path.name, []).append(name)
        for i, line in enumerate(s.text.splitlines(), start=1):
            if "gate-sorry" not in line:
                continue
            m = marker_pattern.search(line)
            if not m or len(m.group(2).strip()) < 4:
                rep.errors.append(f"sorry  {name}:{i}: write '% gate-sorry: <check> — <reason>'")
                continue
            marked_checks = {c.strip() for c in m.group(1).split(",") if c.strip()}
            if checks is not None:
                marked_checks.intersection_update(checks)
                if not marked_checks:
                    continue
            own_line = line.split("%", 1)[0].strip()
            target = i if own_line else i + 1  # a marker on its own line covers the next line
            key = (name, target)
            if relative_paths and key in marks:
                old_checks, old_reason, _ = marks[key]
                marked_checks |= old_checks
                reason = f"{old_reason} / {m.group(2).strip()}"
            else:
                reason = m.group(2).strip()
            marks[key] = (marked_checks, reason, i)
    used, kept = set(), []
    for err in rep.errors:
        m = location_pattern.match(err)
        key = (m.group(2), int(m.group(3))) if m else None
        mark = marks.get(key) if key else None
        if relative_paths and m and not mark and "/" not in key[0]:
            matches = [(name, key[1]) for name in aliases.get(key[0], [])
                       if (name, key[1]) in marks and m.group(1) in marks[(name, key[1])][0]]
            if len(matches) == 1:
                key = matches[0]
                mark = marks[key]
        if mark and m.group(1) in mark[0]:
            used.add(key)
            if allow:
                rep.sorries.append(f"{err}  [sorry: {mark[1]}]")
                continue
            err = f"{err}  [sorry not allowed with --no-sorry: {mark[1]}]"
        kept.append(err)
    rep.errors = kept
    for key, (checks, _, line) in marks.items():
        if key not in used:
            rep.errors.append(f"sorry  {key[0]}:{line}: marker for {', '.join(sorted(checks))} matches no error; remove it")


def finish_checks(paper, cfg, srcs, rep, *, allow_sorry):
    import theory_gate

    if not cfg.get("theory_gate"):
        rep.errors.extend(theory_gate.single_tex_source_errors(paper, cfg))
        apply_sorries(srcs, rep, allow=allow_sorry)
        return

    settings = cfg["theory_gate"]
    theory_cfg = {**cfg, **settings} if isinstance(settings, dict) else cfg
    overrides = {src.path.relative_to(paper): src.text for src in srcs}
    sources = theory_gate.load_sources(paper, theory_cfg, overrides=overrides)
    theory = theory_gate.check_paper(paper, theory_cfg, sources=sources, defer_sorries=True)
    rep.errors.extend(theory["errors"])
    # Resolve waivers once across both gates, including mixed check names on the same line.
    combined = [Src(path, "", raw) for path, raw in (overrides | sources).items()]
    apply_sorries(combined, rep, allow=allow_sorry, relative_paths=True)


def check_placeholders(srcs, cfg, rep, *, allow_sorry: bool = True):
    allowed = set(cfg.get("allowed_placeholders", ["TBD"])) if allow_sorry else set()
    count = 0
    for s in srcs:
        for m in re.finditer(r"(?<![A-Za-z0-9_])(?:TBD|TODO|XXX)(?![A-Za-z0-9_])|\?\?\?|\\pending\{", s.text):
            tok = "TBD" if m.group(0).startswith("\\pending") else m.group(0)
            if tok in allowed:
                count += 1
            else:
                rep.errors.append(f"placeholder  {s.path.name}:{line_of(s.text, m.start())}: {tok}")
    if count:
        rep.infos.append(f"placeholder  {count} allowed placeholder(s) ({', '.join(sorted(allowed))})")


def check_process(srcs, cfg, rep):
    try:
        allowed = [re.compile(pattern) for pattern in cfg.get("process_leak_allow", [])]
    except re.error as exc:
        raise ValueError(f"invalid process_leak_allow regular expression: {exc}") from exc
    for src in srcs:
        for hit in find_latex_process_leaks(src.text):
            sentence = hit["sentence"]
            if not any(pattern.search(sentence) for pattern in allowed):
                rep.errors.append(f"process  {src.path.name}:{hit['line']}: [{hit['kind']}] {sentence}")


STOP_WORDS = set("""introduce introduced propose proposed present presented achieve achieves achieving reach reaches
reaching complete completing completed replace replaces replaced conclusion conclude evaluation evaluations design
designs capture capturing strong stronger progress retain retains remain remains show shows shown demonstrate
however therefore moreover finally overall future""".split())


def stem(w: str) -> str:
    for suf in ("ations", "ation", "ings", "ing", "ies", "es", "ed", "ly", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def check_new_words(srcs, cfg, rep):
    stop = set(cfg.get("new_word_allowlist", [])) | STOP_WORDS
    body = [s for s in srcs if s.scope in ("body", "back")]
    concl_name = cfg.get("conclusion_file", "conclusion.tex")

    def words(t):
        return {w for w in re.findall(r"[a-z][a-z\-]{4,}", masked(t).lower()) if not w.endswith("-")}

    inner = {stem(w) for s in body if s.path.name != concl_name for w in words(s.text)}
    for s in srcs:
        if s.scope == "abstract" or s.path.name == concl_name:
            new = sorted(w for w in words(s.text) if w not in stop and stem(w) not in inner)
            if new:
                rep.warnings.append(f"new-words  {s.path.name}: not used elsewhere in the body: {', '.join(new[:12])}")


def check_scope(srcs, cfg, paper, rep):
    """Tasks/datasets named in the abstract or conclusion must be introduced in the experimental sections.

    Catches e.g. a conclusion that lists "color Gaussian denoising" as remaining work while no
    experiments/analysis/appendix passage ever mentions it, even as pending. Vocabulary comes from
    semantic.evidence (tasks, datasets, files); exemptions go in scope_allow.
    """
    ev = cfg.get("semantic", {}).get("evidence", {})
    files = set(ev.get("files", []))
    if not files:
        return
    allow = {x.lower() for x in cfg.get("scope_allow", [])}
    concl = cfg.get("conclusion_file", "conclusion.tex")
    heads = [s for s in srcs if s.scope == "abstract" or s.path.name == concl]
    body = " ".join(masked(s.text) for s in srcs if s.path.relative_to(paper).as_posix() in files)
    for axis in ("tasks", "datasets"):
        for label, aliases in ev.get(axis, {}).items():
            if label.lower() in allow:
                continue
            names = sorted({label, *aliases}, key=len, reverse=True)
            rx = re.compile(r"(?<![\w-])(?:" + "|".join(r"\s+".join(map(re.escape, n.split())) for n in names) + r")(?![\w-])", re.I)
            if rx.search(body):
                continue
            for s in heads:
                m = rx.search(masked(s.text))
                if m:
                    rep.errors.append(f"scope  {s.path.name}:{line_of(s.text, m.start())}: '{m.group(0)}' appears here but no "
                                      f"experimental section ({', '.join(sorted(files))}) introduces it, not even as pending work")


def check_figures(srcs, cfg, paper, rep):
    try:
        import fitz
    except ImportError:
        rep.warnings.append("figures  PyMuPDF missing; figure-text acronym check skipped")
        return
    white = DEFAULT_WHITELIST | set(cfg.get("acronym_whitelist", [])) | set(cfg.get("acronyms", {}))
    body_text = "".join(s.text for s in srcs if s.scope in ("body", "abstract"))
    acrs = cfg.get("acronyms", {})
    for s in srcs:
        if s.scope != "body":
            continue
        for m in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", s.text):
            p = paper / m.group(1)
            if p.suffix.lower() != ".pdf" or not p.exists():
                continue
            text = " ".join(pg.get_text() for pg in fitz.open(p))
            toks = {t for t in ACR_RE.findall(text) if sum(c.isupper() for c in t) >= 2}
            unknown = sorted(t for t in toks if t not in white and t.rstrip("s") not in white and t[:-1] not in toks)
            if unknown:
                rep.errors.append(f"figure  {p.name}: acronyms in the figure not registered or defined: {', '.join(unknown)}")
            undefined = sorted(t for t in toks if t in acrs and not re.search(re.escape(acrs[t]) + r"s?\s*\(\s*(?:\\texttt\{)?" + t, body_text, re.I)
                               and not re.search(t + r"\s*\(\s*" + re.escape(acrs[t]), body_text, re.I))
            if undefined:
                rep.errors.append(f"figure  {p.name}: {', '.join(undefined)} appear in the figure but are never spelled out in the text or caption")


def check_floats(paper, cfg, srcs, labels, rep):
    pdf = paper / cfg.get("pdf", Path(cfg["main_tex"]).stem + ".pdf")
    try:
        import fitz
    except ImportError:
        return
    if not pdf.exists() or not labels:
        return
    doc = fitz.open(pdf)
    pages = []
    for pg in doc:
        blocks = [b[4].strip() for b in pg.get_text("blocks")]
        pages.append(blocks)
    concl = labels.get(cfg.get("conclusion_label", "sec:conclusion"))
    concl_page = int(concl[1]) if concl and concl[1].isdigit() else None
    body_labels = {lab for s in srcs if s.scope == "body" for lab in re.findall(r"\\label\{((?:fig|tab):[^}]+)\}", s.text)}
    for lab in sorted(body_labels):
        num, page = labels.get(lab, ("", ""))
        if not page.isdigit():
            continue
        page = int(page)
        kind = "Figure" if lab.startswith("fig") else "Table"
        cap = re.compile(rf"^{kind}\s+{re.escape(num)}[.:]")
        ref = re.compile(rf"\b{kind}s?\s+(?:\d+\s*(?:,|and)\s*)*{re.escape(num)}\b" if kind == "Table" else rf"\bFigures?\s+{re.escape(num)}\b")
        first = next((i + 1 for i, bl in enumerate(pages) for b in bl if not cap.match(b) and ref.search(b.replace("\n", " "))), None)
        if first is None:
            rep.warnings.append(f"float  {kind} {num} ({lab}) is never referenced in the text")
            continue
        if page - first >= 2:
            rep.errors.append(f"float  {kind} {num} sits on p{page}, {page - first} pages after its first reference (p{first})")
        if concl_page and page > concl_page and lab not in cfg.get("allow_floats_after_conclusion", []):
            rep.errors.append(f"float  {kind} {num} sits on p{page}, after the Conclusion (p{concl_page})")


def check_layout(paper: Path, cfg: dict, labels: dict, rep: Report, *, render_pages: bool = False):
    """Check overfull hbox, venue page limit, margins, sparse final page, and optional PNG page previews."""
    venue = str(cfg.get("venue", "arxiv")).lower()
    page_limit = cfg.get("page_limit")
    overfull_pt = float(cfg.get("overfull_pt", 6.0))
    stem = Path(cfg["main_tex"]).stem
    log_path = paper / f"{stem}.log"
    if log_path.is_file():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"Overfull \\hbox \(([0-9.]+)pt too wide\)", log_text):
            pts = float(m.group(1))
            if pts < overfull_pt:
                continue
            msg = f"layout  {log_path.name}: Overfull \\hbox ({pts:.1f}pt too wide)"
            if venue != "arxiv" and pts >= 12.0:
                rep.errors.append(msg)
            else:
                rep.warnings.append(msg)

    pdf = paper / cfg.get("pdf", f"{stem}.pdf")
    try:
        import fitz
    except ImportError:
        return
    if not pdf.is_file():
        return
    doc = fitz.open(pdf)
    end_marker = str(cfg.get("main_text_end_marker") or "")
    end_match = re.search(r"\\label\{([^}]+)\}", end_marker)
    end_label = end_match.group(1) if end_match else cfg.get("conclusion_label", "sec:conclusion")
    end_entry = labels.get(end_label)
    main_pages = int(end_entry[1]) if end_entry and str(end_entry[1]).isdigit() else len(doc)
    main_pages = max(1, min(main_pages, len(doc)))

    if page_limit is not None and main_pages > int(page_limit):
        rep.errors.append(f"layout  main text uses {main_pages} pages, exceeding {venue} limit {page_limit}")

    for idx in range(main_pages):
        pg = doc[idx]
        w = pg.rect.width
        bad = [b for b in pg.get_text("blocks") if b[4].strip() and (b[0] < 10.0 or b[2] > w - 10.0)]
        if bad:
            rep.warnings.append(f"layout  p{idx + 1}: {len(bad)} text/float block(s) close to page edge (<10pt)")

    if venue != "arxiv" and main_pages >= 2:
        last_pg = doc[main_pages - 1]
        blocks = [b for b in last_pg.get_text("blocks") if b[4].strip()]
        if blocks:
            span = max(b[3] for b in blocks) - min(b[1] for b in blocks)
            if span < 0.12 * last_pg.rect.height:
                rep.warnings.append(f"layout  p{main_pages}: trailing main page uses <12% height (consider tightening)")

    if render_pages or (venue != "arxiv" and cfg.get("render_layout_preview", False)):
        out_dir = paper / ".gate_cache" / "layout_preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        count = min(main_pages, 12)
        for idx in range(count):
            pix = doc[idx].get_pixmap(dpi=130)
            pix.save(str(out_dir / f"page_{idx + 1:02d}.png"))
        rep.infos.append(f"layout-preview  rendered {count} page PNG(s) in .gate_cache/layout_preview/ for visual review")


def compile_paper(paper: Path, cfg: dict, rep: Report) -> bool:
    cmd = list(cfg.get("compile", ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", cfg["main_tex"]]))
    if cmd and cmd[0] == "latexmk" and shutil.which("latexmk") is None and os.name == "nt":
        win_mks = sorted(Path("C:/texlive").glob("*/bin/windows/latexmk.exe"), reverse=True)
        if win_mks:
            cmd[0] = str(win_mks[0])
    for _ in range(int(cfg.get("compile_passes", 2))):
        r = subprocess.run(cmd, cwd=paper, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        if r.returncode != 0:
            rep.errors.append(f"compile  {' '.join(cmd)} exited {r.returncode}")
            return False
    return True


def run_checks(paper, cfg, srcs, rep, apply_fix, *, allow_sorry: bool = True):
    labels = read_aux(paper, cfg)
    fix_terms(srcs, cfg, rep, apply_fix)
    fix_acronyms(srcs, cfg, rep, apply_fix)
    fix_xref(srcs, labels, rep, apply_fix)
    check_refs(srcs, labels, paper, cfg, rep)
    check_equations(srcs, cfg, rep)
    check_numbers(srcs, rep)
    check_placeholders(srcs, cfg, rep, allow_sorry=allow_sorry)
    check_process(srcs, cfg, rep)
    check_new_words(srcs, cfg, rep)
    check_scope(srcs, cfg, paper, rep)
    check_figures(srcs, cfg, paper, rep)
    return labels


WRITER_ACTION = ("请撰稿模型读回改后的句子，核对技术含义、指代和单复数。"
                 "后续写作沿用已确认的规范名。遇到含义不同的用法，请核对该处原文和术语表。")
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])|\n[ \t]*\n")


def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        tmp = Path(handle.name)
    try:
        tmp.write_bytes(data)
        if path.exists():
            shutil.copymode(path, tmp)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def save_autofixes(paper, changed, edits):
    sources = {s.path: s for s in changed}
    records = []
    for edit in sorted(edits, key=lambda e: (e.path.as_posix(), e.start)):
        if edit.path not in sources:
            continue
        text = sources[edit.path].text
        left = 0
        for sep in SENTENCE_BREAK.finditer(text, 0, edit.start):
            left = sep.end()
        sep = SENTENCE_BREAK.search(text, edit.end)
        right = sep.start() if sep else len(text)
        records.append({
            "kind": edit.kind, "file": edit.path.relative_to(paper).as_posix(),
            "line": line_of(text, edit.start), "column": edit.start - text.rfind("\n", 0, edit.start),
            "before": edit.before, "after": edit.after,
            "context_after": " ".join(text[left:right].split()),
        })
    history = paper / ".gate_backup"
    history.mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=dt.datetime.now().strftime("%Y%m%d-%H%M%S-"), dir=history))
    receipt = backup / "writer_review.json"
    payload = {"event": "PAPER_GATE_AUTOFIX_APPLIED", "action": WRITER_ACTION, "edits": records}
    written = {}
    try:
        for s in changed:
            dest = backup / s.path.resolve().relative_to(paper.resolve())
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s.path, dest)
            if dest.read_text(encoding="utf-8") != s.orig:
                raise RuntimeError(f"{s.path} changed during the check")
        for s in changed:
            original = (backup / s.path.resolve().relative_to(paper.resolve())).read_bytes()
            if s.path.read_bytes() != original:
                raise RuntimeError(f"{s.path} changed during the check")
            data = s.text.encode("utf-8")
            if b"\r\n" in original:
                data = data.replace(b"\n", b"\r\n")
            atomic_write(s.path, data)
            written[s.path] = data
        atomic_write(receipt, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        for path, data in written.items():
            if path.read_bytes() == data:
                atomic_write(path, (backup / path.resolve().relative_to(paper.resolve())).read_bytes())
        raise
    return receipt.relative_to(paper).as_posix(), records


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paper_dir", type=Path)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--fix", action="store_true", help="apply the safe auto-fixes (terms, acronyms, xref)")
    ap.add_argument("--compile", action="store_true", help="compile after fixing, then run float/ref checks on the fresh PDF")
    ap.add_argument("--no-sorry", action="store_true",
                    help="treat every '%% gate-sorry' marker as an error (use for the submission build)")
    ap.add_argument("--layout-check", action="store_true",
                    help="run PDF/log layout checks (page limit, overfull hbox, margins) even without --compile")
    ap.add_argument("--render-pages", action="store_true",
                    help="render main-text page PNG previews to .gate_cache/layout_preview/ for visual review")
    ap.add_argument("--semantic", action="store_true",
                    help="also run the embedding layer (paper_gate_semantic.py): restatements, synonym candidates, unsupported abstract/conclusion sentences")
    a = ap.parse_args()
    paper = a.paper_dir.resolve()
    cfg = json.loads((a.config or paper / "paper_gate.json").read_text(encoding="utf-8"))
    srcs = load_sources(paper, cfg)

    rep = Report()
    try:
        run_checks(paper, cfg, srcs, rep, a.fix, allow_sorry=not a.no_sorry)
    except ValueError as exc:
        ap.error(str(exc))
    changed = [s for s in srcs if s.text != s.orig]
    writer_notice = []
    if a.fix and changed:
        receipt, records = save_autofixes(paper, changed, rep.edits)
        print(f"PAPER_GATE_AUTOFIX_APPLIED  edits={len(records)}  review={receipt}  请撰稿模型读回改动处。", flush=True)
        writer_notice = ["\n## 撰稿模型请复核本次自动替换", WRITER_ACTION,
                         f"共 {len(records)} 处。逐项记录和改后原句见 {receipt}。"]
        for entry in records[:6]:
            writer_notice.append(f"- {entry['file']}:{entry['line']}  {entry['before']!r} → {entry['after']!r}")
            writer_notice.append(f"  改后原句为「{entry['context_after']}」")
        if len(records) > 6:
            writer_notice.append(f"其余 {len(records)-6} 处见本次逐项记录。")
        rep.infos.append(f"backup  originals of {len(changed)} file(s) in {Path(receipt).parent.as_posix()}")

    if a.compile:
        if compile_paper(paper, cfg, rep):
            # re-check on the compiled state (labels, log, floats); fixes are already applied
            post = Report()
            srcs = load_sources(paper, cfg)
            labels = run_checks(paper, cfg, srcs, post, False, allow_sorry=not a.no_sorry)
            check_floats(paper, cfg, srcs, labels, post)
            check_layout(paper, cfg, labels, post, render_pages=a.render_pages)
            rep.errors = [e for e in rep.errors if e.startswith("compile")] + post.errors
            rep.warnings = post.warnings
            rep.infos = [i for i in rep.infos if not i.startswith(("placeholder", "term-context"))] + post.infos
            finish_checks(paper, cfg, srcs, rep, allow_sorry=not a.no_sorry)
            # deliver_as: one path or a list; relative to the paper, or absolute (e.g. an Obsidian library)
            deliver = cfg.get("deliver_as")
            targets = [deliver] if isinstance(deliver, str) else list(deliver or [])
            for target in targets:
                if rep.errors:
                    rep.infos.append(f"deliver  NOT copied to {target} because errors remain")
                    continue
                dest = paper / target
                src_pdf = paper / cfg.get("pdf", Path(cfg["main_tex"]).stem + ".pdf")
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_pdf, dest)
                except OSError as exc:  # an unmounted drive must not fail the gate, but a failed copy on a mounted dir must
                    if dest.anchor and not Path(dest.anchor).exists():
                        rep.warnings.append(f"deliver  could not copy to {target}: {exc}")
                    else:
                        rep.errors.append(f"deliver  failed to copy PDF to {target}: {exc}")
                    continue
                note = f" with {len(rep.sorries)} sorry" if rep.sorries else ""
                rep.infos.append(f"deliver  copied PDF to {target}{note}")
        else:
            finish_checks(paper, cfg, srcs, rep, allow_sorry=not a.no_sorry)
    else:
        labels = read_aux(paper, cfg)
        check_floats(paper, cfg, srcs, labels, rep)
        if a.layout_check or a.render_pages:
            check_layout(paper, cfg, labels, rep, render_pages=a.render_pages)
        finish_checks(paper, cfg, srcs, rep, allow_sorry=not a.no_sorry)

    if a.semantic:  # warnings only; never changes the exit code
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import paper_gate_semantic
        paper_gate_semantic.run(load_sources(paper, cfg), cfg, paper, rep, masked, line_of)

    lines =[f"# paper_gate  {paper.name}  {dt.datetime.now():%Y-%m-%d %H:%M}",
             f"errors {len(rep.errors)} | sorry {len(rep.sorries)} | warnings {len(rep.warnings)} | auto-fixes {len(rep.fixes) if a.fix else 0}"]
    lines.extend(writer_notice)
    for title, items in (("FIXED" if a.fix else "FIXABLE", rep.fixes), ("ERROR", rep.errors), ("SORRY", rep.sorries), ("WARN", rep.warnings), ("INFO", rep.infos)):
        if items:
            lines.append(f"\n## {title}")
            lines += [f"- {x}" for x in items]
    out = "\n".join(lines) + "\n"
    # a second config such as paper_gate_zh.json writes paper_gate_report_zh.md, so builds do not overwrite each other
    suffix = a.config.stem[len("paper_gate"):] if a.config and a.config.stem.startswith("paper_gate") else ""
    (paper / f"paper_gate_report{suffix}.md").write_text(out, encoding="utf-8")
    print(out)
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())

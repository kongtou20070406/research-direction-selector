"""Check LaTeX theory, evidence bindings, and claim framing without a model."""

import argparse
from bisect import bisect_right
import json
from os import walk
from os.path import normpath
from pathlib import Path
import re
from types import SimpleNamespace

from latex_text import _group, _skip_options, latex_to_text


CHECKS = (
    "intuition", "symbol_budget", "proof_sketch", "method_equations",
    "abstract_lead", "lean_bindings", "zh_glossary", "defensive_hedge",
    "abstract_budget", "title_alignment", "table_cell_budget", "unexplained_shorthand",
    "section_separation", "empty_theorem_body", "symbol_aliases", "verbatim_algorithm",
    "single_tex_source",
)
NON_CODE = re.compile(
    r"(?<!\\)(?P<escapes>(?:\\\\)*)%[^\n]*"
    r"|\\begin\{(?P<literal>verbatim\*?|Verbatim\*?|lstlisting|minted)\}"
    r"(?P<contents>.*?)\\end\{(?P=literal)\}", re.S,
)
INPUT = re.compile(r"\\(?:input|include)\s*\{([^{}]+)\}")
THEOREM = re.compile(r"\\begin\{(theorem|proposition)\*?\}(?:\s*\[[^\]]*\])?(.*?)\\end\{\1\*?\}", re.S)
THEOREM_BODY = re.compile(THEOREM.pattern.replace("theorem|proposition", "theorem|proposition|lemma"), re.S)
PROOF = re.compile(r"\\begin\{proof\*?\}(?:\s*\[[^\]]*\])?(.*?)\\end\{proof\*?\}", re.S)
ROW_ENVS = {"align", "alignat", "flalign", "gather", "eqnarray"}
DISPLAY_ENVS = "|".join(sorted(ROW_ENVS | {"equation", "multline", "displaymath"}))
DISPLAY_PATTERN = (
    r"\\begin\{(?P<environment>(?:" + DISPLAY_ENVS + r")\*?)\}(?P<body>.*?)\\end\{(?P=environment)\}"
    r"|\\\[.*?\\\]|(?<!\\)\$\$.*?(?<!\\)\$\$"
)
DISPLAY = re.compile(DISPLAY_PATTERN, re.S)
MATH = re.compile(DISPLAY_PATTERN + r"|\\\(.*?\\\)|(?<![\\$])\$(?!\$).*?(?<!\\)\$", re.S)
PROSE_COMMAND = re.compile(
    r"\\(?:begin|end|label|cite\w*|ref|eqref|cref|Cref|autoref|url|includegraphics)\*?"
    r"(?:\s*\[[^\]]*\]){0,2}\s*\{[^{}]*\}|\\[a-zA-Z]+\*?|[{}]"
)
SECTION = re.compile(
    r"\\(?P<abstract>begin|end)\{abstract\}|(?P<appendix>\\appendix\b|\\begin\{appendices\})"
    r"|\\(?P<level>(?:sub){0,2}section)\b\*?(?:\s*\[[^\]]*\])?\s*\{(?P<title>(?:[^{}]|\{[^{}]*\})*)\}"
)
HEADINGS = {
    "excluded": re.compile(r"\b(?:discussion|limitations?|append(?:ix|ices)|supplement\w*)\b|讨论|局限|局限性|附录|补充材料", re.I),
    "abstract": re.compile(r"\babstract\b|摘要", re.I),
    "intro": re.compile(r"\bintro(?:duction)?\b|引言|绪论", re.I),
    "method": re.compile(r"\bmethod(?:s|ology)?\b|方法", re.I),
    "experiments": re.compile(r"\b(?:experiments?|results?|evaluation)\b|实验|结果|评测", re.I),
    "other": re.compile(r"\b(?:related work|conclusions?|references)\b|相关工作|结论", re.I),
}
HEDGES = (
    "merely a testbed", "only a diagnostic tool", "narrow conclusion", "only a preliminary attempt",
    "cannot conclude", "仅作为测试工具", "窄结论", "只能视为初步尝试", "尚不足以证明", "仅是初步探索",
)
HEDGE = re.compile("|".join(
    r"\b" + r"\s+".join(map(re.escape, phrase.split())) + r"\b" if phrase.isascii()
    else re.escape(phrase)
    for phrase in HEDGES
), re.I)
HAN = re.compile(r"[\u3400-\u9fff]")
ITEM = re.compile(r"\\item\b(?:\s*\[[^\]]*\])?")
ABBREVIATION = re.compile(r"\b(?:e\.g\.|i\.e\.|et al\.|(?:Fig|Eq|Sec|Tab|Thm|Dr|vs)\.)", re.I)
SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)|[。！？]+")
MATH_TOKEN = re.compile(r"\\(?:[a-zA-Z]+|.)|[^\W\d_]|[_^{}]")
MATH_TEXT = re.compile(r"\\(?:text|operatorname)\*?\s*\{(?:[^{}]|\{[^{}]*\})*\}")
OPERATORS = set(r"""
\frac \dfrac \tfrac \sqrt \left \right \middle \big \Big \bigg \Bigg
\bigl \bigr \Bigl \Bigr \biggl \biggr \Biggl \Biggr \binom
\mathbb \mathcal \mathscr \mathfrak \mathbf \mathrm \mathsf \mathit \mathtt \bm \boldsymbol
\hat \widehat \bar \overline \underline \tilde \widetilde \vec \dot \ddot \overbrace \underbrace
\cdot \times \div \pm \mp \sum \prod \int \iint \oint \lim \log \ln \exp \sin \cos \tan
\min \max \inf \sup \arg \det \ker \gcd \mod \bmod \pmod
\in \notin \subset \subseteq \supset \supseteq \cup \cap \setminus \wedge \vee \neg
\le \leq \ge \geq \ne \neq \equiv \sim \simeq \approx \propto \cong
\to \gets \mapsto \rightarrow \leftarrow \leftrightarrow \Rightarrow \Leftarrow \Leftrightarrow
\implies \iff \forall \exists \colon \mid \vert \Vert \lvert \rvert \lVert \rVert
\langle \rangle \ldots \cdots \dots \vdots \ddots \quad \qquad \enspace
\displaystyle \textstyle \scriptstyle \scriptscriptstyle \phantom \hspace \vphantom
\notag \nonumber \limits \nolimits
""".split())


def blank(value):
    return re.sub(r"[^\n]", " ", value)


def masked(pattern, text):
    return pattern.sub(lambda match: blank(match[0]), text)


def tex_code(text, *, keep_verbatim=False):
    def clean_block(match):
        if match["escapes"] is not None:
            escapes = match["escapes"]
            return escapes + blank(match[0][len(escapes):])
        if keep_verbatim and match["literal"].lower().startswith("verbatim"):
            start, end = match.span("contents")
            return text[match.start():start] + blank(match["contents"]) + text[end:match.end()]
        return blank(match[0])

    return NON_CODE.sub(clean_block, text)


def names(value):
    return [value] if isinstance(value, str) else value or []


def input_path(path, target, sources):
    target = Path(target)
    if target.suffix != ".tex":
        target = target.with_name(target.name + ".tex")
    for candidate in (Path(normpath(path.parent / target)), Path(normpath(target))):
        if candidate in sources:
            return candidate
    return Path(normpath(path.parent / target))


def load_sources(paper, cfg, *, overrides=None):
    """Read configured LaTeX files and their local inputs in declaration order."""
    paper = paper.resolve()
    overrides = overrides or {}
    found = {}
    main_tex = cfg.get("main_tex")
    if not cfg.get("sources") and main_tex and (paper / main_tex).is_file():
        found[Path(main_tex)] = None
    patterns = cfg.get("sources") or ["sections_en/*.tex", "sections_zh/*.tex", "*.tex"]
    for pattern in [*names(patterns), *names(cfg.get("method_files"))]:
        path = Path(pattern)
        if path.is_absolute():
            path = path.relative_to(paper)
        for source in sorted(paper.glob(path.as_posix())):
            if source.is_file() and source.suffix == ".tex":
                found[source.relative_to(paper)] = None

    texts = {}

    def load(rel):
        absolute = (paper / rel).resolve()
        if not absolute.is_relative_to(paper):
            return
        rel = absolute.relative_to(paper)
        if rel in texts or not absolute.is_file():
            return
        texts[rel] = overrides[rel] if rel in overrides else absolute.read_text(encoding="utf-8-sig")
        for match in INPUT.finditer(tex_code(texts[rel])):
            target = input_path(rel, match[1], found | texts)
            if not (paper / target).is_file():
                target = input_path(Path("."), match[1], {})
            load(target)

    for rel in found:
        load(rel)
    return texts


def heading(title):
    return next((kind for kind, pattern in HEADINGS.items() if pattern.search(title)), None)


def is_figure_source(path, raw):
    if path.stem.casefold().startswith(("fig", "tikz")) or any(
        part.casefold() == "figures" for part in path.parts[:-1]
    ):
        return True
    text = tex_code(raw)
    return r"\begin{tikzpicture}" in text and not re.search(r"\\section\b", text)


class Document:
    """An input-expanded document with diagnostics mapped to the original files."""

    def __init__(self, paths, sources, inputs):
        self.offsets = []
        self.segments = []
        self.starts = []
        parts = []
        size = 0

        def append(path, start, end):
            nonlocal size
            if end > start:
                self.offsets.append(size)
                self.segments.append((path, start, sources[path]))
                value = sources[path][start:end]
                parts.append(value)
                size += len(value)

        def emit(path, stack):
            nonlocal size
            self.starts.append((size, path))
            start = 0
            for match, target in inputs[path]:
                if target not in sources or target in stack:
                    continue
                append(path, start, match.start())
                emit(target, {*stack, target})
                start = match.end()
            append(path, start, len(sources[path]))
            parts.append("\n\n")
            size += 2

        for path in paths:
            emit(path, {path})
        self.text = "".join(parts)

    def locate(self, offset):
        index = max(0, bisect_right(self.offsets, offset) - 1)
        path, start, text = self.segments[index]
        return path, text.count("\n", 0, start + offset - self.offsets[index]) + 1


def documents(sources, *, keep_verbatim=False):
    code = {path: tex_code(text, keep_verbatim=keep_verbatim) for path, text in sources.items()}
    inputs = {
        path: [(match, input_path(path, match[1], code)) for match in INPUT.finditer(text)]
        for path, text in code.items()
    }
    referenced = {target for edges in inputs.values() for _, target in edges}
    groups = {}
    for path, text in code.items():
        if path in referenced:
            continue
        # Each manuscript with its own preamble is independent. Loose sections share their language folder.
        is_main = bool(inputs[path]) or r"\begin{document}" in text
        key = (path if is_main else path.parent, is_main)
        groups.setdefault(key, []).append(path)
    roots = groups.values() if groups else [[path] for path in code]
    return [Document(paths, code, inputs) for paths in roots]


def regions(document, cfg):
    method_files = names(cfg.get("method_files"))
    events = []
    for start, path in document.starts:
        stem = path.stem.lower()
        configured = stem.startswith("03_method") or any(path.match(pattern) for pattern in method_files)
        scope = "method" if configured else "intro" if stem.startswith("01_introduction") else (
            heading(path.stem.replace("_", " "))
        )
        events.append((start, "method_file" if configured else "file", scope))
    for match in SECTION.finditer(document.text):
        if match["appendix"]:
            events.append((match.start(), "appendix", "excluded"))
        elif match["abstract"]:
            if match["abstract"] == "begin":
                events.append((match.end(), "abstract", "abstract"))
            else:
                events.append((match.start(), "end_abstract", None))
        else:
            events.append((match.start(), match["level"], heading(match["title"])))
    scopes = []
    previous, scope, in_abstract, appendix = 0, None, False, len(document.text)
    file_scope = None
    section_scope = None
    for offset, event, next_scope in sorted(events, key=lambda event: event[0]):
        if appendix < len(document.text):
            break
        if event in {"file", "method_file"}:
            file_scope = next_scope
            if in_abstract or next_scope is None:
                continue
        elif event == "section" and next_scope is None:
            next_scope = file_scope
        if event in {"subsection", "subsubsection"}:
            next_scope = "excluded" if next_scope == "excluded" else section_scope
        else:
            section_scope = next_scope
        if offset > previous:
            scopes.append((previous, offset, scope))
        previous, scope = offset, next_scope
        if event in {"abstract", "end_abstract"}:
            in_abstract = event == "abstract"
        if event == "appendix":
            appendix = offset
    scopes.append((previous, len(document.text), scope))
    return scopes, appendix


def prose(text):
    return masked(PROSE_COMMAND, masked(MATH, text))


def sentence_spans(text):
    start = 0
    scan = masked(ABBREVIATION, text)
    for match in SENTENCE_END.finditer(scan):
        if re.search(r"[^\W\d_]", text[start:match.end()]):
            yield start, match.end(), True
        start = match.end()
    if re.search(r"[^\W\d_]", text[start:]):
        yield start, len(text), False


def has_intuition(prefix):
    splits = re.split(
        r"\n[ \t]*\n|\\par\b|\\(?:begin|end)\{(?:proof|theorem|proposition)\*?\}"
        r"|\\(?:sub){0,2}section\b\*?(?:\s*\[[^\]]*\])?\s*\{[^{}]*\}",
        prefix.rstrip(),
    )
    paragraph = splits[-1].strip()
    visible = prose(paragraph)
    for start, end, completed in sentence_spans(visible):
        sentence = visible[start:end]
        if not completed or (len(re.findall(r"[A-Za-z]+", sentence)) < 3 and len(HAN.findall(sentence)) < 4):
            continue
        original = paragraph[start:end].strip()
        math_chars = sum(len(match[0]) for match in MATH.finditer(original))
        if math_chars < 0.35 * len(original):
            return True
    return False


def theorem_symbols(body):
    active = set()
    nested = 0
    for match in MATH.finditer(body):
        formula = match["body"] if match["body"] is not None else match[0]
        formula = re.sub(r"\\(?:begin|end|label|tag)\{[^{}]*\}", "", formula)
        depth, groups, pending = 0, [], False
        for token in MATH_TOKEN.findall(MATH_TEXT.sub("", formula)):
            if token in {"^", "_"}:
                pending = True
                nested = max(nested, len(groups) + 1)
                continue
            if token == "{":
                depth += 1
                if pending:
                    groups.append(depth)
            elif token == "}":
                if groups and groups[-1] == depth:
                    groups.pop()
                depth -= 1
            elif token not in OPERATORS and token[-1].isalpha():
                active.add(token)
            pending = False
    return sorted(active), nested


def proof_length(body):
    parts = ITEM.split(body)
    counts = [sum(1 for _ in sentence_spans(prose(part))) for part in parts]
    return counts[0] + sum(max(1, count) for count in counts[1:]), len(list(DISPLAY.finditer(body)))


def numbered_rows(display):
    environment = display["environment"]
    if environment is None or environment.endswith("*") or environment == "displaymath":
        return 0
    body = display["body"]
    if environment not in ROW_ENVS:
        return int(not re.search(r"\\(?:notag|nonumber)\b", body))
    # Mask inner math layouts so their line breaks and tags stay inside the outer row.
    body = re.sub(r"\\begin\{([^{}]+)\}.*?\\end\{\1\}", "<inner layout>", body, flags=re.S)
    return sum(
        bool(row.strip()) and not re.search(r"\\(?:notag|nonumber)\b", row)
        for row in re.split(r"\\\\(?:\s*\[[^\]]*\])?", body)
    )


ABSTRACT_WORD = re.compile(r"[A-Za-z0-9]+(?:[-'/][A-Za-z0-9]+)*")
SCORE = re.compile(r"(?<![\w.])(?:\d+\.\d+(?:\s*\\?%)?|\d+\s*\\?%)")
PRECISE_SCORE = re.compile(r"(?<![\w.])\d+\.\d{3,}(?!\d)")
TABLE = re.compile(r"\\begin\{(table\*?)\}(.*?)\\end\{\1\}", re.S)
TABULAR = re.compile(r"\\begin\{(tabular\*?|tabularx|tabulary|longtable)\}")
CAPTION = re.compile(r"\\caption\b\*?")
SHORTHAND = re.compile(r"\bmode\s+[A-Z]\d+\b")
SHORTHAND_DEFINITION = re.compile(
    r"(?:[\w\u3400-\u9fff][ \t]*\(\s*(?P<inside>mode\s+[A-Z]\d+)\s*\)"
    r"|(?P<before>mode\s+[A-Z]\d+)\s*\([^()\n]*[^()\s\d][^()\n]*\))"
)


def captions(text):
    for match in CAPTION.finditer(text):
        if arg := _group(text, _skip_options(text, match.end()), "{"):
            yield arg


def numeric_cells(text):
    for match in TABULAR.finditer(text):
        start = _skip_options(text, match.end())
        for _ in range(1 if match[1] in {"tabular", "longtable"} else 2):
            arg = _group(text, start, "{")
            if arg:
                start = _skip_options(text, arg[1])
        end = text.find(r"\end{" + match[1] + "}", start)
        if end < 0:
            continue
        body = re.sub(r"\\(?:cmidrule|cline)(?:\([^)]*\))?\s*\{[^{}]*\}", "", text[start:end])
        cells = re.split(r"(?<!\\)&|\\\\(?:\s*\[[^\]]*\])?|\\tabularnewline\b", body)
        count = sum(bool(re.search(r"(?<!\w)\d", latex_to_text(cell))) for cell in cells)
        yield match.start(), count


def check_structure(document, cfg, scopes, appendix, error):
    text = document.text
    introduction = set()
    abstract_regions = []
    main_tables = list(TABLE.finditer(text, 0, appendix))
    for start, end, scope in scopes:
        region = text[start:end]
        if scope == "intro":
            introduction.update(PRECISE_SCORE.findall(region))
        elif scope == "abstract" and region.strip():
            if abstract_regions and abstract_regions[-1][1] == start:
                abstract_regions[-1] = (abstract_regions[-1][0], end)
            else:
                abstract_regions.append((start, end))
    for start, end in abstract_regions:
        region = text[start:end]
        visible = latex_to_text(region)
        scores = SCORE.findall(visible)
        overlap = sorted(set(PRECISE_SCORE.findall(visible)) & introduction)
        location = document.locate(start + len(region) - len(region.lstrip()))
        for count, limit, label in (
            (len(ABSTRACT_WORD.findall(visible)), cfg.get("max_abstract_words", 220), "EN words"),
            (len(HAN.findall(visible)), cfg.get("max_abstract_zh_chars", 380), "ZH Han characters"),
            (len(scores), cfg.get("max_abstract_decimals", 6), "decimals/percentages"),
        ):
            if count > limit:
                error("abstract_budget", *location, f"{count} {label} exceeds {limit}")
        if len(overlap) >= 4:
            error("abstract_budget", *location, "Abstract and Introduction repeat: " + ", ".join(overlap))

    main = cfg.get("main_tex")
    selected_main = main and Path(main) == document.starts[0][1]
    if cfg.get("title_pillars") and document.offsets and (
        selected_main or not main or cfg.get("sources")
    ):
        experiment_tables = [table for table in main_tables
                             if any(scope == "experiments" and start <= table.start() < end
                                    for start, end, scope in scopes)]
        table_captions = [
            [latex_to_text(body).casefold() for body, _ in captions(match[2])]
            for match in experiment_tables
        ]
        anchor = experiment_tables[0].start() if experiment_tables else document.offsets[0]
        for pillar in cfg["title_pillars"]:
            keywords = [r"\s+".join(map(re.escape, keyword.casefold().split())) for keyword in pillar["keywords"]]
            count = sum(any(re.search(r"(?<![a-z0-9])(?:" + keyword + r")(?![a-z0-9])", caption)
                            for caption in table for keyword in keywords) for table in table_captions)
            minimum = pillar.get("min_tables", 1)
            if count < minimum:
                error("title_alignment", *document.locate(anchor),
                      f"{pillar['name']}: {count} Experiments tables, requires {minimum}")

    limit = cfg.get("max_table_numeric_cells", 48)
    tables = []
    cursor = 0
    for table in main_tables:
        tables.extend((cursor + offset, count) for offset, count in numeric_cells(text[cursor:table.start()]))
        tables.append((table.start(), sum(count for _, count in numeric_cells(table[2]))))
        cursor = table.end()
    tables.extend((cursor + offset, count) for offset, count in numeric_cells(text[cursor:appendix]))
    for offset, count in tables:
        if count > limit:
            error("table_cell_budget", *document.locate(offset), f"{count} numeric cells exceeds {limit}")

    visible = prose(text)
    allowed = {" ".join(item.split()) for item in names(cfg.get("allowed_shorthands"))}
    definitions = [(m.start(), " ".join((m["inside"] or m["before"]).split()))
                   for m in SHORTHAND_DEFINITION.finditer(visible)]
    for match in SHORTHAND.finditer(visible):
        label = " ".join(match[0].split())
        if label not in allowed and label.split()[1] not in allowed and not any(
            name == label and abs(offset - match.start()) <= 160 for offset, name in definitions
        ):
            error("unexplained_shorthand", *document.locate(match.start()), f"define {label} at first use")

    for match in THEOREM_BODY.finditer(text):
        if not any(re.search(r"\w", m["body"] if m["body"] is not None else m[0])
                   for m in MATH.finditer(match[2])):
            error("empty_theorem_body", *document.locate(match.start()), f"{match[1]} needs a math expression")


def symbol_pattern(symbol):
    return re.compile(r"(?<![\w\\])" + r"\s*".join(map(re.escape, re.findall(r"\\[A-Za-z]+|[^\s]", symbol)))
                      + r"(?![\w{])")


def check_section_separation(all_documents, sources, cfg, error):
    from paper_gate import norm_eq

    setups = [path for path in sources if path.stem.lower().startswith("02_setup")]
    methods = [path for path in sources if path.stem.lower().startswith("03_method")]
    if not (setups and methods):
        return
    document_paths = [{path for _, path in document.starts} for document in all_documents]
    pairs = {method: [setup for setup in setups if setup.parent == method.parent
                      or any({setup, method} <= paths for paths in document_paths)] for method in methods}

    def equations(text):
        for match in DISPLAY.finditer(text):
            body = match["body"] if match["body"] is not None else match[0][2:-2]
            body = re.sub(r"(?<!\\)&|\\tag\*?\s*\{[^{}]*\}", "", body)
            yield norm_eq(body).removesuffix(r"\\"), text.count("\n", 0, match.start()) + 1

    setup_equations = {}
    for path in dict.fromkeys(setup for matching in pairs.values() for setup in matching):
        text = tex_code(sources[path])
        setup_equations[path] = {}
        for normalized, line in equations(text):
            setup_equations[path].setdefault(normalized, line)
        for symbol in names(cfg.get("method_only_symbols")):
            if match := symbol_pattern(symbol).search(text):
                error("section_separation", path, text.count("\n", 0, match.start()) + 1,
                      f"Method-only symbol {symbol} appears in Setup")
    for path, matching in pairs.items():
        for normalized, line in equations(tex_code(sources[path])):
            other = next((setup for setup in matching if normalized in setup_equations[setup]), None)
            if normalized and other is not None:
                original = setup_equations[other][normalized]
                error("section_separation", path, line,
                      f"display duplicates Setup equation at {other.as_posix()}:{original}")


def check_symbol_roles(sources, cfg, error):
    aliases = [(canonical, alias, symbol_pattern(alias))
               for canonical, variants in cfg.get("symbol_aliases", {}).items() for alias in names(variants)]
    reserved = [(symbol, {role.casefold() for role in names(roles)}, symbol_pattern(symbol))
                for symbol, roles in cfg.get("reserved_symbols", {}).items()]
    if not (aliases or reserved):
        return
    for path, raw in sources.items():
        text = tex_code(raw)
        for canonical, alias, pattern in aliases:
            for match in pattern.finditer(text):
                error("symbol_aliases", path, text.count("\n", 0, match.start()) + 1,
                      f"use {canonical} consistently in place of {alias}")
        for symbol, roles, pattern in reserved:
            for match in pattern.finditer(text):
                after = text[match.end():].lstrip(" $")
                before = text[max(0, match.start() - 100):match.start()].rstrip(" $")
                definition = re.match(r"(?:denotes\s+|represents\s+|(?:is|be)\s+(?:the|a|an)\s+)"
                                      r"(?:(?:the|a|an)\s+)?([^.;,。；，\n]+)"
                                      r"|(?:表示|代表|是)\s*([^。；，\n]+)", after, re.I)
                named = re.search(r"\b(?:(?:let|define)\s+(?:the\s+)?([\w -]+?)\s+as"
                                  r"|the\s+([\w -]+?)\s+is)$", before, re.I)
                role = (next(part for part in definition.groups() if part is not None) if definition
                        else (named[1] or named[2]) if named else "")
                if role and not any(re.search(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", role.casefold())
                                    for name in roles):
                    error("symbol_aliases", path, text.count("\n", 0, match.start()) + 1,
                          f"{symbol} is reserved for {' / '.join(sorted(roles))}, found {role.strip()!r}")


ALGORITHM = re.compile(r"\\begin\{(algorithm\*?)\}(.*?)\\end\{\1\}", re.S)
ALGORITHM_CAPTION = re.compile(r"^\s*(?:Algorithm\b|算法)", re.I)
ALGORITHM_CAPTION_OF = re.compile(r"\\captionof\*?\s*\{algorithm\}")
VERBATIM_BEGIN = re.compile(r"\\begin\{(?:[Vv]erbatim)\*?\}")
AFTER_CAPTION = re.compile(r"\s*(?:\\label\s*\{[^{}]*\}\s*|\\end\{algorithm\*?\}\s*)*")


def check_verbatim_algorithms(sources, error):
    if not any(VERBATIM_BEGIN.search(raw) for raw in sources.values()):
        return
    for document in documents(sources, keep_verbatim=True):
        text = document.text
        algorithms = list(ALGORITHM.finditer(text))
        offsets = {
            match.start(2) + block.start()
            for match in algorithms for block in VERBATIM_BEGIN.finditer(match[2])
        }
        caption_ends = [end for body, end in captions(text)
                        if ALGORITHM_CAPTION.match(latex_to_text(body))
                        or any(match.start() < end < match.end() for match in algorithms)]
        for match in ALGORITHM_CAPTION_OF.finditer(text):
            if arg := _group(text, _skip_options(text, match.end()), "{"):
                caption_ends.append(arg[1])
        for end in caption_ends:
            start = AFTER_CAPTION.match(text, end).end()
            if VERBATIM_BEGIN.match(text, start):
                offsets.add(start)
        for offset in sorted(offsets):
            error("verbatim_algorithm", *document.locate(offset), "typeset pseudocode in the algorithm")


DOCUMENTCLASS = re.compile(r"\\documentclass\b")


def single_tex_source_errors(paper, cfg):
    paper = Path(paper).resolve()
    ignored = {"_archive", "archive", "figures"}
    allowed = names(cfg.get("allowed_tex_trees"))

    def excluded(path):
        return path.name.casefold() in ignored or any(
            path.match(pattern) or path == (paper / pattern).resolve() for pattern in allowed
        )

    def has_main(path, *, recursive=False):
        for directory, folders, files in walk(path):
            folders[:] = [name for name in folders if recursive and not excluded(Path(directory) / name)]
            for name in files:
                if name.endswith(".tex") and DOCUMENTCLASS.search(
                    tex_code((Path(directory) / name).read_text(encoding="utf-8-sig"))
                ):
                    return True
        return False

    def active_children(parent, *, recursive=False):
        return [path for path in sorted(parent.iterdir())
                if path.is_dir() and not excluded(path) and has_main(path, recursive=recursive)]

    trees = []
    if cfg.get("single_tex_source"):
        trees = active_children(paper, recursive=True)
        if has_main(paper):
            trees.insert(0, paper)
    if len(trees) <= 1 and has_main(paper) and not excluded(paper):
        trees = active_children(paper.parent)
    if len(trees) > 1:
        location = Path(cfg.get("main_tex") or "paper_gate.json").as_posix()
        return [f"single_tex_source  {location}:1: competing LaTeX source trees: "
                + ", ".join(tree.name for tree in trees)]
    return []


def check_document(document, cfg, error):
    text = document.text
    scopes, appendix = regions(document, cfg)
    max_symbols = cfg.get("max_theorem_symbols", 5)
    declared = cfg.get("symbols")
    for match in THEOREM.finditer(text):
        start = match.start()
        if start < appendix and not has_intuition(text[:start]):
            error("intuition", *document.locate(start), "add a prose intuition sentence in the preceding paragraph")
        active, depth = theorem_symbols(match[2])
        problems = []
        if len(active) > max_symbols:
            problems.append(f"{len(active)} active symbols exceeds {max_symbols}: {', '.join(active)}")
        if depth >= 3:
            problems.append(f"subscript/superscript nesting reaches {depth} levels")
        if declared is not None:
            missing = [symbol for symbol in active if symbol not in declared and symbol.lstrip("\\") not in declared]
            if missing:
                problems.append("undeclared symbols: " + ", ".join(missing))
        if problems:
            error("symbol_budget", *document.locate(start), ". ".join(problems))

    max_steps, max_displays = cfg.get("max_proof_steps", 5), cfg.get("max_proof_equations", 2)
    for match in PROOF.finditer(text, 0, appendix):
        steps, displays = proof_length(match[1])
        if steps > max_steps or displays > max_displays:
            error("proof_sketch", *document.locate(match.start()),
                  f"{steps} steps (max {max_steps}), {displays} display environments (max {max_displays})")

    max_equations = cfg.get("max_method_equations", 4)
    method_equations = abstracts = 0
    method_at = None
    method_name = cfg.get("method_name")
    for start, end, scope in scopes:
        if scope == "method" and text[start:end].strip():
            if method_at is None:
                method_at = document.locate(start + len(text[start:end]) - len(text[start:end].lstrip()))
            for match in DISPLAY.finditer(text, start, end):
                count = numbered_rows(match)
                if method_equations <= max_equations < method_equations + count:
                    excess_at = document.locate(match.start())
                method_equations += count
        if scope == "abstract" and text[start:end].strip():
            abstracts += 1
            if method_name:
                visible = prose(text[start:end])
                # Drop math and markup while preserving sentence terminators.
                ends = [stop for _, stop, _ in sentence_spans(visible)]
                lead = visible[:ends[min(2, len(ends) - 1)]] if ends else ""
                name = r"\s+".join(map(re.escape, method_name.split()))
                if not re.search(r"(?<![A-Za-z0-9_])" + name + r"(?![A-Za-z0-9_])", lead, re.I):
                    pos = start + len(text[start:end]) - len(text[start:end].lstrip())
                    error("abstract_lead", *document.locate(pos), f"mention {method_name} in the first three sentences")
        if scope in {"abstract", "intro", "method"}:
            for match in HEDGE.finditer(prose(text[start:end])):
                error("defensive_hedge", *document.locate(start + match.start()),
                      f"state the supported claim directly: {match[0].strip()!r}")
    if method_equations > max_equations:
        error("method_equations", *excess_at, f"{method_equations} numbered equations exceeds {max_equations}")
    elif method_at is not None and not method_equations:
        error("method_equations", *method_at, "Method needs at least one numbered equation")
    check_structure(document, cfg, scopes, appendix, error)
    return abstracts


LEAN_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|--[^\n]*|/-')
LEAN_COMMENT = re.compile(r"/-|-/")


def lean_code(raw):
    """Remove Lean comments and strings, including nested block comments."""
    depth, cursor = 0, 0
    parts = []
    while match := (LEAN_COMMENT if depth else LEAN_TOKEN).search(raw, cursor):
        if not depth:
            parts.append(raw[cursor:match.start()])
        token = match[0]
        if token == "/-":
            depth += 1
        elif token == "-/" and depth:
            depth -= 1
            if not depth:
                parts.append(" ")
        else:
            parts.append(" ")
        cursor = match.end()
    if not depth:
        parts.append(raw[cursor:])
    return "".join(parts)


def check_bindings(paper, sources, bindings, error):
    labels = {}
    for path, raw in sources.items():
        for match in re.finditer(r"\\label\s*\{([^{}]+)\}", tex_code(raw)):
            labels.setdefault(match[1], (path, raw.count("\n", 0, match.start()) + 1))
    for binding in bindings:
        label = binding.get("tex_label")
        path, line = labels.get(label, (next(iter(sources), Path("paper_gate.json")), 1))
        problems = []
        if label not in labels:
            problems.append(f"TeX label {label!r} is missing")
        lean = binding.get("lean_file")
        name = binding.get("lean_theorem")
        if not lean or not (paper / lean).is_file():
            problems.append(f"Lean file {lean!r} is missing")
        else:
            code = lean_code((paper / lean).read_text(encoding="utf-8-sig"))
            if not name or not re.search(r"\b(?:theorem|lemma)\s+" + re.escape(name) + r"(?![\w'.])", code):
                problems.append(f"theorem or lemma {name!r} is missing from {lean}")
            gaps = sorted(set(re.findall(r"\b(?:sorry|axiom)\b", code)))
            if gaps:
                problems.append(f"{lean} contains " + " and ".join(gaps))
        for key in ("method_eq", "probe_evidence"):
            value = binding.get(key)
            if not value or isinstance(value, str) and not value.strip():
                problems.append(f"{key} is empty")
        code_ref = binding.get("code_ref")
        if code_ref:
            code_path = Path(code_ref) if Path(code_ref).is_absolute() else (paper / code_ref)
            if not code_path.is_file():
                problems.append(f"code_ref {code_ref!r} is missing")
            else:
                code_text = code_path.read_text(encoding="utf-8-sig")
                for sym in names(binding.get("code_symbols")):
                    if not re.search(rf"\b{re.escape(sym)}\b", code_text):
                        problems.append(f"code_symbol {sym!r} is missing from {code_ref}")
        if problems:
            error("lean_bindings", path, line, ". ".join(problems))


def check_glossary(sources, glossary, error):
    for path, raw in sources.items():
        text = tex_code(raw)
        language = re.search(r"(?:^|[_-])(en|zh|cn)$", path.stem, re.I)
        language = language[1].lower() if language else None
        if "sections_en" in path.parts or language == "en":
            continue
        if "sections_zh" not in path.parts and language not in {"zh", "cn"} and not HAN.search(text):
            continue
        for canonical, variants in glossary.items():
            for variant in names(variants):
                if variant and variant != canonical:
                    for match in re.finditer(re.escape(variant), text):
                        error("zh_glossary", path, text.count("\n", 0, match.start()) + 1,
                              f"use {canonical!r} in place of {variant!r}")


def check_paper(paper, cfg=None, *, no_sorry=False, sources=None, defer_sorries=False):
    """Return errors and acknowledged gaps. Pass parsed config and source overrides from paper_gate."""
    paper, cfg = Path(paper).resolve(), cfg or {}
    if isinstance(cfg.get("theory_gate"), dict):
        cfg = {**cfg, **cfg["theory_gate"]}
    if sources is None:
        sources = load_sources(paper, cfg)
    report = SimpleNamespace(errors=[], sorries=[])

    def error(check, path, line, message):
        report.errors.append(f"{check}  {path.as_posix()}:{line}: {message}")

    abstract_count = 0
    prose_sources = {path: raw for path, raw in sources.items() if not is_figure_source(path, raw)}
    all_documents = documents(prose_sources)
    for document in all_documents:
        abstract_count += check_document(document, cfg, error)
    check_section_separation(all_documents, prose_sources, cfg, error)
    check_symbol_roles(sources, cfg, error)
    check_verbatim_algorithms(sources, error)
    report.errors.extend(single_tex_source_errors(paper, cfg))
    if cfg.get("method_name") and not abstract_count:
        error("abstract_lead", next(iter(sources), Path("paper_gate.json")), 1, "no Abstract text found")
    check_bindings(paper, sources, cfg.get("theorems", []), error)
    check_glossary(sources, cfg.get("zh_glossary", {}), error)
    if not defer_sorries:
        from paper_gate import Src, apply_sorries
        apply_sorries([Src(path, "", raw) for path, raw in sources.items()], report,
                      allow=not no_sorry, relative_paths=True, checks=CHECKS)
    return vars(report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper_dir", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--no-sorry", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    config = args.config or args.paper_dir / "paper_gate.json"
    try:
        cfg = json.loads(config.read_text(encoding="utf-8-sig")) if args.config or config.is_file() else {}
        result = check_paper(args.paper_dir, cfg, no_sorry=args.no_sorry)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"theory_gate  errors {len(result['errors'])} | sorry {len(result['sorries'])}")
        for check, items in result.items():
            for item in items:
                print(f"{check}: {item}")
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())

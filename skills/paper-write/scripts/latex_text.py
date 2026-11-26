"""Extract manuscript text from LaTeX source without running TeX."""

from __future__ import annotations

import re
from pathlib import Path

_TOKEN = re.compile(r"\\([A-Za-z@]+\*?|[\s\S])|\$\$?|[{}&]")
_SOURCE_TOKEN = re.compile(
    r"\\begin\{(?P<literal>verbatim\*?|Verbatim|lstlisting|minted)\}.*?\\end\{(?P=literal)\}"
    r"|(?P<comment>%[^\r\n]*)|\\(?P<command>[A-Za-z@]+\*?|[\s\S])",
    re.S,
)
_REFERENCE = re.compile(
    r"label|(?:[a-zA-Z]*cite[a-zA-Z]*|ref|pageref|eqref|autoref|cref|Cref|"
    r"nameref|vref|subref|refstepcounter|nocite)\*?"
)
_ENV = re.compile(r"\{([A-Za-z]+\*?)\}")
_DISPLAY = {"equation", "align", "alignat", "flalign", "gather", "multline",
            "displaymath", "eqnarray", "split", "aligned", "alignedat", "gathered"}
_TABLE = {"tabular", "tabularx", "tabulary", "longtable", "array"}
_VERBATIM = {"verbatim", "Verbatim", "lstlisting", "minted"}
_HEADINGS = {"chapter": "# ", "section": "## ", "subsection": "### ",
             "subsubsection": "#### ", "paragraph": "##### "}
_LAYOUT_ARGS = {
    "includegraphics": 1, "input": 1, "include": 1, "bibliography": 1, "bibliographystyle": 1,
    "hspace": 1, "vspace": 1, "rule": 2, "setlength": 2, "addtolength": 2,
    "cline": 1, "cmidrule": 1, "specialrule": 3, "draftlabel": 1,
}
_SKIP_LEADING_ARGS = {
    "href": 1, "hypertarget": 1, "textcolor": 1, "colorbox": 1, "raisebox": 1,
    "multicolumn": 2, "multirow": 2, "drafttable": 1,
}
_LITERALS = {"%": "%", "&": "&", "#": "#", "_": "_", "{": "{", "}": "}", "$": "$",
             "textasciitilde": "~", "textbackslash": "\\", "textbar": "|",
             ",": " ", ";": " ", ":": " ", " ": " ", "enspace": " ", "quad": " ", "qquad": " "}


def draft_format(draft_dir: Path, fmt: str | None = None) -> str:
    if fmt is not None:
        if fmt not in ("md", "tex"):
            raise ValueError(f"Unknown draft format: {fmt!r}")
        return fmt
    return "tex" if (draft_dir / "latex" / "sections_en").is_dir() else "md"


def section_dirs(draft_dir: Path, fmt: str) -> tuple[Path, Path]:
    root = draft_dir / "latex" if fmt == "tex" else draft_dir
    return root / "sections_en", root / "sections_zh"


def binding_section_path(file: str, fmt: str) -> Path:
    """Resolve a legacy section binding to its current LaTeX source."""
    path = Path(file.replace("\\", "/"))
    if fmt == "tex" and len(path.parts) == 2 and path.parts[0] in ("sections_en", "sections_zh"):
        path = Path("latex") / path.with_suffix(".tex")
    return path


def _group(text: str, pos: int, opener: str) -> tuple[str, int] | None:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    if pos >= len(text) or text[pos] != opener:
        return None
    stack = ["}" if opener == "{" else "]"]
    start = pos + 1
    pos = start
    while pos < len(text):
        c = text[pos]
        if c == "\\":
            pos += 2
            continue
        if c == stack[-1]:
            stack.pop()
            if not stack:
                return text[start:pos], pos + 1
        elif c == "{" or (c == "[" and stack[-1] == "]"):
            stack.append("}" if c == "{" else "]")
        pos += 1
    return None


def _skip_options(text: str, pos: int) -> int:
    while (arg := _group(text, pos, "[")) is not None:
        pos = arg[1]
    return pos


def _skip_args(text: str, pos: int, count: int) -> int:
    for _ in range(count):
        arg = _group(text, _skip_options(text, pos), "{")
        if arg is None:
            break
        pos = arg[1]
    return pos


def _without_references(source: str) -> tuple[str, set[str]]:
    text = _SOURCE_TOKEN.sub(lambda m: "" if m["comment"] is not None else m[0], source)
    labels = set()
    chunks = []
    start = 0
    for m in _SOURCE_TOKEN.finditer(text):
        command = m["command"]
        if m.start() < start or command is None or not _REFERENCE.fullmatch(command):
            continue
        arg = _group(text, _skip_options(text, m.end()), "{")
        if command == "label" and arg is not None:
            labels.add(arg[0].strip())
        chunks.append(text[start:m.start()])
        stop = arg[1] if arg is not None else _skip_options(text, m.end())
        # Keep line breaks for reviewer context and the checker's diagnostics.
        chunks.append("\n" * text[m.start():stop].count("\n"))
        start = stop
    chunks.append(text[start:])
    return "".join(chunks), labels


def section_labels(source: str) -> set[str]:
    return _without_references(source)[1]


def _end_pattern(delimiter: str) -> re.Pattern:
    return re.compile(r"(?<!\\)(?:\\\\)*(" + re.escape(delimiter) + ")")


def _math(text: str, token: str, end: int) -> tuple[str, int, bool] | None:
    if token in ("$", "$$", r"\(", r"\["):
        closer = {r"\(": r"\)", r"\[": r"\]"}.get(token, token)
        stop = _end_pattern(closer).search(text, end)
        if stop:
            return text[end:stop.start(1)], stop.end(), token in ("$$", r"\[")
    return None


def _table(body: str, for_checks: bool) -> str:
    # Split only at top-level alignment tokens. Groups and math keep their own separators.
    rows: list[list[str]] = []
    cells: list[str] = []
    start = pos = 0
    while (m := _TOKEN.search(body, pos)) is not None:
        token = m[0]
        pos = m.end()
        math = _math(body, token, pos)
        if math:
            pos = math[1]
        elif token == "{" and (group := _group(body, m.start(), "{")):
            pos = group[1]
        elif token in ("&", r"\\", r"\tabularnewline", r"\cr"):
            cells.append(_render(body[start:m.start()], False))
            if token != "&":
                rows.append(cells)
                cells = []
                pos = _skip_options(body, pos + (body[pos:pos + 1] == "*"))
            start = pos
    tail = _render(body[start:], False)
    if cells or tail.strip():
        rows.append([*cells, tail])
    lines = [" | ".join(" ".join(cell.split()) for cell in row) for row in rows]
    if for_checks:
        lines = [f"| {line} |" for line in lines]
    return "\n\n" + "\n".join(lines) + "\n\n"


def _render(text: str, for_checks: bool) -> str:
    out: list[str] = []
    lists: list[str] = []
    pos = 0
    while (m := _TOKEN.search(text, pos)) is not None:
        out.append(text[pos:m.start()].replace("~", " "))
        token, command = m[0], m[1]
        pos = m.end()
        math = _math(text, token, pos)
        if command in ("begin", "end") and (env := _ENV.match(text, pos)):
            pos = env.end()
            name = env[1].rstrip("*")
            if name in ("enumerate", "itemize", "description"):
                if command == "begin":
                    lists.append(name)
                elif lists:
                    lists.pop()
            if command == "begin":
                pos = _skip_options(text, pos)
                stop = (_end_pattern(r"\end{" + env[1] + "}").search(text, pos)
                        if name in _DISPLAY | _TABLE | _VERBATIM else None)
                if stop and name in _DISPLAY:
                    start = _skip_args(text, pos, int(name in ("alignat", "alignedat")))
                    math = (text[start:stop.start(1)], stop.end(), True)
                elif stop and name in _TABLE:
                    start = _skip_args(text, pos, 2 if name in ("tabularx", "tabulary") else 1)
                    start = _skip_options(text, start)
                    out.append(_table(text[start:stop.start(1)], for_checks))
                    pos = stop.end()
                elif stop:
                    start = _skip_args(text, pos, int(name == "minted"))
                    body = text[start:stop.start(1)].strip("\r\n")
                    fence = "```\n" if for_checks else ""
                    out.append("\n\n" + fence + body + "\n" + fence + "\n")
                    pos = stop.end()
        elif command in _LITERALS:
            out.append(_LITERALS[command])
        elif command == "item":
            marker = "1. " if for_checks and lists and lists[-1] == "enumerate" else "- " if for_checks else ""
            out.append("\n" + marker)
            if (label := _group(text, pos, "[")) is not None:
                out.append(_render(label[0], for_checks) + " ")
                pos = label[1]
        elif command and command.rstrip("*") in _LAYOUT_ARGS:
            pos = _skip_args(text, _skip_options(text, pos), _LAYOUT_ARGS[command.rstrip("*")])
        elif command in ("par", "\\", "draftseparator"):
            out.append("\n\n")
        elif command is not None:
            name = command.rstrip("*")
            pos = _skip_args(text, _skip_options(text, pos), _SKIP_LEADING_ARGS.get(name, 0))
            if name in _HEADINGS or name == "caption":
                arg = _group(text, pos, "{")
                if arg:
                    prefix = (_HEADINGS.get(name, "> ") if for_checks else "")
                    out.append("\n\n" + prefix + _render(arg[0], for_checks) + "\n\n")
                    pos = arg[1]
        elif token == "&":
            out.append(token)
        elif token == "{" and (group := _group(text, m.start(), "{")):
            out.append(_render(group[0], for_checks))
            pos = group[1]
        elif token.startswith("$") and not math:
            out.append(token)
        if math:
            body, pos, display = math
            if display and for_checks:
                out.append("\n\n$$\n" + body.strip() + "\n$$\n\n")
            elif display:
                out.append("\n\n$" + body.strip() + "$\n\n")
            else:
                out.append("$" + body + "$")
    out.append(text[pos:].replace("~", " "))
    return "".join(out)


def latex_to_text(source: str, *, for_checks: bool = False) -> str:
    """Convert a section to text. Add structure markers for the existing linters on request."""
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    text, _ = _without_references(source)
    return _render(text, for_checks).strip()


def read_section(path: Path, *, for_checks: bool = False) -> str:
    raw = path.read_text(encoding="utf-8-sig")
    return latex_to_text(raw, for_checks=for_checks) if path.suffix == ".tex" else raw

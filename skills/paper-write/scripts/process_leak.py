"""Deterministic checks for manuscript prose about internal research workflows."""

from __future__ import annotations

import re


OBJECTS = {
    "en": re.compile(
        r"\b(?:experiments?|evaluations?|comparisons?|ablations?|runs|"
        r"(?:a|the|each|every|one|this|training|evaluation)\s+run|"
        r"results?|data(?:sets?)?|models?|baselines?|tables?|benchmarks?)\b", re.I
    ),
    "zh": re.compile(r"实验|试验|主表|表格|对照|对比|结果|数据|评测|评估|模型|基线|消融"),
}

# Prefer an explicit exclusion over a plan, and a plan over a stage label.
CUES = {
    "en": {
        "exclusion": re.compile(
            r"\b(?:"
            r"(?:used|retained|kept)\s+(?:only|solely)\s+for\s+(?:\w+\s+){0,2}diagnos\w*|"
            r"(?:only|solely)\s+(?:used\s+)?for\s+(?:\w+\s+){0,2}diagnos\w*|"
            r"(?:excluded|omitted|removed|withheld)\s+from\s+(?:the\s+)?(?:main[- ]text|main tables?)|"
            r"(?:will not|do not|no longer|not)\s+(?:be\s+)?(?:appear|included|reported|go)\s+in(?:to)?\s+"
            r"(?:the\s+)?(?:main[- ]text|main tables?)|"
            r"(?:left|leaving)\s+out\s+of\s+(?:the\s+)?(?:main[- ]text|main tables?)"
            r")\b", re.I
        ),
        "plan": re.compile(
            r"\b(?:"
            r"(?:will|must|should|need(?:s)?\s+to|plan\s+to)\s+(?:be\s+)?re[- ]?run|"
            r"to\s+be\s+(?:completed|filled\s+in)|"
            r"(?:are|is)\s+(?:specified|scheduled|planned)\s+to\s+(?:be\s+)?(?:run|conducted)|"
            r"(?:prior\s+to|before|by)\s+(?:the\s+)?(?:submission(?:\s+freeze)?|camera[- ]ready(?:\s+deadline)?)|"
            r"(?:remain(?:s)?|still|currently)\s+(?:in progress|pending|underway)|"
            r"(?:marked|listed|left)\s+(?:as\s+)?TBD"
            r")\b", re.I
        ),
        "stage": re.compile(
            r"\b(?:"
            r"(?:early|pilot|preliminary|exploratory)\s+(?:\w+[- ]+){0,2}"
            r"(?:phase|stage|experiments?|runs?|results?|comparisons?|data|ablations?)|"
            r"finali[sz]ed\s+(?:\w+[- ]+){0,2}(?:model|specification|recipe)|"
            r"(?:official|final|confirmatory)\s+(?:main[- ]text\s+|main\s+)?tables?"
            r")\b", re.I
        ),
    },
    "zh": {
        "exclusion": re.compile(
            r"(?:仅|只)(?:供|用[于作]|作(?:为)?)[^，,；;。！？]{0,10}诊断|"
            r"(?:不再|不|未)(?:纳入|进入|列入|计入|放入)[^，,；;。！？]{0,10}(?:主表|正文)|"
            r"(?:移入|移至|移到|挪到)附录"
        ),
        "plan": re.compile(
            r"(?:将|拟|计划|需要?|应|待)[^，,；;。！？]{0,30}?(?:重跑|补跑|重产)|"
            r"待(?:补充|补齐|重做|完成)|"
            r"(?:投稿|提交|终稿冻结|截稿)(?:之前|前)|"
            r"(?:仍|尚)在进行|正在(?:补充|补跑)|"
            r"(?:主表|正式实验)要求[^。！？]{0,40}完成训练"
        ),
        "stage": re.compile(
            r"正式主表|定稿(?:版|模型|配方)|探索(?:阶段|性?(?:数据|实验|消融|结果|诊断))|"
            r"早期[^，,；;。！？]{0,14}(?:实验|数据|结果|对照|版本|探索)"
        ),
    },
}

FUTURE_WORK = {
    "en": re.compile(r"\bfuture\s+(?:work|research|studies|experiments)\b", re.I),
    "zh": re.compile(r"未来(?:工作|研究|方向|实验)|后续(?:工作|研究)"),
}
DIRECT_CUES = {
    "en": {
        "harness_split": re.compile(
            r"\b(?:recipe[- ]ablation harness|unified runner|different (?:training )?harness|"
            r"rows? \d+(?:--?|–)\d+ use.*row \d+ uses)\b", re.I
        ),
        "meta_outline": re.compile(
            r"\b(?:Each subsection first identifies.*then explains|In this section we first.*then explain)\b",
            re.I,
        ),
    },
    "zh": {
        "harness_split": re.compile(r"不同训练脚本|两套训练脚本|混用.*脚本"),
        "meta_outline": re.compile(r"本小节首先指出.*随后说明"),
    },
}
FUTURE_PLAN = {
    "en": re.compile(
        r"\b(?:(?:will|plan\s+to|intend\s+to)\s+(?:evaluate|compare|report|run|train|test|"
        r"repeat|complete|add|include|be\s+(?:evaluated|compared|reported|run|trained|tested|"
        r"repeated|completed|added|included))|(?:is|are)\s+planned)\b", re.I
    ),
    "zh": re.compile(
        r"(?:计划|拟|将)(?:会|统一)?(?:开展|进行|采用|使用|报告|训练|评测|评估|对比|补充|发布)|"
        r"将在[^，,；;。！？]{1,30}(?:比较|对比|评估|训练|报告)"
    ),
}
DISCUSSION = re.compile(r"\b(?:discussion|conclusions?)\b|讨论|结论|总结", re.I)
ABBREVIATION = re.compile(
    r"\b(?:et al|e\.g|i\.e|vs|figs?|tabs?|eqs?|secs?|app|refs?|pp|cf|approx)\.",
    re.I,
)
BOUNDARY = re.compile(
    r"(?<=[。！？])|(?<=[.!?])[\"'”’)\]]*(?=[ \t\r\n\x1e\u4e00-\u9fff]|$)|"
    r"(?P<separator>\n[ \t\r]*\n|\x1e)"
)
MARKDOWN_STRUCTURES = re.compile(
    r"\A---[ \t]*\r?\n[\s\S]*?^---[ \t]*(?:\r?\n|$)|"
    r"^[ \t]*(?P<fence>```|~~~|\$\$)[^\n]*\n[\s\S]*?^[ \t]*(?P=fence)[ \t]*$|"
    r"^[ \t]*(?P<level>#{1,6})[ \t]+(?P<title>[^\n]+)|"
    r"^[ \t]*[>|][^\n]*",
    re.M,
)


def _classify(sentence: str, lang: str, discussion: bool) -> dict | None:
    for kind, pattern in DIRECT_CUES[lang].items():
        if matches := list(pattern.finditer(sentence)):
            return {"sentence": sentence, "cues": list(dict.fromkeys(m[0] for m in matches)), "kind": kind}
    if not OBJECTS[lang].search(sentence):
        return None
    future_work = list(FUTURE_WORK[lang].finditer(sentence))
    for kind, pattern in CUES[lang].items():
        matches = list(pattern.finditer(sentence))
        if kind == "plan" and not (discussion and future_work):
            matches.extend(FUTURE_PLAN[lang].finditer(sentence))
            matches.extend(future_work)
        if matches:
            cues = list(dict.fromkeys(m[0] for m in sorted(matches, key=lambda m: m.start())))
            return {"sentence": sentence, "cues": cues, "kind": kind}
    return None


def _segment_hits(text: str, raw: str, start: int, end: int, lang: str | None, discussion: bool):
    segment = text[start:end]
    safe = ABBREVIATION.sub(lambda m: m[0].replace(".", "\0"), segment)
    left = 0
    for boundary in [*BOUNDARY.finditer(safe), None]:
        if boundary is None:
            right = len(segment)
        else:
            right = boundary.start() if boundary["separator"] else boundary.end()
        chunk = segment[left:right]
        candidate = re.sub(r"\s+", " ", chunk).strip()
        for language in (lang,) if lang else OBJECTS:
            if hit := _classify(candidate, language, discussion):
                begin = start + left + len(chunk) - len(chunk.lstrip())
                finish = start + left + len(chunk.rstrip())
                hit["sentence"] = re.sub(r"\s+", " ", raw[begin:finish]).strip()
                yield begin, hit
                break
        if boundary is not None:
            left = boundary.end()


def _scan(text: str, raw: str, lang: str | None, structures):
    sections = {}
    cursor = 0
    for start, end, level, title in structures:
        yield from _segment_hits(text, raw, cursor, start, lang, any(sections.values()))
        if level is not None:
            sections = {depth: allowed for depth, allowed in sections.items() if depth < level}
            sections[level] = bool(DISCUSSION.search(title))
        cursor = end
    yield from _segment_hits(text, raw, cursor, len(text), lang, any(sections.values()))


def find_process_leaks(text: str, lang: str | None) -> list[dict]:
    """Return sentence findings for plain text or Markdown. Use None to check both languages."""
    structures = (
        (m.start(), m.end(), len(m["level"]) if m["level"] else None, m["title"])
        for m in MARKDOWN_STRUCTURES.finditer(text)
    )
    return [hit for _, hit in _scan(text, text, lang.lower() if lang else None, structures)]


# Formatting that wraps readable prose. Other command arguments are skipped.
INLINE_PROSE = {
    "emph", "textbf", "textit", "textmd", "textnormal", "textrm", "textsc",
    "textsf", "textsl", "texttt", "textup", "underline", "pending",
}


def _latex_projection(source: str):
    from latex_text import (
        _DISPLAY, _ENV, _HEADINGS, _LITERALS, _REFERENCE, _SOURCE_TOKEN, _TABLE, _TOKEN,
        _VERBATIM, _end_pattern, _group, _math, _skip_options,
    )

    def spaces(value):
        return re.sub(r"[^\r\n]", " ", value)

    comment_lines = []

    def omit_comment(match):
        if match["comment"] is None:
            return match[0]
        line_start = source.rfind("\n", 0, match.start()) + 1
        if not source[line_start:match.start()].strip():
            comment_lines.append(match.start())
        return spaces(match[0])

    raw = _SOURCE_TOKEN.sub(omit_comment, source)
    visible = list(raw.replace("~", " "))
    for pos in comment_lines:
        # Keep TeX comment-only lines from splitting a wrapped prose paragraph.
        visible[pos] = "\v"
    headings = []
    pos = 0
    while (token := _TOKEN.search(raw, pos)) is not None:
        start, pos = token.span()
        symbol, command = token[0], token[1]
        name = command.rstrip("*") if command else ""
        separator = False
        if math := _math(raw, symbol, pos):
            _, pos, separator = math
        elif name in {"begin", "end"} and (env := _ENV.match(raw, pos)):
            pos = _skip_options(raw, env.end())
            if name == "begin" and env[1].rstrip("*") in _DISPLAY | _TABLE | _VERBATIM:
                stop = _end_pattern(r"\end{" + env[1] + "}").search(raw, pos)
                if stop:
                    pos = stop.end()
            separator = True
        elif name in _HEADINGS or name == "caption":
            pos = _skip_options(raw, pos)
            if arg := _group(raw, pos, "{"):
                if name in _HEADINGS:
                    title, pos = arg
                    headings.append((start, pos, len(_HEADINGS[name].strip()), title))
                else:
                    separator = True
                    visible[arg[1] - 1] = "\x1e"
        elif name in INLINE_PROSE or name in {"item", "par", "\\"}:
            pos = _skip_options(raw, pos)
            separator = name in {"item", "par", "\\"}
        elif command in _LITERALS:
            visible[start:pos] = _LITERALS[command] + " " * (pos - start - len(_LITERALS[command]))
            continue
        elif command:
            pos = _skip_options(raw, pos)
            if _REFERENCE.fullmatch(command):
                if arg := _group(raw, pos, "{"):
                    pos = arg[1]
            else:
                while arg := _group(raw, pos, "{"):
                    pos = _skip_options(raw, arg[1])
        if visible[start] != "\x1e":
            visible[start:pos] = spaces(raw[start:pos])
        if separator:
            visible[start] = "\x1e"
    return "".join(visible), raw, headings


def find_latex_process_leaks(source: str, lang: str | None = None) -> list[dict]:
    """Return the same findings for LaTeX with the sentence's original 1-based line."""
    visible, raw, headings = _latex_projection(source)
    return [
        {**hit, "line": source.count("\n", 0, start) + 1}
        for start, hit in _scan(visible, raw, lang.lower() if lang else None, headings)
    ]

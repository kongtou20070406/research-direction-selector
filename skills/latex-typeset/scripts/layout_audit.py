"""Zero-LLM-token LaTeX layout auditor, prose-preservation guard, and annotated PDF renderer."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

sys.dont_write_bytecode = True

VENUE_LIMITS: dict[str, int | None] = {
    "icml": 8,
    "neurips": 9,
    "iclr": 10,
    "cvpr": 8,
    "arxiv": None,
}

_TEX_FILE_RE = re.compile(r"\(([^()\s\"']+\.tex)\b")
_OVERFULL_HBOX_RE = re.compile(r"Overfull \\hbox \(([0-9.]+)pt too wide\)(?:.*?(?:at lines (\d+)--(\d+)|detected at line (\d+)))?")
_OVERFULL_VBOX_RE = re.compile(r"Overfull \\vbox \(([0-9.]+)pt too high\)(?:.*?(?:at lines (\d+)--(\d+)|detected at line (\d+)))?")
_UNDERFULL_BOX_RE = re.compile(r"Underfull \\([hv]box) \(badness (\d+)\)(?:.*?(?:at lines (\d+)--(\d+)|detected at line (\d+)))?")
_FLOAT_TOO_LARGE_RE = re.compile(r"LaTeX Warning: Float too large for page by ([0-9.]+)pt(?: on input line (\d+))?")
_UNDEF_REF_RE = re.compile(r"LaTeX Warning: (Reference|Citation) `([^']+)' on page \S+ undefined(?: on input line (\d+))?")
_MISSING_CHAR_RE = re.compile(r"Missing character: There is no (.+?) in font ([^!]+)")
_FATAL_LOG_RE = re.compile(r"(?:^!\s*(?:Emergency stop|Fatal error occurred)|.+?:\d+:\s*Emergency stop|\*\*\*\s*\(job aborted|==>\s*Fatal error occurred)")

_VSPACE_HACK_RE = re.compile(r"\\(?:vspace\*?|vskip)\s*\{?\s*-\s*([0-9.]+)\s*(pt|em|ex|cm|mm|in)\s*\}?")
_SPACING_HACK_RE = re.compile(r"\\(?:renewcommand\s*\{\s*\\baselinestretch\s*\}\s*\{([0-9.]+)\}|linespread\s*\{([0-9.]+)\})")
_GEOM_HACK_RE = re.compile(r"\\addtolength\s*\{\s*\\(textheight|textwidth|parskip)\s*\}\s*\{\s*-\s*([0-9.]+)")
_TABULAR_BAR_RE = re.compile(r"\\begin\{tabularx?\}(?:\{[^}]*\})?\{([^}]*\|[^}]*)\}")
_SCALEBOX_RE = re.compile(r"\\scalebox\s*\{\s*([0-9.]+)\s*\}")
_FLOAT_H_RE = re.compile(r"\\begin\{(?:figure|table)\*?\}\s*\[([^\]]*[Hh][^\]]*)\]")
_HEADING_TEXT_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+\S|[A-Z]\.\d*\s+\S|Abstract\b|Introduction\b|Conclusion\b)")
_SECTION_BOUNDARY_RE = re.compile(r"^\s*(?:References|Bibliography|Appendix)\b", re.I | re.M)

_STRIP_LAYOUT_CMDS = re.compile(
    r"\\(?:vspace\*?|hspace\*?|looseness|setlength|addtolength|tabcolsep|arraystretch|baselinestretch|"
    r"linespread|centering|raggedright|raggedleft|small|footnotesize|scriptsize|tiny|normalsize|"
    r"toprule|midrule|bottomrule|cmidrule(?:\([^)]*\))?(?:\{[^}]*\})?|hline|allowbreak|linebreak|"
    r"newline|clearpage|newpage|FloatBarrier|noindent|quad|qquad|label\{[^}]*\})\b"
    r"(?:\s*=\s*-?[0-9.]+\w*|\s*\{[^}]*\})*"
)
_TEXORPDF_RE = re.compile(r"\\texorpdfstring\{([^{}]*)\}\{[^{}]*\}")


def _strip_tex_comment(line: str) -> str:
    out = []
    escaped = False
    for ch in line:
        if ch == "%" and not escaped:
            break
        escaped = (ch == "\\") and not escaped
        out.append(ch)
    return "".join(out)


def _line_tag(m: re.Match[str], start_group: int) -> str:
    g = m.groups()
    if len(g) >= start_group + 2 and g[start_group] and g[start_group + 1]:
        return f"L{g[start_group]}-{g[start_group + 1]}"
    if len(g) >= start_group + 3 and g[start_group + 2]:
        return f"L{g[start_group + 2]}"
    return ""


def parse_tex_log(log_text: str, *, overfull_pt: float = 6.0, error_overfull_pt: float = 12.0) -> list[dict]:
    """Parse a LaTeX .log with file-stack tracking so every overflow names its .tex file and line."""
    findings: list[dict] = []
    file_stack: list[str] = []

    def current_file() -> str:
        return file_stack[-1] if file_stack else "main.tex"

    lines = log_text.splitlines()
    for idx, line in enumerate(lines):
        # Approximate TeX's paren file stack on each line.
        pos = 0
        while pos < len(line):
            ch = line[pos]
            if ch == "(":
                m_file = _TEX_FILE_RE.match(line, pos)
                file_stack.append(m_file.group(1) if m_file else "<group>")
            elif ch == ")" and file_stack:
                file_stack.pop()
            pos += 1

        if m := _OVERFULL_HBOX_RE.search(line):
            pts = float(m.group(1))
            if pts >= overfull_pt:
                loc = _line_tag(m, 1)
                findings.append({
                    "severity": "error" if pts >= error_overfull_pt else "warning",
                    "kind": "OVERFULL_HBOX",
                    "file": current_file(),
                    "line": loc,
                    "message": f"Overfull \\hbox ({pts:.1f}pt too wide)",
                })
        elif m := _OVERFULL_VBOX_RE.search(line):
            pts = float(m.group(1))
            findings.append({
                "severity": "error",
                "kind": "OVERFULL_VBOX",
                "file": current_file(),
                "line": _line_tag(m, 1),
                "message": f"Overfull \\vbox ({pts:.1f}pt too high)",
            })
        elif m := _UNDERFULL_BOX_RE.search(line):
            if int(m.group(2)) >= 10000:
                findings.append({
                    "severity": "warning",
                    "kind": f"UNDERFULL_{m.group(1).upper()}",
                    "file": current_file(),
                    "line": _line_tag(m, 2),
                    "message": f"Underfull \\{m.group(1)} (badness {m.group(2)})",
                })
        elif m := _FLOAT_TOO_LARGE_RE.search(line):
            findings.append({
                "severity": "error",
                "kind": "FLOAT_TOO_LARGE",
                "file": current_file(),
                "line": f"L{m.group(2)}" if m.group(2) else "",
                "message": f"Float too large for page by {m.group(1)}pt",
            })
        elif m := _UNDEF_REF_RE.search(line):
            findings.append({
                "severity": "error",
                "kind": "UNDEFINED_REF",
                "file": current_file(),
                "line": f"L{m.group(3)}" if m.group(3) else "",
                "message": f"Undefined {m.group(1).lower()} `{m.group(2)}`",
            })
        elif m := _MISSING_CHAR_RE.search(line):
            findings.append({
                "severity": "error",
                "kind": "MISSING_CHAR",
                "file": current_file(),
                "line": "",
                "message": f"Missing character `{m.group(1)}` in font {m.group(2).strip()}",
            })
        elif _FATAL_LOG_RE.search(line):
            findings.append({
                "severity": "error",
                "kind": "COMPILE_ABORTED",
                "file": current_file(),
                "line": "",
                "message": line.strip(),
            })
    return findings


def _to_pt(value: float, unit: str) -> float:
    scale = {"pt": 1.0, "em": 10.0, "ex": 4.5, "cm": 28.35, "mm": 2.835, "in": 72.0}
    return value * scale.get(unit, 1.0)


def scan_tex_sources(tex_files: list[Path], *, venue: str = "arxiv") -> list[dict]:
    """Scan .tex sources for desk-reject spacing hacks and table/float anti-patterns."""
    findings: list[dict] = []
    strict = venue.lower() != "arxiv"
    for path in tex_files:
        try:
            raw_lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            continue
        clean_lines = [_strip_tex_comment(ln) for ln in raw_lines]
        for lno, line in enumerate(clean_lines, 1):
            if m := _VSPACE_HACK_RE.search(line):
                pts = _to_pt(float(m.group(1)), m.group(2))
                if pts >= 4.0:
                    findings.append({
                        "severity": "error" if strict else "warning",
                        "kind": "NEGATIVE_VSPACE_HACK",
                        "file": path.name,
                        "line": f"L{lno}",
                        "message": f"Negative vertical space hack (-{m.group(1)}{m.group(2)})",
                    })
            if m := _SPACING_HACK_RE.search(line):
                val = float(m.group(1) or m.group(2))
                if val < 0.98:
                    findings.append({
                        "severity": "error" if strict else "warning",
                        "kind": "SPACING_HACK",
                        "file": path.name,
                        "line": f"L{lno}",
                        "message": f"Line-spacing compression ({val}) risks desk-reject",
                    })
            if m := _GEOM_HACK_RE.search(line):
                findings.append({
                    "severity": "error" if strict else "warning",
                    "kind": "GEOMETRY_HACK",
                    "file": path.name,
                    "line": f"L{lno}",
                    "message": f"Margin/geometry shrink on \\{m.group(1)}",
                })
            if _TABULAR_BAR_RE.search(line):
                findings.append({
                    "severity": "warning",
                    "kind": "TABLE_VERTICAL_RULES",
                    "file": path.name,
                    "line": f"L{lno}",
                    "message": "Vertical rules (|) in tabular; prefer clean booktabs",
                })
            if "\\hline" in line:
                findings.append({
                    "severity": "warning",
                    "kind": "TABLE_HLINE",
                    "file": path.name,
                    "line": f"L{lno}",
                    "message": "Raw \\hline in table; use \\toprule/\\midrule/\\bottomrule",
                })
            if m := _SCALEBOX_RE.search(line):
                if float(m.group(1)) < 0.80:
                    findings.append({
                        "severity": "warning",
                        "kind": "SHRINK_SCALEBOX",
                        "file": path.name,
                        "line": f"L{lno}",
                        "message": f"\\scalebox{{{m.group(1)}}} shrinks below readable 0.80x",
                    })
            if "\\tiny" in line:
                findings.append({
                    "severity": "warning",
                    "kind": "TINY_FONT",
                    "file": path.name,
                    "line": f"L{lno}",
                    "message": "\\tiny text hurts readability; use \\footnotesize or \\small",
                })
            if m := _FLOAT_H_RE.search(line):
                findings.append({
                    "severity": "warning",
                    "kind": "FLOAT_H_SPAM",
                    "file": path.name,
                    "line": f"L{lno}",
                    "message": f"Float specifier [{m.group(1)}] often causes mid-page white voids; prefer [t] or [tp]",
                })

        # Check table caption placement (should appear before \end{tabular} inside table environments).
        joined = "\n".join(clean_lines)
        for m_tab in re.finditer(r"\\begin\{(table\*?)\}(.*?)\\end\{\1\}", joined, re.S):
            body = m_tab.group(2)
            cap_pos = body.find("\\caption")
            end_tab_pos = max(body.rfind("\\end{tabular}"), body.rfind("\\end{tabularx}"))
            if cap_pos != -1 and end_tab_pos != -1 and cap_pos > end_tab_pos:
                lno = joined[: m_tab.start()].count("\n") + 1
                findings.append({
                    "severity": "warning",
                    "kind": "TABLE_CAPTION_BELOW",
                    "file": path.name,
                    "line": f"L{lno}",
                    "message": "Table \\caption sits below tabular; venue style expects captions above tables",
                })
    return findings


def _normalize_prose(text: str) -> list[str]:
    cleaned = "\n".join(_strip_tex_comment(ln) for ln in text.splitlines())
    cleaned = _TEXORPDF_RE.sub(r"\1", cleaned)
    cleaned = _STRIP_LAYOUT_CMDS.sub(" ", cleaned)
    cleaned = re.sub(
        r"\\(?:begin|end)\{(?:aligned|split|multline|gather|align|equation|table\*?|figure\*?|tabularx?|adjustbox)\*?\}(?:\[[^\]]*\])?(?:\{[^}]*\})*",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"\\makecell(?:\[[^\]]*\])?", " ", cleaned)
    cleaned = re.sub(r"\\resizebox\{[^}]*\}\{[^}]*\}", " ", cleaned)
    cleaned = re.sub(r"\\scalebox\{[^}]*\}(?:\[[^\]]*\])?", " ", cleaned)
    cleaned = cleaned.replace("\\\\", " ").replace("&", " ")
    # Collapse pure-layout whitespace differences while preserving prose words, math operators, kana/hangul/greek, & numbers.
    tokens = re.findall(r"[A-Za-z0-9_.\-%]+|[<>=≠≤≥±+\-*/]+|[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af\u0370-\u03ff]+", cleaned)
    return tokens


_TEX_INPUT_RE = re.compile(r"\\(?:input|include)\{([^}]+)\}")


def _expand_tex_inputs(tex_files: list[Path], root: Path | None = None) -> list[Path]:
    ordered: list[Path] = []
    seen: set[Path] = set()
    queue: list[Path] = list(tex_files)
    while queue:
        p = queue.pop(0)
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp in seen or not rp.is_file():
            continue
        seen.add(rp)
        ordered.append(p)
        text = rp.read_text(encoding="utf-8-sig", errors="replace")
        for raw_line in text.splitlines():
            code = re.split(r"(?<!\\)%", raw_line, maxsplit=1)[0]
            for m in _TEX_INPUT_RE.finditer(code):
                rel = m.group(1).strip()
                if not rel:
                    continue
                if not Path(rel).suffix:
                    rel = f"{rel}.tex"
                for base in ([root] if root else []) + [rp.parent]:
                    cand = (base / rel)
                    if cand.is_file():
                        queue.append(cand)
                        break
    return ordered


def extract_prose_fingerprint(tex_files: list[Path], root: Path | None = None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in sorted(_expand_tex_inputs(tex_files, root=root)):
        key = str(p.relative_to(root)).replace("\\", "/") if root and p.is_relative_to(root) else p.name
        out[key] = _normalize_prose(p.read_text(encoding="utf-8-sig", errors="replace"))
    return out


def snapshot_prose(tex_files: list[Path], out_path: Path, root: Path | None = None) -> dict[str, list[str]]:
    fp = extract_prose_fingerprint(tex_files, root=root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return fp


def verify_prose(tex_files: list[Path], snapshot_path: Path, root: Path | None = None) -> list[dict]:
    expected: dict[str, list[str]] = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
    actual = extract_prose_fingerprint(tex_files, root=root)
    diffs: list[dict] = []
    for key in sorted(set(expected) | set(actual)):
        exp_toks = expected.get(key)
        act_toks = actual.get(key)
        if exp_toks is None:
            diffs.append({"file": key, "reason": "unexpected new file"})
        elif act_toks is None:
            diffs.append({"file": key, "reason": "missing file from snapshot"})
        elif exp_toks != act_toks:
            pos = next((i for i, (a, b) in enumerate(zip(exp_toks, act_toks)) if a != b), min(len(exp_toks), len(act_toks)))
            diffs.append({
                "file": key,
                "reason": f"prose/number drift at token {pos}: expected {exp_toks[pos:pos+4]} != got {act_toks[pos:pos+4]}",
            })
    return diffs


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for lo, hi in ordered[1:]:
        if lo <= merged[-1][1] + 2.0:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def audit_pdf(
    pdf_path: Path,
    *,
    venue: str = "arxiv",
    page_limit: int | None = None,
    margin_pt: float = 24.0,
    render_dir: Path | None = None,
    dpi: int = 150,
) -> list[dict]:
    """Audit PDF page limit, margins, box collisions, orphan headings, white voids, and render annotated PNGs."""
    try:
        import fitz
    except ImportError:
        return [{"severity": "warning", "kind": "NO_PYMUPDF", "file": pdf_path.name, "line": "", "message": "PyMuPDF missing; PDF visual audit skipped"}]

    if not pdf_path.is_file():
        return [{"severity": "warning", "kind": "NO_PDF", "file": pdf_path.name, "line": "", "message": f"PDF not found: {pdf_path.name}"}]

    doc = fitz.open(pdf_path)
    findings: list[dict] = []
    limit = page_limit if page_limit is not None else VENUE_LIMITS.get(venue.lower())

    main_pages = len(doc)
    for idx, page in enumerate(doc):
        if idx > 0 and _SECTION_BOUNDARY_RE.search(page.get_text("text")):
            main_pages = idx
            break
    if limit is not None and main_pages > limit:
        findings.append({
            "severity": "error",
            "kind": "PAGE_LIMIT",
            "file": pdf_path.name,
            "line": f"p1-p{main_pages}",
            "message": f"Main body uses {main_pages} pages, exceeding {venue} limit {limit}",
        })

    per_page_boxes: dict[int, list[tuple[str, str, tuple[float, float, float, float]]]] = {}
    for p_idx, page in enumerate(doc):
        w, h = float(page.rect.width), float(page.rect.height)
        raw_dict = page.get_text("dict")
        blocks: list[dict] = []
        font_sizes: list[float] = []
        for blk in raw_dict.get("blocks", []):
            bbox = tuple(float(v) for v in blk.get("bbox", (0, 0, 0, 0)))
            if bbox[2] - bbox[0] < 2.0 or bbox[3] - bbox[1] < 2.0:
                continue
            btype = blk.get("type", 0)
            text = ""
            max_size = 0.0
            if btype == 0:
                lines_txt = []
                for ln in blk.get("lines", []):
                    spans_txt = []
                    for sp in ln.get("spans", []):
                        spans_txt.append(sp.get("text", ""))
                        sz = float(sp.get("size", 0.0))
                        if sz > 0:
                            font_sizes.append(sz)
                            max_size = max(max_size, sz)
                    lines_txt.append("".join(spans_txt))
                text = " ".join(" ".join(lines_txt).split())
                if not text:
                    continue
            blocks.append({"bbox": bbox, "type": btype, "text": text, "max_size": max_size})

        body_size = statistics.median(font_sizes) if font_sizes else 10.0
        page_marks: list[tuple[str, str, tuple[float, float, float, float]]] = []

        # 1) Margin breaches (skip running headers/footers that stay horizontally inside left/right margins).
        for blk in blocks:
            x0, y0, x1, y1 = blk["bbox"]
            is_running = blk["type"] == 0 and len(blk["text"]) <= 60 and x0 >= margin_pt and x1 <= w - margin_pt and (y1 <= 36.0 or y0 >= h - 36.0)
            if is_running:
                continue
            if x0 < margin_pt or y0 < margin_pt or x1 > w - margin_pt or y1 > h - margin_pt:
                findings.append({
                    "severity": "error",
                    "kind": "MARGIN",
                    "file": pdf_path.name,
                    "line": f"p{p_idx + 1}",
                    "message": f"p{p_idx + 1}: block breaches {margin_pt:.0f}pt margin",
                })
                page_marks.append(("error", "MARGIN", blk["bbox"]))

        # 2) Box overlaps / collisions.
        for i, ba in enumerate(blocks):
            ax0, ay0, ax1, ay1 = ba["bbox"]
            area_a = max(1.0, (ax1 - ax0) * (ay1 - ay0))
            for bb in blocks[i + 1 :]:
                bx0, by0, bx1, by1 = bb["bbox"]
                iw = min(ax1, bx1) - max(ax0, bx0)
                ih = min(ay1, by1) - max(ay0, by0)
                if iw > 3.0 and ih > 3.0:
                    area_b = max(1.0, (bx1 - bx0) * (by1 - by0))
                    if (iw * ih) / min(area_a, area_b) > 0.12:
                        union = (min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1))
                        findings.append({
                            "severity": "error",
                            "kind": "OVERLAP",
                            "file": pdf_path.name,
                            "line": f"p{p_idx + 1}",
                            "message": f"p{p_idx + 1}: overlapping content boxes",
                        })
                        page_marks.append(("error", "OVERLAP", union))

        # 3) Orphan headings near bottom of page with no body text below in same column.
        for blk in blocks:
            if blk["type"] != 0 or len(blk["text"]) > 90:
                continue
            x0, y0, x1, y1 = blk["bbox"]
            looks_heading = bool(_HEADING_TEXT_RE.match(blk["text"])) or blk["max_size"] >= body_size * 1.12
            if looks_heading and y0 >= h * 0.85:
                has_below = any(
                    other is not blk
                    and other["bbox"][1] > y1 - 1.0
                    and min(x1, other["bbox"][2]) > max(x0, other["bbox"][0])
                    for other in blocks
                )
                if not has_below:
                    findings.append({
                        "severity": "warning",
                        "kind": "ORPHAN",
                        "file": pdf_path.name,
                        "line": f"p{p_idx + 1}",
                        "message": f"p{p_idx + 1}: orphan heading `{blk['text'][:40]}` near page bottom",
                    })
                    page_marks.append(("warning", "ORPHAN", blk["bbox"]))

        # 4) Large mid-page whitespace voids on non-final main pages.
        if p_idx + 1 < main_pages and len(blocks) >= 2:
            merged_y = _merge_intervals([(b["bbox"][1], b["bbox"][3]) for b in blocks])
            for (_, prev_end), (next_start, _) in zip(merged_y, merged_y[1:]):
                if next_start - prev_end >= h * 0.32:
                    findings.append({
                        "severity": "warning",
                        "kind": "VOID",
                        "file": pdf_path.name,
                        "line": f"p{p_idx + 1}",
                        "message": f"p{p_idx + 1}: large vertical whitespace void ({next_start - prev_end:.0f}pt)",
                    })
                    page_marks.append(("warning", "VOID", (margin_pt, prev_end, w - margin_pt, next_start)))

        # 5) Sparse trailing main-body page on conference venues.
        if venue.lower() != "arxiv" and p_idx + 1 == main_pages and main_pages >= 2 and blocks:
            max_y1 = max(b["bbox"][3] for b in blocks)
            min_y0 = min(b["bbox"][1] for b in blocks)
            if (max_y1 - min_y0) < h * 0.15:
                findings.append({
                    "severity": "warning",
                    "kind": "SPARSE",
                    "file": pdf_path.name,
                    "line": f"p{p_idx + 1}",
                    "message": f"p{p_idx + 1}: trailing main page uses <15% height",
                })
        per_page_boxes[p_idx] = page_marks

    if render_dir is not None:
        render_dir.mkdir(parents=True, exist_ok=True)
        for p_idx, page in enumerate(doc):
            for sev, label, (x0, y0, x1, y1) in per_page_boxes.get(p_idx, []):
                color = (0.85, 0.15, 0.15) if sev == "error" else (0.85, 0.55, 0.05)
                shape = page.new_shape()
                shape.draw_rect(fitz.Rect(x0, y0, x1, y1))
                shape.finish(color=color, width=1.5)
                shape.commit()
                page.insert_text((x0 + 2, max(12.0, y0 - 3)), label, fontsize=8, color=color)
            pix = page.get_pixmap(dpi=dpi)
            pix.save(render_dir / f"page_{p_idx + 1:02d}.png")

    return findings


def _discover_inputs(target: Path, tex_args: list[Path] | None, log_arg: Path | None, pdf_arg: Path | None):
    if target.is_file():
        root = target.parent
        tex_files = tex_args or ([target] if target.suffix == ".tex" else [])
        log_path = log_arg or (target.with_suffix(".log") if target.with_suffix(".log").is_file() else None)
        pdf_path = pdf_arg or (target.with_suffix(".pdf") if target.with_suffix(".pdf").is_file() else None)
        return root, tex_files, log_path, pdf_path
    root = target
    tex_files = tex_args or sorted(root.rglob("*.tex"))
    log_path = log_arg or next(iter(sorted(root.glob("*.log"))), None)
    pdf_path = pdf_arg or next(iter(sorted(root.glob("*.pdf"))), None)
    return root, tex_files, log_path, pdf_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", type=Path, help="paper directory or main .tex file")
    ap.add_argument("--tex", nargs="+", type=Path, default=None)
    ap.add_argument("--log", type=Path, default=None)
    ap.add_argument("--pdf", type=Path, default=None)
    ap.add_argument("--venue", default="arxiv")
    ap.add_argument("--page-limit", type=int, default=None)
    ap.add_argument("--overfull-pt", type=float, default=6.0)
    ap.add_argument("--render-annotated", action="store_true")
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--snapshot-prose", type=Path, default=None)
    ap.add_argument("--verify-prose", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root, tex_files, log_path, pdf_path = _discover_inputs(args.target, args.tex, args.log, args.pdf)
    if args.snapshot_prose is not None:
        fp = snapshot_prose(tex_files, args.snapshot_prose, root=root)
        print(f"PROSE SNAPSHOT SAVED ({len(fp)} files) -> {args.snapshot_prose}")
        return 0

    findings: list[dict] = []
    if log_path and log_path.is_file():
        findings.extend(parse_tex_log(log_path.read_text(encoding="utf-8", errors="replace"), overfull_pt=args.overfull_pt))
    findings.extend(scan_tex_sources(tex_files, venue=args.venue))
    if pdf_path and pdf_path.is_file():
        render_dir = (args.out_dir or (root / ".gate_cache" / "typeset_preview")) if args.render_annotated else None
        findings.extend(audit_pdf(pdf_path, venue=args.venue, page_limit=args.page_limit, render_dir=render_dir))
    if args.verify_prose is not None:
        for diff in verify_prose(tex_files, args.verify_prose, root=root):
            findings.append({
                "severity": "error",
                "kind": "PROSE_DRIFT",
                "file": diff["file"],
                "line": "",
                "message": diff["reason"],
            })

    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    if args.json:
        print(json.dumps({"errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
    else:
        for item in errors + warnings:
            loc = f":{item['line']}" if item["line"] else ""
            print(f"[{item['severity'].upper()}] {item['kind']} {item['file']}{loc} — {item['message']}")
        print(f"LAYOUT AUDIT: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

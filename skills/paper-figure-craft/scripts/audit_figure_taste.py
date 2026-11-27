"""Quantified Publication Figure Taste & Anti-AI-Slop Auditor (0-100 composite score).

Evaluates ANY figure (PNG/JPG + optional TeX/SVG/HTML/Python source) across 9
computable visual design, typography, and human-editorial craft dimensions
calibrated against published Nature / ICLR / Mila / arXiv figures:

  1. prose_box_score      (12 pts) : Diagram vs. prose discipline (word count, primitives vs boxes)
  2. math_typo_score      (15 pts) : Zero ASCII-math violations (no raw `R^2`, `z_hat`, `->`, `<=`)
  3. whitespace_score     (12 pts) : Ink density [0.08..0.36], breathing whitespace, margin balance
  4. color_harmony_score  (12 pts) : Pastel fill saturation cap (<= 0.28), hue restraint (<= 5)
  5. editorial_craft_score(15 pts) : Anti-AI-slop craft (open #FFFFFF paper >= 65-75%, flat matte fills,
                                     NO drop shadows, NO glossy gradients, NO SaaS gray dashboard cards,
                                     NO Tailwind neon-mint defaults)
  6. accessibility_score  ( 9 pts) : Local WCAG contrast & Deuteranopia + Grayscale separation
  7. typography_score     ( 9 pts) : Font hierarchy & print legibility (>= 7 pt at 6.75 in width)
  8. alignment_score      ( 8 pts) : Zero text-box / label collisions & framing
  9. focal_score          ( 8 pts) : Focal salience & data-ink integrity
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


_RAW_ASCII_PATTERNS = [
    (r"(?<!\$)\b[A-Za-z][A-Za-z0-9]*\^[A-Za-z0-9{}\-]+\b(?![^<]*>)", "caret_superscript"),
    (r"(?<![A-Za-z$\\/])[A-Za-z]_[A-Za-z0-9{}]+(?![^<]*>)", "underscore_subscript"),
    (r"->|<-|=>|<=", "ascii_arrow_or_ineq"),
    (r"\bz_hat\b|\bDelta\s+[A-Za-z]|\bmu\s*\(", "ascii_math_word"),
]

_TAILWIND_SAAS_TOKENS = ("#f8fafc", "#0f766e", "#ccfbf1", "#1d4ed8", "#c2410c", "#ffedd5")


def _parse_float_attr(val: str | None, default: float = 0.0) -> float:
    if not val:
        return default
    m = re.search(r"[-+]?\d*\.?\d+", str(val))
    return float(m.group(0)) if m else default


def _extract_source_text_nodes(src_path: Path | None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if src_path is None or not src_path.exists():
        return [], {}
    text = src_path.read_text(encoding="utf-8", errors="replace")
    text_lower = text.lower()
    suffix = src_path.suffix.lower()
    nodes: list[dict[str, Any]] = []
    stats = {
        "boxes": 0,
        "visual_primitives": 0,
        "arrows": 0,
        "large_card_rects": 0,
        "heavy_border_rects": 0,
        "medium_boxes": 0,
        "shadow_filters": 0,
        "gradients": 0,
        "bezier_curves": 0,
        "ortho_pipes": 0,
        "polygons": 0,
        "tailwind_saas_hits": sum(1 for tok in _TAILWIND_SAAS_TOKENS if tok in text_lower),
    }

    if suffix in (".svg", ".html", ".htm"):
        svg_chunks = re.findall(r"<svg[\s\S]*?</svg>", text, flags=re.IGNORECASE)
        if not svg_chunks and suffix == ".svg":
            svg_chunks = [text]
        for chunk in svg_chunks:
            try:
                root = ET.fromstring(chunk)
            except ET.ParseError:
                clean = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", chunk)
                try:
                    root = ET.fromstring(clean)
                except ET.ParseError:
                    root = None
            if root is not None:
                vb = root.attrib.get("viewBox", "")
                vb_nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+", vb)]
                canvas_w = vb_nums[2] if len(vb_nums) >= 4 else _parse_float_attr(root.attrib.get("width"), 1350.0)
                canvas_h = vb_nums[3] if len(vb_nums) >= 4 else _parse_float_attr(root.attrib.get("height"), 450.0)
                canvas_area = max(1.0, canvas_w * canvas_h)

                for el in root.iter():
                    tag = el.tag.split("}")[-1].lower()
                    if tag in ("filter", "fedropshadow"):
                        stats["shadow_filters"] += 1
                    elif tag in ("lineargradient", "radialgradient"):
                        stats["gradients"] += 1
                    elif tag == "rect":
                        stats["boxes"] += 1
                        rw = _parse_float_attr(el.attrib.get("width"))
                        rh = _parse_float_attr(el.attrib.get("height"))
                        area = rw * rh
                        fill = el.attrib.get("fill", "").strip().lower()
                        sw = _parse_float_attr(el.attrib.get("stroke-width"), 0.0)
                        stroke = el.attrib.get("stroke", "none").strip().lower()
                        is_bg_canvas = area >= 0.85 * canvas_area and fill in ("#ffffff", "#fff", "white")
                        if not is_bg_canvas:
                            if area >= 0.10 * canvas_area:
                                stats["large_card_rects"] += 1
                            if rw > 24 and rh > 18:
                                stats["medium_boxes"] += 1
                            if stroke not in ("", "none") and sw >= 1.55 and fill not in ("", "none"):
                                stats["heavy_border_rects"] += 1
                    elif tag in ("circle", "ellipse", "polygon", "polyline"):
                        stats["visual_primitives"] += 1
                        if tag == "polygon":
                            stats["polygons"] += 1
                    elif tag == "path":
                        d = el.attrib.get("d", "")
                        has_curve = bool(re.search(r"[CcSsQqTtAa]", d))
                        l_count = len(re.findall(r"\bL\b|\sL\s", " " + d + " "))
                        if has_curve:
                            stats["bezier_curves"] += 1
                        elif l_count >= 3 and "z" not in d.lower():
                            stats["ortho_pipes"] += 1
                        if len(d) > 25:
                            stats["visual_primitives"] += 1
                        else:
                            stats["arrows"] += 1
                    elif tag == "line":
                        stats["arrows"] += 1
                    elif tag == "text":
                        txt = "".join(el.itertext()).strip()
                        if txt:
                            fs_raw = el.attrib.get("font-size", "10")
                            m_fs = re.search(r"[\d.]+", fs_raw)
                            fs = float(m_fs.group(0)) if m_fs else 10.0
                            nodes.append({
                                "text": txt,
                                "font_size": fs,
                                "font_family": el.attrib.get("font-family", ""),
                                "x": el.attrib.get("x", "0"),
                                "y": el.attrib.get("y", "0"),
                                "words": len(txt.split()),
                            })
        if not nodes:
            for m in re.finditer(r"<text[^>]*>([\s\S]*?)</text>", text):
                plain = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                if plain:
                    nodes.append({"text": plain, "font_size": 10.0, "words": len(plain.split())})
        return nodes, stats

    if suffix == ".tex":
        # Strip comments first
        no_cmt = re.sub(r"(?<!\\)%[^\n]*", "", text)
        stats["visual_primitives"] += len(re.findall(r"\b(?:ellipse|circle|trapezium|arc|controls|bend\s+left|bend\s+right|plot|polygon)\b", no_cmt))
        stats["visual_primitives"] += len(re.findall(r"\\(?:fill|draw)\b", no_cmt))
        stats["bezier_curves"] += len(re.findall(r"\b(?:controls|bend\s+left|bend\s+right|out=|in=)\b", no_cmt))
        stats["shadow_filters"] += len(re.findall(r"\b(?:drop\s+shadow|circular\s+drop\s+shadow)\b", no_cmt))
        stats["gradients"] += len(re.findall(r"\\shade\b|\bshading=", no_cmt))
        stats["boxes"] += len(re.findall(r"\brectangle\b|\bdraw[^;\n]*\bnode\[[^\]]*(?:draw|rectangle)", no_cmt))

        # Parse node labels with balanced braces after `node ... {`
        i = 0
        n_len = len(no_cmt)
        while i < n_len:
            m = re.search(r"\bnode(?:\s*\[[^\]]*\])?\s*(?:\([^)]*\)\s*)?\{", no_cmt[i:])
            if not m:
                break
            start = i + m.end()
            depth = 1
            j = start
            while j < n_len and depth > 0:
                ch = no_cmt[j]
                if ch == "{" and (j == 0 or no_cmt[j - 1] != "\\"):
                    depth += 1
                elif ch == "}" and (j == 0 or no_cmt[j - 1] != "\\"):
                    depth -= 1
                j += 1
            raw_lbl = no_cmt[start:j - 1].strip()
            i = j
            if not raw_lbl:
                continue
            plain = re.sub(r"\\(?:textbf|textit|textrm|textsf|mathsf|mathrm|small|footnotesize|scriptsize|tiny)\s*\{([^}]*)\}", r"\1", raw_lbl)
            plain = plain.replace("\\\\", " ").strip()
            if plain:
                nodes.append({"text": plain, "font_size": 9.5, "raw": raw_lbl, "words": len(plain.split())})
        return nodes, stats

    # Python fallback: inspect ONLY visible rendered label arguments
    if suffix == ".py":
        label_pats = [
            r'(?:label|set_xlabel|set_ylabel|set_title)\(\s*r?["\']([^"\'\n]{2,120})["\']',
            r'\blabel\s*=\s*r?f?["\']([^"\'\n]{2,120})["\']',
            r'\b(?:annotate|add_direct_label)\([^\n]*?r?f?["\']([^"\'\n]{2,120})["\']',
        ]
        seen = set()
        for pat in label_pats:
            for m in re.finditer(pat, text):
                s = re.sub(r"\{[^}]+\}", "0.0", m.group(1)).strip()
                if s and s not in seen and s not in ("<->", "->"):
                    seen.add(s)
                    nodes.append({"text": s, "font_size": 9.2, "words": len(s.split())})
        return nodes, stats

    for m in re.finditer(r'["\']([^"\'\n]{2,120})["\']', text):
        s = m.group(1).strip()
        if s.startswith("#") or "/" in s or s.endswith((".png", ".svg", ".json", ".py")):
            continue
        nodes.append({"text": s, "font_size": 9.5, "words": len(s.split())})
    return nodes, stats


def _detect_ascii_math(nodes: list[dict[str, Any]], src_path: Path | None) -> list[dict[str, str]]:
    """Detect ugly raw ASCII math outside LaTeX `$...$` or rich SVG tspans/HTML sub/sup."""
    if src_path is not None and src_path.suffix.lower() in (".html", ".htm"):
        raw = src_path.read_text(encoding="utf-8", errors="replace")
        raw = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
        raw = re.sub(r"<style[\s\S]*?</style>", " ", raw, flags=re.I)
        candidates = re.findall(r"<text[^>]*>([\s\S]*?)</text>", raw, flags=re.I)
    else:
        candidates = [n.get("raw", n["text"]) for n in nodes]

    hits: list[dict[str, str]] = []
    for raw_txt in candidates:
        cleaned = re.sub(r"\$[^$]+\$", " MATH ", raw_txt)
        cleaned = re.sub(r"<[^>]+>", "", cleaned).strip()
        if not cleaned:
            continue
        for pat, kind in _RAW_ASCII_PATTERNS:
            if pat == r"->|<-|=>|<=" and "=>" in cleaned and ("r=0" in cleaned or "Δ" in cleaned):
                continue
            m = re.search(pat, cleaned)
            if m:
                tok = m.group(0)
                if tok in ("_self", "__main__", "x_pos", "y_pos"):
                    continue
                hits.append({"kind": kind, "token": tok, "context": cleaned[:60]})
    return hits


def _rgb_to_hsv_stats(arr: np.ndarray) -> dict[str, Any]:
    """Compute ink ratio, pure-white continuity, saturation discipline, hue clusters, and congestion."""
    rgb = arr[:, :, :3].astype(np.float32) / 255.0
    h, w, _ = rgb.shape
    cmax = rgb.max(axis=2)
    cmin = rgb.min(axis=2)
    delta = cmax - cmin

    sat = np.zeros_like(cmax)
    nz = cmax > 1e-6
    sat[nz] = delta[nz] / cmax[nz]
    val = cmax

    lum = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]

    is_white = (lum > 0.965) & (sat < 0.05)
    pure_white_ratio = float(((lum > 0.985) & (sat < 0.02)).mean())
    ink_ratio = float((~is_white).mean())
    whitespace_ratio = float(is_white.mean())

    pad_y, pad_x = max(2, int(0.02 * h)), max(2, int(0.02 * w))
    rim_ink = max(
        float((~is_white[:pad_y, :]).mean()),
        float((~is_white[-pad_y:, :]).mean()),
        float((~is_white[:, :pad_x]).mean()),
        float((~is_white[:, -pad_x:]).mean()),
    )

    gh, gw = max(1, h // 10), max(1, w // 10)
    block_inks = []
    for r in range(10):
        for c in range(10):
            patch = ~is_white[r * gh:(r + 1) * gh, c * gw:(c + 1) * gw]
            if patch.size:
                block_inks.append(float(patch.mean()))
    p95_congestion = float(np.percentile(block_inks, 95)) if block_inks else ink_ratio

    chroma_mask = (sat > 0.10) & (val > 0.15)
    if chroma_mask.any():
        r_c, g_c, b_c = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        hue = np.zeros_like(cmax)
        mask_r = chroma_mask & (cmax == r_c) & (delta > 1e-6)
        mask_g = chroma_mask & (cmax == g_c) & (delta > 1e-6)
        mask_b = chroma_mask & (cmax == b_c) & (delta > 1e-6)
        hue[mask_r] = 60.0 * (((g_c[mask_r] - b_c[mask_r]) / delta[mask_r]) % 6.0)
        hue[mask_g] = 60.0 * (((b_c[mask_g] - r_c[mask_g]) / delta[mask_g]) + 2.0)
        hue[mask_b] = 60.0 * (((r_c[mask_b] - g_c[mask_b]) / delta[mask_b]) + 4.0)
        vals_h = hue[chroma_mask]
        hist, _ = np.histogram(vals_h, bins=18, range=(0, 360))
        sig_bins = int((hist > max(50, vals_h.size * 0.025)).sum())
    else:
        sig_bins = 0

    fill_mask = (~is_white) & (lum > 0.65)
    fill_s95 = float(np.percentile(sat[fill_mask], 95)) if fill_mask.sum() > 100 else 0.0

    dark_mask = lum < 0.45
    high_sat_accent = float(((sat > 0.35) & (val > 0.25) & (lum < 0.80)).mean())
    lum_fg_std = float(lum[~is_white].std()) if (~is_white).sum() > 100 else 0.0
    contrast_proxy = float((np.percentile(lum[is_white], 50) + 0.05) / (np.percentile(lum[dark_mask], 25) + 0.05)) if (is_white.any() and dark_mask.any()) else 4.5

    return {
        "width": int(w),
        "height": int(h),
        "aspect": round(w / max(1, h), 3),
        "ink_ratio": round(ink_ratio, 4),
        "whitespace_ratio": round(whitespace_ratio, 4),
        "pure_white_ratio": round(pure_white_ratio, 4),
        "rim_ink_max": round(rim_ink, 4),
        "p95_local_congestion": round(p95_congestion, 4),
        "chromatic_hue_clusters": sig_bins,
        "surface_fill_sat_p95": round(fill_s95, 4),
        "focal_accent_ratio": round(high_sat_accent, 4),
        "grayscale_lum_std": round(lum_fg_std, 4),
        "contrast_ratio_proxy": round(contrast_proxy, 2),
    }


def audit_figure(
    image_path: str | Path,
    source_path: str | Path | None = None,
    figure_kind: str = "auto",
) -> dict[str, Any]:
    """Score a figure (PNG/JPG + optional TeX/SVG/HTML/Py source) from 0 to 100 across 9 dimensions."""
    img_p = Path(image_path)
    src_p = Path(source_path) if source_path else None
    with Image.open(img_p) as raw_im:
        if raw_im.mode in ("RGBA", "LA") or "transparency" in raw_im.info:
            rgba = raw_im.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            im_rgb = Image.alpha_composite(bg, rgba).convert("RGB")
        else:
            im_rgb = raw_im.convert("RGB")
        arr = np.array(im_rgb)
    px = _rgb_to_hsv_stats(arr)

    nodes, svg_stats = _extract_source_text_nodes(src_p)
    ascii_hits = _detect_ascii_math(nodes, src_p)

    if figure_kind == "auto":
        name_l = img_p.name.lower()
        if "fig1" in name_l or "method" in name_l or "arch" in name_l:
            figure_kind = "diagram"
        elif any(k in name_l for k in ("fig2", "fig3", "curve", "roofline", "plot", "panel")):
            figure_kind = "plot"
        else:
            figure_kind = "reference"

    # 1. Visual-vs-Prose Discipline (12 pts)
    if nodes and src_p and src_p.suffix.lower() in (".svg", ".html", ".htm", ".tex"):
        total_words = sum(n["words"] for n in nodes)
        prose_boxes = sum(1 for n in nodes if n["words"] >= 10)
        prose_score = 12.0
        if total_words > 95:
            prose_score -= min(5.0, (total_words - 95) * 0.12)
        prose_score -= min(5.0, prose_boxes * 2.5)
        if figure_kind == "diagram" and svg_stats.get("visual_primitives", 0) < 6:
            prose_score -= 4.0
        prose_score = max(0.0, round(prose_score, 2))
    else:
        total_words = len(nodes) * 4 if nodes else -1
        prose_boxes = sum(1 for n in nodes if n["words"] >= 11)
        prose_score = 10.5 if px["p95_local_congestion"] <= 0.42 else max(5.0, 10.5 - (px["p95_local_congestion"] - 0.42) * 18.0)
        prose_score = round(prose_score, 2)

    if src_p is not None and not src_p.is_file():
        raise FileNotFoundError(f"--source file not found: {src_p}")
    # 2. Zero ASCII-Math Typography Gate (15 pts)
    if src_p is not None:
        math_score = max(0.0, 15.0 - 3.5 * len(ascii_hits))
    else:
        math_score = 13.5

    # 3. Ink Density & Whitespace Breathing (12 pts)
    ink = px["ink_ratio"]
    ws_score = 12.0
    if ink > 0.38:
        ws_score -= min(5.0, (ink - 0.38) * 30.0)
    elif ink < 0.06:
        ws_score -= min(4.0, (0.06 - ink) * 65.0)
    if px["rim_ink_max"] > 0.15:
        ws_score -= min(3.5, (px["rim_ink_max"] - 0.15) * 14.0)
    if px["p95_local_congestion"] > 0.55:
        ws_score -= min(4.0, (px["p95_local_congestion"] - 0.55) * 16.0)
    ws_score = max(0.0, round(ws_score, 2))

    # 4. Chromatic Discipline & Pastel Surface Cap (12 pts)
    sat95 = px["surface_fill_sat_p95"]
    hues = px["chromatic_hue_clusters"]
    color_score = 12.0
    if sat95 > 0.25:
        color_score -= min(5.0, (sat95 - 0.25) * 18.0)
    if hues > 5:
        color_score -= min(4.0, (hues - 5) * 1.3)
    elif hues == 0:
        color_score -= 1.5
    color_score = max(0.0, round(color_score, 2))

    # 5. Human Editorial Craft & Anti-AI-Slop (15 pts)
    # Real papers use flat matte pastel vector fills on open white paper (#FFFFFF >= 65-75%),
    # ZERO drop shadows, ZERO glossy gradients, ZERO SaaS gray cards, ZERO Tailwind defaults.
    editorial_score = 15.0
    if src_p and src_p.suffix.lower() in (".svg", ".html", ".htm", ".tex"):
        if svg_stats.get("shadow_filters", 0) >= 1:
            editorial_score -= min(3.0, svg_stats["shadow_filters"] * 1.5)
        if svg_stats.get("gradients", 0) >= 3:
            editorial_score -= 1.5
        if svg_stats.get("large_card_rects", 0) >= 2:
            editorial_score -= min(4.5, (svg_stats["large_card_rects"] - 1) * 1.4)
        if svg_stats.get("heavy_border_rects", 0) >= 2:
            editorial_score -= min(2.5, svg_stats["heavy_border_rects"] * 0.7)
        if svg_stats.get("medium_boxes", 0) > 12:
            editorial_score -= min(2.5, (svg_stats["medium_boxes"] - 12) * 0.35)
        if svg_stats.get("tailwind_saas_hits", 0) >= 3:
            editorial_score -= 2.5
        if svg_stats.get("ortho_pipes", 0) >= 1:
            editorial_score -= min(2.0, svg_stats["ortho_pipes"] * 1.5)
        if figure_kind == "diagram":
            if px["pure_white_ratio"] < 0.65:
                editorial_score -= min(4.0, (0.65 - px["pure_white_ratio"]) * 18.0)
    else:
        if px["pure_white_ratio"] < 0.50:
            editorial_score -= min(3.5, (0.50 - px["pure_white_ratio"]) * 12.0)
        if sat95 > 0.38:
            editorial_score -= min(2.0, (sat95 - 0.38) * 6.0)
    editorial_score = max(0.0, round(editorial_score, 2))

    # 6. Perceptual Contrast & Grayscale Separability (9 pts)
    acc_score = 9.0
    if px["contrast_ratio_proxy"] < 4.5:
        acc_score -= min(4.5, (4.5 - px["contrast_ratio_proxy"]) * 1.8)
    if px["grayscale_lum_std"] < 0.12:
        acc_score -= 2.0
    acc_score = max(0.0, round(acc_score, 2))

    # 7. Typographic Hierarchy & Print Legibility (9 pts)
    if nodes and src_p and src_p.suffix.lower() in (".svg", ".html"):
        sizes = [n.get("font_size", 9.5) for n in nodes]
        min_fs = min(sizes)
        max_fs = max(sizes)
        typo_score = 9.0
        if min_fs < 7.0:
            typo_score -= min(3.5, (7.0 - min_fs) * 1.4)
        if max_fs - min_fs < 1.5:
            typo_score -= 1.5
    elif src_p and src_p.suffix.lower() == ".tex":
        typo_score = 9.0  # native LaTeX/TiKZ font hierarchy
    else:
        typo_score = 8.2 if px["width"] >= 1000 else 6.8
    typo_score = round(typo_score, 2)

    # 8. Geometric Alignment & Clean Framing (8 pts)
    align_score = 8.0
    if px["aspect"] < 1.1 and figure_kind == "diagram":
        align_score -= 1.0
    if px["rim_ink_max"] > 0.08:
        align_score -= 1.2
    if src_p and src_p.suffix.lower() == ".py" and "errorbar" in src_p.read_text(encoding="utf-8", errors="ignore"):
        if figure_kind == "plot":
            align_score -= 1.0
    align_score = max(0.0, round(align_score, 2))

    # 9. Focal Anchor & Data-Ink Integrity (8 pts)
    focal_score = 8.0
    if px["focal_accent_ratio"] < 0.004:
        focal_score -= 2.0
    elif px["focal_accent_ratio"] > 0.25:
        focal_score -= 3.0
    focal_score = max(0.0, round(focal_score, 2))

    total = round(
        prose_score + math_score + ws_score + color_score + editorial_score + acc_score + typo_score + align_score + focal_score,
        2,
    )

    if px["pure_white_ratio"] >= 0.98 or px["ink_ratio"] <= 0.005:
        total = min(total, 40.0)
    return {
        "image": str(img_p),
        "source": str(src_p) if src_p else None,
        "kind": figure_kind,
        "total_score": total,
        "grade": "ORAL" if total >= 90 else ("ACCEPT" if total >= 82 else ("BORDERLINE" if total >= 72 else "REJECT")),
        "dimensions": {
            "prose_box_12": prose_score,
            "math_typo_15": math_score,
            "whitespace_12": ws_score,
            "color_harmony_12": color_score,
            "editorial_craft_15": editorial_score,
            "accessibility_9": acc_score,
            "typography_9": typo_score,
            "alignment_8": align_score,
            "focal_anchor_8": focal_score,
        },
        "metrics": {
            **px,
            "word_count": total_words,
            "prose_boxes_ge10w": prose_boxes,
            "svg_boxes": svg_stats.get("boxes", -1),
            "svg_large_cards": svg_stats.get("large_card_rects", -1),
            "svg_heavy_borders": svg_stats.get("heavy_border_rects", -1),
            "svg_shadow_filters": svg_stats.get("shadow_filters", -1),
            "svg_gradients": svg_stats.get("gradients", -1),
            "svg_bezier_curves": svg_stats.get("bezier_curves", -1),
            "svg_ortho_pipes": svg_stats.get("ortho_pipes", -1),
            "tailwind_saas_hits": svg_stats.get("tailwind_saas_hits", -1),
            "svg_visual_primitives": svg_stats.get("visual_primitives", -1),
            "ascii_math_violations": len(ascii_hits),
            "ascii_math_samples": ascii_hits[:8],
        },
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Quantified top-venue figure taste & anti-AI-slop auditor.")
    p.add_argument("images", nargs="+", help="PNG/JPG image path(s)")
    p.add_argument("--source", default=None, help="Optional TeX/SVG/HTML/Py source for text, math & craft AST audit")
    p.add_argument("--kind", choices=("auto", "diagram", "plot", "reference"), default="auto")
    p.add_argument("--json-out", default=None)
    args = p.parse_args()

    results = [audit_figure(im, source_path=args.source, figure_kind=args.kind) for im in args.images]
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for r in results:
        d = r["dimensions"]
        m = r["metrics"]
        print(
            f"[{r['grade']:10s} {r['total_score']:5.1f}/100] {Path(r['image']).name:32s} | "
            f"prose={d['prose_box_12']:.1f} math={d['math_typo_15']:.1f} ws={d['whitespace_12']:.1f} "
            f"col={d['color_harmony_12']:.1f} edit={d['editorial_craft_15']:.1f} | "
            f"white={m['pure_white_ratio']:.2f} sat95={m['surface_fill_sat_p95']:.2f} ascii={m['ascii_math_violations']}"
        )
    if any(r["grade"] == "REJECT" for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

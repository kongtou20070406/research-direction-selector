# Venue Recipes & XeLaTeX CJK Checklist

Quick reference for clean conference/preprint layout rules without desk-reject hacks.

## 1. Venue Page Limits & Column Geometry

| Venue | Columns | Main Body Limit | Caption Position | Required Packages / Notes |
|---|---|---|---|---|
| **ICML** | 2-col | 8 pages (refs/app unlimited) | **Above** tables, **Below** figures | Strict `icml2025.sty` / `icml2026.sty`; no `\vspace` hacks |
| **NeurIPS** | 1-col | 9 pages (refs/checklist/app unlimited) | **Above** tables, **Below** figures | `neurips_2025.sty`; check margin width & checklist |
| **ICLR** | 1-col | 10 pages (camera-ready; 9–10 review) | **Above** tables, **Below** figures | `iclr2026_conference.sty`; `\iclrfinalcopy` only when accepted |
| **CVPR** | 2-col | 8 pages (refs unlimited) | **Below** figures; **Above** or below tables (keep consistent) | Strict `cvpr.sty`; watch `figure*` on page 1 |
| **arXiv** | 1-col | Unlimited | **Above** tables, **Below** figures | Clean readable preprint (`microtype`, `booktabs`, `xurl`) |

## 2. Two-Column Float & Table Rules

- **Top-of-page `[t]` default**: Prefer `\begin{figure}[t]` and `\begin{table*}[t]`. Avoid `[h]`, `[h!]`, and `[H]` (causes mid-page voids and orphan text).
- **Pre-place `figure*` / `table*`**: LaTeX places two-column `*` floats on the *next* page at earliest; put them 1–2 paragraphs **before** their first `\ref{...}` so they land on the same spread as the discussion.
- **Keep float pages clean**: Use `[tp]` when a full-width figure/table nearly fills a column/page so LaTeX does not trap 2 lines of body text beneath it.

## 3. XeLaTeX Chinese (`ctex` / `sections_zh`) Checklist

1. **Missing glyphs**: Always check `layout_audit.py` for `Missing character: There is no ...` (especially Unicode `↑`, `↓`, `⚠`, `—`, `≥`, `≤` in math vs. text fonts). Load `amssymb` / `xeCJK` fallback or use LaTeX math `$\uparrow$`, `$\downarrow$`, `$\ge$`, `$\le$`.
2. **Line breaking & URLs**: Load `\usepackage{xurl}` and set `\emergencystretch=1.5em` so long English identifiers, URLs, and code symbols inside Chinese prose wrap cleanly without `Overfull \hbox`.
3. **Math-text spacing & `\texorpdfstring`**: Keep thin spaces around inline math consistent (`xeCJK` handles CJK–Latin spacing automatically; do not manually pad with `~` everywhere). Wrap math in section titles with `\texorpdfstring{...}{...}` to prevent `hyperref` bookmark warnings.
4. **Font embedding**: Compile with `xelatex -interaction=nonstopmode -halt-on-error` and verify `pdffonts` shows no Type 3 bitmap fonts.

# Quantified Figure Taste Rubric & Reference Benchmarks

Every generated figure passes an automated 9-dimension Taste & Anti-AI-Slop audit (`scripts/audit_figure_taste.py`, `0–100` composite score) calibrated against real top-venue papers before delivery.

## 1. The 9 Computable Dimensions (100 Points Total)

| # | Dimension | Max Pts | Target / Threshold | What It Catches |
|---|---|---:|---|---|
| 1 | **Visual-vs-Prose Discipline** (`prose_box_12`) | `12` | Concise captions (`<= 85` words), zero prose blocks (`>= 10` words), rich geometry primitives | Box-and-arrow text walls with no physical/geometric depiction |
| 2 | **Zero ASCII-Math Gate** (`math_typo_15`) | `15` | `0` raw `R^2`, `z_hat`, `h_{t+1}`, `->`, `<=`, `>=` outside LaTeX `$...$` or SVG sub/sup tags | Unrendered Markdown/code math (`fig1 v1` had `34` hits) |
| 3 | **Ink Density & Whitespace** (`whitespace_12`) | `12` | `ink_ratio` in `[0.06, 0.38]`, clean rims `< 15%`, local p95 congestion `< 0.55` | Crammed multi-row panels with colored boxes filling the page |
| 4 | **Chromatic & Pastel Cap** (`color_harmony_12`) | `12` | Large fill saturation `S_95 <= 0.25`, distinct hue clusters `<= 5` | Garish neon box fills (`S_95 = 0.45–0.96`) |
| 5 | **Human Editorial Craft & Anti-AI-Slop** (`editorial_craft_15`) | `15` | Pure `#FFFFFF` canvas (`>= 65%` for diagrams), **0 drop shadows, 0 glossy gradients, 0 SaaS gray outer cards, 0 Tailwind defaults, 0 ortho CAD pipes** | AI-generated web-dashboard cards, fake UI pills, KPI stat footers (`fig1 v2` scored `8.2/15`) |
| 6 | **Perceptual Contrast & Grayscale** (`accessibility_9`) | `9` | Foreground-to-bg proxy `>= 4.5:1`, luminance spread `>= 0.12` | Faint text or curves indistinguishable in B/W print |
| 7 | **Typographic Hierarchy** (`typography_9`) | `9` | Native LaTeX or `>= 7.0 pt` min print scale with clear hierarchy | Tiny unreadable subscripts or uniform sizing |
| 8 | **Geometric Alignment & Framing** (`alignment_8`) | `8` | Wide landscape balance, clean rims, CI ribbons instead of jagged error bars | Spiky error bars, clipped labels, tight margins |
| 9 | **Focal Anchor & Data-Ink** (`focal_anchor_8`) | `8` | Focal accent fraction `[0.004, 0.25]` separating *Ours* from muted baselines | Everything bright yellow/red so nothing stands out |

**Grading Tiers**: `>= 90 ORAL` · `82–89.9 ACCEPT` · `72–81.9 BORDERLINE` · `< 72 REJECT`. Both **`ascii_math_violations == 0`** and visual inspection (`read_file`) are mandatory regardless of score.

## 2. Real Paper & Before/After Benchmark Calibration (Updated 2026-11-22)

| Figure | Engine / Source | Score / Grade | Edit Craft | Pure White | `S_95` | ASCII Math | Takeaway |
|---|---|---:|---:|---:|---:|---:|---|
| *Fold2Reason* (`p1`) | arXiv 2026 | **`94.9` ORAL** | `15.0` | `0.92` | `0.21` | `0` | Crisp flat matte dusty-plum/steel bars & open white paper |
| *Huginn Fig. 2* | LaTeX/Ti*k*Z 2025 | **`91.2` ORAL** | `14.7` | `0.86` | `0.43` | `0` | Clean unrolled prelude/recurrent/coda blocks, dashed arcs |
| *MoTA Table/Fig. 1* | LaTeX 2026 | **`90.9` ORAL** | `15.0` | `0.89` | `0.00` | `0` | Pure white paper typography |
| *LeWorldModel Fig. 1* | Vector 2026 | **`90.2` ORAL** | `15.0` | `0.84` | `0.35` | `0` | Unrolled flow + single `SIGReg` zoom box, zero drop shadows |
| *DreamerV3 Fig. 1* | Nature 2025 | **`89.0` ACCEPT** | `15.0` | `0.80` | `0.37` | `0` | Environment frames + discrete grid latents + serif math |
| *Paper-A Fig. 1 (v1)* | Matplotlib boxes | `55.5` REJECT | — | — | `0.45` | `34` | Prose-box flowchart wall + ASCII math |
| *Paper-A Fig. 1 (v2)* | HTML SaaS cards | `80.8` BORDERLINE | `8.2` | `0.52` | `0.24` | `0` | Gray `#F8FAFC` outer cards + Tailwind pills + KPI footer |
| *Paper-A Fig. 1 (v3)* | Ti*k*Z (`helvet` + mint) | `90.7` ORAL | `15.0` | `0.82` | `0.16` | `0` | Flat-matte layout, but `helvet`+CM clash & monochrome mint |
| *Paper-A Fig. 1 (v4)* | Ti*k*Z (`sourcesanspro` multi-hue) | `92.9` ORAL | `15.0` | `0.83` | `0.22` | `0` | Multi-hue borderless triad, yet low focal contrast & too many colors |
| **`Paper-A Fig. 1 (v5)`** | **Ti*k*Z (`#000000` + 2-color)** | **`97.0` ORAL** | **`15.0`** | **`0.88`** | **`0.01`** | **`0`** | **Single-row 3-sec story: `#D55E00` residual arrow + `#007257` Ours + `same weights`** |
| **`Paper-A Fig. 2 (v5)`** | **Matplotlib (pure `#000000`)** | **`89.1` ACCEPT** | **`15.0`** | **`0.88`** | **`0.11`** | **`0`** | **No grid, `0.55pt` axes, exact `T=8` arrow, equal-aspect Push-T** |
| **`Paper-A Fig. 3 (v5)`** | **Matplotlib (Cleveland dots)** | **`96.7` ORAL** | **`15.0`** | **`0.95`** | **`0.15`** | **`0`** | **Unified color semantics (`Ours=#007257`) + log dumbbell chart** |

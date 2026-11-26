# Palettes, Typography, and Human Publication Figure Grammar (v5)

## 1. Pure-Black Ink + 2-Color Restraint (Nature / ICLR Oral Standard)

- **Ink & Structure**: Always use **pure `#000000` black** for axis spines (`0.55pt`), tick labels, captions, and regular text—never gray `#5C6573` or slate `#64748B` body copy that washes out in print.
- **No Gridlines by Default**: Set `axes.grid = False` in data plots; use `lines.linewidth = 1.0–1.3pt`, `markersize = 3.0–3.5pt`, no white halo circles around markers.
- **Panel Labels Over Subplot Titles**: Omit centered bold subplot headers `(a) Long Title`; use lowercase bold **`a`**, **`b`**, **`c`** at top-left outside the axes and put explanations in the caption.
- **Strict 2-Color Restraint for Mechanism Diagrams (Fig. 1)**:
  - Everything structural is **black ink (`0.75–0.9pt`) on white / whisper neutral (`#F2F3F5`)**.
  - Reserve **one** dominant warm accent (vermilion `#D55E00`, bold `1.6pt`) exclusively for the core feedback mechanism or error signal, plus **one** method color (forest green `#007257`) synced across **every** figure in the paper (Ours = `#007257` in Fig. 1, Fig. 2, AND Appendix Fig. 3!).
- **Data Plot Color Semantics**:
  - Primary Ours: `#007257` (forest green)
  - Secondary Ours / auxiliary curve: `#005AB5` (cobalt blue)
  - Contrast / Error / Shock: `#D55E00` (vermilion)
  - Non-recurrent baselines: unified gray `#666666` / `#888888`

## 2. Print Dimensions & Typography

- Full two-column width: `5.5 in` (single-row mechanism `5.5 × 1.7 in`; 3-panel plot `10.5 × 3.1 in` scaled neatly).
- **LaTeX/Ti*k*Z**: `\usepackage[default,scale=0.88]{sourcesanspro}` + `\usepackage{newtxmath}`.
- **Matplotlib**: `Arial / Helvetica`, `7–8 pt`, `mathtext.fontset = "stix"`, `axes.linewidth = 0.55`, pure `#000000` ink.

## 3. Anti-Overlap & Truth-Alignment Checklist (Mandatory Before Saving)

1. Zero number mismatch between plot annotations and manuscript tables (`T=8` arrow must point to `T=8`, NOT `T=2`!).
2. Keep the same model's color & linestyle identical across panels (`T_tr <= 12` solid blue in both `a` and `b`).
3. Use `set_aspect("equal", adjustable="box")` on any 2D physical spatial plot (never distort trajectories with unequal `x/y` scaling).
4. Use dot/dumbbell plots instead of log-scale bar charts when baseline bars share arbitrary log baselines or identical rows.
5. Limit Hero Fig. 1 to `<= 12` labels and at most **one** simple equation; put inequalities/theory proofs in §2.4, not glued into Fig. 1.

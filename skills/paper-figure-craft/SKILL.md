---
name: paper-figure-craft
description: Design, render, and quantitatively audit publication-grade scientific and ML conference figures (NeurIPS, ICML, ICLR, Nature) via TikZ, HTML+SVG, or Matplotlib. 绘制与审计顶会论文图表。Do NOT use for paper prose drafting (-> paper-write), literature search (-> paper_search), or auto-tracing model code into a layer diagram (-> nn-svg-diagram).
---

# Publication figures

- **Art Director vs. Graphics Worker**: The lead (main) model acts as Art Director—owning visual narrative, panel hierarchy, palette, exact symbols, and **final visual PNG sign-off** (stepping in directly for bespoke Fig. 1 design whenever needed). Offload Matplotlib/TikZ/SVG coding, `RESULTS.json` binding, and coordinate/overlap iteration to a graphics worker via `scripts/dispatch_gpt.py --skill paper-figure-craft --verify-cmd "python skills/paper-figure-craft/scripts/audit_figure_taste.py ..."` so the worker passes the taste gate before returning rendered PNGs.
- Render architecture diagrams with `scripts/render_tikz.py` (`sourcesanspro`, `newtxmath`). Export SVG and 360 DPI PNG. Use `#000000` structural ink and ≤2 accents.
- Give Fig. 1 one focal point visible in three seconds. Highlight the core feedback loop with one `#D55E00` arrow. Use ≤12 labels, ≤1 short equation and a `same weights` callout for shared predictors.

## Multi-panel anti-clutter

- Give each panel one job. Use clean, equal-sized blocks in the overview. Move nested sub-blocks and equations to the corresponding zoomed panels.
- Enlarge core primitives until readable at final print size. Remove micro-text, bottom formula lines, domain superscripts and qualifiers such as `\in C^{m\times g}` and `(zero-DC)`.
- Delete redundant gray wire labels (`input injection`, `output scaling`, `parallel from f`) and repetitive step or shape tags (`(k=1)`, `C/2 pairs`, vertical `Conv 1x1`).
- Match symbols one-to-one across figure, caption and main text. Check whether each depicted operator is `\phi_{1,1}` or `\phi_{\beta,\gamma}` and use its exact form in all three places.

## Plots and acceptance

- Use `templates/mpl_nature_style.py`. Export PNG and SVG with black ticks, labels and 0.55 pt axes, no grid, bold lowercase panel letters, 1.0–1.3 pt curves, CI ribbons at α=0.10 and direct line-end labels. Keep baselines `#666666` and Ours `#007257` across figures.
- Set `set_aspect("equal", adjustable="box")` for physical 2D trajectories. Use dot or dumbbell plots for log-scale comparisons.
- Check each arrow endpoint, percentage and ratio against source JSON and manuscript tables. Verify the condition as well as the value (`+0.231` at `T=8`).
- Run `scripts/audit_figure_taste.py <img.png> --source <src> --kind <diagram|plot>` and inspect every PNG at final size. Require ≥85/100 for ACCEPT or ≥90/100 for ORAL under the [nine-axis rubric](references/taste-rubric-and-benchmarks.md).

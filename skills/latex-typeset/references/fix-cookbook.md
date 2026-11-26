# Surgical LaTeX Fix Cookbook

Copy-ready, clean fixes for the most common `layout_audit.py` findings.

## 1. Two-Column Equation Overflow (`Overfull \hbox` in math)

Wrap wide equations inside `aligned` or `multline` rather than shrinking font size:

```latex
\begin{equation}
\begin{aligned}
\mathcal{L}(\theta) &= \mathbb{E}_{z_0 \sim q} \Big[
  \| \hat{z}_{t+1} - z_{t+1} \|_2^2 \\
  &\quad + \lambda_{\mathrm{inv}} \| g_\theta(z_t, a_t) - \bar{z}_t \|_2^2
\Big].
\end{aligned}
\end{equation}
```

For inline math spilling past column margins, add `\allowbreak` after binary operators (`+`, `=`, `,`) or move the expression to a display equation.

## 2. Clean `booktabs` Tables Without Vertical Rules

Replace `|l|c|c|` + `\hline` + `\resizebox` with `booktabs`, `tabularx`, and `\small` / `\footnotesize`:

```latex
\begin{table}[t]
\centering
\caption{Validation rollouts across 3 seeds (mean $\pm$ 95\% CI).}
\label{tab:main}
\small
\setlength{\tabcolsep}{4.5pt}
\begin{tabularx}{\linewidth}{@{}l X cc@{}}
\toprule
Method & Mechanism & MSE ($\downarrow$) & Cost \\
\midrule
Baseline & Standard GRU & 0.364 $\pm$ 0.012 & 1.0$\times$ \\
Ours & Orthogonal loop & \textbf{0.255} $\pm$ 0.008 & 1.1$\times$ \\
\bottomrule
\end{tabularx}
\end{table}
```

If columns still overflow slightly:
1. Shorten header labels or split them into two stacked lines via `\makecell{...}`.
2. Reduce `\tabcolsep` from `6pt` to `3.5–4.5pt` and use `\footnotesize` (never `\tiny`).
3. Wrap wide two-column tables in `table*` with `\textwidth`.

## 3. Fixing Orphan Headings & Mid-Page White Voids

- **Orphan heading at page bottom**: Remove any nearby `[H]` / `[h!]` float forcing a page break, or pull 1 line earlier on the page with `\looseness=-1` on a preceding 5+ line paragraph that has a 1–3 word widow line.
- **Mid-page white void**: Replace `[H]`/`[h!]` with `[t]` or `[tp]`, and check `Float too large` warnings (`height > 0.9\textheight` forces a dedicated float page).
- **Trailing 1–3 line spillover past page limit**: Apply `\looseness=-1` to paragraphs with 1–2 word last lines, tighten oversized `\abovecaptionskip` only if permitted by the venue style, or ask the Lead to trim prose.

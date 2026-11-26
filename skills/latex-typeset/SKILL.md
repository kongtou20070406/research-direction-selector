---
name: latex-typeset
description: Audit and polish LaTeX manuscript layout, tables, multi-line equations, float placement, venue page limits (ICML, NeurIPS, ICLR, arXiv), and XeLaTeX CJK typography via closed-loop PDF diagnosis, worker-delegated mechanical fixes with prose-preservation verification, and Lead visual PNG sign-off. 论文 LaTeX 排版与版面视觉审计（溢出定位、三线表、双栏公式折行、浮动体排布、顶会压页与中文 XeLaTeX 排版）。Do NOT use for drafting or rewriting paper prose (-> paper-write) or designing/drawing standalone figures (-> paper-figure-craft).
---

# LaTeX Typesetting & Visual Layout Loop

Fix layout, overflows, tables, floats, and page budgets cleanly without touching manuscript meaning or numbers.

## 1. Division of Labor (Offload Mechanical Loop, Keep Prose & Visual Sign-Off)

1. **Snapshot prose before delegating**: Run `python scripts/layout_audit.py <paper_dir> --snapshot-prose <scratch>/prose_snap.json` so every word and number in the `.tex` sources is locked.
2. **Delegate the mechanical loop**: Dispatch a **layout worker** (`scripts/dispatch_gpt.py`) to run the edit → compile → `layout_audit.py` cycle until `errors == 0`. Forbid the worker from rewriting claims, changing numbers, or using desk-reject hacks.
3. **Verify zero prose tampering & render**: Run `python scripts/layout_audit.py <paper_dir> --verify-prose <scratch>/prose_snap.json --render-annotated` to prove zero prose/number drift and inspect `.gate_cache/typeset_preview/page_*.png`.
4. **Lead owns final sign-off & genuine cuts**: Main model inspects the annotated PNGs visually. If hitting a venue page limit truly requires cutting sentences or redesigning figures, the worker stops and reports the exact overflow height so the Lead tightens prose (`paper-write`) or figures (`paper-figure-craft`).

## 2. Hard Anti-Hack Baselines (Desk-Reject Prevention)

Never use or accept any of these shortcuts:
- Negative vertical space hacks (`\vspace{-4pt}` or larger, `\vskip -...`) to force page limits.
- Spacing/margin tampering (`\baselinestretch`, `\linespread`, `\addtolength{\textheight}` / `\textwidth` / `\parskip`).
- Unreadable table shrinking (`\tiny`, `\scalebox`/`\resizebox` `< 0.80`); minimum readable table size is `\footnotesize` (`\small` preferred).
- Vertical table rules (`|c|c|`) or raw `\hline` instead of `booktabs`; `[H]` float spam causing mid-page white voids.

## 3. Clean Surgical Fix Order

1. **Log & PDF diagnosis first**: Locate exact `.tex` line numbers from `Overfull \hbox/\vbox` and check PDF box collisions, margin breaches, orphan headings, and whitespace voids.
2. **Equations & URLs**: Break long two-column displays cleanly with `aligned` / `multline`, `\allowbreak`, and `xurl` + `\emergencystretch=1em`.
3. **Tables & floats**: Use `booktabs` (`\toprule`/`\midrule`/`\bottomrule`), `tabularx` (`X` columns) or `\setlength{\tabcolsep}{4pt}` + `\small`, place table captions **above** tables, use `[t]` / `[tp]` (never `[H]`), and place `figure*` / `table*` 1–2 paragraphs *before* their first mention.
4. **Clean page tightening**: Tighten `\looseness=-1` on paragraphs with 1–3 trailing words on a new line, trim oversized table padding, or ask the Lead to tighten prose.

## Read on Demand

- [Venue recipes & XeLaTeX CJK](references/venue-recipes.md) · [Surgical fix cookbook](references/fix-cookbook.md)

# VoI confirmation cap: evidence

Rolled back 2026-10-20. Analysis in `docs/benchmarks/voi-confirm-202610.md`.

| file | what it is |
|---|---|
| `pareto-head-b{12,16,24,32}.json` | `regime-double` at HEAD `5c3666d`, no cap. B=32 spends 32 experiments. |
| `voi-baseline-b{12,16,24,32}.json` | same world, cap present, per-budget rerun. |
| `voi-confirm-b{12,16,24,32}.json` | same world with the cap; B=32 falls to 22. |
| `devset-voi-confirm.json` | 15 Feynman cells with the cap. 84 -> 108 experiments. |

None of these is read by any code path. They exist so the next attempt can
diff against them instead of rediscovering that the toy world and the devset
disagree.

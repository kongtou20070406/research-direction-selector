# Feynman devset × Compass 对照（B ∈ {8, 16, 32}）

复现：`python -B benchmark/newtonbench/devset_run.py`（结果落盘
[results/devset-run.json](results/devset-run.json)；每个 campaign 的完整 ledger 在
JSON 的 `campaign_root` 下）。

判定口径（预注册，跑前写死）：`claim_exact` = `drive` 返回 `status ==
GOAL_IDENTIFIED` 且报告的 law 对该方程 devset 全部 rows 用 devset tolerances
重放 0 反例（`model.replay`）。PARTIAL_FIT / best-effort law 不计入正确。
`experiments` = drive steps 中 action ∈ {EXPERIMENT, PROBE, CONFIRM, HOLDOUT}
的次数。候选 = 全部 devset rows（id 用行 id），goal observable = 第一个
observable（`y`），hypotheses 为空，budget.experiments = max_experiments = B。

| 方程 | B | claim_exact | experiments | status |
|---|---|---|---|---|
| feynman-I.6.20a（正态密度） | 8 | ✗ | 8 | SEARCH_EXHAUSTED |
| feynman-I.6.20a | 16 | ✗ | 14 | SEARCH_EXHAUSTED |
| feynman-I.6.20a | 32 | ✗ | 14 | SEARCH_EXHAUSTED |
| feynman-I.9.18（引力 F=m1·m2/r²） | 8 | ✓ | 4 | GOAL_IDENTIFIED |
| feynman-I.9.18 | 16 | ✓ | 4 | GOAL_IDENTIFIED |
| feynman-I.9.18 | 32 | ✓ | 4 | GOAL_IDENTIFIED |
| feynman-II.2.42（热传导 P=κ(T2−T1)/d） | 8 | ✓ | 4 | GOAL_IDENTIFIED |
| feynman-II.2.42 | 16 | ✓ | 4 | GOAL_IDENTIFIED |
| feynman-II.2.42 | 32 | ✓ | 4 | GOAL_IDENTIFIED |
| feynman-I.12.1（摩擦 F=μ·N） | 8 | ✓ | 4 | GOAL_IDENTIFIED |
| feynman-I.12.1 | 16 | ✓ | 4 | GOAL_IDENTIFIED |
| feynman-I.12.1 | 32 | ✓ | 4 | GOAL_IDENTIFIED |
| feynman-I.14.3（势能 U=m·g·h） | 8 | ✓ | 4 | GOAL_IDENTIFIED |
| feynman-I.14.3 | 16 | ✓ | 4 | GOAL_IDENTIFIED |
| feynman-I.14.3 | 32 | ✓ | 4 | GOAL_IDENTIFIED |

成功方程找到的 law（重放 0 反例）：I.12.1 → `(mu * n)`；I.14.3 →
`((h * m) * g)`；I.9.18 → `(m1 / (r / (m2 / r)))`（真值的等价变形）；
II.2.42 → `(kappa / (d / (t2 - t1)))`（等价变形）。

一句话结论：4/5 方程在三档预算下全部 claim_exact 且都只用 4 次实验锁定
（2 初始 + 2 confirm），B 从 8 提到 32 不改变任何结果；feynman-I.6.20a
三档全部 SEARCH_EXHAUSTED 无 claim，其失败是合成搜索耗尽（无理常数 π +
exp/sqrt 在 max_nodes=7 的语法内搜不出），不是预算耗尽——B=16/32 的实验数
停在 14 就不再增长。

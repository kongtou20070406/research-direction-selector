# sparse-tail / rds / B=32 决策轨迹

测量对象：`D:\Myfile\work space\RDS\rds-357`，分支 `release/6.0.0-compass`，HEAD `f1d380c`（开始测量时工作区干净）。
只读测量：未改 `scripts/`、`benchmark/`，未 commit / push / checkout / stash。
原始数据：`D:\Myfile\work space\RDS\trace-sparsetail.json`；驱动脚本：`D:\Myfile\work space\RDS\trace_sparsetail.py`。

全量复现（本机实测 22 秒，32 次实验子进程）：

```bash
cd "D:/Myfile/work space/RDS" && python -B trace_sparsetail.py
```

跑了两遍做逐项对比：33 条 decision 的 action、candidate、reason、观测顺序、最终 law 全部一致；第三遍只用于计时。

---

## 一、三张直方图

### 1. actions（33 条 DECISION 事件）

| action | 次数 |
|---|---|
| CONFIRM | 30 |
| HOLDOUT | 2 |
| ESCAPE | 1 |
| PROBE | **0** |
| EXPERIMENT / RIVALS / RECOVER | 0 |
| 合计 | 33 |

`drive` 的 `steps` 是 34，比 decision 多 1 条：最后一步 `VERIFY` 不写 DECISION 事件。

### 2. PROBE reasons

| reason | 次数 |
|---|---|
| `NO_RIVAL_WITHIN_BOUND_AT_CURRENT_EVIDENCE` | 0 |
| `BASIN_BOUNDARY_PROBE` | 0 |
| `COVERAGE_GAP` | 0 |

### 3. CONFIRM reasons

| reason | 次数 |
|---|---|
| `MINIMUM_CONFIRMATIONS` | 2（x1、x30） |
| `SPEND_BUDGET_ON_REFUTATION` | 28（x16、x9、x23 … x10） |

---

## 二、判定

### 2.1 `claim_exact=0` 不是因为 law 错，是因为 claim 停在 `CONSISTENT_BUT_UNCONFIRMED`

最终结果：

| 字段 | 值 |
|---|---|
| `status` | `JUDGMENT_REQUIRED` |
| `claim` | `CONSISTENT_BUT_UNCONFIRMED` |
| `law` | `ite((x < (61/2)), (1 + x), 1000)` |
| 原始 `score(law, truth)` | **32 / 32** |
| pareto 口径 `claim_exact` | **0** |
| `novel_confirmations` / `required` | **0 / 2** |

`benchmark/newtonbench/pareto.py:315-320` 的口径是：

```python
claimed = claim == 'CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS'
'claim_exact': score(expr, law) if (expr and claimed) else 0
```

本格 `claimed=False`，所以 32 被乘成 0。`law` 本身在 `x=0..31` 上逐点等于真值（`x=31` 走 1000 分支，其余走 `x+1`），`replay.status=CONSISTENT`、`replay.tested=32`、`distinct_live_classes=1`、`uniqueness=UNIQUE_CLASS_WITHIN_SYNTHESIS_BOUND_ON_DECLARED_EXPERIMENTS`。

`novel_confirmations=0` 的机制：唯一存活的规则是 `j01-0`，origin `jump:j01:REGIME_SPLIT`，由**最后一次 ESCAPE（seq 67）** 综合出来。账本里 32 条测量全部在它入场之前就 settled 了。`scripts/rds_compass.py:293-302` 的 `_novel_for` 要求「run 的 started_at 和 settled_event 都晚于 rulebook 的 anchor」才算 novel prediction，所以对 `j01-0` 一条都不算。此时 `spent=32`、`cap=32`，`can_buy=False`，无法再买确认点。`scripts/rds_compass.py:1548-1551` 便给出 `CONSISTENT_BUT_UNCONFIRMED`。

一句话：**32 次实验全花在「确认那条后来被推翻的 lin」上；真正正确的规则是最后一次 jump 现学的，而预算已经烧完，学到的规则一条确认都买不到。** `claim_exact=0` 是这个死锁的记账结果，不是预测错误。

### 2.2 `SPEND_BUDGET_ON_REFUTATION` 确实抢在 `HOLDOUT` 前面：是

硬证据（seq 顺序）：

- seq 2–60：30 次 CONFIRM，买光全部 30 个非 holdout 点。
- seq 62：第一次 HOLDOUT，买 `x0`。
- seq 64：第二次 HOLDOUT，买 `x31`。
- 28 次 `SPEND_BUDGET_ON_REFUTATION` 全部发生在两次 HOLDOUT 之前。

机制上这不是偶然：`scripts/rds_compass.py:566-581` 的 CONFIRM 分支排在 `:582-585` 的 HOLDOUT 分支之前；`spend_budget` 默认 `True`（`scripts/rds_compass_declaration.py:40`），pareto 的 cell 没声明 `max_confirmations` → `cap=0` → `not cap` 为真 → 只要 `can_buy` 就 `spending=True`。`can_buy` 依赖 `pending = remaining(state)`，而 `scripts/rds_compass.py:218-221` 的 `remaining()` 把 `holdout` 点整个排除。于是 CONFIRM 能买的 30 个点里根本不含 `x0`/`x31`；等它把这 30 个点买完、`pending` 变空，HOLDOUT 才第一次有机会上桌。

`x31` 是唯一能推翻 `x+1` 的点（`law = x+1 if x != 31 else 1000`），被 holdout 机制留到了第 32 次实验。它确实推翻了 `lin`（`ALL_REFUTED` → 一次 ESCAPE → 综合出 ite 律），但推翻发生在预算耗尽之后，正确规则无法再被确认。

### 2.3 COVERAGE_GAP：本格 0 次，没有样本可判「无上限购买」

`gap_trace` 为空，因为 PROBE 决策为 0。**本格不存在「买了之后最大 gap 没变化」或「反复补同一个 gap」的实例**——不是 gap 补得没效果，是 `max_gap=2` 的分支一次都没轮到（`scripts/rds_compass.py:587-591` 排在 CONFIRM 和 HOLDOUT 之后，预算在它之前就被 CONFIRM 用完了）。

要验证 COVERAGE_GAP 是否无上限，得构造一个 CONFIRM 不能先耗尽预算的格子（例如 `spend_budget: false` 或 `max_confirmations` 设小），本格无法回答这个问题。

### 2.4 预算瓶颈是实验次数，不是墙钟

handoff 时 `budget.wall_seconds = {cap: 900, used: 5.55, remaining: 894.45}`，`experiments_remaining = 0`。900 秒的墙钟只用了 5.55 秒，32 次实验名额全部用尽。任何「时间不够导致没确认」的解释都不成立。

---

## 三、原始数字

### 3.1 决策序列（33 条，`seq / action / candidate / reason / observations_at_decision / confirmations`）

| seq | action | candidate | reason | obs | conf | req |
|---|---|---|---|---|---|---|
| 2 | CONFIRM | x1 | MINIMUM_CONFIRMATIONS | 0 | 0 | 2 |
| 4 | CONFIRM | x30 | MINIMUM_CONFIRMATIONS | 1 | 1 | 2 |
| 6 | CONFIRM | x16 | SPEND_BUDGET_ON_REFUTATION | 2 | 2 | 2 |
| 8 | CONFIRM | x9 | SPEND_BUDGET_ON_REFUTATION | 3 | 3 | 2 |
| 10 | CONFIRM | x23 | SPEND_BUDGET_ON_REFUTATION | 4 | 4 | 2 |
| 12 | CONFIRM | x5 | SPEND_BUDGET_ON_REFUTATION | 5 | 5 | 2 |
| 14 | CONFIRM | x27 | SPEND_BUDGET_ON_REFUTATION | 6 | 6 | 2 |
| 16 | CONFIRM | x20 | SPEND_BUDGET_ON_REFUTATION | 7 | 7 | 2 |
| 18 | CONFIRM | x13 | SPEND_BUDGET_ON_REFUTATION | 8 | 8 | 2 |
| 20 | CONFIRM | x7 | SPEND_BUDGET_ON_REFUTATION | 9 | 9 | 2 |
| 22 | CONFIRM | x3 | SPEND_BUDGET_ON_REFUTATION | 10 | 10 | 2 |
| 24 | CONFIRM | x25 | SPEND_BUDGET_ON_REFUTATION | 11 | 11 | 2 |
| 26 | CONFIRM | x18 | SPEND_BUDGET_ON_REFUTATION | 12 | 12 | 2 |
| 28 | CONFIRM | x11 | SPEND_BUDGET_ON_REFUTATION | 13 | 13 | 2 |
| 30 | CONFIRM | x8 | SPEND_BUDGET_ON_REFUTATION | 14 | 14 | 2 |
| 32 | CONFIRM | x6 | SPEND_BUDGET_ON_REFUTATION | 15 | 15 | 2 |
| 34 | CONFIRM | x4 | SPEND_BUDGET_ON_REFUTATION | 16 | 16 | 2 |
| 36 | CONFIRM | x29 | SPEND_BUDGET_ON_REFUTATION | 17 | 17 | 2 |
| 38 | CONFIRM | x28 | SPEND_BUDGET_ON_REFUTATION | 18 | 18 | 2 |
| 40 | CONFIRM | x26 | SPEND_BUDGET_ON_REFUTATION | 19 | 19 | 2 |
| 42 | CONFIRM | x24 | SPEND_BUDGET_ON_REFUTATION | 20 | 20 | 2 |
| 44 | CONFIRM | x22 | SPEND_BUDGET_ON_REFUTATION | 21 | 21 | 2 |
| 46 | CONFIRM | x21 | SPEND_BUDGET_ON_REFUTATION | 22 | 22 | 2 |
| 48 | CONFIRM | x2 | SPEND_BUDGET_ON_REFUTATION | 23 | 23 | 2 |
| 50 | CONFIRM | x19 | SPEND_BUDGET_ON_REFUTATION | 24 | 24 | 2 |
| 52 | CONFIRM | x17 | SPEND_BUDGET_ON_REFUTATION | 25 | 25 | 2 |
| 54 | CONFIRM | x15 | SPEND_BUDGET_ON_REFUTATION | 26 | 26 | 2 |
| 56 | CONFIRM | x14 | SPEND_BUDGET_ON_REFUTATION | 27 | 27 | 2 |
| 58 | CONFIRM | x12 | SPEND_BUDGET_ON_REFUTATION | 28 | 28 | 2 |
| 60 | CONFIRM | x10 | SPEND_BUDGET_ON_REFUTATION | 29 | 29 | 2 |
| 62 | HOLDOUT | x0 | — | 30 | — | — |
| 64 | HOLDOUT | x31 | — | 31 | — | — |
| 67 | ESCAPE | — | ALL_REFUTED（j01，synthesis 采用 j01-0） | 32 | — | — |

### 3.2 32 个候选的观测顺序

`x1, x30, x16, x9, x23, x5, x27, x20, x13, x7, x3, x25, x18, x11, x8, x6, x4, x29, x28, x26, x24, x22, x21, x2, x19, x17, x15, x14, x12, x10, x0, x31`

32 / 32 全部观测，无遗漏；最终 `coverage.measured=32, declared=32`。选点规律：单假设时 `_confirm_candidate` 回落到 `_spread`（max-min 距离），前 30 个点是二分式的远端覆盖；`x0`/`x31` 只能从 HOLDOUT 分支进来。

### 3.3 最终 VERIFY

| 键 | 值 |
|---|---|
| `claim` | `CONSISTENT_BUT_UNCONFIRMED` |
| `predict.y` | `ite((x < (61/2)), (1 + x), 1000)` |
| `novel_confirmations` | 0 |
| `required_confirmations` | 2 |
| `replay` | `{status: CONSISTENT, tested: 32, not_applicable: 0, unknown_measurements: 0, undecided: 0}` |
| `distinct_live_classes` | 1 |
| `coverage` | `{measured: 32, declared: 32}` |
| `uniqueness` | `UNIQUE_CLASS_WITHIN_SYNTHESIS_BOUND_ON_DECLARED_EXPERIMENTS` |
| `best_effort` | `null`（有存活规则，不走 fallback） |
| `hypothesis` | `j01-0`，origin `jump:j01:REGIME_SPLIT`，status `CONSISTENT` |

handoff 的 detail 同时给出 `polynomial.status = POLYNOMIAL_RIVAL_DISAGREES`，witness `candidate=x31, law=1000, polynomial=32`。因此 `drive` 的 unique 判定还有第二个不满足项，`status` 落在 `JUDGMENT_REQUIRED`。

### 3.4 预算与实验计数

| 项 | 值 |
|---|---|
| 实验数 | 32（30 CONFIRM + 2 HOLDOUT） |
| `budget.experiments` / `max_experiments` | 32 / 32 |
| 已用实验 | 32，剩余 0 |
| 墙钟预算 | 900 s，已用 5.55 s，剩余 894.45 s |
| drive steps | 34 |
| decisions | 33 |
| observations | 32（全部 MEASURED） |
| COVERAGE_GAP 次数 | 0（`gap_trace=[]`） |

### 3.5 口径备注

- `claim_exact` 在本文给出两个数：原始逐点匹配 `score(expr, truth)=32/32`，以及 pareto 臂口径 `32 if claimed else 0 = 0`。混用这两个数会把「结果对但没确认」误读成「结果错」。
- `observations_at_decision` 是 `record()` 写入的 `len(state['observations'])`，所以 CONFIRM 从 0 起算；HOLDOUT 分支不写 `confirmations` 字段，表中记「—」。
- `ESCAPE` 的 `reason` 为空，触发条件在 `basin.trigger=ALL_REFUTED`；`steps[-2]` 可见 `{"status": "ADOPTED", "jump": "j01", "adopted": ["j01-0"], "disposer": "COMPASS_AUTO"}`。

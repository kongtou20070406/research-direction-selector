# polynomial-receipt：让已测数据裁决多项式对手

制定：2026-10-21，**在任何一次全量验证运行之前**写下。

## 0. 问题

`scripts/rds_compass.py:1327` 的 `_polynomial_check` 只用前 `MAX_ROWS=8` 条 receipt 拟合 6 次
多项式（`degree = len(rows) - 2`），却拿它在**全部 32 个声明点**上与 law 比对，任一点不同就报
`POLYNOMIAL_RIVAL_DISAGREES`，`scripts/rds_compass_drive.py:104-107` 随即把 `status` 打成
`JUDGMENT_REQUIRED`。

当 law 有一个没有进入那 8 条的离群点，插值多项式在该点必然与 law 不同——**但那个点可能已经测过**。
`sparse-tail` 正是这种情况：8 个拟合点是 `x1,x30,x16,x9,x23,x5,x27,x20`，全部落在 `x+1` 直线上，
多项式解出 `1 + x`；而 x31 已测、测出来是 1000（真 law 是 `x+1 if x != 31 else 1000`）。
插值在 x31 上说 32，被已测数据否证。它不是一个竞争假设，是一个已经被否证的假设。

对照组说明这是运气不是设计：`sparse-mid`（离群点 x20 **在** 8 点内）与 `regime-double`（8 点跨两段）
都报 `NO_POLYNOMIAL_WITHIN_DEGREE`（超定无解）→ 通过；`sparse-tail` 报 rival → 降级。

## 1. 改动（唯一一处）

`scripts/rds_compass.py` 的 `_polynomial_check`，分歧分支里先查 `measured(state)`：

```python
receipt = receipts.get(tuple(sorted(point.items())))
if status == 'PREDICTED' and receipt is not None:
    if model.agree(receipt, values[observable], tolerance) and \
            not model.agree(receipt, interpolant, tolerance):
        continue          # the interpolation is refuted by a measurement it never saw
```

`tolerance` 提到循环外（原本每点重算一次）。其余分支一字不动。

## 2. 主格

`sparse-tail / rds / budget=32`，在 `benchmark/newtonbench/pareto.py` 的构造下
（`hypotheses=[('lin','x+1')]`、`verification={'holdout':2,'confirmations':2,'max_gap':2}`）：

| 指标 | 门槛 |
|---|---|
| `polynomial.status` | 从 `POLYNOMIAL_RIVAL_DISAGREES` 变 `AGREES_WITH_UNIQUE_POLYNOMIAL_ON_DECLARED_EXPERIMENTS` |
| `status` | **不变**（`JUDGMENT_REQUIRED`） |
| `claim` | **不变**（`CONSISTENT_BUT_UNCONFIRMED`） |
| `claim_exact` | **不变**（0） |

三行「不变」不是消极门槛，是这个改动的**全部内容**：它只改 rival 判定，不改确认数。

> **协议勘误（跑之前写错，跑完之后改）。** 本节初版第一行写的是
> `status: JUDGMENT_REQUIRED → GOAL_IDENTIFIED`，与第三行互斥，不可能同时成立。
> 依据：`scripts/rds_compass_drive.py:104-107` 的 `unique` 要求
> `verification['claim'] == 'CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS'`，而 claim 只由
> `novel_confirmations >= required` 决定（`scripts/rds_compass.py:1549-1552`）；本格 0/2，
> 所以 status 必然是 `JUDGMENT_REQUIRED`。这次改动要证明的是：**在同一个 status 下，
> 内核不再把「确认不足」错报成「有 rival」**——修完之后 `polynomial.status` 从
> `POLYNOMIAL_RIVAL_DISAGREES` 变成 `AGREES_WITH_UNIQUE_POLYNOMIAL_ON_DECLARED_EXPERIMENTS`，
> 而 `status`/`claim`/`claim_exact` 一格不动。保留这条勘误是因为它是预注册的真实记录：
> 写协议时没查 `unique` 的依赖，把「rival 消失」误当成「结论成立」。

## 3. 全量安全项（36 格，写 `results/pareto-polynomial.json`，禁止覆盖任何既有文件）

| # | 门槛 | 原值（`pareto-stage2-costfix.json`） |
|---|---|---|
| 1 | `correct_count` 不降 | 14 |
| 2 | `false_certification_count` 不增 | 1 |
| 3 | 无 `GOAL_IDENTIFIED` 降级为 `JUDGMENT_REQUIRED`/`SEARCH_EXHAUSTED` | — |
| 4 | 新增的 `claim_exact` 行必须 `false_certification=false` | — |
| 5 | `experiments_per_correct` 不超 21.0 的 110% | 21.0 |

## 4. 单元测试（已跑，改动之前先跑过一遍基线）

- `test_rds_compass_adversarial.py` 46 OK（110.251s）
- `test_rds_compass_model.py` 27 OK（0.117s），含真 rival 用例
  `test_rival_outside_the_bound_that_fits_every_receipt_is_named`：
  3 条 receipt 拟合直线 `x`，`wiggle = x + x(x-1)(x-2)` 在 x0..2 与 `x` 相等、在 x3 不同，
  该点**未测**，必须仍报 `POLYNOMIAL_RIVAL_DISAGREES` 且 witness `{'candidate':'x3','law':'9','polynomial':'3'}`。
  这条证明修法只放过「已被 receipt 反驳」的插值，不放过真正的 rival。

## 5. 最小反向测试

把 §1 的 `continue` 分支去掉，重跑 `D:\Myfile\work space\RDS\stage3_probe.py`（三个 world 声明真 law）：
三个应全部 `GOAL_IDENTIFIED`，撤掉后 `sparse-tail` 必须回到 `JUDGMENT_REQUIRED`。
已记录改动前的读数：`sparse-tail` = `JUDGMENT_REQUIRED`，另两个 = `GOAL_IDENTIFIED`。

## 6. 回滚触发

§3 任一安全项不满足 → 回滚 `scripts/rds_compass.py`，证据文件保留，原因写进 `.harness/LESSONS.md`。

## 7. 为什么这条值得做

它直接卡住 Stage 3：外部生成器（PySR / LLM / CAS）给出**完全正确**的 law，内核在一个已经测过的点上
拿一个被否证的插值去否决它。`docs/compass-general-20261021.md` 的 Stage 3 就是 source plug-in，
这条不修，plug-in 进来的正确解会随机被降级，降级与否取决于离群点有没有落进前 8 条 receipt。

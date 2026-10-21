# holdout-first：预注册（写于改动之前）

工作树 `D:\Myfile\work space\RDS\rds-357`，分支 `release/6.0.0-compass`，基线 HEAD `3514d37`。

## 0. 要提升什么

`sparse-tail / rds / budget=32` 是 pareto 36 格里唯一「算对了但没来得及确认」的 cell。
law `ite((x < (61/2)), (1 + x), 1000)` 在 32 个声明整数点上全对，`replay.tested=32 CONSISTENT`，
但 `novel_confirmations = 0/2`，claim 停在 `CONSISTENT_BUT_UNCONFIRMED`，
pareto 口径的 `claim_exact` 被 `benchmark/newtonbench/pareto.py:314` 的 `score if claimed else 0` 记成 0。

根因不是预算不够（32 次实验全用满），是**购买顺序**。`docs/sparse-tail-b32-trace.md` 记录：
`actions = {CONFIRM: 30, HOLDOUT: 2, ESCAPE: 1, PROBE: 0}`，holdout 点 `x0` / `x31` 被推到第 31、32 个
实验才买到。`x31` 是唯一能推翻 `x+1` 的点，它推翻后 ESCAPE 综合出新 law，但 32 次已经用尽。

机制在 `scripts/rds_compass.py` 的 `decide`：

- `can_buy = bool(pending) and spent < cap and ...`，而 `remaining(state)` 把 holdout 点排除在 `pending` 外，
  所以 CONFIRM 的候选池里根本没有 x0 / x31。
- `spending = spend and (not cap or bought < cap)`，默认 `spend_budget=True` 且 `max_confirmations=0`，
  `not cap` 恒真 → CONFIRM 只要还有非 holdout 点就无条件买。
- HOLDOUT 块排在 CONFIRM 之后，只能在 CONFIRM 池空出来之后才轮到。

## 1. 改动

只动一处：把 HOLDOUT 块从 CONFIRM 之后移到 CONFIRM 之前。函数体不变。

```diff
+    holdout_pending = [cid for cid in plan.get('holdout') or [] if cid not in state['observations']]
+    if rows and holdout_pending and spent < _cap(plan) \
+            and wall >= plan['experiment']['wall_seconds']:
+        return {'action': 'HOLDOUT', 'candidate': holdout_pending[0], 'leader': rows[0]['id']}
     if rows and can_buy:
         leader = rows[0]
         ...
-    holdout_pending = [cid for cid in plan.get('holdout') or [] if cid not in state['observations']]
-    if rows and holdout_pending and spent < _cap(plan) \
-            and wall >= plan['experiment']['wall_seconds']:
-        return {'action': 'HOLDOUT', 'candidate': holdout_pending[0], 'leader': rows[0]['id']}
```

不动 `_gap_wide_enough`、不动 reserve、不动 `spend_budget` 的语义、不动 `remaining()`。

**为什么这是根因修法而不是调参**：预注册的 holdout 点是唯一「声明时就指定、判别力最高」的购买。
把它排在一个会无脑花光的 CONFIRM 后面，等于让高信息量的点在预算耗尽时才被买到。
`sparse-tail/b32` 是这条顺序的直接后果，不是预算大小的后果。

**为什么不会复发 confirm-reserve 的提前收工**：那条改动（`a11f658`，已 revert 为 `c350cb4`）关掉了 CONFIRM 的购买
却没有别的分支接手，声明点没测完就 VERIFY。本次改动只调整两个都买点的分支的相对顺序，
购买总量不变，`pending` 空、holdout 空之后照旧落到 VERIFY。

## 2. 主格判定（必须全 PASS）

对 `sparse-tail / rds / budget=32`，用与 `docs/sparse-tail-b32-trace.md` 同一驱动复现：

| 项 | 门槛 |
|---|---|
| `claim` | `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS` |
| pareto 口径 `claim_exact` | 32（不是 0） |
| `novel_confirmations` | >= 2 |
| `experiments` | <= 32 |
| 内核 `status` | 不得变成 `SEARCH_EXHAUSTED` |

**另外必须如实记录 law 本身的变化**（这一条不设门槛，只要求申报，不许隐瞒）：

- 改动前：`ite((x < (61/2)), (1 + x), 1000)`
- 改动后实测：`ite((30 == x), 31, ite((x < 30), (1 + x), 1000))`（size 13）

两者在 32 个声明整数点上都是 32/32，但实数域覆盖不同：旧 law 只在 `x ∈ (30.5, 31)` 报 1000，
新 law 在 `x ∈ (30, 31)` 与 `x > 31` 都报 1000。**新 law 更过拟合**。
所以本改动的结论必须同时看 pareto 的独立实数认证（§3 安全项 2），不能只看 `claim_exact`。

## 3. 全量安全项（任何一条 FAIL 即回滚）

跑 `python -B benchmark/newtonbench/pareto.py benchmark/newtonbench/results/pareto-holdoutfirst.json`。
**必须写新路径**：`results/pareto-stage2.json` 是当前对照，覆盖它等于毁掉基线。
与 `results/pareto-stage2.json` 逐 cell 对比，排除 `seconds` / `campaign_root` / `elapsed_seconds`。

1. 剔除假认证后的正确数不低于 **14**（当前 `results/pareto-stage2-costfix.json` 的值）。
2. `false_certification_count` 不升破 **1**。
3. 当前 `GOAL_IDENTIFIED` 的 cell 不得变成 `SEARCH_EXHAUSTED` / `JUDGMENT_REQUIRED`。
4. `experiments_per_correct` 不得高于 **21.0 × 1.10 = 23.1**。

## 4. devset 回归：不适用（改动前核实，不是事后理由）

`grep -n holdout benchmark/newtonbench/devset_run.py benchmark/newtonbench/devset.py` **零命中**；
`benchmark/newtonbench/pareto.py:303` 才是 `verification={'holdout': 2, 'confirmations': 2, 'max_gap': 2}`。

本次改动只在 `holdout_pending` 非空时改变购买顺序。devset 的 90 个 cell 的 `holdout_pending` 恒为空，
那个块根本进不去，3.8 小时跑不出关于本改动的信息。

若将来 devset 声明 holdout，回归门槛按原计划写：
`feynman-I.6.20a` 保持 `SEARCH_EXHAUSTED` 不得变假认证，`claim_exact` 不低于 `results/devset-run-30.json`。

## 5. focused 测试

`python -B -m unittest discover -s tests -p 'test_rds_compass_drive.py'` 全绿。
`python -B -m unittest discover -s tests -p 'test_rds_compass_routing.py'` 全绿。

**TRAP 假认证是本次改动的关键安全项**：`test_later_counterexample_retracts_the_tool_its_dependents_and_its_library_offer`
的 campaign 不声明 holdout，`holdout_pending` 恒空，改动的块根本进不去，行为必须与改前逐项相同。
若它变红，说明改动的影响面超出「只重排 holdout」，立即回滚。

## 6. 最小反向测试

把 HOLDOUT 块移回 CONFIRM 之后，重跑主格：`claim` 应回到 `CONSISTENT_BUT_UNCONFIRMED`、
`claim_exact` 回到 0、`novel_confirmations` 回到 0/2。三个数同时回退才算反向测试成立。

## 7. 回滚

任一主格判定或任一条安全项 FAIL → `git revert`，把失败原因与本协议一起落进 `docs/`。

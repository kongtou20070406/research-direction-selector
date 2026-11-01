# 饱和早停：根因定位（2026-10-20）

**预注册**：[`benchmark/newtonbench/saturation-protocol.md`](../benchmark/newtonbench/saturation-protocol.md)，写在任何验证运行之前。
**一句话结论**：`_gap_wide_enough` 不是杠杆点。真正让成本停不下来的，是 `scripts/rds_compass.py:468` 那条饱和守卫**在 basin 探针场景里不可达**——它要求「jump 之后没有新观测」，而 basin 探针本身就会产生新观测。

## 1. 现场

`regime-double` B=24 的实际构成（HEAD 与工作区逐字相同）：

```
21 次实验 = PROBE 15 + CONFIRM 3 + EXPERIMENT 1 + HOLDOUT 2
claim_exact = 32/32, status = GOAL_IDENTIFIED
predict = ite((x < 10), (1 + x), (x + x))
```

对照基线 `benchmark/newtonbench/results/pareto.json`（tracked）：B=6/8/12/16/24/32 → exp 6/8/12/16/**21/21**，四档 claim_exact 都是 32/32。

## 2. 实验一：G2 判据零杠杆

`scripts/rds_compass.py:420` 的 `_gap_wide_enough` 两版：

| 版本 | 判据 |
|---|---|
| HEAD | `return bool(gaps) and max(gaps) > min(gaps)` |
| 工作区（G2） | `return max(gaps) > max(2, len(plan['candidates']) // 10)` |

四档实测（`python -B benchmark/newtonbench/probe_cell.py <world> <budget> <out.json>`），HEAD 快照与工作区各跑一次：

| B | HEAD exp | G2 exp | actions | claim_exact |
|---|---|---|---|---|
| 12 | 12 | 12 | 逐字相同 | 32 |
| 16 | 16 | 16 | 逐字相同 | 32 |
| 24 | 21 | 21 | 逐字相同 | 32 |
| 32 | 21 | 21 | 逐字相同 | 32 |

**判定：回滚**（机械判定，不是价值判断）。预注册门槛 `max(Exp@16,Exp@24,Exp@32) − Exp@12 ≤ 2`，实测 `max(16,21,21) − 12 = 9`，差 7。回滚用 `git checkout -- scripts/rds_compass.py`，不产生 commit；回滚后 `LawTests` 13 OK。

## 3. 实验二：`max_gap` 声明是死的

把 `verification.max_gap` 从 2 改成 0 重跑 regime-double B=24：

```
max_gap=2: declared=2 experiments=21 probes=15
max_gap=0: declared=0 experiments=21 probes=15   ← 逐字相同
```

`scripts/rds_compass.py:536-540` 的 COVERAGE_GAP 分支返回
`{'action': 'PROBE', 'candidate': probe, 'reason': 'COVERAGE_GAP', 'leader': rows[0]['id']}`
——**不带 `basin` 键**。实测 15 条 PROBE **全部带 `basin`**，所以 COVERAGE_GAP 一次都没触发，`max_gap` 在这个场景里是死声明。

## 4. 实验三：15/15 都走 `:490`

`decide` 有两个 PROBE 返回点，都带 `basin`：

| 行 | 返回 | 守卫 |
|---|---|---|
| `:472` | 带 `reason='NO_RIVAL_WITHIN_BOUND_AT_CURRENT_EVIDENCE'` | `diagnosis['trigger'] and last and _barren(state, last) and last.get('observations') == len(state['observations'])` |
| `:490` | 不带 `reason` | 无（basin 触发即买） |

账本里的 decision 只保留 `['action','basin','candidate','head','observations','seq']`。但**根因不是存储层丢弃**：patch `rds_compass.next_step`（`scripts/rds_compass_drive.py:63` 是 `step = compass.next_step(state, stale)`，走模块属性，patch 有效）记录 `:490` 返回的**完整 dict**，它本身就没有 `reason` 键——`:490` 从不自述自己为什么被选中，所以从账本分不开这两个返回点：

```
probes: 15
reasons: {"<no reason>": 15}
trigger_counts: {REFUTED_STREAK: 2, ALL_REFUTED: 13}
```

**15/15 无 reason → 全部走 `:490`；`:472` 的守卫一次都没成立。**

## 5. 根因

`:468` 的守卫要求「jump 之后没有新观测」（`last.get('observations') == len(state['observations'])`）。
**但 basin 探针本身就会产生新观测。** 第一次买点之后这个等式永远为假，整条守卫变成不可达代码——注释自述的功能是防饱和，实测不承担这个功能。

于是 `ALL_REFUTED`/`REFUTED_STREAK` 触发一次 → 买一个探针点 → 仍触发 → 再买，直到 `_boundary_probe` 找不到 `gap > 2`（B=32 停在这里）或 `_cap(plan) − reserve` 耗尽（B=24 的 `24 − 3 = 21`）。

## 5.1 独立复核（另一 agent 的 A/B 报告）

`docs/satstop-probe-report.md`（146 行）用独立工具链复核，结论一致且更硬：

- **四档 HEAD/工作区成对 sha256 相等**：B=12 → `1805beef…`、B=16 → `2e63faf1…`、B=24 → `a8a1fd22…`、B=32 → `2d4068ff…`，`cmp` 逐字节相同，experiments 12/16/21/21。
- **`--max-gap 0/2/8/默认` 四个文件唯一差异是回显的 `declaration.verification.max_gap`**（diff 各只 1 行），其余 604 字节逐字节相同。
- **`--dump-decisions` 落盘的 29 条决策里 `COVERAGE_GAP` 出现 0 次**，15 个 PROBE 的 `reason` 全为 `null`。
- 机制：`_boundary_probe` 的签名是 `def _boundary_probe(state, min_gap=2)`（`scripts/rds_compass.py:390`），**默认值 2 硬编码**，`:490` 调用不带参数 → 该路径与声明里的 `max_gap` 无关。
- **3 个 CONFIRM 的 reason 全是 `MINIMUM_CONFIRMATIONS`**，`SPEND_BUDGET_ON_REFUTATION` 0 次。

一处值得记的差异：B=12 的 predict 是 `ite((x < (19/2)), (1 + x), (x + x))`（有理数边界，且是**正确的** 9.5），B≥16 才是 `ite((x < 10), ...)`。B=12 用 12 次实验拿到了比 B=24 的 21 次更精确的边界——**自我帕累托支配在预测表达上也看得见**，不只是实验数。

## 6. 修法（待 k3 裁定）

- **(a)** 把 `:468` 的条件从「没有新观测」改成「自 jump 以来 synthesis 没有再产出新对手」——让既有守卫的语义与注释一致，改动最小。
- **(b)** 给 basin 探针另加计数上限：连续 N 次触发没产出新对手就 ESCAPE 或 HANDOFF。

## 7. 复现

```bash
# 单格：HEAD 快照与工作区各跑一次对比
python -B benchmark/newtonbench/probe_cell.py regime-double 24 cell.json

# 四档（必须写新路径，results/pareto.json 是基线，覆盖它等于毁掉对照）
python -B benchmark/newtonbench/pareto.py benchmark/newtonbench/results/pareto-satstop.json

# PROBE 来源区分，两条独立路径：
python -B benchmark/newtonbench/probe_cell.py regime-double 24 cell.json --dump-decisions
python -B ../probe_sources2.py   # patch next_step，看 :490 返回的完整 dict
```

## 8. 未决

- **`:490` 不自述 reason**（返回 dict 里没有 `reason` 键，账本里记成 `null`），导致这条承担 15/21 实验的主路径在账本里不可区分。修法是给它补一个显式 reason 字符串。独立复核的 agent 也把这条列为最值得做的下一步。
- `ALL_REFUTED` 该不该有停止条件（13/15 都在这条上）：所有对手被证伪后继续买点找新对手，这算不算科研上的浪费。
- 证据边界：只覆盖 `regime-double@24` 一个单元，其他 world / 预算档未测。
- 环境注意：`scripts/rds_compass.py` 有一版 G2 字节（sha256 `4333c595…`）已回滚且**不可恢复**，`git log -S "len(plan['candidates']) // 10" --all` 全历史无命中，确认 G2 从未提交。

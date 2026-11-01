# pareto 结果行新增两字段的回归验证（2026-10-21）

## 1. 预注册判定规则（跑前写死）

- **PASS 当且仅当**：`rows` 差异字段集合 **恰好** 是 `{confirmations, required_confirmations}`，且 rds 臂每行
  `required_confirmations == 2`，且其余字段逐字节不变。
- 任何其他字段变化 = **FAIL**，不解释成噪声或 CRLF 差异。
- 比对口径：key = `(task, arm, budget)`，36 格必须对齐；排除字段 `seconds`、`campaign_root`、
  `elapsed_seconds`、`certification_check`、`campaign`（实测两文件 rows 中只有 `certification_check`
  存在，其余四项均不存在，属防御性排除）。

## 2. 起点

- 目录：`D:/Myfile/work space/RDS/rds-357`，分支 `release/6.0.0-compass`
- `git status --short`（跑前）：

```
 M benchmark/newtonbench/pareto.py
 M docs/pareto-holdoutfirst.md
```

- 工作区 `pareto.py` 未提交 diff 只有这一个 hunk（`7 insertions(+), 1 deletion(-)`）：待验证的两行
  `'confirmations': verification.get('novel_confirmations', 0)`、
  `'required_confirmations': verification.get('required_confirmations', 0)`，加四行注释。
- `benchmark/newtonbench/results/pareto-confirmations.json` 跑前不存在。
- `pareto-stage2.json` 跑前 sha256：
  `12da476e8c9ca37e2bed03efb60206ac51d1fb56017670c140f2996b77784bc6`

## 3. 命令与结果

命令原文（任务指定，实际执行时加了计时外壳并把 stdout 重定向到 `/tmp/pareto-confirmations.run.log`）：

```
cd "D:/Myfile/work space/RDS/rds-357" && python -B benchmark/newtonbench/pareto.py benchmark/newtonbench/results/pareto-confirmations.json
```

- 退出码：**0**
- 耗时：**267 s**（约 4 分 27 秒，`date +%s` 前后差）
- 产出 sha256：`a815377f09b0a8d4e215dca97dd0bd822e549b7fef7053caa5c02ac0c8e87dd3`

## 4. 逐字段比对（rows）

`pareto-stage2.json`（改前）vs `pareto-confirmations.json`（改后）：

- rows 数：36 / 36，key 集合完全相等，无重复、无缺格。
- 差异条数：**36**，全部为 rds 行上的两个纯新增字段（改前 `'<absent>'`）。
- 其余既有字段（`task`、`arm`、`budget`、`experiments`、`status`、`claim`、`claim_exact`、
  `best_effort_exact`、`exact_match`、`claimed`、`false_certification`、`law`）**零变化**；
  baseline-uniform 的 18 行不含这两个字段，与改前完全一致。

| cell（task/rds/budget） | 字段 | 改前 -> 改后 |
| --- | --- | --- |
| regime-double/rds/6 | confirmations | `<absent>` -> 1 |
| regime-double/rds/6 | required_confirmations | `<absent>` -> 2 |
| regime-double/rds/8 | confirmations | `<absent>` -> 3 |
| regime-double/rds/8 | required_confirmations | `<absent>` -> 2 |
| regime-double/rds/12 | confirmations | `<absent>` -> 3 |
| regime-double/rds/12 | required_confirmations | `<absent>` -> 2 |
| regime-double/rds/16 | confirmations | `<absent>` -> 3 |
| regime-double/rds/16 | required_confirmations | `<absent>` -> 2 |
| regime-double/rds/24 | confirmations | `<absent>` -> 7 |
| regime-double/rds/24 | required_confirmations | `<absent>` -> 2 |
| regime-double/rds/32 | confirmations | `<absent>` -> 15 |
| regime-double/rds/32 | required_confirmations | `<absent>` -> 2 |
| sparse-mid/rds/6 | confirmations | `<absent>` -> 6 |
| sparse-mid/rds/6 | required_confirmations | `<absent>` -> 2 |
| sparse-mid/rds/8 | confirmations | `<absent>` -> 0 |
| sparse-mid/rds/8 | required_confirmations | `<absent>` -> 2 |
| sparse-mid/rds/12 | confirmations | `<absent>` -> 3 |
| sparse-mid/rds/12 | required_confirmations | `<absent>` -> 2 |
| sparse-mid/rds/16 | confirmations | `<absent>` -> 3 |
| sparse-mid/rds/16 | required_confirmations | `<absent>` -> 2 |
| sparse-mid/rds/24 | confirmations | `<absent>` -> 7 |
| sparse-mid/rds/24 | required_confirmations | `<absent>` -> 2 |
| sparse-mid/rds/32 | confirmations | `<absent>` -> 15 |
| sparse-mid/rds/32 | required_confirmations | `<absent>` -> 2 |
| sparse-tail/rds/6 | confirmations | `<absent>` -> 6 |
| sparse-tail/rds/6 | required_confirmations | `<absent>` -> 2 |
| sparse-tail/rds/8 | confirmations | `<absent>` -> 8 |
| sparse-tail/rds/8 | required_confirmations | `<absent>` -> 2 |
| sparse-tail/rds/12 | confirmations | `<absent>` -> 12 |
| sparse-tail/rds/12 | required_confirmations | `<absent>` -> 2 |
| sparse-tail/rds/16 | confirmations | `<absent>` -> 16 |
| sparse-tail/rds/16 | required_confirmations | `<absent>` -> 2 |
| sparse-tail/rds/24 | confirmations | `<absent>` -> 24 |
| sparse-tail/rds/24 | required_confirmations | `<absent>` -> 2 |
| sparse-tail/rds/32 | confirmations | `<absent>` -> 0 |
| sparse-tail/rds/32 | required_confirmations | `<absent>` -> 2 |

差异字段集合（并集）：`{confirmations, required_confirmations}`，恰好两条，无第三个字段。
rds 臂 `required_confirmations` 分布：`{2}`，18/18 行满足。

## 5. 三个指定格子的新字段值

| cell | confirmations | required_confirmations | status | claim | exact_match | claim_exact |
| --- | --- | --- | --- | --- | --- | --- |
| sparse-tail/rds/32 | 0 | 2 | JUDGMENT_REQUIRED | CONSISTENT_BUT_UNCONFIRMED | 32 | 0 |
| regime-double/rds/12 | 3 | 2 | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 32 | 32 |
| sparse-mid/rds/12 | 3 | 2 | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 32 | 32 |

自洽性：`claimed=true` 的 15 行 confirmations 均 >= 2；`claimed=false` 的 3 行（regime-double/b6 = 1、
sparse-mid/b8 = 0、sparse-tail/b32 = 0）均 < 2。两字段与 claim 语义一致，无「已 claim 却未达 required」的行。

## 6. `pareto-stage2.json` 完整性

- 跑前：`12da476e8c9ca37e2bed03efb60206ac51d1fb56017670c140f2996b77784bc6`
- 跑后：`12da476e8c9ca37e2bed03efb60206ac51d1fb56017670c140f2996b77784bc6`（一字未变）

跑后 `git diff --stat` 对该文件为空（与 HEAD 一致）。

## 7. 最终判定

**PASS**（按本文件第 1 节死规则，对 `rows` 口径）：差异字段集合恰好是
`{confirmations, required_confirmations}`；rds 臂 18/18 行 `required_confirmations == 2`；
其余字段逐字节不变。

**未执行任何 commit / push / checkout / stash / revert；本次未修改任何源码。**

## 8. 范围外发现（不影响 rows 判定，但必须让 lead 知道）

### 8.1 顶层摘要字段变了，来源是更早的口径提交，不是本次两字段

新文件顶层 `correct_count` / `experiments_per_correct` / `cost_per_correct_rule` 与 stage2 不同：
全文件 15 -> 14、39.2 -> 42.0；rds 臂 15 -> 14、19.6 -> 21.0。

证据：`pareto-stage2.json` 由 `7ca23c8`（2026-10-21 11:20:42）提交；commit `75570aa`
（"stop counting a refuted law as a solved equation"，2026-10-21 11:23:00）在它之后 2 分 18 秒，
把 `cost_per_correct` 的筛选从 `claim_exact` 改为 `claim_exact and not false_certification`，
其 commit message 列出的 before/after 数字与本处观察逐项一致（15/14、19.6/21.0、39.2/42.0）。
即 `pareto-stage2.json` 的顶层是旧口径，`rows` 本身早已带 `false_certification`。

交叉验证：`75570aa` 附带的 `benchmark/newtonbench/results/pareto-stage2-costfix.json`
与新文件对比，顶层 **逐键相等**，`rows` 除两个新字段外 **零差异** → 顶层变化与本次改动无关。

### 8.2 运行窗口内有并发改动：`scripts/rds_compass.py`

跑前 `git status` 只有 2 项；跑后多了 ` M scripts/rds_compass.py`（mtime 12:54:25，`_polynomial_check`
+13/-1，内容是「用已测 receipt 反驳插值」）。**不是本 agent 所为**，本 agent 未写任何 `scripts/` 文件；
`pareto.py` 运行期间未被二次改动（mtime 12:50:35，diff 与检查时相同）。
运行未被污染的依据：结果与 `pareto-stage2-costfix.json`（`75570aa` 历史产物）在 rows 与顶层逐字段一致
（除两个新字段）；若并发改动影响了本次运行，这一致性不成立。

## 9. 复现

```
cd "D:/Myfile/work space/RDS/rds-357"
python -B benchmark/newtonbench/pareto.py benchmark/newtonbench/results/pareto-confirmations.json
python -B docs/pareto-confirmations-20261021-compare.py
sha256sum benchmark/newtonbench/results/pareto-stage2.json
```

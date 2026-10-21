# Pareto A/B: `5cb73ff` (Stage 2 之前) vs `601bc53` (HEAD)

日期 2026-10-21。分支 `release/6.0.0-compass`。
本文件记录一次干净的全量 A/B：同一份 `pareto.py`，同一台机器，父提交与 HEAD 各跑一次全量，逐字段比对。

## 0. 结论

- `rows` 36 行 **0 处差异**，`rds` 与 `baseline-uniform` 两个聚合块 **0 处差异**，顶层字段 **0 处差异**。
- 三份结果文件（`pareto-pre-stage2.json`、`pareto-stage2.json`、`pareto-stage2-nostage2.json`）**字节级相同**：规范化换行后 `sha256 12da476e8c9ca37e2bed03efb60206ac51d1fb56017670c140f2996b77784bc6`，大小同为 27967 字节。工作区原始文件是 CRLF（28947 字节，`sha256 4427…35037`），`.gitattributes` 的 `* text=auto eol=lf` 会在入库时转成 LF，以 LF 版为准。
- 可归因于 `639e19f` / `890f8d1` / `601bc53` 的差异：**0 处**。**UNKNOWN 清单：空**。
- `pareto-stage2.json` 里 `rds` 块的那一条假认证在第 6 节；两份文件中该行也完全相同。

## 1. 为什么必须 A/B，而不是直接比旧 `pareto.json`

旧的 `benchmark/newtonbench/results/pareto.json` 是 **schema 1**：

- 顶层是 list（36 个元素），每行只有 `task / arm / budget / experiments / exact_match` 5 个字段；
- 没有 `claimed`、`claim`、`status`、`claim_exact`、`best_effort_exact`、`certification_check`、`false_certification`，也没有任何 `law` 表达式。

当前 `pareto.py` 写的是 **schema 2**：顶层 dict，含 `rows`、`rds`、`baseline-uniform`、`false_certification_*`、`experiments_per_correct`。schema 2 由 `ce134b6 feat(benchmark): measure false certification on independent points` 引入。

两件事让旧文件不可用于归因：

1. **跨了太多 commit。** `pareto.json` 是 tracked 文件，当前内容来自 `addf892 feat(benchmark): share the paid-query ledger, ...`（2026-10-20 16:23:48）。从 `addf892` 到父提交 `5cb73ff`（2026-10-21 10:55:54）有 **92 个 commit**；文件的工作区 mtime 是 2026-10-20 21:30:55，也无法对应到某个确定的 HEAD。
2. **无法重建假认证口径。** `benchmark/newtonbench/pareto.py:248` 的 `load_rows` 接受两种形状，但 `:253` 的 `backfill` 对 schema 1 明确返回
   `{'schema': 1, ..., 'false_certification_status': 'UNKNOWN_NO_LAW_STORED', ...}`：
   schema 1 只存了计分后的计数，没存 law 表达式，`claimed` / `false_certification` 不能重算。
   同时 5 字段与 12 字段没有一一对应关系，逐字段比较无从谈起。

所以对照必须在 `5cb73ff` 上重跑一次，拿到同 schema、同脚本的全量输出。

## 2. 复现命令与耗时

建工作树（钉死在父提交，不碰主仓）：

```
cd "D:/Myfile/work space/RDS/rds-357"
git worktree add --detach "D:/Myfile/work space/RDS/rds-pre-stage2" 5cb73ff
```

跑批命令 A（父提交，本次执行）：

```
cd "D:/Myfile/work space/RDS/rds-pre-stage2"
python -B benchmark/newtonbench/pareto.py "D:/Myfile/work space/RDS/rds-357/benchmark/newtonbench/results/pareto-pre-stage2.json"
```

- 实测耗时 **311 秒**（2026-10-21 11:13:10 启动 → 11:18:21 结果文件落盘，由启动 epoch `1792552390` 与结果文件 mtime epoch `1792552701` 相减得到）。

跑批命令 B（HEAD，历史运行，本次未重跑）：

```
python -B benchmark/newtonbench/pareto.py "D:/Myfile/work space/RDS/rds-357/benchmark/newtonbench/results/pareto-stage2.json"
```

- 那次运行的耗时**没有记录**；`pareto-stage2.json` 的 mtime 是 2026-10-21 11:11:37，`601bc53` 提交于 11:07:03，`639e19f` 提交于 11:05:34。本次没有重跑 HEAD，因为父提交跑完后与既有 `pareto-stage2.json` 字节相同（见第 3 节），重跑不会增加信息。

删工作树（结果文件已确认在主仓后才删）：

```
git -C "D:/Myfile/work space/RDS/rds-357" worktree remove --force "D:/Myfile/work space/RDS/rds-pre-stage2"
```

- 删除后 `git worktree list` 只剩主仓与 `ralph-compass-report-split` 工作树。

两棵树里的 `benchmark/newtonbench/pareto.py` 没有提交差异（`git diff 5cb73ff 601bc53 -- benchmark/newtonbench/pareto.py` 为空）。字节不同仅因主仓 `core.autocrlf=true`（CRLF 332 行）而新工作树是 LF；LF 归一化后两份文件完全相同，执行逻辑同一。

## 3. 36 行逐字段比对

两份都是 schema 2。行匹配 key 用 `(task, budget, arm)`：`(task, budget)` 每格对应两行（`rds` 与 `baseline-uniform`），单独两列不唯一。36 行 = 18 格 × 2 臂。

排除的时间字段集合：`seconds` / `elapsed_seconds` / `campaign_root` / `started_at` / `finished_at`。本环境产出的两份文件都不含这些键，因此排除集合是预防性的，实际未吞掉任何字段。

比对方式：对每行递归比较全部键与值（`certification_check` 整棵子树也逐字段比），数值按原样比较（`Fraction` 字符串不转浮点）。

**结果：36 / 36 行全部相同，差异表为空（0 行、0 字段）。**

相同字段集合（12 个键）：`task` `arm` `budget` `experiments` `status` `claim` `claim_exact` `best_effort_exact` `exact_match` `claimed` `false_certification` `certification_check`。`baseline-uniform` 行没有 `status` / `claim` / `claim_exact` / `best_effort_exact` 四个键，其余相同。

36 行的共同值如下（两份文件里逐字段相同；`—` 表示该臂没有这个键）。

| task | budget | arm | status | claim | experiments | exact_match | claim_exact | best_effort_exact | false_certification |
|---|---:|---|---|---|---:|---:|---:|---:|---|
| regime-double | 6 | rds | JUDGMENT_REQUIRED | CONSISTENT_BUT_UNCONFIRMED | 6 | 30 | 0 | 0 | false |
| sparse-mid | 6 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 6 | 31 | 31 | 0 | false |
| sparse-tail | 6 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 6 | 31 | 31 | 0 | false |
| regime-double | 8 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 8 | 30 | 30 | 0 | false |
| sparse-mid | 8 | rds | JUDGMENT_REQUIRED | CONSISTENT_BUT_UNCONFIRMED | 8 | 32 | 0 | 0 | false |
| sparse-tail | 8 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 8 | 31 | 31 | 0 | false |
| regime-double | 12 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 12 | 32 | 32 | 0 | **true** |
| sparse-mid | 12 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 12 | 32 | 32 | 0 | false |
| sparse-tail | 12 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 12 | 31 | 31 | 0 | false |
| regime-double | 16 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 16 | 32 | 32 | 0 | false |
| sparse-mid | 16 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 16 | 32 | 32 | 0 | false |
| sparse-tail | 16 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 16 | 31 | 31 | 0 | false |
| regime-double | 24 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 24 | 32 | 32 | 0 | false |
| sparse-mid | 24 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 24 | 32 | 32 | 0 | false |
| sparse-tail | 24 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 24 | 31 | 31 | 0 | false |
| regime-double | 32 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 32 | 32 | 32 | 0 | false |
| sparse-mid | 32 | rds | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 32 | 32 | 32 | 0 | false |
| sparse-tail | 32 | rds | JUDGMENT_REQUIRED | CONSISTENT_BUT_UNCONFIRMED | 32 | 32 | 0 | 0 | false |
| regime-double | 6 | baseline-uniform | — | — | 6 | 0 | — | — | **true** |
| sparse-mid | 6 | baseline-uniform | — | — | 6 | 31 | — | — | false |
| sparse-tail | 6 | baseline-uniform | — | — | 6 | 0 | — | — | **true** |
| regime-double | 8 | baseline-uniform | — | — | 8 | 0 | — | — | **true** |
| sparse-mid | 8 | baseline-uniform | — | — | 8 | 31 | — | — | false |
| sparse-tail | 8 | baseline-uniform | — | — | 8 | 0 | — | — | **true** |
| regime-double | 12 | baseline-uniform | — | — | 12 | 0 | — | — | **true** |
| sparse-mid | 12 | baseline-uniform | — | — | 12 | 0 | — | — | **true** |
| sparse-tail | 12 | baseline-uniform | — | — | 12 | 0 | — | — | **true** |
| regime-double | 16 | baseline-uniform | — | — | 16 | 0 | — | — | **true** |
| sparse-mid | 16 | baseline-uniform | — | — | 16 | 31 | — | — | false |
| sparse-tail | 16 | baseline-uniform | — | — | 16 | 0 | — | — | **true** |
| regime-double | 24 | baseline-uniform | — | — | 24 | 0 | — | — | **true** |
| sparse-mid | 24 | baseline-uniform | — | — | 24 | 0 | — | — | **true** |
| sparse-tail | 24 | baseline-uniform | — | — | 24 | 0 | — | — | **true** |
| regime-double | 32 | baseline-uniform | — | — | 32 | 0 | — | — | **true** |
| sparse-mid | 32 | baseline-uniform | — | — | 32 | 0 | — | — | **true** |
| sparse-tail | 32 | baseline-uniform | — | — | 32 | 1 | — | — | **true** |

行级分布（两份文件一致）：

- `rds` 18 行：15 行 `GOAL_IDENTIFIED` + `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`，3 行 `JUDGMENT_REQUIRED` + `CONSISTENT_BUT_UNCONFIRMED`（`(regime-double, 6)`、`(sparse-mid, 8)`、`(sparse-tail, 32)`）。
- `rds` 的 `exact_match` 分布：32 出现 10 次、31 出现 6 次、30 出现 2 次。
- `baseline-uniform` 18 行全部 `claimed=true`；`false_certification` 15 true / 3 false，3 个 false 是 `(sparse-mid, 6)`、`(sparse-mid, 8)`、`(sparse-mid, 16)`，`exact_match` 均为 31。
- `certification_check` 子树（含 32 个采样点上的 `max_abs_error`、`worst_point`、`reason` 等）在 36 行上逐字段相同。

## 4. 两个聚合块与顶层

`rds` 块（两份文件相同）：

| 字段 | 值 |
|---|---|
| false_certification_rate | 0.06666666666666667 |
| false_certification_count | 1 |
| claimed_count | 15 |
| checkable_claimed_count | 15 |
| false_certification_status | FALSE_CERTIFICATIONS_FOUND |
| correct_count | 15 |
| experiments_total | 294 |
| experiments_per_correct | 19.6 |
| cost_per_correct_rule | experiments over the arm divided by the rows whose law reproduced the target with 0 counterexamples |

`baseline-uniform` 块（两份文件相同）：

| 字段 | 值 |
|---|---|
| false_certification_rate | 0.8333333333333334 |
| false_certification_count | 15 |
| claimed_count | 18 |
| checkable_claimed_count | 18 |
| false_certification_status | FALSE_CERTIFICATIONS_FOUND |
| correct_count | 0 |
| experiments_total | 294 |
| experiments_per_correct | null |
| cost_per_correct_rule | experiments over the arm divided by the rows whose law reproduced the target with 0 counterexamples |

顶层字段（两份文件相同）：

| 字段 | 值 |
|---|---|
| schema | 2 |
| false_certification_rate | 0.48484848484848486 |
| false_certification_count | 16 |
| claimed_count | 33 |
| checkable_claimed_count | 33 |
| false_certification_status | FALSE_CERTIFICATIONS_FOUND |
| correct_count | 15 |
| experiments_total | 588 |
| experiments_per_correct | 39.2 |
| false_certification_rate_rule | false_certification_count / checkable_claimed_count |
| false_certification_rule | an arm that states a law differs from the world truth on 32 independent samples in the declared box, beyond the zero tolerance; null (UNKNOWN) when no checkable claim exists, never 0.0 |
| cost_per_correct_rule | experiments over the arm divided by the rows whose law reproduced the target with 0 counterexamples |

聚合层面的口径：顶层 `false_certification_count = 16` 是两臂合计（`rds` 1 + `baseline-uniform` 15）；题目所说的 `rds` 聚合里的 1 是 `rds` 块自己的计数。

## 5. 判定

### 5.1 可归因于 Stage 2 的差异

**0 处。** 判定依据是 `rows` 36 行、两个聚合块、顶层全部字段逐字段相同，且两份文件字节相同。

为什么这条链上的三个 commit 本来就可能不改变输出：

- `639e19f feat(compass): let a declared hypothesis set stand on its own` 的唯一代码改动在 `scripts/rds_compass.py:554`：RIVALS 条件追加 `and not _all_declared(rows)`（`_all_declared` 定义在 `:197`）。它减少的是 RIVALS 轮次（决策步数），不是购买的观测点。该 commit 的 message 原文含 **"eight RIVALS rounds gone, with the purchased points and the final claim unchanged"**，并自述 `sparse-mid, budget 8` 从 17 步降到 10 步。`pareto.py` 的 rds 行记录 `experiments` / `status` / `claim` / `claim_exact` / `best_effort_exact` / `exact_match` 与 certification 结果，不记录步数，所以这个改动落在结果文件的盲区里。
- `601bc53 fix(compass): report zero undecided cells instead of dropping the field` 只改 `scripts/rds_compass.py:1541`，给 `verify()` 的 `replay` 字典加 `undecided` 默认 0。`pareto.py` 的 `main` 只读 `verification['claim']`、`verification['predict']`、`verification['best_effort']`，不读 `replay`，所以不影响本文件。
- `890f8d1 docs(benchmark): record why the DERIVED search keeps its full budget` 只新增 `docs/derived-cap-20261021.md`，不碰代码。

### 5.2 无法归因（UNKNOWN）清单

**空。** 不存在任何字段级差异需要归因，因此也没有"看起来不像 Stage 2 能造成"的差异。

边界说明：字节相同只证明**输出层**等价。`639e19f` 声称的内部步数变化（17 → 10）不在 `pareto` 结果文件里，本次 A/B 也没有测步数，所以本报告不对步数做断言。若要把步数也纳入 A/B，需要在两棵树上同时记录 decision 轨迹或 compass run 的 argv/receipt，那是另一个口径。

## 6. 那条假认证行（只报事实）

`pareto-stage2.json` 的 `rds` 聚合 `false_certification_count = 1`、`false_certification_status = FALSE_CERTIFICATIONS_FOUND`。对应行：

- `task = regime-double`
- `budget = 12`
- `arm = rds`

该行在 `pareto-pre-stage2.json` 里也完全相同。完整行（原样，来自 `pareto-stage2.json`）：

```json
{
 "task": "regime-double",
 "arm": "rds",
 "budget": 12,
 "experiments": 12,
 "status": "GOAL_IDENTIFIED",
 "claim": "CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS",
 "claim_exact": 32,
 "best_effort_exact": 0,
 "exact_match": 32,
 "claimed": true,
 "false_certification": true,
 "certification_check": {
  "points_checked": 32,
  "max_abs_error": "7956287824/887427361",
  "tolerance": "0",
  "tolerance_rel": "0",
  "seed": 357,
  "sampling": "uniform in [min(xs), max(xs)], rounded half away from zero",
  "independence": "excludes x0..x5, the only points either arm saw",
  "reason": "DIFFERS_BEYOND_TOLERANCE on x=8843715185/887427361",
  "evaluation_failures": 0,
  "exploded_differences": 0,
  "visible_clashes_redrawn": 0,
  "worst_point": {
   "x": "8843715185/887427361"
  }
 }
}
```

要求的四个量：

- `law`：**该文件没有 `law` 键**。`benchmark/newtonbench/pareto.py:159` 的 `certify_arm` 只回填 `claimed`、`false_certification`、`certification_check`，不落盘 law 表达式。为了给原文，按 `benchmark/newtonbench/pareto.py:284` 的 `main` 同一构造在 HEAD `601bc53` 上单独重跑该格（seed 357），得到
  `law = "ite((x < (19/2)), (1 + x), (x + x))"`，
  同一次重跑的 `status=GOAL_IDENTIFIED`、`claim=CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`、`claim_exact=32`、`experiments=12`，与该行逐项一致。这只是一次确定性复现，不是 seed 重复。
- `max_abs_error = "7956287824/887427361"`（精确 Fraction，约 8.9655）。
- `tolerance = "0"`（`tolerance_rel = "0"`）。
- `points_checked = 32`。

同一行同时出现 `exact_match = 32` 与 `max_abs_error > 0`，是两套口径：`exact_match` / `claim_exact` 在整数 `0..31` 上计分；`certification` 在 32 个独立均匀采样点上判（含非整数，`worst_point.x = 8843715185/887427361 ≈ 9.9664`）。`benchmark/newtonbench/pareto.py:35-44` 的注释写明了这个划分，两种计数可以共存。本报告只记录事实，不评价该行是否应当存在。

## 7. 限制

- **单次运行、无 seed 重复**：两次全量各跑一次，`seed=357` 固定。可以声明"这两次确定性运行的输出相同"，不能声明"输出分布相同"或"带随机扰动仍相同"。
- HEAD 那次的历史耗时未记录；父提交本次实测 311 秒。两棵树的硬件、Python（3.13.5）、同一 `pareto.py` 逻辑相同，但 HEAD 那次不在本次观测窗口内。
- A/B 只覆盖 `pareto.py` 的输出。`pareto_l2a.py`、`devset_run.py` 等其他消费者不在此次比对范围内。
- 两棵树 `pareto.py` 的字节差异只来自 CRLF/LF（主仓 `core.autocrlf=true`），归一化后相同。
- 三份结果文件与本报告在 `7ca23c8` 入库；`benchmark/newtonbench/results/pareto.json` 未改动，它继续作为 `l2a_accept.py:61` 读取的冻结 schema-1 对照。

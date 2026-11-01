# pareto Stage 2 重跑：36 格前后对比

- 仓库：`D:\Myfile\work space\RDS\rds-357`
- 分支：`release/6.0.0-compass`，HEAD `601bc534f7917692b69e80975865c2ac14616e47`
- Python 3.13.5
- 本次产物：`benchmark/newtonbench/results/pareto-stage2.json`（新文件，不覆盖旧 `pareto.json`）

## 1. 为什么重跑

`benchmark/newtonbench/pareto.py:296` 给每个 rds 格子传入一条**预先声明**的假设：

```python
path = fx.law_campaign(root, law, range(32), hypotheses=[('lin', 'x+1')], ...)
```

Stage 2（commit `639e19f`）改了这条路径上的买点策略：

- `scripts/rds_compass.py:197` 新增 `def _all_declared(rows): return bool(rows) and all(h.get('origin') == 'declared' for h in rows)`；
- `scripts/rds_compass.py:554` 的 RIVALS 购买条件追加 `and not _all_declared(rows)`；
- `scripts/rds_compass_declaration.py:232` 用 `model.compile_hypothesis({**value, 'origin': 'declared'}, ...)` 给来源打标，`tests/compass_fixture.py:66` 把 hypotheses 写进 declaration。

即：当候选假设全部来自 declared 时，不再购买 RIVALS 轮。

现有的 `benchmark/newtonbench/results/pareto.json` 生成于 `addf892`（2026-10-20 16:23:48），当时 `pareto.py` 还是旧输出格式（`json.dumps(rows, indent=1)`，schema 1 的 bare list）；schema 2 与 false certification 由 `ce134b6`（2026-10-20 21:34:58）引入。因此旧文件不能与 HEAD 直接等价，必须在 HEAD 上重跑一份同口径的结果。

## 2. 命令与耗时

全量 36 格：

```bash
cd "D:\Myfile\work space\RDS\rds-357"
python -B benchmark/newtonbench/pareto.py benchmark/newtonbench/results/pareto-stage2.json
```

- 开始 `2026-10-21T11:07:42`，结束 `2026-10-21T11:11:37`，耗时 **235 s**，退出码 0。
- 产物 `pareto-stage2.json` 28,947 bytes，`schema: 2`，36 行。

隔离对照（最小反向测试，同 HEAD，只把 `rds_compass._all_declared` 换成 `lambda rows: False` 模拟 Stage 2 之前的行为）：

- 开始 `2026-10-21T11:12:11`，结束 `2026-10-21T11:17:23`，耗时 312 s，退出码 0；
- 产物 `benchmark/newtonbench/results/pareto-stage2-nostage2.json`。

跑前跑后核对：HEAD 未变；`benchmark/newtonbench/results/pareto.json` 的 SHA256 前后都是 `cad299b801a3e5726890409fa5e57b229c502cd17b917caa72c425909637e9cc`，未被覆盖；`benchmark/newtonbench/pareto.py`、`scripts/rds_compass.py` 等内核文件哈希前后一致。

## 3. 对比口径

只比较两个文件里**都存在**的字段，排除任何含时间语义的键（`seconds`、`elapsed_seconds`、`campaign_root`）。

| arm | 旧文件字段 | 新文件字段 | 可比字段 |
|---|---|---|---|
| `baseline-uniform` | 5（`arm,budget,exact_match,experiments,task`） | 8（多 `claimed,false_certification,certification_check`） | 2：`experiments`、`exact_match` |
| `rds` | 9（`arm,best_effort_exact,budget,claim,claim_exact,exact_match,experiments,status,task`） | 12（多 `claimed,false_certification,certification_check`） | 6：`experiments`、`status`、`claim`、`claim_exact`、`best_effort_exact`、`exact_match` |

`claimed` / `false_certification` / `certification_check` 是 schema 1→2 新增的字段，只在新文件存在，属于格式差异，不计入行为对比。

## 4. 结果

- `baseline-uniform` 18 格：**全部相同**（按 2 个可比字段）。
- `rds` 18 格：**10 格全同，8 格有差异**（差异共 32 个字段值）。

### 4.1 全部 36 格明细

差异格用 **old -> new** 标出；`-` 表示该字段在两侧都不存在。

| world | arm | budget | experiments | status | claim | claim_exact | best_effort_exact | exact_match |
|---|---|---|---|---|---|---|---|---|
| sparse-tail | baseline-uniform | 6 | 6 | - | - | - | - | 0 |
| sparse-tail | rds | 6 | 6 | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 31 | 0 | 31 |
| sparse-mid | baseline-uniform | 6 | 6 | - | - | - | - | 31 |
| sparse-mid | rds | 6 | 6 | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 31 | 0 | 31 |
| regime-double | baseline-uniform | 6 | 6 | - | - | - | - | 0 |
| regime-double | rds | 6 | 6 | JUDGMENT_REQUIRED | CONSISTENT_BUT_UNCONFIRMED | 0 | 0 | 30 |
| sparse-tail | baseline-uniform | 8 | 8 | - | - | - | - | 0 |
| sparse-tail | rds | 8 | 8 | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 31 | 0 | 31 |
| sparse-mid | baseline-uniform | 8 | 8 | - | - | - | - | 31 |
| sparse-mid | rds | 8 | 8 | **SEARCH_EXHAUSTED -> JUDGMENT_REQUIRED** | **NOT_ESTABLISHED -> CONSISTENT_BUT_UNCONFIRMED** | 0 | **31 -> 0** | **0 -> 32** |
| regime-double | baseline-uniform | 8 | 8 | - | - | - | - | 0 |
| regime-double | rds | 8 | 8 | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 30 | 0 | 30 |
| sparse-tail | baseline-uniform | 12 | 12 | - | - | - | - | 0 |
| sparse-tail | rds | 12 | 12 | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 31 | 0 | 31 |
| sparse-mid | baseline-uniform | 12 | 12 | - | - | - | - | 0 |
| sparse-mid | rds | 12 | **11 -> 12** | **SEARCH_EXHAUSTED -> GOAL_IDENTIFIED** | **NOT_ESTABLISHED -> CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS** | **0 -> 32** | **31 -> 0** | **0 -> 32** |
| regime-double | baseline-uniform | 12 | 12 | - | - | - | - | 0 |
| regime-double | rds | 12 | 12 | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 32 | 0 | 32 |
| sparse-tail | baseline-uniform | 16 | 16 | - | - | - | - | 0 |
| sparse-tail | rds | 16 | 16 | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 31 | 0 | 31 |
| sparse-mid | baseline-uniform | 16 | 16 | - | - | - | - | 31 |
| sparse-mid | rds | 16 | **15 -> 16** | **SEARCH_EXHAUSTED -> GOAL_IDENTIFIED** | **NOT_ESTABLISHED -> CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS** | **0 -> 32** | **31 -> 0** | **0 -> 32** |
| regime-double | baseline-uniform | 16 | 16 | - | - | - | - | 0 |
| regime-double | rds | 16 | 16 | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 32 | 0 | 32 |
| sparse-tail | baseline-uniform | 24 | 24 | - | - | - | - | 0 |
| sparse-tail | rds | 24 | 24 | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 31 | 0 | 31 |
| sparse-mid | baseline-uniform | 24 | 24 | - | - | - | - | 0 |
| sparse-mid | rds | 24 | **19 -> 24** | **SEARCH_EXHAUSTED -> GOAL_IDENTIFIED** | **NOT_ESTABLISHED -> CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS** | **0 -> 32** | **31 -> 0** | **0 -> 32** |
| regime-double | baseline-uniform | 24 | 24 | - | - | - | - | 0 |
| regime-double | rds | 24 | **21 -> 24** | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 32 | 0 | 32 |
| sparse-tail | baseline-uniform | 32 | 32 | - | - | - | - | 1 |
| sparse-tail | rds | 32 | 32 | JUDGMENT_REQUIRED | **CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS -> CONSISTENT_BUT_UNCONFIRMED** | **32 -> 0** | 0 | 32 |
| sparse-mid | baseline-uniform | 32 | 32 | - | - | - | - | 0 |
| sparse-mid | rds | 32 | **19 -> 32** | **SEARCH_EXHAUSTED -> GOAL_IDENTIFIED** | **NOT_ESTABLISHED -> CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS** | **0 -> 32** | **31 -> 0** | **0 -> 32** |
| regime-double | baseline-uniform | 32 | 32 | - | - | - | - | 0 |
| regime-double | rds | 32 | **21 -> 32** | GOAL_IDENTIFIED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 32 | 0 | 32 |

### 4.2 八格差异逐条

1. **`sparse-mid / rds / budget 8`**：`status` `SEARCH_EXHAUSTED`→`JUDGMENT_REQUIRED`；`claim` `NOT_ESTABLISHED`→`CONSISTENT_BUT_UNCONFIRMED`；`best_effort_exact` 31→0；`exact_match` 0→32；`experiments` 8→8（不变）；`claim_exact` 0→0（不变）。
2. **`sparse-mid / rds / budget 12`**：`experiments` 11→12；`status` `SEARCH_EXHAUSTED`→`GOAL_IDENTIFIED`；`claim` `NOT_ESTABLISHED`→`CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`；`claim_exact` 0→32；`best_effort_exact` 31→0；`exact_match` 0→32。
3. **`sparse-mid / rds / budget 16`**：`experiments` 15→16；其余同第 2 条。
4. **`sparse-mid / rds / budget 24`**：`experiments` 19→24；其余同第 2 条。
5. **`sparse-mid / rds / budget 32`**：`experiments` 19→32；其余同第 2 条。
6. **`regime-double / rds / budget 24`**：`experiments` 21→24；其余 5 个字段全部不变。
7. **`regime-double / rds / budget 32`**：`experiments` 21→32；其余 5 个字段全部不变。
8. **`sparse-tail / rds / budget 32`**：`claim` `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`→`CONSISTENT_BUT_UNCONFIRMED`；`claim_exact` 32→0；`status`（两侧均 `JUDGMENT_REQUIRED`）、`best_effort_exact`、`exact_match` 不变。

`claim_exact` 的定义是 `score(expr, law) if (expr and claimed) else 0`（`benchmark/newtonbench/pareto.py:314`），`exact_match` 是 `score(expr, law) if expr else 0`（同文件 `:316`）。因此第 1 格里 `exact_match` 32、`claim_exact` 仍为 0，是因为该格 `claimed=False`（`JUDGMENT_REQUIRED` 未认证），不是表达式没对上。`best_effort_exact` 与前者是不同候选的分数，不代表同一字段的前后版本。

## 5. 判定

隔离对照的结论是：**Stage 2（`639e19f`）本身不改变这 36 格结果**。同 HEAD 把 `_all_declared` 关掉后，`pareto-stage2-nostage2.json` 与 `pareto-stage2.json` 在 36 格全部可比字段和顶层聚合上 **0 处差异**。

单格探针（`sparse-mid` / budget 8，同参数各跑一次）给出了 patch 确实生效的直接证据：

| 口径 | decisions | rivals_rounds | observations | compass_runs | status | claim |
|---|---|---|---|---|---|---|
| 当前代码（skip on） | 9 | 0 | 8 | 8 | JUDGMENT_REQUIRED | CONSISTENT_BUT_UNCONFIRMED |
| 关掉 skip（skip off） | 17 | 8 | 8 | 8 | JUDGMENT_REQUIRED | CONSISTENT_BUT_UNCONFIRMED |

两侧最终 law 都是 `ite((20 == x), 1000, (1 + x))`。即：Stage 2 省掉了 8 个 RIVALS 轮（决定性差异 17→9 步，整批耗时 312 s→235 s），但不改变买到的实验点、结论和 status。

**但 `results/pareto.json` 作为基线已经过期**：它生成于 `addf892`，`git log --oneline addf892..HEAD -- scripts/rds_compass*.py` 显示中间有 16 个内核 commit（`601bc53, 639e19f, 5cb73ff, 5611a7d, 93d2377, 93aaa00, 2693759, 4c820e4, e56924f, 53ed3d1, 4a5cd39, 6bf1efa, 832a11f, 4042d42, 97e133c, ea2edfd`），其中 `601bc53` 只在 `verify()` 的 replay 字典补 `'undecided': replay.get('undecided', 0)`、`5cb73ff` 只给 PROBE/ESCAPE 加决策元数据，均不改分支。所以第 4 节的 8 格差异不能归因于 Stage 2；要定位是哪次内核变更引入的，需要对单个格子（比如 `sparse-mid / rds / budget 12`，约 7 s/次）在 `addf892..601bc53` 之间做 bisect。

一句话：Stage 2 没改变 pareto 的 36 格结果，但旧基线文件已经落后 16 个内核 commit，8 格数值不再可当对照 —— 若要继续用 pareto 作基线，得把基线文件换成这次重跑的结果。

## 6. 限制

每个格子只跑了**一次**，没有 seed 重复。`pareto.py` 不暴露 seed 参数，单次运行无法区分「确定性结果」和「随机波动」；上表所有差异都来自同一次对照跑的逐字段比对，没有做多次重复的置信区间。

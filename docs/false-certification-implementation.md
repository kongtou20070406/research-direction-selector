# 假认证率：写进结果文件 + 加门禁

审计报告 `docs/false-certification-audit.md` 的结论是：RDS 内核层不自证也不计算假认证率，
`results/devset-run.json`、`results/pareto.json`、`results/l2a-*.json` 里都没有
`false_certification` 字段；历史上那 51 次假认证是事后在 PR 评论里人工核算的。
这份文档记录把它变成可测载体的实现。

改动只碰三个文件：`benchmark/newtonbench/devset_run.py`、`benchmark/newtonbench/pareto.py`、
`benchmark/newtonbench/l2a_accept.py`。`scripts/rds_compass.py`、`benchmark/newtonbench/devset.py`、
任何一个 `results/*.json` 都没动（最后一条见第 6 节的自检）。

---

## 1. 定义

每条 run/trial 条目新增三个字段：

```json
"claimed": true,
"false_certification": false,
"certification_check": {"points_checked": 32, "max_abs_error": "0", "tolerance": "0",
                        "tolerance_rel": "0", "seed": 357, "reason": "..."}
```

- **`claimed`**：这一条是否做出了「已识别定律」的主张。devset 侧沿用现有
  `claim_exact` 的口径（`status == GOAL_IDENTIFIED`）；pareto 侧 RDS 臂是
  `status == GOAL_IDENTIFIED` 且 `claim == CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`，
  基线臂只要拟合出直线就算主张（拟合本身就是断言）。
- **`false_certification`**：`claimed == true` 且报告出来的 law 表达式与真值 law 在
  **独立抽样点**上超容差不等价。三态，不是两态：
  - `true`  —— 在至少一个独立点上超容差；
  - `false` —— 至少有一个点可求值、且全部落在容差内；
  - `null`  —— 无法下结论（读不到真值 law、表达式不可求值、样本全部奇异）。
    **`null` 不计入分母**，也绝不冒充「零假认证」。

### 独立抽样点怎么来的

- **devset（`devset_run.py:157 sample_points`）**：每个方程在 `spec['rows']` 给出的
  变量 min/max 框内**均匀重抽** 32 个点，固定 `seed=357`，并**排除所有声明过的 devset 行**。
- 为什么排除整个网格而不只是拟合用点：`devset_run.py` 的 one-off campaign 只落盘
  `task.json`（真值）和 `experiment.py`，**没有保存「本次跑了哪些行」**（见
  `campaign()` 原实现）。拿不到拟合点集合，退而求其次的「排除所有声明行」不但不会
  把拟合点漏进来，比「只排除拟合点」更严格，而且不依赖 campaign 根目录。
  这是口径上的主动加严，代价是它比题面要求更强。
- **pareto（`pareto.py:86 certification`）**：两个臂都只会看 `x0..x5`（六点初始）
  或均匀网格，**从不看完整的 `range(32)`**，所以 `x ∉ {0,1,2,3,4,5}` 恰好就是两个臂都没见过的点。
  在 `[0, 31]` 内均匀抽样并排除 `0..5`，同样固定 `seed=357`、32 个点。

### 怎么比

- 两边都走 `rds_compass_model` 的**同一套精确求值器**（`compile_hypothesis` + `predict`），
  所以误差是精确有理数差，不是浮点残差。devset 侧真值 law 通过
  `devset_run.py:55 truth_ast` 交给 `model.parse` 读入；pareto 侧通过
  `pareto.py:55 _law` 读入。
- 容差从该 run 已有的容差配置读：devset 用
  `spec['tolerances'][spec['truth']['observable']]`（实际值 `0/0`，即精确相等）；
  pareto 的 toy world 没有配置容差，读不到，**明确使用默认 `(0, 0)`**，
  理由与 devset 相同：toy world 是整数域上的精确算术，模型本身也是精确有理数，
  没有浮点噪声需要吸收。容差写进 `certification_check.tolerance` /
  `.tolerance_rel`，接收端的判据是 `|a-b| ≤ tolerance + tolerance_rel*|b|`。
- **求值失败**：定义域外、除零、复数、比较表达式（不是数值 law）—— 单点失败**不算不等价**，
  但会计入 `certification_check.evaluation_failures`，并把原因写进 `reason`。
  若 32 个点**全部**失败，结论是 `null`（`NO_EVALUABLE_POINT`），不是 `false`。
- 残差超过 `MAX_ABS_ERROR = 10^6` 时不存精确值（精确分数可能有 30 位分子分母），
  改写 `max_abs_error: null` + `max_abs_error_note: "> 1000000, exact value not stored"`，
  **不截断、不近似**，避免把估计值当测量值。

## 2. 汇总指标

结果文件顶层新增：

```json
"false_certification_rate": 0.0,
"false_certification_count": 0,
"claimed_count": 12,
"checkable_claimed_count": 12,
"false_certification_rate_rule": "false_certification_count / checkable_claimed_count",
"false_certification_status": "ZERO_FALSE_CERTIFICATIONS"
```

`rate = count / checkable_claimed_count`。`checkable_claimed_count == 0` 时
`rate = null`、`false_certification_status = "NO_CLAIMS"`，**不写 0.0**。

注意分母用的是 `checkable_claimed_count` 而不是 `claimed_count`：当有 claim 但读不到真值时，
那是 `null` 证据，不能算进「零假认证」的分子分母。两个数都写进文件，口径无歧义。
`claimed_count` 仍然保留（题面要求），并等于 `checkable_claimed_count` + 无法核查的 claim 数。

pareto 另外给每个臂单独出 `<arm>` 键（`rds` / `baseline-uniform`），同时给整文件一份汇总。

## 3. 改了哪些函数

### `benchmark/newtonbench/devset_run.py`（+221 行）

| 行号 | 内容 |
|---|---|
| 47-53 | `CHECKS=32`、`DEFAULT_SEED=357`、`MAX_ABS_ERROR`、`BOOLEAN_OPS` 常量 |
| 55 | `truth_ast(law, variables)`：把 devset 的 Python 算术 `law` 交给 `model.parse` 读入。`!=` / `not in` 是 `model.parse` 唯一不认的形式，先改写成 `not(<cond>)`；`low if cond else high` 由 `model.parse` 自己变成 `ite` |
| 157 | `sample_points(spec, count, seed)`：在定义域框内均匀重抽、排除所有声明行，返回 `(problem, points, clashes)`；定义域零宽或抽够时返回结构化原因，不抛异常 |
| 188 | `_evaluate(ast, variables)`：编译 + 挡掉比较表达式（返回固定 token，不是值） |
| 199 | `certification(expr, spec, tolerance, count, seed)`：主比对，返回 `false_certification` + `certification_check` |
| 255 | `certify_run(run, spec)`：给单行填三个字段；方程没有 loader 时 `claimed` 照旧、结论 `null`（`NO_TRUTH_LAW`），不猜 |
| 289 | `summarize(runs)`：顶层汇总；`NO_CLAIMS` 时 `rate = null` |
| 303 | `backfill(document)`：从已存文件重算（不重跑）；崩溃行（没有 `status`）记 `RUN_ERROR` 且不算可核查 |
| 322 | `main()`：`run_one` 结果走 `certify_run`，写 `summarize` + `false_certification_rule` |

### `benchmark/newtonbench/pareto.py`（+246 行）

| 行号 | 内容 |
|---|---|
| 44-53 | `CHECKS`、`DEFAULT_SEED`、`MAX_ABS_ERROR`、`FALSE_CERTIFICATION_RULE` |
| 55 | `_law(expr, temperature)`：`model.parse` 读入；`x != k` 的单点例外改写成 `ite(x == k, odd, body)`（模型精确比较，逐点等价） |
| 71 | `_error(claim, truth_ast, point)`：返回 `(误差, 真值, exploded)`；claim 在奇异点抛错（可去奇点，如 `x*x/x/x` 在 x=1）记 `exploded` 并记 0，不算不等价 |
| 86 | `certification(expr, law, tolerance, count, seed, xs, visible)`：主比对 |
| 159 | `certify_arm(row, law, claimed, exponent)`：给单臂填三个字段 |
| 173 | `summarize(rows)`：分臂 + 整文件汇总 |
| 229 | `load_rows(document)`：兼容「裸列表（schema 1）」与「`{schema:2, rows:[...]}`」两种形态 |
| 234 | `backfill(document)`：从已存文件重算；同时接受 `law` 与 `pareto_l2a.py` 写的 `predict` 字段名 |
| 265 | `main()`：两个臂都走 `certify_arm`，输出 `{schema:2, rows, ...rate...}` |

**输出格式变了**：`pareto.json` 从裸列表变成 `{"schema": 2, "rows": [...], ...}`。
JSON 没法给列表挂属性，所以只能这样。已同步改 `l2a_accept.py` 的 `rows_of()`（第 39 行）两种都认。

### `benchmark/newtonbench/l2a_accept.py`（+61 行）

| 行号 | 内容 |
|---|---|
| 39 | `rows_of(document)`：同时认 `rows`（pareto）和 `runs`（devset），以及裸列表 |
| 46 | `rate_of(document, module)`：文件带 rate 就直接读；老文件走对应模块的 `backfill` 重算 |
| 116-136 | **判据 6**：`false_certification_rate` 必须为 `0` 或 `null`，否则 FAIL，与既有判据同一 `check()` 输出格式 |

判据 6 覆盖 `devset-run.json`、`pareto-l2a.json`、`devset-l2a.json`。
`pareto.json` **显式跳过**并打 `INFO` 行，理由见第 5 节「与审计描述的偏差」第 2 条。

## 4. 实际数字

### `results/devset-run.json`（15 个 run，回填，未重跑）

```
false_certification_rate      0.0
false_certification_count     0
claimed_count                 12
checkable_claimed_count       12
false_certification_status    ZERO_FALSE_CERTIFICATIONS
```

12 个 claimed run = 4 个方程（I.12.1、I.14.3、I.9.18、II.2.42）× 3 个预算（8/16/32），
**每个都是 `points_checked=32`、`evaluation_failures=0`、`max_abs_error=0`（精确 0，不是浮点残差）**。
`feynman-I.6.20a` 的 3 个 run 是 `SEARCH_EXHAUSTED`、`law=null`，判 `NOT_CLAIMED`，
不进分母。合计 802.5 秒的基线文件，回填耗时 < 1 秒。

一条容易误读的口径：磁盘上这份 `devset-run.json` 是**旧 schema**，每行只有
`{equation, B, status, claim, law, counterexamples, claim_exact, experiments, seconds}`，
**没有 `claimed` / `false_certification` 字段**（所以直接读文件筛 `r['claimed']` 得到 0 条）。
`backfill()` 从 `status` + `law` 现场推出 `claimed`、再现场比对，新字段只出现在返回值里，
**没有写回磁盘**。「磁盘文件 0 个 claimed」和「回填结果 12 个 claimed」不矛盾。
要让新字段真正落盘得跑一次 `devset_run.py` 或显式把 `backfill()` 输出写回——本任务不做。

### `results/pareto.json`（36 行，schema 1）

**无法计算。** 该文件每行只有 `exact_match` 计数，**没有保存 law 表达式**，因此
`false_certification` 没有载体。`backfill()` 返回
`false_certification_status = "UNKNOWN_NO_LAW_STORED"`、`rate = null`、`claimed_count = null`，
并在 `certification_note` 里写明原因。这里如实返回 `null`，**没有编造 0.0**。

为了证明 `pareto.py` 的代码路径本身能算，用合成文档跑了同样的逻辑（合成文档，不是结果）：

| world | 臂 | law | false_certification | points | max_abs_error |
|---|---|---|---|---|---|
| sparse-tail | RDS | `x+1` | false | 32 | 0 |
| sparse-mid | RDS | `x+1` | false | 32 | 0 |
| regime-double | RDS | `x+1` | **true** | 32 | 25085642769/846053740 |
| 三个 world | baseline | `x * 2 + 0` | **true** ×3 | 32 | 见 reason |

`sparse-*` 上 `x+1` 被判等价是对的：那两个 world 的例外分别在 x=31 和 x=20，
32 个连续抽样点命中单点的概率约等于 0。`regime-double` 上 `x+1` 必然错（x≥10 时真值是 2x），
判 true 也对。**这恰好暴露了「32 个随机点抓不到单点例外」这个真实的检测盲区**，
见第 5 节第 4 条。

### 负控制（证明这不是空转）

- devset：把 `I.14.3` 的 `((h * m) * g)` 改成 `((h * m) * g * 2)` →
  `rate = 0.25`（3/12）、`false_certification_status = FALSE_CERTIFICATIONS_FOUND`，
  残差 `max_abs_error_note = "> 1000000, exact value not stored"`。
- pareto：把 `regime-double B=12` 的 law 改成 `x * 2` → `pareto-l2a.json` 那行
  `rate=0.6111`、判据 6 FAIL。

## 5. 与审计报告对不上的地方（以代码为准）

1. **§5.1 说「排除拟合用点」，代码里拿不到。** `devset_run.py` 的 `campaign()` 只写
   `task.json` + `experiment.py`，没有记录「本次实验了哪些行」的记录。所以按「排除所有声明行」
   实现——比题面更严格，且不需要 campaign 根目录。这条限制在 devset 上几乎不吃亏：
   声明 `rows` 就是完整笛卡尔积网格（子 agent 独立核对已确认），框内继续抽连续点
   能测到网格之间的行为；换成非满网格的方程表时，口径要重新审。
2. **`pareto.json` 永远不会有这个字段，所以判据 6 跳过它。**
   它是冻结的对照基线，`saturation-protocol.md:36` 明确禁止覆写、
   `probe_cell.py` 也声明不碰它。它每行只有 `exact_match`，没有 law。
   如果照题面「每个结果文件都要有 rate」，这条门禁会**永久红灯**，
   红灯就不再是信号。所以判据 6 只检查会真正重跑、且能携带 law 的三个文件，
   对 `pareto.json` 打 `INFO` 而不是假装 PASS，也不是假装它合规。
3. **`pareto_l2a.py` 存的字段名是 `predict` 不是 `law`。** 题面写的 `law` 字段在
   `devset.py` 里确实存在，但 pareto 的 l2a 结果文件用的是 `predict`。
   `backfill()` 两种都认，否则 `pareto-l2a.json` 会被误判成「没有载体」。
4. **32 个随机点对单点例外是盲的。** `sparse-tail`（x=31）和 `sparse-mid`（x=20）上，
   `x+1` 被判「等价」——严格说这是**漏检**，不是「等价」。这是 32 点均匀抽样的
   固有分辨率，不是实现 bug。要真正封住这个洞，得加「与拟合点的拓扑差异」判据
   （比如按分段线性假设的反例搜索），那是另一个改动，本报告不越界。
   审计 §4.2 讨论的 holdout 破防漏洞，属于同一类：**抽样密度决定检测下限**。
5. **`devset_run.py` 的 `claim_exact` 语义没有改。** 它仍然是「在全量 devset 重放上 0 反例」，
   这跟 `false_certification` 是两条不同口径：前者对 32 点全量重放，后者对框内连续独立抽样。
   两个都保留，报告里不混用。

## 6. 验证

### fail-closed 自检（结果文件挪走）

```
===== fail-closed A 两条基线同时缺失 =====
FAIL  baselines present
FAIL  pareto.json false_certification_rate is 0 or null  file missing
FAIL  devset-run.json false_certification_rate is 0 or null  file missing
FAIL  pareto-l2a.json false_certification_rate is 0 or null  file missing
FAIL  devset-l2a.json false_certification_rate is 0 or null  file missing
OVERALL: FAIL
[exit 1]
```

文件用 `shutil.copy2` 备份、`try/finally` 保证复原；复原后 `git diff --stat -- benchmark/newtonbench/results/`
为空，`git ls-tree HEAD` 的 blob 未变。中途有一次 shell `set -e` 让复原步骤没执行，
当场用 `git checkout --` 恢复到 HEAD（逐字节一致），没有丢失。

### 门禁能 PASS 也能 FAIL

用合成的 `-l2a.json`（跑完即删，没有留在 `results/` 里）：

```
PASS  devset-run.json false_certification_rate is 0 or null  rate=0.0 claimed=12 source=backfilled
PASS  pareto-l2a.json false_certification_rate is 0 or null  rate=0.0 claimed=18 source=backfilled
PASS  devset-l2a.json false_certification_rate is 0 or null  rate=0.0 claimed=12 source=backfilled
INFO  pareto.json false_certification_rate  skipped: frozen schema-1 control, stores no law expression
```

把其中一条 law 改坏 → 判据 6 FAIL（`rate=0.6111`）。判据 6 本身双向可触发。

### 聚焦测试

`ls tests/ | grep -iE 'devset|pareto|l2a'` **没有匹配**。`grep -rln` 命中的 5 个文件
（`test_rds_advisor_cli.py` 等）都是 `pareto_front` 字段，跟 `pareto.py` 无关，不是聚焦测试。

替代验证：`python -m unittest tests.test_import_graph` → **97 tests OK**；
三个模块导入 + 关键函数存在性检查全部通过。

### 没有做的事

- 没有重跑 devset 全量（802.5 秒），没有重跑 pareto（会覆写冻结基线）。
- 没有 `commit`、没有 `push`。

## 7. `git diff --stat`

```
 benchmark/newtonbench/devset_run.py | 221 +++++++++++++++++++++++++++++++-
 benchmark/newtonbench/l2a_accept.py |  61 ++++++++-
 benchmark/newtonbench/pareto.py     | 246 ++++++++++++++++++++++++++++++++++--
 3 files changed, 503 insertions(+), 25 deletions(-)
```

## 8. 下一步可以

- 把 pareto 的 schema-2 结果真正重跑到 `results/pareto-l2a.json`（需另开跑，会覆写预注册基线，
  要在合规的时机做）；
- 补「拓扑差异」判据，封住第 5 节第 4 条的 32 点盲区；
- 给 `pareto.py` / `devset_run.py` 补单测（本仓库 `tests/` 现无对应模块）。

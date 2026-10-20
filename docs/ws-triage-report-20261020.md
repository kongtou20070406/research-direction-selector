# 工作区清理报告 — rds-357 / release/6.0.0-compass

- 日期：2026-10-20
- 仓库：`C:\Users\XU\rds-357`（本机实际路径 `D:\Myfile\work space\RDS\rds-357`）
- 分支：`release/6.0.0-compass`。**基线订正**：任务交办的「HEAD `c873e9d`」是 upstream `origin/release/6.0.0-compass` 的位置（已推送），但本地工作区在 triage 开始时已领先它一个提交——B4 的父提交是 `5e4ff46 docs: revise development outline and archive the NewtonBench negative result`（本地未推送，起始时 `git status -sb` 为 `[ahead 1]`）。两者差异**全在 `docs/`**（`docs/benchmarks/newtonbench-202610.md`、`docs/rds-development-outline.md`、`docs/releases/6.0.0.md`，235 增 65 删），`scripts/` 与 `tests/` 逐字节相同，所以「两条 `RsiTests` 失败是预存在」的结论不受影响；但下面所有「快照 / HEAD 基线」的数字，其准确标签是 **`5e4ff46`**，不是 `c873e9d`。
- 干净对照：`git archive HEAD` 导出到 `D:\Myfile\work space\RDS\.triage-head`（导出时 HEAD = `5e4ff46`，1267 个文件；`scripts/rds_compass.py` sha256 `d62590bdc5fcbef0361980a57b5d0a8f68e9b948c01db74533b1ae0d63d7f6df`）
- 依据：`docs/handoff-20261020.md` §5 / §6

## 0. 预注册的判定规则（跑之前写死，未事后修改）

| 待判事项 | 判定命令 | 通过 = 保留 | 失败 = 回滚 |
|---|---|---|---|
| G1 八个 `scripts/*.py` | `python -B -m unittest discover -s tests -p 'test_import_graph.py'` + `test_rds_owned_advisor.py` + `test_rds_owned_tools.py` | 全绿且 `upward_edges` 下降、无新增 upward pair | 任一红灯 |
| G2 `scripts/rds_compass.py` | `python -B -m unittest discover -s tests -p 'test_rds_compass_drive.py' -k LawTests -v` | 13/13 OK（且与 HEAD 快照一致） | 任一新红 |
| G3 `tools/import_graph.py` | `python -B -m unittest discover -s tests -p 'test_import_graph.py'` | 97/97 OK | 非全绿 |
| 回归归因 | 同一命令在 `.triage-head` 快照上重跑 | HEAD PASS 且工作区 FAIL = 本批引入 | 两侧同 FAIL = 预存在，不计本批 |
| 删除项 | 判定脚本比对结果文件，不靠人眼 | 被更完整结果覆盖 = 保留删除 | 否则恢复 |

反向测试口径：每次改动关掉新逻辑，旧行为必须原样（用 `.triage-head` 快照作为「新逻辑关闭」侧）。

## 1. 九个文件的逐项判定

汇总：**8 个保留、1 个回滚**。G1 八文件保留；**G2（`scripts/rds_compass.py`）判定回滚**——lead 在 `regime-double` 上的实测证明该分支没有杠杆（见下）。

提交状态（2026-10-20 收尾）：B4 → B1 → B2 已本地提交（`9963ae9` / `3b48f85` / `d89b1eb`），每个 commit 只含一件事，message 列明两条已知基线失败并把 CI 标 UNKNOWN。**B5 已按裁定「恢复不删」执行**（见 §4）。**B3 待终裁**：lead 四档实测确认零差异，但 `--max-gap` 诊断未落地前不动 `scripts/rds_compass.py`。**全程未 push、未碰 main、未 force-push。**

**证据标签口径（关于 commit message 里的 `c873e9d`，2026-10-20 lead 裁定）**：两条基线红灯的**实测**跑在 `5e4ff46` 快照上；`c873e9d` 是**传递性结论**——依据是 `5e4ff46` 相对其父提交 `c873e9d` 只改 3 个 markdown（`docs/benchmarks/newtonbench-202610.md` / `docs/rds-development-outline.md` / `docs/releases/6.0.0.md`，+235/−65），`git show --name-only 5e4ff46 | grep -Ev '^(docs/|benchmark/.*\.md$)'` 输出为空，**零代码、零测试**。三个 sha **不改**（lead 裁定：为一行标签精度去动三个 sha 不划算；三个提交均未 push，改历史无副作用，但收益/成本倒挂）。需要更严格口径的读者可顺着这一行自己复验。

### G1 — 打破三条 upward edge（`audit-verify-structure` 批次，8 个文件）

| 文件 | 改动 | 判定 | 证据 |
|---|---|---|---|
| `scripts/rds_reference_values.py` | 新增 L1 常量 `CAPABILITIES` | 保留 | `test_import_graph.py` 97 OK |
| `scripts/rds_capabilities.py` | 删本地 `CAPABILITIES` 定义，改为从 `rds_reference_values` 导入 | 保留 | 同上；CAPABILITIES 变单一定义点 |
| `scripts/rds_advisor_search.py` | `from rds_capabilities import CAPABILITIES` → `from rds_reference_values import CAPABILITIES` | 保留 | 打破 `rds_advisor_search → rds_capabilities` |
| `scripts/rds_output_values.py` | 新增 `MAX_REPORT_BYTES = 32 * 1024 * 1024` | 保留 | `test_rds_owned_advisor.py` 36 OK |
| `scripts/rds_campaign.py` | `from rds_owned_advisor import MAX_REPORT_BYTES` → `from rds_output_values import ...` | 保留 | 同上 |
| `scripts/rds_owned_advisor.py` | 删本地 `_original` / `_read_original` / `MAX_REPORT_BYTES` 与 `import hashlib` / `from pathlib import Path`，改为从 `rds_output_values` 和 `rds_original_read` 导入 | 保留 | `test_rds_owned_advisor.py` 36 tests OK（140s） |
| `scripts/rds_owned_tools.py` | `from rds_owned_advisor import _read_original` → `from rds_original_read import _read_original` | 保留 | `test_rds_owned_tools.py` 16 tests OK（64s） |
| `scripts/rds_tool_workbench.py` | `from rds_owned_advisor import _read_original, _original` → `from rds_original_read import _original, _read_original` | 保留 | 打破 `rds_tool_workbench → rds_owned_advisor` |

**等价性核查（读代码，不是靠测试猜）**：`scripts/rds_original_read.py` 由 `d924ab9` 引入，其 `_original(store, relative)` 与 `_read_original(store, item, *, keep=False, max_bytes=MAX_JSON_BYTES)` 与 `rds_owned_advisor.py` 删掉的版本逐行等价，唯一差异是 `size <= MAX_JSON_BYTES` 改为 `size <= max_bytes`，默认值同为 2 MiB。调用点在 `rds_owned_advisor.py` 的 :272 / :280 / :685 / :739 都仍在。

**唯一失败项与这 8 个文件无关**（见 §3）：`test_import_graph.py` 改前 97 中 1 failure，是**预存在**的 `paid_query` 未登记。

**import 图前后对比**（口径：在各自 root 跑 `python -B tools/import_graph.py --root .`，解析 JSON）。复测时间 2026-10-20 18:16，工作区 `git status --porcelain -uall` 指纹 `5f13f2f9`：

| 指标 | HEAD | 工作区（含 G1+G2+G3） |
|---|---|---|
| modules | 173 | 173 |
| edges | 638 | **642** |
| sites | 894 | 897 |
| upward_edges | 21 | **18** |
| SCC 组数 | 4 | 4 |
| 最大 SCC | 49 | 49 |
| mutual_pairs | 41 | **40** |
| unclassified_edges | 0 | 0 |
| UNKNOWN 层 | `['paid_query']` | `[]` |

打破的 upward pair 恰好 3 条，且**没有新增**：
`rds_advisor_search → rds_capabilities`、`rds_owned_tools → rds_owned_advisor`、`rds_tool_workbench → rds_owned_advisor`。

三点订正（对 `docs/handoff-20261020.md` §3 与 lead 的转述）：

1. 审计报告的「52 模块大环」**证伪**：两侧最大 SCC 都是 **49**，与 handoff 的订正一致。
2. 「只打破了一条依赖环」与「反向边 −3」并不矛盾：3 条 upward 里只有 1 对是双向的（`mutual_pairs` 41→40），SCC 组数与最大 SCC 都没变。
3. lead 给的 `edges 638→639` / `mutual_pairs 41→41` 与本次实测（`638→642` / `41→40`）不符，差异应是测量时点不同；本文数字绑定了上表的 git 指纹，可复现。

### G2 — `scripts/rds_compass.py` 的 `_gap_wide_enough`

改动（`scripts/rds_compass.py:420-432`）：

```python
def _gap_wide_enough(state):
    plan = state['plan']
    if len(plan['inputs']) != 1:
        return False
    name = next(iter(plan['inputs']))
    seen = sorted(...)
    gaps = [right - left for left, right in zip(seen, seen[1:])]
    if not gaps:
        return False
    return max(gaps) > max(2, len(plan['candidates']) // 10)
```

HEAD 版本是 `return bool(gaps) and max(gaps) > min(gaps)`。调用点唯一：`scripts/rds_compass.py:521-522`，在 `spending` 条件链上控制 `SPEND_BUDGET_ON_REFUTATION`。

**判定：回滚（机械判定，不是价值判断）** —— 依据 `benchmark/newtonbench/saturation-protocol.md` §2.1 的**预注册门槛**：`max(Exp@16, Exp@24, Exp@32) − Exp@12 ≤ 2`。实测 `max(16, 21, 21) − 12 = 9`，**门槛差 7**；两侧四档 `experiments`（12/16/21/21）与 `actions` / `predict` / `status` / `claim_exact` 五项全部逐字相同。按玩法第 4 条「判定脚本通过 + 安全项不恶化 + 一条命令可复现 = 提交；任一不满足 = 回滚」，它三条一条都没占。**回滚理由是「未达预注册门槛且零行为差异」；不能写成「没有价值收益」——那是主观判断，按玩法不作回滚依据。**

**执行**：`git checkout -- scripts/rds_compass.py`（不产生 commit）。回滚后 `python -B -m unittest discover -s tests -p 'test_rds_compass_drive.py' -k LawTests -v` → `Ran 13 tests in 119.423s — OK`；`git diff --stat -- scripts/rds_compass.py` 为空；`git status --short` 里 ` M scripts/rds_compass.py` 消失。

以下为本判定之前的过程证据，保留作记录。依据：

**四档实测（HEAD 快照，探针单格跑，`benchmark/newtonbench/probe_cell.py`）**：

| B | exp | actions | claim_exact | 停因 |
|---|---|---|---|---|
| 12 | 12 | RIVALS 5, CONFIRM 4, EXPERIMENT 1, PROBE 7, ESCAPE 1 | 32 | 预算耗尽（exp == cap） |
| 16 | 16 | RIVALS 5, CONFIRM 4, EXPERIMENT 1, PROBE 11, ESCAPE 1 | 32 | 预算耗尽 |
| 24 | 21 | RIVALS 7, CONFIRM 3, EXPERIMENT 1, PROBE 15, ESCAPE 1, HOLDOUT 2 | 32 | 覆盖满足 |
| 32 | 21 | 与 B=24 逐字相同 | 32 | 覆盖满足 |

`PROBE` 随预算线性涨 7 → 11 → 15 → 15，而 `claim_exact` 恒 32。

**工作区（G2）版四档 exp 与 HEAD 逐字相同（12 / 16 / 21 / 21）**，`actions` / `predict` / `status` / `claim_exact` 也全部一致——所以门槛算式 `max(16,21,21) − 12 = 9` 在两侧同值，零差异不是只发生在 B=24 一档。停止链读作 `scripts/rds_compass.py:503-543`：`CONFIRM` → `HOLDOUT` → `PROBE`（COVERAGE_GAP，`:536-540`）→ `if rows: return VERIFY`。B=24/32 停在 21 不是「预算到了」，是 `_boundary_probe(state, min_gap=2)` 返回 None——0..31 上相邻距离 ≤2 的覆盖约需 16 个点，填满就买不动了。

=> 「自我帕累托支配」的确切机制是：**`max_gap=2` 的覆盖要求**逼工具在 B≥21 时买到 21，而 B=12/16 是预算先耗尽；两条路径都不看「leader 是不是已经不需要再测」。`_gap_wide_enough` 守的那 3 个 CONFIRM 在链里排在 PROBE 前面，杠杆本来就小——四档印证。

B=24 单档对照（HEAD 版 vs 工作区版，**输出逐字相同**）：

lead 在 `regime-double` B=24 上跑 HEAD 版与工作区版，**输出逐字相同**：

```
experiments: 21
actions: {"RIVALS":7,"CONFIRM":3,"EXPERIMENT":1,"PROBE":15,"ESCAPE":1,"HOLDOUT":2}
predict: "ite((x < 10), (1 + x), (x + x))"
```

两侧都复现冻结基线 `results/pareto.json` 的 21，所以探针口径可信（单次 35 秒）。拆开算：`21 = PROBE 15 + CONFIRM 3 + EXPERIMENT 1 + HOLDOUT 2`。`_gap_wide_enough` 只守 `spending` 分支的 CONFIRM，而那 3 个 CONFIRM 里 2 个是最小确认要求（`confirmations: 2`），只有 1 个是 `spend_budget` 买的——**G2 的杠杆面是 21 分之 1，实测连这 1 个都没动。** 成本大头 71%（15/21）在 PROBE，来自 `max_gap=2` 的 COVERAGE_GAP 规则（`scripts/rds_compass.py` 里 `probe = _boundary_probe(state, min_gap=max_gap)` 那段），G2 碰不到；这与 `docs/audit-verify-cost.md:78` 的归因一致。

=> **回滚理由不是「LawTests 分辨不出」，而是「真实场景里该分支只占总成本的 1/21，实测零差异」。** 滚回 HEAD 版本不损失任何已验证的行为。

以下为判定过程中的辅助证据：

```
命令: python -B -m unittest discover -s tests -p 'test_rds_compass_drive.py' -v
工作区: Ran 31 tests in 1451.492s — FAILED (failures=1, errors=1)
        其中 LawTests 13/13 ok
快照:   python -B -m unittest discover -s tests -p 'test_rds_compass_drive.py' -k LawTests -v
        Ran 13 tests in 114.656s — OK
```

新逻辑与旧逻辑覆盖的行为集合完全一致，且失败集合两侧逐字相同（§3），G2 没有引入新红。

**裁定权不在这个测试模块。** `benchmark/newtonbench/saturation-protocol.md`（lead 于 10-20 预注册，83 行）把 G2 的主场景定为 **`regime-double`**（`benchmark/newtonbench/pareto.py` + 输出 `results/pareto-satstop.json`），主指标是 RDS 臂的实际实验数 `Exp`，对照 HEAD `c873e9d` 的 B=12→12 / B=16→16 / B=24→21 / B=32→21，门槛 `max(Exp@16,24,32) − Exp@12 ≤ 2`。`LawTests` 全绿只是协议 §2.3 的一节回归要求，**不是 G2 有效性的判据**。

**`regime-double` 上门槛确实不是 2。** `tests/compass_fixture.py:51` 的 `law_campaign(root, law, xs, ...)` 把 `candidates` 按 `xs` 逐点展开（候选数 = `len(xs)`）；`benchmark/newtonbench/pareto.py:66` 三个 world 都传 `range(32)`，故候选数 = **32**，`len(plan['candidates']) // 10 = 3`，门槛 `max(2, 3)` = **3**。现成产物对得上：`benchmark/newtonbench/results/sparse-campaign/compass.json`（同一 `law_campaign` 生成的 `rds-compass-declaration-v1`）的 `candidates` 恰为 32 项，`inputs.x = {"min": 0, "max": 31, "integer": true}`。所以 `max(2, ...)` 里的 2 在两边都是死代码，但 `//10` 那一项在 `regime-double` 上是活的——**LawTests 测不出差异是样本特征（候选数 9/5/9），不是 G2 的性质**。

（旁注：`scripts/rds_compass.py:417` 的 `_cap(plan)` 是 `plan['budget'].get('max_experiments') or len(plan['candidates'])`；`pareto.py` 传了 `max_experiments=budget` 所以 `_cap` = B，别拿 `plan_cap=12` 去反推候选数。）

**但必须记录两条保留意见，别把它写成「修好了饱和早停」：**

1. **在现有测试上是 no-op。** 在快照里 monkeypatch `rds_compass._gap_wide_enough`，对同一 state 同时计算新旧判据（脚本 `.triage-head/_gap_probe.py`，日志 `_gap_probe.log`）：
   ```
   law tests selected: 13   tests run: 13   failures: 0   errors: 0
   _gap_wide_enough calls: 13
   old != new: 0        old True: 13    new True: 13
     gaps=(4,2,2) candidates=9        old=True new=True
     gaps=(9,7)   candidates=5        old=True new=True
     gaps=(3,1,1,1,2) candidates=9    old=True new=True
   ```
   13 次调用、新旧判定 13/13 完全相同，现有 LawTests 里没有一条用例能区分这两个判据。

2. **它不是单向收紧，且 `max(2, ...)` 是死代码。** `_boundary_probe(state, min_gap=2)`（`scripts/rds_compass.py:390-412`）在 :521 已经保证 `max(gaps) > 2`，所以 `max(2, len(candidates)//10)` 里的 2 永远不构成约束——上表 13 次调用中 `len(candidates)//10` 全是 0。语义上，旧判据要求「间隔分布不均匀」，新判据是绝对宽度门槛：当 gaps 全相等（均匀网格）或只有单个 gap 时，旧版返回 `False`、新版可能返回 `True`，**这些场景新版比旧版更愿意买点**。真正的收紧只发生在 `len(candidates) >= 10` 且 `max(gaps) < len(candidates)//10` 时，现有测试一条都没走到。

   => 这个改动的效果**没有被任何测试证明**。它无害（不阻断提交），但要宣称它改善了饱和早停，得先补一个 N≥10 的密集域用例。

### G3 — `tools/import_graph.py` 补 `paid_query` 登记

见 §3。

## 2. 八个 upward-edge 改动与唯一失败项的关系

`test_import_graph.py` 在改动前的工作区：97 tests，1 failure。

```
FAIL: test_real_repository_is_covered (test_import_graph.test_import_graph.ImportGraphTests.test_real_repository_is_covered)
  File "tests/test_import_graph.py", line 983, in test_real_repository_is_covered
    self.assertFalse([n for n, m in report["modules"].items() if m["layer"] == "UNKNOWN"])
AssertionError: ['paid_query'] is not false
```

归因（同一命令在快照上重跑）：

```
cd .triage-head
python -B -m unittest discover -s tests -p 'test_import_graph.py' -k test_real_repository_is_covered -v
Ran 1 test in 3.736s — FAILED (failures=1)
  AssertionError: ['paid_query'] is not false
```

=> **与 G1 的 8 个文件无关**，是预存在缺陷（handoff §6 已记录：`cf8964b` 上就是 96/97）。G1 的验收命令必须在 G3 之后跑，否则看到的红灯是 `paid_query` 的账。

## 3. `paid_query` 的修法与理由

**修法**：在 `tools/import_graph.py` 的 `LAYERS` 登记表里补 `paid_query = "L4"`（与 `rds_compass_cli` 同层），而不是把 `scripts/paid_query.py` 排除。

**理由**：

1. **仓库既有约定是登记，不是排除。** `tools/import_graph.py:513` 的 `inventory()` 只扫 `(root/"scripts").rglob("*.py")`——`scripts/` 下每个 `.py` 都必须有层，`benchmark/` 本来就扫不到，没有「跟 benchmark 一起排除」这条路。
2. `CLAUDE.md` 写明：`tools/import_graph.py must classify every new scripts/rds_*.py module`。
3. `test_import_graph.py:983` 断言的就是「不存在 UNKNOWN 层模块」，排除等于绕过断言，属于「为了让测试过而放宽断言」，本次明令禁止。
4. `paid_query` 是 CLI 入口（付费查询驱动的入口脚本），与 `rds_compass_cli` 同属 L4，不是内部实现模块。

**验证**（改动后全绿）：

```
cd rds-357
python -B -m unittest discover -s tests -p 'test_import_graph.py'
Ran 97 tests — OK
```

反向测试（关掉这一行 = 快照状态）：`test_real_repository_is_covered` 1 test FAILED，`AssertionError: ['paid_query'] is not false`，正是这条断言在管。改前 96 OK + 1 failure、改后 97 OK，计数与断言双向对上。

## 4. `benchmark/newtonbench/results/smoke-devset.json` 的删除判定

**最终裁定（lead / k3，2026-10-20）：恢复，不删。** 已执行 `git checkout -- benchmark/newtonbench/results/smoke-devset.json`，文件回到 HEAD 版本（**46 行 / 1024 字节**），`git status` 里那行 ` D` 消失。裁定理由：删它的唯一收益是「git status 干净」，而为一个零引用的小文件付这个收益等于零；反过来 `seconds` / `campaign_root` 确实不可从 `devset-run.json` 导出，k3 那条「不可重建的运行元数据也算证据」的顾虑无法用数据驳倒。**删了没有正收益、留了没有负成本 → 走保守侧。**

以下为本报告原先的分析（结论是「可删」，与最终裁定取向不同，保留作记录）：

**判定：有意删除，保留删除状态。** 依据是结果文件本身，不是推测：

- 被删文件（HEAD 版本）：3 条 run，全是 `feynman-I.12.1` @ B=8/16/32，`claim_exact` 全 true，`experiments` 全 4，`elapsed_seconds` 15.5。
- 取代它的 `benchmark/newtonbench/results/devset-run.json`：15 条 run（5 个 Feynman 方程 × B=8/16/32），**包含 smoke 的全部 3 个 key**；`schema` / `budgets` / `claim_exact_rule` 三个字段完全相同。
- 逐字段差异只有耗时：`seconds` 4.7/5.2/5.6 → 4.9/4.6/4.1。结论性字段（status / claim_exact / experiments / law）无差异。
- 全仓检索 `smoke-devset` 无任何代码或文档引用。
- 提交历史：`e55f3e3` 引入 smoke，`28741fa`（"test(benchmark): five Feynman equations at B=8/16/32"）引入 devset-run.json，后者更晚且更完整。

=> 信息无损（只丢 3 个重复数据点的耗时字段），保留删除。

**按 k3 第 2 轮的要求补「独有信息」核查**（判定脚本读结果文件自动比对，不靠人眼）：

| 维度 | 被删文件 | `devset-run.json` | 是否独有 |
|---|---|---|---|
| `schema` | 1 | 1 | 否 |
| `budgets` | `[8, 16, 32]` | `[8, 16, 32]` | 否 |
| `claim_exact_rule` | 有 | 逐字相同 | 否 |
| 内容哈希 | 两层字段均无 | 无 | 否 |
| 生成命令（`command`/`argv`） | 不存 | 不存 | 否 |
| 结论性字段（`status` / `claim_exact` / `experiments` / `law`） | 3 条 run | 全部覆盖 | 否 |
| `campaign_root` | `C:\Users\XU\AppData\Local\Temp\devset-run-3plu_hor` | `...\devset-run-f_0jl6l7` | **是**（临时目录名，证明两次是独立运行） |
| `elapsed_seconds` | 15.5 | 802.5 | **是**（运行环境元数据） |
| 每 run 的 `seconds` | 4.7 / 5.2 / 5.6 | 4.9 / 4.6 / 4.1 | **是**（同上） |

**引用检索（全仓）**：

- `smoke-devset` 在 `*.py` / `*.md` / `*.yml` / `*.txt` 上 **0 命中**。全仓 `-i smoke` 的 23 条命中全是别的 smoke（`benchmark/newtonbench/pysr_smoke.md`、`skill_ab_smoke.py`、`benchmark/advisor-frontier/run.py:177` 的 leakage smoke check），**没有一条指向 `results/smoke-devset.json`**。
- 反过来 `devset-run` 的引用链完整：`benchmark/newtonbench/devset-run.md:4` → `[results/devset-run.json](results/devset-run.json)`；`benchmark/newtonbench/devset_run.py:144` → 默认输出路径；`docs/benchmarks/newtonbench-202610.md:89`；`docs/handoff-20261020.md:38`（devset 表那一段）。**无一处提 smoke。**

**可重建性**：两个 `campaign_root` 目录**都还在磁盘上**（`C:\Users\XU\AppData\Local\Temp\devset-run-3plu_hor\feynman-I.12.1` 与 `...\devset-run-f_0jl6l7` 下的五个方程子目录），但两份结果**都不含命令、输入配置或哈希**——重跑只能靠运行脚本的代码默认值，不能靠结果文件自证。

=> **本报告建议：可删。** 三条理由：(1) 结论性信息零独有，3 个 key 全被覆盖、`schema`/`budgets`/`claim_exact_rule` 逐字相同、结论字段无差异；(2) 引用链只认 `devset-run.json`，删了不产生悬空链接；(3) 那三个独有字段不含任何「怎么跑出来的」信息——无命令、无配置、无哈希，**留着也复现不了任何东西**。

**限定（对手方理由也在）**：`seconds` 与 `campaign_root` 确实是真独有、且不能从 `devset-run.json` 导出的字段。若 k3 认为「不可重建的运行元数据也算证据」，则应保留。**这个取向我给不出压倒性理由，最终由 lead / k3 拍。**（最终裁定即取此侧，见本节开头。）

**旁出的发现（lead 记录进 outline，不属本报告范围，但比「删不删」更重要）**：两个 `campaign_root` 都指向 `C:\Users\XU\AppData\Local\Temp\`——benchmark 结果文件把账本位置存成**临时目录路径**，`Temp` 一清复现链就断。这是「结果文件不内联配置/哈希」那个问题的更严重一面：不只是「不能自证怎么跑的」，而是「指向的账本本身可能已经不存在了」。

## 5. 清理清单

| 项 | 处置 | 依据 |
|---|---|---|
| `benchmark/newtonbench/results/.dsh-ops-update-5566232a...lock` | 删除（未跟踪，不进 commit） | 0 字节；`git check-ignore` 返回 1（未被忽略）；DSH 工具遗留锁，非仓库产物；时间戳 17:26:41 |
| `tools/.dsh-ops-update-9b5d730f...lock` | 删除（同上） | 同上；时间戳 17:53:48，由本次 triage 会话的 ops 文件操作产生 |
| `__pycache__/`、`*.pyc` | 不干预 | `.gitignore` 已含 `__pycache__/` 与 `*.py[cod]`；`git status --porcelain -uall` 中不出现 |
| `benchmark/newtonbench/saturation-protocol.md` | **不入本批次**（见 §7） | lead 声明为他的写入范围（G2 的预注册协议），不是并发第三方 |
| `benchmark/newtonbench/.dsh-ops-update-3e56c816...lock` | 收尾统一删除 | 同类的 DSH 遗留锁；ops 工具每动一次文件就在**目标目录**留一个，边删边长，故留到最后一次清 |
| `docs/.dsh-ops-update-8a19f1fb...lock` | 收尾统一删除 | 本次写报告产生的 DSH 遗留锁 |
| `benchmark/newtonbench/probe_cell.py` | **来路待确认，未动** | 本轮 triage 期间出现的未跟踪文件，不在 lead 声明的写入范围，也不在交接清单 11 项内；已报 lead（见 §7） |
| `_lead_graph_head.json` / `_lead_graph_ws.json`（仓库根） | **不入本批次** | lead 的 import 图对比输出（260912 / 260579 字节），不在其声明的 `docs/` 范围内；已报 lead 处置 |

## 6. 分批方案（每批一件事，含验收命令）

| 批 | 文件 | 验收命令 | 预期 |
|---|---|---|---|
| B4（先做） | `tools/import_graph.py` | `python -B -m unittest discover -s tests -p 'test_import_graph.py'` | 97 OK |
| B1 | `rds_reference_values.py`、`rds_capabilities.py`、`rds_advisor_search.py`、`rds_output_values.py`、`rds_campaign.py`、`rds_owned_advisor.py` | 同上 + `python -B tools/import_graph.py` | 97 OK；upward 21→18 |
| B2 | `rds_owned_advisor.py`、`rds_owned_tools.py`、`rds_tool_workbench.py` | `test_rds_owned_advisor.py` / `test_rds_owned_tools.py` | 36 OK / 16 OK |
| B3 | `rds_compass.py` | `git checkout -- scripts/rds_compass.py` + `-k LawTests` | **已回滚**（未达预注册门槛 9 vs ≤2，零行为差异；见 §1 G2） |
| B5 | `benchmark/newtonbench/results/smoke-devset.json` | `git checkout -- <该文件>` | **恢复，不删**（见 §4 最终裁定） |
| 不入库 | 两个 `.dsh-ops-update-*.lock` | `git status --short` 不再出现 | — |

- **B4 必须排在 B1 之前**：否则 B1 的验收会看到预存在的 `paid_query` 红灯（96/97），分不清责任。
- **与 G1 无关、不得混进 G1 的**：`rds_compass.py`（B3）、`tools/import_graph.py`（B4）。G1 的 8 个文件本身没有夹带，全部服务于打破那 3 条 upward edge。
- B1/B2 是否拆分取决于 `git add -p`：`rds_owned_advisor.py` 同时属于两组（常量下沉 + reader 换源），要拆就得部分暂存。

**实际执行结果**（按 k3 的「先固定基线 → 独立提交 B4 → 再 B1 → 再 B2」纪律）：

| 批 | commit | 文件 | 提交前验收 | 反向测试 |
|---|---|---|---|---|
| B4 | `9963ae9` | `tools/import_graph.py`（1 个） | `test_import_graph.py` 97 tests **OK**（改前 97 / 1 failure） | 快照上不补这一行 → 断言红；补上 → counts 逐项不变（638/173/894/1/21），UNKNOWN 由 `['paid_query']` 变 `[]` |
| B1 | `3b48f85` | `rds_reference_values.py`、`rds_capabilities.py`、`rds_advisor_search.py`、`rds_output_values.py`、`rds_campaign.py`（5 个） | `test_rds_capabilities.py` 4 OK、`test_rds_advisor_search.py` 22 OK、`test_import_graph.py` 97 OK | 快照 vs 工作区：`CAPABILITIES`、`MAX_REPORT_BYTES` 33554432、`MAX_JSON_BYTES` 2097152 **逐值相同**；新增的只是 L1 对象身份（`l1_has_capabilities` False→True、`l1_has_max_report_bytes` False→True、`capabilities_from_l1` False→True） |
| B2 | `d89b1eb` | `rds_owned_advisor.py`、`rds_owned_tools.py`、`rds_tool_workbench.py`（3 个） | `test_rds_owned_advisor.py` 36 OK、`test_rds_owned_tools.py` 16 OK、`test_import_graph.py` 97 OK（在 B1 之上重跑确认） | `rds_owned_advisor._read_original is rds_original_read._read_original`、`_original is _original`、`MAX_JSON_BYTES` 两侧 2097152 |
| B3 | **已回滚**（无 commit） | `scripts/rds_compass.py` → `git checkout --` 恢复 HEAD 版 | `-k LawTests` 13 tests / 119.423s **OK**；`git diff --stat` 为空 | 预注册门槛 `max(Exp@16,24,32) − Exp@12 ≤ 2`，实测 **9**（差 7）；四档两侧 `exp`/`actions`/`predict`/`status`/`claim_exact` 逐字相同 |
| B5 | **已执行：恢复，不删** | `benchmark/newtonbench/results/smoke-devset.json` → `git checkout --` | `git status --short` 里 ` D` 消失；文件回到 46 行 / 1024 字节 | 见 §4 最终裁定（删无正收益、留有零成本 → 保守侧） |

`rds_owned_advisor.py` 原先按「一组」估的包含关系，实际**按改动语义拆开了**：常量下沉那一行进 B1，reader 换源的三行进 B2，用 `git add` 显式列文件实现，未用 `git add -p`。三次提交的 `git diff --cached --name-only` 都核对过只含名单内路径，**没有一次用过 `git add .`**。

**快照标签复核（曾疑为错字，已澄清）**：三个 commit message 的末段都写「reproduce verbatim on the clean **c873e9d** snapshot」。**这不是错字**——`c873e9d` 是 `5e4ff46` 的父提交，而 `5e4ff46` 只改 3 个 markdown（`docs/benchmarks/newtonbench-202610.md` / `docs/rds-development-outline.md` / `docs/releases/6.0.0.md`，+235/−65，**零代码零测试**；`git show --name-only 5e4ff46 | grep -Ev '^(docs/|benchmark/.*\.md$)'` 输出为空），所以「预存在」的断言由这个纯文档 diff 传递成立。**唯一的精度问题**：本报告的实测跑在 `5e4ff46` 快照上，`c873e9d` 是传递性结论；若要把它钉成实测点，三条一起 rebase 改这一个词，hash 会变，等 lead 定夺。

## 7. 未能判定 / 明确标记 UNKNOWN

1. **UNKNOWN — `RsiTests` 是否在 CI 上也红。** 本机（Windows）上两条稳稳红，但 `.github/workflows/test.yml:44` 有全量 `unittest discover`（ubuntu+windows × py3.11/3.13）跑一个已推送的 HEAD，两者不能同时为真，除非平台相关或 CI 红灯被容忍。我没有跑 CI、也没在 Linux 上跑，不做推断。
2. **部分关闭 — `_gap_wide_enough` 新阈值在 `regime-double` 密集域上的实际效果为零差异**（四档 B=12/16/24/32，见 §1 G2）。**仍 UNKNOWN**：(a) 换其他域（尤其是 N≥10 的稀疏不均匀网格）会怎样——「哪些场景放宽、哪些收紧」目前只有读代码的方向性判断，无实测；(b) `--max-gap 0/2/8` 诊断结果（子 agent 在跑），用于分离 COVERAGE_GAP 规则单独拿走多少成本。
3. **未跑（不是无法跑，是超出「1–2 个聚焦模块」的约束）**：`rds_advisor_search` / `rds_campaign` / `rds_capabilities` / `rds_output_values` / `rds_reference_values` / `rds_tool_workbench` 的行为级测试模块。这些文件的改动是「导入源切换 + 常量下沉」，覆盖证据是：`test_import_graph.py` 97 OK、九个模块导入自检通过、`CAPABILITIES` / `MAX_REPORT_BYTES` / `MAX_JSON_BYTES` / `_read_original` 的 identity 与 HEAD 一致、以及 `test_rds_owned_advisor.py` / `test_rds_owned_tools.py` 的行为测试。**没有跑 = 没有跑**，不写成「应该没问题」。
4. **已裁定并执行 — `_gap_wide_enough` 四档零差异，工作区版已回滚**（`scripts/rds_compass.py` 回到 HEAD 版，`-k LawTests` 13/13 OK，耗时 119.423s）。B=24/32 两侧输出逐字相同，B=12/16 是预算先耗尽、根本没走到验证阶段。**仍 UNKNOWN**：`--max-gap 0/2/8` 诊断的数字（lead 已不等它——判据换成「未达预注册门槛」，与诊断结果无关）。
5. **已澄清 — `benchmark/newtonbench/probe_cell.py` 是 lead 写的单格探针**（为协议 §2.1 准备，避免覆盖冻结对照 `results/pareto.json`）。**保留、不删、不入本批次提交**；§1 G2 的四档数据就是它跑出来的。
6. **已澄清 — 三个 commit message 里的快照标签 `c873e9d` 成立，不是错字**（详见 §6 末段的复核）。实测跑在 `5e4ff46` 快照上，`c873e9d` 是传递性结论；要钉成实测点就得 rebase 改写历史，等 lead 定夺。
7. **并发写入处理**：`benchmark/newtonbench/saturation-protocol.md`（lead 声明为他的）、`_lead_graph_head.json` / `_lead_graph_ws.json`（lead 的对比输出）、`probe_cell.py`（来路待确认）都不在本次提交范围内。三次提交都只 `git add` 名单内路径，**从未用过 `git add .`**。

## 8. 最终状态

**状态（最终，已回填）**：B4 + B1 + B2 已提交；**B5 已执行「恢复，不删」**；**B3 已执行回滚**。任务书那 11 项全部处置完毕（9 个 `scripts/*.py` 里 8 个进 B1/B2、`rds_compass.py` 回滚；`tools/import_graph.py` 进 B4；`smoke-devset.json` 恢复）。

lead 随后把收尾内容入库：`62559f5`（`saturation-protocol.md` + `probe_cell.py`）、`d202124`（**本报告** + `satstop-root-cause.md` + `satstop-probe-report.md`）、`eec22b1`（outline 口径更正）。下方的 `git log` / `git status` 已按最终态更新——**本报告即以 `d202124` 里的版本为基线**。

```
$ git log --oneline -9
eec22b1 docs(outline): correct the saturation attribution and the P0 acceptance rule
d202124 docs: record the saturation root cause and the workspace triage evidence
62559f5 test(compass): pre-register the saturation-stop protocol and its single-cell probe
d89b1eb refactor(kernel): point the confirmation readers at rds_original_read
3b48f85 refactor(kernel): sink CAPABILITIES and MAX_REPORT_BYTES into the L1 constant modules
9963ae9 fix(import-graph): classify paid_query so the layer inventory stays total
5e4ff46 docs: revise development outline and archive the NewtonBench negative result
c873e9d docs: update the handoff with the finished devset table
28741fa test(benchmark): five Feynman equations at B=8/16/32

$ git status --short          # 本报告回填提交之后
（空）

$ git status --short          # 收尾前（lock 残留未删时）
?? benchmark/newtonbench/.dsh-ops-update-3e56c816....lock
?? docs/.dsh-ops-update-2eaab6d4....lock
?? docs/.dsh-ops-update-452c2722....lock
?? docs/.dsh-ops-update-72832a6e....lock
?? docs/.dsh-ops-update-8a19f1fb....lock
```

**这五个 lock 的性质：UNKNOWN，但确定不是仓库产物。** 事实：全部 0 字节，时间戳为 `18:21:59`（`docs/8a19f1fb`，与本报告首次落盘同刻）、`18:27:55`（`benchmark/newtonbench/3e56c816`）、`18:43:08` / `18:44:14` / `18:45:21`（`docs/` 下三个，与 lead 写 `satstop-*` 文档同刻）。**被证伪的推断**：曾以为是「DSH 文件工具每次写操作留一个」，但本次 §8 回填共写入 2 次（含 1 次 `EDIT_CONFLICT` 失败）**未新增任何 lock**，推断作废；真实生成条件未查明，标 UNKNOWN。

不影响判断的部分：它们 0 字节、不进任何 commit、删除后不影响任何测试或导入，`git status` 也随之干净。本次收尾已把五个全部删除；更早两个（`benchmark/newtonbench/results/` 与 `tools/` 下）已在 §5 处置，`_lead_graph_head.json` / `_lead_graph_ws.json` 已由 lead 删除。

`git status -sb` 首行：`## release/6.0.0-compass...origin/release/6.0.0-compass [ahead 7]`（`5e4ff46` + 本次 3 个提交 + lead 的 `62559f5` / `d202124` / `eec22b1`，**全部未推送**）。

B5 恢复与 B3 回滚都已执行，所以 tracked 侧**除了本报告自身的这次 §8 回填之外没有任何改动**——九个改动文件的处置全部落地。

未提交前基线（2026-10-20 18:16，指纹 `5f13f2f9`）：

```
 D benchmark/newtonbench/results/smoke-devset.json
 M scripts/rds_advisor_search.py
 M scripts/rds_campaign.py
 M scripts/rds_capabilities.py
 M scripts/rds_compass.py
 M scripts/rds_output_values.py
 M scripts/rds_owned_advisor.py
 M scripts/rds_owned_tools.py
 M scripts/rds_reference_values.py
 M scripts/rds_tool_workbench.py
 M tools/import_graph.py
?? benchmark/newtonbench/results/.dsh-ops-update-5566232a...lock
?? benchmark/newtonbench/saturation-protocol.md
?? tools/.dsh-ops-update-9b5d730f...lock
```

## 9. 证据文件索引

| 文件 | 内容 |
|---|---|
| `_triage_rsi_fail.txt` | `RsiTests` 两侧完整 traceback 原文 + 命令 + 计数 |
| `rds-357/_triage_compass_test.log` | 工作区 `test_rds_compass_drive.py` 全量输出（31 tests / 1451.492s） |
| `.triage-head/_head_rsi_test.log` | 快照 `-k RsiTests` 输出（2 tests / 1204.848s） |
| `.triage-head/_head_law_tests.log` | 快照 `-k LawTests` 输出（13 tests / 114.656s / OK） |
| `.triage-head/_gap_probe.log` | 新旧判据 instrument（13 次调用 / old != new: 0） |
| `.triage-head/_head_paidquery_off.log` | 快照未被覆盖的单条断言原文 |
| `_triage_graphcmp.py` | HEAD vs 工作区 import 图对比脚本（§1 表格的复现入口） |
| `.triage-head/_gap_probe.py` | 阈值 instrument 脚本 |
| `_triage_constants.py` | 反向测试：快照 vs 工作区的常量值、L1 对象身份、reader 模块归属（运行时探针 + 静态读导入行） |
| `_msg_b1.txt` / `_msg_b2.txt` | B1 / B2 的 commit message 原文（`git commit -F` 用） |
| `.triage-head/` | `5e4ff46` 的干净快照（1267 个文件），所有「关闭新逻辑」侧的对照 |

复现入口（一条命令）：`cd "D:\Myfile\work space\RDS" && python -B _triage_graphcmp.py`

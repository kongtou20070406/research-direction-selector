# 结构性审计核实：四代子系统与依赖环

核对对象：`docs/rds-development-outline.md:49-50`（外部审计原文，已随 `cf8964b` 收录）。

代码版本：`release/6.0.0-compass`。审计声明基准 HEAD `cf8964b`；核对时主工作区已推进到 `8f08c95`，但该提交只新增 `docs/real-research-benchmarks.md`，`scripts/` 与 `cf8964b` 逐字节相同。行数、行号均按 `cf8964b` 口径。

工具：`tools/import_graph.py`（`cf8964b` 版本），Python 3.13.5，Windows。

并发说明：核对期间主工作区有其他 agent 的未提交改动（`scripts/rds_compass.py`、`scripts/rds_compass_declaration.py`，非本任务），HEAD 也从 `cf8964b` 推进到 `97e133c` 及以后。本文所有行号、行数、图统计都用 `git show cf8964b:<path>` 与 `git worktree` 导出的干净快照复核，不依赖工作区当前状态；§5 的改动效果也在 `cf8964b` 快照上加改动复算过。

判定口径：一条 claim 拆成可独立核查的子句，**全部子句成立**才判「正确」；有子句不成立或口径错位判「部分正确」；与代码/工具输出相反判「错误」。

## 结论

| claim | 结论 |
|---|---|
| 1. 四代平行子系统并存 | **部分正确** |
| 2. 52 模块强连通分量 + 21 条反向依赖 | **部分正确** |

- Claim 1：四条线的**文件和机制都存在**，`rds_compass_declaration.py:405` 的「借壳」一句**精确成立**。错在两处证据：`rds_cli.py:66-190` 不是 SQLite 账本（该区间没有任何 `sqlite` 代码），RSI 第三套 `rds_tools.py + rds_tool_workbench.py` 实为 740 行而非 1,060 行；「四套互不兼容 schema」在账本部分口径夸大（物理存储只有 2 个 SQLite 文件）。
- Claim 2：173 模块 / 638 边 / 21 条 upward / 环横跨四层**全部可复现**；**「52 模块 SCC」与「48 对双向互引」不可复现**：同一工具同一版本输出最大 SCC = 49、双向对 = 41，且从 160 模块到 173 模块的五个历史版本一直是 49/41。另有 `rds_project_hooks.py:1-60`（文件只有 21 行）和「148 个 `rds_*.py`」（实为 170）两处错误。
- 最严重错误项：**52/48 这两个数字**。审计明确写「`tools/import_graph.py` 实测」，但该工具在任何可复现版本上都给不出这两个数；这是证据级错误，不是估算偏差。

## 1. Claim 1 逐项核实

### 1.1 状态与账本（审计称 4 套）

| 子句 | 实测 | 判定 |
|---|---|---|
| `rds_cli.py:66-190` 是 L3 SQLite 账本 | 该区间是 CLI 参数/契约校验辅助函数（`identity` 63、`json_object` 69、`required_field` 74、`path_field` 79 等），**不含任何 `sqlite` 代码**；真正的账本在 `rds_state.py:12`（`RDSState`，`state.sqlite3`）和 `rds_cli.py:1674/1697`（打开 `.rds/project.sqlite3`、构造 `RDSState`） | **错误**（文件对、行号指错） |
| `rds_state.py` 是独立 SQLite 账本 | 存在，141 行；`rds_state.py:42-45` 建 `state/events/artifacts/receipts` 四张表，独立文件 `state.sqlite3` | **正确** |
| Quick 账本 `rds_quick.py` / `rds_quick_ledger.py` | 两文件存在（762 / 404 行）。但 `rds_quick_ledger.py` **没有自己的 `CREATE TABLE`**，直接 `from rds_project import ProjectStore` 复用 `project.sqlite3`，只在预算行上做 quick 路由记账 | **部分正确**（是独立记账路径，不是独立 schema） |
| Full ProjectStore `rds_project.py:1-2039` | 文件恰好 2,039 行；`rds_project.py:1061-1067` 建 `contract/budget/runs/output_claims/receipts/exposures/events` 七张表 | **正确** |
| Compass 事件链 `rds_compass_store.py:78-137` | `_rows` 77 行、`events` 84 行、`append` 110 行（`INSERT INTO events(body)` 在 136 行），确实把 `COMPASS_*` 哈希链写进 ProjectStore 的 `events` 表 | **正确** |

结论：4 条账本**路径**存在，但物理存储只有 2 个 SQLite 文件（`state.sqlite3`、`project.sqlite3`），Quick 与 Compass 都是 `project.sqlite3` 上的不同记账方式。所以「四套账本」在「四代并行实现」意义上成立，在「四套互不兼容 schema」意义上不成立。

### 1.2 Advisor 引擎（审计称 4 套）

| 子句 | 实测行数（`cf8964b`） | 判定 |
|---|---|---|
| `rds_advisor.py` + `rds_advisor_search.py`（审计 2,427） | 942 + 1484 = **2,426** | 正确（差 1 行） |
| `rds_owned_advisor.py`（797） | **797** | 正确 |
| `rds_research_advisor.py`（375） | **375** | 正确 |
| `rds_compass.py`（1,477） | **1,491** | 差 14 行，超出 ±10 |

结论：**部分正确**。四套引擎都在，行数 3 项精确；`rds_compass.py` 的 1,477 与实测 1,491 差 14 行（超出 ±10），但不影响「4 套 Advisor」的结论。

### 1.3 Jump / 越狱引擎（审计称 3 套）

| 子句 | 实测 | 判定 |
|---|---|---|
| `rds_structure.py` + `rds_jump.py`（2,065） | 881 + 1184 = **2,065** | 正确 |
| `rds_theory_progression.py` + `rds_frontier.py`（1,017） | 485 + 532 = **1,017** | 正确 |
| `rds_compass.py:escape()` | `rds_compass.py:1010` 存在 `def escape(store, state, diagnosis)` | 正确 |

结论：**正确**。

### 1.4 RSI / 算子演化（审计称 3 套）

| 子句 | 实测 | 判定 |
|---|---|---|
| `rds_rsi.py`（254） | **253** | 正确（差 1 行） |
| `rds_tools.py` + `rds_tool_workbench.py`（1,060） | 429 + 311 = **740**；从 `2e4193c` 到 `cf8964b` 五个版本都是 740 | **错误**（差 320 行，约 43%） |
| `rds_compass_rsi.py`（552） | **547** | 正确（差 5 行） |

结论：**部分正确**（第三套的行数证据错误，但「三套 RSI」结构成立：`rds_rsi.py` 的变异晋升、`rds_tools.py`+`rds_tool_workbench.py` 的工具资格晋升、`rds_compass_rsi.py` 的符号工具族晋升确实是三条独立路径）。

### 1.5 `compass.json` 伪装 `mode='quick'`

审计原句：「`rds_compass_declaration.py:405` 甚至被迫将 `compass.json` 伪装编译成 `mode='quick'` 的 `ProjectStore`，再把 `COMPASS_*` 哈希链塞进 `events(body)` 字段中『借壳上市』」。

实测：

- `rds_compass_declaration.py:405` 精确是 `initialize_project(store, contract, mode='quick')`。
- `rds_project_lifecycle.py:165-180`：`FULL requires an explicit valid advisor_policy or --recipe; QUICK requires no advisor_policy`。Compass 声明没有 `advisor_policy`，所以只能走 `quick`。
- `rds_compass_store.py:137`：`db.execute('INSERT INTO events(body) VALUES (?)', (canonical(body),))`，`kind` 前缀是 `COMPASS_`，确实写进 ProjectStore 的 `events` 表。
- `rds_compass_store.py:78-81` 读回时用 `json_extract(body,'$.kind') LIKE 'COMPASS_%'` 过滤同一张表。

结论：**正确**，行号精确，机制描述准确。

### 1.6 「3～4 套互不兼容的 JSON Schema 与状态机」

- Advisor 四套确实是四套不同契约（`rds_advisor`、`rds_owned_advisor`、`rds_research_advisor`、`rds_compass` 各有独立输入/状态结构）。
- 账本只有 2 套物理 schema + 2 条复用路径（见 1.1）。
- Jump/RSI 的「三套」是三条算法路径，不是三套 schema。

结论：**部分正确**。「4 套 Advisor / 3 套 Jump / 3 套 RSI 的实现路径并行」成立；「3~4 套互不兼容的 JSON Schema」在账本维度不成立。

### 1.7 Claim 1 小结

- 成立：四套 Advisor 文件与规模、三套 Jump、三套 RSI、借壳机制（行号精确）。
- 错误：`rds_cli.py:66-190` 指向非账本代码；`rds_tools.py + rds_tool_workbench.py` 的行数（1,060 vs 740）。
- 口径夸大：账本「四套 schema」实为 2 套物理 schema + 2 条复用。
- 整体：**部分正确**。

## 2. Claim 2 逐项核实

复现命令（`cf8964b`，工作区未改动时）：

```
cd C:/Users/XU/rds-357
python -B tools/import_graph.py | python -c "import json,sys; d=json.load(sys.stdin); print(d['counts'], [len(g) for g in d['cycles']], len(d['mutual_pairs']))"
```

实测输出：`{'edges': 638, 'modules': 173, 'sites': 894, 'unresolved': 1, 'upward_edges': 21} [49, 4, 3, 2] 41`

| 子句 | 实测 | 判定 |
|---|---|---|
| 173 个模块 | 173（其中 `rds_*.py` 170 个） | 正确 |
| 638 条边 | `counts.edges = 638`（去重 source→target 对；站点数 `sites=894`） | 正确 |
| 21 条 upward edges | `counts.upward_edges = 21` | 正确 |
| 52 模块巨型 SCC | `cycles = [[49], [4], [3], [2]]`，最大 SCC = **49**；4 个 SCC 合计 58 个模块在环中 | **错误** |
| 48 对双向互引 | `mutual_pairs = 41`（`import_graph.py` 的 `mutual_pairs` 统计：`a < b and (b, a) in pairs`） | **错误** |
| SCC 横跨 L1/L2/L3/L4，含点名模块 | 最大 SCC 层分布：L1 25、L2 3、L3 18、L4 3；审计点名的 `rds_project`、`rds_campaign`、`rds_cas`、`rds_tms_store`（L1）、`rds_hypergraph`（L2）、`rds_advisor`、`rds_owned_advisor`、`rds_autonomy`、`rds_jump`（L3）、`rds_quick`（L4）**全部在同一个 49 模块 SCC 内** | 正确（大小除外） |
| `rds_project_hooks.py:1-60` | 文件只有 **21 行**（`wc -l`），`provider()` 在 15-21 行 | **错误**（行号越界） |
| 148 个 `rds_*.py` 平铺 | 实际 170 个 `rds_*.py`、173 个 Python 模块；审计自己在 `outline:22` 写的是「170 个」，与 `:50/:99` 的 148 自相矛盾 | **错误** |

历史版本对照（同工具，各 commit 的 `--root` 指向该 worktree）：

| commit | 模块 | 边 | upward | SCC 大小 | 双向对 |
|---|---|---|---|---|---|
| `2e4193c` | 160 | 591 | 22 | 49,4,3 | 41 |
| `85f74a0` | 161 | 597 | 22 | 49,4,3 | 41 |
| `5baa04a` | 170 | 632 | 21 | 49,4,2,3 | 41 |
| `97b6ad2` | 171 | 634 | 21 | 49,4,2,3 | 41 |
| `68256e2` | 172 | 637 | 21 | 49,4,2,3 | 41 |
| `cf8964b` | 173 | 638 | 21 | 49,4,3,2 | 41 |

结论：**52 和 48 在审计基准版本及此前五个版本上都无法复现**，最大 SCC 恒为 49、双向对恒为 41。173/638/21 与「环横跨四层」正确。

### 2.1 Claim 2 小结

- 成立：173 模块、638 边、21 条 upward、四层跨度、点名模块确实同在最大 SCC。
- 错误：52（实为 49；4 个 SCC 共 58 模块）、48（实为 41）、`rds_project_hooks.py:1-60`（文件 21 行）、148 个 `rds_*.py`（实为 170）。
- 整体：**部分正确**。

## 3. 设计债 vs 真 bug

| 项 | 性质 | 依据 |
|---|---|---|
| 四套 Advisor / 三套 Jump / 三套 RSI 并行 | **设计债**（可延后） | 重复实现抬高维护成本，但没有运行时错误；各路径有独立测试（见 §7） |
| Compass 借壳 `mode='quick'` + 写 `events` 表 | **设计债**（可延后；但重构优先级应高于普通重复实现） | `rds_compass_declaration.py:405`、`rds_compass_store.py:137`。它绕过了 FULL 模式的 `advisor_policy` 校验，是「有意的语义规避」，不是崩溃 |
| Quick/Full 共用 `project.sqlite3` | **设计债** | `rds_quick_ledger.py` 无独立建表 |
| 49 模块 SCC 与 18 条剩余 upward（原 21 条） | **设计债**（可延后，但阻碍静态检查与分包） | §4、§6 |
| 审计的 52/48/148/1,060/`:66-190`/`hooks:1-60` | **证据错误**，不是代码 bug | 见 §1、§2；修文档，不修代码 |
| `paid_query` 未登记 `LAYERS`，`test_real_repository_is_covered` 在 `cf8964b` 上即失败 | **真 bug（CI 红）**，范围外，本次未修 | `python -B -m unittest tests.test_import_graph` → `AssertionError: ['paid_query'] is not false`；`scripts/paid_query.py` 由 `addf892` 引入，未加入 `tools/import_graph.py` 的 `LAYERS` |

两条 claim 本身**不包含真 bug**：账本并行、依赖环是架构债；数字错误是审计证据问题。本次核实中唯一实测到的真问题（CI 红）是范围外的 `paid_query` 层登记缺失，已在 §9 记录，未改。

## 4. 21 条 upward edges 清单（审计基准 `cf8964b`）

口径：`tools/import_graph.py` 的 `upward` 列表去重后的 (source, target) 对。「层」取自 `LAYERS`；`SHARED` 不参与 upward 判定。

| # | 源（层） | 目标（层） | 站点行 | 现在状态 |
|---|---|---|---|---|
| 1 | `rds_advisor_search`（L3） | `rds_capabilities`（L4） | 763 | **已打破** |
| 2 | `rds_autonomy`（L3） | `rds_autonomy_worker`（L4） | 71 | 剩余 |
| 3 | `rds_campaign`（L1） | `rds_hypergraph`（L2） | 498 | 剩余 |
| 4 | `rds_campaign`（L1） | `rds_owned_advisor`（L3） | 420、463 | 剩余（420 的 `MAX_REPORT_BYTES` 站点已消除，463 的 `_state` 仍在） |
| 5 | `rds_discrimination`（L2） | `rds_advisor_search`（L3） | 13 | 剩余 |
| 6 | `rds_method_revision`（L1） | `rds_autonomy`（L3） | 69 | 剩余 |
| 7 | `rds_method_revision`（L1） | `rds_owned_advisor`（L3） | 106 | 剩余 |
| 8 | `rds_owned_tools`（L1） | `rds_owned_advisor`（L3） | 154 | **已打破** |
| 9 | `rds_project`（L1） | `rds_advisor_search`（L3） | 560 | 剩余 |
| 10 | `rds_project`（L1） | `rds_owned_advisor`（L3） | 405（provider 回调） | 剩余 |
| 11 | `rds_project_hooks`（L1） | `rds_owned_advisor`（L3） | 18 | 剩余（不可拆，见 §6） |
| 12 | `rds_project_lifecycle`（L1） | `rds_owned_advisor`（L3） | 352 | 剩余 |
| 13 | `rds_rsi_confirmation`（L1） | `rds_meta`（L3） | 6 | 剩余 |
| 14 | `rds_steering`（L1） | `rds_dialogue`（L4） | 254 | 剩余 |
| 15 | `rds_theory_progression`（L1） | `rds_operators`（L3） | 161 | 剩余 |
| 16 | `rds_tms_store`（L1） | `rds_hypergraph`（L2） | 10 | 剩余 |
| 17 | `rds_tool_applicability`（L1） | `rds_advisor_search`（L3） | 147 | 剩余 |
| 18 | `rds_tool_applicability`（L1） | `rds_owned_advisor`（L3） | 146 | 剩余 |
| 19 | `rds_tool_calls`（L1） | `rds_owned_advisor`（L3） | 166 | 剩余 |
| 20 | `rds_tool_workbench`（L1） | `rds_owned_advisor`（L3） | 121 | **已打破** |
| 21 | `rds_tools`（L1） | `rds_quick`（L4） | 21 | 剩余（文档化例外） |

## 5. 已打破的 3 条（实测）

### 5.1 `rds_advisor_search(L3) -> rds_capabilities(L4)`：常量下移

- 机制：`CAPABILITIES` 是 5 个字符串的纯常量（`rds_capabilities.py:10`），下移到 SHARED 层的 `rds_reference_values.py:15-17`。`rds_capabilities` 改为 `from rds_reference_values import CAPABILITIES`，公开名字保留；`rds_advisor_search.py:763` 改从 `rds_reference_values` 取。
- 改动文件：`scripts/rds_reference_values.py`、`scripts/rds_capabilities.py`、`scripts/rds_advisor_search.py`。
- 公开 API 不变：`rds_capabilities.CAPABILITIES` 仍可导入（已核 `import rds_capabilities` 输出 5 元组）。

### 5.2 `rds_owned_tools(L1) -> rds_owned_advisor(L3)` 与 `rds_tool_workbench(L1) -> rds_owned_advisor(L3)`：消除重复实现

- 机制：`_original` / `_read_original` 在 `rds_owned_advisor.py`（199-225 行）和 `rds_original_read.py`（L1，`_original` 10 行、`_read_original` 17 行）**各有一份实现**，两版逻辑相同，`rds_original_read` 版只多一个向后兼容的 `max_bytes` 默认参数。把 `rds_owned_advisor` 改为从 `rds_original_read` 导入（`_original`/`_read_original` 名字保留，属 re-export），`rds_owned_tools.py:154`、`rds_tool_workbench.py:121` 也改从 `rds_original_read` 导入。
- 改动文件：`scripts/rds_owned_advisor.py`（删 30 行重复定义与已无用的 `hashlib`/`Path` import）、`scripts/rds_owned_tools.py`、`scripts/rds_tool_workbench.py`。
- 公开 API 不变：`rds_owned_advisor._original`、`._read_original` 仍可导入；调用方 `rds_autonomy.py:182/347`、`rds_dialogue.py:83` 未改，回归测试通过。

### 5.3 顺带消除一个站点（边未完全打破）

`rds_campaign -> rds_owned_advisor` 的 420 行站点是 `from rds_owned_advisor import MAX_REPORT_BYTES`（32 MiB 常量）。把常量下移到 `rds_output_values.py:5-7`（SHARED），`rds_owned_advisor` re-export，`rds_campaign.py:420` 改从 `rds_output_values` 取。但 `rds_campaign.py:463` 还有 `from rds_owned_advisor import _state, OWNED_PREFIX`（读 owned 状态），所以这条边**仍在**，只是站点数 2→1。

改动后复测：

```
python -B tools/import_graph.py | python -c "import json,sys; d=json.load(sys.stdin); print(d['counts'])"
```

`upward_edges` 21 → **18**，被打破的三对是 `advisor_search->capabilities`、`owned_tools->owned_advisor`、`tool_workbench->owned_advisor`。`edges` 638 → 642（新增 SHARED 导入边，无新增 upward），`mutual_pairs` 41 → 40（`owned_tools` 与 `owned_advisor` 的双向对消失），SCC 大小不变。

## 6. 剩余 18 条的建议

按拆解成本分组。备注「不可拆」的，是指拆了会把环挪个位置或破坏既有的延迟加载设计。

**低成本（常量/纯函数下移，一次可拆多条）**

- #5、#17：`evaluate_condition` 是三值纯函数（`rds_advisor_search.py:106-138`，依赖 `_reported`/`_finite`/`_evidence_status`），可下移到 L1/SHARED；`rds_discrimination.py:13` 和 `rds_tool_applicability.py:147` 都只用它做条件求值。一次可破 2 条。
- #2：`output_paths` 是纯路径函数（`rds_autonomy_worker.py`），可下移到 L1（`rds_autonomy_read` 一类），`rds_autonomy.py:88` 的调用不变。
- #16：`rds_tms_store.py:10` 从 `rds_hypergraph` 取 `ASSURANCE, _validate, review_hypergraph, audit_sources`。`ASSURANCE` 是字符串常量、`_validate` 是纯结构校验，可下移到 L1 的图校验模块；`review_hypergraph` 依赖 L2 数据模型，需评估。若把校验下移到 L1，注意 `rds_hypergraph` 已 import `rds_hypergraph_blockers`（L2），不要形成新的 L2→L1 回环。
- #13：`rds_rsi_confirmation.py:6` 的 `_atomic_bytes`、`_graph_lock`、`_load_object` 是通用原子写/锁/读工具，应下移到 L0/L1（`rds_core` 或 SHARED io 模块）；`digest`/`require` 已在 `rds_project_values`/`rds_reference_values` 有更低的副本。

**中等成本（抽读接口，不改语义）**

- #3：`rds_campaign.py:498` 的 `_validate(graph)` 用于校验 owned settlement 里的图。可把图校验抽到 L1（例如与 `rds_tms_store` 共用一个校验模块），但 `_validate` 返回 `(index, edges, goals, limits)` 且会被 `review_hypergraph` 复用，抽走要处理返回值契约。
- #4、#18、#19：`rds_campaign.py:463` 的 `_state`、`rds_tool_applicability.py:146` 的 `_state/_collect/_facts`、`rds_tool_calls.py:166` 的 `review` 都直接读 owned 状态。参照 §5.2 的做法，把「owned 只读状态」抽到 L1 的 `rds_owned_read`（`rds_owned_history` 已是类似先例），再由 L3 re-export。`review` 是引擎主体，不能简单抽。
- #6：`rds_method_revision.py:69` 的 `controller_reservation(store, db)` 是只读查询，可放进 L1 的 autonomy 读接口（`rds_autonomy_read` 已存在）。
- #7、#12：`validate_policy(store, contract)` 是纯契约校验（`rds_owned_advisor.py:37` 起），可抽到 L1（依赖 `require`/`canonical`/`MAX_JSON_BYTES`/`CONTEXT_FIELDS`），再 re-export。`rds_project_lifecycle.py:352`、`rds_method_revision.py:106` 是仅有的两处直接调用。
- #9：`rds_project.py:560` 的 `_dependency_review` 是 L3 审查函数，下移成本高（依赖 owned advisor 的图与策略），建议先保留、记录白名单。
- #15：`rds_theory_progression.py:161` 的 `BoundedFiniteModelOperator` 是类型/协议定义，可下移到 SHARED（类型不需要行为），但要同步 `rds_operators` 的 re-export 与 `isinstance` 检查。
- #14：`rds_steering.py:254` 的 `from rds_dialogue import build` 是 L4 展示逻辑的延迟调用。拆法是把 `build` 作为参数注入 `steering`，由 CLI 层（L4）传入；不改会让 steering 永远依赖 L4。
- #10：`rds_project` 的 3 个 `provider("advisor")` 回调是生命周期 provider 设计（`rds_project.py:405/447/1024`），拆它等于改 provider 机制，建议保持并记录为设计例外。

**不可拆（拆了会循环或破坏既有设计）**

- #11 `rds_project_hooks.py:18`：这个模块的存在目的就是「按名字延迟加载 L3/L4 provider」（`_modules = {"advisor": "rds_owned_advisor", "quick": "rds_quick_ledger"}`）。去掉 import 就没法注册 provider；改成 L4 装配才能消除，属大改，不建议在「破边」任务里做。
- #21 `rds_tools.py:21` 的 `from rds_quick import execute`：`rds_quick.py:313` 反过来 `from rds_tools import _preparation_contract`，**两条边构成双向依赖**；只拆一边会把环挪到另一边。`tests/test_rds_entrypoint_imports.py:17-20` 已把它列为「documented quick policy and execution edges」白名单。要消这条边，得把执行器改成注入参数，属接口重构。
- #19 `rds_tool_calls.py:166` 的 `review`：owned advisor 的引擎主体（`review` 函数），下移等于搬整个 L3 模块进 L1，会制造新的反向依赖。

## 7. 改动与测试

改动文件（8 个，均为 §5 涉及的模块；未改公开 API）：

```
scripts/rds_reference_values.py   +CAPABILITIES
scripts/rds_capabilities.py       CAPABILITIES 改为从 SHARED 导入
scripts/rds_advisor_search.py     导入来源改为 rds_reference_values
scripts/rds_output_values.py      +MAX_REPORT_BYTES
scripts/rds_owned_advisor.py      导入 MAX_REPORT_BYTES/_original/_read_original；删重复定义与死 import
scripts/rds_campaign.py           MAX_REPORT_BYTES 来源改为 rds_output_values
scripts/rds_owned_tools.py        _read_original 来源改为 rds_original_read
scripts/rds_tool_workbench.py     _original/_read_original 来源改为 rds_original_read
```

测试（逐模块独立进程跑，避免测试文件互相 `import test_xxx` 的路径干扰）：

```
PYTHONPATH=tests python -B -m unittest tests.test_import_graph tests.test_rds_entrypoint_imports
# Ran 101 tests，1 failure：test_real_repository_is_covered（paid_query 未登记 LAYERS，预先存在）

PYTHONPATH=tests python -B -m unittest tests.test_rds_advisor_search          # 22 OK
PYTHONPATH=tests python -B -m unittest tests.test_rds_advisor_workset         # 11 OK
PYTHONPATH=tests python -B -m unittest tests.test_rds_advisor_discrimination   # 13 OK
PYTHONPATH=tests python -B -m unittest tests.test_rds_capabilities            # 4 OK
PYTHONPATH=tests python -B -m unittest tests.test_rds_tool_applicability      # 13 OK
PYTHONPATH=tests python -B -m unittest tests.test_rds_owned_tools             # 16 OK
PYTHONPATH=tests python -B -m unittest tests.test_rds_tool_workbench          # 12 OK
PYTHONPATH=tests python -B -m unittest tests.test_rds_tool_calls              # 10 OK
PYTHONPATH=tests python -B -m unittest tests.test_rds_campaign_binding        # 44 OK
PYTHONPATH=tests python -B -m unittest tests.test_rds_campaign_report_boundary # 4 OK
PYTHONPATH=tests python -B -m unittest tests.test_rds_structure               # 24 OK
PYTHONPATH=tests python -B -m unittest tests.test_rds_advisor                 # 28 OK
PYTHONPATH=tests python -B -m unittest tests.test_rds_advisor_review_regressions # 5 OK
PYTHONPATH=tests python -B -m unittest tests.test_pr303_owned_open_planning   # 5 OK

cd scripts && python -B -c "import rds_cli"                                    # ok
```

最小反向测试：改动前后 `tests.test_import_graph` 都是 97 个测试、96 通过，唯一失败仍是预先存在的 `paid_query`（`PYTHONPATH=tests python -B -m unittest tests.test_import_graph`）；`tests.test_rds_entrypoint_imports` 4 个全过。`git stash` 后 `upward_edges` 回到 21。

## 8. 复现命令汇总

```
# 审计基准版本（cf8964b）
git worktree add --detach /tmp/rds-cf8964b cf8964b
python -B /tmp/rds-cf8964b/tools/import_graph.py --root /tmp/rds-cf8964b > /tmp/ig.json

# 统计口径
python - /tmp/ig.json <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
print("counts:", d["counts"])
print("SCC sizes:", [len(g) for g in d["cycles"]])
print("mutual_pairs:", len(d["mutual_pairs"]))
print("max SCC layers:", __import__("collections").Counter(d["modules"][m]["layer"] for m in max(d["cycles"],key=len)))
PY
```

`cf8964b` 期望输出：`modules 173 / edges 638 / upward_edges 21`；SCC 大小 `[49,4,3,2]`；`mutual_pairs 41`；最大 SCC 层分布 `{'L1':25,'L2':3,'L3':18,'L4':3}`。

## 9. 范围外发现（未修）

`scripts/paid_query.py`（由 `addf892` 引入）没有登记到 `tools/import_graph.py` 的 `LAYERS`，导致 `tests/test_import_graph.py:983` 的 `test_real_repository_is_covered` 在审计基准 `cf8964b` 上就失败（`AssertionError: ['paid_query'] is not false`）。这是本次实测发现的真问题，但不在「结构性两条 claim」的核实范围，按任务约束未改。建议后续把 `paid_query` 归到 `L1` 或 `SHARED`（按它是否只做记账）。

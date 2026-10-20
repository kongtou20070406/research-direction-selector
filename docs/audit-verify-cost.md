# 外部审计核对：成本与帕累托

核对对象：`kongtou20070406/research-direction-selector`，分支 `release/6.0.0-compass`，HEAD `cf8964b`。
审计原文：`docs/rds-development-outline.md:51`（"Compass 状态折叠的 O(N²) 全量重放与磁盘 I/O 放大"）、`:60`（"保守确认策略造成'自我帕累托支配'"）、`:62`（"隐性合规税未被计入成本账本"）。原文为外部审计报告，已随 `cf8964b` 收录进 outline。

方法：只读检索 + 读源码 + 本机实测（Python 3.13.5 / Windows）。核对期间工作区出现并行 agent 的未提交改动（含 `rds_compass.py` 的 replay cache、`_gap_wide_enough` 等），为保证口径，**全部实测在 `git archive cf8964b` 导出的干净快照上重跑**，未把未提交改动计入；本文未改动任何代码，只新增本文件。

判定口径（沿用 `docs/audit-verify-express.md`）：一条 claim 拆成可核查子句；**关键子句**不成立或口径错位算「部分正确」；只有计数/措辞误差而核心主张成立时判「正确」；与代码相反算「错误」。

| # | 审计 claim | 判定 | 性质 |
|---|---|---|---|
| 1 | `fold` O(N²)：drive 每步 fold、`ingest` 调 3 次 fold、`stale_bindings` 全盘重算 SHA-256 | **部分正确** | 核心成立；`ingest` 实调 **4 次**，`stale_bindings` 行号偏 15 行 |
| 2 | 自我帕累托支配：`spend_budget: True` 花光剩余预算，`RDS(B=16/24/32)` 被 `RDS(B=12)` 支配 | **部分正确** | 支配现象成立；归因错，多出的是 PROBE/HOLDOUT，不是 `spend_budget`；`_confirmation_plan` 不存在 |
| 3 | 合规税未计入：账本只记实验数和 jump token，忽略 SKILL.md / 22 个 JSON Schema / 重试的 context token | **正确** | 核心成立；schema 实测 21 个不是 22，账本字段比"两项"多几项 |

---

## 1. `fold` O(N²) —— 部分正确

### 1.1 逐子句核对

| 子句 | 代码事实（HEAD `cf8964b`） | 判定 |
|---|---|---|
| `rds_compass_drive.py:53-86` 单步循环 | `scripts/rds_compass_drive.py:53` 是 `while True:`，`:54` 调 `compass.ingest`，`:55` 调 `stale_bindings`，`:86` 写 REPORT；区间对得上 | **正确** |
| `fold(store)` 在 `rds_compass.py:54-108` | `scripts/rds_compass.py:54-108` 整段就是 `fold`：`events`/`runs`/`budget`/`settlement_order` 全量读，再对事件逐条回放，末尾 `_derive(state)`（`:145-159`）把所有假设对所有历史测量重跑 replay | **正确** |
| `ingest()` 内部连续调用 **3 次** `fold(store)` | 实际 **4 次**：`scripts/rds_compass.py:220`、`:232`、`:242`（`retract_unbound(store, fold(store))`）、`:243`（`return fold(store), fresh`）。实测每次 ingest 恰好 4 次 | **错误（少算 1 次）** |
| `stale_bindings()` 在 `rds_compass.py:1228-1244` | 函数实际位于 `scripts/rds_compass.py:1243-1259`，偏 15 行 | **行号错误** |
| `stale_bindings` 全盘重算 SHA-256 | 成立：对每个 `MEASURED` 观测，`cstore.artifact`（`scripts/rds_compass_store.py:185-196`）读输出文件重算 sha256；`cstore.recheck`（`:173-182`）对该 receipt 绑定的**全部** artifact（含 `stdout.bin`/`stderr.bin`，`scripts/rds_project.py:1695`）逐个 `read_bytes()` 再 sha256。实测一次 campaign 中 `recheck` 142 次、重哈希 568 个 artifact、累计重读 65,876 字节 | **正确** |
| 事件上限 `MAX_EVENTS=20,000` | `scripts/rds_compass_store.py:18` 确为 `MAX_EVENTS = 20000`；`:79-80` 与 `:128` 在读/写两侧 fail-closed | **正确** |
| "每次执行 `step`、`decide` 或 `status`，`fold(store)` 都会……" | 方向对但不精确：`decide(state)` 本身不 fold（`scripts/rds_compass.py:397-491` 只读传入的 state）；是 drive 每步 `ingest` 触发 4 次 fold，另加每次 RIVALS 1 次（B=12 共 5 次）、ESCAPE/VERIFY/HANDOFF 各 1 次 | **口径偏松** |
| I/O 与 CPU 开销呈二次方爆炸 | 成立：每步 4 次全量 fold × 每次 O(事件数 + 假设数×测量数)，步数 N 则总成本 O(N²)。实测见下 | **正确** |

### 1.2 实测（regime-double，HEAD 快照）

| B | 实验数 | 记录步数 | fold 次数 | 每步 fold | fold 总耗时 | 单次 fold p50 / max | 首次→末次 fold |
|---|---|---|---|---|---|---|---|
| 12 | 12 | 19 | 85 | 4.47 | 1.86 s | 22.99 / 44.14 ms | 2.13 → 40.17 ms |
| 16 | 16 | 23 | 101 | 4.39 | 2.51 s | 25.97 / 57.98 ms | 2.46 → 45.15 ms |
| 24 | 21 | 30 | 131 | 4.37 | 4.16 s | 32.53 / 66.10 ms | 2.87 → 56.58 ms |
| 32 | 21 | 30 | 131 | 4.37 | 4.02 s | 31.58 / 81.90 ms | 2.99 → 51.97 ms |

fold 调用来源（B=12，共 85 次）：`ingest:220` 19、`ingest:232` 19、`ingest:242` 19、`ingest:243` 19、`generate_rivals` 5、`drive:81`（ESCAPE）1、`report` 1、`handoff` 1、初始化 1。B=24/32 为 30×4+7+1+1+1+1 = 131，同样对得上。

B=12 → B=24：步数 ×1.58，fold 调用 ×1.54，单次 fold p50 ×1.42，fold 总耗时 ×2.24。单次 fold 耗时从 2 ms 量级涨到 40–57 ms，与事件/观测规模同步增长；总 fold 时间对步数呈超线性。墙钟总耗时（B=12 的 32.6 s、B=24 的 52.0 s）中 fold 占 5.7% / 8.0%，当前规模下还是小头，但趋势与审计描述一致。

### 1.3 结论

审计这条的**核心结论成立**：drive 每步全量 fold、`ingest` 内多次 fold、`stale_bindings` 对磁盘文件全盘重算 SHA-256、成本随证据数二次增长。两处事实错：`ingest` 是 4 次不是 3 次；`stale_bindings` 行号偏 15 行。"decide 也 fold"的说法不准确（`decide` 是纯函数，fold 发生在它的调用者）。按判定口径判 **部分正确**，但这不是误判，方向与量级都对。

---

## 2. 自我帕累托支配 —— 部分正确

### 2.1 逐子句核对

| 子句 | 代码/数据事实 | 判定 |
|---|---|---|
| `rds_compass_declaration.py:38` 将 `spend_budget` 默认设为 `True` | `scripts/rds_compass_declaration.py:38` 的 `verification` 默认值确含 `'spend_budget': True` | **正确** |
| `rds_compass.py:466-478` 在"存活集仅剩 1 个假设且存在边界未探测间隙"时买确认点 | `scripts/rds_compass.py:466-471`：`spending = spend and (not cap or bought < cap) and _boundary_probe(state) is not None`。`_boundary_probe` 这一半与审计描述吻合；但代码里没有"仅剩 1 个假设"这个判定，`spending` 只看 spend/cap/boundary | **基本正确** |
| 会"**强制花光所有剩余实验预算**" | 不成立。`decide` 每次只返回**一个** `CONFIRM` 动作（`:474-478`），drive 每步只执行一个动作，没有循环花光；`_boundary_probe` 返回 `None` 时 `spending` 立即为 False。实测 B=24/32 的 `SPEND_BUDGET_ON_REFUTATION` 触发 **0 次**，实验停在 21（< 24、< 32） | **错误** |
| `_confirmation_plan` 花光预算（转述用名；审计原文 `:60` 的措辞是"强制花光所有剩余实验预算"） | 该符号在仓库全部 Python 文件、全部 git 历史中**都不存在**（`git log --all -S "_confirmation_plan"` 为空）。当前实现确认点是 `_confirm_candidate`（`scripts/rds_compass.py:342-357`），买点逻辑在 `decide` 内联；"花光"本身也不成立（见上一行） | **错误（函数名不存在 + 行为夸大）** |
| `RDS(B=16/24/32)` 被 `RDS(B=12)` 自我支配（`benchmark/newtonbench/pareto-review.md:126-130`） | 成立：`benchmark/newtonbench/results/pareto.json` 中 regime-double 的实验数为 B=12→12、B=16→16、B=24→21、B=32→21，`claim_exact` 都是 32/32（B=12 预测 `ite((x < (19/2)), (1+x), (x+x))`，整数域等价于 `x < 10`；其余为 `ite((x < 10), (1+x), (x+x))`）。成本严格更高、收益相同，构成自我支配 | **正确** |
| 归因："这直接导致"成本暴涨 | 不成立。审计说"实验次数从 29 暴增至 61"：61 确有出处（`docs/releases/6.0.0.md:193`、`benchmark/compass/README.md` 的 5 runs 合计 8+10+20+20+3），但那是 2026-10-17 旧停止规则的记录，早于 2026-10-20 15:14 的闸门提交 `adc444c`；且 61 是"改停止规则前 vs 后"的对比，不是"修复前 vs 修复后"。当前 HEAD 上 regime-double 多出来的成本是 PROBE + HOLDOUT（见 2.2），不是 `spend_budget` | **错误（版本与归因双错位）** |

### 2.2 regime-double 实测决策序列（HEAD 快照，与 `pareto.py` 同配置）

| B | 实验数 | RIVALS | CONFIRM（其中 SPEND_BUDGET） | PROBE | HOLDOUT | ESCAPE | 终止 |
|---|---|---|---|---|---|---|---|
| 12 | 12 | 5 | 4（1） | 7 | 0 | 1 | GOAL_IDENTIFIED |
| 16 | 16 | 5 | 4（1） | 11 | 0 | 1 | GOAL_IDENTIFIED |
| 24 | 21 | 7 | 3（**0**） | 15 | 2 | 1 | GOAL_IDENTIFIED |
| 32 | 21 | 7 | 3（**0**） | 15 | 2 | 1 | GOAL_IDENTIFIED |

读法：

- B=12 → B=16 多出的 4 次实验**全是 PROBE**（7→11），CONFIRM 次数不变（4 次，其中 SPEND_BUDGET 各 1 次）。
- B=16 → B=24/32 多出的 5 次实验是 **4 次 PROBE + 2 次 HOLDOUT 再减 1 次 CONFIRM**（4→3）；`SPEND_BUDGET_ON_REFUTATION` 一次都没触发，因为此时 `_boundary_probe` 已返回 `None`。
- PROBE 的来源是 `verification.max_gap=2` 的覆盖间隔规则（`scripts/rds_compass.py:483-488`，`reason='COVERAGE_GAP'`）与探针预算，不是 `spend_budget`。B=12/16 的那 1 次 SPEND_BUDGET 也确实买了点（x27 / x17，都紧贴未覆盖的 regime 边界），属于设计意图内的"用剩余预算测反例"，不是"无脑填坑"。
- B=24/32 停在 21 而不是 24/32：`decide` 在确认数与覆盖都满足后走 VERIFY，预算上限只封顶、不强买。

`benchmark/newtonbench/pareto-review.md:128` 自己写的归因是"由于缺少跨步早停，算法继续消耗了 16~21 次实验"——这个方向与实测一致（多出来的是覆盖探针/holdout 与缺少饱和早停）。审计转述时把它替换成了"`spend_budget` 机械烧光剩余预算"，这一步替换没有代码或数据支持。

### 2.3 "已修 / 未修"的准确状态

**已修（部分）**：`spend_budget` 的"有余额就买"已经收窄为"**存在宽度 > 2 的未测间隔才买，且每次决定只买 1 点**"。

- 修复提交：`adc444c`（2026-10-20 15:14），`scripts/rds_compass.py:470` 从 `spend and (not cap or bought < cap)` 改为再加 `and _boundary_probe(state) is not None`。
- 实测效果：B=24/32 上 `SPEND_BUDGET_ON_REFUTATION` 触发 0 次；B=12/16 各触发 1 次，且都发生在确有 >2 未测间隔时（两次决定的 `boundary_probe` 都是 x17）。
- 因此审计说的"无脑花光"在**当前 HEAD 已经不成立**。若审计基于更早快照，则它描述的是旧代码，但"29→61"的数字取自 10-17 的 release notes，仍然不能用来描述当前 HEAD（审计文本收录于 `cf8964b` 时，闸门已合入）。

**未修（仍存在）**：没有饱和早停。

- B=12 时 regime-double 已锁定 32/32，B=16/24/32 仍继续买覆盖点与 holdout，成本从 12 涨到 16/21/21。
- 这部分成本来自 `max_gap=2` 覆盖规则与 `holdout: 2`，以及"必须攒够确认数"的 `MINIMUM_CONFIRMATIONS`，与 `spend_budget` 无关。
- 所以"`RDS(B=16/24/32)` 被 `RDS(B=12)` 支配"**现象仍在**，根因是缺少"锁定即停"的停机规则，不是审计说的 `spend_budget` 花光。

一句话状态：**审计的机制描述（`spend_budget` 无脑花光）已被 `adc444c` 的边界闸门部分修复，在 B=24/32 上完全失效；但"自我支配"现象未修，实际多花的是 PROBE/HOLDOUT 与缺失的早停。`_confirmation_plan` 从未存在；"29→61"是 10-17 旧规则记录，不能用来描述当前 HEAD。**

---

## 3. 合规税未计入 —— 正确

### 3.1 逐子句核对

| 子句 | 代码/数据事实 | 判定 |
|---|---|---|
| 账本"只统计实验次数和显式上报的 Jump Token" | 报告的成本向量在 `scripts/rds_compass.py:1485-1487`：`experiments`、`jumps`（**次数**，不是 token）、`agent_tokens`（token 花费，来自 agent 自报）。此外还记 `budget.wall_seconds`；`agent_tokens` 只在声明了 `agent` 时才有值，否则为 `None` | **基本正确，措辞需修正** |
| "Jump Token"= 每次 jump 的 agent 调用自报 token | 成立：`_usage`（`scripts/rds_compass.py:779-788`）解析 agent stdout 的 `{input_tokens, output_tokens, cached_input_tokens?}`；`_call_agent`（`:853-872`）把它写进 `evidence['usage']`；`escape` 把 evidence 写进 `DISPOSITION.agent`（`:1046-1049`）；`agent_tokens`（`:836-849`）汇总全部 dispositions 的 usage。**未上报的调用单独计数并 fail-closed**（`status='USAGE_UNREPORTED'` 时不再买 agent 调用，`:992-993`） | **正确** |
| 忽略"阅读 `SKILL.md`"的 token | 成立：`SKILL.md` 22,722 字节；Compass 账本没有任何字段表示宿主 agent 的常驻上下文或 skill 阅读成本 | **正确** |
| "拼装 22 个严格 JSON Schema" | 数量不准：`schemas/` 实测 **21 个** JSON Schema 文件（全部 `$schema: draft/2020-12`）。另有两组内联 schema 常量（`rds_compass.py:24-26` 的 workset/packet/report，`rds_compass_declaration.py:21-22` 的 declaration/plan），但仓库里可数的 schema 文件是 21 | **数字错误（21 不是 22）** |
| 忽略"被拒绝打回重试"的 context token | 成立，但要加一层限定：RDS 的 agent 调用**不自动重试**（`_call_agent` 单次 `subprocess.run`，失败记 `AGENT_FAILED` 并结束本次 jump）；实验有 retry slot（`attempts()`，`rds_compass.py:166-168`），但那是重跑实验，不产生 LLM context。宿主 agent 被 RDS 拒绝后自行重试的 token 确实发生在账本之外 | **正确（但"重试"发生在宿主侧，不是 RDS 内建机制）** |
| 核心主张："隐性合规税未被计入总成本账本" | 成立：`report()` 的 `cost` 只有 experiments/jumps/agent_tokens 三项，没有 SKILL.md 阅读、schema 拼装、宿主重试的 context token | **正确** |

### 3.2 结论

审计这条**成立**：Compass 账本是 campaign 账本，不是宿主 agent 的全生命周期账本；SKILL.md 阅读、schema 拼装、被拒重试的 context token 都不在 `report()` 的成本向量里。三处需要修正：schema 文件实测 21 个（不是 22）；账本实际还记 `wall_seconds` 与每次 agent 调用的 input/output/cached 三项细分（不只是"实验数和 jump token"）；`agent_tokens` 只在声明了 `agent` 的 campaign 里非空。按判定口径的"关键子句"标准判 **正确**——核心主张没有被这些计数/措辞问题推翻。

---

## 附：复现

所有实测都在 `git archive cf8964b` 的干净快照上跑，只 monkeypatch 内存中的 `compass.fold` / `compass.decide` / `cstore.recheck` / `cstore.artifact`，不改仓库代码。实测脚本在 `C:/Users/XU/AppData/Local/Temp/audit-cost/`（`verify_head.py`、`stale_count.py`；临时目录，仓库外）。

```bash
# 1. 导出 HEAD 快照（不碰工作区）
git archive cf8964b scripts tests/compass_fixture.py benchmark/newtonbench/sparse_suite.py \
  | tar -x -C /tmp/rds-head

# 2. 在快照上跑 regime-double 的 B=12/16/24/32，统计 fold 调用来源与 decision 序列
cd /path/to/audit-cost && RDS_SRC=/tmp/rds-head python -B verify_head.py
```

判定用的原始输出（本机）：

- fold 调用来源、每步耗时：`C:/Users/XU/AppData/Local/Temp/audit-cost/fold_callers.json`（工作区初跑；与 HEAD 快照复跑一致）
- 决策序列与 CONFIRM 归因：`C:/Users/XU/AppData/Local/Temp/audit-cost/decision_full.json`（同上）
- HEAD 快照复跑（本文表 1.2、2.2 的数据）：`C:/Users/XU/AppData/Local/Temp/audit-cost/head_verify.json`
- `stale_bindings` 重哈希计数：`C:/Users/XU/AppData/Local/Temp/audit-cost/stale_count.py` 输出（HEAD 快照：21 次 stale、142 次 recheck、568 个 artifact、65,876 字节）

关键代码位置：`scripts/rds_compass.py:54-108`（fold）、`:145-159`（`_derive`）、`:218-243`（ingest）、`:342-357`（`_confirm_candidate`）、`:360-382`（`_boundary_probe`）、`:466-478`（spending）、`:1243-1259`（`stale_bindings`）、`:1453-1491`（report/cost）；`scripts/rds_compass_store.py:18`（MAX_EVENTS）、`:173-196`（recheck/artifact）；`scripts/rds_compass_drive.py:53-86`（单步循环）；`scripts/rds_compass_declaration.py:38`（spend_budget 默认值）。

# RDS 开发纲领（2026-10-21 修订版）

制定：lead session + kimi-k3 决策搭档（决策责任方）。
依据：`RDS-Compass-structure-review-20261020.md`（结构审查）、`RDS-Compass-AI4S-review-20261019.md`（收益审计）、`handoff-20261020.md`（逐条核实后的口径）、devset 实验（5 Feynman 方程 × B=8/16/32）、第二轮外部审计（2026-10-21，逐条裁定见附录 B.4）。

**修订史**：

- **2026-10-20 首版**：正文曾直接采信第一轮审计，其中三条被核实为假（`52 模块 SCC` 实为 49、`_confirmation_plan` 在全部 git 历史中不存在、「29→61」出自旧停止规则）。正文只保留已核实事实，审计原文降为附录 B 并逐条标注。
- **2026-10-21 本版**：吸收第二轮审计（9 条成立、4 条需更正）；把「饱和早停」的归因从「死代码」改成实测的成本构成；新增铁律 8「RDS 是裁判不是选手」；标记成本两轴已可自动计算。

---

## 一、目标泛函

```
max  E[ claim_exact 独立复核正确率 ]
     − λ · (每正确解 token)
s.t. 假认证率 ≤ 对照
     best_effort_exact 永不计入正确
     成本可从账本重算
```

- `λ` 未标定 → 保持 `UNKNOWN`，fail-closed，不允许猜默认值。
- 主指标是**独立复核正确率**，不是成功率、不是实验增速、不是工程完整性。
- 质量 = `claim_exact` + 假认证率；成本 = 实验数 + token + 墙钟 + 计算。
- **成本轴现已可自动计算**（`381e648`）：`devset_run.summarize` 与 `pareto.summarize` 都报 `experiments_per_correct` / `seconds_per_correct`。用真实结果文件验证过：`devset-run.json` → 12 correct / 84 experiments / 802.6s = **7.0 次实验、66.9 秒每个正确解**（与成本审计手算的 7.00/66.88 对得上）；`pareto.json` → rds 臂 **21.667**、baseline 臂 **null**（294 次实验解出 0 个）。零解时比值保持 `null`，不用 0/0 冒充便宜。

## 二、八条铁律

1. **口径分离**：`claim_exact`（`GOAL_IDENTIFIED` 且对全部真值点 0 反例）与 `best_effort_exact`（`PARTIAL_FIT`）分开统计，**后者永不算正确**。假认证率不升是安全门槛，不是可优化目标。
2. **预注册 + 相对门槛**：判定规则在跑之前写死；门槛用相对比例，不用绝对点数；判定脚本读结果文件自动判，不靠人眼。现有基线：NewtonBench 预注册 `claim_exact` Compass 0.264 vs `code_assisted` 0.938 / `vanilla` 0.958；成本 1.32M vs 85k input token/正确解。
3. **成本必须可对账**：token 未进入 `budget` 表之前，**禁止宣称任何成本优势**。现状：C1 阶段 A 已落地（`53ed3d1` 记账入口 + `bfd583f` 修双重扣减），`agent_tokens` 成为第一个能从账本重算的资源，`remaining = cap - drawn - reserved` 里 token 只减一次。每正确解成本已由 `381e648` 落进结果文件。**仍缺**：token 三分类（input / cached / output 分价），折扣口径保持 `UNKNOWN`。
4. **瓶颈先归因**：改预算前必须先证明瓶颈是预算。devset 证据：4/5 方程每 **4 次实验**就锁定，B 从 8 提到 32 结果**零变化**；唯一失败的 `feynman-I.6.20a`（需 π+exp+sqrt）实验数停在 14 不再增长——这是**合成器表达力边界，不是预算边界**。**结构性天花板已量化**：`max_nodes` 默认 7，而 40 个真解里 10 个 size > 7（`benchmark/newtonbench/results/truth-size.json`），那些方程在当前默认声明下算法上不可能被枚举到，跟预算大小无关。表达力优先于预算。
5. **饱和即停**：定律锁定后继续买探针是纯烧钱。现象：`regime-double` 上 B=12/16/24/32 → 实验数 12/16/21/21，`claim_exact` 四档都 32/32，即 `RDS(B=16/24/32)` 被 `RDS(B=12)` **自我支配**。**成本构成（2026-10-21 实测）**：B=24 那 21 次 = PROBE 15 + CONFIRM 3 + EXPERIMENT 1 + HOLDOUT 2。
   **关键更正（本版）**：饱和守卫 `_gap_wide_enough` 定义在 `scripts/rds_compass.py:421`，但**全仓没有任何调用点**（`grep -rn _gap_wide_enough --include=*.py .` 只回定义行）——`97e133c` 引入它，`832a11f` 重写决策路径时解除了调用，函数留在原地。所以：**审计说「死代码」方向是对的**，只是行号 `:468` 指错（那是 `_agent_live` 附近，`def decide` 在 `:473`）；我上一轮反驳「13 次调用全 True、不是死代码」是错的，撤回。这也解释了为什么「收紧它」实测零杠杆——它压根没被调用，不是在守卫 PROBE。
   **两条已试并回滚的修法**：① 收紧 `_gap_wide_enough` 判据（零杠杆，因为它没接线）；② 砍 basin 探针（`not probes_since`），四档全打 `SEARCH_EXHAUSTED`——探针在为 VERIFY 的唯一性确认攒区分证据，砍不得。**真正的杠杆在 PROBE 那 15 次怎么分配**，要先把守卫重新接进 `:514`/`:582` 的 `_boundary_probe` 调用链，再谈阈值。证据见 `docs/satstop-root-cause.md`。
6. **引用必须可查**：外部审计/报告的每条 claim，先在 `git archive` 干净快照上 grep 钉死再写进文档。审计给的行号常偏移或指错函数。
7. **口径改动独立复核**：任何触及统计口径的改动（阈值、容差、闸门、分类）必须带一条最小反向测试，并由独立复核子 agent 复判。防的是「为了过测试而放水」。
8. **RDS 是裁判，不是选手**。最值钱的资产是拒绝假认证、审计链、EIG 选点、等价类判别——外部生成器一个都没有。所以假设来源可以外包（PySR / LLM / CAS），RDS 负责判定它们。任何「我们自己写更强的枚举 / 更强的生成器」的提案，先回答它是否比「接一个外部生成器 + 保留裁判」在帕累托上前进更多。这条同时是 Stage 2 `hypotheses` 块存在的理由。

## 三、结构债处理（S1–S8）

结构审查发现七处问题。**关键裁定（kimi-k3）：`rds_compass.py` 拆分缓议，合并后再做。**

| 编号 | 问题 | 处置 |
|---|---|---|
| S1 | `rds_compass.py` 1491 行 / 86KB 装 8 类职责，是其依赖两个 L1 模块之和的 2.4 倍 | **合并后拆**，只按两刀（B1 agent 传输+校验、B2 呈现+TMS 镜像），纯移动 + re-export |
| S2 | token 不是一等预算资源，卡死帕累托前沿 | 立 issue（C1），优先级高 |
| S3 | fold-on-every-read 无物化层 + 20000 行硬 cap | 立 issue（C2） |
| S4 | 治理口径三方打架 + 负结果未归档 + auto-merge 开启 | **已处理**（auto-merge 于 2026-10-20 关闭） |
| S5 | RSI 工具库是账本外 sidecar；`publish_tms` 冲突即崩 | 立 issue（C3） |
| S6 | 测试按历史 PR 号命名；docs 平铺；`rds_models` 空包 | Batch A 机械处理 |
| S7 | Compass 直接 `from rds_project import`（125KB 遗留上帝文件） | 标注释，等 #325 中央服务落地 |
| S8 | 四代平行子系统（L3 SQLite / Quick 账本 / ProjectStore / Compass 事件链）；`rds_compass_declaration.py:405` 把 `compass.json` 伪装成 `mode='quick'` 借壳 | **先出收拢计划，不直接改**（风险最高） |

### 拆分的翻转条件（三条，任一满足即执行）

1. 合并完成且 `rds_compass.py` 连续两周无功能改动 → 立即拆；
2. 合并前若需第三次功能改动且单次触及 ≥3 类职责 → 先拆再改；
3. 拆分验收标准：函数体 diff 为空、公共符号全可达、focused 测试绿。

**为什么现在不拆**：会和开发 session 的未提交改动直接冲突，让未合并的 PR diff 再膨胀，且零科研收益。理由写在这里，避免下次重新论证。

## 四、优先级路线

| 序 | 事项 | 状态 | 完成判据 |
|---|---|---|---|
| 0 | **工作区清算**：`paid_query` 补登记 `LAYERS`；11 项未提交改动分批定性 | **已完成** | `test_import_graph.py` 97 OK；`git status` 干净 |
| 1 | **成本两轴可自动计算**：`experiments_per_correct` / `seconds_per_correct` 进结果文件 | **已完成**（`381e648`） | 用真实结果文件复算与手算对得上 |
| 2 | **区间观测往返**（P0）：`{'lo','hi'}` 持久化后半径不许丢 | 进行中 | 六处 LOSSY 全修；`test_rds_compass_interval` 绿 |
| 3 | **Stage 2 假设接缝**：外部生成器（PySR / LLM / CAS）的入口 | 协议 v2 已写，待接线 | `docs/stage2-hypothesis-protocol.md` 六条判据 |
| 4 | **饱和早停**：要动的是 PROBE 的预算分配优先级 | 两条修法已回滚，杠杆面已定位 | `benchmark/newtonbench/saturation-protocol.md`：`max(Exp@16,Exp@24,Exp@32) − Exp@12 ≤ 2`（基线 9）；devset 4/5 方程三档实验数 ≤6 且总 `claim_exact` ≥12/15；`feynman-I.6.20a` 不许从 `SEARCH_EXHAUSTED` 变假认证 |
| 5 | **表达力**：抬高 `max_nodes` 或换生成器（`I.6.20a`、size>7 的 10 个方程） | 已量化未做 | 40 个方程里 size>7 那 10 个至少攻下多数 |
| 6 | 结构拆分（B1/B2） | 缓议 | 见翻转条件 |
| 7 | **四代子系统收拢计划**（S8） | 只出计划 | 不动代码 |

## 五、防再犯机制（三条，必须落成可执行检查）

1. **行数闸门（CI）**：`scripts/rds_*.py` 新文件 >800 行、或改动后 >1000 行即 fail。现存超标文件进「只减不增」白名单。这条直接防 S1 类问题第二次发生——`rds_cli.py` 124KB、`rds_project.py` 125.5KB、`rds_compass.py` 86KB 都是这么长出来的。
2. **符号引用校验**：文档/评论合入前，对每条 `符号@commit` 在 `git archive` 快照里 grep，不存在则红。防「引用不存在的函数」（`_confirmation_plan` 事件）和数字漂移（52 vs 49）。
3. **负结果强制归档**：release 文档必须链接 `docs/benchmarks/` 下的负结果归档，PR 模板加 checkbox。防 NewtonBench 负结果只活在 PR 评论里、PR 一合就沉没。

## 六、不可逆风险与防护

| 风险 | 防护 | 状态 |
|---|---|---|
| auto-merge 触发合并、绕过科研增益门槛；负结果随评论沉没 | 关 auto-merge；合并条件写进 `versioning.md`；负结果归档前不合 | **auto-merge 已于 2026-10-20 关闭**；归档待做 |
| 脏工作区混提污染 HEAD（`_gap_wide_enough` 未跑 regression、fold 缓存测试未跑完） | `git status` 干净前禁止开发；每批独立提交；fold 缓存单独跑完全量测试 | 处理中 |
| 口径放水污染假认证率基线（PARTIAL_FIT 算正确、放宽容差或闸门） | 每个改动带最小反向测试；假认证率对 NewtonBench 51 次基线对照；口径改动由独立复核子 agent 复判 | 铁律 7 |

## 七、合并/发布门槛

满足以下四条之前，6.0.0 只能定位为**「可审计、可复现的执行内核」**，不得宣称科研能力提升：

1. 预注册、未见过的、不可穷举域上，正确率增益 95% 下界 > 0；
2. 假认证率不高于对照，上界 < 1%；
3. 每正确解成本 ≤ 对照 1.25 倍（或明确只宣称质量、不宣称效率）；
4. 全部负结果、无 claim、超时、预算超限、模型未调用、适配失败率公开。

**现状判定：工程可过（CI 绿 + adversarial 全绿），科研未过（NewtonBench 0.264 vs 0.958、51 次假认证）。**

## 八、不做

- 不为单任务写特化补丁（如只为 sparse 世界调参）。
- 不在无独立复核的情况下宣称「RDS 超过 baseline」。
- 不改 `scientific_support` 的 `UNKNOWN` 语义。
- 不放宽 pilot/campaign/forecast/drive 的任何超时上限来让测试通过。
- 不在 `git status` 干净之前开始任何功能开发。

## 九、每步验收

- 同模型、同预算（实验数 × token × 墙钟）对照；
- 主指标 `claim_exact`，安全项 `false_certification` 不升；
- 改动附一条复现命令 + 一条最小反向测试；
- 失败也记录进 `.harness/LESSONS.md`。

---

## 附录 A：本版剔除的伪事实

| 上一版引用 | 核实结论 | 出处 |
|---|---|---|
| 52 模块 SCC / 48 双向对 | 实为 **49 / 41**（`tools/import_graph.py` 在 160→173 五个历史版本一致输出） | handoff §3 |
| `rds_compass.py:466-478` 的 `_confirmation_plan` | 该函数**全部 git 历史中不存在** | handoff §3 |
| 「29→61 次实验」 | 出自 10-17 的**旧停止规则**，非 `spend_budget` | handoff §3 |
| 「agent 先验倒置：枚举耗尽才叫 LLM」 | 因果反了：真实原因是「枚举成功即收工」，HEAD 已改为失败也发 `PROPOSALS` | handoff §3 |
| 「表达力锁死」 | 真限制只有「RSI 只能单参数整数函数」；15 节点/200k 候选是硬上界（默认只用 7/20000）；分母 ≤8 是设计选择 | handoff §3 |
| 「合规税 Schema 22 个」 | 实为 **21** 个 | handoff §3 |
| `rds_cli.py:66-190` 含 sqlite | 不含 sqlite；物理只有 2 个 sqlite 文件 | handoff §3 |
| 「自我帕累托支配归因于 spend_budget」 | 现象成立但归因错：多出来的是 **PROBE+HOLDOUT** | handoff §3 |
| 「ingest 调 3 次 fold」 | 实为 **4 次** | handoff §3 |
| 「49 个模块组成的 SCC」 | 实为**最大 SCC 含 49 个节点**；`cycles` 只有 4 组 | 2026-10-21 `tools/import_graph.py` 实测 |
| 「`rds_compass.py:468` 饱和守卫是死代码」 | **死代码判断成立**：`_gap_wide_enough` 在 `:421`，全仓无调用点（`97e133c` 引入、`832a11f` 解除调用）。行号 `:468` 错，应为 `:421` | 2026-10-21 `grep -rn _gap_wide_enough` 实测 |
| 「`rds_cpu_jepa.py:214 _code_only` 是为应付 PR 审查」 | 有意的 fail-closed，docstring 写明理由（超参数不进特征，否则两个不同动作特征相同）。该批评的是**特征不含超参数**，不是这段该删 | 源码 docstring |
| 「`measured()` 只收精确点值」 | 过时：`exact({'lo','hi'})` 已支持区间，`tests/test_rds_compass_interval.py` 16 用例 | `93d2377` |
| 「`rds_experiments.py:96 execution_authorized: False`」 | 行号偏移，实际在 `:132-133` | 源码 |

## 附录 B：外部审计原文（两轮，保留，逐条标注裁定）

> 裁定标记：✗ = 已证伪；≈ = 部分正确；✓ = 成立。以上文附录 A 与 handoff §3 为准。

**审计对象**：`release/6.0.0-compass` 分支，`scripts/` 下 148 个 Python 模块、51,397 行代码，`tools/import_graph.py` 追踪 173 个模块、638 条依赖边。

### B.1 代码级结构性问题

**1. 四代平行子系统并存 ≈ 部分正确**
- 状态与账本（4 套）：L3 SQLite（`rds_cli.py:66-190` ✗ 不含 sqlite）、Quick 账本（`rds_quick.py`, `rds_quick_ledger.py`）、Full ProjectStore（`rds_project.py`）、Compass 事件链（`rds_compass_store.py`）—— 12 组文件都在，借壳成立。
- Advisor 引擎（4 套）：`rds_advisor.py` + `rds_advisor_search.py`、`rds_owned_advisor.py`、`rds_research_advisor.py`、`rds_compass.py` —— ✓
- Jump / 越狱引擎（3 套）：`rds_structure.py` + `rds_jump.py`、`rds_theory_progression.py` + `rds_frontier.py`、`rds_compass.py:escape()` —— ✓
- RSI / 算子演化（3 套）：`rds_rsi.py`、`rds_tools.py` + `rds_tool_workbench.py`、`rds_compass_rsi.py`（审计称 1,060 行，实为 740 行 ≈）

**2. 52 模块强连通依赖环 ✗ 已证伪**
- `tools/import_graph.py` 在 160→173 五个历史版本上一致输出 **49 / 41**，不是 52 / 48。
- 173 个模块 / 638 条边 / **21 条反向边** ✓（反向边已打破 3 条，21→18，清单见 `docs/audit-verify-structure.md §4`，其中 3 条明确不可拆）。
- 148 个 `rds_*.py` 平铺单一 `scripts/` 目录、无包层级隔离、函数体内延迟导入与动态注册表 —— ✓

**3. Compass 状态折叠 O(N²) ≈ 部分正确**
- 每步全量 fold 成立，单次 2ms→40–57ms；但 `ingest` 是 **4 次** fold 不是 3 次；`decide` 是纯函数。
- `stale_bindings` 全盘重算 SHA-256 ✓

**4. 表达力硬编码锁死在「一维玩具定律」≈ 部分正确**
- 真限制只有「RSI 只能单参数整数函数」（`rds_compass_rsi.py`）。
- `MAX_NODES=15` / `MAX_CANDIDATES=200,000` 是硬上界，默认只用 7 / 20000。
- `sys.settrace` 计步已移除（`fa2dce6`，省 6–47× 开销）✓ 已修。

### B.2 帕累托前沿审视

- **自我帕累托支配 ≈ 部分正确，归因错**：现象成立（B=12/16/24/32 = 12/16/21/21，claim 都 32/32）；但多出来的是 **PROBE+HOLDOUT**，不是 CONFIRM；`_confirmation_plan` 全 git 历史不存在；「29→61」出自 10-17 旧停止规则。
- **Agent 先验倒置（0 token）≈ 部分正确，因果反了**：0 token 属实，但原因是「枚举成功即收工」，不是「枚举耗尽才叫 LLM」；HEAD 已改为失败也发 `PROPOSALS`。
- **合规税未计入 ✓ 成立**：`cost` 只有 experiments/jumps/agent_tokens；schema 是 **21** 个不是 22。

### B.3 纲领驱动的 RSI 三层架构（未核实，作为方向参考）

| 层级 | 现状 | 演进机制 | 判定 |
|---|---|---|---|
| L1 表示层 | RSI 仅单变量整数函数 | 跨任务反统一算子蒸馏 + MDL 库学习 | 方向合理，未验证 |
| L2 先验与路由 | 固定节点数均匀枚举 | 条件先验 P(operator\|domain,residual) + VoI 路由器 | 与「饱和早停」同源 |
| L3 停机与分配 | 自我帕累托支配 | 参数化停机策略 + 帕累托超体积校准 λ | **与 P0 一致，优先做** |

准入闭环（影子回放 + 双侧统计门禁 + 自动淘汰）：方向合理，但 `r̂_lb > 1` 的检验需要先有可对账的成本账本（依赖 C1），**当前无法执行**。

### B.4 第二轮外部审计（2026-10-21）——逐条裁定

比第一轮可靠：**9 条成立、4 条需更正、1 条待核实**。

**成立的**（逐条对过源码）：

- `rds_math_router.py:22-27`：`MAX_ROWS = MAX_COLUMNS = MAX_POLYNOMIAL_DEGREE = 8`、`MAX_COEFFICIENT_BITS = 64`。
- `rds_compass_rsi.py:144`：`require(len(defs) == 1 and len(defs[0].args.args) == 1, 'Compass tools are one-argument functions')`。
- `rds_tensor_verify.py:17-19`：`MAX_RANK = 4`、`MAX_DIMENSION = 64`、`MAX_TRACE_ELEMENTS = 4096`。
- `rds_tools.py:61-64`：只准 `{math, fractions}` 两个 import。
- `rds_experiments.py:132-133`：`candidate_only: True, execution_authorized: False`（审计写的 `:96` 是偏移的）。
- NewtonBench 那组：0.264 / 0.938 / 0.958、1.32M vs 85k、22 条写不出、51 次假认证 —— 与本纲领铁律 2 同源。
- L1-a 的 `admitted = 0`；L2-a 的 5 处缺陷（已修 3 处，见 `4a5cd39`）。
- 「长任务事件只是计划、没有执行器」：成立。

**需更正的**：

1. **「49 个模块组成的 SCC」** → 实测 `cycles` 只有 4 组，`len(max_cycle)` = 49，即**最大 SCC 含 49 个节点**。把成员数写成组数，差一个量级。
2. **「`rds_compass.py:468` 的饱和守卫是永远不可达的死代码」** → **结论成立，行号错**。`_gap_wide_enough` 在 `:421`，`grep -rn _gap_wide_enough --include=*.py .` 在整个仓库只回定义行——它被 `832a11f` 解除调用后留在原地。`:468` 是 `_agent_live` 附近，`def decide` 在 `:473`。这条审计比本纲领上一版准：上一版曾反驳「不是死代码」，那是错的，已在铁律 5 撤回。
3. **「`rds_cpu_jepa.py:214 _code_only` 是为应付 PR 审查拒绝超参数」** → 有意的 fail-closed，docstring 写明：structural model 只编码 `parameters.code`，传入 `learning_rate` 会让两个不同动作的特征完全相同，所以拒绝而不是静默合并。该批评的是**特征提取不包含超参数**，不是这段该删——删了才是真 bug。
4. **「`measured()` 只收精确点值」** → 过时。`exact({'lo','hi'})` 已支持区间（`93d2377`），`tests/test_rds_compass_interval.py` 有 16 条用例。真问题是**持久化路径丢半径**，已列为路线第 2 项。

**待核实**：「48 个测试文件专门测边界拦截」这个计数未逐个数。

**本轮据审计做的改动**：`381e648`（两个 sweep 报每正确解成本）、Stage 2 协议 v2（`54d19ec`）、`comparable()` 口径收紧（`e435229`）。

**方向判断**：审计主张「生成交给外部引擎、验证留给自己」——**这一条采纳**，落成铁律 8。主张「废弃手搓合成器」——**不采纳**：RDS 的资产是裁判，不是选手；Enumerate 死掉的地方正好是 Stage 2 该接外部生成器的地方，不是该删掉整个内核的地方。

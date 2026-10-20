# RDS 开发纲领（2026-10-20 修订版）

制定：lead session + kimi-k3 决策搭档（决策责任方）。
依据：`RDS-Compass-structure-review-20261020.md`（结构审查）、`RDS-Compass-AI4S-review-20261019.md`（收益审计）、`handoff-20261020.md`（逐条核实后的口径）、devset 实验（5 Feynman 方程 × B=8/16/32）。

**本次修订说明**：上一版正文直接采信了外部审计，其中三条事实已被核实为假（`52 模块 SCC` 实为 49、`_confirmation_plan` 函数在全部 git 历史中不存在、「29→61」出自旧停止规则）。本版正文只保留已核实的事实；审计原文降为附录并逐条标注裁定。

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

## 二、七条铁律

1. **口径分离**：`claim_exact`（`GOAL_IDENTIFIED` 且对全部真值点 0 反例）与 `best_effort_exact`（`PARTIAL_FIT`）分开统计，**后者永不算正确**。假认证率不升是安全门槛，不是可优化目标。
2. **预注册 + 相对门槛**：判定规则在跑之前写死；门槛用相对比例，不用绝对点数；判定脚本读结果文件自动判，不靠人眼。现有基线：NewtonBench 预注册 `claim_exact` Compass 0.264 vs `code_assisted` 0.938 / `vanilla` 0.958；成本 1.32M vs 85k input token/正确解。
3. **成本必须可对账**：token 未进入 `budget` 表之前，**禁止宣称任何成本优势**。现状是 1.32M vs 85k 这个 15.5 倍差距由人在 PR 评论里手算，系统内自算不出来。
4. **瓶颈先归因**：改预算前必须先证明瓶颈是预算。devset 证据：4/5 方程每 **4 次实验**就锁定，B 从 8 提到 32 结果**零变化**；唯一失败的 `feynman-I.6.20a`（需 π+exp+sqrt）实验数停在 14 不再增长——这是**合成器表达力边界，不是预算边界**。表达力优先于预算。
5. **饱和即停**：定律锁定后继续买探针是纯烧钱。现象：`regime-double` 上 B=12/16/24/32 → 实验数 12/16/21/21，`claim_exact` 四档都 32/32，即 `RDS(B=16/24/32)` 被 `RDS(B=12)` **自我支配**。**根因（2026-10-20 实测，推翻早先归因）**：`scripts/rds_compass.py:468` 的饱和守卫不可达——它要求「jump 之后没有新观测」，而 basin 探针本身就会产生新观测，于是第一次买点之后条件永假。三条 PROBE 出口里承担 15/21 消耗的是 `:490`（无 `reason`、调 `_boundary_probe(state)` 用硬编码默认 `min_gap=2`）；`:536` 的 `max_gap` 覆盖规则 0 次触发，`:472` 的 `NO_RIVAL_WITHIN_BOUND` 守卫 0 次触发。证据见 `docs/satstop-root-cause.md`。
6. **引用必须可查**：外部审计/报告的每条 claim，先在 `git archive` 干净快照上 grep 钉死再写进文档。审计给的行号常偏移或指错函数。
7. **口径改动独立复核**：任何触及统计口径的改动（阈值、容差、闸门、分类）必须带一条最小反向测试，并由独立复核子 agent 复判。防的是「为了过测试而放水」。

## 三、结构债处理（S1–S7）

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

| 序 | 事项 | 完成判据 |
|---|---|---|
| 0 | **工作区清算**：`paid_query` 补登记 `LAYERS`；11 项未提交改动分批定性（提交或回滚）；`git status` 干净 | import 测试全绿；`git status` 无残留 |
| 1 | **饱和早停**（P0）：接 `:468` 那条不可达守卫（`_gap_wide_enough` 阈值收紧已实测零杠杆并回滚，不要再试） | 预注册门槛见 `benchmark/newtonbench/saturation-protocol.md`：`max(Exp@16,Exp@24,Exp@32) − Exp@12 ≤ 2`（基线 9）；devset 4/5 方程三档实验数 ≤6 且总 `claim_exact` ≥12/15；`feynman-I.6.20a` 不许从 `SEARCH_EXHAUSTED` 变假认证。达不到即回滚 |
| 2 | **Batch A**：~~A1 关 auto-merge~~（已完成）；A2 归档 NewtonBench 负结果进 `docs/benchmarks/`；A3 测试按被测对象改名；A4 docs 分层 | A2 文档入库且 release notes 有指针；A3/A4 grep 零残留 |
| 3 | **C1**：token 进入 `budget` 表，`report` 能自算每正确解成本 | 成本误差 0（与手算对账一致） |
| 4 | 结构拆分（B1/B2） | 见翻转条件 |
| 5 | **表达力**：无理常数 + 超越函数组合（`I.6.20a`） | devset 上 5/5 `claim_exact` |
| 6 | **四代子系统收拢计划**（S8） | 只出计划，不动代码 |

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

## 附录 B：外部审计原文（2026-10-20，保留，逐条标注裁定）

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

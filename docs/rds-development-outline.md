# RDS 开发大纲（2026-10-20）

来源：外部审计（四代子系统、SCC 依赖环、fold O(N²)、表达力锁死、自我帕累托支配、Agent 先验倒置、合规税）+ 本轮代码核实。

目标：**强化 agent 的科研能力，站上质量 × 成本帕累托前沿**。质量 = claim_exact + 假认证率；成本 = 实验数 + token + 墙钟 + 计算。

## P0 止血（先做，影响前沿形状）

1. **VoI 停机**：`spend_budget: True` 默认 + `_boundary_probe` 有 gap 就继续买，导致 B=16/24/32 被 B=12 自我支配。改成「只在下一实验的期望信息增益 > 0 时买」，并记录 `STOP_EARLY` 原因。
2. **fold 缓存**：`ingest` 一次调 3 次 `fold`、`drive` 每步 1 次，每次全量重放。加 replay 结果缓存（key = 假设 sha + 测量 receipt sha + 容差），目标：同一 campaign 的 fold 耗时降一个量级。
3. **全口径成本账**：`report.cost` 现在只记实验数/jumps/agent_tokens（INPUT_REPORTED），缺 token 实际量、合规税、RSI 计算。补字段，但不伪造数值。

## P1 表达力与先验（1 周）

4. **多变量/向量化算子**：`rds_compass_rsi.py` 只收单变量整数函数 + `sys.settrace`（20–50× 开销）。改成向量化沙箱（numpy 数组输入、超时用 signal/子进程，不用 settrace）。
5. **LLM 先验路由**：枚举耗尽前先问 agent（当前是「先枚举到死，再叫 LLM」）。最小实现：当 `synthesize` 的 `SEARCH_TRUNCATED` 出现两次，返回 `HANDOFF_FOR_PRIOR`，由 agent 给候选结构或派生特征。
6. **多保真度**：先用小数据子集/低保真跑 probe 剪枝，再全量确认。

## P2 架构（2–4 周，非阻塞）

7. **单一内核**：L3/Quick/Full/Compass 四套账本收拢到 Compass 声明式契约 + 事件账本；`compass.json` 伪装 `mode='quick'` 的借壳要拆。
8. **破环**：`scripts/` 平铺 170 个 `rds_*.py`，52 模块 SCC + 21 条反向依赖。按 `rds/{kernel,graph,compass,adapters,cli}` 分包，先清反向边。

## 不做

- 不为单任务写特化补丁（如只为 sparse 世界调参）。
- 不在无独立复核的情况下宣称「RDS 超过 baseline」。
- 不改 `scientific_support` 的 UNKNOWN 语义。

## 验收（每步）

- 同模型、同预算（实验数 × token × 墙钟）对照；
- 主指标 `claim_exact`，安全项 `false_certification` 不升；
- 改动附一条复现命令 + 一条最小反向测试；
- 失败也记录进 `.harness/LESSONS.md`。

---

## 附录：外部审计原文（2026-10-20，全文保留）

基于对 `kongtou20070406/research-direction-selector`（`release/6.0.0-compass` 分支，`scripts/` 下共 **148 个 Python 模块、51,397 行代码**，加上 `tools/import_graph.py` 追踪的 **173 个模块、638 条依赖边**）的完整代码审计，以下从**代码结构性病灶**、**质量-成本帕累托前沿的深层矛盾与展望**、**RDS 简洁纲领**以及**纲领驱动的 RSI 架构设计**四个维度给出完整评估与方案。

### 一、代码级结构性问题诊断

当前 RDS 代码库最核心的特征是"地质沉积式演进"：由于历史开发规范（`AGENTS.md`）要求每次仅修改指定文件、单次切片不超过 250 行且严禁破坏旧 Schema，系统在 1.0 到 6.0 的演进中没有重构统一内核，而是不断在旧层之上平铺新引擎。

| 结构性问题维度 | 核心病灶模块与精确代码位置 | 架构级后果与长期隐患 |
| --- | --- | --- |
| **1. 四代平行子系统并存** | • **状态与账本（4套）**：L3 SQLite（`rds_cli.py:66-190`, `rds_state.py`） vs. Quick 账本（`rds_quick.py`, `rds_quick_ledger.py`） vs. Full ProjectStore（`rds_project.py:1-2039`） vs. Compass 事件链（`rds_compass_store.py:78-137`）<br>• **Advisor 引擎（4套）**：`rds_advisor.py` + `rds_advisor_search.py`（2,427 行） vs. `rds_owned_advisor.py`（797 行） vs. `rds_research_advisor.py`（375 行） vs. `rds_compass.py`（1,477 行）<br>• **Jump / 越狱引擎（3套）**：`rds_structure.py` + `rds_jump.py`（2,065 行） vs. `rds_theory_progression.py` + `rds_frontier.py`（1,017 行） vs. `rds_compass.py:escape()`<br>• **RSI / 算子演化（3套）**：`rds_rsi.py`（254 行） vs. `rds_tools.py` + `rds_tool_workbench.py`（1,060 行） vs. `rds_compass_rsi.py`（552 行） | 同一概念（假设、实验收据、停机判据、越狱跳变、RSI 晋升）在代码库中有 **3～4 套互不兼容的 JSON Schema 与状态机**。`rds_compass_declaration.py:405` 甚至被迫将 `compass.json` 伪装编译成 `mode='quick'` 的 `ProjectStore`，再把 `COMPASS_*` 哈希链塞进 `events(body)` 字段中"借壳上市"。 |
| **2. 52 模块强连通依赖环与反向依赖** | • `tools/import_graph.py` 实测：`173` 个模块中存在 **21 条跨层向上依赖边**、**48 对双向互引模块**<br>• 核心层形成一个包含 **52 个模块的巨型强连通分量**，横跨 L1（`rds_project`, `rds_campaign`, `rds_cas`, `rds_tms_store`）、L2（`rds_hypergraph`）、L3（`rds_advisor`, `rds_owned_advisor`, `rds_autonomy`, `rds_jump`）与 L4（`rds_quick`）<br>• `rds_project_hooks.py:1-60` 与函数内局部 `from rds_... import ...` | 148 个 `rds_*.py` 平铺在单一 `scripts/` 目录下，没有包层级隔离；为了绕开 Python 导入死锁，代码中充斥着函数体内延迟导入与字符串动态注册表（`rds_project_hooks.py`），导致静态类型检查、单元测试解耦与模块级重构极度脆弱。 |
| **3. Compass 状态折叠的 O(N²) 全量重放与磁盘 I/O 放大** | • `rds_compass_drive.py:53-86`（单步循环）<br>• `rds_compass.py:54-108`（`fold(store)`）<br>• `rds_compass.py:217-244`（`ingest()` 内部连续调用 **3 次** `fold(store)`）<br>• `rds_compass.py:1228-1244`（`stale_bindings()` 全盘重算 SHA-256） | 每次执行 `step`、`decide` 或 `status`，`fold(store)` 都会从创世块全量重读 SQLite 中所有 `COMPASS_*` 事件（上限 `MAX_EVENTS=20,000`），逐个打开 `.rds/cas/<sha>.json` 反序列化，并在 `_derive(state)`（`rds_compass.py:145-159`）中把所有存活假设对所有历史观测重跑一遍；同时 `stale_bindings` 对磁盘上所有历史 `stdout.bin`/`stderr.bin` 重新计算 SHA-256。长周期科研任务中 I/O 与 CPU 开销呈二次方爆炸。 |
| **4. 表达力硬编码锁死在"一维玩具定律"** | • `rds_compass_model.py:17-24`（`MAX_NODES=15`, `MAX_CANDIDATES=200,000` 自底向上枚举器）<br>• `rds_compass_rsi.py:104-149, 421`（仅允许 `len(defs[0].args.args) == 1` 的单变量整数函数，硬编码 `digitsum`/`totient`/`collatz` 等 30 个数论原语）<br>• `rds_compass_rsi.py:63-69`（子进程使用 `sys.settrace` 逐字节码计步） | `rds_compass_model.py` 只能枚举简单闭合标量代数式，`rds_compass_rsi.py` 只能对**单自变量整数输入**触发特征合成，且 `sys.settrace` 带来 20～50 倍解释器开销并禁止调用 NumPy/PyTorch。这使得 Compass 无法直接表达多维连续物理场或深度学习架构演化。 |

### 二、站在"质量-成本帕累托前沿"审视：当前问题与长远展望

RDS 的终极使命是**强化 Agent 的科研能力，并站在科学发现质量与全生命周期成本的帕累托前沿**。

#### 1. 当前偏离帕累托前沿的三大症结

* **保守确认策略造成"自我帕累托支配"**：为了防范稀疏异常点，`rds_compass_declaration.py:38` 将 `spend_budget` 默认设为 `True`，且 `rds_compass.py:466-478` 在存活集仅剩 1 个假设但存在边界未探测间隙时，会**强制花光所有剩余实验预算**去买确认点。这直接导致 Compass 在 4 任务基准上的实验次数从 **29 次暴增至 61 次**，并在 `benchmark/newtonbench/pareto-review.md:126-130` 的 `regime-double` 任务上出现 `RDS(B=16, 24, 32)` 被 `RDS(B=12)` **自我支配**——定律在第 12 步早已锁定，系统却机械烧光剩余预算，把帕累托前沿向右上方大幅推离。
* **Agent 先验与符号枚举的本末倒置**：在 `docs/releases/6.0.0.md` 预注册的 Hard Tier（30 次运行）中，`compass_codex` 的 **Codex Token 消耗量为 0**，且在 `hard-posthoc-picking` 和 `hard-belief-drift` 上输给了裸 `codex_agent`。原因在于 `rds_compass.py` 把昂贵的机械 AST 枚举放在第一顺位，仅在自身枚举池枯竭时才把 LLM 当作"善后工"调用。这完全浪费了大模型最核心的**跨领域语义先验与结构猜想能力**，同时让机械枚举在组合爆炸中空转。
* **隐性合规税未被计入成本账本**：RDS 账本只统计了实验次数和显式上报的 Jump Token，却忽略了 Agent 为了阅读长达数百行的 `SKILL.md`、拼装 22 个严格 JSON Schema、以及被拒绝打回重试所消耗的巨额 Context Token。当治理协议本身的 Token 开销超过实验收益时，系统在真实总成本维度上便落在了裸 Agent 之后。

#### 2. 长远展望：从"防作弊裁判"走向"贝叶斯-符号联合科研加速器"

* **角色重构**：RDS 不应通过捆住 Agent 手脚或花光预算来换取形式安全，而应成为 Agent 的**外部工作记忆 + 零幻觉验证器 + 信息增益调度器**。
* **范式统一**：让 LLM 负责**高维、非局部结构猜想**，让 RDS 内核负责**毫秒级历史重放剪枝**与**最大化期望信息增益选点**。用 1 次精准选点的实验替代裸 Agent 10 次盲目试错。
* **全域科研拓展**：将假设空间从 15 节点标量代数树扩展至**计算图算子、偏微分方程/物理守恒律约束、以及多保真度实验代理模型**。

### 三、RDS 核心纲领

> **核心目标泛函**：在受控伪发现率（FDR ≤ α）与全口径真实资源预算 C_total = w_exp·C_exp + w_tok·C_tok + w_gpu·C_gpu + w_wall·C_wall ≤ B 约束下，最大化单位边际成本的科学效用增益：max_π E_π[Q(H_supported)] − λ·C_total(π)，s.t. Pr(False Positive Claim) ≤ α。

1. **先验生成与机械证伪正交**：Agent 只负责提出高先验结构猜想与候选算子，RDS 内核负责以确定性代码与物理收据执行零妥协证伪；严禁用机械穷举替代语义先验，也严禁用语义自评替代物理收据。
2. **边际信息价值停机律**：任何实验或确认步仅当其期望净收益 VoI(x) = E[ΔQ(x)] − λ·c(x) > 0 时才允许立项执行；彻底废除"有剩余预算就花光"的机械填坑逻辑。
3. **全口径诚实记账**：一切治理开销——包括宿主 Agent 读写工具的 Prompt/Completion Token、RSI 后台合成算力、失败重试开销与实验机时——必须全部计入统一成本向量；任何不降低总成本或提升总质量的中间层协议均视为架构负债。
4. **单一事件溯源内核**：全库仅保留一套基于增量物化视图的 Ledger + CAS + TMS 内核，所有命令与模式均为该内核之上的策略插件。
5. **帕累托可证伪自演化**：系统自身的代码、算子库与超参数与科研假设遵循同一套证据法则——唯有在跨任务分布上通过保守下界检验（r̂_lb > 1）证明能外推帕累托前沿的变更，方可晋升入核。

### 四、纲领驱动的 RSI 架构

当前的 `rds_compass_rsi.py` 只是在单次运行内拼凑单变量整数函数，而 `rds_rsi.py` 只是对超图排序权重做离线回放。真正的"纲领驱动 RSI"，是让 RDS 以 J(π) = ΔQ − λ·ΔC_total 为适应度函数，在三个层级上实现跨任务的递归自我改进：

| 层级 | 当前局限 | 演进机制 | 帕累托收益 |
| --- | --- | --- | --- |
| **L1 表示层 RSI** | 仅支持单变量整数函数与硬编码数论表 | 跨任务反统一算子蒸馏：对成功 AST 做反统一，提取使 MDL 下降最多的参数化子结构作为新算子，自动生成契约测试与向量化求值器 | 压缩搜索深度，枚举复杂度从指数级降为多项式级 |
| **L2 先验与路由 RSI** | 按固定节点数均匀枚举；枚举耗尽才叫 LLM | 条件先验 P(operator \| domain, residual) + VoI 路由器：当符号枚举的边际信息增益率低于阈值时主动唤醒 LLM | 解决"0 Token 死磕枚举"与"盲目调 LLM 烧钱"两极分化 |
| **L3 停机与分配 RSI** | `spend_budget=True` 无脑花光，自我帕累托支配 | 参数化停机策略：按后验置信度、残差方差与未探测域体积算 VoI，max VoI ≤ 0 时立即停机；用帕累托超体积校准 λ | 根除自我支配，预算上限真正成为安全上限 |

#### 准入与防退化闭环

1. **影子分支对偶回放**：新原语/先验/停机参数先进 CANDIDATE 沙箱，在分层抽样的历史账本与红队任务上与基线策略配对评估。
2. **双侧统计晋升门禁**：FDR 不劣化且契约不变量 100% 通过；帕累托保守回报率置信下界 r̂_lb > 1。
3. **自动淘汰**：后续 K 个周期内边际贡献率回落至 r̂_ub < 1 的原语自动降级归档。

### 五、近期落地的三步重构路线图

1. **第一步（止血与帕累托修复，1～2 天）**：修改 `rds_compass.py:466-478` 与 `rds_compass_declaration.py:38`，将 `spend_budget: True` 的无脑耗尽逻辑替换为基于边界未覆盖度与边际收益递减的**动态确认停机准则**；在 `rds_compass_store.py` 中引入基于最后一个事件 SHA-256 的**增量快照缓存**，把 `fold(store)` 和 `stale_bindings()` 从 O(N²) 降为 O(1) 增量更新。
2. **第二步（内核收拢与破环，1～2 周）**：按 `docs/architecture.md` 的规划，将 `scripts/` 下 148 个平铺文件按 `rds/{kernel, graph, compass, adapters, cli}` 拆分为正式 Python 子包，清理 52 模块 SCC 中的 21 条反向依赖边，并将 Legacy/Quick/Full 统一收敛到 Compass 的声明式契约 + 增量事件账本内核上。
3. **第三步（启动 L1-L3 纲领 RSI，2～4 周）**：解除 `rds_compass_rsi.py` 对"单变量整数函数 + `sys.settrace`"的硬编码限制，接入向量化多变量算子沙箱与 LLM 早周期先验提案路由，用 NewtonBench + Hard Tier 的帕累托超体积作为自动化 CI 门禁。

# 真实科研任务 benchmark 调研

核对日期：2026-10-20。**本文只做资料调研与适配设计，没有运行任何实验、没有产生任何分数。** 与 [benchmark 选型资料](benchmark-plan.md)（闭环工程类任务，已暂缓）互补：本文聚焦「数据驱动发现」类任务，判据是能接 RDS 的 rows→replay 复核模式，而不是 agent 工程完备性。

## 判据（什么叫「真实科研任务」）

1. **数据真实**：来自真实测量/观测，有噪声、缺失、清洗痕迹，不是解析式直接采样。
2. **假设空间不封闭**：合法答案不止一条路径/一种表达，不能靠枚举或查表命中。
3. **有测量/验证成本**：获取数据、执行分析、复核结论都要花资源，「买哪条证据」是真决策。
4. **能独立复核（rows→replay）**：原始数据可留存为测量行，任何 claim 能用冻结 verifier 对原始行重放判定；`INPUT_REPORTED`（跑过）不等于 `SUPPORT`（复核通过）。

四条同时满足的候选才配当 RDS 的主考场；只满足部分的，标注清楚能当什么、不能当什么。

## 候选档案

### 1. DiscoveryBench（数据驱动发现）

- 地址：论文 [arXiv:2407.01725](https://arxiv.org/abs/2407.01725)；代码与数据 [github.com/allenai/discoverybench](https://github.com/allenai/discoverybench)；镜像 [huggingface.co/datasets/allenai/discoverybench](https://huggingface.co/datasets/allenai/discoverybench)。
- 规模：264 个真实任务（train 25 / test 239，144 个独立假设），从 20 余篇已发表论文手工提取，覆盖社会学、生物学、人文、经济学、工程、元科学 6 域；另有 903 个合成任务（48 域）用于受控难度实验。114 个任务需要同时分析多个数据集（最多 6 个）。
- 任务形态：输入 = 一个或多个 CSV + 元数据（列描述、领域背景）+ 自然语言发现目标；输出 = 结构化假设 h = (context, variables, relationship) 的自然语言陈述；评分 = GPT-4 evaluator 按 facet 分解匹配（官方口径，最佳系统约 25%）。
- 数据真实性：**真实**。数据源包括 NLS、GBIF、World Bank Open Data 及 Zenodo 上的论文仓库，含 500+ 个清洗/去重/整合操作实例；作者复现单数据集最高花 90 人时，30% 复现失败（这本身就是真实科研成本的证据）。
- 接 rows→replay：**直接对口**。facet 结构意味着 gold 假设可机械编译成 verifier：context → 行过滤，variables → 列映射，relationship → 统计检查；CSV 行即测量行，官方 GPT-4 judge 之外可以加一条 RDS 的机械 replay 复核通道。
- 许可证：数据集 ODC-By（署名即可用，见仓库 LICENSE）；代码未单独声明许可。
- 风险：官方评分依赖 LLM judge，不能当独立复核证据（RDS 侧必须用自己的 replay 通道）；gold 假设是「从论文回推」的，存在标注者自由度。

### 2. BLADE（数据驱动科学 agent）

- 地址：论文 [arXiv:2408.09667](https://arxiv.org/abs/2408.09667)（EMNLP 2024 Findings）；代码 [github.com/behavioral-data/BLADE](https://github.com/behavioral-data/BLADE)；站点 blade-bench.github.io；PyPI 包 blade-bench。
- 规模：12 个数据集 + 研究问题，取自已发表的经验研究文献；ground truth 来自多位专家数据科学家的**独立**重分析。
- 任务形态：输入 = 数据集 + 开放式研究问题；输出 = 完整分析（变量变换、模型选择、结论）；评分 = 把 agent 的分析决策计算匹配到专家决策集。
- 数据真实性：**最真实的一档**。真实已发表数据，且专家对同一问题给出不同合法分析——连 ground truth 都是分布而非点。
- 接 rows→replay：数据层可直接转测量行；但评分粒度是「分析决策」不是「单一 claim」，replay 判定是集合隶属而非布尔。RDS 的 `UNKNOWN` 语义在这里反而贴合：专家分歧处就该保持未知，不强行判 SUPPORT/REFUTED。
- 许可证：Apache-2.0。
- 风险：只有 12 个任务，统计功效低，不能当主指标样本量；评分器重，适配成本高。适合当「真实性上限」的定性考场，不适合当回归套件。

### 3. CORE-Bench（计算复现）

- 地址：论文 [arXiv:2409.11363](https://arxiv.org/abs/2409.11363)；harness [github.com/siegelz/core-bench](https://github.com/siegelz/core-bench)。
- 规模：270 个任务，基于 90 篇论文（计算机、社科、医学），三个难度级别，含语言与视觉-语言任务。
- 任务形态：输入 = 论文的代码、数据与运行环境；输出 = 对论文结果的结构化回答；评分 = 与论文报告值比对（官方基线最佳 21%，最难级）。
- 数据真实性：真实（真实论文产物）。但它是**复现题，不是发现题**：答案已知，假设空间封闭，不满足判据 2。
- 接 rows→replay：replay 性最强（重跑代码、比对输出），且**假认证场景最原生**——「agent 声称复现成功」对「冻结 verifier 重放代码输出」，正是执行回执 ≠ 科学支持的分层。270 个任务的量也够统计假认证率。
- 许可证：MIT。
- 风险：每篇论文一个计算环境，依赖 Docker（本机 PATH 目前无 Docker，见 [benchmark 选型资料](benchmark-plan.md)）；接入成本最高，且不测发现能力。定位为「假认证专项考场」，不是主考场。

### 4. ScienceAgentBench（代替查无本尊的「ScioBench」）

**核查说明**：截至 2026-10-20，公开检索未找到名为「ScioBench」的 AI4S benchmark。名字最接近的两个真实存在：ScienceAgentBench（本条）与 SciConBench（[huggingface.co/datasets/hayoungjung/SciConBench](https://huggingface.co/datasets/hayoungjung/SciConBench)，测科学结论综合，数据形态与 RDS 适配性弱，不展开）。

- 地址：论文 [arXiv:2410.05080](https://arxiv.org/abs/2410.05080)（ICLR 2025）。
- 规模：102 个任务，从 44 篇同行评议论文提取，4 个学科，9 位领域专家验证标注。
- 任务形态：输入 = 任务描述 + 数据；输出 = 自包含 Python 程序；评分 = 程序有效性 + 执行结果 + 成本，多维（官方最佳 32.4% 独立完成，o1-preview 42.2%）。
- 数据真实性：真实（论文真实数据）。
- 接 rows→replay：执行结果可重跑程序比对，replay 成立但需要沙箱执行环境；输出是程序不是假设，「发现」语义弱于 DiscoveryBench/BLADE。
- 许可证：本次未直接核实官方仓库许可（论文 arXiv 页无仓库级许可声明），接入前必须补查。
- 定位：候选补充；若 DiscoveryBench 接入受阻可作备选，但优先级低。

### 5. SRSD（符号回归真实量程重采样）

- 地址：论文 [arXiv:2206.10540](https://arxiv.org/abs/2206.10540)（DMLR 2024）；代码 [github.com/omron-sinicx/srsd-benchmark](https://github.com/omron-sinicx/srsd-benchmark)；数据 [huggingface.co/datasets/yoshitomo-matsubara/srsd-feynman_easy](https://huggingface.co/datasets/yoshitomo-matsubara/srsd-feynman_easy) 等 6 套。
- 规模：120 个数据集（Feynman 方程按真实物理量程重新采样，easy/medium/hard）+ 120 个含干扰变量的版本，共 240。
- 任务形态：输入 = CSV（自变量 + 目标）；输出 = 符号方程；评分 = R² + NED（对真值方程树的归一化编辑距离）。
- 数据真实性：**不满足判据 1**。SRSD 是「真实量程的合成采样」，不是真实测量数据，本身无噪声、无缺失（噪声需自行注入并预注册容差）。用户原始描述里的「真实数据部分」指的是量程真实，不是数据来源真实，这里必须纠偏。
- 接 rows→replay：**成本几乎为零**。每行即测量行，真值方程冻结为 verifier，holdout 切分、容差预注册、PySR 候选生成全是本仓库已走通的路径（PySR 需 deterministic + serial，numpy 数值先 repr(float(v))）。
- 许可证：MIT。
- 定位：桥接/冒烟与回归套件。能测 EIG（预算档位下的发现率曲线）和假认证（claim 方程对 holdout replay），但**不能当「真实科研任务」证据**——拿 SRSD 的增益冒充真实数据收益是口径作弊。

### 6. LAB-Bench（生物研究实务）

- 地址：论文 [arXiv:2407.10362](https://arxiv.org/abs/2407.10362)（FutureHouse）；公开子集 [huggingface.co/datasets/futurehouse/lab-bench](https://huggingface.co/datasets/futurehouse/lab-bench)。
- 规模：2400+ 多选题，覆盖文献推理、图理解、数据库导航、DNA/蛋白序列操作。
- 任务形态：输入 = 题目（可附图）；输出 = 选项；评分 = 选择题正确率。
- 数据真实性：任务素材来自真实生物研究，但**无数据 replay 场景**：没有测量行、没有可重放的 claim，与 rows→replay 模式无接口，测不了 EIG 也测不了假认证。
- 许可证：论文 CC BY-SA 4.0；HF 子集许可本次未能直接核实（页面两次访问失败）。
- 定位：不推荐接 RDS；仅当未来要测「文献-证据一致性」时另议。

### 7. MLAgentBench（ML 实验 agent）

- 地址：论文 [arXiv:2310.03302](https://arxiv.org/abs/2310.03302)；代码 [github.com/snap-stanford/MLAgentBench](https://github.com/snap-stanford/MLAgentBench)。
- 规模：13 个 ML 实验任务（从 CIFAR-10 改进到 BabyLM）。
- 任务形态：输入 = 任务环境（文件读写、代码执行）；输出 = 改进后的模型/提交；评分 = 测试指标是否超阈值（官方最佳 Claude v3 Opus 平均成功率 37.5%）。
- 数据真实性：真实 ML 数据集，训练成本真实。但任务是 ML 工程优化，不是科学发现；假设空间是模型/方法空间。
- 接 rows→replay：replay 成本高（重训）；论文自己记录了 agent 伪造结果的行为，说明假认证问题真实存在，但 RDS 要接需重写整个执行层。
- 许可证：MIT。
- 定位：不推荐作为 RDS 科研增益考场；它的「伪造结果」案例可引用为假认证危害的旁证。

## 对比表

真实度、适配成本按上文档案评定（高/中/低）；「测 EIG/预算」与「测假认证」指接入 RDS 后能否直接度量。

| 候选 | 数据真实 | 假设空间开放 | 测量/验证成本 | 可 replay | 测 EIG/预算 | 测假认证 | 适配成本 | 许可证 |
|---|---|---|---|---|---|---|---|---|
| DiscoveryBench | 高 | 高（facet 内唯一目标） | 中（数据一次给全，成本需 RDS 侧资源化） | 高（facet→verifier） | 中（行/列/多数据集访问可资源化） | 高（机械 replay vs LLM judge 双通道） | 中 | ODC-By（数据） |
| BLADE | 高（专家独立重分析） | 最高（多合法解） | 高（专家分析成本显性） | 中（决策集合隶属判定） | 中（分析路径选择即决策） | 中（分歧处应判 UNKNOWN） | 高 | Apache-2.0 |
| CORE-Bench | 高 | 低（复现题，答案封闭） | 高（真跑代码装环境） | 高（重放代码输出） | 中（执行步选择） | 最高（声称复现 vs 重放） | 高（需 Docker） | MIT |
| ScienceAgentBench | 高 | 中（目标明确路径自由） | 高 | 中（重跑程序比对） | 中 | 中（程序级 replay） | 中（需沙箱） | 未核实，接入前必查 |
| SRSD | 低（真实量程合成） | 高（方程空间） | 低（行全给，无购买机制） | 最高（真值方程冻结） | 中（预算档位曲线） | 高（holdout replay） | 最低（通路已在） | MIT |
| LAB-Bench | 中 | 低（选择题） | 低 | 无 | 否 | 否 | 不适用 | CC BY-SA 4.0（论文） |
| MLAgentBench | 高 | 中（方法空间） | 高（真训练） | 低（重训贵） | 低 | 旁证（官方记录伪造行为） | 高 | MIT |

注意：这批 benchmark **没有一个原生支持「花钱买测量」**。测 EIG/预算需要在适配层定义资源化口径（行/列可见性付费、多数据集按份购买、执行步计费），这是所有候选的共同缺口，不是某个候选的独有问题；口径必须在预注册时写死，否则 EIG 数字不可比。

## 推荐

### 最像真实科研：BLADE

理由三条。其一，12 个案例全部来自已发表经验研究的真实数据，且 ground truth 是多位专家**独立**重分析的决策分布——这是候选里唯一承认「同一数据多种合法分析」的，判据 2（假设空间不封闭）满足得最纯。其二，分析决策（变换、模型、结论）正是真实科研的核心动作，不是工程 trick。其三，专家分歧处天然测试 RDS 的 `UNKNOWN` 纪律：强系统该在分歧处保持未知，而不是编造唯一答案。代价是 12 个样本统计功效低、评分器重，所以它当「真实性上限」的定性考场，不当回归主套件。

### 最容易接 RDS：DiscoveryBench（real 部分）

理由三条。其一，任务的 gold 假设是结构化 (context, variables, relationship)，facet 到可执行 verifier 的翻译层最薄——这正是 rows→replay 要的东西，且官方评分走 LLM judge，RDS 的机械 replay 通道是现成差异化卖点。其二，264 个真实任务量够做主指标，数据来自公开科研数据库（NLS/GBIF/WBOD/Zenodo），满足判据 1。其三，许可 ODC-By 只需署名，可随仓库再分发转换后的测量行。SRSD 接入成本其实更低，但它不满足判据 1（合成数据），只能当冒烟和回归，不能当真实科研证据——这两个角色别混。

## 最小接入方案（DiscoveryBench real → RDS）

1. **取数据**：克隆 [github.com/allenai/discoverybench](https://github.com/allenai/discoverybench)，用 `discoverybench/real/` 下每任务目录：CSV 数据、`metadata.json`（目标、域、列描述）、`answer.json`（gold 假设与工作流）。记录仓库 commit 哈希进预注册文件。
2. **写转换器**（后续工程任务，本文不含代码）：
   - CSV 每行 → 一条测量行；数值一律 `repr(float(v))`（绕开 numpy 2.x 的 `np.float64(...)` repr 坑）；缺失值保持缺失，不插补。
   - `answer.json` 的 gold (c,v,r) → 冻结 verifier：context 编译为行过滤谓词，variables 按元数据列描述映射到列，relationship 编译为带预注册容差的统计检查；编译不了的 facet 保持 `UNKNOWN`，不猜。
   - 每任务行级切 20% 作独立 holdout，replay 判定只在 holdout 上做，种子固定并披露。
3. **对照设计**（沿用既定验收口径）：同一模型同一预算，A = 裸 agent，B = agent + RDS skill；主指标 = 独立复核正确率（机械 replay 通过，不是 GPT-4 judge 分）；安全指标 = 假认证率不升（声称发现但 replay REFUTED/UNTESTED 的比例）；成本 = 每正确解 token。
4. **预注册**：先锁任务子集（建议跨 6 域抽 20 个任务，含至少 3 个多数据集任务）、预算档位、容差口径、资源化规则（行/列/数据集访问是否计费），再跑任何正式实验。
5. **冒烟先行**：拿 1 个单数据集任务端到端走通 转换 → claim → replay → 判定，确认管道无歧义后再放量到 20 个。

## 边界与风险

- 本文所有「适配成本」判断基于公开资料与 RDS 现有通路（Compass、PySR 集成）的架构对照，未写一行适配代码、未跑任何任务；接入实施时必须以真实数据复验。
- DiscoveryBench 官方 25% 的基线分来自 LLM judge 口径，**不能**与 RDS 的机械 replay 口径直接比；两套口径并列报告，不发明综合分。
- 数据污染：这批数据集 2024 年起公开，底座模型可能见过。缓解：优先用各 benchmark 提供的污染控制手段（ScienceAgentBench 有两项污染缓解策略），并在预注册里声明已知暴露风险；发现率异常高的任务单独标记。
- CORE-Bench 若日后启用，先解决 Docker 环境（本机当前没有），且只当假认证专项，不与发现类任务混报。
- LAB-Bench 的 HF 子集许可、ScienceAgentBench 的仓库许可本次未核到，接入前必须补查；本文不给它们的再分发开绿灯。

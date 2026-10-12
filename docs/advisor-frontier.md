# Advisor：从证据断口生成新提案任务

核验日期：2026-10-01。`discover_frontier(spec)` 将已声明的研究目标、来源记录和有限量纲约束转换成开放提案任务，必要时生成有类型的乘积 AST。这里的输出是**新生成提案任务**，不是只查询一个 `UNKNOWN` fact，也不是已经成立的新理论。AI 仍须提出关系、假设、预测、实验和正负结果下的下一行动；程序保留来源、不确定性、作用域与资源边界。

## 最接近的近期工作与采用边界

本次核验三个 primary 来源，非穷尽综述。较新的 [SciLaws-Bench，arXiv v1，2026-09-01](https://arxiv.org/html/2609.01552v1) 将真实记录与平行世界分开，区分记忆、预测拟合、科学有效性和候选选择；其结论指出更大的候选池可包含更好的定律，而模型未必选中。本地采用“生成与选择分开验收”的原则，没有复现其结果或导入其 judge。

[NewtonBench，最新核验 arXiv v3，2026-02-24 / ICLR 2026](https://arxiv.org/html/2510.07172v3) 用改变经典规律的模拟任务减轻记忆问题，并要求交互探测；它不能为著名历史题提供无污染保证。[DiscoveryBench，ICLR 2025](https://openreview.net/pdf?id=vyflgpwfJW) 将数据、元数据与发现目标明确化，并按发现环节评价。本地仅借用输入/任务/评分边界，不把图断口命中率当成这些工作的科学发现得分。

## 输入、时间与来源

入口为 `scripts/rds_frontier.py` 的 `discover_frontier(spec)`，或：

```powershell
python scripts/rds_cli.py advise --frontier benchmark/advisor-frontier/magnet-conductor.input.json
python benchmark/advisor-frontier/run.py
```

`spec.schema_version` 为 `1`，可声明 ISO 日期 `as_of`。`nodes` 包含稳定 `id/kind/source`，可附 `dimensions`；`edges` 包含 `from/to/relation/status/source`，其中 `status` 是 `SUPPORTED/PROPOSED/CONTRADICTED`。`goals` 用已有现象的 `target`、非空 `anchors`、会改变的 `decision` 和来源定义研究问题；没有任务锚点的任意缺边不自动成为推荐。可选 `goal.relations` 限定可达关系；未限定时只能作一般图拓扑解释。

`observations` 用 `model/node/observed/predicted/tolerance/protocol` 绑定实测与模型预测；没有可靠同口径数字时不伪造残差。历史 fixture 的 `observations` 为空，只使用原文可核验的定性现象。`dimension_requests` 指定目标 node ID、变量 node IDs、决策与来源；目标维度从目标节点读取，目标本身不能兼作输入变量。`limits` 限定 gap 数、节点、边、组合、幂次与非零项数。每条 source/protocol 元数据最多 1024 UTF-8 字节；输出引用只保留定位字段，避免同一大段元数据被大量候选重复复制。

使用 `as_of` 时，每条决定性记录须声明 `available_on`。未来记录剔除并记录在 `excluded`；日期缺失不能假装当时可用。出版日与研究者已经观察到信息的日期是两件事。[来源目录](../benchmark/advisor-frontier/sources.json) 分开保存 `publication_date`、`available_on`、日期精度、摘要和段落定位。前两例明确使用追述重建日期；日期近似不会因为写成 ISO 字符串而变成精确史实。提供 URL 或 `SUPPORTED` 标签只声明来源身份，不会独立认证科学主张。

## 图可达性能够说明什么

图只沿有效、截止时点可用且状态为 `SUPPORTED` 的有向边遍历；如目标限定了关系类型，再应用该过滤。对于每个 goal，从其 anchors 分别检查是否已有通往 target 的路径，生成带断口端点和来源引用的 `MISSING_BRIDGE`。它指向缺少可审查连接的研究问题，不为所有节点两两补边，也不把某条新边直接写回判断图。

可达性取决于节点粒度、边方向、关系含义及录入是否完整。路径可达不证明因果识别；路径不可达也不证明自然界不存在关系。把 `contains/observed_with/explains` 全混为一种路径会改变问题，因此历史解释任务只允许 `explains` 路径。输入中保留可核验的组成关系、观察关系和现行解释身份，而不是人为增加一个“后来理论”节点，再要求补齐通往它的边。

`MODEL_FAILURE` 则要求有来源、协议和明确数字的模型/观察不一致；它不能由故事中的“模型不够好”自动推出。`TRANSFER_GAP` 比较显式源/目标作用域中的运行、指标或时域等键，指出尚未支持的迁移；缺失作用域仍是未知。同一个局部测试通过，不等于在不同执行代码、长链或目标指标下成立。所有这些断口都要求新提案，不能自行把假设变成实测证据。

输出 gap 包含 `anchors/target/question/why/evidence_refs/required_proposal_fields`，状态为 `OPEN`，科学支持保持 `UNKNOWN_SCIENTIFIC_SUPPORT`。成本未知时保持 `cost.status=UNKNOWN`。这些字段提供审查入口，不是建议成功率、科学优先级或自动运行授权。

## 主动拓宽理论路线

可选 `goal.reformulation` 声明 `current_model`、`reason`、`source` 与 `available_on`，请求 `THEORY_REFORMULATION`。它保留原目标与下一决定；即使图已经可达、没有数值残差，也能请求更宽的表述，不要求先失败三次。当前模型必须是可用节点；请求及新候选的来源、时间、输入大小与枚举上限仍受检查。`as_of` 下来源日期缺失或来自未来不能进入当时的路线，截断不表示没有其他理论。

回复的 `proposal.theory_bridge` 只必需 `candidate_model/mapping/source`；当前模型、原目标和已有前提可复用，补充条件按需声明。`kind` 可省略，默认 `unspecified`；四种常见关系只是示例，也允许其他短标签，不强迫分类或排名。明确声明 `approximation` 时仍须填 `domain/error_bound`。候选必须实际进入当前模型到原目标的提案路径；不能附一个孤立新概念，借旧路径声称已拓宽。理论映射与解释/预测边分开，不自动合成包含层级。

可选 `goal.reformulation.signals` 为相关理论工具的查询标签，如 `trajectory_degradation/local_global_gap`。程序只在可用的改写 gap 上附最多三张[预载工具](theory-reformulation.md#按需查预载工具与可运行算子脚手架)摘要卡；它是 TAG_MATCH_ONLY，前提尚未检查，不自动分配 GPU 或执行。`as_of` 早于工具目录可用日期时不使用该目录，保持历史证据边界。

经验检查保留不同预测；`test.kind=proof_check` 声明 `test.outcomes=["verified","counterexample","unresolved"]`，已核验/反例分别对应正负后续决定，未解决保持停止条件，不强制编造一个 rival 理论。定义完整仍只是 `NEEDS_EVIDENCE`：适用性 UNKNOWN、新关系 PROPOSED、执行未授权。无关的 Lean PASS 不会让理论路线进入可执行候选池。详细字段、反例与边界见[理论改写](theory-reformulation.md)。

## AI 回复与新节点

AI 可以提出原图不存在的概念，不限于已有规则动作。将回复保存为 `{"schema_version":1,"proposals":[...]}`，再通过同一输入检查：

```powershell
python scripts/rds_cli.py advise --frontier research-graph.json --frontier-proposals proposed-bridges.json
```

每条 proposal 需有唯一 `id`、当前 `gap_id`、`relations`、`assumptions`、`test`、`next_if_positive` 和 `next_if_negative`。可选 `new_nodes` 声明新 `id/kind/label`；relation 用 `from/to/relation` 连接已定义节点。默认经验模式的 `prediction` 指定可观测 node ID，以及 `if_proposal/if_rival` 两种不同预测；`test` 需说明 `protocol/measurement/stop_condition`。显式证明检查改用三类结果分支。正负结果必须对应不同的下一决定；理论改写的新节点还须保留来源及可用日期。

程序将截止时点内的原有 `SUPPORTED` 路径与新边组合，检查是否至少使用一条新边连接断口；解释任务的 `explains` 限定同样适用，纯 `related_to` 不能冒充解释。未来边、超出节点/边搜索上限的记录和自签成功字段不能关闭断口。缺定义返回 `NEEDS_DEFINITION`；结构满足返回 `NEEDS_EVIDENCE`，所有新边仍为 `PROPOSED`。它不采用规则、不修改输入、不运行实验，也不证明两段不同预测文字实际可判别。来源定位仍是 `INPUT_REPORTED`，需要原始记录导入和适用条件核验才能加强证据。

## 真实使用记录带来的改进

本次另对用户提供的真实研究档案做只读回放，原始文件与回放结果留在本地。记录中出现了“冻结状态的一步操纵通过，但正式训练后的长链指标未达标”，且诊断与正式任务的 runtime、metric、horizon、data role 不同。因此增加 `TRANSFER_GAP`，把缺少的迁移依据显式转成新提案任务，而不是沿用局部通过标签推进训练。

同时观察到原假说未获支持后出现另一个幅度现象，以及失败进程留下有效的局部测量。前者应建立独立待检假说，后者应分别保存运行状态、测量有效性和科研指标判断。这些记录提示下一步应优先复用原有产物、区分竞争解释；未测成本和根因保持未知。本次输入表示由开发者从原记录整理，尚未证明 Advisor 能自动从任意档案构图或得到同样诊断。

## 整数量纲约束与 AST

给每个变量一个整数维度向量 `d_i`，目标为 `d_target`，有界枚举整数幂 `a_i`，满足：

```text
sum_i a_i * d_i = d_target
expression = product_i variable_i ** a_i
```

负指数保留，零指数不进入 AST，全零向量不作表达式。缺少 `dimensions` 表示未知；显式 `{}` 才表示无量纲。只有齐次无量纲目标可以用最大公约数约去成比例的幂向量；有量纲目标必须保留准确整数向量，否则约分会改变目标单位。枚举受幂次、变量数、项数与组合上限约束，截断结果不得当作穷尽搜索。

候选 AST 的 `op=product`，每个 term 用 `node_id/exponent` 指向同一输入变量；`exponents` 与 `dimension_check` 应描述同一个对象。满足约束仅证明声明单位的乘积算术正确，不确定系数、函数族、边界条件、适用区间，也不推出因果或自然规律。候选保持 `status=PROPOSED`、`scientific_support=UNKNOWN`；不会自动获得 `SUPPORTED` 边。

现实工作例：对训练运行的吞吐量比较，先绑定同一 run 的实际处理样本数与计时区间，再请求 `sample/T` 的表达式。[throughput.input.json](../benchmark/advisor-frontier/throughput.input.json) 用显式计数单位 `sample` 和时间单位 `T` 生成 `processed-samples * elapsed-time**-1`。这里 `sample` 是工程记账单位，例子没有虚构测量值。维度一致仍不能发现“只计有效更新却除以包含空闲的时间”、重复样本计数或代码根本没实现提案等问题；需核对原始日志、实际 AST 绑定与评估协议。

自然语言提案应先转成有类型对象，再经 parser、生成器和 evaluator 使用同一变量身份、作用域、假设及实际运算。数学命题若要宣称形式化证明，须交给真实 Lean 内核并检查所证明命题是否就是执行所用命题；当前量纲枚举没有运行 Lean。经验主张须保留观测、对照、干预实现与独立确认身份；没有这些材料就保持未知，不能用 AST 或证明收据代替实证。

## 三个历史切片

| 案例与 cutoff | 输入中保留的信息 | 原始来源与日期边界 |
|---|---|---|
| 磁体/导体，1905-06-01 | 匹配相对运动下电流相同，而通常叙述按谁运动采用不同描述；目标解释已知对称现象。 | [Einstein 1905 原文](https://echo-old.mpiwg-berlin.mpg.de/ECHOdocuViewSB?mode=texttool&pn=1&url=%2Fpermanent%2Feinstein%2Fannalen%2FEinst_Zurel_de_1905%2Findex.meta&viewMode=text_image)，p.891 首段；cutoff 为明确近似重建，不声称某人该日拥有完整信息。 |
| 两病区，1847-03-01 | 1840 教学分配、交替收治、截至 1846 的病区死亡差异、现行共同环境解释；目标辨析局部流程因素。 | [Semmelweis 本人追述](https://textgridrep.org/browse/4bn26.0)，1861 题名页，原印 pp.2-6/Table I；截至 1846 信息的可用日近似设为 1847-01-01，非同期公开档案。 |
| 铁环，1831-08-29 | 绝缘双线圈、接通/断开时的短暂响应、继续接通后指针复位；目标提出连接装置与瞬态现象的机制及检验。 | [Faraday 日记](https://www.faradaysdiary.com/ws2/faraday.pdf)，Vol.I pp.367-368 §§2-9；条目有私人记录日期，1932 出版与 2008 preview 不被伪装为 1831 公开出版。 |

输入不含后来的理论、方程或预防解法。评分专用 [targets.json](../benchmark/advisor-frontier/targets.json) 才保存后来的方向与下一日记录。每个 goal 的 target 是已知现象，不是预先藏入图中的最终理论；评分只核对程序是否生成相应新提案任务，不宣称程序已经推出历史解法。Kepler 1605/Mars 候选未纳入：虽有 [1609 原本](https://www.e-rara.ch/zut/content/titleinfo/162514)，本次未充分核验早期精确知识日期，不以任意日或编造残差补足案例。

## 软件验收与真正科研验收

2026-10-01 的实际 RDS 开发执行记录：全套 402 项中 397 项通过、5 项跳过、0 项失败；收据 `SUCCEEDED`，原测试产物已导入。跳过项是未配置的两项原生 Lean、缺 PyTorch 的两项模块检查，以及 Windows 不适用的一项 POSIX 大小写路径检查。下述 4 个有限案例和 5 项负对照也通过。它们是软件验收结果，没有模型调用或科研收益评分。

[runner](../benchmark/advisor-frontier/run.py) 先将各 input 单独传给 `discover_frontier`，完成负对照调用后才读取 `targets.json`；它不把评分目标、历史解法或强模型补充候选送给生成器。量纲例的评分另从生成 AST 重算指数与维度，核对 `exponents/dimension_check` 与同一输入对象一致，不只相信 `SATISFIED` 标签。默认保存 [results/latest.json](../benchmark/advisor-frontier/results/latest.json)，包括原输入、输入 hash、完整输出、断口锚点、来源、不确定性、成本未知项和有限软件评分。它不调用模型，不运行科研实验。

负对照检查：没有 goals 的缺边不推荐；删除 goal 来源后，手写 `verified/SUPPORTED` 不能自签通过；未来的 `SUPPORTED` 桥接边被剔除；未检验 `PROPOSED` 桥接边不能关闭断口；量纲候选仍是 `PROPOSED`，并且原输入不被修改。明显解法字符串检测只作小型泄漏检查，不保证没有语义泄漏。

这三个著名历史答案可能早已在同一模型训练资料中。因此这里只能声称**时间切片的软件组件挑战**，不能证明普通人或一般模型完成了未知科学发现，也不能证明完整重建了真实历史认知。图中的 goal 与表示本身仍由人类整理；断口命中说明约束执行正常，不能测量生成质量、低排名救回、真实收益或投入优势。

要验证“同一 AI 加 Advisor 促成跳跃”，需另用冻结后新任务或防记忆任务，给两组相同模型、证据与规则内容，并比较同一总预算的完整轨迹：分别记录过滤前的生成池、共同池上的排序/保留与默认截断线以下的救回；用外部实验、独立确认或真实形式证明核验推进。历史目标匹配和模型自评分不能代替这一步。

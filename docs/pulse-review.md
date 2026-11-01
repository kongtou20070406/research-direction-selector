# 单点脉冲候选生成器 core 准入审查

审查日期：2026-10-20。工作区：C:/Users/XU/rds-357；分支：release/6.0.0-compass；起始 HEAD：72c1d73b2b6c045e27b3ee114264fac052536742。
审查开始时，scripts/rds_compass.py 的 PULSE_PATCH 是已存在的未提交改动，不能仅用起始 HEAD 重现审查；交付复核时 HEAD 已由其他操作推进至 c37e738d150fada8cd9b835e9d05358a1cc6baff。
期间五个请求审查文件的内容哈希均未变化，本文结论绑定文末快照；其他操作产生的 Pareto 结果变化不属于本次审查或修改。

**结论：需改。当前实现不批准进入 core。** 单输入整数域上的候选构造可保留为 benchmark 机制探针；现有 core 草案尚未落实整条规则最多一个脉冲、独立复测和冻结 holdout 等准入条件。建议先补齐闸门，再评审受限、显式启用的模板；现有证据不证明科研收益。

本次只新增本文档。未改实现、测试或其他文档；未运行 campaign、购买实验、合并或发布。

## 1. 实现正确性：构造和计数基本成立，准入约束缺失

- `benchmark/newtonbench/pulse_patch.py:45-58` 用 exact Fraction 处理输入/观测，构造 `ite((x == v), observed, leader)`，然后调用既有 compile_hypothesis。整数 v 的等值分支和其余输入返回 leader 的语义正确；有理数观测以精确分数表达，没有浮点舍入。文本 leader 与合法 AST leader 均可用。
- `pulse_patch.py:60-72` 确实对全部传入 rows 做 replay，并以 `(反例数, point)` 排序。反例数就是 replay 返回列表的长度，没有在此截断；不是只测被修补的那一行。
- `scripts/rds_compass_model.py:550-585` 的反例单位是“观测行 × 被预测 observable”的失败单元；重复观测分别计数，不等于不同输入点数。该 helper 只预测一个 observable，所以当前测试里等于失败行数。
- 零反例不等于全量通过：未知容差、缺失输出或 undecided 可以不产生反例。实际诊断中，传入只含 z 的 tolerances，得到 `counterexamples_count=0, status=UNTESTED, tested=0`。准入须同时核对 status、tested 和 unknown/undecided，不能只读 count。
- `pulse_patch.py:40-58` 对每个已见整数点生成候选，并未先找 leader 的失败点；32 个候选是 32 个独立假设，不代表单个假设有 32 个脉冲，但会增加搜索成本和重复候选。
- `tests/test_pulse_patch.py:24-65` 的 sparse-mid 正例成立：leader 仅在 x=20 预测 21、观测为 1000；最佳候选 replay 32/32，其余候选各保留这一个反例。本次该测试通过。

**helper 的边界问题（P2）**：`pulse_patch.py:28-34` 只在未指定 variable 时检查第一行单输入；显式 variable 可绕过检查，二维域的 `x==v` 是一个截面，不能称单点。应校验所有行的输入键。`pulse_patch.py:56` 将完整整数写入 id，合法的 512-bit 范围内大整数也可能超过 compile_hypothesis 的 80 字符 id 上限；本次 x=10**80 复现 `ValueError: Invalid hypothesis id`。应使用有界、内容绑定的 id，保留完整数值在表达式中。重复点的 seen_points 采用首条观测，应明确冲突/容差政策；非整数点目前跳过，不能宣传为通用实数域生成器。

`pulse_patch.py:34` 在 tolerances 缺失或为空时默认为零容差，适合作为此显式精确合成探针的约定；不能原样带进 core 覆盖“容差未声明即 UNKNOWN”的合同。core 应继续复用 `_tolerances` 的结果。

## 2. 红队闸门落实情况

依据：`docs/pulse-patch-redteam.md:13-20`。K=1、5% 都是待冻结的保守政策，不是经验最优阈值。

| 闸门 | benchmark helper | 当前 core 草案（rds_compass.py:652-671） | 判断 |
|---|---|---|---|
| 整条候选最多一个脉冲 | 每次只包一层；未检查 leader 已有脉冲 | 每次只包一层；未检查 AST/祖先 | 未落实；可累计两个以上 |
| 脉冲数 ≤ floor(训练点数 × 5%) | 无 | 无；3 个点也接受候选 | 未落实；按该政策少于 20 点应禁用 |
| 冻结 leader 的训练反例恰为 1 | 无 leader replay；0 或多个反例均生成 | 检查恰为 1，但读取全局 replay | 部分落实；数据分区和 observable 口径不对 |
| 只从训练集选 v、observed | 使用调用者传入的全部 rows | 非 escalate 过滤 rows，但反例来自全局状态；escalate 会包含 holdout | 未落实 |
| 独立复测 v，另有未见输入 holdout | 无对应接口/流程 | 沿用一般确认/holdout，未要求 v 的独立复测 | 未落实 |
| 冻结后独立 verifier 复核；失败不回灌 | 训练 replay 使用同一个 model 模块 | 接受前 replay 全部 measured；逃逸可再用 holdout 拟合 | 不足以满足独立性 |
| 复杂度、候选消耗和适用界声明 | 排序只用反例数/point | 记录总 size、mode、parent 和 accepted；无 pulse attempted/政策界 | 部分落实 |

“记录了 PULSE_PATCH”与“闸门已实施”是两件事。当前 `rds_compass.py:670-671` 已写 origin/mode 和 notes，`681-690` 也保留 parent；这部分无需重复添加。

## 3. 阻断准入的具体发现

**P1：holdout 参与候选构造，并有可复现的 StopIteration。**

`rds_compass.py:145-154` 的 hypothesis replay 来自所有 measured 行。`600-601` 在普通拟合时剔除 holdout，`655-659` 却继续从全局 replay 选 counter，再在过滤后的 rows 中 next 查找它。若唯一反例来自 holdout，且前面的有界 whole/residual 搜索无解，就查不到该行并抛出 StopIteration；这是合法受限搜索下的真实分支，不应靠增加搜索预算规避。

相同状态使用 `escalate=True` 时，`600` 不再隔离 holdout，能把该反例的观测值写入脉冲并记 `accepted=True`。`escape:950` 正是这样调用 _fits。这说明旧 holdout 已成为搜索证据，不能再充当这个新候选的独立验证集。即使训练 witness 合法，`667-668` 以全部 measured 的 replay 决定是否输出候选，也让 holdout 进入筛选；它应位于候选冻结后的验证阶段。

最小修正：对 pulse 单独构造固定训练分区，在该分区按目标 observable 重算 leader replay，v 与 observed 只取该训练 witness。不把全局 REFUTED 状态或其他 observable 的失败单元直接当训练失败。保留全局 fold 的真实状态，不改写它来迎合生成器。失效/已用于搜索的 holdout 不得复用；没有新的已声明独立证据时，只保留待验证候选并阻止 exact claim。

**P1：一层包装不能落实“一条规则最多一个脉冲”。**

`rds_compass.py:652-662` 和 `pulse_patch.py:54` 都接受已有 ite 的 leader。本次用 leader `ite(x==20,1000,x+1)`，再加入 x=21→2000 的观测，helper 与 core 都产生含两个 ite 的零反例规则。对后续新证据重复此过程即可逐点记忆。

最小修正：检查完整 AST 及现有 parent/provenance，而非只统计本次新加节点。首轮可保守地拒绝已有 ite/比较型例外或 pulse 祖先的 leader；更精细的计数必须识别完整规则中的单点例外，不能只看 origin 标签。同时实施 5% 的训练覆盖政策，固定分母和重复观测口径。

**P1：现有确认机制不能替代红队要求的异常点独立复测。**

`rds_compass.py:479-482` 购买预留 holdout；`1247-1307` 复核 receipts、replay、证书和 novel confirmations。这些保障仍需保留，但没有强制“冻结候选后独立复测 v，且另有未见点”，没有为 pulse 单独判定 holdout 是否已污染。`1297-1300` 的 claim 主要看 replay 与确认数；一般确认点即使全过，也可能完全没有再测 v。

本次只复现了候选筛选问题，**没有据此宣称已复现 GOAL_IDENTIFIED 或假认证**。结论是 pulse 的专门验证闸门缺失，现有 verifier 不能由训练 replay 的成功推导出测量例外可重复。

`OUT_OF_SCOPE_DISCOVERY: scripts/rds_compass_declaration.py:193 - 当前声明拒绝重复输入，且 :201 仅允许 IMPROVE 使用 replicates；单输入 IDENTIFY_LAW campaign 无法直接预留同一 v 的独立复测。` 本轮不改该文件；后续需另行评审既有独立 campaign/原始 receipt 如何接入冻结 verifier，或明确授权一个最小声明扩展。不能拿失败重试槽、旧 receipt 或自动重买代替独立复测，也不增设账本。

**P2：模板能力和搜索成本未进入声明界。**

`rds_compass.py:660-671` 可在仅声明 add、max_nodes=1 的搜索下产生含 eq、ite、观测字面量的规则，且 notes 没有 pulse attempted。现有 residual/regime 模板本来也可有组合开销，因此不能机械要求“最终总 size ≤ max_nodes”；需要明确 pulse 是哪个已授权模板、其部件/总节点界、数据字面量和候选消耗如何计费。全局 64 节点合法性上限不等于此模板已被声明允许。

最小修正：保留总 AST size，显式冻结模板启用和预算，记录一次生成/一次 replay 的实际消耗及搜索分区。不凭找到漂亮候选而扩 grammar、提高节点/时间/实验上限。

## 4. 最小接入位置与“反例 ≤2”

候选生成的局部位置是 `rds_compass.py:651` 的 RESIDUAL_CORRECTION 之后、`672` 的 REGIME_SPLIT 之前。当前工作树已经放在这里；应收紧这段草案，不再新增第二个入口，也不让生产 scripts 导入 benchmark 模块。

**≤2 可以是粗筛，不应成为单脉冲的接受门槛。** 首轮按红队政策只接受 `e_leader=1`；0 个反例不生成，2 个不同输入的失败用一个单点补丁无法全修复。若为性能采用 `1≤e_leader≤2` 预筛，进入构造前仍须满足冻结的 K=1，并以完整训练 replay 拒绝残余错误。将两个重复测量单元重新解释成一个异常点，也需要预先固定计数政策，不能看到结果后放宽 K。

建议的最小 diff 仅作描述，未应用：

| 位置 | 建议修改 |
|---|---|
| `rds_compass.py:_fits:597-603,652-671` | pulse 使用永久隔离的训练行；以该目标的训练 replay 选冻结 leader；先过滤 eligibility，再按失败数/size/id 确定选择，避免全局最佳者不合格时跳过其他合格者。保留 not found 的回退语义。 |
| 同一 pulse 分支 | 要求已声明适用域、K=1、总脉冲≤1、5% 上限、模板/候选预算；只生成唯一训练 witness 的候选；无许可/缺证据则记录跳过原因。首轮建议限制单输入整数域，连续域另审邻域验证。 |
| 同一 pulse 分支 | 训练 replay 用原始 observable 容差；要求可判定覆盖完整、零反例。notes 保留 `PULSE_PATCH`，补 base、训练 witness receipt、attempted、训练结果、总 size/脉冲数和待验证原因；`accepted` 仅指训练筛选。 |
| `rds_compass.py:_hypothesis_from_fit:681-690` | statement 前置“含单点例外”，保留完整可执行公式、parent、origin；复用既有 HYPOTHESIS/DECISION/JUMP 的 CAS/hash-chain 路径，不新增状态存储。 |
| `rds_compass.py:verify:1276-1307` | 从冻结规则、既有事件和原始 receipts 重算 pulse 验证条件；独立复测/未见点证据缺失、过期、污染或失败时阻止成功 claim。沿用已有 claim 枚举及 UNKNOWN，不新增未追踪的成功状态。 |
| 后续另行授权的声明/复核范围 | 冻结 opt-in、复杂度/候选预算及独立复测方案；先解决 :193 的声明限制。若不能完整落地，这一轮只保留 benchmark，不以 20 行候选补丁冒充已完成 core 准入。 |
| `tests/test_pulse_patch.py`；必要时既有 drive 测试 | 增加下面列出的负例和一次有真实 receipt 的冻结复核测试；只跑覆盖修改的 1–2 个模块。 |

只改 _fits 可以完成“生成一个待验证候选”的局部工作，不能完整交付红队规定的 core 准入。无需修改 exact AST 求值器，也无需建立新 scheduler、ledger 或自认证工具。

## 5. claim 必须说明单点例外；复杂度按实际 AST 计

**需要显式说明。** 现有 statement 默认写完整公式，读者仍可能只注意主规律；应把“含单点例外”放在前面，报告例外点/值、总脉冲数、容差、训练覆盖、独立复测及未见点 holdout 状态。可以表述为：

> 候选规则含单点例外：x=20 时 y=1000，其余输入 y=x+1。训练 replay 为 32/32（abs=rel=0）；异常点独立复测与未见点 holdout 尚未提供，规则待验证；scientific_support=UNKNOWN。

这是完整的分段候选，不能缩写成无例外的 `y=x+1`。独立复核通过也只允许既有受限 claim；未测输入仍为 PREDICTED_NOT_MEASURED，不能宣称真实机制或外推已恢复。PARTIAL_FIT 仍是 refuted 的 best effort，不进入正确 claim 计数。

`scripts/rds_compass_model.py:165-166,527-530` 按完整 AST 递归计节点。非负整数 v、c 都是单节点时：

```text
size(ite(eq(x,v), c, L)) = 1 + (1+1+1) + 1 + size(L) = size(L)+5
size(x+1) = 3；size(ite(x==20,1000,x+1)) = 8
```

本次实测 observed=1/2 时解析后总 size=10，observed=-1000 时为 9；应以最终编译 AST 为准，不能固定“只加 2–3 个节点”。`docs/symbolic-exception-survey.md:71` 的估计与当前实现不符。

现有 size/prior 可以继续使用；另并列报告 n_pulse、例外字面量 v/c 的编码长度或位数、候选尝试和测量成本。节点数不体现“从数据抄入一个超长常数”的全部复杂度。若采用红队的额外 λ 惩罚，须预注册并冻结系数，未声明即 UNKNOWN，不临时发明默认权重。

## 6. 更安全的路线：复用两步残差合成，验证仍不能省

既有 `rds_compass.py:645-651` 已调用 `model.synthesize(...,base=leader)`；`rds_compass_model.py:814-821,901-910` 已实现“计算 observed−leader，再返回 leader+correction”。因此两步残差合成已有入口，无需重建合成器。

建议先冻结 L，再对原始训练点的残差 r(x)=y−L(x) 做一次有界搜索，优先寻找可迁移的结构（奇偶性、边界、已有 domain feature 等）；仅在单个可判定残差失败点且所有闸门满足时，提出稀疏例外候选：

```text
δ = observed_v − L(v)
h(x) = L(x) + ite(x==v, δ, 0)
例：L=x+1，v=20，δ=979；等价替换式是 ite(x==20,1000,x+1)。
```

这种表达把“修正幅度来自哪里”写清，也更容易发现多个残差实际上来自一个结构性规律。最终仍对原始 y、原始容差做完整 replay；不能直接用残差值重新缩放相对容差。可执行式若正规化为替换式，保留残差来源的既有事件证据即可。

**它只在搜索和解释上更便于审计，不天然更能泛化。** 单点残差依然可能是噪声；仍禁止堆叠、删除反例或把 holdout 回灌。两种式子仅在 L(v) 有定义且可判定时按上述 δ 等价；L=1/x、v=0 时残差无法定义，不能声称通用等价。显式加法式也要按全部节点计数，不能隐藏 correction 的成本。

### 调研中需要校正的依据

- `docs/symbolic-exception-survey.md:20-21,72` 把 DRSR 写成逐层残差修正。核对所列 2026 版本的第 4 节与 Algorithm 1，方法是按残差行为描述符维护 QD archive，并进行遗传搜索/系数优化；不能作为上述两阶段加法算法的直接出处。两阶段方案应标为基于现有 base 接口的工程选择，DRSR 仅启发保留不同残差解释。[DRSR 原文](https://arxiv.org/html/2605.15809v1#S4)
- `survey:59` 的“任意比例稀疏 corruption 都能精确恢复”超出原文条件；论文针对特定多项式动力系统、遍历/采样等假设下的恢复，不能推出静态单点例外应被视为真实定律。[Tran–Ward 原文](https://arxiv.org/abs/1607.01067v2)
- `survey:65` 从“极端稀疏”直接推出 RDS 落入 RPCA 理论安全区，没有建立矩阵结构及恢复条件；只能作分解思路的类比。[RPCA 原文](https://arxiv.org/abs/0912.3599)
- `survey:75` 的“验证端不改、假认证率不会上升”无证明。未见点若全不命中 v，只验证了 L 的其他位置，无法检验被记忆的异常观测；本次 holdout 回灌诊断更说明需要单独闸门。该结论不能支撑 core 准入。

这些是对所列原始来源的定向核查，不是新的效果实验；本轮未改调研文档。

## 7. 本次验证与后续验收

已执行 `python -X utf8 -B -m unittest discover -s tests -p 'test_pulse_patch.py' -v`：**1 个测试通过**。未运行全套测试。

另外只在内存中调用现有函数，未写 fixture、未启动实验子进程。core 诊断固定 grammar={variables:[x],constants:[1],operators:[add]}、max_nodes=1、max_candidates=8、probes=[]；这些是用于暴露分支的明确受限搜索条件，没有提高任何预算。最小 state 按观测重算 replay，不具有真实 kernel receipt，故证据限于代码行为。

| 诊断输入/调用 | 实际输出 |
|---|---|
| x=0…31，L=x+1，x20→1000，helper | 32 候选；最佳 x20，0 反例，size=8 |
| x=-2→1/3、x=0→1；文本和 AST leader | 两者均生成正确精确式并 CONSISTENT |
| L 原本零反例；helper | 仍生成 32 个候选，说明未设适用门 |
| x20→1000、x21→2000，L=x+1；helper | 仍生成 32 个；最佳仍有 1 个反例 |
| leader 已含 x20 脉冲，再观察 x21→2000 | helper 与 core 均生成两个脉冲的零反例式；core size=13、accepted=True |
| 唯一失败 x20 被标为 holdout；普通 _fits | 前置有界搜索无 fit 时抛 StopIteration |
| 同一状态，_fits(escalate=True) | 使用 x20 观测生成补丁，PULSE_PATCH.accepted=True |
| 仅 3 个训练点，其中一个例外，holdout=[] | core 仍接受候选，未实施 5%/复核门 |
| helper 的 tol 只有 z；或 x=10**80 | 分别得到 UNTESTED/0 tested/0 反例；Invalid hypothesis id |

后续批准前至少验证：零反例不启用；两个不同失败点不兜底；嵌套/祖先脉冲被拒绝；不足 20 点按冻结政策禁用；holdout-only witness 不崩溃且不被拟合；异常点独立复测失败否决 exact claim；holdout 被搜索使用后失去独立资格；篡改 receipt 仍 fail closed；声明的候选、节点、时间和实验预算不被放宽。

科研收益另需同模型、同信息/工具、同总预算的冻结未见任务对照，报告独立复核正确率、假认证率和每正确 claim 成本。上述一个单测和内存诊断不满足该条件，scientific_support 继续 UNKNOWN。

## 8. 审查快照

以下 SHA-256 绑定本次读取的五个请求文件，行号均针对该工作树：

| 文件 | SHA-256 |
|---|---|
| benchmark/newtonbench/pulse_patch.py | 48cd2fdd57db1d78174de8c478e090b45d0f99071abe3120451d381d97599768 |
| tests/test_pulse_patch.py | d89fcc81d48808ec1ebc66d5e2360d9effeb12cf54ec42b9c120761baee51edf |
| docs/pulse-patch-redteam.md | 82e05246de089dfe1efcf13497c981ce6f156b00fdfae2a8d9e5e0d039b3b161 |
| docs/symbolic-exception-survey.md | 56d32337146deed70c6c2fc8a89a6832e30da5c5491bea5edd952b74fc8a5706 |
| scripts/rds_compass.py | f2aeddde2ed8d7f83311486164a51a5de1e3559b0fc4ec5fd3fae2d6c36d31ac |

执行记录：范围＝指定候选/测试/文档及 core 调用链只读审查；发现＝3 项 P1 与上述 P2；修改＝仅本文；验证＝1 单测通过及内存反例复现；准入＝需改；科研支持＝UNKNOWN。

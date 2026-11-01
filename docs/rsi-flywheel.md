# RSI 飞轮的形式化：用 RDS 改进 RDS 自身

本文档把「RDS 用于加快 RDS 自身的研究」写成可检验的量。它定义量、口径和一个最小实验，不证明任何结论。

本次只新增本文档。未改实现、测试或其他文档；未运行 campaign、未购买实验、未合并发布。文中引用的 `file:line` 以工作区 `C:/Users/XU/rds-357` 当前内容为准，未绑定 git commit（任务未运行 git）。全文所有既有数字均转引自已提交文档并标注来源；未定的数字一律标「预注册占位」。

**scientific_support: UNKNOWN。本文档定义的每一个量都只有在按 §5 跑完并 replay 之后才可能有值；在此之前全部为 UNKNOWN。**

## 0. 一句话主张

可检验命题：**如果 RDS 的实验记录（receipt、EIG、replay、holdout）能以递增的单位成本回报，产出让 RDS 自身 claim 正确率更高的策略改动，那么两轮观测的单位成本增益比 r̂ > 1。**

边界（先说死）：r̂ > 1 只陈述「这两轮、这个任务集、这个候选池」上的回报递增。它不证明 RDS 能自我改进，更不证明存在一般意义的智能爆炸。r̂ ≤ 1 时飞轮假设在本口径下被杀死（§5.4）。

被改进的「RDS 自身」取狭义：Compass 驱动的研究决策与验证策略——选实验（EIG）、holdout、停机、合成、脉冲与 RSI 特征注入（docs/compass.md:79-157）。不含模型权重；改动必须是**可声明为 arm 的配置级改动**，这样它才能进入 IMPROVE 协议比较。

## 1. 背景：研究回报率 r

Chan et al. (2026), *What if automating AI R&D triggers an intelligence explosion?*（[arXiv:2609.36054](https://arxiv.org/abs/2609.36054)，cs.CY/cs.AI，2026-09-28 提交，Chan, Winter, Barto, Pachocki, Hinton, Horvitz, Bengio, Song, Clark, Greaves, Korinek, Hammond, Graepel, Bariach, Torr, McIlraith, Clune, Manning, Sastry, Davidson, Eth, Mindermann）用 r 表示 research 的回报率：r < 1 时单位研究努力的效果递减，r = 1 持平，r > 1 时自动化研究产出超过投入、进度自我加速。该文转引 Ho et al. (2025) 对 r 的估计 1.2–1.9（**转引，本文档未直接核验 Ho et al. 2025 原文**）。

**口径隔离声明**：Chan et al. 的 r 是宏观叙事量（自动化研究劳动对整个前沿研究进度的弹性）；本文档的 r̂ 是单系统、两轮、微观可 replay 的记账比值。两者同名不代表同量，数值不可直接比较，Ho et al. 的 1.2–1.9 在本文档中仅作数量级参照。混用这两个口径是本文档明确禁止的。

宏观 r 之所以只能叙事，是因为「研究努力」和「研究进度」都不可审计。RDS 恰好两个都有账本：实验购买有 receipt（docs/compass.md:145），claim 有 replay 与 sealed truth 裁决。这是本文档能把 r 降级成可检验量的全部前提。

## 2. 目标形式化：把「提升 claim 正确率」写成 IMPROVE 声明

### 2.1 实体映射

Compass 的 IMPROVE 协议比较报告逐样本指标的 arms（docs/compass.md:206-214）。映射如下：

| Compass 概念 | 飞轮语境 |
|---|---|
| arm（candidate） | RDS 的一个策略配置（base = 当前配置；候选 = §3 的改动） |
| sample | 任务集 T 中的一个任务（sealed 真值的世界或域） |
| observable `claim_correct` | 该配置在该任务上的严格口径裁决：提出 claim 且 replay 全域无反例记 1，否则 0 |
| experiment | harness 进程：以该配置跑完整个任务集 T，输出逐任务 `claim_correct` 与伴随审计量 |

收益口径取双轨（benchmark/newtonbench/pareto.py:83-84、pareto-review.md:8,141-142）：主口径 `claim_exact` 只认「有 claim 且闭环认证」，`best_effort_exact` 仅作工程参考。**PARTIAL_FIT 与 best_effort 解不得计入增益**。现状锚点：既有 Pareto 记录中 baseline 的 `claim_exact` 在所有任务、所有预算下恒为 0（pareto-review.md:93）——增益的起点是 0，不是某个正基线，这让「增量」的判定反而干净。

### 2.2 声明模板

按 `rds-compass-declaration-v1` 的真实字段（benchmark/compass/run.py:97-103 是既有 IMPROVE 声明样板）：

```json
{"schema": "rds-compass-declaration-v1",
 "goal": {"kind": "IMPROVE",
          "statement": "Improve strict claim correctness over the task set, with no per-task regression and no new false certifications"},
 "observables": {"claim_correct": {"tolerance": {"abs": 0}, "higher_is_better": true}},
 "profile": {"baseline": "base", "metric": "claim_correct",
             "max_degradation": 0, "min_gain": 0},
 "experiment": {"argv": ["{python}", "-B", "rsi_flywheel_harness.py", "--config", "{id}", "--tasks", "tasks_preregistered.json", "--output", "{output}"],
                "files": {"code": ["rsi_flywheel_harness.py"], "data": ["tasks_preregistered.json"],
                          "evaluator": ["rsi_flywheel_harness.py"]},
                "timeout_seconds": 7200, "wall_seconds": 7500},
 "candidates": [{"id": "base"}, {"id": "pulse_gate"}, {"id": "random_holdout"},
                {"id": "confirm_cap"}, {"id": "derived_inject"}, {"id": "budget_wide"}],
 "budget": {"wall_seconds": 21600}}
```

标注：`rsi_flywheel_harness.py` **当前不存在**。本文档只形式化；实现 harness 属于另行授权的 scope。声明里 argv 是占位，字段结构与校验规则全部对齐既有实现（profile 字段集与 `min_gain >= 0` 校验见 scripts/rds_compass_declaration.py:160-170）。

### 2.3 阈值与两条验收线

- `min_gain: 0`：IMPROVE 规则本就要求 mean gain > 0 且 ≥ min_gain（docs/compass.md:208-210），profile 只做方向门槛，统计判定交给 §4 的区间口径，不在阈值上预支结论。
- `max_degradation: 0`：任何任务从 claim 正确翻错（逐样本全额度记 degradation，docs/compass.md:214）即 `LOCAL_REGRESSION`，拒收。
- **安全伴随线（协议层，不进 profile）**：伴随审计量 `false_certifications`（提出 claim 但 replay 出反例的任务数）必须不高于 base。profile 只支持单 metric，这条线作为预注册协议约束：违反即整臂作废，不论 `claim_correct` 增益多大。正确数是用新增假认证换来的，不算改进。
- arm 可声明 `replicates`（docs/compass.md:212）。合成世界是确定性的，N=1 重放恒同（compass-yield.md 的既有口径）；若任务含随机组件（如 PySR 搜索），必须按既定经验开 deterministic=True + serial，否则 receipt 绑不住前沿，该任务不入 T。

### 2.4 双层账本纪律

外层 IMPROVE campaign 本身是一次 Compass 驱动：arm 的每次运行有 argv、输出绑定与 receipt；内层每个任务又是各自世界的 receipt 全集。两层都走 ProjectStore/CAS/TMS，不设旁账本，不设第二状态机（AGENTS.md 不变量 1）。增益裁决（sealed truth 对 replay 的判定）是纯计算，不购买实验。

## 3. 实验臂

五个候选改动。每个臂 = 一个可运行的配置差异，成本记三层（§4.1），收益按 `claim_exact`（§2.1）。既有机制证据全部来自已提交探针，均为 `INPUT_REPORTED` / `scientific_support: UNKNOWN` 的确定性单例，只证明机制存在，不证明平均收益。

| 臂 | 改动 | 代码/文档锚 | 预期机制 | 既有机制证据 |
|---|---|---|---|---|
| `pulse_gate` | 单点脉冲候选 `ite((x == v), observed, leader)` 按红队闸门接入合成：整条规则 ≤1 脉冲、v 与 observed 只取训练分区、冻结后独立复测、5% 训练覆盖上限 | docs/pulse-review.md:13,28-36 | 稀疏例外世界从「无 claim」变「有真 claim」；当前草案的闸门未落实（含 escalate 卷入 holdout 的 P1），本臂是补齐后的版本 | docs/pulse-review.md:18（sparse-mid 正例，最佳候选 replay 32/32） |
| `random_holdout` | `verification.holdout: N` 从按 declaration order 取尾改为按预注册种子随机保留，并阻断 escalate 把 holdout 当搜索证据 | docs/compass.md:154-157；holdout_seed 已在声明字段（scripts/rds_compass_declaration.py:39）；污染问题 docs/pulse-review.md:44-46 | 假认证减少、claim 更可信；随机化防「按顺序留尾」被候选排序策略利用 | docs/compass-yield.md:13（x=14 稀疏例外：economy 规则 1 次假认证/3 实验；spend_budget 1 次/12 实验；holdout: 2 零次/8 实验） |
| `confirm_cap` | 确认购买上限/EIG-per-cost 停机阈值进入默认策略（`max_confirmations` / `spend_budget` 的配置化） | docs/compass.md:157-159,37 | 同样的正确 claim，更少实验数——纯成本臂 | docs/compass-yield.md:14（64 输入域预算 20：默认 20 实验；max_confirmations: 5 用 8；max_confirmations: 10 用 13，同样正确 claim） |
| `derived_inject` | 把 qualified tool 的 derived input `<tool>_<input>` 默认注入合成 grammar（链路已存在于 RSI 流程） | docs/compass.md:118-131（尤其 :126）、rds_compass.py:587-588 | 超越函数律从 `SEARCH_EXHAUSTED` 变 `GOAL_IDENTIFIED` | docs/compass-yield.md:12（sin/cos/sqrt/exp/log 世界，开 grammar 后同等实验数下从 SEARCH_EXHAUSTED 变 GOAL_IDENTIFIED；算术世界与 RSI-tool 世界不变） |
| `budget_wide` | 预算档位扩张（`budget.experiments` 上限提高一档） | docs/compass.md:246（上限 = 2×候选数） | 更早买到反例：假认证变无 claim（安全改善），但 claim 正确数不一定升 | 既有 Pareto 记录的定性结论：B 增大让 RDS 更早测到反例（单点例外、分段边界），从假认证变成无 claim |

两个诚实标注：`budget_wide` 是**预期收益可能为零或负**的对照臂，留着它是为了测「预算是不是当前瓶颈」，不是为了刷增益；`derived_inject` 与 `random_holdout` 各自对症的任务类型不同，任务集构成（§5.1）必须避免只包含对症任务。

## 4. r 的估计

### 4.1 两轮定义与记账

一轮 = 「以当前 base 配置为基准，评估一组候选臂，按预注册规则选出至多一个臂合并为新 base」的完整循环。

- **成本 C_i（全量记账）**：轮 i 内购买的全部 receipted 测量数，包括所有臂、base 重跑、S_sel 与 S_est 上的、以及被否证的购买。三层口径：
  - 主口径 `c_exp`：receipt 数（含重试槽消耗）。这是账本原生量，可 replay。
  - 辅口径 `c_tok`：agent 自报 token（`INPUT_REPORTED`，不是测量；`USAGE_UNREPORTED` 与 `CAP_UNKNOWN` 按 docs/compass.md:108-112 口径处理，绝不把未报当 0）。
  - 辅口径 `c_wall`：墙钟（合成与 EIG 的 CPU 时间入账）。
- **增益 G_i（只在 S_est 上量）**：任务集 T 预注册划分为 S_sel（选臂用）与 S_est（估增益用），互斥、冻结。G_i = 轮 i 胜出臂相对轮 i 开始的 base 在 S_est 上的 `claim_exact` 任务数增量。配对计数：b_i = base 错而臂对的任务数，c_i = base 对而臂错的任务数，Ĝ_i = b_i − c_i。增益裁决是把已购 receipt 重放进 sealed truth 裁决器，零额外实验购买；但跑 arm 在 S_est 上的 receipt 已计入 C_i。
- 两轮预算上限预注册相同；C 用实际花费。轮 2 以轮 1 合并后的配置为新 base，候选臂可以是 §3 机制的加深版或新机制。这正是 r 要捕捉的递归：G₂ 的参照系是轮 1 的产出。

不对称是故意的：**G 只从 S_est 取，C 全量计**。只算 S_est 上的成本会把「选臂的学费」藏掉，r̂ 会虚高。

### 4.2 公式

$$\hat r = \frac{G_2 / C_2}{G_1 / C_1} = \frac{G_2 \, C_1}{G_1 \, C_2}$$

- r̂ > 1 ⟺ 单位成本增益在第二轮上升。
- **定义域条件**：仅当 G₁ 的保守下界 > 0 时 r̂ 才有意义。G₁ ≤ 0 意味着第一轮就没有可复现增益，分母为零或为负，r̂ 无定义——这本身就是杀死条件（§5.4 第 1 条），不是「r 很大」的好消息。
- **选择效应下的保守下界**。两层偏差必须处理：(a) 从 k 个臂里挑 max 的 winner's curse，让选中臂的增益估计在选臂样本上有正偏；(b) 任务集挑选偏差。(b) 由任务集预注册冻结杀死；(a) 由 split-sample 杀死：**采纳决策只准用 S_sel 上的结果与预注册规则，S_est 的任何观测不得反馈给选臂、调参或候选设计**。S_sel 上无论多好看，G 一律在 S_est 上量。
- 区间用配对翻转口径（McNemar 近似）：G 的 95% 近似区间为 (b − c) ± 1.96·√(b + c)（b + c = 0 时 Ĝ = 0，区间下界取 0、上界取保守的 3.84 ≈ (1.96)²；报告时同时给 paired bootstrap 区间）。则

$$\hat r_{\text{lb}} = \frac{G_{2}^{\text{lb}} / C_2}{G_{1}^{\text{ub}} / C_1}$$

分子取第二轮增益的区间下界、分母取第一轮增益的区间上界：两边都往保守方向取，比值不会高估 r。宣称「r > 1」的准入线是 **r̂_lb > 1**，不是 r̂ > 1。
- 成本没有选择偏差问题：C 是记账量不是估计量。

### 4.3 外生候选测的是上界

最小实验里候选臂由研究者提出（外生），不是 RDS 自己生成的（内生）。两者的关系：内生候选 ⊆ 全候选池，且内生流程额外花生成成本（jump agent token、RSI tool 的提取与验证），故 C_endo ≥ C_exo。因此外生实验测得的回报是内生飞轮可达回报的**上界**：

- r̂_exo ≤ 1 ⟹ 内生 r ≤ 1：连免费候选、只付评估成本都换不来递增回报，「RDS 用自己加速自己的研究」在本口径下不成立。杀死假设。
- r̂_exo > 1 ⟹ 仅必要条件成立：内生飞轮还差「RDS 能稳定生成这些候选且生成成本不吞掉增益」，这部分的 scientific_support 仍是 UNKNOWN。

### 4.4 口径敏感性

r̂ 依赖成本口径。若 r̂ > 1 只在 `c_exp` 口径成立、`c_tok` 或 `c_wall` 口径不成立（比如增益靠多跑 agent 调用换来），飞轮是记账口径的幻象。报告时三口径都算；**口径间结论不一致时，以最小 r̂ 为准**。

## 5. 最小可跑实验

### 5.1 任务集（防「机制-任务互相点名」）

T 预注册 n = 36 个确定性合成世界（sealed 真值），三层混合，避免只收对症任务：

| 层 | 构成 | 数量（预注册占位） | 对症臂 |
|---|---|---|---|
| 稀疏例外/分段 | 单点例外、regime 边界世界（yield_holdout 类） | 12 | `pulse_gate`、`random_holdout` |
| 超越律 | sin/cos/sqrt/exp/log 及工具缺口世界 | 12 | `derived_inject` |
| 无对症臂 | 算术可表达但搜索难的律、三变量隐藏常数类 | 12 | 无——测平均而非最优 |

划分 S_sel 18 + S_est 18（分层随机，种子预注册）。功效边界如实写：|S_est| = 18 时，b − c ≥ 7（约 8 个翻对、1 个翻错）才有 95% 区间下界 > 0；效应更小则本实验测不出，需扩 |S_est|。这是规模选择的依据，不是对效应大小的承诺。若实验结束时发现任务集构成实质上按臂定制（如对症层占比被临时调高），r̂ 作废（§5.4 第 6 条）。外部域（NewtonBench 风格，不可穷举）不进本实验：adapter 自身定位是机制探针而非 benchmark 分数（benchmark/newtonbench/README.md:30），外推问题留给后续实验。

### 5.2 轮次与预算

两轮，每轮 base + 5 臂。预算数字全部是**预注册占位**，按既有探针外推：合成世界单任务内部实验数 8–20（compass-yield.md:13-14、compass.md:168-170），36 任务 × 6 臂 × 均值 ~14 ≈ 3000 receipted 实验/轮上限；两轮合计 ≈ 6000 上限。墙钟占位 ≤ 12 h/轮（本地确定性进程为主，大头是合成与 EIG 的 CPU）。agent token 仅在启用 jump agent 时产生，声明 `agent.max_tokens` 并全程按 INPUT_REPORTED 记账。外层 campaign 自身的 budget.experiments ≤ 12（每臂一槽，docs/compass.md:246 的 2×候选数上限）。

预期输出：每臂 (c_exp, c_tok, c_wall, claim_exact 数, false_cert 数) 表；两轮 Pareto 前沿（成本 × claim 正确数）；G₁、G₂ 的配对翻转表与区间；r̂ 与 r̂_lb（三口径）；全部 receipt 可 replay。

### 5.3 什么结果支持「继续谈」

只有同时满足：G₁ 的 95% 下界 > 0；G₂ 的 95% 下界 > 0；r̂_lb > 1；false_cert 全程不升；全部增益经 replay 复现。此时结论仍然只有一句：「该任务分布、该外生候选池、两轮内，观察到递增的单位成本回报」。后续必须换任务分布重测，r̂ 才有泛化可言。

### 5.4 什么结果杀死飞轮假设

1. **G₁ 点估计 ≤ 0**：免费候选、对症任务、宽松口径下都提不动 claim 正确数。飞轮未启动，r 无定义。死。
2. **G₂ 点估计 ≤ 0 而 G₁ > 0**：增益是首轮一次性红利，边际回报归零，r̂ ≤ 0。死。
3. **r̂ > 1 但 r̂_lb ≤ 1**：区间跨 1，保守口径下无法宣称 r > 1。当前实验规模下按死处理；扩样本或加轮次是复活程序，不是本实验的结论。
4. **安全门槛破坏**：胜出臂的 false_cert 高于 base。该臂作废；若所有正增益臂都伴随假认证上升，「正确数是用假认证买的」，增益整体作废。死。
5. **replay 失效**：任一增益所依赖的 receipt 变 STALE_EVIDENCE 或不验证，该增益记 UNKNOWN 不入 G；大面积失效则整轮 UNKNOWN，实验作废重跑（不许静默 re-buy）。
6. **口径审查失败**：任务集被发现按臂定制、S_est 观测泄漏进选臂决策、或三口径 r̂ 不一致且最保守口径 < 1。r̂ 作废或按死处理。

反向的诚实条款：即便全部通过，「RDS 能自我改进」仍是 UNKNOWN（§4.3 只给了必要条件）；§5.3 的结论一个字都不许外推到未测的任务分布上。

## 6. 边界与不主张

- 本文档定义可检验的量，不运行它，不证明 RDS 能自我改进。所有量的当前值：UNKNOWN。
- 与 Chan et al. (2026) 的关系是借用变量名与三分法（r<1 / r=1 / r>1），不是借用其证据；宏观 r 与本文档 r̂ 口径不可比（§1）。
- Ho et al. (2025) 的 1.2–1.9 是转引，未核验原文，只作数量级参照。
- 引用的全部既有机制证据为确定性单例探针（N=1），`INPUT_REPORTED` / `scientific_support: UNKNOWN`，证明机制存在，不证明平均收益，更不证明收益递增。
- 全部测量与裁决走 ProjectStore/CAS/TMS 单一账本；增益裁决不购买实验；未报 token 不当 0；缺阈值的量记 UNKNOWN，绝不插值默认。
- 本文未改代码、未跑实验、未花预算；harness 与任务集实现属于另行授权的 scope。

## 参考

- Chan, A., Winter, C., Barto, A., Pachocki, J., Hinton, G., Horvitz, E., Bengio, Y., Song, D., Clark, J., Greaves, H., Korinek, A., Hammond, S., Graepel, T., Bariach, B., Torr, P. H. S., McIlraith, S. A., Clune, J., Manning, S., Sastry, G., Davidson, T., Eth, D., & Mindermann, S. (2026). *What if automating AI R&D triggers an intelligence explosion?* arXiv:2609.36054. <https://arxiv.org/abs/2609.36054>（GovAI 页：<https://www.governance.ai/research-paper/what-if-automating-ai-r-d-triggers-an-intelligence-explosion>）
- Ho et al. (2025)，r ∈ [1.2, 1.9] 的估计：转引自上文的任务背景，本文档未直接核验。
- 项目内锚点：docs/compass.md、docs/compass-yield.md、docs/pulse-review.md、benchmark/compass/run.py、benchmark/newtonbench/pareto.py、benchmark/newtonbench/pareto-review.md，行号见文中引用。

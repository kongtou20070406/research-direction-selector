# World-Model Top-Venue Vocabulary & Writing Reference

Before writing any paper section, check 2–3 accepted papers in your exact target venue and sub-field (*DreamerV3* Nature 2025, *TD-MPC2* ICLR Spotlight, *DINO-WM* ICLR 2025, *LeWorldModel* 2026, *Counterfactual World Models* 2026, *ReDU-IR* ICLR 2027 3/3/3).

Use the community's own clean words. Do not invent private jargon.

## 1. Standard World-Model Terminology Table

| Concept in our project | Accepted top-venue English term | Clean Chinese term | Avoid (internal / awkward) |
|---|---|---|---|
| Ordinary training episodes | *factual transitions* / *observational trajectories* | 事实轨迹 / 普通观测转移 | 常规轨迹、正向路径 |
| Alternative outcome query | *counterfactual prediction* | 反事实预测 | 虚拟分支推断 |
| Unknown physics / gain / wind | *hidden disturbance* / *latent environment parameters* | 隐藏环境扰动 / 隐物理参数 | 未知环境变量噪声因子 |
| `mu_theta(m, s, a)` | *action-conditioned predictor* / *latent transition model* | 条件一步转移预测器 | 盲视推演头、单步计算子 |
| Pre-trained image features | *pre-trained visual features* / *frozen patch embeddings* | 冻结预训练视觉特征 | 离线图编码特征块 |
| Mapping latent state to pose | *state readout* | 物理状态读出头 | 回归头、读出解码映射 |
| Skipping pixel generation | *decoder-free* / *in latent space without pixel reconstruction* | 免图像重建的隐空间推演 | 纯隐空间无重构版 |
| Single loss weight `lambda` | *single tunable hyperparameter* (cf. *LeWorldModel*) | 单一可调超参 | 单标量防作弊权衡系数 |
| JEPA collapse | *representation collapse* | 表征坍缩 | 隐态退化塌陷 |
| MPC / CEM action search | *action sequence optimization* / *closed-loop planning* | 动作序列优化 / 闭环规划 | 下游规划决策测试 |
| `T` update steps | *inference iterations* / *recurrent depth* | 推理迭代轮数 | 思考时间步 |

## 2. Accepted Venue Rhythm (Easy 10-Minute Read)

1. **Four-beat abstract**: Problem (2 short sentences) -> Why prior methods break (1–2 sentences) -> Our simple fix (2 short sentences) -> Headline proof + honest limit (2–3 sentences).
2. **Simple, modest claims (*ReDU-IR* lesson)**: Claim only what Table 1 and Table 2 show right on the first page. Put small component gains and mechanism probes in later ablation tables.
3. **One idea per sentence**: Active voice, subject first, `<= 22` EN words or `<= 28` ZH chars on average.

## 3. Authentic Top-Venue Framing & Phrase Bank (From Primary arXiv Sources)

Verified directly against primary text of *LeWorldModel* (`arXiv:2603.19312`), *LoopWM* (`arXiv:2606.18208`), *Huginn* (`arXiv:2502.05171`), and *Counterfactual World Models* (`external/counterfactual-WM`):

1. **Cohesive Theory-Method Voice (Zero Self-Undermining Meta-Talk)**: Never introduce a named method (`RecurrentWM`) and then apologize that its components are "merely replaceable realizations to test theory". State cleanly what theoretical bottleneck prevents prior world models from succeeding, derive the geometric conditions, and present `RecurrentWM` as the unified architecture realizing them.
2. **Authentic Motivation & Problem Phrasing**:
   - *How world models act*: *"World models learn to predict how an environment evolves in accordance with actions..."* (`LoopWM`); *"infer hidden environment dynamics from sparse observations and optimize action sequences online"*
   - *Why prior methods fail*: *"highly prone to collapse"* / *"degrade rapidly over extended horizons across rollout steps"* (`LeWM` / `LoopWM`); *"one-step teacher forcing loses parameter gradients across missing frames"*
   - *Recurrent computation*: *"iterative refinement mechanism"*, *"unrolling to arbitrary depth at test time"*, *"reusing parameters across depth"*, *"scaling test-time compute"* (`LoopWM` / `Huginn`)
   - *Planning*: *"plan via online optimization"*, *"amortized / search-free trajectory planning"*, *"dimension scaling cost of sampling search"*
3. **Banned Telegraphic Chinese Abbreviations (Never Clip Words to Beat 42-Char Limit)**: Never invent compressed pseudo-terms in Chinese manuscripts (`抑漂`, `单步教学`, `保住`, `算残差`, `长程不崩`, `越算越深`, `越算越准且长程不崩`, `打破采样维度代价`, `解出物理与动作`, `打通这两件事`, `算崩`). Always write full standard terms (*单步教师强制*, *零空间漂移抑制*, *测试期迭代推理*, *长程展开稳定性*, *采样搜索的维数代价*, *隐藏物理反演*, *闭环动作规划*) in naturally split short sentences.

# RDS 科研能力评测集扩容方案（NewtonBench-Scale）

**设计者**：大肥鱼（RDS 科研搭子）  
**归档日期**：2026-10-20  
**目标文件**：`docs/benchmark-scaling-plan.md`  
**实施约束**：本方案仅规划架构与评测协议，不修改现有执行代码；评测遵从《开发纲领》单一账本、零假认证（Zero False Certification）与失败 closed 原则。

---

## 0. 执行摘要（Executive Summary）

- **当前瓶颈**：现有开发集 `benchmark/newtonbench/devset.py` 仅有 5 个方程，单方程胜负跳变即引起 $20\%$ 的准确率抖动，统计误差半宽达 $\pm 43.8\%$，无法建立具备统计检验力的能力梯度与帕累托前沿。
- **推荐规模**：
  - **开发集（DevSet-v2）**：扩容至 **30 个方程**（置信区间半宽降至 $\pm 17.9\%$，支持敏捷调优与防过拟合检验）；
  - **正式评测集（NewtonBench-100）**：扩容至 **100 个方程**（置信区间半宽降至 $\pm 9.8\%$，双侧 $\alpha=0.05$、功效 $1-\beta=0.80$ 下可检出 $\ge 0.20$ 的微小增益，单方程翻转影响仅 $1\%$）。
- **数据源与资产**：结合 AI Feynman (100 条物理定律，MIT)、SRSD-Feynman (带干扰变量 Hard 档，Apache-2.0)、DiscoveryBench (真实科研观测行，ODC-By) 与 BLADE (开放多解真实科研，Apache-2.0)，构建从代数闭式到高维噪声干扰的梯度考场。
- **评测成本**：全量 100 方程 $\times 3$ 档预算 ($B \in \{8, 16, 32\}$) $\times 3$ 对照臂共 900 trials，RDS 符号臂耗时约 1.3 小时（4 进程并行，0 token），两 LLM 基线臂合计消耗约 6M~8M tokens，耗时约 4~6 小时。

---

## 1. 现有实现与执行机制深度盘点

在扩展前，必须严密审视现有 NewtonBench 的实现架构与数据流，杜绝概念漂移：

### 1.1 方程与语法定义（`benchmark/newtonbench/devset.py`）
- **数据结构**：返回标准化字典：
  - `rows`: 测量点列表，每点形如 `{'id': 'pt_0', 'inputs': {'x': '1/4'}, 'outputs': {'y': '0.386...'}}`。数值均通过 `rds_compass_model.text` 规范化为精确字符串（整型/有理数表达为 Python `Fraction` 字符串，如 `'1/4'`；连续浮点数表达为 `repr(float(val))`）。
  - `tolerances`: 针对输出的容差限制，支持绝对容差 `abs` 与相对容差 `rel`（如 `{'abs': '1/1000', 'rel': '1/1000'}` 或无容差精确匹 `{'abs': 0, 'rel': 0}`）。
  - `grammar`: 算子与词法空间限制，声明 `variables`、`constants` 与允许的 `operators`（如 `['neg', 'sub', 'div', 'pow', 'exp', 'sqrt', 'pi']`）。
  - `truth`: 记录物理定律的真值元数据，包含 `observable`、`expression`（规范 AST）与 `law`（用于模拟器计算的 Python 表达式）。
- **现状样本**：内置 5 个方程：
  1. `feynman-I.6.20a`（正态概率密度，含 $\exp, \sqrt{\cdot}, \pi$）；
  2. `feynman-I.9.18`（引力平方反比，$m_1 m_2 / r^2$）；
  3. `feynman-II.2.42`（热传导通量，$\kappa(T_2-T_1)/d$）；
  4. `feynman-I.12.1`（干摩擦力，$\mu N$）；
  5. `feynman-I.14.3`（重力势能，$m g h$）。

### 1.2 执行驱动与评估逻辑（`benchmark/newtonbench/devset_run.py`）
- **Campaign 声明**：遵循 `rds-compass-declaration-v1` 协议。为每个方程和预算阶梯创建独立隔离目录，写出 `experiment.py`（沙箱模拟器，通过受控 `eval(task['law'])` 吐出测量点）与 `task.json`。
- **驱动执行**：调用 `rds_compass_declaration.initialize` 初始化不可变账本，调用 `rds_compass_drive.drive(root, publish=False)` 开展主动实验循环。
- **真值判定（Ground Truth Verifier）**：
  提取 Agent 提交的主张表达式，调用 `rds_compass_model.compile_hypothesis` 编译为可执行假说，并在全量 `rows` 上执行 `model.replay`，严格统计反例数（counterexamples）。
- **主指标判定准则**：
  $$\text{claim\_exact} = (\text{status} == \text{'GOAL\_IDENTIFIED'}) \land (\text{counterexamples} == 0)$$
  任何 `PARTIAL_FIT`、`SEARCH_EXHAUSTED` 或存在反例的声称均严格判定为 `False`。
- **成本指标**：统计属于主动探测行为的步骤数：
  $$\text{experiments} = \sum \mathbf{1}(\text{action} \in \{\text{'EXPERIMENT'}, \text{'PROBE'}, \text{'CONFIRM'}, \text{'HOLDOUT'}\})$$

### 1.3 现有基准实测数据（`benchmark/newtonbench/results/devset-run.json`）
- **样本数**：5 个方程 $\times 3$ 档预算 ($B \in \{8, 16, 32\}$) = 15 runs。
- **运行特征**：
  - 4 个代数方程（`I.9.18`, `II.2.42`, `I.12.1`, `I.14.3`）在所有预算档位下均只用 **4 次实验** 就锁定真值并达成 `claim_exact=true`，耗时 4.1s ~ 52.3s 不等。说明在当前闭式代数方言内，EIG 与反例剪枝效率极高；
  - 1 个超越函数方程（`I.6.20a`）由于涉及无理常数 $\pi$ 与复合超越函数，现有 7 节点搜索树无法容纳，全部 `SEARCH_EXHAUSTED`，分别消耗 8、14、14 次实验，单 run 耗时 141.6s ~ 229.7s（占 15 runs 总耗时 802.5s 的 73.7%）。
- **暴露的核心痛点**：
  1. 样本量 $N=5$ 无法做连续帕累托曲线对比；
  2. 4 个简单题全对（上限饱和），1 个超纲题全错（下限触底），缺乏中间阶梯题；
  3. 无法体现高维变量选择（Variable Selection）与抗噪声能力。

---

## 2. 扩容规模的统计学论证（Sample Size Determination）

绝不凭感觉拍脑袋，评测集规模的设定必须建立在二项分布误差控制、最小可检测效应量（MDE）与配对检验功效分析之上。

### 2.1 比例估计的置信区间半宽（Margin of Error, MoE）
设方程总数为 $N$，评测准确率（$\text{claim\_exact}$ 比例）为 $p$。在显著性水平 $\alpha = 0.05$（双侧临界值 $z_{1-\alpha/2} = 1.96$）下，误差半宽（Marginal of Error）为：
$$E = z_{1-\alpha/2} \sqrt{\frac{p(1-p)}{N}}$$
在最大方差工况（$p=0.5$，$p(1-p)=0.25$）下，样本量 $N$ 与估计精度的关系如下表：

| 评测集规模 $N$ | 置信区间半宽 $E$（95% CI） | 单题颠倒对总分的冲击 | 统计效用评级 |
| :--- | :--- | :--- | :--- |
| **5（现状）** | **$\pm 43.8\%$** | **$20.0\%$** | **无法使用**（噪声彻底掩盖算法本质） |
| **15** | $\pm 25.3\%$ | $6.7\%$ | 极低（仅能粗略区分极端差异） |
| **30（推荐开发集）** | **$\pm 17.9\%$** | **$3.3\%$** | **开发集可用**（可敏捷迭代，检出宏观回归） |
| **50** | $\pm 13.9\%$ | $2.0\%$ | 中等（初步具备区分度） |
| **100（推荐测试集）** | **$\pm 9.8\%$** | **$1.0\%$** | **严谨科学基准**（精度控制在 $\pm 10\%$ 内） |
| **200** | $\pm 6.9\%$ | $0.5\%$ | 极高（计算与 token 成本翻倍） |

**推论**：要使准确率的 95% 置信区间收窄到 $\pm 10\%$ 以内，样本量 $N$ 必须满足：
$$N \ge \frac{1.96^2 \times 0.25}{0.10^2} \approx 96.04 \implies N \ge 100$$

### 2.2 双臂对比的最小可检测效应量（MDE）与统计功效（Power）
在评估 RDS 相对基线（如 Code-assisted Agent）的科研能力收益时，假设基线表现为 $p_1 = 0.35$，RDS 提升至 $p_2 = 0.55$（效应量 $\Delta = p_2 - p_1 = 0.20$）。
在两独立样本比例检验中（$\alpha=0.05$，检验功效 $1-\beta = 0.80$ 即 $z_{1-\beta}=0.842$），所需样本量公式（Fleiss et al., 2003）：
$$N = \frac{\left( z_{1-\alpha/2}\sqrt{2\bar{p}(1-\bar{p})} + z_{1-\beta}\sqrt{p_1(1-p_1) + p_2(1-p_2)} \right)^2}{(p_2 - p_1)^2}$$
其中 $\bar{p} = \frac{p_1 + p_2}{2} = 0.45$。代入参数：
- $\sqrt{2\bar{p}(1-\bar{p})} = \sqrt{2 \times 0.45 \times 0.55} = \sqrt{0.495} \approx 0.7036$
- $\sqrt{p_1(1-p_1) + p_2(1-p_2)} = \sqrt{0.35 \times 0.65 + 0.55 \times 0.45} = \sqrt{0.2275 + 0.2475} = \sqrt{0.475} \approx 0.6892$
- 分子项：$[1.96 \times 0.7036 + 0.842 \times 0.6892]^2 = [1.379 + 0.580]^2 = 1.959^2 \approx 3.838$
- 分母项：$(0.55 - 0.35)^2 = 0.04$
- 计算得：$N \approx \frac{3.838}{0.04} \approx 95.95$

**推论**：每臂需要约 **96 个方程**，才能在 $80\%$ 统计功效下稳定检出 $20\%$ 的性能差异。若样本量仅为 5 个，统计功效不足 $10\%$，任何正向声称在统计学上均不可信。

### 2.3 配对设计下的 McNemar 检验分析
由于三对照臂评测的是同一批物理方程，属于典型的**配对二元设计（Paired Binary Response）**。
设两模型产生不一致判定（Discordant Pairs）的概率为 $p_{\text{disc}}$。根据 Machin et al. (2008) 配对样本公式：
$$N_{\text{paired}} = \frac{(z_{1-\alpha/2}(\psi+1) + 2 z_{1-\beta}\sqrt{\psi})^2}{(\psi-1)^2 \cdot p_{\text{disc}}}$$
当胜负几率比 $\psi = 3.0$（即在有分歧的题目中 RDS 胜率占 75%），且不一致比例 $p_{\text{disc}} \approx 0.30$ 时，计算得 $N \approx 62$。
综合上述三种统计口径，**100 个方程是兼顾统计功效（$\ge 80\%$）、误差容限（$\le 10\%$）与计算预算的黄金平衡点**。

---

## 3. 可靠数据集来源与选型论证

所有数据源必须公开、可查、协议兼容，绝不自造虚假数据集。

| 数据集名称 | 论文与学术标识 | 获取方式与规范链接 | 许可协议 | 资产规模 | 在 RDS 体系中的定位与选型理由 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **AI Feynman Database (FSReD)** | Udrescu & Tegmark, *Science Advances* 2020 (arXiv:1905.11481) | [GitHub: SJ001/AI-Feynman](https://github.com/SJ001/AI-Feynman) (`FeynmanEquations.csv`) | **MIT License** | 100 个物理方程 | **核心基础题库**。直接源自《费曼物理学讲义》，解析真值确凿，物理意义与量纲严谨，支持 Python `Fraction` 精确有理数回放与闭环验证，无外部环境依赖。 |
| **SRSD-Feynman** | Matsubara et al., *NeurIPS 2022* (arXiv:2206.10540) | [GitHub: omron-sinicx/srsd-benchmark](https://github.com/omron-sinicx/srsd-benchmark) / HuggingFace | **Apache-2.0** / **MIT** | 120 个量程重采样 + 120 个含 Dummy 变量 | **抗过拟合与特征选择考场**。真实量程采样弥补了均匀网格缺陷；Hard 档注入了无关扰动变量（Dummy Variables），极其贴近真实科研中「未知参量冗余」的探索环境，用于检验 EIG 主动采样的去伪存真能力。 |
| **DiscoveryBench** | Kalyan et al., *ICLR 2025* (arXiv:2407.01725) | [GitHub: allenai/discoverybench](https://github.com/allenai/discoverybench) / HuggingFace | **ODC-By**（数据） | 264 个真实任务（25 train / 239 test）+ 903 合成任务 | **真实科研任务转化接口**。从已发表顶刊提取，含真实观测 CSV（来自 World Bank, NLS 等）。其 gold 假设以结构化 facet (context, variables, relationship) 呈现，可机械编译为独立 verifier，是验证真实科研发现的进阶考场。 |
| **BLADE** | Wang et al., *EMNLP 2024 Findings* (arXiv:2408.09667) | [GitHub: behavioral-data/BLADE](https://github.com/behavioral-data/BLADE) / PyPI `blade-bench` | **Apache-2.0** | 12 个真实经验研究数据集 | **开放多解定性考场**。由多位领域专家独立重分析构建分布基准，用于检验 RDS 在复杂开放假设空间下的边界纪律（在专家分歧处输出 `UNKNOWN` 而非强行判真）。 |

---

## 4. 正交难度分层架构（Difficulty Stratification）

为避免全部题目集中在极简或极难区间，按**自变量维度**、**算子类型**、**超越与无理特性**及**干扰变量**划分为 5 个难度层级（共 100 题）：

```
[Level 1: 基础代数与整幂次]  (20题, 20%) -> 验证基本生成与冒烟机制
[Level 2: 有理分式与负幂次]  (25题, 25%) -> 验证分母奇异点防范与有理化等价性
[Level 3: 根号与几何非线性]  (20题, 20%) -> 验证非线性边界与定义域防御
[Level 4: 超越函数与无理常数](20题, 20%) -> 验证扩展 Grammar 与常数包络
[Level 5: 干扰变量与高维鲁棒](15题, 15%) -> 验证 EIG 降噪与特征维度剪枝
```

### 4.1 各层级定义与题目示例

#### L1: 基础代数与整幂次（20 题，占比 20%）
- **特征**：自变量 $D \in [1, 3]$，仅使用加、减、乘及正整数次幂（树节点深度 $\le 5$）。
- **代表题目**：
  - `feynman-I.12.1`: $F = \mu N$（滑动摩擦力）
  - `feynman-I.14.3`: $U = m g h$（重力势能）
  - `feynman-I.12.2`: $F = q E$（电场力）
  - `feynman-I.24.13`: $E = \frac{1}{2} \omega^2 r^2$（动能）
  - `feynman-I.15.10`: $p = m v$（非相对论动量）
- **考察目标**：基准语法树遍历、最简有理数计算、冒烟自检。

#### L2: 有理分式与负幂次（25 题，占比 25%）
- **特征**：自变量 $D \in [2, 4]$，引入除法、负整幂次与分母复合项（树节点深度 $5 \sim 7$）。
- **代表题目**：
  - `feynman-I.9.18`: $F = \frac{m_1 m_2}{r^2}$（万有引力）
  - `feynman-II.2.42`: $P = \frac{\kappa(T_2 - T_1)}{d}$（热传导定律）
  - `feynman-I.26.2`: $n = \frac{c}{v}$（折射率与光速）
  - `feynman-I.27.6`: $\frac{1}{f} = \frac{1}{d_1} + \frac{1}{d_2}$（高斯透镜成像）
  - `feynman-II.11.20`: $P = \frac{n p^2 E}{3 k T}$（居里顺磁极化率）
- **考察目标**：有理分式化简、零除越界拦截、负幂次合成。

#### L3: 根号与几何非线性（20 题，占比 20%）
- **特征**：引入开方运算 $\sqrt{\cdot}$、欧氏距离与相对论修正因子（树深度 $5 \sim 8$）。
- **代表题目**：
  - `feynman-I.8.14`: $d = \sqrt{(x_2 - x_1)^2 + (y_2 - y_1)^2}$（平面欧氏距离）
  - `feynman-I.15.1`: $p = \frac{m v}{\sqrt{1 - v^2/c^2}}$（相对论动量）
  - `feynman-I.15.3x`: $x' = \frac{x - u t}{\sqrt{1 - u^2/c^2}}$（洛伦兹变换）
  - `feynman-I.34.14`: $k = \frac{\omega}{c}$（色散关系）
- **考察目标**：负数开方越界防御（Domain Guard）、非线性边界探测。

#### L4: 超越函数与无理常数（20 题，占比 20%）
- **特征**：引入 $\exp, \ln, \sin, \cos$ 算子与无理常数 $\pi$。
- **代表题目**：
  - `feynman-I.6.20a`: $f = \frac{1}{\sqrt{2\pi}} \exp(-\theta^2 / 2)$（标准正态分布）
  - `feynman-I.40.1`: $n = n_0 \exp(-m g x / k T)$（等温玻尔兹曼分布）
  - `feynman-I.18.12`: $\tau = r F \sin\theta$（力矩空间角）
  - `feynman-I.41.16`: $N = N_0 \exp(-m g h / k T)$（分子高度分布）
  - `feynman-I.38.12`: $\lambda = \frac{h}{p} = \frac{2\pi \hbar}{p}$（德布罗意波长）
- **考察目标**：扩展方言下的搜索剪枝、浮点与高精度有理数混合容差判定。

#### L5: 干扰变量与高维鲁棒（15 题，占比 15%）
- **特征**：基于 SRSD-Hard，在上述真实物理方程输入中注入 $1 \sim 3$ 个统计无关的扰动变量（Dummy Variables，如随机高斯白噪声、常数虚假相关量），维度 $D \in [4, 8]$。
- **代表题目**：
  - `srsd-hard-I.9.18`: $F = \frac{m_1 m_2}{r^2}$，伴随无关输入 $z_1, z_2$
  - `srsd-hard-I.14.3`: $U = m g h$，伴随无关输入 $w_{\text{dummy}}$
  - `srsd-hard-II.2.42`: 热传导定律加注环境温度扰动列
- **考察目标**：EIG 主动采样对无关变量的灵敏度衰减、避免虚假相关性过拟合。

---

## 5. 评测口径与对照体系设计

### 5.1 判定口径（严格分离，绝不混淆）
1. **主指标：严格科学认证（`claim_exact`）**
   - 必须同时满足两项铁律：
     1. Agent 驱动状态达到终止标志（`status == 'GOAL_IDENTIFIED'`）；
     2. 独立冻结验证回放器（`model.replay`）在全量候选集与隐藏 Holdout 点上执行比对，**反例数严格为 0**（`counterexamples == 0`）。
2. **副指标：部分拟合（`best_effort_exact` / `partial_fit`）**
   - 表达式在部分点上拟合良好但在 Holdout 出现反例，或因超算力停机给出的最佳候选。
   - **铁律**：单独披露比率，**严禁计入主指标 `claim_exact`**。
3. **安全底线指标：假认证率（`false_certification_rate`）**
   - 定义：Agent 声称发现了定律（`claim == CONSISTENT` 或 `status == GOAL_IDENTIFIED`），但被独立 verifier 检出存在反例。
   - 门槛：RDS 的假认证率必须为 **0.00%**。任何引入假认证的机制改进必须立即无条件回滚。
4. **效率指标：帕累托前沿（Pareto Frontier）**
   - 统计横轴：实验测量次数（Experiments bought）；
   - 统计纵轴：`claim_exact` 累积成功率。

### 5.2 成本度量（全三维可审计账本）
1. **实验测量数（Experiments / Observational Queries）**：调用科学环境模拟器或购买数据切片的物理调用次数（计入账本动作：`EXPERIMENT`, `PROBE`, `CONFIRM`, `HOLDOUT`）。
2. **模型 Token 消耗**：统计 LLM 对照臂的 Prompt Tokens、Completion Tokens 及 Total Tokens。
3. **计算墙钟时间（Wall-clock seconds）**：每个 run 从初始化到终止的物理耗时。

### 5.3 三对照臂（Competitive Baseline Arms）设计

| 对照臂标识 | 架构机制描述 | 实验设计与主动采样能力 | 验证与自省机制 |
| :--- | :--- | :--- | :--- |
| **Arm 1: Vanilla LLM Agent** | 纯语言模型（如 DeepSeek-V4.1-Flash）。直接在 Prompt 中给定先验说明与少量观测点，单轮或多轮思考后直接输出 LaTeX / Python 方程。 | 无主动实验能力。仅能被动接收固定样本。 | 无独立沙箱。完全依赖 LLM 内部概率生成，极易产生幻觉与假认证。 |
| **Arm 2: Code-Assisted Agent** | 具备 Python 代码执行沙箱的标准科学 Agent（ReAct 范式）。可编写脚本拟合数据，调用 Scipy / Sympy / PySR 等开源符号回归库。 | 启发式采样或均匀网格遍历。无形式化 EIG 分区决策。 | 依赖自测脚本输出，缺乏目标界定的独立 holdout 零反例证明链。 |
| **Arm 3: RDS (Compass Loop)** | 完整加载 RDS 架构。单权威账本、EIG 不相交分区决策、候选假说不可变 CAS 依赖维护、以及闭环回放器。 | **严格基于 EIG 主动设计**。每次挑选信息增益最大或能证伪当前存活假设的测量点。 | **形式化闭式 Replay**。独立验证器拦截，确保 $0$ 反例，假认证闭门绝收。 |

---

## 6. 预注册协议与防事后调参规范（Pre-Registration Protocol）

依据《开发纲领》第二章「预注册口径」执行，杜绝 Cherry-Pick 与事后过拟合：

### 6.1 预注册门槛（相对比例）
- **科学收益判据**：
  $$\text{Exact}(\text{RDS}) - \text{Exact}(\text{Code-Assisted}) > 0$$
  且在配对 McNemar 检验或双比例检验下，**95% 置信区间下界大于 0**（$p < 0.05$）。
- **安全红线判据**：
  $$\text{False Certification Rate}(\text{RDS}) = 0.00\%$$
- **效率收益判据**：
  在相同 `claim_exact` 达成率下，RDS 的平均实验步数 $\bar{E}_{\text{RDS}} \le 0.70 \times \bar{E}_{\text{Code-Assisted}}$（节省至少 $30\%$ 测量成本）。

### 6.2 试验次数规划（Trials Budget）
- 目标集规模：100 个方程；
- 预算梯度：$B \in \{8, 16, 32\}$（每个方程测试 3 档预算，观察帕累托动态）；
- 重复次数：每个配置固定 1 次确定性运行（RDS 与 Code-assisted 采用固定随机种子，Vanilla Agent 采用温度系数 $T=0.0$ 保证可复现）；
- **总试验数**：$100 \text{ 方程} \times 3 \text{ 预算} \times 3 \text{ 对照臂} = \mathbf{900 \text{ trials}}$。

### 6.3 严格防范事后调参（Anti-P-Hacking Rules）
1. **代码与协议哈希封存**：评测前将当前代码库 Git Commit 哈希、评测脚本哈希及 `compass.json` 规范哈希登记入预注册文档；
2. **参数全冻结**：搜索深度 `max_nodes`、候选集最大容量 `max_candidates`、容差参数在运行前固定，中途严禁根据部分方程结果修改；
3. **测试集盲测隔离（Holdout Blindness）**：100 个方程的真实解析式存储在隔离只读沙箱中，Agent 执行期间仅能通过 IPC 管道向模拟器传入参数并接收测量输出，绝无权限读取真实方程文本；
4. **负结果强制归档**：严格执行《开发纲领》第五章，不论结果显著与否，全量 900 trials 的原始 JSON 数据必须完整归档并计算 p 值，禁止挑选优势子集。

---

## 7. 运行成本与资源精确估算

基于当前 15 runs 实测基准（耗时 802.5s，其中 4 题代数均耗 4s~50s，1 题超越函数耗时 220s）：

### 7.1 单 Run 耗时加权测算
按 5 层难度分布测算单 Run 耗时期望：
- L1（基础代数，20%）：约 5 秒 / run
- L2（有理分式，25%）：约 20 秒 / run
- L3（根号几何，20%）：约 35 秒 / run
- L4（超越函数，20%）：约 150 秒 / run（带剪枝与超时上限）
- L5（干扰变量，15%）：约 90 秒 / run

$$\mathbb{E}[\text{Time}] = 0.20 \times 5 + 0.25 \times 20 + 0.20 \times 35 + 0.20 \times 150 + 0.15 \times 90 = 1 + 5 + 7 + 30 + 13.5 = \mathbf{56.5 \text{ 秒 / run}}$$

### 7.2 三对照臂全量评测资源消耗表

| 对照臂 | 运行规模 (Trials) | 单 Run 期望消耗 | 总墙钟时间 (Wall-clock) | 总 Token 消耗 | 预估财务成本 (CNY) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Arm 3: RDS (Compass)** | 300 runs | 56.5 秒，0 token | 单核 4.7 小时 / **4核并行 1.2 小时** | **0 tokens**（纯代数符号推理） | ¥ 0.00 |
| **Arm 2: Code-Assisted Agent** | 300 runs | 40 秒，15k input + 2.5k output | 单核 3.3 小时 / **4核并行 0.9 小时** | 4.5M input / 0.75M output | 约 ¥ 12.00 ~ ¥ 25.00 |
| **Arm 1: Vanilla LLM Agent** | 300 runs | 10 秒，4k input + 0.8k output | 单核 0.8 小时 / **4核并行 0.2 小时** | 1.2M input / 0.24M output | 约 ¥ 3.00 ~ ¥ 6.00 |
| **全量总计** | **900 runs** | — | **多核并发并发执行约 2.5 ~ 3 小时** | **约 6.7M tokens** | **< ¥ 35.00** |

**结论**：全量评测在单机 4~8 核心 CPU 与主流 API 额度下完全可在半个工作日内完成，资源预算极低且可控。

---

## 8. 分阶段落地路线图与验收判据（Phased Roadmap）

采用敏捷迭代策略，分为两阶段稳步推进：

```
[Phase 1: 敏捷验证与开发集扩容 (DevSet-30)]
  ├── 选取 30 个典型方程 (L1~L4)
  ├── 验证扩展超越函数 Grammar 与基础 Replay 流水线
  └── 验收判据: 30 题无挂死，零假认证，实测耗时受控

[Phase 2: 工业级全量基准与预注册发布 (NewtonBench-100)]
  ├── 注入 SRSD-Hard 干扰变量与 DiscoveryBench 观测行
  ├── 锁定代码哈希，跑通 900 trials 三臂盲测对照
  └── 验收判据: 生成帕累托曲线、McNemar p值检验、负结果完整归档
```

### 阶段 1：开发集扩容（MVE，DevSet-30）
- **规模与范围**：将 `devset.py` 由 5 个方程扩充至 **30 个方程**（L1: 8题, L2: 10题, L3: 6题, L4: 6题），全部采用内置离线精确 Python 生成器。
- **周期**：2 天。
- **验证重点**：
  1. 验证新增超越函数方言（$\exp, \sin$）的搜索边界与数值稳定性；
  2. 验证多变量有理分式的等价性判定（Equivalence Partitioning）；
  3. 检验单 run 超时截断机制是否生效，杜绝跑飞。
- **验收判据（Exit Criteria）**：
  - [ ] 30 个方程全部具备确定性输入输出生成器，单元测试 `python -B -m unittest discover -s tests` 100% 通过；
  - [ ] 30 个方程 $\times 3$ 档预算在本地完整跑通，无未捕获异常退出；
  - [ ] 假认证率保持绝对 0（`false_certification == 0`）。

### 阶段 2：全量评测集发布与三臂对照（NewtonBench-100）
- **规模与范围**：扩充至 **100 个方程**（包含 70 个 AI Feynman 方程、20 个 SRSD-Hard 带扰动变量方程、10 个 DiscoveryBench real 真实科研观测任务）。
- **周期**：5 ~ 7 天。
- **验证重点**：
  1. 建立测试集独立沙箱与盲测通信接口；
  2. 执行 900 trials 的全量预注册对比实验；
  3. 绘制 3 臂在不同预算阶梯下的帕累托前沿曲线（Pareto Frontier）。
- **验收判据（Exit Criteria）**：
  - [ ] 100 个任务元数据与验证器哈希锁定，测试脚本与数据独立打包；
  - [ ] 900 trials 执行回执与事件完整记录入 SQLite CAS 账本；
  - [ ] 产出正式评测报告 `docs/benchmarks/newtonbench-100-results.md`，给出精确置信区间与假设检验 p 值；
  - [ ] 严格遵循预注册条款，若 RDS 表现劣于基线，按第五章机制完整归档负结果。

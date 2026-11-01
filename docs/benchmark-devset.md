# RDS 公开 Benchmark 开发集选型与评测适配方案

## 1. 任务背景与核心动机

在当前 NewtonBench 的早期评测中，基准仅包含 3 个自造任务（`benchmark/newtonbench/pareto.py` 中的 `sparse-tail`、`sparse-mid`、`regime-double`）。虽然这 3 个任务揭示了主动探索对比均匀采样的优势，但由于开发期间已经多次观测其解析真值与边界特征，继续在其上调整 RDS Compass 的合成超参数（如 `max_nodes`、`max_candidates`、跳跃探测步长等）存在极高的过拟合风险。

为使 RDS 的科学发现能力具备客观、可复现、可推广的学术说服力，必须引入标准化、具备严格物理与数学解析真值（Ground Truth）的公开基准体系。

---

## 2. 三个候选 Benchmark 的核实结果

| 候选 Benchmark | 论文 / 提出团队 | 官方仓库地址 | 开源许可证 | 数据形态与下载方式 | 适用性与局限性分析 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Feynman Database (AI-Feynman)** | Udrescu & Tegmark (Science Advances 2020, arXiv:1905.11481) | [GitHub: SJ001/AI-Feynman](https://github.com/SJ001/AI-Feynman) | **MIT License** | 包含 100 个来自《费曼物理学讲义》的方程。带精确解析公式、变量名、量纲及参数区间。可通过 GitHub 仓库内的 `FeynmanEquations.csv` 直接获取。 | **极高**。物理定律明确，解析真值闭式确凿，非常适合用精确有理数（Fraction）与严格区间包络（Rational Enclosure）进行反例验证，无网络依赖。 |
| **SRBench / PMLB** | La Cava et al. (NeurIPS 2021) / Cranmer (2023) | [GitHub: cavalab/srbench](https://github.com/cavalab/srbench) | **GPL-3.0** (srbench) / **MIT** (PMLB) | 涵盖 120 个合成物理方程（Feynman + Strogatz 常微分方程组）及数十个真实黑盒表格。通过 `pip install pmlb` 或 GitHub 下载。 | **中等**。黑盒数据集无解析真值，无法做 100% Exact Match 闭环判定；ODE 动力系统重在数值轨迹逼近，与当前 Compass 的代数/分段定律认证偏离。 |
| **SRSD (Scientific Discovery)** | Matsubara et al. (NeurIPS 2022, arXiv:2206.10540) | [GitHub: omron-sinicx/srsd-benchmark](https://github.com/omron-sinicx/srsd-benchmark) | **Apache-2.0** | 由 OMRON SINIC X 开源，基于 Feynman 方程重构。分为 Easy/Medium/Hard，且在 Hard 档注入了无关扰动变量（Dummy Variables）。通过 HuggingFace Datasets 下载。 | **测试集极佳 / 开发集过重**。注入 Dummy Variables 极其贴近真实科研中“不知哪些参量有效”的未知探索环境，是对抗过拟合的最佳考场，但不宜在早期开发集引入。 |

---

## 3. 选型决议与论证

### 3.1 选型结论
- **开发集（Development Set）**：选 **AI Feynman Database (FSReD)**。选取 20 个覆盖代数乘除、有理分式、几何距离、指数与超越函数的经典物理方程，离线内置于本地。
- **测试集候选（Holdout Test Set）**：选 **SRSD-Feynman (Hard / Dummy Variables)**。严格隔离，开发期**不加载、不看数据、不运行任何实验**。

### 3.2 为什么选 AI Feynman 作为开发集？
1. **解析真值确凿，支持零假认证（Zero False Certification）核验**：
   RDS Compass 的核心科学原则是「不能以碰巧高分冒充科学真理」。Feynman 方程全部源自坚实的物理定律，支持基于 Python `Fraction` 的精确有理数点回放。这使得我们可以严格以 `claim_exact`（即 `status == GOAL_IDENTIFIED` 且全域测试点 100% 精确匹配）为唯一正收益指标，拒绝任何容差过松或过拟合的假定律。
2. **算子与复杂度梯度平滑**：
   Feynman 数据库涵盖从 1 维到 9 维、从单算子乘法（$F=\mu N$）到复合超越函数（$f = \frac{e^{-\theta^2/2}}{\sqrt{2\pi}}$）的丰富层级，能够清晰标定 Compass 的语法穷举边界与推理跃迁能力。
3. **离线自包含，杜绝外部网络与环境漂移**：
   数据与公式可直接内置或以静态数据文件存储，不依赖外部 API、HuggingFace 下载或复杂 C/Fortran 依赖，保障在任何离线测试沙箱中完全确定性复现。

### 3.3 为什么选 SRSD 作为测试集候选？
真实科研中，科学家往往不知道视野中的哪些变量与待测物理量相关。SRSD 在 Feynman 真实物理定律的基础上，人为加入了符合统计特征但实际无关的 Dummy Variables。如果模型在开发集上过度调优了先验变量权重，在 SRSD 上就会被彻底证伪。将其作为冻结的测试集，是检验 RDS 假设驱动探测（EIG）能否真正去粗取精、实现 AI4S 泛化增益的终极判据。

---

## 4. 开发/测试划分方案与 20 个开发集方程规划

### 4.1 20 个开发集方程清单（按难度与结构分级）

1. **基础代数与线性规律（5 个）**：
   - `feynman-I.12.1`: $F = \mu N$（摩擦力定律，乘法）
   - `feynman-I.14.3`: $U = m g h$（重力势能，三元乘积）
   - `feynman-I.12.2`: $F = q E$（静电力）
   - `feynman-I.24.13`: $E = \frac{1}{2} \omega^2 r^2$（简谐转动动能）
   - `feynman-I.26.2`: $n = c / v$（折射率与光速）
2. **有理分式与反比定律（5 个）**：
   - `feynman-II.2.42`: $P = \frac{\kappa (T_2 - T_1)}{d}$（热传导通量，差值乘除）
   - `feynman-I.9.18`: $F = \frac{m_1 m_2}{r^2}$（引力平方反比律）
   - `feynman-I.27.6`: $\frac{1}{f} = \frac{1}{d_1} + \frac{1}{d_2}$（高斯透镜成像公式）
   - `feynman-I.32.5`: $P = \frac{q^2 a^2}{6\pi \epsilon_0 c^3}$（拉莫尔辐射公式）
   - `feynman-II.11.20`: $P = \frac{n p^2 E}{3 k T}$（居里-朗之万顺磁极化率）
3. **几何距离与相对论根号（3 个）**：
   - `feynman-I.8.14`: $d = \sqrt{(x_2 - x_1)^2 + (y_2 - y_1)^2}$（二维欧氏距离）
   - `feynman-I.15.1`: $p = \frac{m v}{\sqrt{1 - v^2/c^2}}$（相对论动量）
   - `feynman-I.15.3x`: $x' = \frac{x - u t}{\sqrt{1 - u^2/c^2}}$（洛伦兹变换空间分量）
4. **统计物理与超越函数（4 个）**：
   - `feynman-I.6.20a`: $f = \frac{1}{\sqrt{2\pi}} \exp(-\theta^2 / 2)$（标准正态概率密度）
   - `feynman-I.40.1`: $n = n_0 \exp(-m g x / k T)$（玻尔兹曼等温大气压分布）
   - `feynman-I.41.16`: $N = N_0 \exp(-m g h / k T)$（分子高度分布）
   - `feynman-I.34.14`: $k = \omega / c$（电磁波矢与频率关系）
5. **复合物理动力学（3 个）**：
   - `feynman-I.13.12`: $U = G m_1 m_2 (\frac{1}{r_1} - \frac{1}{r_2})$（引力势差做功）
   - `feynman-I.18.12`: $\tau = r F \sin\theta$（力矩与空间角）
   - `feynman-I.43.16`: $v = \frac{\mu k T}{q}$（爱因斯坦扩散关系）

### 4.2 最小可用版本（内置 5 个物理方程）
在 `benchmark/newtonbench/devset.py` 中先行内置实现了 5 个经典低维物理方程，免去任何外部数据下载：
- `feynman-I.6.20a`: 单变量高斯分布（验证指数与常数 $\pi$ 包络）
- `feynman-I.9.18`: 引力平方反比（验证多变量乘方与反比）
- `feynman-II.2.42`: 傅里叶稳态热传导（验证减法与有理分式）
- `feynman-I.12.1`: 阿蒙顿摩擦力（验证基本双变量乘法）
- `feynman-I.14.3`: 均匀重力势能（验证多变量整型乘积）

---

## 5. 如何在研发过程中严格避免过拟合

1. **测试集不可见原则（Test Set Blindness）**：
   - 测试集（SRSD）数据与代码绝不进入开发环境的任何调用路径。
   - 开发期间严禁在测试集上运行任何诊断、探索或消融实验。
2. **预注册制度（Pre-registration）**：
   - 在进入测试集盲测之前，必须预先固定所有超参数：包括最大搜索节点深度 `max_nodes`、最大候选遍历数 `max_candidates`、容差字典 `tolerances`、实验预算梯级 $B \in [6, 32]$、逃逸与探测跳跃规则等。
   - 评测脚本一旦启动，中途不得根据部分结果微调规则。
3. **单一可信账本审计（Cryptographic Ledger Audit）**：
   - 所有的实验输入、输出、合成假说与验证结果必须通过 Content-Addressed Storage (`CAS`) 记录在不可篡改的事件链条中。
   - 科学正确性判定（`claim_exact`）由独立的不可变回放器判定，杜绝任何人工标注修改或事后挑选种子的作弊行为。
4. **严格的同模型同预算对照口径**：
   - 任何科学增益的汇报，必须严格建立在与 `baseline-uniform`（等量均匀采样下的经典直线/最小二乘拟合）或无 RDS 介入的标准符号回归的同预算横向对比之上。
   - 证明系统的收益来源于「基于 EIG 与反例驱动的主动实验设计」，而非单纯算力或数据量的优势。

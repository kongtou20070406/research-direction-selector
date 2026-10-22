# 符号回归处理异常点 / 单点例外 / 分段：文献调研

调研日期：2026-10-20。范围：学界对「主规律 + 单个/少量例外点」结构的处理方法，服务于 RDS Compass 的合成器改造决策。

**背景**：RDS Compass 在稀疏任务上已能发现主规律（如 `y = x + 1`），但遇到单个例外点（如 `x=20, y=1000`）时合成失败，只能回退到部分拟合（PARTIAL_FIT）。需要回答：学界如何表达与发现「主规律 + 单点/少量例外」这种结构，目标形式为 `ite(x==v, observed, leader)`。

---

## 方向一：对 outlier / exception 鲁棒的符号回归

### 1.1 ROIDS: Robust Outlier-Aware Informed Down-Sampling
- **作者/年份**：Alina Geiger, Martin Briesch, Dominik Sobania, Franz Rothlauf，GECCO 2026
- **链接**：arXiv:2601.19477，DOI [10.1145/3795095.3805130](https://doi.org/10.1145/3795095.3805130)
- **方法**：在 GP 每代的训练子样本选择（informed down-sampling）中显式识别并降权 outlier 点，使主规律的搜索不被少数点拉偏。
- **对 RDS 的可用性**：它的 outlier 检测步骤可直接用来**定位** ite 里的 `v`；但注意它的目标是「排除」例外而非「解释」例外——RDS 要的是后者，检测留下、删除逻辑不要用。

### 1.2 Diversified Residual Symbolic Regression (DRSR)
- **作者/年份**：Koki Ikeda, Masahiro Nomura, Ryoki Hamano，GECCO 2026
- **链接**：arXiv:2605.15809，DOI [10.1145/3795095.3805087](https://doi.org/10.1145/3795095.3805087)
- **方法**：用 Quality-Diversity 维护多样化候选 archive，并在残差上继续做 SR：先拟合主项，再对残差拟合修正项，逐层叠加。
- **对 RDS 的可用性**：残差再拟合正是「主规律 + 例外修正」的天然框架——leader（`x+1`）锁定后残差只在 `x=20` 非零，第二轮小预算搜索即可暴露单点脉冲结构，工程上比全文搜索便宜。

### 1.3 SubTSBR：子采样阈值稀疏贝叶斯回归
- **作者/年份**：Sheng Zhang, Guang Lin，2021（期刊版见 Journal of Computational Physics，具体卷期未核）
- **链接**：arXiv:1907.07788
- **方法**：随机子采样 + 阈值稀疏贝叶斯回归 + 多数投票，只保留在多数子集上都被选中的项，对高噪声与 outlier 的方程发现显著鲁棒。
- **对 RDS 的可用性**：「多数子集一致」天然把例外点隔离在少数子集里，可反过来用作 `v` 的候选生成器；但它是系数空间的稀疏选择，落地到 RDS 的表达式合成器需要包一层，优先级低于 1.1/1.2。

---

## 方向二：piecewise / regime-switching 的方程发现（自动切点）

### 2.1 Unified Piecewise Symbolic Regression (UPSR)
- **作者/年份**：Guillaume Doquet，EuroGP 2025（LNCS 15609, pp. 190–206）
- **链接**：DOI [10.1007/978-3-031-89991-1_12](https://doi.org/10.1007/978-3-031-89991-1_12)
- **方法**：同时划分输入空间为若干子区域、并在每个子区域学局部符号回归器，联合成一个全局分段模型；切点自动学出，无需预先给定。
- **对 RDS 的可用性**：单点例外是宽度退化为 0 的 regime，UPSR 框架在表达上覆盖这种情况，但为单个点划一整个子模型性价比低；真遇到分段任务（而不只是单点例外）时它是首选参照。

### 2.2 Model Selection for Hybrid Dynamical Systems via Sparse Regression
- **作者/年份**：Niall M. Mangan, Travis Askham, Steven L. Brunton, J. Nathan Kutz, Joshua L. Proctor，Proc. R. Soc. A 475:20180534, 2019
- **链接**：DOI [10.1098/rspa.2018.0534](https://doi.org/10.1098/rspa.2018.0534)
- **方法**：给每个 regime 配一个指示（indicator）函数乘以该 regime 的方程库，做一次稀疏回归同时决定切换位置与每段的方程项。
- **对 RDS 的可用性**：`ite(x==v, ...)` 就是支撑集为单点的 indicator，这篇给了「indicator × 子模型」统一进稀疏选择的范式——说明脉冲项可以和主项在**同一个优化问题**里被选出来，而不是事后补丁。

### 2.3 Usability of Symbolic Regression for Hybrid System Identification
- **作者/年份**：Swantje Plambeck, Maximilian Schmidt, Audine Subias, Louise Travé-Massuyès, Goerschwin Fey，DX'24（35th Int. Conf. on Principles of Diagnosis and Resilient Systems）
- **链接**：DOI [10.4230/OASIcs.DX.2024.30](https://doi.org/10.4230/OASIcs.DX.2024.30)，HAL [hal-04794459](https://hal.science/hal-04794459v1)
- **方法**：用 SR 从数据中学习混合系统，直接依据动态行为检测连续 regime 之间的切换点（含「故障模式」这一特殊 regime），并对算法参数做了系统研究。
- **对 RDS 的可用性**：把例外点视作「瞬态故障模式」的视角有启发性；但它面向时间序列 ODE，迁到 RDS 的静态函数 `y=f(x)` 场景需要改造，参考价值大于直接复用价值。

---

## 方向三：「主项 + 脉冲/指示项」的稀疏回归表达

### 3.1 Exact Recovery of Chaotic Systems from Highly Corrupted Data
- **作者/年份**：Giang Tran, Rachel Ward，2017（期刊版见 SIAM Multiscale Modeling & Simulation）
- **链接**：arXiv:1607.01067
- **方法**：把观测建模为 `y = Θξ + e`，其中 `e` 是稀疏的 corruption 向量，用 L1 交替最小化同时恢复方程系数 ξ 与被污染点的位置及取值。
- **对 RDS 的可用性**：三方向中与 RDS 场景**数学上最同构**的一篇——`e` 就是单点脉冲，`ite(x==v, observed, leader)` 与「leader + 稀疏 e」互为改写；它证明了对任意比例的稀疏 corruption 都能精确恢复主规律，给「单点例外不应杀死合成」提供了理论支点。

### 3.2 Robust Principal Component Analysis?（低秩 + 稀疏分解）
- **作者/年份**：Emmanuel J. Candès, Xiaodong Li, Yi Ma, John Wright，Journal of the ACM 58(3), 2011
- **链接**：arXiv:0912.3599，DOI [10.1145/1970392.1970395](https://doi.org/10.1145/1970392.1970395)
- **方法**：把矩阵分解为低秩主结构 + 稀疏例外项，在相当宽的条件下可精确分离两者；是「主结构 + 稀疏例外」这一分解范式的源头（3.1 即其在方程发现上的延伸）。
- **对 RDS 的可用性**：不是 SR 方法，但它给出判据——只要「例外」足够稀疏且与主结构不相干，分离就是可解的；RDS 单点例外是极端稀疏情形，落在该理论的安全区内，可作为方案合理性的依据引用。

---

## 对我们的最小可行改动（≤5 条）

1. **合成器加单点脉冲算子 `ite(x==v, c, leader)`**：在 leader 之上枚举残差非零的 `x` 作为 `v` 候选，`c` 取观测值；复杂度只加 2–3 个节点，可直接进现有表达式模板。理论依据见 3.1/3.2。
2. **两步残差合成**：先正常合成并锁定 leader，再对残差跑一轮小预算合成（DRSR 思路，见 1.2）；残差仅在少数点非零时直接产出 delta/ite 候选，避免在全表达式空间重搜。
3. **outlier 检测只用于定位 `v`，绝不删点**（区别于 ROIDS 的用法，见 1.1）：RDS 的目标是精确解释全部观测，删除例外点会丢信息并破坏 claim 的可复核性。
4. **pareto 口径拆分为 `claim_exact` 与 `best_effort_exact`**：后者允许显式声明例外点集合（写入 claim 文本，如 `y=x+1 except x=20→1000`），例外点数计入复杂度惩罚；`PARTIAL_FIT` 一律不得计为正确。
5. **验证端不改**：ite 表达式在独立复核时按全点 replay，脉冲项在 holdout 点上自然退化为 leader，不影响插值/外推验证，假认证率不会因此上升。

# NewtonBench 成本-正确率帕累托审查报告 (Pareto Review)

## 1. 实验结果总览表

| 任务 (Task) | 臂 (Arm) | 预算 (Budget) | 状态 (Status) | 实验数 (Experiments) | 正确匹配数 (Exact Match, 满分32) | 正确率 (%) |
| :--- | :--- | :---: | :--- | :---: | :---: | :---: |
| **sparse-tail** | baseline-6pts | 6 | - | 6 | 31 | 96.88% |
| sparse-tail | rds | 6 | GOAL_IDENTIFIED | 6 | 31 | 96.88% |
| sparse-tail | rds | 8 | GOAL_IDENTIFIED | 8 | 31 | 96.88% |
| sparse-tail | rds | 12 | GOAL_IDENTIFIED | 12 | 31 | 96.88% |
| sparse-tail | rds | 16 | GOAL_IDENTIFIED | 16 | 31 | 96.88% |
| sparse-tail | rds | 24 | GOAL_IDENTIFIED | 24 | 31 | 96.88% |
| sparse-tail | rds | 32 | JUDGMENT_REQUIRED | 32 | 32 | 100.00% |
| **sparse-mid** | baseline-6pts | 6 | - | 6 | 31 | 96.88% |
| sparse-mid | rds | 6 | GOAL_IDENTIFIED | 6 | 31 | 96.88% |
| sparse-mid | rds | 8 | SEARCH_EXHAUSTED | 8 | 0 | 0.00% |
| sparse-mid | rds | 12 | SEARCH_EXHAUSTED | 11 | 0 | 0.00% |
| sparse-mid | rds | 16 | SEARCH_EXHAUSTED | 15 | 0 | 0.00% |
| sparse-mid | rds | 24 | SEARCH_EXHAUSTED | 19 | 0 | 0.00% |
| sparse-mid | rds | 32 | SEARCH_EXHAUSTED | 19 | 0 | 0.00% |
| **regime-double** | baseline-6pts | 6 | - | 6 | 10 | 31.25% |
| regime-double | rds | 6 | JUDGMENT_REQUIRED | 6 | 30 | 93.75% |
| regime-double | rds | 8 | GOAL_IDENTIFIED | 8 | 30 | 93.75% |
| regime-double | rds | 12 | GOAL_IDENTIFIED | 12 | 32 | 100.00% |
| regime-double | rds | 16 | GOAL_IDENTIFIED | 16 | 32 | 100.00% |
| regime-double | rds | 24 | GOAL_IDENTIFIED | 24 | 32 | 100.00% |
| regime-double | rds | 32 | GOAL_IDENTIFIED | 32 | 32 | 100.00% |

### 跨任务聚合表现 (3 任务总和，满分 96 点)

| 臂 / 预算配置 | 总实验数 (Cost) | 总正确点数 (Score) | 总体正确率 (%) | 相对 Baseline 关系 |
| :--- | :---: | :---: | :---: | :--- |
| **baseline-6pts** | 18 | 72 | 75.00% | 初始基准线 |
| **rds (budget=6)** | 18 | 92 | 95.83% | **严格支配 Baseline**（成本同为18，得分 92 vs 72，净胜 20 点） |
| **rds (budget=8)** | 24 | 61 | 63.54% | **被 Baseline 支配**（成本 24 > 18，得分 61 < 72） |
| **rds (budget=12)** | 35 | 63 | 65.62% | **被 Baseline 支配**（成本 35 > 18，得分 63 < 72） |
| **rds (budget=16)** | 47 | 63 | 65.62% | **被 Baseline 支配**（成本 47 > 18，得分 63 < 72） |
| **rds (budget=24)** | 67 | 63 | 65.62% | **被 Baseline 支配**（成本 67 > 18，得分 63 < 72） |
| **rds (budget=32)** | 83 | 64 | 66.67% | **被 Baseline 支配**（成本 83 > 18，得分 64 < 72） |

---

## 2. 帕累托支配关系严格判定

按多目标优化判据（成本=实验数越少越好，收益=exact_match 越高越好）：

### (1) RDS 支配基线（RDS Dominates Baseline）的点
- **在 `regime-double` 任务中**：
  - `RDS(B=6)` (exp=6, match=30) **严格支配** `baseline-6pts` (exp=6, match=10)。在相同实验消耗（6 次）下，RDS 准确率提高了 200%（30 vs 10）。
- **在 3 任务聚合宏观视角中**：
  - `RDS(B=6)` (总 exp=18, 总 match=92) **严格支配** `baseline-6pts` (总 exp=18, 总 match=72)。相同成本下，总体正确率高出 20.8 个百分点。

### (2) RDS 被基线支配（Baseline Dominates RDS）的点
- **在 `sparse-tail` 任务中**：
  - `RDS(B=8, 12, 16, 24)` 的实验数分别为 8, 12, 16, 24，但 exact_match 停留在 31，与 `baseline-6pts` (exp=6, match=31) 完全一致。成本单调递增，精度收益为零，**被 baseline-6pts 严格支配**。
- **在 `sparse-mid` 任务中**：
  - `RDS(B=8, 12, 16, 24, 32)` 实验数分别为 8, 11, 15, 19, 19，exact_match 全部坍缩为 0。而 `baseline-6pts` 仅用 6 次实验就取得 31 分。**RDS 所有 B>=8 的点均被 baseline-6pts 严重反向支配**。
- **在 3 任务聚合宏观视角中**：
  - `RDS(B=8, 12, 16, 24, 32)` 总实验数从 24 剧增至 83，总正确得分却从 72 跌落至 61~64 分。**除 B=6 外的所有 RDS 高预算点在聚合层面上全被 baseline 严格支配**。

---

## 3. 帕累托前沿点与机制剖析

### (1) 帕累托前沿点 (Pareto Frontier)
- **任务级前沿**：
  - `sparse-tail`: 包含 2 个前沿点 —— `(exp=6, match=31)` [baseline / RDS(B=6)] 与 `(exp=32, match=32)` [RDS(B=32)]。
  - `sparse-mid`: 仅有 1 个前沿点 —— `(exp=6, match=31)` [baseline / RDS(B=6)]。
  - `regime-double`: 包含 2 个前沿点 —— `(exp=6, match=30)` [RDS(B=6)] 与 `(exp=12, match=32)` [RDS(B=12)]。
- **三任务综合聚合前沿**：
  - **唯一的前沿点是 `RDS(B=6)` (exp=18, match=92)**。
  - 更高预算的 RDS 因认知塌陷与无效消耗，全部脱离了帕累托前沿。

### (2) RDS 目前脱离前沿的根本机制
1. **`sparse-mid` 的「认知破缺与假说死锁」导致断崖式归零（最致命问题）**：
   - 在 B=6 时，RDS 采样窗口未触及 x=20，侥幸以 `x+1` 斩获 31 分。
   - 当 B>=8 时，主动探针准确踩中 x=20 (y=1000)，成功证伪原假设 `x+1`。但系统的语法树合成器（`max_nodes: 5`，无分段条件算子）在候选空间内根本无法表达 `x+1 if x!=20 else 1000`。
   - 搜索耗尽（`SEARCH_EXHAUSTED`）后，预测表达式置为 None，评分直接归零（0/32）。
   - **机制洞察**：更严谨的科学探索敏锐地发现了反例，却因假说生成器缺乏局部容错与分段表达能力，陷入「发现了错误却无法解释，直接瘫痪弃权」的死锁，反而惨败给盲目外推的 Baseline。
2. **`regime-double` 与 `sparse-tail` 的「早饱和与盲目烧钱」**：
   - 在 `regime-double` 中，RDS 在 B=12 时已经成功合成出真实规律（exact_match=32 满分）。但在 B=16/24/32 时，执行器缺乏饱和早停机制，硬生生把设定的实验上限全部跑满，使得精度不再增长的同时成本线性放大，产生自我支配。
   - 在 `sparse-tail` 中，x=31 的异常点位于最末端，B=8~24 的采样步长未能覆盖该点，系统在无法证伪 `x+1` 的情况下依然盲目跑满预算，造成严重的无效消耗。
3. **缺乏「最佳次优假设回退 (Best-Effort Fallback)」弹性**：
   - 系统将「发现反例」与「全盘放弃」强绑定。一旦原假设被驳倒且全局新假说合成失败，没有将「历史对数据解释率最高、残差最小的次优假说」作为兜底保留的机制。

---

## 4. 把 RDS 推上前沿的具体建议（按预期收益排序）

### 建议 1【优先级 1 · 预期收益最高】：引入「证伪失败时的最佳次优假设回退 (Best-Effort Fallback)」
- **具体做法**：
  - 修改结算与预测导出逻辑：当原假设被证伪且符号合成器耗尽（`SEARCH_EXHAUSTED`）无法产生覆盖所有观测点的全局公式时，禁止返回空预测（None），而是回退到「对历史观测数据解释率最高 / 预测残差平方和最小」的候选假设。
- **预期收益**：
  - `sparse-mid` 在 B=8..32 时，即便未能拟合 x=20，也能保留 `x+1` 作为最佳近似，得分立即从 0 分跃升至 31 分。直接挽回 31 点（96.9%）的断崖式损失，彻底消除所有被 Baseline 反向支配的异常点。

### 建议 2【优先级 2 · 收益明确】：增加「验证饱和与信息增益枯竭」的自适应早停门控 (Early-Stopping)
- **具体做法**：
  - 在 `rds_compass_drive` 循环中加入收敛判据：
    1. 当候选假说已连续通过 holdout 验证与多轮独立确认且在全域无反例（如 `regime-double` 在 B=12 达到满分确认时），提前终止实验并成功退出；
    2. 若连续 N 步主动探索所采集的数据未能提供新的信息增益（例如全部与当前假说预测一致），主动跳出实验循环。
- **预期收益**：
  - 剪掉 `regime-double` 在 B=16/24/32 处的平坦无效消耗（实验数从 16/24/32 缩减回收至 12 次），同时削减 `sparse-tail` 的无效探测成本，直接将高精度满分点推向帕累托前沿。

### 建议 3【优先级 3 · 拓宽能力前沿】：在候选生成中支持轻量分段/奇异点隔离修补 (Piecewise / Delta Repair)
- **具体做法**：
  - 在语法树合成器或符号搜索阶段，允许引入条件指示算子（如脉冲项 `delta(x, c)`）或两段分段结构，或者在主定律外维护稀疏异常点字典。
- **预期收益**：
  - 赋予系统容纳稀疏突变点的表达能力，使 `sparse-mid` 与 `sparse-tail` 在中等预算（如 B=8~16）就能捕获并解释异常点，达成 32/32 满分，从而在低成本区间开拓出超越 Baseline 的全新高精度帕累托前沿。

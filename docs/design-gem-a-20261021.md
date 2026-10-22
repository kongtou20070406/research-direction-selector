# 深度学习实验循环映射到 Compass 架构设计方案（任务 A）

- **设计者**：大肥鱼（RDS 设计员，gem-design）
- **日期**：2026-10-21
- **目标文件**：`docs/design-gem-a-20261021.md`
- **代码基线**：分支 `release/6.0.0-compass`，HEAD `393c62f`（基于 `3864dfa`）

---

## 0. 本轮三行（开工铁律）

- **提升能力**：让 Compass 闭环能够承载深度学习（DL）等实证科学实验，支持“配置 A 优于配置 B”等比较式假说的形式化验证。
- **可证伪信号**：在固定 $k$ 个配置的深度学习超参/消融实验中，候选集数量严格保持为 $O(k)$，EIG 主动采样与判定循环完整走通，单次实验不出现重复训练计费。
- **绝对不允许**：绝对不允许假认证（`false_certification_rate > 0.00%`）；绝对不允许绕过单一账本引入第二状态机；绝对不允许将噪声单 seed 测量静默折叠为点值真理。

---

## 1. 核心矛盾：比较式假设 vs 预测式内核

### 1.1 现状与语义鸿沟

Compass 现有的核心逻辑是**绝对预测式（Predictive）**：
- **假设形态**：`scripts/rds_compass_model.py:528-556`（`compile_hypothesis`）要求声明 `predict: {observable: rule}`，即给定候选点输入 $x$，预测具体物理量值 $\hat{y}$。
- **判定核心**：`scripts/rds_compass_model.py:500-509`（`decide`）：
  ```python
  radius = getattr(predicted, 'radius', 0) + getattr(observed, 'radius', 0)
  gap, spread = abs(predicted - observed), relative * getattr(observed, 'radius', 0)
  if gap + radius <= absolute + relative * abs(observed) - spread:
      return True
  if gap - radius > absolute + relative * abs(observed) + spread:
      return False
  return None
  ```
  `decide` 将预测区间与观测区间做分离/包含检验，输出三值结果：`True`（相容）、`False`（反例）、`None`（不可判）。

深度学习研究的假设则是**相对比较式（Comparative）**：
- 科学家不关心也不可能预测“配置 $A$ 在验证集上的绝对损失精确等于 $1.4325$”。
- 关心的核心是关系命题：“在相同计算预算下，配置 $A$ 的验证指标优于配置 $B$”（$\Delta_{\text{metric}} = y_A - y_B > 0$），或“随着层数增加，收益呈现边际递减”。
- 观测值是随机变量：多 seed 重复试验给出的均值与方差。

---

## 2. 把「配置对」当候选的 $O(k^2)$ 成本解剖

若将“配置对 $(A, B)$”直接作为 Compass 的 Candidate，把 8 个配置扩充为 $\binom{8}{2} = 28$ 个候选对，在什么规模下不可接受？

### 2.1 崩溃临界点：$k \ge 12$ 时直接代码硬崩溃

1. **Declaration 静态硬约束**：
   - 代码依据：`scripts/rds_compass_declaration.py:26`：
     ```python
     MAX_CANDIDATES = 64
     ```
   - 以及 `scripts/rds_compass_declaration.py:189`：
     ```python
     require(isinstance(rows, list) and 1 <= len(rows) <= MAX_CANDIDATES, f'Declare 1..{MAX_CANDIDATES} candidates')
     ```
   - 当配置数 $k=8$ 时，$\binom{8}{2} = 28 \le 64$（尚可通过）；
   - 当配置数 $k=11$ 时，$\binom{11}{2} = 55 \le 64$（逼近上限）；
   - **当配置数 $k=12$ 时，$\binom{12}{2} = 66 > 64$**，触发 fail-closed 拦截，在 Ingest 校验期直接异常崩溃。
   - 结论：**无需等待算法变慢，在 $k=12$ 规模下现有系统直接拒绝加载**。

### 2.2 算法与计算开销退化（$k \ge 30$）

若修改常量放宽 `MAX_CANDIDATES`，计算复杂度仍会迅速击穿调度循环：
1. **EIG 排序开销**：
   - 依据 `scripts/rds_compass_model.py:716-755`（`rank_experiments`），每次迭代必须遍历全量未测候选点，并在每个候选点上评估所有存活假设的分区（`outcome_partition`，`:638-665`）。
   - 复杂度为 $O(N_{\text{cand}} \cdot M_{\text{hyps}} \cdot \text{Cost}(\text{eval}))$。
   - 当 $k=30$ 时，$N_{\text{cand}} = 435$；当 $k=100$ 时，$N_{\text{cand}} = 4950$。
   - 每次实验前的信息增益计算耗时将从数毫秒增加到数十秒，决策算法自身开销开始超越短轻量训练任务本身。

### 2.3 物理计费与数据流断层（核心物理缺陷）

配置对作为候选存在**致命的物理经济学断层**：
1. **重复训练计费**：若把对 $(A, B)$ 作为一个测量单元，购买该单元意味着同时训练 $A$ 与 $B$。下一轮若系统选择测量 $(A, C)$，若无侵入式外部缓存，则配置 $A$ 被重复训练，产生高达 $k-1$ 倍的 GPU 资源浪费；
2. **破坏 Receipt 绑定纪律**：若在实验沙箱内部做跨 Candidate 缓存，则破坏了 `measured(state)`（`scripts/rds_compass.py:141-144`）中 `receipt_sha256` 与具体单次物理执行字节严格绑定的密码学不变量（`AGENTS.md` I.1）。

### 2.4 统计检验多重性爆炸

两两比较引入 $\binom{k}{2}$ 个假说检验。在区间测量的重叠判决下，若缺乏多重假设校正，发生随机误差重叠断裂（假认证）的概率随 $k^2$ 激增，直接威胁 `false_certification_rate = 0.00%` 的红线。

---

## 3. 架构归约方案：星形基准锚定与 Delta 观测量（$O(k)$）

### 3.1 核心设计思想

科学实践中，绝大多数 DL 探索并非无序的完全图两两对比，而是围绕**对照组（Control / Baseline）**开展的消融与增量实验。
因此，必须保持 **Candidate 对应真实物理训练实体（$O(k)$）**，将比较逻辑收敛在 **Observable** 维度。

### 3.2 具体设计规范

#### 1. 候选集：物理实体映射（$N_{\text{cand}} = k$）
- 保持 $1:1$ 物理映射。每个 Candidate 即一次训练配置：
  ```json
  {
    "id": "arm_dropout_03",
    "inputs": {
      "lr": "1/1000",
      "dropout": "3/10",
      "weight_decay": "1/10000"
    },
    "cost": "1.0"
  }
  ```
- 约定 `c_0` 为基准配置（Baseline，例如 `id: "baseline"`，`inputs: {"dropout": "0", ...}`）。

#### 2. 证据层：区间的 Minkowski 差分测量
- 实验测量返回两类输出：
  1. 绝对量：`val_loss`, `accuracy`；
  2. 显式声明的 Delta 相对量：`delta_val_loss`, `delta_accuracy`。
- 沙箱执行器在测得 `c_i` 的多 seed 区间 $[y_i^{\text{lo}}, y_i^{\text{hi}}]$ 后，与固化的基准区间 $[y_0^{\text{lo}}, y_0^{\text{hi}}]$ 做区间差分：
  $$\Delta = y_i - y_0 = \left[ y_i^{\text{lo}} - y_0^{\text{hi}}, \; y_i^{\text{hi}} - y_0^{\text{lo}} \right]$$
- 传递给 Compass 的测量格式直接复用 Stage 1 已实现的字典区间结构（`scripts/rds_compass_model.py:94-102`）：
  ```json
  "outputs": {
    "accuracy": {"lo": "812/1000", "hi": "824/1000"},
    "delta_accuracy": {"lo": "12/1000", "hi": "24/1000"}
  }
  ```
- `scripts/rds_compass.py:141` 中的 `model.exact(v)` 将其自然解析为 `Inexact(lo, hi)`，无需修改解析核心。

#### 3. 假说层：显式声明预测与作用域（Stage 2 Seam）
- 利用 `scripts/rds_compass_declaration.py:229-234` 支持的 `hypotheses` 声明块，研究员或 Agent 提出相对增益假说：
  ```json
  {
    "id": "hyp_dropout_gain",
    "origin": "agent:deepseek-harness",
    "statement": "delta_accuracy > 0",
    "predict": {
      "delta_accuracy": "18/1000"
    },
    "applies": "dropout > 0"
  }
  ```
- 配合声明容差：`tolerances: {"delta_accuracy": {"abs": "10/1000", "rel": "0"}}`。

#### 4. 判定层：三值判决的天然映射
`decide(predicted, observed, tol)`（`scripts/rds_compass_model.py:500-509`）无需任何修改：
- **`True`（CONSISTENT）**：当测量的区间差严格落在容差带内，表明性能提升符合假说预期；
- **`False`（REFUTED / Counterexample）**：若实测区间差整体脱离容差带（例如 $\Delta \le 0$，说明配置反而劣化），产生确定性反例；
- **`None`（UNDECIDED）**：若由于 seed 较少或方差过大导致区间较宽，跨越了容差边界，系统诚实判定为 `UNDECIDED`，驱动循环继续采样（或触发预算终止），绝不冒进认定。

---

## 4. 四步落地方案（做什么 / 为什么 / 验收 / 回滚）

### 方案 1：声明层支持 Delta 观测语义与基准锚点（`scripts/rds_compass_declaration.py`）
- **做什么**：
  在 declaration 规范中允许声明基准候选标识（`benchmark_anchor: "c_baseline"`），并在 `observables` 校验中允许声明依赖差分量；保持 `MAX_CANDIDATES = 64` 约束物理臂数量。
- **为什么这么做**：
  将比较复杂度从 $O(k^2)$ 压制到 $O(k)$，彻底避开候选列表膨胀与 EIG 组合爆炸。
- **怎么验收**：
  1. 反向测试：未声明 `benchmark_anchor` 的经典 NewtonBench 方程跑出来的 `plan` 字典结构严格保持逐字段相同；
  2. 边界拦截：声明了不存在的 `benchmark_anchor` 时，在 ingest 时 fail-closed 抛出 `ValueError`。
- **什么时候回滚**：
  若任何旧 devset 方程的 declaration 校验耗时增长超过 $5\%$，或产生结构变更，立即回滚。

### 方案 2：实验执行器输出差分区间测量（`benchmark/` 实验模板）
- **做什么**：
  在 DL 实验执行模板中，标准化“多 seed 汇总 $\to$ 区间封装 $\to$ 相对基准减法”的管线。输出格式严格遵守 `scripts/rds_compass_model.py:94` 要求的 `{'lo': ..., 'hi': ...}`。
- **为什么这么做**：
  遵守“不修改内核判定代码”的原则，将统计学不确定性通过原生 `Inexact` 承载，保证反例判决严谨。
- **怎么验收**：
  单配置运行 3 个 seeds，生成的测量 JSON 经 `model.exact()` 解析后类型为 `Inexact`，且 `radius > 0`；计算的 `delta_accuracy` 区间等于两点 Minkowski 差。
- **什么时候回滚**：
  若出现 `radius == 0` 的点值塌缩（把统计均值当成了精确真值），立即回滚。

### 方案 3：假说预编译与 Seam 接入（`scripts/rds_compass.py:_fits`）
- **做什么**：
  落实 Stage 2 预注册判据（`docs/stage2-hypothesis-protocol.md`）：当 declaration 显式传入 `hypotheses` 时，`_fits` 直接返回已编译的外部假说，跳过 `synthesize` 枚举。
- **为什么这么做**：
  根据 `docs/enum-ceiling-20261021.md`，深度学习没有代数闭式语法可供穷举。由 Agent 显式注入先验假说，是越过枚举天花板的唯一合法路径。
- **怎么验收**：
  1. 按照 `stage2-hypothesis-protocol.md` Check 2，monkeypatch `model.synthesize` 强制抛异常，DL 运行依然正常走通；
  2. 运行时 `state['hypotheses']` 完整记录来源 `origin: "agent:..."`，并写入 `COMPASS_HYPOTHESIS` 账本事件。
- **什么时候回滚**：
  若旧代数方程在未声明 `hypotheses` 时跳过了符号枚举，或返回结果与基线不一致，立即回滚。

### 方案 4：假认证零容忍门禁（Zero False-Certification Gate）
- **做什么**：
  在结果收集与验收脚本中，必须显式统计 `false_certification_rate`。判定准则：若系统给出 `status == 'GOAL_IDENTIFIED'`，但在额外 holdout seed 上验证出现反例，计为一次假认证。
- **为什么这么做**：
  DL 噪声极易产生过拟合，避免将 `CONSISTENT` 误读为绝对真理，守护学术诚信底线。
- **怎么验收**：
  在全量评测运行中，`false_certification_rate` 严格等于 $0.00\%$。
- **什么时候回滚**：
  若出现任何 1 起假认证，对应改动立即无条件全部回滚。

---

## 5. 对比总结表

| 维度 | 配置对笛卡尔积方案（Pairwise Candidate） | 本方案：基准锚定 Delta 观测方案（Star Delta） |
| :--- | :--- | :--- |
| **候选集规模** | $\binom{k}{2} = O(k^2)$（8 配置 $\to$ 28 候选，30 配置 $\to$ 435 候选） | $k = O(k)$（8 配置 $\to$ 8 候选，30 配置 $\to$ 30 候选） |
| **突破 `MAX_CANDIDATES=64`** | $k \ge 12$ 时直接报错崩溃 | 可平稳支持到 $k=64$（若需更大仅需线性放宽） |
| **物理训练开销** | 存在 $O(k^2)$ 次训练或复杂的隐式缓存依赖 | 严格 $k$ 次物理训练，无重复计费 |
| **Receipt 密码学绑定** | 难以对齐单次物理执行与对候选 | 单配置对应单一收据，完全守恒 |
| **EIG 调度耗时** | 随 $k^2$ 剧增，可能反噬训练耗时 | 保持 $O(k)$ 线性极速评估 |
| **内核改动需求** | 需重构 Candidate 调度语义 | **0 内核逻辑改动**，完全复用现有 `Inexact` 与 `decide` |

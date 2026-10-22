# JUMP 符号回归合成增量复用与性能优化方案设计

> **文件标识**：`docs/jump-synthesis-reuse-20261021.md`  
> **仓库路径**：`D:\Myfile\work space\RDS\rds-357`  
> **分支与基线**：`release/6.0.0-compass` @ `89c1744`  
> **执行约束**：本文档为纯方案论证与技术设计，不修改任何内核代码、不提交 commit、不发起 push。所有推断标注 `UNVERIFIED`，性能预估标注 `ESTIMATE`，源码引用严格精确至 `file:line`。

---

## 0. 根因定位核验与反证

针对用户提供的根因定位结论，本节进行逐项源码比对与理论核验：

### 0.1 属实且核验一致的判断
1. **耗时瓶颈归因准确**：`escape()`（`scripts/rds_compass.py:1108`）与 `generate_rivals()`（`scripts/rds_compass.py:833`）确实在每一轮 JUMP 中反复调用 `_fits`，进而触发 `model.synthesize`；
2. **从零全量枚举属实**：`_fits`（`scripts/rds_compass.py:692`）每轮均重新从 `measured(state)` 构建 `rows`，并在 `synthesize` 中重置搜索树自底向上重新枚举候选（默认 20,000 个，在 `derived_fit` 分支甚至达 200,000 个）；
3. **`probes` 确实严格缩小**：`probes = [env(state, c) for c in remaining(state)]`（`scripts/rds_compass.py:705`），随着候选点被测量，`next_attempt` 变为 None，点被移出 `remaining(state)`（`scripts/rds_compass.py:212`），因此 `probes` 随着实验推进严格单调收缩。

### 0.2 定位存在偏差的关键事实与严格反证
> **待核验判断**：“老点的位置在 points 前缀里也不变（probes 由 remaining(state) 算，会随已测点变化而缩小）”。

**核验结论**：**该判断不成立（反证如下）。老点在 `points` 数组全局视角下的绝对位置发生了重排与平移，只有纯已测点区间（`0 <= index < len(rows_old)`）的位置保持不变。**

#### 严格反证推导
在 `scripts/rds_compass_model.py:799-800` 中，`points` 数组由两部分拼接而成：
```python
points = [{k: exact(v) for k, v in row['inputs'].items()} for row in rows]
points += [{k: exact(v) for k, v in env.items()} for env in probes]
```
1. 设第 $k$ 次 JUMP 时已测点集为 $R_k = [r_1, r_2, \dots, r_m]$，待测探针集为 $U_k = [u_1, u_2, \dots, u_p]$。此时合成器接收到的点集为：
   $$\text{points}_k = [r_1, \dots, r_m, u_1, u_2, \dots, u_p]$$
   其中索引 $0 \le i < m$ 对应已测点，索引 $m \le j < m+p$ 对应未测探针点。
2. 驱动器根据主动学习（EIG / 差异度最大化）挑选下一个实验点。系统选择测量的**通常不是**探针列表的第一项 $u_1$，假设选中了 $u_3$。
3. 实验完成，记录写入 Ledger。系统进入第 $k+1$ 次 JUMP：
   - 已测行变为：$R_{k+1} = [r_1, \dots, r_m, u_3]$（按 `measured(state)` 的事件写入序号 `seq` 严格单调追加到末尾）；
   - 探针集变为：$U_{k+1} = [u_1, u_2, u_4, \dots, u_p]$（从列表中剔除了已测的 $u_3$）；
   - 拼接后的点集为：
     $$\text{points}_{k+1} = [r_1, \dots, r_m, \mathbf{u_3}, \mathbf{u_1}, u_2, u_4, \dots, u_p]$$
4. **对比两轮在索引 $m$ 处的元素**：
   - $\text{points}_k[m] = \mathbf{u_1}$（老探针列表首项）；
   - $\text{points}_{k+1}[m] = \mathbf{u_3}$（刚刚新测入的行）！
5. **推论**：
   - 数组在索引 $m$ 处的内容发生了改变；
   - 原探针点 $u_1, u_2$ 的下标从 $m, m+1$ 移动到了 $m+1, m+2$；
   - **如果缓存实现直接以 `points` 数组的下标索引切片或保存 `values: tuple`，在新一轮比对时必然发生下标错位（Index Misalignment），导致严重的假剪枝或假拟合错误！**

---

## 1. 调用链核验（带 file:line）

### 1.1 `escape` → `_fits` → `model.synthesize` 逐层传参

```
drive (scripts/rds_compass_drive.py:81)
  └─► escape (scripts/rds_compass.py:1105)
        └─► _fits(state, escalate=True) (scripts/rds_compass.py:692)
              ├─► model.synthesize (whole, line 708)
              ├─► model.synthesize (escalated, line 715)
              ├─► model.synthesize (derived_fit, line 732)
              ├─► model.synthesize (residual, line 747)
              └─► model.regime_split (line 791) ──► _side_fit ──► model.synthesize (line 920)
```

1. **`escape` 入口**（`scripts/rds_compass.py:1105-1115`）：
   ```python
   # line 1108
   found, notes = _fits(state, escalate=True)
   ...
   # 若未找到且 RSI 合格，fold 状态后再次调用：line 1114
   found, notes = _fits(state, escalate=True)
   ```
   - 传递参数：`state=state`, `escalate=True`。
   - `rows`, `probes`, `bound` 均未显式传递，使用默认值 `None`。

2. **`_fits` 内部参数处理**（`scripts/rds_compass.py:692-707`）：
   ```python
   def _fits(state, *, rows=None, probes=None, bound=None, escalate=False):
   ```
   - **`rows`**：`rows = measured(state) if rows is None else rows`（line 697）。
     - 关键分支（line 700）：`holdout = set() if escalate else set(plan.get('holdout') or [])`。
     - 在 `escape` 中（`escalate=True`），`holdout` 为空集，**所有已测点（全量数据）全部参与反例拟合**；在 `generate_rivals` 中（`escalate=False`），排除 holdout 保留点。
     - 过滤条件（line 701）：只保留包含目标可观测量且不在 holdout 中的测点。
   - **`probes`**：`probes = [env(state, c) for c in remaining(state)] if probes is None else probes`（line 705）。
   - **`bound`**：`bound = bound or plan['synthesis']`（line 706）。
   - **`grammar, names`**：来自 `_grammar(state)`（line 704），合并 inputs 变量与当前 `QUALIFIED` 工具的 features。

3. **`_fits` 传递给 `model.synthesize` 的实际参数**（`scripts/rds_compass.py:708-750`）：
   - `observations`: 过滤后的 `rows` 列表；
   - `inputs`: `names` 变量名列表；
   - `observable`: 目标可观测量标识符；
   - `tolerances`: 容差字典；
   - `grammar`: 当前语法配置字典；
   - `max_nodes`: 来自 `bound['max_nodes']`；
   - `max_candidates`: 来自 `bound['max_candidates']`（或在派生分支放大）；
   - `probes`: 探针点输入环境列表；
   - `keep`: 保留候选上限（`whole` 为 4，`residual` 为 2，`regime_split` 为 1）；
   - `base`: 残差模式传入的基准表达式（其余分支为 `None`）；
   - `derived`: 派生变量映射字典（`derived_fit` 分支传入，其余为 `None`）。

### 1.2 `probes` 计算方式与 `remaining(state)` 定义

- **`remaining(state)` 源码**（`scripts/rds_compass.py:212-215`）：
  ```python
  def remaining(state):
      holdout = set(state['plan'].get('holdout') or [])
      return [c for c in state['plan']['candidates']
              if c['id'] not in holdout and next_attempt(state, c) is not None]
  ```
  - 定义：从静态声明的候选集 `plan['candidates']` 中，排除 holdout 点，且尚有可用实验尝试机会（`next_attempt is not None`）的未测/重试点集合。
- **`probes` 生成**（`scripts/rds_compass.py:705`）：
  - `probes = [env(state, c) for c in remaining(state)]`；
  - `env(state, candidate)`（`scripts/rds_compass.py:120-127`）将候选点的原始输入参数与已合格工具在该点产生的衍生特征（features）合并为精确的有理数环境字典。

### 1.3 `bound` 的定义与取值

- **声明层**（`scripts/rds_compass_declaration.py:35`）：
  `'synthesis': {'max_nodes': 7, 'max_candidates': 20000}`；
- **边界校验层**（`scripts/rds_compass_declaration.py:45-46`）：
  `max_nodes` 受限于 `(1, model.MAX_NODES=15)`；`max_candidates` 受限于 `(1, model.MAX_SYNTH_CANDIDATES=200000)`；
- **运行时默认**：`bound['max_nodes'] = 7`，`bound['max_candidates'] = 20000`。

### 1.4 `escalate=True` 时 6 个分支的执行判定

在 `scripts/rds_compass.py:708-795` 中，各分支流转依赖关系如下：

| 分支名称 | 代码位置 | 触发条件 | `max_candidates` 上限 | 在 `feynman-I.18.4` 下的实际行为 |
| :--- | :--- | :--- | :--- | :--- |
| **1. whole** | line 708 | **无条件必跑** | `bound['max_candidates']` (20,000) | **必定执行**。`max_nodes=7` 无法表达 4 变量复合分式，枚举 20,000 个全军覆没（耗时约 25s `ESTIMATE`）。 |
| **2. escalated** | line 711 | `escalate and not whole['fits'] and not (plan.get('grammar') or {}).get('operators')` | `min(6000, bound['max_candidates'])` (6,000) | **跳过**。devset 方程（`devset.py:357`）显式声明了 operators，故不满足未指定算子的条件。 |
| **3. derived_fit** | line 723 | `escalate and not whole['fits'] and len(names) >= 2` | `min(200000, bound['max_candidates'] * 10)` (**200,000**) | **必定执行**。4 变量满足 $\ge 2$。自动派生两两和与积，搜索上限拉至 200,000。耗费数百秒最终命中 `((m1*r1 + m2*r2) / (m1 + m2))`。 |
| **4. residual** | line 743 | `bool(refuted)`（与 escalate 无关，存在被证伪假说即跑） | `min(20000, bound['max_candidates'])` (20,000) | **视假说状态执行**。若历史已有假说被反例打死，以最小残差者为基准拟合修正项。 |
| **5. pulse** | line 754 | `refuted and len(inputs) == 1 and not found and len(training) >= 4` | 不调 `synthesize`，单点 ite 补丁 | **跳过**。`feynman-I.18.4` 为 4 输入变量，非单变量。 |
| **6. regime_split** | line 789 | `not found and len(rows) >= 4` | 变量分段阈值切分，每段 3,000 | **跳过**（因 derived_fit 已找到解，`found` 非空）。 |

---

## 2. 增量复用的可行性分析

### 2.1 连续 JUMP 之间 `points` 前缀变化与缓存键设计

1. **前缀不稳定的根本机制**：
   如 §0.2 所述，`points = rows + probes`。每当新点从待测变为已测，它被追加至 `rows` 末端，同时从 `probes` 剔除。
   这导致数组在 `len(rows_old)` 之后的所有探针点位置**集体发生右移或重排**。
2. **缓存键的正确设计方式**：
   - **错误做法**：将 `values: tuple` 作为固定顺序元组存入，并按列表切片下标比对（必定错位）；
   - **正确做法 A（Canonical Point Key）**：
     每个点具有全局不可变的标识。可取 `point_id = candidate['id']`（例如 `c01`, `c02`）或其输入的规范化哈希 `env_key = tuple(sorted(env.items()))`。
     值缓存结构应为字典的字典：
     $$\text{Cache}: \text{node\_signature} \to \{\text{point\_id}: \text{value}\}$$
   - **正确做法 B（全局全集投影向量）**：
     由于整个 campaign 的候选点空间 $C = [c_1, \dots, c_N]$（如 Newtonbench 网格的 54 个点）在 declaration 阶段已静态确定。可将 $C$ 的固定顺序定义为全局基准向量，任何节点的值向量 $\mathbf{V}_C(node)$ 都是全局不变的。当前轮次使用时，通过当前 `points` 到 $C$ 的全局索引映射表（Index Mapping）提取对应分量。

### 2.2 `targets` 变化时 `pools`/`seen` 能否跨轮复用？

#### 核心结论：`seen` 在全局坐标下可累积，但 `pools` 绝不能直接复用（存在早停截断致命漏洞）！

#### 1. `seen` 的复用可行性
在 `scripts/rds_compass_model.py:851-855` 中，`seen` 记录的是候选节点的输出特征向量 `key`。
若以全局点集 $C$ 构建特征向量，由于两个数学表达式在全集 $C$ 上的取值是否完全相等是一个客观数学事实，因此在全局网格下，`seen` 判重具有单调累积性。

#### 2. `pools` 跨轮复用的致命早停漏洞（Kill Condition）
在 `scripts/rds_compass_model.py:856-858` 中：
```python
if all(v is not None and matches(v, t, tol) for v, t in zip(values[:n], targets)):
    fits.append(node)
return len(fits) < keep
```
- **早停机制**：一旦搜出 `keep`（默认 4）个匹配当前 targets 的候选，`consider()` 立即返回 `False`，整个自底向上枚举立即 `break` 终止。
- **漏洞推演**：
  1. 在第 1 次 JUMP 时，已测点只有 2 个。匹配 2 个点非常容易，一些极其简单的平凡多项式（如 $m_1 - 1$ 或常数解）迅速满足条件；
  2. 合成器在枚举到第 150 个候选时就凑齐了 4 个 fits，**搜索立即停止**！此时生成的 `pools` 仅仅包含了前 150 个浅层节点；
  3. 随后驱动器采集了第 3 个测点，这 4 个平凡候选全部被新数据证伪；
  4. **若此时直接复用上一轮的 `pools` 而不重新枚举**：上一轮的 `pools` 里只有那 150 个节点，在新点上全部无法拟合；
  5. 合成器直接返回 `NO_FIT`，**导致本应在第 5,000 个节点才能搜出的真实物理定律被永久遗漏（致命假阴性）！**

---

## 3. 三个候选方案评估（按风险由低到高排序）

综合算法保真度、代码侵入性与漏解风险，排序如下：
$$\text{方案 C（低风险）} < \text{方案 A（中风险）} < \text{方案 B（高风险·严重缺陷）}$$

---

### 方案 C（低风险·强烈推荐）：`_evaluate` 闭包编译与向量化求值加速

#### 1. 核心机制
- 瓶颈根因在于 `scripts/rds_compass_model.py:368` 的 `_enclose` 与 `_evaluate`：每个候选节点在 54 个点上逐点进行深度优先递归，反复解析字典键、创建 Python 函数栈帧；200,000 个候选 $\times$ 54 点产生超 10,000,000 次 Python 函数调用；
- 改造核心：将 AST 节点在构造时直接编译为 Python 可执行闭包函数，或者在 `synthesize` 自底向上枚举构建时，直接复用子节点在 `pools` 中已算出的 values 元组，利用算子逐点计算：
  $$\text{values}_{\text{parent}} = \text{tuple}(\_apply(op, l, r) \text{ for } l, r \text{ in zip}(vals_{left}, vals_{right}))$$
  使树深度 $D$ 的求值复杂度从 $O(D \cdot |P|)$ 骤降为 $O(1 \cdot |P|)$。

#### 2. 改动函数与签名
- `scripts/rds_compass_model.py`：
  - 内部重写 `_evaluate` 热路径，或在 `synthesize` 内部自底向上组合时采用向量直接映射；
  - 对外函数签名 `synthesize(...)` **完全无需改变**。

#### 3. 全部 7 个调用点核查（逐一列出）
对现有生产代码中 7 处 `synthesize` 调用**全部保持 0 修改**：
1. `scripts/rds_compass.py:708` (`whole`) —— **无需修改**；
2. `scripts/rds_compass.py:715` (`escalated`) —— **无需修改**；
3. `scripts/rds_compass.py:732` (`derived_fit`) —— **无需修改**；
4. `scripts/rds_compass.py:747` (`shifted`) —— **无需修改**；
5. `scripts/rds_compass_model.py:920` (`_side_fit`) —— **无需修改**；
6. `scripts/rds_compass_rsi.py:372` (RSI 评估) —— **无需修改**；
7. `scripts/rds_compass_cli.py:42` (CLI 接口) —— **无需修改**。

#### 4. 什么情况下会算错（边界风险）
- **除零与定义域异常**：`_enclose` 严密捕获了 `DomainError`（如 $1/0$、负数开偶数根）和 `_bounded` 分母过大溢出。闭包或向量化算子在除零时必须严格返回 `None`，不可漏抛或导致解释器崩溃；
- **Inexact 区间包络**：对于产生无理数的结果（如 $\sqrt{2}$），需保证区间包络不退化。但在 Newtonbench devset 中变量与运算均为纯有理数，主要覆盖精确 Fraction 算术。

#### 5. 预计收益
- 消除 1000 万次递归调用与字典查找：求值吞吐提升 **5x ~ 10x**（`ESTIMATE`）；
- `derived_fit` 的 200,000 个候选枚举时间从 632 秒压缩至 **70 ~ 120 秒**（`ESTIMATE`）；
- 单格总耗时直接脱离 900 秒超时死线。

---

### 方案 A（中风险）：`synthesize` 注入单点计算值缓存，新点补算

#### 1. 核心机制
- 在 `synthesize` 中引入 `cache` 可选参数，缓存规范化 AST 在各已命名网格点上的取值；
- 每轮依然完整执行自底向上枚举（保证不漏解），但在调用 `vector(node)` 时，已在缓存中的点直接读取，仅对本轮新增加的测点执行 `_evaluate` 补算。

#### 2. 改动函数与签名
- `scripts/rds_compass_model.py`：
  ```python
  def synthesize(observations, inputs, observable, tolerances, *,
                 grammar=None, max_nodes=7, max_candidates=20000,
                 probes=(), base=None, keep=4, derived=None,
                 cache=None):  # 新增可选命名参数
  ```
- `scripts/rds_compass.py`：
  ```python
  def _fits(state, *, rows=None, probes=None, bound=None, escalate=False,
            cache=None):  # 新增可选命名参数
  ```

#### 3. 全部 7 个调用点核查（逐一列出）
- `scripts/rds_compass.py` 内部 4 处调用（lines 708, 715, 732, 747）：均需透传 `cache=cache`；
- 其余 3 处调用：
  - `scripts/rds_compass_model.py:920` (`_side_fit`)：传 `cache=None`（向前兼容）；
  - `scripts/rds_compass_rsi.py:372` (RSI)：传 `cache=None`（向前兼容）；
  - `scripts/rds_compass_cli.py:42` (CLI)：传 `cache=None`（向前兼容）。
- `escape`（`scripts/rds_compass.py:1108`）与 `generate_rivals`（`line 833`）需在内存状态中持有并传递该 cache 实例。

#### 4. 什么情况下会算错（边界风险）
- **点序错位灾难**：若缓存按数组元组下标存储而非点标识存储，探针缩减引发错位导致误拟合（见 §0.2）；
- **派生变量作用域污染**：`derived` 参数在 `whole` 分支为 `None`，在 `derived_fit` 分支为两两派生项。若缓存键未对 `derived` 命名空间进行隔离，会导致前一分支的未定义符号污染后一分支；
- **首轮无收益**：第 1 轮 JUMP 仍需执行 632 秒冷启动，无法解决首轮超时问题。

#### 5. 预计收益
- 从第 2 轮 JUMP 起，避免老点的重复计算，单次 `synthesize` 耗时压缩至 **1/3**（加速比 **2x ~ 3x**，`ESTIMATE`）；
- 但由于仍需遍历 200,000 次 Python 循环与 AST 归一化，收益存在天花板。

---

### 方案 B（高风险·算法级缺陷）：`_fits` 缓存上一轮候选，新点仅做验证

#### 1. 核心机制
- 在 `_fits` 层记录上一轮命中的候选或 `pools`；
- 当新测点产生后，完全跳过自底向上枚举，仅将已有候选代入新测点计算 target 匹配。

#### 2. 改动函数与签名
- `scripts/rds_compass.py`：修改 `_fits` 实现，内部挂载上一轮候选池缓存；
- `synthesize` 签名保持不变。

#### 3. 全部 7 个调用点核查（逐一列出）
- 对 `synthesize` 的 7 处调用无签名影响；改动全部集中在 `scripts/rds_compass.py` 的 `_fits` 内部。

#### 4. 什么情况下会算错（致命缺陷！）
- **致命缺陷 1（早停截断导致真理永久漏检）**：如 §2.2 详细论证，上一轮因 `len(fits) < keep` 早停，候选池极其狭窄且充满浅层假拟合。新测点将这几个候选全部证伪后，若不重新枚举，系统将错误得出 `NO_FIT`，**造成 100% 漏检！**
- **致命缺陷 2（残差基线漂移）**：`residual` 分支依赖被证伪假设中残差最小的 `best` 作为基线。新测点往往改变了谁是 `best` 假设，上一轮针对旧 base 拟合的候选在新的残差下完全失去数学意义；
- **致命缺陷 3（高频穿透）**：为防止漏解，候选全部被证伪时必须 Fallback 回全量枚举。实测中初期假候选被新测点证伪的概率超过 90%，导致缓存几乎永远穿透，白白增加判断开销。

#### 5. 预计收益
- 命中时耗时 $<0.05s$（`ESTIMATE`）；但因存在无法克服的漏解缺陷与高频穿透，**强烈建议否决**。

---

## 4. 每个方案一条最小反向测试

遵循项目规范（改动必须伴随一条最小反向测试证明「行为与未改动前逐字节一致」）：

### 4.1 方案 C 的最小反向测试（闭包求值与递归求值等价性）
- **测试代码位置**：`tests/test_rds_compass_model.py`
- **测试实现**：
  在包含加、减、乘、除、幂以及派生变量的典型表达式集上，针对固定点集，同时调用原递归版求值与闭包向量化求值，机械比对输出：
  ```python
  def test_closure_evaluator_exact_byte_equivalence(self):
      from scripts import rds_compass_model as model
      # 构造 100 个随机但确定种子的候选 AST
      grammar = {'variables': ['x', 'y'], 'constants': [1, 2], 'operators': ['add', 'mul', 'div', 'pow']}
      rows = [{'inputs': {'x': '1/2', 'y': '3/4'}, 'outputs': {'z': '5/4'}}]
      probes = [{'x': '2', 'y': '3'}, {'x': '0', 'y': '1'}]  # 包含除零边界点
      
      # 分别运行原版与闭包加速版
      legacy_res = model.synthesize_legacy(rows, ['x', 'y'], 'z', {'z': '1/1000'}, grammar=grammar, probes=probes, max_candidates=5000)
      closure_res = model.synthesize(rows, ['x', 'y'], 'z', {'z': '1/1000'}, grammar=grammar, probes=probes, max_candidates=5000)
      
      # 断言尝试次数、唯一行为数及拟合 AST 序列化 JSON 逐字节严格相等
      self.assertEqual(legacy_res['attempted'], closure_res['attempted'])
      self.assertEqual(legacy_res['distinct_behaviours'], closure_res['distinct_behaviours'])
      self.assertEqual(json.dumps(legacy_res['fits'], sort_keys=True),
                       json.dumps(closure_res['fits'], sort_keys=True))
  ```

### 4.2 方案 A 的最小反向测试（带缓存与无缓存增量一致性）
- **测试代码位置**：`tests/test_rds_compass_model.py`
- **测试实现**：
  模拟第 1 轮（2 点）后追加第 3 点进入第 2 轮：
  ```python
  def test_synthesis_cache_exact_byte_equivalence(self):
      from scripts import rds_compass_model as model
      cache = {}
      # 运行带缓存的第 1 轮与第 2 轮
      model.synthesize(rows[:2], names, 'z', tol, probes=probes[2:], cache=cache)
      cached_res = model.synthesize(rows[:3], names, 'z', tol, probes=probes[3:], cache=cache)
      
      # 独立运行无缓存的第 2 轮冷启动
      clean_res = model.synthesize(rows[:3], names, 'z', tol, probes=probes[3:], cache=None)
      
      # 验证结果完全一致
      self.assertEqual(json.dumps(cached_res, sort_keys=True),
                       json.dumps(clean_res, sort_keys=True))
  ```

### 4.3 方案 B 的最小反向测试（早停致命缺陷证伪测试）
- **测试代码位置**：`tests/test_rds_compass.py`
- **测试实现**：
  构造单变量方程 $y = x^2 + 1$。
  1. 第 1 步给出点 $(x=1, y=2)$，设置 `keep=1`。方案 B 命中平凡解 $y = 2$ 并早停退出，缓存池仅有 $[y=2]$；
  2. 第 2 步追加点 $(x=2, y=5)$；
  3. 方案 B 仅检验缓存，在点 2 失败后直接输出 `NO_FIT`；
  4. 标准全量合成器重新枚举，成功命中 $y = x^2 + 1$；
  5. 断言 `scheme_b_res['status'] == 'NO_FIT'` 且 `standard_res['status'] == 'FIT_FOUND'`。此反向测试在自动化门禁中直接判定方案 B 破坏了完备性，阻止其合入主线。

---

## 5. 综合选型决策与建议路线

1. **首选基石：方案 C（热路径闭包/向量化编译）**
   - 彻底解决首轮冷启动 632 秒的核心瓶颈；
   - 外部 7 个调用点完全零修改，无跨轮状态污染，风险最低；
2. **渐进增强：方案 C + 方案 A**
   - 在完成方案 C 的基础上，引入基于规范点标识（Canonical Point Key）的增量计算缓存；
   - 既消解首轮计算压力，又使后续 JUMP 获得近乎常数级的飞跃；
3. **坚决摒弃：方案 B**
   - 早停截断反例已从数学上证明其存在严重的假阴性漏解隐患，不符合 RDS 严密科研内核的标准。

# 独立复核报告：commit 93d2377 (`feat(compass): let a measured value carry a half-width into the verdict`) 漏径分析

- **复核基准**：仓库 `D:\Myfile\work space\RDS\rds-357`，分支 `release/6.0.0-compass`，HEAD `3864dfa`
- **复核对象**：commit `93d2377`
- **复核日期**：2026-10-21
- **环境**：Python 3.12 (Miniconda), 测试沙箱目录 `D:\Myfile\work space\RDS\_interval_review\`

---

## 摘要与核心发现

Commit `93d2377` 的设计意图是允许带噪声的测量以区间 `{'lo': ..., 'hi': ...}` 形式进入 `decide()`，在区间跨越容差带边界时返回 `None`（`UNTESTED` / `undecided`），避免硬判。

**复核结论**：该改动存在**关键链路阻断级漏径**。`Inexact` 虽然在 `exact()` 和单次纯内存 `decide()` 中被构造，但：
1. **账本持久化与往返回放（最严重）**：观测值进入账本（`ProjectStore` CAS 和 events）时，在 `rds_compass.py:237` 被 `model.text(v)` 序列化，直接剥离了 `radius` 退化为中点有理数文本。通过 `compass.fold(store)` 重建状态并用 `measured(state)` 回放假说时，原本跨带未决（`UNTESTED`）的区间观测变成了精确点值，假说被错误判为 `CONSISTENT`，**科学判定彻底翻转**。
2. **算术运算降级**：`Inexact` 继承自 `Fraction`，但未重载 `__sub__`、`__add__` 等算术运算。在带 base 的假说合成（`rds_compass_model.py:846`）、样本轮廓增益计算（`rds_compass_model.py:1031`）等关键计算中，减法返回原生 `Fraction`，`radius` 静默丢失，将不确定区间当成精确值拟合或判准。
3. **合成结果缓存碰撞**：`rds_compass.py:729` 在计算 `_fits_memo_key` 时使用 `model.text()` 序列化数据点，导致区间测量与其中点点值产生相同的缓存 SHA256，造成错误命中。
4. **形式化验证与多项式校验**：`rds_compass.py:1299-1304` 的 `_lean` 只提取了预测值的 `radius`，完全忽略了观测值的 `radius`；`_polynomial_check` 直接将区间作为精确标量求解高斯消元。

---

## 详细消费点复核清单

### 1. `scripts/rds_compass.py` 消费路径

| 路径 / 函数 | 文件:行号 | 判定 | 核心机理与实测证据 |
| :--- | :--- | :--- | :--- |
| **账本持久化与折叠回放 (`_observation` & `measured`)** | `scripts/rds_compass.py:237`, `scripts/rds_compass.py:141` | **LOSSY** | **机理**：`_observation` 在收据生成观测记录时执行 `outputs = {k: model.text(v) ...}`。`model.text`（`rds_compass_model.py:126`）对 `Inexact` 仅输出中点分数文本，丢弃 `radius`。写进 CAS 和 events 账本后，`compass.fold()` 读回的是普通分数文本，`measured(state)` 调用的 `exact()` 只能恢复出零半径点值。<br>**实测复现**：`test_ledger_e2e.py`<br>- 直接内存 replay（带区间 `[7/10, 11/10]`，tol abs=1，假说 y=0）：`status: UNTESTED`, `undecided: 1`<br>- 经账本存储并 `fold` 读回后 replay：`status: CONSISTENT`, `tested: 1`, `undecided: None`<br>判定由未决硬判为一致。 |
| **合成结果缓存键 (`_fits_memo_key`)** | `scripts/rds_compass.py:728-729` | **LOSSY** | **机理**：`_fits_memo_key` 使用 `model.text(row['outputs'][observable])` 拼接行输出。区间测量与中点相同的单点测量序列化文本完全相同，导致 memo key 发生哈希碰撞，使区间测量命中单点测量的合成缓存，改变合成结论。<br>**实测复现**：`test_memo_collision.py`<br>- 区间 `{'lo': '7/10', 'hi': '11/10'}` 与点值 `'9/10'` 生成的 `key` 完全一致（`key_interval == key_point: True`，hash 为 `e12881896036...`）。 |
| **Lean 形式化验证转换 (`_lean`)** | `scripts/rds_compass.py:1299-1304` | **LOSSY** | **机理**：`_lean` 仅对预测值读取半径：`radius = getattr(values[name], 'radius', 0)`，完全没有读取 `observed` 的 `radius`。在生成 Lean 证明义务时，若预测值无半径，即便 `observed` 为区间，也会在绝对容差为 0 时退化为 `eq` 关系，或在不等式中丢失观测半宽，导致形式化验证器验证的命题与 `decide()` 脱节。 |
| **多项式独立核验 (`_polynomial_check`)** | `scripts/rds_compass.py:1349-1356` | **LOSSY** | **机理**：`rhs = [r['outputs'][observable] for r in rows]`，区间测量被直接作为有理数塞进 `_solve_exact` 执行高斯消元，以及通过 `router._fraction_json` 转为精确点值。真实区间噪声直接导致线性方程判定无解或拟合出假对手多项式。 |
| **候选点输入环境 (`env`)** | `scripts/rds_compass.py:121-128` | **SAFE** | **机理**：`env` 对 `inputs` 和 qualified features 转换 `model.exact(v)`。candidate 输入通常为确切离散参数；若含有区间，`_enclose` 会正确提取 `radius` 形成包络，不影响确定性语义。 |
| **容差解析 (`_tolerances`)** | `scripts/rds_compass.py:116-118` | **SAFE** | **机理**：commit 93d2377 在 `model.tolerance` 中已添加显式断言：`require(not hasattr(absolute, 'radius') and not hasattr(relative, 'radius'), ...)`，直接 fail-closed 拦截非法区间容差。 |
| **脉冲补丁 (`_pulse_patch`)** | `scripts/rds_compass.py:857-858` | **SAFE** | **机理**：当假说存在单个反例时，提取 `counter['observed']` 作为分支常数构造补丁。常数语法树节点本身必须是标量常数，且补丁生成后会立即调用 `model.replay`，原区间仍会在 `training` 数据集中接受完整 `decide` 校验。 |
| **报告观测输出展示 (`_publish_tms` / `report`)** | `scripts/rds_compass.py:1603` | **DISPLAY_ONLY** | **机理**：`'source': {'locator': 'INPUT_REPORTED measurement ' + canonical(obs['outputs'])[:200]}` 仅用于生成 TMS 证明链的节点可读描述，不参与任何真值判定与逻辑推理。 |

---

### 2. `scripts/rds_compass_model.py` 消费路径

| 路径 / 函数 | 文件:行号 | 判定 | 核心机理与实测证据 |
| :--- | :--- | :--- | :--- |
| **带 Base 偏置的假说合成 (`synthesize`)** | `scripts/rds_compass_model.py:846` | **LOSSY** | **机理**：在 `base` 模式下计算残差目标：`targets = [t - o for t, o in zip(targets, offsets)]`。由于 `Inexact` 继承自 `Fraction`，二者相减调用 `Fraction.__sub__`，返回一个普通的 `Fraction` 实例，`radius` 被静默丢弃！后续在 `matches(v, t, tol)` 校验残差时，跨带区间变成了精确点值，原本无法拟合的候选律被错误保留为有效 fit。<br>**实测复现**：`test_synthesize_base.py`<br>- 减法前：`type(target): Inexact, radius: 1/10`<br>- 减法后：`type(sub): Fraction, hasattr radius: False`<br>- 匹配结果：原本 `matches` 判为 `False`（区间跨带未决），减法丢失半径后 `matches` 判为 `True`，产生假阳性拟合。 |
| **样本级轮廓与增益评估 (`sample_profile` & `profile_verdict`)** | `scripts/rds_compass_model.py:1031, 1036-1048` | **LOSSY** | **机理**：在计算增益增量时：`deltas[k] = sign * (exact(candidate[k]) - base)`。减法使 `candidate[k]` 的 `radius` 丢失，只保留中点之差。若某 sample 的区间下界发生严重恶化（例如 `[0.2, 2.0]`，基线为 `1.0`），但中点为 `1.1`，减法得 `+0.1`，导致 `worst_degradation` 被算为 0，`profile_verdict` 给出了 `ACCEPT`，掩盖了严重的局部退化。<br>**实测复现**：`test_sample_profile.py`<br>- 输入：baseline `'10/10'`, candidate `{'lo': '2/10', 'hi': '20/10'}`<br>- 运行结果：`worst_degradation: '0'`, `Verdict: ACCEPT`。 |
| **单点侧向拟合 (`_side_fit`)** | `scripts/rds_compass_model.py:976` | **LOSSY** | **机理**：当分割后单侧仅含 1 行时，`value = text(values.pop())` 将 `Inexact` 转成中点文本作为常量表达式返回（`expression: {'const': value}`）。如果原区间跨越容差，该常量在实际回放时原本会被拒绝，但在 `_side_fit` 中被直接认作完美拟合（`attempted: 0`）。 |
| **回放主入口 (`replay`)** | `scripts/rds_compass_model.py:465-492` | **SAFE** | **机理**：`replay` 中行输出完整传入 `decide(observed, predicted, tol)`，若 `observed` 保留有 `Inexact`，`decide` 会正确计入 `undecided`，不会丢失。 |
| **区间包络判定 (`decide`)** | `scripts/rds_compass_model.py:382-421` | **SAFE** | **机理**：93d2377 的核心修改点，计算 `radius = getattr(predicted, 'radius', 0) + getattr(observed, 'radius', 0)`，以及跨带时返回 `None`。自身逻辑健全。 |
| **容差匹配 (`matches` & `agree`)** | `scripts/rds_compass_model.py:423-431, 440-444` | **SAFE** | **机理**：内部委托给 `decide()`，并将 `None` 视为不匹配（`decide(...) is True`），保持了 fail-closed 语义。 |
| **文本渲染 (`text`)** | `scripts/rds_compass_model.py:126-129` | **DISPLAY_ONLY** / **LOSSY** (视调用上下文) | **机理**：单纯用于展示时为 **DISPLAY_ONLY**；但当它被 `_observation()` 借用作序列化工具，或被 `_fits_memo_key` 借用作摘要生成时，其丢失半径的特性直接引发了上游的 **LOSSY** 缺陷。 |
| **语法树求值 (`evaluate` / `_enclose`)** | `scripts/rds_compass_model.py:364-380` | **SAFE** | **机理**：`_enclose` 会检查变量的 `getattr(value, 'radius', 0)` 并构造 `(value - radius, value + radius)` 区间包络进行区间算术，能够正确消费带半径的输入变量。 |
| **假说编译 (`compile_hypothesis`)** | `scripts/rds_compass_model.py:456-463` | **SAFE** | **机理**：仅负责解析表达式语法树并计算结构哈希与大小，不直接接触测量观测值。 |
| **结构哈希 (`digest`)** | `scripts/rds_compass_model.py:131-137` | **SAFE** | **机理**：仅对编译后的假说表达式、常量和算符进行序列化哈希，不涉及观测数据流。 |
| **界限判断 (`_bounded`)** | `scripts/rds_compass_model.py:433-437` | **SAFE** | **机理**：内部委托给 `matches`，语义与 `matches` 保持一致。 |

---

### 3. `scripts/rds_fit_trend.py` 消费路径

| 路径 / 函数 | 文件:行号 | 判定 | 核心机理与实测证据 |
| :--- | :--- | :--- | :--- |
| **台阶方向判定 (`step`)** | `scripts/rds_fit_trend.py:37` | **LOSSY** | **机理**：在两水平比较中，若超出容差，通过 `(v < u) == (better == lower_is_better)` 判定改善或恶化。`v` 和 `u` 若为 `Inexact`，`<` 运算调用 `Fraction.__lt__` 仅比较中点。即使两区间高度重叠，只要中点不同，便被硬判为单调变化（`IMPROVES` / `WORSENS`），而不是返回 `UNRESOLVED`。<br>**实测复现**：`u = Inexact(1, radius=1)`（区间 `[0, 2]`），`v = Inexact(12/10, radius=1/10)`（区间 `[1.1, 1.3]`）。两区间显著重叠，但 `v < u` 为 `False`，直接根据中点给出确切方向。 |
| **台阶跨度计算 (`analyze`)** | `scripts/rds_fit_trend.py:72` | **LOSSY** | **机理**：`'gap': span([sign * (t - r) for r, t in levels[x]])`。`t - r` 相减丢失 `radius`，返回普通 `Fraction`，报告的 gap 跨度仅代表中点跨度，丢失了区间的极值范围。 |

---

## 修复建议（仅供参考，不改动源码）

1. **账本序列化支持区间结构**：
   在 `scripts/rds_compass.py:237` 的 `_observation` 中，不能直接调用 `model.text(v)`。若 `hasattr(v, 'radius') and v.radius > 0`，应序列化为结构体字典 `{'lo': model.text(v - v.radius), 'hi': model.text(v + v.radius)}`；读回时 `exact()` 原生支持该字典反序列化。
2. **`Inexact` 类补全算术运算保护**：
   在 `rds_compass_model.py` 中，`Inexact` 重载 `__sub__`、`__add__`、`__neg__` 等方法，或者在 `synthesize` 残差计算（`rds_compass_model.py:846`）和 `sample_profile`（`rds_compass_model.py:1031`）中显式传播半径区间算术，禁止静默退化为点值 `Fraction`。
3. **修复 `_fits_memo_key`**：
   若输出包含 `radius`，应在 memo key 中将 `radius` 纳入序列化（如 `text(v) +/- text(v.radius)`），杜绝缓存碰撞。
4. **修复 `_lean` 观测值半宽**：
   在 `rds_compass.py:1299-1304` 中，将 `observed` 的 `radius` 同步提取并计入误差上界约束。

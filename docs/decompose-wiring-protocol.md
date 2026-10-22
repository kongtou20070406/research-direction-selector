# 反向分解接线：预注册验收协议

- 仓库：`D:\Myfile\work space\RDS\rds-357`，分支 `release/6.0.0-compass`，基线 `bd0d58b`
- 涉及模块：`scripts/rds_compass_model.py`（`synthesize`）、`scripts/rds_compass_decompose.py`（`decompose`，不改语义）
- 状态标记：**§1–§5 写于跑任何接线实验之前**（2026-10-21 接线前）；§6 起是实测结果与判定。

## 0. 三行自检

- 本轮要提升的能力：让 `synthesize` 在满足条件时先试反向分解，用更少的候选换同样的结果。
- 可证伪信号：接线前后每个 case 的 `fits` 规范化形式逐 case 完全相同，门槛 **100%**，一个不同即失败。
- 绝对不允许：改变 `fits`；把「值向量相同」偷换成「规范化形式相同」；跳过 case 挑好看的报；事后补口径。

## 1. 主指标（预注册）

**规范化形式**：把一个 `fits` 列表里每个 fit 的 `expression` 用 `model.render` 出一串，得到字符串集合，排序去重。

```
canon(result) = sorted({model.render(fit['expression']) for fit in result['fits']})
```

**主指标** = `canon(接线后)` 与 `canon(接线前)` 逐 case 相同的数量占比。

**门槛：100%**。任一个 case 不同即判定失败，不做「差一个是等价的所以算过」的豁免。

## 2. 启用条件（预注册，四条全部成立才尝试 `decompose`）

1. **零容差**：`tolerances[observable] == (0, 0)`。
   口径说明：`synthesize` 内部 `targets = [exact(...)]`，`exact()` 永远返回 `Fraction`（float 走 `Fraction(repr(v))`），
   所以「观测目标值都是精确有理数」在 `synthesize` 入口**恒成立**；`decompose` 里的 `Inexact` 只可能来自
   中间区间的 enclosure，不会出现在 targets 里。因此条件 1 的实际判别力全部落在「没有容差带」这一条上，
   用零容差判（`decompose` 靠值向量完全相等查表，非零容差下查表本身就是错的）。
2. **算子集受限**：`grammar` 的 operators `⊆ {add, sub, mul, div}`（`decompose.INVERSE_OPERATORS`）。
   `pow / sqrt / exp / log / sin / cos / neg / abs / floor / ceil / pi` 任一出现即不尝试。
3. **观测点数 ≥ 2**：1 个点反解不出缺失侧。
4. **开关**：`RDS_NO_DECOMPOSE=1` 时完全绕过，走纯枚举。

回退静默：`decompose` 抛异常、返回空 `fits`、或超时一律走旧路径；`KeyboardInterrupt` / `SystemExit` 不吞。

## 3. 对照 case 集（预注册）

| # | case | 行数 | grammar operators | 预期路径 | 用途 |
| --- | --- | ---: | --- | --- | --- |
| 1 | `feynman-I.18.4` 全部 54 行 | 54 | add/div/mul | 触发 | 主战场（报告称 190×） |
| 2 | `feynman-I.18.4` 前 2 行 | 2 | add/div/mul | 触发 | 下界：信息不足时的行为 |
| 3 | `feynman-I.18.4` 前 6 行 | 6 | add/div/mul | 触发 | 报告称 decompose 0.15s 解出 |
| 4 | `feynman-I.12.1` | 25 | mul | 触发 | size-3，最简单 |
| 5 | `feynman-II.2.42` | 32 | div/mul/sub | 触发 | 含 sub，4 变量 |
| 6 | `feynman-I.6.20a` | 25 | neg/sub/div/pow/exp/sqrt/pi | **不触发** | 反向验证：不支持的 grammar 必须回退 |
| 7 | `math-triangular` | 8 | add/mul/div | 触发 | 新数学方程，size 7 |

口径统一：`observables='y'`，`max_nodes=7`，`probes=()`，`keep=4`，容差取 devset 声明值。
`max_candidates` 取生产路径对应分支的值（`_fits` 的 WHOLE_LAW 用 `bound['max_candidates']`，默认 20000；
DERIVED 分支用 `min(200000, bound['max_candidates'] * 10)`）。两种口径都测，分别标注。

## 4. 加速指标（预注册）

在**能触发 decompose 的 case** 上记录：接线前 `synthesize` 耗时、`decompose` 耗时、接线后实际耗时，
以及各自的 `attempted`。表里给绝对值，不停在「快了 X 倍」。

## 5. 反向测试（预注册，名字先定死）

- `test_no_decompose_switch_matches_the_old_head`：`RDS_NO_DECOMPOSE=1` 时 `synthesize` 的返回与接线前基线逐字段相同
  （基线快照由 `git stash` 前后的两次运行对照，或直接对照基线提交上的函数输出）。
- `test_unsupported_grammar_takes_the_old_path`：`feynman-I.6.20a` 的 grammar 含 `pow/exp/sqrt/pi`，判定为不触发，
  返回与旧版逐字段相同。
- 判据：两条都通过才算接线成立；任一条不通过即回滚。

## 6. 实测（2026-10-21）

跑法：把接线逻辑写成**仓库外**的模拟函数（`decide -> decompose -> 失败静默回退 enumerate`），
不修改任何源码，直接对比「接线前 `model.synthesize`」与「模拟接线后的返回」。
脚本：`_wiring_accept.py`（主指标）、`_wiring_verify.py`（等价性）、`_wiring_probe.py`（加速口径）。

### 6.1 主指标逐 case 表

口径：`observables='y'`，`max_nodes=7`，`probes=()`，`keep=4`，`max_candidates=20000`（`_fits` 的 WHOLE_LAW 默认）。

| # | case | 行 | 旧路径 | 新路径（模拟接线） | 规范化形式 | 旧耗时 | 新耗时 | 实际走哪条 |
| --- | --- | ---: | --- | --- | --- | ---: | ---: | --- |
| 1 | `feynman-I.18.4` 前 2 行 | 2 | FIT `((r1 + r2) / 2)` | FIT 同 | **相同** | 0.10s | &lt;0.01s | decompose |
| 2 | `feynman-I.18.4` 前 6 行 | 6 | NO_FIT，0 fit | NO_FIT，0 fit | **相同** | 0.61s | 0.63s | enumerate（decompose 0 fit 回退） |
| 3 | `feynman-I.18.4` 全部 54 行 | 54 | SEARCH_TRUNCATED，0 fit | SEARCH_TRUNCATED，0 fit | **相同** | 7.06s | 7.02s | enumerate（decompose 0 fit 回退） |
| 4 | `feynman-I.12.1` | 25 | FIT `(mu * n)` | FIT 同 | **相同** | 0.01s | &lt;0.01s | decompose |
| 5 | `feynman-II.2.42` | 32 | FIT `(kappa / (d / (t2 - t1)))` | FIT `((kappa / d) * (t2 - t1))` | **不同** | 2.20s | 0.03s | decompose |
| 6 | `math-triangular` | 8 | FIT `(n / (2 / (1 + n)))` | FIT `((1 + n) * (n / 2))` | **不同** | 0.08s | &lt;0.01s | decompose |
| 7 | `feynman-I.6.20a` | 25 | SEARCH_TRUNCATED，0 fit | SEARCH_TRUNCATED，0 fit | **相同** | 9.15s | 9.00s | enumerate（非零容差挡住） |

**主指标 = 5/7 = 71.4% < 100% → 判定失败。**

### 6.2 两个 DIFFERENT 的根因：同一值向量、不同语法

两个 case 的旧/新 fit 都**全点零容差精确匹配**，且在全部观测点上值向量完全相同：

| case | 旧 fit | 新 fit | 全点精确匹配 | 值向量 |
| --- | --- | --- | --- | --- |
| `feynman-II.2.42` | `(kappa / (d / (t2 - t1)))` size 7 | `((kappa / d) * (t2 - t1))` size 7 | 两者都是 True | identical=True |
| `math-triangular` | `(n / (2 / (1 + n)))` size 7 | `((1 + n) * (n / 2))` size 7 | 两者都是 True | identical=True |

机制：`synthesize` 按枚举顺序保留前 `keep` 个匹配（`rds_compass_model.py:896-898`），
`decompose` 按「值向量去重 + size 最小 + `(size, render)` 排序」取前 `keep`（`rds_compass_decompose.py:271-277`）。
同一观测等价类的两个语法形式在两条路径里的排序不同，**任何加速接线都无法保证 render 串一致**。
要 100% 相同，只能先跑一遍 enumerate 再比较——那就没有加速。

不是 `keep` 设小能绕过的：`keep=1` 与 `keep=4`（生产值，`rds_compass.py:755` 的 `keep=4`）都实测过，
两个 case 在两种 keep 下都给出不同形式（keep=1 时各自只返回一个，仍是 `(kappa / (d / (t2 - t1)))` vs `((kappa / d) * (t2 - t1))`）。
只有「解唯一」的 case（如 I.18.4 前 2 行、I.12.1）才不受影响。

### 6.3 derived 口径：收益存在，但恰好落在条件 2 挡掉的调用上

生产路径里唯一带 `derived` 的调用是 `_fits` 的 DERIVED 分支（`rds_compass.py:803-817`），
它的 grammar 是 `原 operators ∪ EXTENDED_OPERATORS`，而
`EXTENDED_OPERATORS = ('add','sub','mul','div','sqrt','exp','log','sin','cos','pi')`（`rds_compass.py:680`）。
**只要进入这个分支，条件 2 就一定不满足**（含 `sqrt/exp/log/sin/cos/pi`），所以按预注册的启用条件，
这条加速路径在生产代码里永远不会被尝试。

但如果**绕过条件 2** 直接跑（`max_candidates=200000`，grammar 含扩展算子，`derived` = 12 个两两组合）：

| case | `synthesize` | `decompose` | 规范化形式 | 加速 |
| --- | ---: | ---: | --- | ---: |
| `feynman-I.18.4` 前 6 行 | 61.07s，attempted=200000 | 0.15s，attempted=3696 | `((m2_times_r2 + r1) / m1_plus_m2)`，**相同** | 407× |
| `feynman-I.18.4` 全部 54 行 | 343.40s，attempted=200000 | 1.64s，attempted=7281 | `((m1_times_r1 + m2_times_r2) / m1_plus_m2)`，**相同** | 209× |

这两组数字复现了 `docs/decompose-report.md` 的 190× 量级（343.40/1.64 = 209）。
**结论：任务书表格里的加速是真的；但它与预注册的启用条件互斥。**

顺带修正一个条件 2 的误解：`decompose` 遇到 `sqrt/exp/...` 并不报错，它只是**不把这些算子当外层反解**
（`_build_pools` 仍会把一元算子放进池，`INVERSE_OPERATORS` 只筛外层）。所以「grammar 含扩展算子」
会让池变大、可能漏掉外层是该算子的解，但不会产生错误 fit——安全网（全点复算）仍在。
条件 2 保守得过头，可这正是本次接线不敢放宽的地方：放宽后 `feynman-II.2.42` / `math-triangular`
那类「等价但语法不同」的替换会扩散到更多 case。

### 6.4 反向测试（模拟接线口径）

| 名字 | 断言 | 结果 |
| --- | --- | --- |
| `test_no_decompose_switch_matches_the_old_head` | `RDS_NO_DECOMPOSE=1` 时走 enumerate，返回与旧版逐字段相同 | **PASS** |
| `test_unsupported_grammar_takes_the_old_path` | `feynman-I.9.18`（零容差、含 `pow`）被条件 2 挡住，返回与旧版相同 | **PASS** |
| `test_inexact_band_takes_the_old_path` | `feynman-I.6.20a`（1/1000 容差带）被条件 1 挡住，返回与旧版相同 | **PASS** |

预注册时把 `feynman-I.6.20a` 当成「grammar 不支持」的反例，实测发现它**先被条件 1（非零容差）挡住**，
`enable_reason` 根本没走到 grammar 判定。条件 2 的挡板改用 `feynman-I.9.18`（`pow`，零容差）单独验证。

### 6.5 若真接线，还有一个必须先修的坑（本次未触发，因为没接线）

`_fits_memo_key`（`rds_compass.py:716-741`）不包含 `RDS_NO_DECOMPOSE`。
同一进程内先跑接线路径、再设 `RDS_NO_DECOMPOSE=1`，会命中旧 memo 并返回接线后的结果——
「开关必须真的能关掉」在生产路径上就不成立。接线成立前必须把开关并入 memo key（或清 memo）。

## 7. 判定

**主指标 71.4%，未达 100% 门槛 → 不接线，零代码改动。**
`scripts/rds_compass_model.py`、`scripts/rds_compass_decompose.py`、`tests/` 均未改动。

按预注册规则，失败原因记录如下（`.harness/LESSONS.md` 另记一条）：

- 「反向分解能加速」与「`synthesize` 返回不变的 fits」在 `keep>1`、观测等价类有多个语法形式时**不可同时满足**；
- 能守住 fits 不变且真加速的调用（DERIVED 分支，209×/407×）被启用条件 2 挡死；
- 启用条件 2 放行的调用里，两个 case 给出了等价但 render 不同的形式。

## 8. 如果放宽口径（需要维护者拍板，不是本次的执行范围）

- **方案 A（建议）**：主指标改成「每个 fit 的值向量签名集合」，并把启用条件放宽为「`decompose` 成功即用」。
  现有 7 个 case 全部相同（§6.1 的 5 个 + §6.2 的 2 个值向量相同），且 DERIVED 分支可拿到 209×/407×。
  代价：`fits` 的**语法形式**会变，下游若按 render 串做 memo/digest 的地方需要重算。
- **方案 B**：保持 render 判据，只在 `decompose` 与 `synthesize` 结果相同的调用上替换——
  必须先跑两种搜索，没有加速，只剩 `attempted` 上的交叉验证价值。
- **方案 C**：把 `decompose` 用作 DERIVED 分支的**优先尝试**，但把 DERIVED 的 grammar 改回四则运算
  （`derived` 已经吸收了中间量，扩展算子在 I.18.4 上并不参与解）。这会改变 DERIVED 分支的搜索空间，
  属于另一个 feature slice，且需要单独证明「缩小 grammar 不会丢掉非四则解」。

## 9. 复现命令

```
# 主指标（仓库外模拟脚本）
python -B _wiring_accept.py cheap
python -B _wiring_accept.py all54
# 等价性
python -B _wiring_verify.py
# derived 口径的加速数字（约 7 分钟）
python -B _wiring_probe.py derived
# 基线测试
python -B -m unittest discover -s tests -p 'test_rds_compass_model.py'      # 27 OK
python -B -m unittest discover -s tests -p 'test_rds_compass_decompose.py'  # 7 OK
python -B -m unittest discover -s tests -p 'test_rds_compass_routing.py'    # 5 OK
python -B -m unittest discover -s tests -p 'test_rds_compass_drive.py'      # 31 OK, 311.3s
```


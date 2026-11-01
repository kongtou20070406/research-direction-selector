# 一个被容差带吞掉的方程：feynman-I.41.16

日期：2026-10-21。commit：容差修复 + `tests/test_newtonbench_tolerance.py`。

## 结论

`devset-run-30-fixed.json` 里 `feynman-I.41.16` 的 3 格（B=8/16/32）
`status=GOAL_IDENTIFIED`、`claim_exact=True`、**`law="0"`** —— 解出来的是常数 0。
这不是找到了真解，是判分带比真值宽五个量级。

修正后同一个方程三档全部 `SEARCH_EXHAUSTED`、`claim_exact=False`。
诚实正确率因此从 **误记的 3 格** 回到 0 格，`devset-run-30-fixed.json` 的
claim_exact 合计要减 3。

## 机制

`benchmark/newtonbench/devset.py:45` 的 `_float_spec` 给所有超越函数方程
同一个绝对容差 `{'abs': '1/1000', 'rel': '1/1000'}`。

`_gen_feynman_I_41_16` 是普朗克谱密度：

```
u = hbar*omega**3 / (pi**2 * c**2 * (exp(hbar*omega/(k*T)) - 1))
```

`hbar = 1.054571817e-34`、`c = 2.99792458e8`，9 个声明点的真值落在
`1.05e-08` 到 `8.09e-08` 之间。`abs = 1e-3` 比最大的真值大 **12400 倍**。

`decide` 的判定是 `|pred - obs| <= abs + rel*|obs|`，`pred = 0` 时左边就是
`|obs| <= 1e-3` —— 9 个点全部通过。于是常数 0 被当作「复现全部测量」的候选，
`claim_exact` 判真。

## 影响面（全量审计，40 个方程）

对每个方程算 `abs_tol / max|y|`：

| 方程 | abs_tol | max\|y\| | 比值 | 判定 |
|---|---|---|---|---|
| `feynman-I.41.16` | 1e-03 | 8.09e-08 | **1.24e+04** | 任何预测都通过 |
| `math-sin-index` | 1e-03 | 9.89e-01 | 1.01e-03 | 正常 |
| `feynman-I.6.20a` | 1e-03 | 3.99e-01 | 2.51e-03 | 正常 |
| 其余 37 个 | — | 更大或零 | &lt; 1e-3 | 正常 |

**只有这一个方程受影响。** 另外 22 个方程 `abs_tol` 本来就是 `0`（纯有理数），
不参与这条判据。审计脚本：`D:\Myfile\work space\RDS\tolerance_audit.py`。

## 修法

`benchmark/newtonbench/devset.py` 的 `_gen_feynman_I_41_16` 返回前把
`spec['tolerances']['y']['abs']` 置为 `'0'`，只留 `rel = 1/1000`。

理由：这条 law 对输入的尺度是齐次的（`omega**3` 对 `exp(omega/T)`），
判相对误差不会放大浮点误差；`rel = 1e-3` 对 `math.exp` 的舍入余量足够。

## 重跑证据

```
python -B benchmark/newtonbench/devset_run.py benchmark/newtonbench/results/devset-i4116.json feynman-I.41.16
```

| B | status | claim | law | counterexamples | claim_exact | experiments | seconds |
|---|---|---|---|---|---|---|---|
| 8 | SEARCH_EXHAUSTED | NOT_ESTABLISHED | null | null | **false** | 8 | 396.2 |
| 16 | SEARCH_EXHAUSTED | NOT_ESTABLISHED | null | null | **false** | 9 | 146.0 |
| 32 | SEARCH_EXHAUSTED | NOT_ESTABLISHED | null | null | **false** | 9 | 54.8 |

结果写 `benchmark/newtonbench/results/devset-i4116.json`。

## 为什么本来就该失败

真解 `hbar*omega**3 / (pi**2*c**2*(exp(hbar*omega/(k*T)) - 1))` 的 AST size 是 **24**，
而 `synthesis.max_nodes` 默认 **7**。这个方程从来都在结构性天花板之外
（见 [size-ceiling-20261021.md](benchmarks/size-ceiling-20261021.md)），
`SEARCH_EXHAUSTED` 才是它应有的答案。

## 守门测试

`tests/test_newtonbench_tolerance.py`：

1. `test_no_equation_is_graded_with_a_band_at_its_own_scale` —— 遍历全部 40 个方程，
   任何 `abs_tol * 100 >= max|y|` 直接失败。以后新增量级小的方程会被这条挡住。
2. `test_the_constant_zero_no_longer_certifies_on_the_spectral_density` —— 常数 0
   在该方程 9 个点上必须一个都不过。

反向验证：撤掉 `devset.py` 的修复，两条都失败；恢复后 2 tests OK。

## 未做

`devset-run-30.json` 与 `devset-run-30-fixed.json` **没有改写**。它们是历史运行记录，
里面 `feynman-I.41.16` 那 3 行（`-fixed` 里的）是旧口径的产物；读这两个文件时
必须扣掉这 3 格。要一份干净的全量基线需要重跑 90 格（约 3.8 小时），
排在下一轮。

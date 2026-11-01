# devset 失败面：87% 的算力花在 12 个不可能成功的格上

结果文件：`benchmark/newtonbench/results/devset-run-30-fixed.json`（23 格重跑，对应 `devset-run-30.json` 的 23 个异常格）。

## 1. 23 格重跑的账

| 状态 | 格数 | 耗时 |
|---|---|---|
| `TIMEOUT`（900s） | 12 | 10800.0s |
| `GOAL_IDENTIFIED` | 10 | 1568.9s |
| `SEARCH_EXHAUSTED` | 1 | 640.8s |
| 合计 | 23 | **12368.9s** |

**12 个 TIMEOUT 占 87.3% 的总耗时**，没有一格产出 claim。

逐方程：

| 方程 | 真解 size | 三档状态 | 备注 |
|---|---|---|---|
| `feynman-I.40.1` | 13 | TIMEOUT ×3 | |
| `feynman-III.10.19` | 14 | TIMEOUT ×3 | |
| `feynman-I.18.4` | 11 | GOAL(8, 727.7s) / TIMEOUT / TIMEOUT | 只有 B=8 解出 |
| `feynman-II.34.11` | 9 | TIMEOUT ×2 / SEARCH_EXHAUSTED(32, 640.8s) | |
| `feynman-I.10.7` | 12 | TIMEOUT(32) | |
| `feynman-II.24.17` | 16 | TIMEOUT(32) | |
| `feynman-I.41.16` | 24 | GOAL ×3（4.3/4.9/4.6s） | **容差假通过，见 §2** |
| `feynman-II.38.3` | — | GOAL ×3（20.6/43.9/92.0s） | 真解 `(x / (d / (A * Y)))` |
| `feynman-I.26.2` | — | GOAL(32, 17.0s) | 真解 `(sin(theta1) / sin(theta2))` |
| `feynman-II.27.18` | — | GOAL(32, 4.3s) | |
| `feynman-II.38.14` | — | GOAL(32, 8.8s) | |

12 个 TIMEOUT 全部落在 size>7 的五个方程上，6 个耗时 900.0s 是硬上限，另有一格在 640.8s 撞到 `max_candidates` 后 `SEARCH_EXHAUSTED`。

**900.0s 不全是结构性的。** 同一台机器稍后在空载下重跑 `feynman-I.10.7` / B=32，得到的是 `SEARCH_EXHAUSTED`、9 次实验、**484.5 秒**，不是 900 秒超时。它的声明只有 9 个候选点（`m_0 ∈ {1,2,5}` × `v ∈ {1e8,2e8,2.5e8}`），9 次实验买完就退出，时间全花在 `synthesize` 上。所以这批 TIMEOUT 里掺着并发负载，与 `docs/benchmarks/size-ceiling-20261021.md` 的 `I.8.14` 那组读数（同一配置 167.3s 与 280.8s，1.68×）是同一个现象。**把「提前放弃」当杠杆时要先扣掉这一项**：这些格在空载下本来就会在 500–650 秒之间自己退出。

## 2. `feynman-I.41.16`：绝对容差把量级吃掉了

`benchmark/newtonbench/devset.py:529-537` 的真解是

```
u = hbar*omega**3 / (pi**2 * c**2 * (exp(hbar*omega/(k*T)) - 1))
```

采样盒把 `hbar*omega/(k*T)` 卡在 [1.27, 3.83]，代入 `hbar = 1.054571817e-34`、`k = 1.380649e-23`、`c = 2.99792458e8`，量级约 **1e-10**。

三格交出的 law 逐字是 `'0'`，`status=GOAL_IDENTIFIED`、`claimed=True`、`counterexamples=0`、`false_certification=False`。

独立认证记录（`devset-run-30-fixed.json`，B=8）原文：

```json
{"points_checked": 32,
 "max_abs_error": "2856519473273363550652931146073950678529/40000000000000000000000000000000000000000000000",
 "tolerance": "1/1000",
 "tolerance_rel": "1/1000",
 "reason": "EQUIVALENT on 32 sampled points",
 "evaluation_failures": 0,
 "worst_point": {"omega": "267944892000000000000000/125810597", "T": "1623774733000/283283801"}}
```

`max_abs_error` 的分数值约 **7.14e-8**，小于绝对容差 `1/1000 = 1e-3`。也就是说：**把答案写成常数 0，与真值的差仍在容差之内**，于是认证给出的结论是 `EQUIVALENT`，`false_certification=False`。

这不是 `certify_run` 的缺陷（`benchmark/newtonbench/devset_run.py:206-259` 的判定链与记录自洽），是**容差口径**的问题：`absolute=1e-3` 对一个 1e-10 量级的被测量没有任何判别力，相对项 `relative=1e-3` 乘上去只有 1e-13，被绝对项完全覆盖。

后果：`law='0'` 这个**什么都没解出来**的答案被记成 3 个正确解。

**已修**：commit `9fcf7d6`（`fix(benchmark): grade the spectral density relatively so zero stops certifying`）在 `benchmark/newtonbench/devset.py:558` 加了 `spec['tolerances']['y']['abs'] = '0'`，把这一个方程改成只有相对容差。所以 `devset-run-30-fixed.json` 里那三行是修复**之前**的读数，重跑才能拿到修复后的结果。修法没有推广到其他方程，其余 29 个方程仍是 `abs=1/1000`。

## 3. 修正后的通过率

把 `I.41.16` 的三格从分子里拿掉：

| 切片 | 格数 | `claim_exact` | 通过率 |
|---|---|---|---|
| size ≤ 7 | 60 | 54 | 90% |
| size > 7 | 30 | **0** | 0% |

此前记的「size>7 有 3/30 通过」来自 `I.41.16` 的三格，剔除后为 0。

这与 `benchmark/newtonbench/results/truth-size.json` 和 `docs/benchmarks/size-ceiling-20261021.md` 的结构结论一致：默认 `max_nodes=7`，40 个真解里 10 个 size>7，天花板是枚举器的形状，不是预算。

## 4. 修复容差不是免费的

`9fcf7d6` 关掉绝对容差之后，同一个 `feynman-I.41.16` / B=8 重跑：

| | 状态 | law | 耗时 |
|---|---|---|---|
| 修前 | `GOAL_IDENTIFIED` | `'0'` | 4.3 s |
| 修后 | `SEARCH_EXHAUSTED` | `None` | **392.0 s** |

三格加起来从 13.8 秒变成约 1176 秒，**85 倍**。这不是回退——原来那 4.3 秒买到的是一个错误的答案。但把它记进账：**正确性修复的方向是把「便宜但错」换成「贵但诚实」**，帕累托前沿上这不是免费移动。

## 5. 这份账指向什么

- 12 个 TIMEOUT 是**纯浪费**：它们不会成功，但每个都要烧满 900 秒墙钟。识别「合成连续撞 `SEARCH_TRUNCATED`」并提前放弃，理论上能回收 87% 的这段算力。
- 但这属于改搜索行为，必须预注册 + 全量安全项，不能拿单格结论提交（`docs/benchmark-confirmreserve-20261021.md` 与 `docs/pareto-holdoutfirst.md` 两次都是单格绿、全量红）。
- `I.41.16` 的绝对容差已经在 `9fcf7d6` 按量级关掉了，但**没有推广**：其余 29 个方程仍是 `abs=1/1000`。是否要按每个方程的被测值量级逐个定容差，是声明期的事，影响主指标分子，需要单独预注册。

## 复现

```bash
python -B -m benchmark.newtonbench.devset_run --only feynman-I.41.16 --budgets 8
```

单格验证 `law='0'` 会在 4 秒左右返回 `GOAL_IDENTIFIED`。

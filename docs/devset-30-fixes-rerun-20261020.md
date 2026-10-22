# devset-30 修复后重跑：23 个异常 cell

日期：2026-10-20 / 2026-10-21（本机时间约 06:49–10:20）
发起方式：`run_one(name, budget, base)` 逐格调用，23 格，单格 900s 硬超时
结果文件：[devset-run-30-fixed.json](../benchmark/newtonbench/results/devset-run-30-fixed.json)

## 0. 启动时状态

```
$ git log --oneline -1
cacc9eb fix(benchmark): declare devset campaigns the kernel can accept

$ git status --porcelain
?? benchmark/newtonbench/results/devset-run-30-fixed.json
?? benchmark/newtonbench/results/devset-run-30.json
?? docs/devset-30-baseline-20261020.md
```

- 代码版本以启动时 HEAD = `cacc9eb` 为准。运行期间没有改动 `scripts/` 和 `benchmark/newtonbench/devset*.py`；收尾时 HEAD 仍是 `cacc9eb`。
- 启动时工作区里已有 2 个未跟踪文件（`devset-run-30.json` 基线、`docs/devset-30-baseline-20261020.md`），不是本轮产生。本轮只新增 2 个：`devset-run-30-fixed.json` 与本报告。
- 交付文件验收：sha256 未核对前先按内容判为 UNVERIFIED，见 §6。

## 1. 为什么不直接看基线

基线 `devset-run-30.json` 的 90 格里，这 23 格行内**没有 `status`**，只有 `error`：

- A 组（12 格）与 B 组（6 格）在**声明阶段**就崩了，耗时 0.0s，一次实验都没跑；
- C 组（3 格）在 ~292s 搜索后崩，D 组（2 格）在 ~4s 崩。

基线 `claim_exact` 汇总 57/90，这 23 格 **全部为 0**（不是答错，是没答）。所以这一轮的任务是「让它们有机会答题」，不是「提高准确率」。

## 2. 逐格对照表

「原异常」取基线行里的 `error`；「新 status」取本轮结果行；`—` 表示没有可评估的 status。

| # | 方程 | B | 原异常 | 新 status | claim_exact | experiments | 耗时 (s) |
|---|---|---|---|---|---|---|---|
| A1 | feynman-I.18.4 | 8 | Declare 1..64 candidates | GOAL_IDENTIFIED | **true** | 8 | 727.7 |
| A2 | feynman-I.18.4 | 16 | Declare 1..64 candidates | TIMEOUT | false | — | 900.0 |
| A3 | feynman-I.18.4 | 32 | Declare 1..64 candidates | TIMEOUT | false | — | 900.0 |
| A4 | feynman-I.40.1 | 8 | Declare 1..64 candidates | TIMEOUT | false | — | 900.0 |
| A5 | feynman-I.40.1 | 16 | Declare 1..64 candidates | TIMEOUT | false | — | 900.0 |
| A6 | feynman-I.40.1 | 32 | Declare 1..64 candidates | TIMEOUT | false | — | 900.0 |
| A7 | feynman-II.38.3 | 8 | Declare 1..64 candidates | GOAL_IDENTIFIED | **true** | 8 | 20.6 |
| A8 | feynman-II.38.3 | 16 | Declare 1..64 candidates | GOAL_IDENTIFIED | **true** | 16 | 43.9 |
| A9 | feynman-II.38.3 | 32 | Declare 1..64 candidates | GOAL_IDENTIFIED | **true** | 32 | 92.0 |
| A10 | feynman-III.10.19 | 8 | Declare 1..64 candidates | TIMEOUT | false | — | 900.0 |
| A11 | feynman-III.10.19 | 16 | Declare 1..64 candidates | TIMEOUT | false | — | 900.0 |
| A12 | feynman-III.10.19 | 32 | Declare 1..64 candidates | TIMEOUT | false | — | 900.0 |
| B1 | feynman-I.10.7 | 32 | budget.experiments must be 0..18 | TIMEOUT | false | — | 900.0 |
| B2 | feynman-I.26.2 | 32 | budget.experiments must be 0..18 | GOAL_IDENTIFIED | **true** | 9 | 17.0 |
| B3 | feynman-I.41.16 | 32 | budget.experiments must be 0..18 | GOAL_IDENTIFIED | **true** | 9 | 4.6 |
| B4 | feynman-II.24.17 | 32 | budget.experiments must be 0..18 | TIMEOUT | false | — | 900.0 |
| B5 | feynman-II.27.18 | 32 | budget.experiments must be 0..16 | GOAL_IDENTIFIED | **true** | 8 | 4.3 |
| B6 | feynman-II.38.14 | 32 | budget.experiments must be 0..18 | GOAL_IDENTIFIED | **true** | 9 | 8.8 |
| C1 | feynman-II.34.11 | 8 | Undeclared variable: B_times_g_factor | TIMEOUT | false | — | 900.0 |
| C2 | feynman-II.34.11 | 16 | Undeclared variable: B_times_g_factor | TIMEOUT | false | — | 900.0 |
| C3 | feynman-II.34.11 | 32 | Undeclared variable: B_times_g_factor | SEARCH_EXHAUSTED | false | 13 | 640.8 |
| D1 | feynman-I.41.16 | 8 | KeyError: 'op' | GOAL_IDENTIFIED | **true** | 8 | 4.3 |
| D2 | feynman-I.41.16 | 16 | KeyError: 'op' | GOAL_IDENTIFIED | **true** | 9 | 4.9 |

A 组原异常全文 `ValueError: Declare 1..64 candidates`；B 组全文 `ValueError: budget.experiments must be an integer from 0 to 18`（II.27.18 是 `0 to 16`）；C 组全文 `ValueError: Undeclared variable: B_times_g_factor`；D 组 `KeyError: 'op'`。

## 3. 异常消失了没有

**全部 23 格都不再抛原来的异常。** 0 格报告 `error` 字段，例外只有超时标记 `TIMEOUT: exceeded 900s, process killed`（那是 harness 的外部杀进程，不是内核异常）。

但「不抛异常」不等于「正常出 status」：

| 结局 | 格数 | 方程 / B |
|---|---|---|
| `GOAL_IDENTIFIED`，claim_exact = true | **10** | I.18.4@8；II.38.3@8/16/32；I.26.2@32；I.41.16@32；II.27.18@32；II.38.14@32；I.41.16@8；I.41.16@16 |
| `SEARCH_EXHAUSTED`，claim_exact = false | 1 | II.34.11@32 |
| `TIMEOUT`（900s 被杀，无 status） | 12 | I.18.4@16/32；I.40.1@8/16/32；III.10.19@8/16/32；I.10.7@32；II.24.17@32；II.34.11@8/16 |

按组：

- **A 组 12 格**：声明阶段修复生效（81 → 54 行），12 格全部进入真实搜索。4 格出 `GOAL_IDENTIFIED`（I.18.4@8 与 II.38.3 三档），8 格 900s 超时。
- **B 组 6 格**：声明阶段修复生效（experiments 被截到 9/9/9/9/8/9）。5 格出 `GOAL_IDENTIFIED`，只有 I.10.7@32 超时。
- **C 组 3 格**：原来 292s 崩的 `Undeclared variable` 不再出现，但**没有一格 claim 出来**。B=32 跑了 640.8s 后 `SEARCH_EXHAUSTED`；B=8、B=16 900s 被杀。
- **D 组 2 格**：`KeyError: 'op'` 修复生效，两格都在 5s 内 `GOAL_IDENTIFIED` 且 claim_exact = true。

## 4. claim_exact 的变化

− **基线 90 格**：57/90 = 0.633
− **这 23 格基线**：0/23（全是异常行，没有任何 claim）
− **这 23 格重跑**：10/23，且 10 格全部 `false_certification = false`
− **合并后**（57 − 0 + 10，其余 67 格沿用旧值）：**67/90 = 0.744**

合并口径说明：这只是**算术合并**，不是一次统一重跑。剩下 67 格是在 `cacc9eb` 之前产生的，本轮没有重跑，所以 67/90 只能写作「合并估计」，不能等同于「在 `cacc9eb` 上跑满 90 格」的结果。在 `cacc9eb` 上重跑完整 90 格的结果是 UNKNOWN。

20 个超时 / 耗尽格的占比：12/23 是超时，这些格子在合并统计里按「无 claim」计 0 分——这个口径符合 `claim_exact_rule: status == GOAL_IDENTIFIED and 0 counterexamples on full devset replay`，但也意味着「跑不完」和「跑完但答错」在分数上不可区分。

## 5. 耗时

23 格总墙钟 `elapsed_seconds = 12373.6`（约 3h26m）。

**最慢三格**：

| 排名 | 方程 / B | 耗时 (s) | 结局 |
|---|---|---|---|
| 1 | feynman-II.34.11 / 32 | **640.8** | SEARCH_EXHAUSTED（唯一跑完的长格） |
| 2 | feynman-II.34.11 / 8 | 900.0 | TIMEOUT |
| 3 | feynman-II.34.11 / 16 | 900.0 | TIMEOUT |

其余 900.0s 超时格并列第二档（A 组 8 格 + I.10.7@32 + II.24.17@32），共 11 格都是撞满 900s 被杀，排序无意义。最慢的**有结果**的一格是 I.18.4@8 的 727.7s。

超时格合计：12 × 900 = 10800s，占总墙钟 87%。

## 6. 超时原因（逐条看过 ledger，不是猜）

读取各超时 cell 的 `.rds/project.sqlite3` 与 `COMPASS_JUMP` 记录：

| cell | 进到第几个观测 | 合成尝试 | 合成结论 |
|---|---|---|---|
| I.10.7@32 | 2 → 4 → 6 → 8（JUMP 4 次） | 14981（每次） | `WHOLE_LAW: NO_FIT` |
| I.40.1@8 | 2 | 20000 | `SEARCH_TRUNCATED` |
| II.24.17@32 | 2 → 4 → 6 → 8（JUMP 4 次） | 20000（每次） | `SEARCH_TRUNCATED` |
| III.10.19@8 | 2 | 14317 | `NO_FIT` |
| II.34.11@8 | 2 | 89382 | `FIT_FOUND`，但 status `PACKET_DELIVERED` 后未收敛 |
| I.41.16@8（对照） | 2 | 37 | `FIT_FOUND` → 4.3s 完成 |

三点判断：

1. **超时主因是「每次 JUMP 都要重跑一次整式穷举合成」**。`SEARCH_TRUNCATED` 出自 `scripts/rds_compass_model.py:906`，含义是合成预算（20000 次尝试）**用尽**，不是证明无解。每个 JUMP 20000 次尝试约 200s，900s 内只够 4 次。
2. **`SEARCH_TRUNCATED` 意味着没搜完**，所以这些格「本应能过 / 应该过不了」都是 **UNKNOWN**，不能把超时当反证。
3. **I.10.7@32 的 `NO_FIT` 是真的搜完了**（14981 < 20000，无截断）。它的真值是 `m_0 / sqrt(1 - v**2/2.99792458e8**2)`，但 devset 行里 `v ∈ {1e8, 2e8, 2.5e8}`，`v²/c² ≈ 0.11 / 0.44 / 0.69`，其实不小；9 行网格里 `m_0 ∈ {1,2,5}`、`v` 三档。若要让这格从「超时」变成「可判」，先决条件是给合成器换个不随观测数重新穷举的策略，而不是调大 900s 超时。**这一条是推断，未经实验：UNVERIFIED。**

## 7. `II.34.11` 到底怎么了

`cacc9eb` 修掉了 `Undeclared variable: B_times_g_factor`（`scripts/rds_compass.py` 里在编译前展开派生中间量）。修复后：

- B=32：跑了 13 个实验点（`experiments = 13`）、6 次 JUMP（观测 2/4/6/8/10/12），每次合成 `WHOLE_LAW: 7478, NO_FIT`（真搜完，非截断），`REGIME_SPLIT:{B, g_factor, m, q}` 全部 `TOO_FEW_POINTS`，最终 `SEARCH_EXHAUSTED`、claim_exact = false。
- B=8 / B=16：进到第 2 个观测、合成 `WHOLE_LAW: 89382, FIT_FOUND`，`PACKET_DELIVERED` 之后继续跑到 900s 被杀。

**结论边界**：原来「跑 292s 必崩」的 bug 确实没了，但那不是这 23 格里的得分项——三档全 0。B=32 的 `SEARCH_EXHAUSTED` 是「在声明预算内没找到可验证的律」，不是「确认这方程无律」，两者不能混。

## 8. 文件未被覆盖的核对

| 文件 | sha256 | 结论 |
|---|---|---|
| `benchmark/newtonbench/results/devset-run.json` | `d75d51c8da5daafa2b75f3af5cd283aaa7bb49e49270ec44f4187bfe21511e45` | 与任务给定值**逐字符一致**，未被动过 |
| `benchmark/newtonbench/results/pareto.json` | `cad299b801a3e5726890409fa5e57b229c502cd17b917caa72c425909637e9cc` | 本轮全程未写入 |
| `benchmark/newtonbench/results/devset-run-30.json` | `daa69f25be50b728f3dc627b2a8cdeae924e23e45c99e6465760481edbfbf457` | 只读；仍是未跟踪文件，内容未变 |
| `benchmark/newtonbench/results/devset-run-30-fixed.json` | `ddbdd8681fe88e6fb683ba9d2977f4e2caa65cb0cb5f20fdc0253f106cd9c4a6` | 本轮新增 |

`pareto.json` 的 sha256 任务没有给基准值，只能核「本轮没写它」；它跑前跑后的值一致。这是 UNVERIFIED 的部分：无法证明它与某个历史版本相同。

`devset-run-30.json` 同理——任务没给基准 sha256，只能说本轮没有对它做写操作。

## 9. 复现

```bash
cd rds-357
git log --oneline -1          # 必须是 cacc9eb
python -B <逐格驱动脚本>       # 调 devset_run.run_one(name, budget, base)，23 格，单格 900s
```

驱动脚本留在 `C:\Users\XU\AppData\Local\Temp\rds357_cell.py`，日志 `C:\Users\XU\AppData\Local\Temp\rds357_rerun_v3.log`。campaign 根目录 `C:\Users\XU\AppData\Local\Temp\devset-run-30-fixed-l89xu0_7`（每格一个独立 ledger）。这些都不在仓库工作区里。

## 10. 收尾 `git status --short`

```
?? benchmark/newtonbench/results/devset-run-30-fixed.json
?? benchmark/newtonbench/results/devset-run-30.json
?? docs/devset-30-baseline-20261020.md
?? docs/devset-30-fixes-rerun-20261020.md
```

与启动时相比，只新增 `devset-run-30-fixed.json` 与本报告这 2 个文件。前 2 个未跟踪文件在启动时就已存在，不是本轮产物。

## 11. 尚未确认的事（UNKNOWN / UNVERIFIED）

1. 在 `cacc9eb` 上跑满 90 格的真实 claim_exact 是 **UNKNOWN**，67/90 只是算术合并。
2. 12 个超时格里，哪些「以后能过」——UNKNOWN。其中 `SEARCH_TRUNCATED` 的 2 格（I.40.1@8、II.24.17@32）只是没搜完，不是反证。
3. `pareto.json` / `devset-run-30.json` 的 sha256 没有历史基准值可比，只能确认「本轮没有写它们」。
4. `II.34.11@32` 的 `WHOLE_LAW: 7478, NO_FIT` 是真搜完，但只覆盖当前合成器能表达的假设类；换成更大假设类会不会有解，UNKNOWN。

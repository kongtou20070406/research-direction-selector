# 外部 Truth-Source 候选可达性探测报告 (2026-10-21)

预注册协议: `benchmark/newtonbench/source-reach-protocol.md`
数据来源: `benchmark/newtonbench/results/source-reach-20261021.json`
运行配置: 10 个 size > 7 方程, 预算 B=8, 单格超时上限 900s, 独立 TemporaryDirectory 隔离。

## 1. 10 方程两臂结果总表

| 方程 | no-source 状态 | no-source exact | truth-source 状态 | truth-source exact | 是否救回 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `feynman-I.41.16` | SEARCH_EXHAUSTED (388.2s) | False | GOAL_IDENTIFIED (28.1s) | True | 是 |
| `feynman-II.24.17` | SEARCH_EXHAUSTED (415.0s) | False | GOAL_IDENTIFIED (25.9s) | True | 是 |
| `feynman-III.10.19` | TIMEOUT (900.2s) | False | GOAL_IDENTIFIED (113.7s) | True | UNKNOWN (对照超时) |
| `feynman-I.40.1` | TIMEOUT (900.1s) | False | GOAL_IDENTIFIED (166.9s) | True | UNKNOWN (对照超时) |
| `feynman-I.10.7` | SEARCH_EXHAUSTED (386.6s) | False | GOAL_IDENTIFIED (14.2s) | True | 是 |
| `feynman-I.6.20a` | SEARCH_EXHAUSTED (42.0s) | False | GOAL_IDENTIFIED (54.9s) | True | 是 |
| `feynman-I.8.14` | SEARCH_EXHAUSTED (699.3s) | False | GOAL_IDENTIFIED (113.2s) | True | 是 |
| `feynman-I.18.4` | GOAL_IDENTIFIED (397.4s) | True | GOAL_IDENTIFIED (81.6s) | True | 否 (自然可达) |
| `feynman-I.27.6` | GOAL_IDENTIFIED (15.3s) | True | GOAL_IDENTIFIED (17.8s) | True | 阴性对照 (PASS) |
| `feynman-II.34.11` | SEARCH_EXHAUSTED (873.8s) | False | GOAL_IDENTIFIED (20.8s) | True | 是 |

## 2. 救回数量统计

- 目标方程总数: 10 个（排除阴性对照 `feynman-I.27.6` 后为 9 个）。
- `feynman-I.18.4` 在当前分支原生无 source 下即可自主收敛（`claim_exact=True`），依协议安全项第 2 条排除，不计入救回。
- 待判定目标集（8 个）: `I.41.16`, `II.24.17`, `III.10.19`, `I.40.1`, `I.10.7`, `I.6.20a`, `I.8.14`, `II.34.11`。
- **确凿救回 6 / 8**：`I.41.16`, `II.24.17`, `I.10.7`, `I.6.20a`, `I.8.14`, `II.34.11`。这 6 格的对照臂都是 `SEARCH_EXHAUSTED`——枚举跑完了确实找不到，所以 source 补上的正是搜索够不到的那部分。
- **UNKNOWN 2 格**：`III.10.19`、`I.40.1`。两格的对照臂是 `TIMEOUT`（900.2s / 900.1s），**没有跑完**，因此本次运行没有显示"搜索到不了这个律"。超时不是证据，不能计入救回。它们的救回上界是 8，确凿下界是 6。
- 本报告初版把这两格记为"是"，采用 `8 / 8` 的口径；该口径把"对照未判定"当成了"对照判定为不可达"，已更正。同一处 `verify_reach.py` 的判定分支和硬编码的结论句一并修掉。

## 3. 协议 §3 六条判据逐格核对

对所有 10 个 truth-source 格（6 个确凿救回 + 2 个 UNKNOWN + 2 个对照），六条判据全部逐项满足:
1. `status == GOAL_IDENTIFIED`: 10/10 满足。
2. `claim == CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`: 10/10 满足。
3. `counterexamples == 0`: 10/10 在全部声明行复算反例数均为 0。
4. `experiments <= 8`: 10/10 实际购买实验点数均为 8（无越界多买）。
5. `false_certification == false` 且 `points_checked == 32`: 10/10 独立点全额验证无假认证。
6. `max_abs_error == 0`: 10/10 在 32 个独立测试点上最大绝对误差均为 0（精确等价）。

## 4. 安全项三条核对

1. **阴性对照一致性**: `feynman-I.27.6` 在两臂中均产出 `GOAL_IDENTIFIED` 且 `claim_exact=True`（误差为 0），两臂结果一致，**通过**。
2. **自然可达隔离**: no-source 臂中 `feynman-I.18.4` 测出 `claim_exact=True`，依协议严格隔离、不计入「救回」，**通过**。
3. **零假认证红线**: 全表 20 格中 `false_certification=True` 发生数为 0，**通过**。

## 5. 脏基线复测对照

在 `benchmark/newtonbench/results/devset-run-30.json` 中，旧记录存在 4 类脏基线崩溃。本次当前代码复测实况:
- `I.41.16`（旧: `KeyError: 'op'`）: 当前代码正常枚举至 `SEARCH_EXHAUSTED` (388.2s)。
- `III.10.19`、`I.40.1`（旧: `ValueError: Declare 1..64 candidates`）: 当前代码正常运行至单格 900s 超时截断（`TIMEOUT`）。
- `I.18.4`（旧: 同上崩溃）: 当前代码正常运行并于 397.4s 成功解出真律（`GOAL_IDENTIFIED`）。
- `II.34.11`（旧: `ValueError: Undeclared variable`）: 当前代码正常枚举至 `SEARCH_EXHAUSTED` (873.8s)。

## 6. 结论

`VERDICT: the truth source rescued 6 of 8 target equations under B=8, with 2 undetermined because the control arm did not finish (feynman-III.10.19, feynman-I.40.1) and 0 failed (none). False certifications across all 20 cells: 0.`

（该句由 `python -B benchmark/newtonbench/verify_reach.py` 从结果文件现算，不手写。）

读法：**stage 3 的 seam 能补上"生成器够不到"这一类失败**——6 个对照臂确实枚举完的方程全部被救回，且 6/6 通过 8 次测量内的确认、32 个独立点的认证、0 反例。**它不能证明对"搜索能到但太慢"这一类同样有效**，那两格的对照没跑完。救回成本只计内核侧（14.2–166.9 秒/格）；source 生成本身在版本库之外，是 UNKNOWN。

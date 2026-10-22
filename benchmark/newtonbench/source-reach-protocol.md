# source reach：外部候选能把多少「结构不可达」的方程救回来

制定：2026-10-21，**在跑表之前**写下。协议里的判据先于数字。

## 0. 问题

`benchmark/newtonbench/results/truth-size.json` 记了 40 个真解的 AST size，其中 10 个大于
默认 `synthesis.max_nodes = 7`。`docs/benchmarks/size-ceiling-20261021.md` 已测死提高
`max_nodes` / `max_candidates` 两个旋钮都无效：`attempted` 恒 20000、`distinct` 一字不差，
第 8 层根本没开始枚举。

source seam（`benchmark/newtonbench/source-seam-protocol.md`）提供了绕过：把真解直接声明成
`sources`，内核只判定。

**本协议只量一件事**：如果外部生成器给出**恰好正确**的 law，这 10 个方程里能救回几个。

## 1. 这是上界，不是真实生成器的能力

truth-source 臂喂的是 `devset` 的 `truth.law` 原文，等价于假设生成器 100% 命中。
它回答「seam 的 reach 上限是多少」，**不回答**「PySR/LLM 实际能命中多少」。
后者的成本（调用次数、token、费用）本协议完全不测，保持 **UNKNOWN**。

结论里禁止把 `claim_exact` 的增量说成「帕累托改善」。reach 变宽和成本变低是两件事。

## 2. 主格

对 `truth-size.json` 里 size > 7 的 10 个方程，逐格跑两个臂，B=8：

| 臂 | 声明 | 期望 |
|---|---|---|
| no-source | 无 `sources` | 基线；多数应为 `SEARCH_EXHAUSTED` 或声明期失败 |
| truth-source | `sources=[{'id':'truth','laws':[truth.law]}]` | 应 `GOAL_IDENTIFIED` |

`feynman-I.27.6` 已知能用 size 7 的等价式救回（`(d2 / ((d2 / d1) + n))`），
仍然照跑，用来验证两臂在该格上一致——它是本表的**阴性对照**：一个本来就成功的方程
不应该因为加了 source 而改变结论。

## 3. 判据

主指标：**`claim_exact` 从 no-source 的 false 变成 truth-source 的 true 的方程数量**。
上界 9（排除阴性对照 `I.27.6`）。

每个救回格必须同时满足，否则该格记为 FAIL 并单独列出：

1. `status == GOAL_IDENTIFIED`；
2. `claim == CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`；
3. `counterexamples == 0`（在全部声明行上复算）；
4. `experiments <= 8`（source 不应多买实验点）；
5. `false_certification == false`，且 `certification_check.points_checked == 32`；
6. `certification_check.max_abs_error == 0`（真解在独立点上应精确）。

安全项：

- 阴性对照 `I.27.6` 两臂结果必须相同；不同则整表作废；
- no-source 臂里若出现 `claim_exact == true`，说明该方程本来就能解，不计入「救回」；
- 任一救回格的 `false_certification == true`，整表作废（假认证优先于 reach）。

## 4. 已知的脏基线

`benchmark/newtonbench/results/devset-run-30.json` 里这 9 个未救回方程的 B=8 记录**不能直接
当基线**：`I.41.16` 是 `KeyError: 'op'`（4.1s）、`III.10.19`/`I.40.1`/`I.18.4` 是
`ValueError: Declare 1..64 candidates`（0.0s）、`II.34.11` 是
`ValueError: Undeclared variable: B_times_g_factor`（292.6s）。只有 `II.24.17`/`I.10.7`/
`I.6.20a`/`I.8.14` 是干净的 `SEARCH_EXHAUSTED`。

所以 no-source 臂**必须用当前代码重跑**，不得引用旧文件。旧文件里那四条错误记录本身
是待查线索，记进报告，不在本协议里修。

## 5. 成本口径

只记内核实测秒数（每个臂一列）。两臂墙钟不可直接相减得到「source 省了多少」——
no-source 跑的是完整枚举，truth-source 是判定一条候选后立刻退出，两者量的不是同一件事。

外部生成成本 **UNKNOWN**。本协议不产出任何每正确解成本数字。

## 6. 反例门槛

`feynman-I.41.16` 的 wrong-source 臂已有独立证据（`source-seam-protocol.md` §3）：
把真解的 `299792458**2` 改成 1，`status` 仍是 `SEARCH_EXHAUSTED`、`claim_exact=false`。
本表不重复测，但若任一救回格声称成功而同格的 law 与 truth 不等价，整表作废。

## 7. 回滚触发

§3 任一救回格不满足 6 条、或安全项任一触发 → 该格记 FAIL，报告照写，不修代码。
本协议只测量，不改内核。

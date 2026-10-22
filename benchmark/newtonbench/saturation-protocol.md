# 饱和早停（Saturation Early Stop）预注册协议

制定：2026-10-20，**在任何一次验证运行之前**写下。改动对象是工作区里 `scripts/rds_compass.py` 的 `_gap_wide_enough`（尚未提交）。

## 0. 本轮三行

- **提升什么能力**：消除自我帕累托支配——RDS 锁定 leader 后继续买 PROBE/HOLDOUT，导致 `RDS(B=16/24/32)` 被 `RDS(B=12)` 支配（成本更高、收益相同）。
- **用什么可证伪信号判断**：`regime-double` 上 B=16/24/32 的实际实验数相对 B=12 的超出量。不降即假设被证伪。
- **绝对不允许**：把 `claim_exact` 放水（PARTIAL_FIT / best-effort 算正确）、放宽任何超时或预算上限来让数字好看、在没有同预算对照的情况下宣称改进。

## 1. 改动内容

`scripts/rds_compass.py:420` 的 `_gap_wide_enough(state)`（由 `scripts/rds_compass.py:522` 调用）：

```diff
-    return bool(gaps) and max(gaps) > min(gaps)
+    if not gaps:
+        return False
+    # A hole worth another paid probe must be a real hole in the declared domain, not the
+    # residual spacing of an already dense grid; otherwise the search saturates by buying.
+    return max(gaps) > max(2, len(plan['candidates']) // 10)
```

语义：只有候选数 10%（至少 2）以上的真实空洞才值得再买一次付费探测；密集网格的残余间距不再触发 PROBE。

已知边界（handoff §3.1）：`97e133c` 的 VoI 停机第一版 LawTests 13/13，但**只挡住 B=24/32**；本次是阈值收紧的第二步，**语法通过但尚未跑 regression**。

## 2. 预注册判定规则

### 2.1 主场景：`regime-double`（自我支配现场）

```bash
python -B benchmark/newtonbench/pareto.py benchmark/newtonbench/results/pareto-satstop.json
```

**必须写新输出路径。`results/pareto.json` 是基线，覆盖它等于毁掉对照。**

| 项 | 值 |
|---|---|
| 主指标 | RDS 臂在 `regime-double` 上的实际实验数 `Exp` |
| 对照 | HEAD `c873e9d`：B=12→12、B=16→16、B=24→21、B=32→21；四档 `claim_exact` 都是 32/32 |
| 门槛（相对） | `max(Exp@B=16, Exp@B=24, Exp@B=32) − Exp@B=12 ≤ 2` |
| 安全项 1 | `regime-double` 四档保持 `GOAL_IDENTIFIED` 且 match 32/32，一档都不许掉 |
| 安全项 2 | `sparse-tail` / `sparse-mid` 的 match 数不降（当前 31/32），`claim_exact` 当前为 0 是已知缺口（合成器无单点脉冲算子），本轮不要求提升 |

### 2.2 回归场景：devset 15 runs

```bash
python -B benchmark/newtonbench/devset_run.py benchmark/newtonbench/results/devset-satstop.json
```

| 项 | 值 |
|---|---|
| 门槛 | 4/5 方程在 B=8/16/32 三档全部 `claim_exact`，每档实验数 ≤ 6（当前都是 4） |
| 门槛 | 总 `claim_exact` ≥ 12/15（当前恰为 12/15，不许降） |
| 安全项 | `feynman-I.6.20a` 的 status 保持 `SEARCH_EXHAUSTED`，不许变成假认证 |

### 2.3 Focused 测试

```bash
python -B -m unittest discover -s tests -p 'test_rds_compass_drive.py'
```

要求全绿，`LawTests` 一条不 skip。

## 3. 判定与回滚

- 三节全部满足 → 提交；
- 任一不满足 → 回滚 `_gap_wide_enough` 改动，失败原因写进 `.harness/LESSONS.md`，证据文件保留不删。

## 4. 最小反向测试

把 `_gap_wide_enough` 恢复成 `max(gaps) > min(gaps)`，在 `regime-double` B=16 上重跑一次，`Exp` 应回到 16。用来证明观测到的变化确实来自这个函数，而不是别的噪声。

## 5. 口径来源

- 自我支配现象与根因（多出来的是 PROBE/HOLDOUT，不是 `spend_budget`）：`docs/audit-verify-cost.md` §2、§5
- 预注册统计口径（`claim_exact` = `GOAL_IDENTIFIED` 且 match 32/32）：`benchmark/newtonbench/pareto-review.md` §1
- 任务定性：`docs/handoff-20261020.md` §3.1

## 6. 结果

（跑完填，不预填。）

# confirm-reserve：预注册实现与验证结果（2026-10-21）

预注册：`benchmark/newtonbench/confirm-reserve-protocol.md`（写于 `f1d380c`，判定规则未改）。
工作树：`D:\Myfile\work space\RDS\rds-357`，分支 `release/6.0.0-compass`。

**执行期的仓库状态变化（如实记录，非本任务所为）**：开工时 HEAD 为 `f1d380c`；验证过程中 HEAD 被外部推进到 `dce9e03`（只新增两份文档：协议 + `docs/sparse-tail-b32-trace.md`，`scripts/` 与 `f1d380c` 逐字节相同），随后又被推进到 `a11f658`（"fix(compass): leave room to confirm a law learned at the end of the budget"），该 commit 的内容就是本报告 §1 的三行改动。本任务没有 commit / push / checkout / stash。收尾时工作树对 `scripts/rds_compass.py` 是干净的（内容等于 `a11f658`），未提交的只剩本报告与 `results/pareto-confirmreserve.json` 两个新文件。

判定：**FAIL**。主格五条全 PASS，但 focused 测试红 + 全量 pareto 四条安全项全 FAIL（详见 §4、§5）。

---

## 1. 改动 diff

只动 `scripts/rds_compass.py` 的 CONFIRM 分支（`decide`，原 `:573`）。`spent`（`scripts/rds_compass.py:485`，`decide` 内 `spent = sum(1 for rid in state['runs'] if rid.startswith('compass-'))`）与 `_cap`（`scripts/rds_compass.py:421`，模块级）都在作用域内，无需改名。`min(200000, ...)` 那行未动，其它分支未动。

```diff
@@ -570,7 +570,9 @@ def decide(state):
         # Not gated on gaps: an isolated exception between measured neighbours is found only by
         # measuring it, and a gap rule never spends on a multi-input law. A caller that wants an
         # earlier claim declares spend_budget: false, and the claim then reports its coverage.
-        spending = spend and (not cap or bought < cap)
+        left = _cap(plan) - spent
+        reserve = 2 * plan['verification']['confirmations'] + len(plan.get('holdout') or [])
+        spending = spend and (not cap or bought < cap) and left > reserve
         if confirmed or spending:
             # Falsification test: a fitted law must survive predictions it did not see. While budget
             # remains, a leader is tested at the uncovered inputs before any claim is made.
```

---

## 2. 主格：`sparse-tail / rds / B=32`

命令：`cd "D:/Myfile/work space/RDS" && python -B trace_sparsetail.py`（每次约 22 s）。

| 判定项 | 改前 | 改后 | 判定 |
| --- | --- | --- | --- |
| `claim` | `CONSISTENT_BUT_UNCONFIRMED` | `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS` | **PASS** |
| `claim_exact`（pareto 口径，`claimed` 门控） | 0 | 32 | **PASS** |
| `novel_confirmations` | 0 / 2 | 2 / 2 | **PASS** |
| `law` | `ite((x < (61/2)), (1 + x), 1000)` | 同左 | **PASS** |
| `experiments` | 32 | 31（≤ 32） | **PASS** |

改前/改后的决策直方图（trace 原始输出）：

| 项 | 改前 | 改后 |
| --- | --- | --- |
| `actions` | `{CONFIRM: 30, HOLDOUT: 2, ESCAPE: 1}` | `{CONFIRM: 28, HOLDOUT: 2, PROBE: 1, ESCAPE: 1, RIVALS: 3}` |
| `confirm_reasons` | `{MINIMUM_CONFIRMATIONS: 2, SPEND_BUDGET_ON_REFUTATION: 28}` | `{MINIMUM_CONFIRMATIONS: 4, SPEND_BUDGET_ON_REFUTATION: 24}` |
| `probe_reasons` | `{}` | `{BASIN_BOUNDARY_PROBE: 1}` |
| holdout 购买时机 | `x0` seq 62、`x31` seq 64（实验 31/32、32/32） | `x0` seq 54、`x31` seq 56（实验 27/32、28/32） |
| 观测顺序尾部 | `... x10, x0, x31` | `... x0, x31, x14, x15, x12` |
| 内核 `status` | `JUDGMENT_REQUIRED` | `JUDGMENT_REQUIRED`（未变） |

两条读数的口径说明（避免误读）：

- trace 自己打印的 `claim_exact` 是 `score(law, LAW)`（32 个声明整数点），改前也是 32；协议里的 `claim_exact` 是 pareto 口径（`benchmark/newtonbench/pareto.py:314` 的 `score(expr, law) if (expr and claimed) else 0`），改前被 `claimed=False` 乘成 0。上表按协议口径填，两个数并存不是矛盾。
- 主格这个 `claim_exact=32` 只保证 32 个整数点；同一个 law 在 pareto 的独立实数采样下被证伪，见 §5 与 §7。

---

## 3. 最小反向测试

把 `spending` 改回旧两项式（保留两行 `left`/`reserve` 只作对照，判定式退回 `spend and (not cap or bought < cap)`），重跑主格：

```
claim                = CONSISTENT_BUT_UNCONFIRMED
claim_exact(pareto)  = 0
novel_confirmations  = 0 / 2
law                  = ite((x < (61/2)), (1 + x), 1000)
experiments          = 32
actions              = {CONFIRM: 30, HOLDOUT: 2, ESCAPE: 1}
confirm_reasons      = {MINIMUM_CONFIRMATIONS: 2, SPEND_BUDGET_ON_REFUTATION: 28}
```

与 §2 改前列逐项一致（含 holdout 购买时机 seq 62 / 64）。反向测试成立：移动 `claim_exact` 的确实是 reserve 这一项。跑完已改回新版本，收尾时文件内容等于 `a11f658`。

---

## 4. focused 测试

| 模块 | 结果 | 耗时 |
| --- | --- | --- |
| `tests/test_rds_compass_drive.py` | **Ran 31 tests, FAILED (failures=1)** | 207.327 s（wall 3m27.6s） |
| `tests/test_rds_compass_routing.py` | Ran 5 tests, OK | 2.578 s |

失败项：

```
FAIL: test_later_counterexample_retracts_the_tool_its_dependents_and_its_library_offer
      (test_rds_compass_drive.RsiTests)
  File "tests/test_rds_compass_drive.py", line 398
    self.assertEqual(result['status'], 'SEARCH_EXHAUSTED')
AssertionError: 'GOAL_IDENTIFIED' != 'SEARCH_EXHAUSTED'
```

归因（不是环境抖动）：单测 `-k test_later_counterexample` 在新版本下重跑 `Ran 1 test in 43.620s FAILED`；把 `spending` 退回旧两项式重跑 `Ran 1 test in 64.776s OK`。同一条测试、同一台机器，红/绿只随这三行切换。

根因（单独复现，临时脚本驱动同一 TRAP campaign，`law = 0 if x in (11, 13) else 2*popcount(x)+1`，17 个声明点）：

```
status               = GOAL_IDENTIFIED
claim                = CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS
predict              = ((1 + digitsum2_x) + digitsum2_x)      # 在 x=11、x=13 上应为 0
coverage             = {measured: 13, declared: 17, unmeasured_inputs_are: PREDICTED_NOT_MEASURED}
novel_confirmations  = 4 / 2
experiments          = 13（预算 17）
unmeasured_declared  = [x1, x11, x13, x15]
```

TRAP 没有声明 holdout，`reserve = 2*2 + 0 = 4`：CONFIRM 买到 `spent = 13` 就停（`left = 4` 不再 `> reserve`），其余分支没人再买，直接 VERIFY 收成 `GOAL_IDENTIFIED`。旧代码会把 17 个点买完，在 `x11`/`x13` 上被证伪，降级为 `SEARCH_EXHAUSTED`。这正是 `832a11f` 修掉的假认证形态（"17 个点只测 11 个就 GOAL_IDENTIFIED"）在更小规模上复发。

---

## 5. 全量 pareto（36 格）

命令：`python -B benchmark/newtonbench/pareto.py benchmark/newtonbench/results/pareto-confirmreserve.json`，耗时 3m54.8s。对照 `results/pareto-stage2.json`（未覆盖，mtime 保持 11:20）。36 格 key（`task`+`arm`+`budget`）完全对齐；对比排除 `seconds`/`campaign_root`/`elapsed_seconds`/`certification_check`（后者只在有 claim 时生成，单独在下面用于安全项判定）。

差异格：**12 格**（全部在 `rds` 臂，`baseline-uniform` 18 格零差异）。

| task | budget | 差异字段（改前 → 改后） |
| --- | --- | --- |
| sparse-tail | 6 | `claim` `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`→`CONSISTENT_BUT_UNCONFIRMED`；`claim_exact` 31→0；`claimed` True→False；`exact_match` 31→32；`status` `GOAL_IDENTIFIED`→`JUDGMENT_REQUIRED` |
| sparse-tail | 8 | `claim_exact` 31→32；`exact_match` 31→32；`false_certification` False→**True** |
| sparse-tail | 12 | `claim_exact` 31→32；`exact_match` 31→32；`false_certification` False→**True** |
| sparse-tail | 16 | `claim_exact` 31→32；`exact_match` 31→32；`false_certification` False→**True**；`status` `GOAL_IDENTIFIED`→`JUDGMENT_REQUIRED` |
| sparse-tail | 24 | `claim_exact` 31→32；`exact_match` 31→32；`false_certification` False→**True**；`status` `GOAL_IDENTIFIED`→`JUDGMENT_REQUIRED` |
| sparse-tail | 32 | `claim`→`CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`；`claim_exact` 0→32；`claimed` False→True；`experiments` 32→31；`false_certification` False→**True** |
| sparse-mid | 8 | `claim` `CONSISTENT_BUT_UNCONFIRMED`→`CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`；`claim_exact` 0→31；`claimed` False→True；`exact_match` 32→31；`status` `JUDGMENT_REQUIRED`→`GOAL_IDENTIFIED` |
| sparse-mid | 12 | `claim_exact` 32→31；`exact_match` 32→31 |
| sparse-mid | 24 | `experiments` 24→21 |
| sparse-mid | 32 | `experiments` 32→28 |
| regime-double | 24 | `experiments` 24→21 |
| regime-double | 32 | `experiments` 32→28 |

新增的 5 个假认证（`sparse-tail` b8/b12/b16/b24/b32）证伪点与理由字符串逐字相同：`DIFFERS_BEYOND_TOLERANCE on x=25931696509/846053740`（≈30.6502），即 law 在 `x ∈ (30.5, 31)` 报 1000 而真值是 `x+1`。同一"只在整数点上对"的 law 在 b32 里就是主格那个 `ite((x < (61/2)), (1 + x), 1000)`。`regime-double b12` 的假认证是改前就有的那 1 个（`x=8843715185/887427361`≈9.9656），未变。

臂级汇总：

| 指标 | 改前 | 改后 |
| --- | --- | --- |
| `claimed_count` | 15 | 16 |
| `correct_count` | 15 | 10 |
| `false_certification_count` | 1 | **6** |
| `false_certification_rate` | 0.0667 | 0.375 |
| `experiments_total` | 294 | 279 |
| `experiments_per_correct` | 19.6 | 27.9 |

### 四条安全项逐条判定

| # | 判据（协议 §Judgement） | 实测 | 判定 |
| --- | --- | --- | --- |
| 1 | rds 臂 `claim_exact` 总数不低于 15，且剔除假认证后的正确数不低于 14 | `claim_exact` 15→16（不减，前半 PASS）；剔除假认证后 **14→10**（后半 FAIL） | **FAIL** |
| 2 | `false_certification_count` 不升破 1 | 1→**6**（新增 5 格） | **FAIL** |
| 3 | 改前 `GOAL_IDENTIFIED` 的格不得变成 `SEARCH_EXHAUSTED`/`JUDGMENT_REQUIRED` | `sparse-tail` b6、b16、b24 三格由 `GOAL_IDENTIFIED` 降为 `JUDGMENT_REQUIRED` | **FAIL** |
| 4 | `experiments_per_correct` 不得比 21.0 高 10% 以上（≤ 23.1） | 21.0→**27.9**（+32.9%） | **FAIL** |

---

## 6. devset

`DEV SET: NOT RUN (time)`。

理由：单次约 3.8 小时（90 格），本 session 的时限内跑不完；且 §5 四条安全项已全部 FAIL、§4 focused 测试已红，按协议 §Rollback「任何一条安全项失败即回滚」，devset 结果不可能改变判定，再花 3.8 小时买不到新信息。如果只想要「devset 上的 `claim_exact` 与 `feynman-I.6.20a` 是否被这条改动带坏」这一条，建议在回滚前单独补跑（命令与输出路径已在协议里写死）。

---

## 7. 结论

**FAIL**。失败在协议 §Judgement 的安全项 1/2/3/4 全部，以及验证顺序第 3 步的 focused 测试：

1. focused：`tests/test_rds_compass_drive.py` 31 tests 1 failure（`GOAL_IDENTIFIED != SEARCH_EXHAUSTED`），TRAP campaign 在只测 13/17 个声明点时就给出 `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`，law 在未测的 `x11`/`x13` 上错。
2. 安全项 2：rds 臂 `false_certification_count` 1→6，`correct_count` 15→10。
3. 安全项 1 后半：剔除假认证后的正确解 14→10。
4. 安全项 3：3 格从 `GOAL_IDENTIFIED` 掉到 `JUDGMENT_REQUIRED`。
5. 安全项 4：`experiments_per_correct` 21.0→27.9（+32.9%，阈值 +10%）。

机制一句话：`left > reserve` 只关掉 CONFIRM 的购买，不保证剩下的预算会被别人花掉；声明点还剩 ≥1 个没测时，campaign 直接 VERIFY，把"没测过的点"当成"预测正确的点"写成 full claim。主格能过，是因为它在 32 点里刚好用 reserve 买到了 `x0`/`x31` 并让 jump 学到了 law；换个规模（TRAP 17 点、pareto b6/b16/b24）同样的规则就变成"提前收工 + 假认证"。

主格还有一个必须说清的边界：`claim_exact=32` 只用 32 个整数点打分，而同一个 law 在 pareto 的独立实数采样上被证伪（`x≈30.6502`）。所以这次改动即便只按主格看，也是"把一句诚实的 `CONSISTENT_BUT_UNCONFIRMED` 换成一句在实数域上错的 `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`"。

建议的回滚动作（由你决定，我没有执行）：`git revert a11f658`，或者在同一分支上把 reserve 条件收窄成「只剩 holdout 未买时才启用 reserve」，并补一条断言：`remaining(state)` 非空时不允许 VERIFY。证据文件（本报告 + `results/pareto-confirmreserve.json`）按协议保留。

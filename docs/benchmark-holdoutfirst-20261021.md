# holdout-first：验证结果（2026-10-21）—— FAIL，已回滚

预注册：`benchmark/newtonbench/holdout-first-protocol.md`（写于任何验证运行之前，判定规则未改）。
基线 HEAD：`3514d37`。代码已 `git checkout -- scripts/rds_compass.py` 回滚。

## 结论

**FAIL，回滚。** 主格五项全 PASS，但 pareto 全量四条安全项全部 FAIL，而且**主格那个 full claim 本身就是一次假认证**。

## 1. 改动

`scripts/rds_compass.py` 的 `decide()`：把 HOLDOUT 块从 CONFIRM 之后移到之前（+8/−4 行）。

## 2. 主格：`sparse-tail / rds / budget=32`

| 项 | 改前 | 改后 | 判定 |
|---|---|---|---|
| `claim` | `CONSISTENT_BUT_UNCONFIRMED` | `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS` | PASS |
| pareto 口径 `claim_exact` | 0 | 32 | PASS |
| `novel_confirmations` | 0 / 2 | 10 | PASS |
| `experiments` | 32 | 32 | PASS |
| `status` | `JUDGMENT_REQUIRED` | `GOAL_IDENTIFIED` | PASS |

**但同一行 `false_certification: False -> True`。** 主格这个 full claim 在 32 个独立实数点上被证伪。
换句话说：五项 PASS 是用一次假认证买来的。

law 也从 `ite((x < (61/2)), (1 + x), 1000)` 变成
`ite((30 == x), 31, ite((x < 30), (1 + x), 1000))`（size 13）。两者在 32 个声明整数点上都是 32/32，
但旧 law 在实数域只在 `x ∈ (30.5, 31)` 错，新 law 在 `x ∈ (30, 31)` 与 `x > 31` 都错。

## 3. 全量 pareto：9 格差异（全部在 `rds` 臂）

| task | budget | 差异（改前 → 改后） |
|---|---|---|
| `regime-double` | 6 | `status` `JUDGMENT_REQUIRED`→`SEARCH_EXHAUSTED`；`claim` `CONSISTENT_BUT_UNCONFIRMED`→`NOT_ESTABLISHED`；`exact_match` 30→0；`experiments` 6→5 |
| `regime-double` | 8 | `status` `GOAL_IDENTIFIED`→`JUDGMENT_REQUIRED`；`claim` full→`CONSISTENT_BUT_UNCONFIRMED`；`claim_exact` 30→0 |
| `regime-double` | 12 | 同上；`false_certification` True→**False**（唯一变好的一条）；`exact_match` 32→30 |
| `sparse-tail` | 6 | `claim_exact` 31→28；`false_certification` False→**True** |
| `sparse-tail` | 8 | `status` `GOAL_IDENTIFIED`→`JUDGMENT_REQUIRED`；`claim` full→`CONSISTENT_BUT_UNCONFIRMED`；`claim_exact` 31→0 |
| `sparse-tail` | 12 | `claim_exact` 31→30；`false_certification` False→**True** |
| `sparse-tail` | 16 | `false_certification` False→**True** |
| `sparse-tail` | 24 | `claim_exact` 31→32；`false_certification` False→**True** |
| `sparse-tail` | 32 | `status` `JUDGMENT_REQUIRED`→`GOAL_IDENTIFIED`；`claim` 升到 full；`claim_exact` 0→32；`false_certification` False→**True** |

臂级汇总：

| 指标 | 改前 | 改后 |
|---|---|---|
| `correct_count` | 15 | **8** |
| `experiments_total` | 294 | 293 |
| `experiments_per_correct` | 19.6 | **36.625** |
| `false_certification_count` | 1 | **5** |
| `false_certification_rate` | 0.0667 | **0.3846** |
| `claimed_count` | 15 | 13 |

## 4. 四条安全项

| # | 门槛 | 实测 | 判定 |
|---|---|---|---|
| 1 | 剔除假认证后正确数 ≥ 14 | 15→**8** | **FAIL** |
| 2 | `false_certification_count` ≤ 1 | 1→**5** | **FAIL** |
| 3 | 无 `GOAL_IDENTIFIED` 降级 | `sparse-tail` b8、`regime-double` b8/b12 三格降级 | **FAIL** |
| 4 | `experiments_per_correct` ≤ 23.1 | 19.6→**36.625** | **FAIL** |

## 5. focused 测试（全绿）

| 命令 | 结果 |
|---|---|
| `test_rds_compass_routing.py` | Ran 5 tests, **OK**（1.898 s） |
| `test_rds_compass_drive.py -k test_later_counterexample` | Ran 1 test, **OK**（62.674 s）——TRAP 不声明 holdout，改动进不去，行为与改前一致 |
| `test_rds_compass_drive.py`（全量） | Ran 31 tests, **OK**（287.073 s） |

测试全绿而安全项全红，说明**测试集覆盖不到这次改动的失败面**：现有 31 条 drive 测试没有一条测「holdout 提前后 law 的实数域覆盖」。

## 6. 机制：为什么 holdout 提前反而更差

`sparse-tail` 的真 law 是 `x+1 if x != 31 else 1000`，`x31` 是唯一的离群点。

- **旧顺序**：CONFIRM 先买 30 个普通点，leader 稳定为 `x+1`；再买 holdout `x0`、`x31`，x31 推翻它时 ESCAPE 手上有 31 个点，学到的 law 在实数域只在 `(30.5, 31)` 错。
- **新顺序**：x0、x31 成为第 1、2 个观测。leader 在任何普通点被测之前就被离群点推翻，ESCAPE 在证据只有 2 个点时触发，合成器只能学到「特判 x=31 + 其余照抄 x+1」的形式，落在实数域上更窄。

**所以「预注册 holdout 判别力最高，应该先买」这个前提在证据不足时不成立**：高判别力的点提前进入，会让 jump 在信息量不足时触发，产出更过拟合的 law；而 jump 拿到的证据越少，它越倾向于特判已见的少数点。

这也解释了早前 `holdout-scan.json` 的现象：holdout 数量从 2 加到 4 加到 8，law 与 claim 一字不变——**问题从来不是点数，也不是顺序，是 jump 触发的证据门槛**。

## 7. 下一步（两条都要单独预注册）

1. **最小证据门槛**：holdout 点只在 leader 已被 ≥N 个普通点支撑时才买（N 需预注册，不能拍）。
2. **CONFIRM 份额上限**：给 `SPEND_BUDGET_ON_REFUTATION` 型 CONFIRM 一个不超过剩余预算一半的份额，让 holdout 总有位置——但这必须同时保住证据累积，否则就是本次的失败模式。

`docs/pareto-bisect-20261021.md:167` 建议的是第 2 条。本次实测说明**只做第 2 条的后半段（重排）会崩**，必须和证据门槛一起做。

## 8. 证据文件

- `benchmark/newtonbench/results/pareto-holdoutfirst.json`（28445 B，36 格，与 `pareto-stage2.json` 同 schema）
- `benchmark/newtonbench/holdout-first-protocol.md`
- `docs/pareto-holdoutfirst.md`：执行侧逐格明细（基线 sha256 跑前跑后一致 `12da476e…`、新结果 sha256 `c55753a5…`、每条 `certification.reason` 原文、耗时 340.9 s）
- 主格 trace：`D:\Myfile\work space\RDS\holdoutfirst-trace.json`
- 对比脚本：`D:\Myfile\work space\RDS\cmp_holdoutfirst.py`
- 回滚前的 patch：`D:\Myfile\work space\RDS\holdout-first.patch`

**口径提醒**：安全项的数字必须拿 `pareto-stage2-costfix.json`（correct 14、epc 21.0）当分母，
不是 `pareto-stage2.json` 的 15 / 19.6——后者是剔除假认证之前的旧口径。
本次实测 8 / 36.625 对两者都是 FAIL，但写进任何后续文档时必须用 costfix 那一列。

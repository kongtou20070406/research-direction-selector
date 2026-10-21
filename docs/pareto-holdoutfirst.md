# holdout-first：全量 pareto 复测与判定

工作树 `D:\Myfile\work space\RDS\rds-357`，分支 `release/6.0.0-compass`，基线 HEAD `3514d375ae118e3d29ad78474ac5a2fe428b2743`。
协议 [holdout-first-protocol.md](../benchmark/newtonbench/holdout-first-protocol.md) 已先完整读过。
只读 + 跑命令：未 commit、未 push、未 checkout、未 stash、未 revert。

## 0. 一句话结论

**FAIL。** 四条安全项全部不通过：rds 臂剔除假认证后的正确数 14 → 8，假认证 1 → 5，3 个 `GOAL_IDENTIFIED` cell 降级，`experiments_per_correct` 21.0 → 36.625。
`sparse-tail / rds / b32` 的主格表面指标确实翻绿了（claim 与 claim_exact=32），但它是**假认证**：那条 law 在独立采样点偏离真值约 968.7。

## 1. 命令与耗时

```bash
cd "D:/Myfile/work space/RDS/rds-357"
python -B benchmark/newtonbench/pareto.py benchmark/newtonbench/results/pareto-holdoutfirst.json
```

- 退出码 0，实测耗时 **340.9 s**（5 分 41 秒），36 格全部跑完，无超时。
- 只跑了这一条评测命令，没有跑 devset。

## 2. 基线完整性

`results/pareto-stage2.json` 的 sha256 跑前跑后一字未变：

| 时点 | sha256 |
|---|---|
| 跑前 | `12da476e8c9ca37e2bed03efb60206ac51d1fb56017670c140f2996b77784bc6` |
| 跑后 | `12da476e8c9ca37e2bed03efb60206ac51d1fb56017670c140f2996b77784bc6` |

新结果写在 `benchmark/newtonbench/results/pareto-holdoutfirst.json`，sha256 `c55753a5dde4c0d4f721475e543ab478f773508f16c4f1077c8f06923753de59`（28445 字节）。
`git status` 只有预期的三项：`M scripts/rds_compass.py`、未跟踪的协议文档、未跟踪的新结果文件。

顺带核对了协议里的改动：`git diff` 显示 HOLDOUT 块从 CONFIRM 之后原样移到之前，函数体与其他分支一字未动，与协议 §1 的 diff 一致。

## 3. 对比口径

两份基线在 `rows` 上逐字段完全相同，差别只在顶层汇总的算法：

| 文件 | rds 臂 correct_count | rds 臂 epc | rds 臂 false_certification_count |
|---|---|---|---|
| `pareto-stage2.json` | 15（旧口径，未剔除假认证） | 19.6 | 1 |
| `pareto-stage2-costfix.json` | **14**（新口径，已剔除） | **21.0** | **1** |
| `pareto-holdoutfirst.json` | **8** | **36.625** | **5** |

所以安全项一律拿 **rds 臂 + costfix 口径** 比，这才是协议 §3 门槛 14 / 23.1 的出处。对比时排除了 `seconds` / `campaign_root` / `elapsed_seconds`。

## 4. 差异格（36 格中 9 格变化，全部可归因于本次改动）

逐字段改前 → 改后（改前 = costfix，改后 = holdoutfirst）：

| # | task / arm / budget | 字段 | 改前 | 改后 |
|---|---|---|---|---|
| 1 | sparse-tail / rds / b6 | claim_exact | 31 | 28 |
| | | exact_match | 31 | 28 |
| | | false_certification | false | **true** |
| | | cert.reason | EQUIVALENT on 32 sampled points | DIFFERS on x=6317087/217727 |
| 2 | sparse-tail / rds / b8 | claim | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | CONSISTENT_BUT_UNCONFIRMED |
| | | claim_exact | 31 | 0 |
| | | exact_match | 31 | 29 |
| | | claimed | true | false |
| | | status | GOAL_IDENTIFIED | **JUDGMENT_REQUIRED** |
| 3 | sparse-tail / rds / b12 | claim_exact | 31 | 30 |
| | | exact_match | 31 | 30 |
| | | false_certification | false | **true** |
| | | cert.reason | EQUIVALENT on 32 sampled points | DIFFERS on x=6317087/217727 |
| 4 | sparse-tail / rds / b16 | false_certification | false | **true** |
| | | cert.reason | EQUIVALENT on 32 sampled points | DIFFERS on x=25931696509/846053740 |
| 5 | sparse-tail / rds / b24 | claim_exact | 31 | 32 |
| | | exact_match | 31 | 32 |
| | | false_certification | false | **true** |
| 6 | sparse-tail / rds / b32 | claim | CONSISTENT_BUT_UNCONFIRMED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS |
| | | claim_exact | 0 | 32 |
| | | claimed | false | true |
| | | false_certification | false | **true** |
| | | status | JUDGMENT_REQUIRED | GOAL_IDENTIFIED |
| 7 | regime-double / rds / b6 | claim | CONSISTENT_BUT_UNCONFIRMED | NOT_ESTABLISHED |
| | | exact_match | 30 | 0 |
| | | best_effort_exact | 0 | 23 |
| | | experiments | 6 | 5 |
| | | status | JUDGMENT_REQUIRED | **SEARCH_EXHAUSTED** |
| 8 | regime-double / rds / b8 | claim | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | CONSISTENT_BUT_UNCONFIRMED |
| | | claim_exact | 30 | 0 |
| | | claimed | true | false |
| | | status | GOAL_IDENTIFIED | **JUDGMENT_REQUIRED** |
| 9 | regime-double / rds / b12 | claim | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | CONSISTENT_BUT_UNCONFIRMED |
| | | claim_exact | 32 | 0 |
| | | exact_match | 32 | 30 |
| | | claimed | true | false |
| | | false_certification | true | false |
| | | status | GOAL_IDENTIFIED | **JUDGMENT_REQUIRED** |

另外 `experiments_total` 从 588 降到 587，`claimed_count`（两臂合计）33 → 31，`false_certification_count`（两臂合计）16 → 20。

## 5. 四条安全项判定

| # | 安全项 | 门槛 | 改前 | 改后 | 判定 |
|---|---|---|---|---|---|
| 1 | 剔除假认证后的正确数 | ≥ 14 | 14 | **8** | **FAIL**（-6，只有门槛的 57%） |
| 2 | `false_certification_count`（rds 臂） | ≤ 1 | 1 | **5** | **FAIL**（+4） |
| 3 | `GOAL_IDENTIFIED` 不得变成 `SEARCH_EXHAUSTED` / `JUDGMENT_REQUIRED` | 0 个降级 | — | **3 个降级** | **FAIL** |
| 4 | `experiments_per_correct` | ≤ 23.1 | 21.0 | **36.625** | **FAIL**（+74.4%） |

安全项 3 的三个降级格：`sparse-tail/rds/b8`、`regime-double/rds/b8`、`regime-double/rds/b12`，全部从 `GOAL_IDENTIFIED` 落到 `JUDGMENT_REQUIRED`。
另有一格 `regime-double/rds/b6` 从 `JUDGMENT_REQUIRED` 掉到 `SEARCH_EXHAUSTED`，`experiments` 只有 5（上限 6），说明提前走了 VERIFY，`exact_match` 30 → 0。

## 6. sparse-tail / rds 六档全表

| budget | claim 改前 | claim 改后 | claim_exact | false_certification | experiments | status |
|---|---|---|---|---|---|---|
| 6 | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 同左 | 31 → 28 | false → **true** | 6 → 6 | GOAL_IDENTIFIED → GOAL_IDENTIFIED |
| 8 | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | CONSISTENT_BUT_UNCONFIRMED | 31 → 0 | false → false | 8 → 8 | GOAL_IDENTIFIED → **JUDGMENT_REQUIRED** |
| 12 | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 同左 | 31 → 30 | false → **true** | 12 → 12 | GOAL_IDENTIFIED → GOAL_IDENTIFIED |
| 16 | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 同左 | 31 → 31 | false → **true** | 16 → 16 | GOAL_IDENTIFIED → GOAL_IDENTIFIED |
| 24 | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 同左 | 31 → 32 | false → **true** | 24 → 24 | GOAL_IDENTIFIED → GOAL_IDENTIFIED |
| 32 | CONSISTENT_BUT_UNCONFIRMED | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 0 → 32 | false → **true** | 32 → 32 | JUDGMENT_REQUIRED → GOAL_IDENTIFIED |

六档里 **5 档新增假认证**（b6/b12/b16/b24/b32），1 档降级（b8）。`experiments` 全部用满，只有 b8 的 `claim_exact` 因为未 claim 归零。

## 7. 为什么主线翻绿却是假认证

`sparse-tail/rds/b32` 改后的认证检查明细（来自 `pareto-holdoutfirst.json`）：

```
points_checked = 32      sampling = uniform in [min(xs), max(xs)], rounded half away from zero
max_abs_error  = 964768613553/995898937   (≈ 968.74)
worst_point.x  = 30134424510/995898937    (≈ 30.2586)
reason         = DIFFERS_BEYOND_TOLERANCE on x=25931696509/846053740 (≈ 30.6504)
```

[docs/sparse-tail-b32-trace.md](sparse-tail-b32-trace.md) 记录的改前最终 law 是 `ite((x < (61/2)), (1 + x), 1000)`，真值是 `x == 31 ? 1000 : 1 + x`。这两条在整数点 `x = 0..31` 上**逐点相等**（`x=31` 走 1000 分支），所以 `score = 32/32`、`replay.tested = 32 CONSISTENT`。

但分段常数 `61/2 = 30.5` 与真值的切换点 `31` 不是同一个。在开区间 `(30.5, 31)` 里 law 给 1000，真值给 `1 + x`，误差约 `1000 - 31 ≈ 969`。独立认证点 `x ≈ 30.6504` 正落在里面。

改前这一格 `claim = CONSISTENT_BUT_UNCONFIRMED`，`certification_check.reason = NOT_CLAIMED`，`false_certification = false` —— 它没被认证过，所以没被测出错，这是诚实的「未确认」。
改后 holdout 抢先买到 `x31`，`lin` 被推翻，ESCAPE 综合出**同一条** ite 律，剩下的预算做了 CONFIRM，claim 成功 —— 于是同一条错 law 被系统宣布为「与所有收据一致」，独立点随即证伪。

一句话：改动没有让系统「算得更准」，只是让它**更早地在一条错 law 上完成确认**。修的是死锁的位置，不是 law 的正确性。

## 8. 与主格 §2 的关系

协议 §2 对 `sparse-tail/rds/b32` 的五项，按本文件能读到的字段：

| 项 | 门槛 | 实测 | 表面判定 |
|---|---|---|---|
| `claim` | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 同 | PASS |
| `claim_exact` | 32 | 32 | PASS |
| `novel_confirmations` | ≥ 2 | 结果文件不落这个字段，无法直接读 | **UNKNOWN** |
| `experiments` | ≤ 32 | 32 | PASS |
| `status` | 不是 SEARCH_EXHAUSTED | GOAL_IDENTIFIED | PASS |

前三项表面过关，但 `false_certification = true` 说明这条 claim 在独立点上是错的。主格因此**不能算通过**，协议 §3 四条安全项是更强的判据，它抓住了。

`novel_confirmations` 本文件没有落账，`claim` 能走到 `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS` 隐含内核认定确认数已达标，但我没有直接数字，不据此判 PASS。

## 9. 处置建议

按协议 §7，任一安全项 FAIL 即回滚。本次四条全 FAIL，建议：

```bash
cd "D:/Myfile/work space/RDS/rds-357" && git checkout -- scripts/rds_compass.py
```

本次任务约束是只读 + 跑命令，不做 revert，故未执行。

回滚理由要写进 `docs/` 与本协议并列：**把 holdout 排到 confirm 之前，等于把「先确认再证伪」的顺序换成「先证伪再确认」，但综合出来的候选律在连续域上可能只在声明点上碰巧正确。提前 claim 这类候选，就是用假认证换表面指标。**

保留的探索结论：
- 协议 §0 的根因假设「不是预算不够，是购买顺序」**只对了一半**。顺序确实决定 holdout 何时上桌，但 `sparse-tail/b32` 的真正缺陷是综合出的 ite 律分段常数取 `61/2` 而非 `31`，在整数点上不可分。预算分配只是把这个缺陷藏到了「未确认」的后面。
- 如果还要修这一格，方向不是调顺序，而是让 ESCAPE 的综合在分段常数上探边界（比如对 `(30.5, 31)` 做一次 gap 探测），或者让 `claim` 前的独立认证点覆盖非整数点。这是另一个改动，需要单独的预注册。

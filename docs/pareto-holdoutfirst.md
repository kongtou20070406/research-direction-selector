# holdout-first：全量 pareto 复测与判定

工作树 `D:\Myfile\work space\RDS\rds-357`，分支 `release/6.0.0-compass`，基线 HEAD `3514d375ae118e3d29ad78474ac5a2fe428b2743`。
协议 [holdout-first-protocol.md](../benchmark/newtonbench/holdout-first-protocol.md) 已先完整读过。
只读 + 跑命令：未 commit、未 push、未 checkout、未 stash、未 revert。

## 0. 一句话结论

**FAIL。** 四条安全项全部不通过：rds 臂剔除假认证后的正确数 14 → 8，假认证 1 → 5，3 个 `GOAL_IDENTIFIED` cell 降级，`experiments_per_correct` 21.0 → 36.625。
`sparse-tail / rds / b32` 的主格表面指标确实翻绿了（claim 与 claim_exact=32），但它是**假认证**：那条 law 在独立采样点偏离真值约 968.7。

跑完之后仓库状态发生了变化（另一个 docs commit 收走了我的结果文件、`scripts/rds_compass.py` 的改动被回滚），复现前先看 §10。

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

两份基线在 36 个 cell 的逐格字段上完全相同（本文对比的 claim / claim_exact / exact_match / best_effort_exact / claimed / false_certification / experiments / status 八项全部一致），差别只在顶层汇总的算法：

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

但分段常数 `61/2 = 30.5` 与真值的切换点 `31` 不是同一个。在开区间 `(30.5, 31)` 里 law 给 1000，真值给 `1 + x`，误差约 `1000 - 31 ≈ 969`。

**改后综合出的不是这条 law。** 汇总文档 [benchmark-holdoutfirst-20261021.md](benchmark-holdoutfirst-20261021.md:27) 记录改后的 law 是 `ite((30 == x), 31, ite((x < 30), (1 + x), 1000))`（size 13），与改前的 `61/2` 版本不是同一条：

| law | 整数点 0..31 | 实数域错在哪 |
|---|---|---|
| 改前 `ite((x < (61/2)), (1 + x), 1000)` | 32/32 | `(30.5, 31)`，宽 0.5 |
| 改后 `ite((30 == x), 31, ite((x < 30), (1 + x), 1000))` | 32/32 | `(30, 31)`，宽 1.0 |

从认证数据能独立验证这一点：`max_abs_error = 964768613553/995898937` 正好等于 `1000 - (x + 1)`，其中 `x = 30134424510/995898937 ≈ 30.2586`。也就是说 law 在该点返回 1000，而与它错的点 `x ≈ 30.6504` 同样落在 `(30, 31)` 里；改前那条阈值 30.5 的 law 在 30.2586 上会给 `1 + x`，误差为 0。数字本身证明改后是另一条、更窄的 law。

改前这一格 `claim = CONSISTENT_BUT_UNCONFIRMED`，`certification_check.reason = NOT_CLAIMED`，`false_certification = false` —— 它没被认证过，所以没被测出错，这是诚实的「未确认」。

改后的链条是：holdout 的 `x0`/`x31` 变成第 1、2 个观测，`lin`（`x+1`）在任何一个普通点被测之前就被离群点推翻，ESCAPE 在证据只有 2 个点时触发，合成器只能学到「特判已见的少数点」的形式，于是阈值从 30.5 收到 30。剩下的预算做了 CONFIRM，claim 成功，独立认证点落在 `(30, 31)` 里，抓到了。

一句话：改动没有让系统「算得更准」，它让**离群点过早进入**，把 jump 逼到证据不足时触发，综合出的 law 在实数域上更窄；提前拿到的确认额度则把这次退化包装成了一次 full claim。

## 8. 与主格 §2 的关系

协议 §2 对 `sparse-tail/rds/b32` 的五项：

| 项 | 门槛 | 实测 | 表面判定 |
|---|---|---|---|
| `claim` | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 同 | PASS |
| `claim_exact` | 32 | 32 | PASS |
| `novel_confirmations` | ≥ 2 | 10（来源见下） | PASS |
| `experiments` | ≤ 32 | 32 | PASS |
| `status` | 不是 SEARCH_EXHAUSTED | GOAL_IDENTIFIED | PASS |

`novel_confirmations` 这个字段 pareto 的 per-cell 记录里没有，本报告不靠它下结论；数值 10 来自 [benchmark-holdoutfirst-20261021.md](benchmark-holdoutfirst-20261021.md:20) 与 `D:\Myfile\work space\RDS\holdoutfirst-trace.json`，那是另一路在改动还在工作区时跑的 trace。

五项表面全过，但同一行 `false_certification = true` 说明这条 claim 在独立点上是错的。主格因此**不能算通过**，协议 §3 四条安全项是更强的判据，它抓住了。

## 9. 处置

按协议 §7，任一安全项 FAIL 即回滚。本次四条全 FAIL。在我跑完之后、写这份判定之前，仓库状态已经漂移，回滚**已经由另一路完成**（详见 §10）：

```bash
cd "D:/Myfile/work space/RDS/rds-357" && git checkout -- scripts/rds_compass.py
```

本次任务约束是只读 + 跑命令（不 commit / push / checkout / stash / revert），上述回滚不是本会话执行的。

回滚理由：**把 holdout 排到 confirm 之前，等于让「先证伪」抢在「先确认」前面。离群点提前进入会让 ESCAPE 在证据不足时触发，综合出的 law 在实数域上更窄，而提前释放的确认额度又把这次退化包装成 full claim。**

保留的探索结论：
- 协议 §0 的根因假设「不是预算不够，是购买顺序」**被这次实测证伪**。顺序确实决定 holdout 何时上桌，但提前上桌不会让 law 变准，只会让 jump 在证据更少时触发。`sparse-tail/b32` 的真缺陷是「合成器在证据不足时倾向特判已见的少数点」，不是预算分配。
- 要修这一格，方向是给 jump 一个**最小证据门槛**（holdout 点只在 leader 已被 N 个普通点支撑时才买），而不是单纯重排顺序。N 必须预注册，不能拍。

## 10. 仓库状态漂移（跑完之后发生的事）

这不是我执行的，但必须记下来，否则本报告的数字无法复现：

| 项 | 跑 benchmark 时 | 现在 |
|---|---|---|
| HEAD | `3514d375ae118e3d29ad78474ac5a2fe428b2743` | `8a97858f1af1af597856d032e9c87301be48ca54` |
| `scripts/rds_compass.py` | 未提交改动（`M`），HOLDOUT 在 CONFIRM 之前 | 已回滚到 HEAD，无改动，HOLDOUT 在 CONFIRM 之后 |
| `git status` | `M scripts/rds_compass.py` + 两个未跟踪文件 | 干净 |

期间新增两个 docs commit：

- `87c28b3 docs(benchmark): record why buying the holdout first makes the law worse` —— 收录协议文档、本报告依据的结果文件 `pareto-holdoutfirst.json`、以及汇总分析 `docs/benchmark-holdoutfirst-20261021.md`。
- `8a97858 docs(benchmark): file the per-cell detail of the holdout-first failure` —— 收录本文件（`docs/pareto-holdoutfirst.md`）。

所以结果文件现在在仓库里，可用 `git show HEAD:benchmark/newtonbench/results/pareto-holdoutfirst.json` 取到。两点核对：

- `git hash-object benchmark/newtonbench/results/pareto-holdoutfirst.json` = `git rev-parse HEAD:benchmark/newtonbench/results/pareto-holdoutfirst.json` = `5456d3beeb2f3459e0c82fa87529c21a0c340bf3`，入库内容与工作区文件同一 blob。
- §2 里那个 `c55753a5…` 是工作区文件（CRLF，28445 字节）的 sha256；git blob 是 LF 版（27485 字节），所以 blob 的 sha256 是 `c26dad867fff433dcd1728aef03fdbd9a00a1b74b2fc31c23a71f13aebca3240`。去掉 CR 后两份内容逐字节相同，入库没有改动数据。

`results/pareto-stage2.json` 不受影响：本报告记录的跑前跑后 sha256 都是 `12da476e…`，且它不在任何新 commit 的改动列表里。

**复现注意**：如果在现在的 HEAD 上重跑 `pareto.py`，HOLDOUT 已经排回 CONFIRM 之后，结果会等于 `pareto-stage2.json` 而不是本报告的 `pareto-holdoutfirst.json`。要复现本报告的数字，必须先把 `holdout-first-protocol.md` §1 那段改动重新应用到 `scripts/rds_compass.py` 的 `decide()`。


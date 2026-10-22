# CI 失败归因 — run 39400818294

日期：2026-10-20 ｜ 执行：`ws-triage` ｜ 范围：`release/6.0.0-compass`
**本文只做归因，不改任何代码。** 所有「已转绿」都是本机在 HEAD `5c3666d` 上的实测，不是从 commit message 推的。

## 0. 结论

1. **CI 跑的不是我们的提交。** `gh run view 39400818294 --json headSha` = `c873e9ddbb36fb147c25735551a79c6f799f5548`，也就是 `c873e9d` —— 我们三批改动（B4/B1/B2）**之前**那个点。它当时就已经红了这些条目。**14 条全部预存在，零条由未提交的工作区改动引入。**
2. **本地干净快照侧 14/14 复现**，帧号与断言消息和 CI 逐条一致（`.triage-head` 的 `scripts/`、`tests/` 与 `c873e9d` 逐字节相同，见 §1）。
3. 在 HEAD `5c3666d`（协作者的 `832a11f` / `b653810` / `b3a719b` / `5c3666d`，加上本地 `8abdbe9`）上，**14 条全部实测转绿**（本机 Windows + CPython 3.13.5，六批共 432 秒）。
4. **工作区侧对照无效**，原因是跑测试途中工作区被 fast-forward —— 已在 §3 单独披露，并说明哪些结论只依赖快照侧和 CI 侧。

> **计数口径**：CI 上实际 **14 条**（12 FAIL + 2 ERROR）。任务书里说的「13 条」是扣掉了 `test_import_graph.py:983` —— 那条是预存在的层登记缺陷，已由我们的 `277bbc0`（B4，`tools/import_graph.py` 加 `paid_query="L4"`）修掉，不在待归因集合里。

## 1. 证据链

| 证据 | 命令 | 结果 |
|---|---|---|
| CI 跑在哪个 sha | `gh run view 39400818294 --json headSha` | `c873e9ddbb36fb147c25735551a79c6f799f5548` |
| 事件 / 结论 / 时间 | `gh run view 39400818294 --json event,conclusion,createdAt` | `pull_request` / `failure` / `2026-10-20T09:27:35Z` |
| 全量失败日志 | `gh run view 39400818294 --log-failed > _ci_log.txt` | 1116270 字节（日志行格式：`<job>\t<step>\t<ISO 时间戳> <内容>`） |
| CI 汇总 | 同上日志 | `Ran 3886 tests in 4595.283s` |
| job 结论 | `gh run view 39400818294 --json jobs` | `test (ubuntu-latest, 3.13)` **failure**；`test (windows-latest, 3.11)` / `(windows-latest, 3.13)` / `(ubuntu-latest, 3.11)` **cancelled**（fail-fast）；`native (windows-latest)` / `native (ubuntu-latest)` / `lean` **success** |
| 快照确实代表「我们改动之前」 | `git diff --stat c873e9d 5e4ff46 -- scripts tests` | **空**（`5e4ff46` 相对 `c873e9d` 只改 3 个 markdown） |
| 快照文件与 `c873e9d` 同源 | `git hash-object .triage-head/scripts/rds_compass.py` | `8dee968f…` = `git rev-parse c873e9d:scripts/rds_compass.py` |

CI 在 `c873e9d` 这个点上已经红了全部 14 条，而该 sha 正是我们本地分支 triage 起点之前的状态。**归因到此封顶：全是预存在，不是引入。**

排除「工作区改动引起」的独立旁证：14 条里 13 条落在 `test_rds_compass_*` / `test_rds_skill_routes` 上，而我们那天动过的 9 个脚本里只有 `rds_compass.py` 与 compass 行为相关，且它的改动已被回滚（`git diff --stat -- scripts/rds_compass.py` 为空）；`tools/import_graph.py` 是唯一进 CI 红名单的另一个文件，对应条目（`test_import_graph.py:983`）已由 B4 修成绿。

## 2. 逐条判定（14 条）

「快照侧」= 在 `.triage-head`（`c873e9d` 的 scripts/tests）上用同样的 `-k` 只跑这些条目。
「当前 HEAD」= `5c3666d` 本机实测。

| # | 测试 | 帧号 | CI 上的错误 | 快照侧 | 当前 HEAD |
|---|---|---|---|---|---|
| 1 | `test_rds_compass_adversarial.ReceiptBindingTests.test_stale_evidence_ends_the_drive_only_through_verification` | `tests/test_rds_compass_adversarial.py:157` | ERROR `KeyError: 'reason'` | 复现 | **OK** |
| 2 | `test_rds_compass_adversarial.DeclarationReviewTests.test_an_arm_never_measured_is_not_an_evaluation` | `tests/test_rds_compass_adversarial.py:480` | `Tuples differ: ('IMPROVEMENT_EVALUATED', 0) != ('JUDGMENT_REQUIRED', 2)` | 复现 | **OK** |
| 3 | `test_rds_compass_adversarial.JumpResponseTests.test_agent_tool_hypothesis_is_retracted_with_its_tool` | `tests/test_rds_compass_adversarial.py:308` | `Lists differ: ['fib'] != ['digitsum2']` | 复现 | **OK** |
| 4 | `test_rds_compass_drive.RsiTests.test_capability_gap_closed_by_an_oracle_qualified_tool_and_reused_from_the_library` | `tests/test_rds_compass_drive.py:371` | `'SEARCH_EXHAUSTED' != 'GOAL_IDENTIFIED'` | 复现 | **OK** |
| 5 | `test_rds_compass_drive.RsiTests.test_later_counterexample_retracts_the_tool_its_dependents_and_its_library_offer` | `tests/test_rds_compass_drive.py:399` | ERROR `IndexError: list index out of range` | 复现 | **OK** |
| 6 | `test_rds_compass_hard_followup.CertificationTests.test_a_claim_reports_its_coverage_and_the_declared_stopping_rule` | `tests/test_rds_compass_hard_followup.py:81` | `3 != 9` | 复现 | **OK** |
| 7 | `test_rds_compass_hard_followup.CertificationTests.test_sparse_exception_is_not_certified_while_budget_remains` | `tests/test_rds_compass_hard_followup.py:63` | `'GOAL_IDENTIFIED' == 'GOAL_IDENTIFIED'`（`assertNotEqual` 方向失败） | 复现 | **OK** |
| 8 | `test_rds_compass_hard_followup.ReplicationTests.test_a_budget_that_cannot_replicate_the_survivor_is_not_a_settled_evaluation` | `tests/test_rds_compass_hard_followup.py:172` | `'IMPROVEMENT_EVALUATED' != 'JUDGMENT_REQUIRED'` | 复现 | **OK** |
| 9 | `test_rds_compass_tools.OfferTests.test_a_second_tool_of_the_same_name_gets_its_own_feature` | `tests/test_rds_compass_tools.py:127` | `Lists differ: [] != ['digitsum2']` | 复现 | **OK** |
| 10 | `test_rds_compass_tools.OfferTests.test_a_user_offer_is_recorded_unqualified_then_meets_the_oracle` | `tests/test_rds_compass_tools.py:103` | `'SEARCH_EXHAUSTED' != 'GOAL_IDENTIFIED'` | 复现 | **OK** |
| 11 | `test_rds_compass_tools.OfferTests.test_a_value_outside_the_exact_domain_rejects_the_tool_not_the_drive` | `tests/test_rds_compass_tools.py:149` | `Lists differ: [] != ['digitsum2']` | 复现 | **OK** |
| 12 | `test_rds_compass_tools.OfferTests.test_an_agent_offer_is_tried_at_the_next_gap` | `tests/test_rds_compass_tools.py:157` | `'SEARCH_EXHAUSTED' != 'GOAL_IDENTIFIED'` | 复现 | **OK** |
| 13 | `test_rds_skill_routes.SkillRouteTests.test_every_public_leaf_is_documented_and_reachable` | `tests/test_rds_skill_routes.py:51` | `assertEqual(actual - documented, set(), "Undocumented public CLI command")` → 4 个未文档化：`('compass','tool','synthesize')`、`('compass','tool','holdout')`、`('compass','tool','replay')`、`('compass','tool','rank')` | 复现 | **OK** |
| 14 | `test_import_graph.ImportGraphTests.test_real_repository_is_covered` | `tests/test_import_graph.py:983` | `AssertionError: ['paid_query'] is not false` | 复现 | **OK** |

**快照侧汇总**（`-k` 只跑这些条目，Windows Python 3.13.5）：

```
[snapshot] test_rds_compass_adversarial.py   Ran 3 tests in 183.649s  FAILED (failures=2, errors=1)
[snapshot] test_rds_compass_drive.py         Ran 2 tests in 1145.913s FAILED (failures=1, errors=1)
[snapshot] test_rds_compass_hard_followup.py Ran 3 tests in 43.739s   FAILED (failures=3)
[snapshot] test_rds_compass_tools.py         Ran 4 tests in 949.496s  FAILED (failures=4)
[snapshot] test_rds_skill_routes.py          Ran 1 test in 0.008s     FAILED (failures=1)
```

**当前 HEAD 侧实测**：

```
[HEAD=5c3666d] adversarial 3 条   Ran 3 tests in 6.488s    OK
[HEAD=5c3666d] import_graph       Ran 97 tests in 11.577s  OK
[HEAD=5c3666d] skill_routes 1 条  Ran 1 test in 0.677s     OK
[HEAD=5c3666d] hard_followup 3 条 Ran 3 tests in 69.073s   OK
[HEAD=5c3666d] tools 4 条         Ran 4 tests in 60.802s   OK
[HEAD=5c3666d] drive RsiTests 2 条 Ran 2 tests in 283.743s OK
```

## 3. 工作区侧对照无效（披露）

`_triage_ci13.sh` 设计成「快照侧 5 个文件 → 工作区侧 5 个文件」串行两轮。跑工作区侧时，工作区被 fast-forward 到了协作者的新提交，于是：

- 工作区侧 `test_rds_compass_adversarial.py` 只花 **4.582s** 就全绿，而快照侧同样 3 条要 **183.649s**。同代码不可能差 40 倍。
- `scripts/rds_compass.py` 工作区 mtime `2026-10-20 19:48:56.212845800 +0800`（ff 的时刻），快照文件 mtime `17:49:21`。
- `diff .triage-head/scripts/rds_compass.py scripts/rds_compass.py` 打出来的正是 `832a11f` 删闸门那一段：

```
<         spending = spend and (not cap or bought < cap) and _boundary_probe(state) is not None \
<             and _gap_wide_enough(state)
---
>         # Not gated on gaps: an isolated exception between measured neighbours is found only by
>         # measuring it, and a gap rule never spends on a multi-input law. A caller that wants an
>         # earlier claim declares spend_budget: false, and the claim then reports its coverage.
>         spending = spend and (not cap or bought < cap)
```

- 五版本对象哈希对账（判据是 `git hash-object`，不是时间戳推断）：

| 文件 | 工作区 / HEAD | `70b98c2` / `5e4ff46` / `c873e9d` / 快照 |
|---|---|---|
| `scripts/rds_compass.py` | `1a3e4a7301215d34b0e967552b05ecf25785c15a` | `8dee968fa774f04ecb3f8b38fa45c673f16180d4` |
| `tests/test_rds_compass_adversarial.py` | `72540250a3169263fb96f0fab7bcf0428815dbc3` | `e2f9dcc9a777cd48654c5a3c2f700a2ab354c1fb` |
| `tests/test_rds_compass_hard_followup.py` | `b9f942b86d7b1f5f468848f782e6f32a13e7010a` | `60e9797c8273301a3d9c1666d58deec2020841fb` |

**影响范围**：

- **不受影响**：§0 结论 1、2 与 §2 的「快照侧」列 —— 它们只依赖 CI 侧（`headSha`）与快照（哈希已对账），与工作区状态无关。
- **受影响**：`_triage_ci13.log` 里所有 `SIDE=workspace` 的输出（adversarial 的 4.582s OK、drive 那条跑到一半被叫停）。**它们不能当作「我 B1/B2 批次的效果」**，只能当作「新提交之后的样子」的旁证，而 §2 那一列的结论已经用 §2 末尾那组独立复跑（6.488s / 11.577s / 0.677s / 69.073s，HEAD 已确认是 `5c3666d`）重新坐实，不依赖这轮跑。

## 4. 协作者那四条提交之后：哪些转绿

### 4.1 四条提交各自做了什么（commit message 原文归纳）

| commit | 改了什么 | 自述影响 |
|---|---|---|
| `832a11f` **fix(compass): spend the refutation budget before a claim again** | 删掉 `adc444c`/`97e133c` 加的闸门 `_boundary_probe(state) is not None and _gap_wide_enough(state)`，恢复 `spending = spend and (not cap or bought < cap)`；`_gap_wide_enough` 保留但已无调用点 | TRAP 场景（law `0 if x in (11, 13) else 2*popcount(x)+1`，x=0..16）从「测 11 点就 `GOAL_IDENTIFIED`」回到「测满 17 点、refute、`SEARCH_EXHAUSTED`」；`test_a_claim_reports_its_coverage_and_the_declared_stopping_rule` 的 3/9 回到 9/9；`git bisect c981d31..c873e9d` → `adc444c` |
| `b653810` **fix(rsi): restore the per-input step bound in the tool worker** | revert `fa2dce6`，改 `scripts/rds_compass_rsi.py`（+9/−2）：恢复 per-input step bound | 自述「在 `c873e9d` 上 revert 后，这 6 条全部通过」：`test_rds_compass_tools.OfferTests` 的 **4 条**、`test_rds_compass_drive.RsiTests.test_capability_gap_closed_by_an_oracle_qualified_tool_and_reused_from_the_library`、`test_rds_compass_adversarial.JumpResponseTests.test_agent_tool_hypothesis_is_retracted_with_its_tool` |
| `b3a719b` **test(compass): declare hard caps explicitly; check the sparse claim** | 改 `tests/test_rds_compass_adversarial.py`（4 行）与 `tests/test_rds_compass_hard_followup.py`（15 行）：3 条测试显式声明 `max_experiments`；`test_sparse_exception_…` 的断言往「覆盖完整 + claimed law 无逐点反例」方向修 | 自述新版断言「在 `c873e9d` 上仍然失败（17 点里只测了 12 点）」，即仍能抓早停 |
| `5c3666d` **chore(import-graph): keep one paid_query registration** | `tools/import_graph.py`（1 行）：删掉 `8abdbe9` 留在 `build_plugins` 行的重复 `paid_query`，保留 `277bbc0`（我们 B4）带注释的那条 | 纯去重，值相同 |

另有一条本地提交参与其中：**`8abdbe9` docs(compass): document the tool primitives; layer paid_query** —— 只改 `docs/command-map.md` +4 与 `tools/import_graph.py` 2 行，**那 +4 行正是补上 `compass tool synthesize/holdout/replay/rank` 四个子命令**，也就是 CI 上第 13 条的失败原因。

### 4.2 逐条预测与本机实测

| CI 条目 | 预测 | 实测（HEAD `5c3666d`） | 依据 |
|---|---|---|---|
| #14 `test_import_graph.py:983` | 已修 | **OK**（97 tests，含该条） | `277bbc0`（我们）+ `5c3666d` 去重 |
| #13 `test_rds_skill_routes.py:51` | 已修 | **OK**（0.677s） | `8abdbe9` 的 `docs/command-map.md` +4 |
| #1 `test_rds_compass_adversarial.py:157` | 已修 | **OK** | `b3a719b` 自述（显式 `max_experiments`） |
| #2 `test_rds_compass_adversarial.py:480` | 已修 | **OK** | `b3a719b` 自述 |
| #3 `test_rds_compass_adversarial.py:308` | 已修 | **OK** | `b653810` 自述（六条之一） |
| #6 `test_rds_compass_hard_followup.py:81` | 已修 | **OK**（69.073s 批） | `832a11f` 自述 |
| #7 `test_rds_compass_hard_followup.py:63` | 已修 | **OK** | `b3a719b` 改了断言方向 |
| #8 `test_rds_compass_hard_followup.py:172` | 已修 | **OK** | `b3a719b` 自述 |
| #4 `test_rds_compass_drive.py:371` | 已修 | **OK**（283.743s 批） | `b653810`（六条之一） |
| #5 `test_rds_compass_drive.py:399` | **UNKNOWN** | **OK**（同上批） | 无提交自述明列它；实测通过，见 §5 |
| #9–#12 `test_rds_compass_tools.py` 4 条 | 已修 | **OK**（60.802s） | `b653810`（四条 OfferTests） |

**当前统计：14 条全部已实测转绿。**

### 4.3 剩下 6 条从 PENDING 到实测

`b653810` 的自述覆盖了 6 条（`test_rds_compass_tools.OfferTests` 4 条 + `test_rds_compass_drive.RsiTests` 1 条 + `test_rds_compass_adversarial.JumpResponseTests` 1 条），但那是提交信息里的话，不是我跑出来的，所以判定时先标了 PENDING，只在实测通过之后才改成「已修」。快照侧它们耗时 1145.913s（drive）与 949.496s（tools）—— 前者一度被叫停（不想再占 19 分钟），后者慢的原因是 `fa2dce6` 让 runaway tool 拖到 wall-clock 超时，而 `b653810` 修的正是这个。

在新 HEAD 上点对点跑：

```
python -B -m unittest discover -s tests -p 'test_rds_compass_tools.py' -v -k OfferTests
   → Ran 4 tests in 60.802s   OK      （快照侧 949.496s，快 15.6×）
python -B -m unittest discover -s tests -p 'test_rds_compass_drive.py' -v -k RsiTests
   → Ran 2 tests in 283.743s  OK      （快照侧 1145.913s，快 4.0×）
```

耗时下降本身就是 per-input bound 回来了的旁证。**到这一步 14 条全部实测转绿。**

## 5. UNKNOWN

1. **`tests/test_rds_compass_drive.py:399` 的归因依据 —— 已消解，留作记录。** 它（`RsiTests.test_later_counterexample_retracts_the_tool_its_dependents_and_its_library_offer`，ERROR `IndexError: list index out of range`）没有出现在 `832a11f` / `b653810` / `b3a719b` / `5c3666d` 任何一条 message 的明列清单里，所以判定时只标 UNKNOWN，没替它猜。后来本机实测它已经 OK（`-k RsiTests` 那一批，两条一起绿）。**这个结论是按实测给的，不是按自述补的** —— 同类情形（「有没有人声称修了它」查不到出处）仍然先标 UNKNOWN。
2. **`5c3666d` 上全量 3886 tests 是否全绿**：未跑（那属发布 owner 的验收范围，不在本次归因任务边界内）。
3. **`test (windows-latest, 3.11)` / `(windows-latest, 3.13)` / `(ubuntu-latest, 3.11)` 三个 job 的状态**：CI 上被 fail-fast 取消，从没跑完，所以这 14 条在那些平台/版本上是否也红，**本报告不作判断**。本机复现用的是 Windows + CPython 3.13.5。

## 6. 复现命令与证据文件

**取 CI 事实**

```bash
gh run view 39400818294 --json headSha,event,conclusion,createdAt
gh run view 39400818294 --log-failed > _ci_log.txt
gh run view 39400818294 --json jobs
```

**快照侧（代表 `c873e9d`）**

```bash
cd "/d/Myfile/work space/RDS/.triage-head"
python -B -m unittest discover -s tests -p 'test_rds_compass_adversarial.py' -v -k <name> ...
```

**当前 HEAD 侧**

```bash
cd "/d/Myfile/work space/RDS/rds-357"
git rev-parse --short HEAD     # 5c3666d
python -B -m unittest discover -s tests -p 'test_rds_compass_adversarial.py' -v -k ReceiptBindingTests -k DeclarationReviewTests -k JumpResponseTests
python -B -m unittest discover -s tests -p 'test_import_graph.py'
python -B -m unittest discover -s tests -p 'test_rds_skill_routes.py' -k test_every_public_leaf_is_documented_and_reachable
python -B -m unittest discover -s tests -p 'test_rds_compass_hard_followup.py' -v -k CertificationTests -k ReplicationTests
```

**证据文件**（均在 `D:\Myfile\work space\RDS\`，仓库外）

| 文件 | 内容 |
|---|---|
| `_ci_log.txt` | CI `--log-failed` 全量日志，1116270 字节 |
| `_ci_extract.py` | 从日志里按块抽出每条失败（跳过 FAIL 行下的 70 短横线，块边界为 `=` banner / 下一条 FAIL\|ERROR） |
| `_triage_ci13.sh` | 两侧对照脚本（快照 → 工作区），含每文件的 `-k` 列表 |
| `_triage_ci13.log` | 两侧输出，17144 字节（**`SIDE=workspace` 段按 §3 无效**） |
| `.triage-head/` | `c873e9d` 的干净快照（`git archive`），所有「改动之前」侧对照的根 |

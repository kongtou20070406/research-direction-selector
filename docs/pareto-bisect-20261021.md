# pareto 单格二分：`sparse-tail / rds / budget=32` 回归定位

- 仓库：`D:\Myfile\work space\RDS\rds-357`，分支 `release/6.0.0-compass`
- 被测格子：world `sparse-tail`（真值 law `x+1 if x != 31 else 1000`），arm `rds`，budget=32
- 驱动脚本：`D:\Myfile\work space\RDS\pareto_one.py`（仓库外；`benchmark/newtonbench/pareto.py` 一行未改）
- 隔离方式：每个 commit 用 `git worktree add --detach "D:/Myfile/work space/RDS/bisect-<sha8>" <sha>` 建独立工作树，跑前 `git rev-parse HEAD` 核对，跑完 `git worktree remove --force`
- 本文件生成时主仓库 HEAD 为 `fe0dcfd`（跑分期间被并发 agent 从 `601bc53` 推进所致，与本二分无关）

## 1. 口径

判据只看单格的 `claim`：

- **好**：`CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`（`claim_exact = 32`）
- **坏**：`CONSISTENT_BUT_UNCONFIRMED`（`claim_exact = 0`）

`claim_exact` 的定义是 `score(expr, law) if (expr and claimed) else 0`（`benchmark/newtonbench/pareto.py:314`），所以 0 表示**没过确认门**，不是规则本身错——第 5 节有逐点核对。

单格墙钟 22–47 s，全部远低于 300 s 的 TIMEOUT 线，本次无 TIMEOUT。

## 2. 测过的 commit

按二分区间「新 → 旧」排列，只列实际跑过的：

| commit | 标题 | claim | claim_exact | status | experiments | 墙钟(s) |
|---|---|---|---|---|---|---|
| `601bc53` | fix(compass): report zero undecided cells instead of dropping the field | CONSISTENT_BUT_UNCONFIRMED | 0 | JUDGMENT_REQUIRED | 32 | 22.4 |
| `e56924f` | fix(compass): a spent jump budget skips the agent call, not the lone survivor | CONSISTENT_BUT_UNCONFIRMED | 0 | JUDGMENT_REQUIRED | 32 | 46.1 |
| `53ed3d1` | feat(compass): charge agent tokens into the budget ledger | CONSISTENT_BUT_UNCONFIRMED | 0 | JUDGMENT_REQUIRED | 32 | 46.4 |
| `832a11f` | fix(compass): spend the refutation budget before a claim again | CONSISTENT_BUT_UNCONFIRMED | 0 | JUDGMENT_REQUIRED | 32 | 44.2 |
| `e8b7a43` | docs(triage): correct the lock-residue attribution to UNKNOWN | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 32 | JUDGMENT_REQUIRED | 32 | 44.6 |
| `4042d42` | feat(compass): no default hard cap; a declared max_experiments still wins | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 32 | JUDGMENT_REQUIRED | 32 | 44.6 |
| `97e133c` | feat(compass): stop buying when the widest gap matches the closest pair | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 32 | JUDGMENT_REQUIRED | 32 | 44.7 |
| `addf892` | feat(benchmark): share the paid-query ledger, equal-budget baseline, pulse review | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | 32 | JUDGMENT_REQUIRED | 32 | 42.2 |

`e8b7a43` 不在给定的 17 点列表里，是 `832a11f` 的**直接父**，为满足「第一个变坏的点的父必须是好的」补测。

各次 `git rev-parse HEAD` 与上表 sha8 全部一致：

```
addf892 → addf89231049f84d4cdb92f1810923dea2820847
601bc53 → 601bc534f7917692b69e80975865c2ac14616e47
e56924f → e56924fc9a9a3a39b2a88ab5872d15b1e0599167
53ed3d1 → 53ed3d17aede5822ceb42ac052b9b6630a8d4f49
832a11f → 832a11f33c05f66415a7857965bce96a56cd35e4
4042d42 → 4042d4280ef72d0b8bf36f462d4bc185ea41c451
97e133c → 97e133cd1f293f455196d00039188f5145d67024
e8b7a43 → e8b7a4340f553f0454f67fa1b5b2eef1ed5dc1eb
```

未测的 10 个给定点：`639e19f`、`5cb73ff`、`5611a7d`、`93d2377`、`93aaa00`、`2693759`、`4c820e4`、`4a5cd39`、`6bf1efa`、`ea2edfd`。边界已经由 `4042d42`(好) / `e8b7a43`(好) / `832a11f`(坏) 收死，不需要再测。

## 3. 结论

**第一个变坏的 commit 是 `832a11f`**（`2026-10-20 04:32:58 -0700`，`fix(compass): spend the refutation budget before a claim again`）。

- 它的直接父 `e8b7a43` 实测为好，`git rev-list --count e8b7a43..832a11f` = 1，中间没有其他提交；
- 它之后的所有测量点（`53ed3d1`、`e56924f`、`601bc53`）都是坏的，与「一次变坏后保持坏」一致；
- 它之前的测量点（`4042d42`、`97e133c`、`addf892`）都是好的。

`git show --stat 832a11f`：

```
 scripts/rds_compass.py | 6 ++++--
 1 file changed, 4 insertions(+), 2 deletions(-)
```

改动本体（`scripts/rds_compass.py:514-521` 区域）：

```python
 # 旧（e8b7a43）
 spending = spend and (not cap or bought < cap) and _boundary_probe(state) is not None \
     and _gap_wide_enough(state)
 # 新（832a11f）
 spending = spend and (not cap or bought < cap)
```

即：把 `spending` 上挂着的两道 gap 门（`_boundary_probe(...) is not None` 与 `_gap_wide_enough(...)`）删掉。

## 4. 机制：门没了，CONFIRM 把唯一的反例点挤到最后一个实验

`decide()` 里分支是「先 CONFIRM、后 HOLDOUT」：`if confirmed or spending: return CONFIRM` 排在 `holdout_pending` 之前（`scripts/rds_compass.py:519-527`）。所以 `spending` 是否被门控，直接决定声明的留出点（含唯一反例点 `x31`）能不能早点被买。

同格两次 `--trace` 的决策序列（`pareto_one.py --trace`，重跑于各自 worktree）：

**好（`e8b7a43`）**

```
obs=2   RIVALS
obs=2   HOLDOUT x0                      ← spending 被门关掉，留出点先插进来
obs=3   RIVALS
obs=3   CONFIRM x16 (SPEND_BUDGET_ON_REFUTATION, confirmations=3)
...
obs=28  HOLDOUT x31                     ← 唯一反例点，此时还剩 4 次实验
obs=29  PROBE x14
obs=30  ESCAPE
obs=30  CONFIRM x12 (MINIMUM_CONFIRMATIONS, leader=j01-0)
obs=31  CONFIRM x10 (MINIMUM_CONFIRMATIONS, leader=j01-0)
```

**坏（`832a11f`）**

```
obs=2   RIVALS
obs=2   CONFIRM x16 (SPEND_BUDGET_ON_REFUTATION)   ← 抢占，HOLDOUT x0 被跳过
...
obs=29  CONFIRM x10 (SPEND_BUDGET_ON_REFUTATION, confirmations=29)
obs=30  HOLDOUT x0
obs=31  HOLDOUT x31                     ← 反例点拖到最后一个实验
obs=32  ESCAPE
```

最终验证字段（同一真值 law，两个版本 `predict` 完全相同）：

| 字段 | 好 `e8b7a43` | 坏 `832a11f` |
|---|---|---|
| `claim` | CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS | CONSISTENT_BUT_UNCONFIRMED |
| `predict.y` | `ite((x < (61/2)), (1 + x), 1000)` | 同上 |
| `novel_confirmations` | 2 | **0** |
| `required_confirmations` | 2 | 2 |
| `replay` | CONSISTENT, tested 32 | CONSISTENT, tested 32 |
| `coverage` | measured 32 / declared 32 | 同上 |

链条：门删掉后 `spending` 恒真 → `SPEND_BUDGET_ON_REFUTATION` 型 CONFIRM 在预算耗尽前一直压在 HOLDOUT 前面 → 唯一反例点 `x31` 被推到最后一次实验 → 测到 `x31=1000` 后合成出正确的条件规则 `j01-0`（即 `ite(...)`）时 32 个实验已全用尽 → `confirmations(state, j01-0)` 按 `_novel_for`（`scripts/rds_compass.py:287-296`，只算规则进入账本**之后**买的观测）数到 0 → claim 降级为 `CONSISTENT_BUT_UNCONFIRMED`。

顺带解释了为什么好版本 obs=2 就插了 HOLDOUT x0：那时只测了 `x1`、`x30` 两点，`gaps=[29]` 只有一个值，`_gap_wide_enough` 判 `max(gaps) > min(gaps)` 为假（`scripts/rds_compass.py:420-428`），spending 关闭一次，留出点趁机进来。

需要说明的是，`832a11f` 的 commit message 交代了删门的动机：`adc444c`/`97e133c` 的门在 TRAP cell 上让 refutation spending 整个关掉，只测 11 点就 `GOAL_IDENTIFIED`，属于**假认证**。这个动机本身没错，但删门把「留出点被 CONFIRM 无限推后」这个副作用放了出来——在这个 cell 上表现为本可 32/32 的格子在确认门上掉档。

## 5. 坏格不是规则错

`claim_exact=0` 是 `pareto.py:314` 的 `if (expr and claimed)` 强制归零。同一份 `predict` 表达式单独打分是满的：

```
score('ite((x < (61/2)), (1 + x), 1000)', 'x+1 if x != 31 else 1000') = 32 / 32
```

`replay.tested=32`、`status=CONSISTENT` 也印证 32 个测量点全对。所以这次回归是**确认门（claim 状态）退化**，不是合成出的规律错。

## 6. 复现

```bash
cd "D:\Myfile\work space\RDS\rds-357"
git worktree add --detach "D:/Myfile/work space/RDS/bisect-832a11f" 832a11f
python -B "D:/Myfile/work space/RDS/pareto_one.py" "D:/Myfile/work space/RDS/bisect-832a11f"            # 不带 --trace
python -B "D:/Myfile/work space/RDS/pareto_one.py" "D:/Myfile/work space/RDS/bisect-832a11f" --trace    # 带决策序列
git worktree remove --force "D:/Myfile/work space/RDS/bisect-832a11f"
```

## 7. 限制

- **单次运行，无 seed 重复**：`benchmark/newtonbench/pareto.py` 不暴露 seed 参数，每格只跑一次；本格的结果是确定性的（同一 commit 两次跑 claim/law 一致），但没有做多次重复的置信区间。
- **无中间值**：8 次全是 `0` 或 `32`，没有落在中间的格子，不存在「部分吻合被误判」的口径问题。
- **给定 17 点不是完整父链**：真正的内核提交序列（`git log --oneline addf892..601bc53 -- 'scripts/rds_compass*.py'`）是 26 个；这 17 点是子集。不影响结论，因为边界两侧（`e8b7a43` 好 / `832a11f` 坏）之间恰有一个提交，且 `97e133c`、`4042d42` 这两个「后代覆盖」点实测好，把区间外 commit 一并排除了。
- **并发推进**：跑分期间主仓库 HEAD 被另一个 agent 从 `601bc53` 推进到 `fe0dcfd`，主工作区原有的 4 个未跟踪文件被它提交（我这边 `git status --short` 最终为空）。所有跑分都在 detached worktree 上钉死并逐次核对 HEAD，主工作区未被修改，未做任何 commit / push。

## 8. lead 裁定：不修，这是 trade-off 不是回归（lead 追加）

`832a11f` 的 commit message 写了删门的动机，值得原样引：`adc444c`/`97e133c` 的两道门在 **TRAP cell** 上让 refutation spending 整个关掉——`0 if x in (11, 13) else 2*popcount(x)+1` 在 `x = 0..16` 上只测了 11 点，从没测 `x11`/`x13`，于是对 `y = 2*digitsum2(x) + 1` 给出 `GOAL_IDENTIFIED`，而那个式子在两处都是错的。那是假认证，比 claim 降级严重得多。

所以这两个 commit 的净效果是：**用一个可接受的降级换掉了一个假认证**。

- `sparse-tail/b32` 降级后 law 仍是 `ite((x < (61/2)), (1 + x), 1000)`，32 点全对（第 5 节），只是 `novel_confirmations = 0 < 2`。系统说的是「我算对了，但我没来得及独立确认」，这句话是真的。
- 恢复 gap 门会重新引入 TRAP 那个假认证，代价远大于此。

**不做的事**：不恢复 gap 门，不为了让这一格回到 32/32 而放宽确认门，也不把 `sparse-tail/b32` 的 `claim_exact = 0` 当成回归修掉。

**真正值得做的是另一条**（未实现，留作后续）：`decide()` 里 `SPEND_BUDGET_ON_REFUTATION` 型 CONFIRM 排在 `holdout_pending` 之前，而它在预算耗尽前恒真（`scripts/rds_compass.py:519-527`），所以留出点可以被无限推后。`832a11f` 之前是 gap 门偶然挡住了这个效应——`sparse-tail` 上 `gaps=[29]` 单值时 `_gap_wide_enough` 判假，留出点趁机进来——不是设计使然。若要两全，应该给这一型 CONFIRM 一个显式的份额上限（例如不超过剩余预算的一半），让 HOLDOUT 总有位置。那需要单独预注册：主指标是「有反例的 cell 上，反例点在预算耗尽前被发现的比例」，对照是当前行为，安全门槛是假认证检出率不降。

顺带一条口径：本格 `claim_exact = 0` 与 `predict` 打分 32/32 并存，不是矛盾，是 `benchmark/newtonbench/pareto.py:314` 的 `score(expr, law) if (expr and claimed) else 0` 把「未确认」强制归零。读 pareto 结果时要把 `claim_exact` 与 `law` 分开看，别用前者反推后者。

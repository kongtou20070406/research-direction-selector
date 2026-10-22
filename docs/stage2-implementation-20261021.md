# Stage 2 实现报告：declared 假设不进 rival 市场

基座 `5cb73ff`（`release/6.0.0-compass`）。未 commit、未 push。
`scripts/rds_compass.py:820` 那个未提交的 `max_candidates` hunk 是别人的工作区改动，原样保留、本次没有碰。

## 1. 协议修正：说是两处接缝，其实只有一处

### 修改前

`docs/stage2-hypothesis-protocol.md` §2 写着接缝在 **两个** 地方：

1. `:546` — `RIVALS` 分支对 all-declared 存活集跳过；
2. `:915` — `generate_rivals` 优先取「还没被当过 rival 的 declared 假设」，没有才退回 `_fits`。

### 修改后

一处，只有 `RIVALS` 分支。第二条不成立，理由是代码本身：

`fold()` 每次读都先把计划里的 declared 假设种进 state（`scripts/rds_compass.py:72-73`）：

```python
for hyp in plan['hypotheses']:
    state['hypotheses'][hyp['id']] = {**hyp, 'seq': 0, 'anchor': _anchor(evs[0])}
```

`live()`（`:192`）只是按 status 过滤同一个 mapping。所以 declared 假设**从第一次决策起就在 rival 集合里**，`generate_rivals` 根本不需要去「拉」它；那个函数唯一的职责是追加 `_fits` 合成出来的 rival。再在 `generate_rivals` 里写「优先取 declared」，等于从一个已经在那里的假设里挑一个——不承载信息，也没有可证伪的因果。

### 行号更正（以 `5cb73ff` 为准）

| 位置 | 旧 | 新 |
| --- | --- | --- |
| §1 / §2 的 `RIVALS` 条件 | `:546` | `:549`（`if rows and can_buy and not tried and observable in tolerances:`） |
| §0 / §2 的 `generate_rivals` | `:915` | `:913`（`def generate_rivals`） |
| §0 的 `_fits` 调用点 | `:1190` / `:1196` | `:1192` / `:1198` |

`5cb73ff` 把 `decide` 里两处各加了一行，行号整体后移。插入 `_all_declared`（5 行）后，工作区里 `RIVALS` 分支在 `:554`、`def generate_rivals` 在 `:918`，`_fits` 的三个调用点变为 `:922` / `:1197` / `:1203`；协议 §2 末尾记了这层偏移，读者按哪个 commit 都能对上。

### §4 第三个回滚触发器（改写）

旧：「要让 block 生效需要再加第三个 `scripts/` 文件」——前提是两处接缝，已不成立。

新：「要让 block 生效需要对 `generate_rivals` 额外动刀」。declared 假设每次 `fold` 后已经在 rival 集合里，如果还要给那个函数布线，说明 `origin` 标记没承载信息，设计要重看。

## 2. `_all_declared`

插在 `live()` 之后（`scripts/rds_compass.py:197-199`）：

```python
def _all_declared(rows):
    """A declared set is not missing competitors; it is a set the caller asserted."""
    return bool(rows) and all(h.get('origin') == 'declared' for h in rows)
```

`RIVALS` 分支条件加 `and not _all_declared(rows)`：

```python
if rows and can_buy and not tried and observable in tolerances and not _all_declared(rows):
    return {'action': 'RIVALS', 'live': [h['id'] for h in rows]}
```

没有加 `tested` 判断，lone-survivor 分支（`:556`）和 `_fits` 都没动。

边界（`tests/test_stage2_hypotheses.py::test_all_declared_boundary`，实测通过）：

| 输入 | 结果 | 含义 |
| --- | --- | --- |
| `[]` | False | 空集不跳过；调用点的 `if rows and` 已经挡住空集，两者一致 |
| `[declared, rival-synthesis:fit]` | False | 混入任何非 declared 假设，枚举照旧 |
| `[declared, declared]` | True | 只有全 declared 才跳过 |

语义边界：declared 假设被 REFUTED 后离开 `live()`，此时 all-declared 不再成立（存活集变空或混入合成假设），枚举/逃逸路径恢复工作——下面第 4 节的 trace 是实测。

## 3. 测试与回归（原始输出）

新测试 `tests/test_stage2_hypotheses.py`（4 条）：

```
test_a_declared_campaign_never_reaches_the_synthesizer ... ok
test_a_refuted_declaration_reopens_enumeration ... ok
test_all_declared_boundary ... ok
test_the_same_campaign_without_the_block_still_enumerates ... ok

Ran 4 tests in 4.885s
OK
```

`tests/test_rds_compass_routing.py`（5 条）：

```
test_a_derived_intermediate_is_expanded_before_it_is_compiled ... ok
test_an_expression_without_derived_leaves_is_unchanged ... ok
test_a_crashed_jump_does_not_block_eig_routing ... ok
test_a_lone_survivor_gets_the_rival_offer_before_an_eig_jump ... ok
test_a_spent_jump_budget_sends_the_lone_survivor_to_confirmation_not_a_jump ... ok

Ran 5 tests in 1.914s
OK
```

没跑全量套件（`test_rds_compass_drive.py` 单模块 500s+，按 AGENTS.md 留给 release owner）。

复现：

```
python -B -m unittest discover -s tests -p 'test_stage2_hypotheses.py' -v
python -B -m unittest discover -s tests -p 'test_rds_compass_routing.py' -v
```

## 4. 实际行为（trace）

### 4.1 单 declared 正确假设（`sq: x*x+1`）

```
CONFIRM x0 -> CONFIRM x2 -> CONFIRM x1 -> VERIFY
status=GOAL_IDENTIFIED
hypotheses: {'sq': ('CONSISTENT', 'declared')}
jumps: []
```

4 步，`RIVALS` 一次都没有；把 `rds_compass_model.synthesize` patch 成抛 `AssertionError` 整条 campaign 照常跑完——core 断言成立。

### 4.2 单 declared 错误假设（`lin: x+1`，law `x*x+1`）

```
CONFIRM x0 -> CONFIRM x2 (lin 被反驳) -> PROBE x1 (ALL_REFUTED)
          -> ESCAPE (ALL_REFUTED，合成 j01-0 = x*x+1，origin=jump:j01:WHOLE_LAW)
          -> VERIFY
status=JUDGMENT_REQUIRED
hypotheses: {'lin': ('REFUTED', 'declared'), 'j01-0': ('CONSISTENT', 'jump:j01:WHOLE_LAW')}
```

`_fits` 在 lin 已 REFUTED 的状态下被调用（测试 3 用 spy 断言了调用时的状态表），即「断言死了，证据接管」。这条路径是 `escape` 的 `_fits(escalate=True)`，不是 `generate_rivals`——live 空时 RIVALS 分支本来就进不去，恢复枚举发生在逃逸里。

## 5. 顺带改的一条既有测试

`tests/test_rds_compass_routing.py::test_a_lone_survivor_gets_the_rival_offer_before_an_eig_jump` 原来用 `hypotheses=[('lin','x+1')]` 造 lone survivor，默认这一条会先走 `RIVALS` 再 `ESCAPE`。stage-2 后 declared 的 untested 假设不再触发枚举（这正是 seam 的语义），第一步直接变 `ESCAPE`，测试红。

处理：给它一个非 declared 的 survivor——`seed()` 直接往账本 append 一条 `origin='staged'` 的 `lin: x+1`，campaign 本身用 `hypotheses=()`。断言原样保留（`['RIVALS', 'ESCAPE']`，第二个 trigger 是 `LOW_MARGINAL_EIG`）。这不是放宽断言，是让这条测试不再依赖「declared 也要枚举」的旧前提；它要锁的顺序（rival 提议在 EIG 跳之前）没变，5 条全绿。

## 6. 诚实的限制

任务书假设「这个 seam 在现有 benchmark 上是 no-op」，**只对了一半**：

- `benchmark/newtonbench/devset_run.py:127` 是 `'hypotheses': []`，空声明 → 真 no-op。
- `benchmark/newtonbench/pareto.py:296` 和 `pareto_l2a.py:39` 是 `hypotheses=[('lin','x+1')]` → **不是 no-op**。每个 cell 都带一个 declared 假设。

实测一个 cell（`sparse-mid`，law `x+1 if x != 20 else 1000`，budget=8，其余照 `pareto.py:296-301`）：

| | 旧行为（`_all_declared` 恒 False） | 新行为 |
| --- | --- | --- |
| 步数 | 17 | 10 |
| 动作序列 | 8 × RIVALS + 7 × CONFIRM + 1 × EXPERIMENT + ESCAPE + VERIFY | 8 × CONFIRM + ESCAPE + VERIFY |
| 买的实验点 | x1, x30, x16, x9, x23, x5, x27, x20 | 同样 8 个点 |
| status / claim | `JUDGMENT_REQUIRED` / `CONSISTENT_BUT_UNCONFIRMED` | 相同 |

也就是说在 pareto 上这个 seam 删掉了每轮重枚举（每轮一次 `_fits` 合成），实验点集合和最终 claim 在这个 cell 上没变；但**全量 18 个 cell 没跑**，`benchmark/newtonbench/results/pareto.json` 里已提交的数字不能默认与新代码等价——尤其旧轨迹里 RIVALS 合成出的 rival 可能改变 `live()` 排序，进而改变 EIG 选点（本 cell 里所有 `r03-*` 都被反驳，所以没发生）。

「声明一个必错假设、再声明一个对的」这种双声明场景，在本实现下不会重新进入 `RIVALS`：只要还有一个 declared 假设活着，all-declared 仍成立，跳过枚举是设计语义（断言集合还剩一个，没有待枚举的竞争者），不是漏判。测试 3 因此用「唯一 declared 被反驳 → 存活集变空 → 逃逸合成」来验证恢复路径。

端到端收益（少合成、更直接的决策轨迹）要跑全量 pareto 对比才能量化，本次没做。

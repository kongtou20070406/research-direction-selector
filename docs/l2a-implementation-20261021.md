# L2-a 实现报告：LOW_MARGINAL_EIG 路由

- 日期 2026-10-21
- 分支 `release/6.0.0-compass`，基线 HEAD `0a091b9`
- 改动文件：`scripts/rds_compass.py`（唯一），`+30 / -0`
- 未 commit、未 push

## 0. 三行

本轮提升：EIG 枯竭时（ranked 为空，或只剩单条存活假设）把当前 wait 卖成一次 agent jump，而不是静默 fall through 到 RIVALS/CONFIRM 继续花钱买零信息。
可证伪信号：`docs/l2a-preregistration-20261021.md` 判定 1–8，规则写死未改。
绝对不允许：任何 cell 由 ✓ 变 ✗、新增假认证、`I.6.20a` 不再 `SEARCH_EXHAUSTED`。

## 1. 改动 diff

```diff
diff --git a/scripts/rds_compass.py b/scripts/rds_compass.py
index 1a3e4a7..b4e9be5 100644
--- a/scripts/rds_compass.py
+++ b/scripts/rds_compass.py
@@ -440,6 +440,28 @@ def _barren(state, jump):
     return disposition is not None and not any(o.get('admitted') for o in disposition['outcomes'])
 
 
+def _eig_routed_at(state, obs_count):
+    """True if LOW_MARGINAL_EIG already fired at this evidence count."""
+    return any(d['action'] == 'ESCAPE'
+               and (d.get('basin') or {}).get('trigger') == 'LOW_MARGINAL_EIG'
+               and d.get('observations') == obs_count
+               for d in state['decisions'])
+
+
+def _median_eig(state):
+    """Median eig_per_cost of past EXPERIMENT decisions, or None if no history."""
+    values = sorted(d['eig_per_cost'] for d in state['decisions']
+                    if d['action'] == 'EXPERIMENT' and d.get('eig_per_cost') is not None)
+    if not values:
+        return None
+    n = len(values)
+    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
+
+
+def _agent_live(state):
+    return bool(state['plan'].get('agent')) and agent_tokens(state)['status'] not in ('EXHAUSTED', 'USAGE_UNREPORTED')
+
+
 def decide(state):
     """Pure decision from the folded state; the caller records and executes it."""
     plan = state['plan']
@@ -502,7 +524,15 @@ def decide(state):
                     'partition_sha256': top['partition_sha256'],
                     'classes': top['classes'], 'alternatives': [r['id'] for r in ranking['ranked'][1:]],
                     'excluded': ranking['excluded'][:16], 'live': [h['id'] for h in rows]}
+        elif _agent_live(state) and not _eig_routed_at(state, len(state['observations'])):
+            return {'action': 'ESCAPE', 'basin': {**diagnosis, 'trigger': 'LOW_MARGINAL_EIG',
+                    'ranked_total': len(ranking.get('excluded', [])), 'excluded': ranking.get('excluded', [])[:16],
+                    'top_eig_per_cost': None, 'median_eig_per_cost': _median_eig(state)}}
     observed_count = len(state['observations'])
+    if len(rows) == 1 and can_buy and not diagnosis['trigger']:
+        if _agent_live(state) and not _eig_routed_at(state, observed_count):
+            return {'action': 'ESCAPE', 'basin': {**diagnosis, 'trigger': 'LOW_MARGINAL_EIG', 'ranked_total': 0,
+                    'excluded': [], 'top_eig_per_cost': None, 'median_eig_per_cost': _median_eig(state)}}
     tried = any(d['action'] == 'RIVALS' and d['observations'] == observed_count for d in state['decisions'])
     if rows and can_buy and not tried and observable in tolerances:
         return {'action': 'RIVALS', 'live': [h['id'] for h in rows]}
```

三处结构性选择：

- `_median_eig` 用 `sorted(...)` 而不是任务给的 `values.sort()`，语义相同，少一行临时变量。
- 触发点 A 按任务口述放在 `if ranking['ranked']:` 之后做 `elif`（预注册文档写的是「之前」用 if/elif，语义等价）。
- `_agent_live` 抽成函数而不是把条件内联两次，因为两处判据必须完全一致；它对齐的是 `escape()` 已有的 fail-closed 口径（见第 4 节）。

## 2. 新增函数与行号

| 位置 | 行号 | 作用 |
| --- | --- | --- |
| `_eig_routed_at(state, obs_count)` | `scripts/rds_compass.py:443` | 同一证据数上是否已经路由过，防重复烧 jump |
| `_median_eig(state)` | `scripts/rds_compass.py:451` | 历史 EXPERIMENT 的 `eig_per_cost` 中位数，无历史为 `None` |
| `_agent_live(state)` | `scripts/rds_compass.py:461-462` | agent 已声明且 token 预算未耗尽/未失联 |
| 触发点 A（rows ≥ 2、ranked 空） | `scripts/rds_compass.py:527-530` | `elif` 挂在 `if ranking['ranked']:` 之后 |
| 触发点 B（rows == 1 入口放宽） | `scripts/rds_compass.py:532-535` | 插在 `observed_count` 之后、`tried` 之前 |

`decide()` 现在从 `scripts/rds_compass.py:465` 开始。

## 3. 验证

### 3.1 LawTests（任务指定命令）

```
python -B -m unittest tests.test_rds_compass_drive.LawTests -v
Ran 13 tests in 102.361s
OK
```

13/13 通过，与改前基线（13 OK，100.135s）一致。

### 3.2 agent 相关 focused 类

改动放宽了 `rows == 1` 的入口，而 agent 相关的两组测试恰好用「单假设 + agent」构造，属于直接覆盖面，单独跑了一次：

```
python -B -m unittest tests.test_rds_compass_drive.JumpAgentTests tests.test_rds_compass_drive.TokenBudgetTests -v
Ran 6 tests in 41.116s
OK
```

没有跑全量套件。

### 3.3 定向验证（LawTests 覆盖不到新逻辑，必须补）

LawTests 的 13 条里没有任何一条的 plan 声明 `agent`，所以两条新分支在 LawTests 内部**不可能触发**。13/13 绿只证明没砸坏旧行为，不证明新逻辑对。为此单独写了一个一次性脚本（跑完已删，不入库），结果 **17/17 全通**：

```
PASS A live rows: 1
PASS A action: 'ESCAPE'
PASS A trigger: 'LOW_MARGINAL_EIG'
PASS A median without history: None
PASS A ranked_total: 0
PASS A2 second call falls through: True
PASS B live rows: 2
B ranked_total/excluded -> 9 9
PASS B action: 'ESCAPE'
PASS B trigger: 'LOW_MARGINAL_EIG'
PASS C no agent keeps RIVALS: 'RIVALS'
PASS D uncapped is CAP_UNKNOWN but callable: True
PASS D spent budget is not callable: False
PASS D unreported usage is not callable: False
PASS E no history: None
PASS E odd: 3.0
PASS E even: 1.5
F drive status SEARCH_EXHAUSTED | steps [('ESCAPE', 'LOW_MARGINAL_EIG'), ('EXPERIMENT', None), ('ESCAPE', 'LOW_MARGINAL_EIG'), ('RIVALS', None), ('EXPERIMENT', None), ('HANDOFF', 'ALL_REFUTED')]
PASS F trigger reached the drive log: True
SUMMARY 17 of 17
```

各段构造与含义：

- **A** `2*popcount(x)+1` + 单假设 `lin=x+1` + `agent='reject'`：`live()` 返回 1 条，`decide()` 直接返回 `ESCAPE / LOW_MARGINAL_EIG`，`median_eig_per_cost` 为 `None`，`ranked_total` 为 0。
- **A2** 在折叠状态上补一条 `observations` 相同的 `ESCAPE` 决策后，`decide()` 不再返回 ESCAPE，即同一证据数只路由一次。
- **B** `x*x+1` + 两条预测相同的假设 `[('a','x+1'),('b','x+1')]`：`live()` 2 条，`rank_experiments` 的 9 个候选全部进 `excluded`（`EQUIVALENT_PARTITION` 不买），`ranked` 为空 → 走触发点 A，`ranked_total=9`、`excluded` 9 条。
- **C** 单假设但 plan 不声明 agent：仍然走 `RIVALS`，旧行为原样。
- **D** `_agent_live` 三种 token 状态：未声明 cap 的 `CAP_UNKNOWN` 放行；`EXHAUSTED` 与 `USAGE_UNREPORTED` 都拒绝。
- **E** `_median_eig` 的 `None` / 奇数 / 偶数三种情形。
- **F** 真跑一遍 `drive()`：trigger 确实出现在步骤日志里（两次，对应两个不同的证据数），`escape()` 全路径没有新增异常，最终 `HANDOFF / ALL_REFUTED`。

## 4. 与任务的唯一偏差：`_agent_live` 没有用 `== 'AVAILABLE'`

任务原文的判据是 `state['budget'].get('agent_tokens') == 'AVAILABLE'`。这行是死代码，两条新分支永不触发：

1. `state['budget']` 由 `_budget()`（`scripts/rds_compass.py:43-47`）构造，SQL 是 `SELECT resource, cap, spent, charged, reserved FROM budget`，键是 plan 声明的 resource（`benchmark/newtonbench/devset_run.py:85` 声明的是 `wall_seconds / experiments / max_experiments`）。里面从来没有 `agent_tokens` 这个键，`.get()` 恒为 `None`。
2. `agent_tokens` 是**函数**（`scripts/rds_compass.py:916`），不是账本资源；它的 `status` 有四种取值，未声明 `max_tokens` 时是 `CAP_UNKNOWN`。
3. 即使写成 `agent_tokens(state)['status'] == 'AVAILABLE'`，也会错误挡掉 `CAP_UNKNOWN` 的 agent——而 `escape()` 的 fail-closed 只拒绝 `EXHAUSTED` / `USAGE_UNREPORTED`（`scripts/rds_compass.py:1072`），`tests/test_rds_compass_drive.py` 的 `TokenBudgetTests.test_an_undeclared_cap_is_unknown_and_spend_is_still_counted` 也断言 `CAP_UNKNOWN` 的 agent 会被调用（`(calls, tokens) == (2, ['CAP_UNKNOWN','CAP_UNKNOWN'])`）。fixture `tests/compass_fixture.py:69-72` 声明 agent 时同样只有 `argv` + `timeout_seconds`。

所以 `_agent_live` 采用与 `escape()` 完全相同的口径：agent 已声明，且 `status` 不是 `EXHAUSTED` / `USAGE_UNREPORTED`。

**如果要严格回退到字面语义**，只改 `scripts/rds_compass.py:462` 一行：

```python
return bool(state['plan'].get('agent')) and agent_tokens(state)['status'] == 'AVAILABLE'
```

代价是未声明 `max_tokens` 的 agent 永远不路由，也就等于把 L2-a 关死。

## 5. 两个阻断级发现：这次改动在 benchmark 上是空操作

这两条决定了预注册判定能不能真的被检验，必须单独写出来。

### 5.1 benchmark 的 campaign 不声明 agent

- `benchmark/newtonbench/devset_run.py:70-86` 的 declaration 没有 `agent` 字段。
- `benchmark/newtonbench/pareto.py:66-69` 调 `fx.law_campaign(...)` 也没传 `agent=`。

于是 `plan.get('agent')` 为 `None`，`_agent_live` 恒 False，两条新分支在 pareto 和 devset 上**一次都不会触发**。后果：

- 预注册判定 4（devset 中 `LOW_MARGINAL_EIG` 触发总次数 ≥ 1）不可达；
- 判定 1/2/3/8 会「PASS」，但那是空过——行为与改前逐字节相同，本来就不该变；
- 判定 5/6（触发后 6 jump 内出现非 `NONE_ADOPTED` 的 jump、触发后 5 步内无新 HYPOTHESIS 比例 ≤50%）没有数据源。

### 5.2 结果记录里没有 trigger，验收脚本也没实现判定 4/5/6

- `benchmark/newtonbench/devset_run.py:112-115` 的 row 只含 `equation / B / status / claim / law / counterexamples / claim_exact / experiments`，不记 `steps`，也不记 trigger。
- `benchmark/newtonbench/l2a_accept.py` 通读下来只实现了判定 1/2/3 加安全项（claim 翻转、`I.6.20a` 保持 `SEARCH_EXHAUSTED`），判定 4/5/6 在脚本里没有对应代码。

要真的跑预注册判定，需要一个独立 slice：给 benchmark 的 campaign 声明 agent、在 row 里落 trigger 序列、给 `l2a_accept.py` 补 4/5/6。这超出「只改 `scripts/rds_compass.py`」的范围，已上报，未自行扩大改动。

## 6. 踩到的问题

1. **`state['budget'].get('agent_tokens')` 是死代码**（详见第 4 节）。这是本轮最重要的一条：按字面实现会安静地交付一个永不触发的分支，而指定的 LawTests 依然是绿的，测试完全兜不住。
2. **定向验证脚本自己的 bug**：手工往 `state['decisions']` 塞假决策时漏了 `seq`，`_since_jump`（`scripts/rds_compass.py:284`）读 `d['seq']` 直接 `KeyError`。补 `'seq': 1` 后通过——记在这里是因为以后任何 mock 决策都要带 `seq`。
3. **行数卡线**：第一版 diff 是 +31 行，超了「净增 ≤30」。把 `_agent_live` 的 docstring 换成注释又换回来都不减行，最后删掉 docstring、把 fail-closed 的解释挪到本报告第 4 节，落到 +30。
4. **行为变化已知**：F 段那次 drive 显示单假设 + agent 的 campaign 现在开局先 `ESCAPE` 两次再回到 `RIVALS`。这正是 L2-a 想要的，但也意味着任何「单假设 + agent」的旧场景轨迹会变——`JumpAgentTests` / `TokenBudgetTests` 已确认不受影响（6/6），benchmark 因为不声明 agent 也不受影响。
5. 仓库没有 lint 配置（无 `setup.cfg` / `tox.ini` / `.flake8` / `pyproject.toml`），`scripts/rds_compass.py:462` 是 117 字符，未超常见的 120。

## 7. 本次没做的

- 没跑 `pareto.py` / `devset_run.py`：不在本次交付范围，且在 5.1 的前提下跑了也是空过，会给出误导性的 PASS。
- 没改 `rds_compass_drive.py`、`escape()`、`rank_experiments`：`drive.py:81` 直接把 `step['basin']` 传给 `escape()`，trigger 自然带过去，验证 F 段已证实。
- 没 commit、没 push。

## 8. 最小反向测试

把 `scripts/rds_compass.py:462` 的 `_agent_live` 改成恒返回 `False`（一行），重跑 devset 15 cells 应与改前逐字节一致。在当前 benchmark 下这条恒成立，因为 agent 从未声明；这也正好说明判定 8 现在不具备判别力——等 5.1 修好后它才有意义。

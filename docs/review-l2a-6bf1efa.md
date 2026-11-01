# 独立审查：L2-a / commit `6bf1efa`

审查对象：commit `6bf1efa336a50d7bda48387bc801cef7fc11cfc5` 的完整 diff，重点为 `scripts/rds_compass.py`。当前 HEAD 正是该 commit；写报告前工作区无未提交改动。

约束：只做静态读取和短命令核对；未运行 benchmark、测试套件或定向测试；未修改源码、未 commit、未 push。本报告是唯一新增文件。

## 结论

共发现 **5 条实现缺陷**：**阻断 1 条、严重 3 条、次要 1 条**。

1. **阻断**：两条 LOW_MARGINAL_EIG 路径绕过 `escape.max_jumps`，可生成第 `max_jumps + 1` 个及更多 jump。
2. **严重**：`rows == 1` 在证明 EIG 已枯竭之前抢占 `RIVALS`，包括零观测的 campaign 开局；每个新 observation count 都先额外消耗一次 agent/jump。
3. **严重**：`_eig_routed_at` 把“写过 ESCAPE 决策”当成“ESCAPE 已执行”，且只按 observation 数去重；崩溃窗口或同观测下 hypothesis/tool 状态变化后，合法重试会被永久压住。
4. **次要**：空 ranking 分支把 `ranked_total` 写成 excluded 数量，造成 `ranked` 为空但 `ranked_total > 0` 的自相矛盾记录，并把错误元数据发给 agent。
5. **严重**：本 commit 新增的验收脚本 fail-open；结果文件缺失时仍打印 `OVERALL: PASS` 并退出 0，且没有实现 L2-a 的触发/采纳验收项。

---

## 发现 1：LOW_MARGINAL_EIG 绕过 `escape.max_jumps`

**严重程度：阻断**

**位置**：

- `scripts/rds_compass.py:500-513`
- `scripts/rds_compass.py:527-535`
- `scripts/rds_compass.py:1042-1085`

**代码片段**：

```python
if diagnosis['trigger']:
    if len(state['jumps']) >= plan['escape']['max_jumps']:
        return {'action': 'HANDOFF', 'reason': 'SEARCH_EXHAUSTED', 'basin': diagnosis}
    ...
    return {'action': 'ESCAPE', 'basin': diagnosis}
```

但两个新增分支位于该块之后，直接返回：

```python
elif _agent_live(state) and not _eig_routed_at(state, len(state['observations'])):
    return {'action': 'ESCAPE', ...}
...
if len(rows) == 1 and can_buy and not diagnosis['trigger']:
    if _agent_live(state) and not _eig_routed_at(state, observed_count):
        return {'action': 'ESCAPE', ...}
```

`escape()` 自身从 `scripts/rds_compass.py:1042` 开始，不再检查 `max_jumps`，会直接用 `len(state['jumps']) + 1` 创建下一条 JUMP。

**为什么是问题**：现有 jump 上限只保护 `diagnosis['trigger']` 路径。新增 LOW_MARGINAL_EIG 路径的前提恰好是正常 basin trigger 不成立，因此永远不会经过现有上限检查。`_eig_routed_at` 只限制“同一 observation count 一次”；每新增一条观测便再次放行，与 jump 总数无关。

**影响面**：

- `escape.max_jumps = 0` 仍可发生 LOW_MARGINAL_EIG ESCAPE。
- `escape.max_jumps = N` 时，只要 observation count 继续增加，就能写入第 `N+1` 个甚至更多 JUMP。
- 破坏 `docs/compass.md:104` 声明的停止边界，也违反本 commit message 所称“sharing the escape.max_jumps budget”。
- agent token/外部调用次数失去声明的 jump 上界；后续 `SEARCH_EXHAUSTED` 语义不可靠。

**建议修法**：把 jump-cap 判定集中成所有 ESCAPE 共用的单一路由。构造 LOW_MARGINAL_EIG basin 后，先检查：

```python
if len(state['jumps']) >= plan['escape']['max_jumps']:
    return {'action': 'HANDOFF', 'reason': 'SEARCH_EXHAUSTED', 'basin': low_eig}
```

再允许 ESCAPE。更稳妥的是让 `decide()` 内所有 ESCAPE 都经过一个 helper，避免以后新增第三条路径再次漏掉上限。

---

## 发现 2：单假设分支在 EIG 枯竭得到证明前抢占正常推进

**严重程度：严重**

**位置**：

- `scripts/rds_compass.py:531-538`
- `scripts/rds_compass.py:682-683`
- `scripts/rds_compass.py:789-805`

**代码片段**：

```python
observed_count = len(state['observations'])
if len(rows) == 1 and can_buy and not diagnosis['trigger']:
    if _agent_live(state) and not _eig_routed_at(state, observed_count):
        return {'action': 'ESCAPE', ...}
tried = any(d['action'] == 'RIVALS' and d['observations'] == observed_count
            for d in state['decisions'])
if rows and can_buy and not tried and observable in tolerances:
    return {'action': 'RIVALS', ...}
```

当没有测量时，ESCAPE 内部调用的 `_fits()` 明确返回：

```python
if not rows or observable not in tolerances:
    return [], {'status': 'NO_MEASUREMENTS'}
```

**为什么是问题**：`len(rows) == 1` 只证明当前 ledger 中恰好有一个 live hypothesis，不证明“经过 rival synthesis/discrimination 后只剩一个存活假设”。在 agent 已声明、仍可购买实验的 campaign 中，这条分支位于 `tried` 计算和 `RIVALS` 之前，所以：

- 初始只声明一个 hypothesis、尚无任何观测时，也会先 ESCAPE；
- 每次 observation count 增加且仍只有一个 live hypothesis 时，又会先 ESCAPE；
- 机器侧 `generate_rivals()` 尚未在该证据状态尝试过，agent 却收到一个可能没有新证据的 PROPOSALS 请求。

它不会让后续每一次 `decide()` 永远都是 ESCAPE：同一 observation count 的第二次调用会被 `_eig_routed_at` 挡住并 fall through 到 RIVALS。但它确实在每个新证据计数的第一次调用上抢占正常推进，额外烧一次 jump/agent call。

**影响面**：所有“单初始假设 + agent + can_buy”的既有 campaign 轨迹都会改变；零证据 agent 调用缺少重新假设所需的反例上下文，还会提前消耗 jump/token 预算。在 max-jump 漏检修复后，这种过早调用又可能耗尽合法 jump 配额，使真正的后期 EIG 枯竭无法逃逸。

**建议修法**：先在当前 evidence state 执行一次 RIVALS，再把“仍为单 live hypothesis”视作结构性 EIG 为零。最小改法是把 `tried` 提前计算，并将单假设条件加上 `and tried`；或把该分支移到 RIVALS 分支之后，仅在同一 observation count 已完成 rival synthesis 时触发。若产品意图允许开局唤醒 agent，应另设明确 trigger，不能把“未尝试 rival synthesis”记录为 LOW_MARGINAL_EIG。

---

## 发现 3：`_eig_routed_at` 不是执行幂等键，会丢失合法重试

**严重程度：严重**

**位置**：

- `scripts/rds_compass.py:443-448`
- `scripts/rds_compass.py:621-628`
- `scripts/rds_compass_drive.py:79-82`

**代码片段**：

```python
def _eig_routed_at(state, obs_count):
    return any(d['action'] == 'ESCAPE'
               and (d.get('basin') or {}).get('trigger') == 'LOW_MARGINAL_EIG'
               and d.get('observations') == obs_count
               for d in state['decisions'])
```

Drive 的顺序是：

```python
elif action == 'ESCAPE':
    compass.record(store, state, step)
    result = compass.escape(store, compass.fold(store), step['basin'])
```

**为什么是问题**：去重函数只看 DECISION 和 observation 数。DECISION 在 `escape()` 执行之前落盘，因此存在确定的崩溃窗口：若进程在 `record()` 成功后、`escape()` 写 JUMP 前终止，重启后 `_eig_routed_at` 返回 True，但实际从未执行过 ESCAPE。该 evidence count 上的 LOW_MARGINAL_EIG 路由被永久压住。

即使 ESCAPE 已执行，`obs_count` 也不是完整状态身份。agent disposition 可新增 HYPOTHESIS，agent 提供的 tool 可在后续被 qualified；这些都会改变 live set / partitions，却不改变 observation 数。旧 DECISION 仍会压住新模型状态上的 LOW_MARGINAL_EIG。只有新增 OBSERVATION 才解除。

**影响面**：

- 外层 kill 可能静默丢失一次本应执行的 agent jump，违反 drive 文件头所称的可恢复语义。
- 同一证据上 agent 已改变 hypothesis/tool 空间后，新的 EIG 枯竭不能再次路由；若后续没有 CONFIRM/HOLDOUT/PROBE，控制流会提前到 VERIFY/HANDOFF。
- 好的一面是：同一 observation count 不会无限 ESCAPE。问题在于它通过过宽的抑制实现这一点。

**建议修法**：记录并比较一个 LOW_MARGINAL_EIG route fingerprint，而非裸 observation count。fingerprint 至少绑定 receipted observation identities、live hypothesis SHA256 集、qualified tool SHA256 集；DECISION 与对应 JUMP 都写入该键。只有匹配 fingerprint 的 JUMP 已落盘时才判定已路由。若还要避免 agent 重试循环，可在相同 fingerprint 的 JUMP/DISPOSITION 完成后阻断，并继续受 `max_jumps` 总上限约束。

---

## 发现 4：`ranked_total` 写入了 excluded 数量

**严重程度：次要**

**位置**：

- `scripts/rds_compass.py:527-530`
- `scripts/rds_compass_model.py:718-729`
- `scripts/rds_compass.py:820-848`

**代码片段**：

```python
elif _agent_live(state) ...:
    return {'action': 'ESCAPE', 'basin': {
        ...,
        'ranked_total': len(ranking.get('excluded', [])),
        'excluded': ranking.get('excluded', [])[:16],
    }}
```

而 ranking API 的定义是：

```python
return {'ranked': ranked[:limit], 'ranked_total': len(ranked),
        'excluded': excluded, 'selection_effect': 'ADVISORY'}
```

**为什么是问题**：进入新增 `elif` 的必要条件正是 `ranking['ranked']` 为空，所以正确的 `ranking['ranked_total']` 必为 0。实现却把 excluded 总数写进同名字段，产生例如“ranked 为空、ranked_total=9”的矛盾记录。

**影响面**：路由本身不受影响，但 basin 会被记录进 DECISION/JUMP，并作为 `packet(..., trigger, ...)` 的 trigger 发给 agent。错误字段会误导审计者和 agent，把“9 个候选全被排除”表达成“9 个候选进入 ranking”。

**建议修法**：写 `'ranked_total': ranking['ranked_total']`，另增 `'excluded_total': len(ranking['excluded'])`；`excluded` 继续保留前 16 条样本。

---

## 发现 5：新增验收脚本 fail-open，且未验收 L2-a 本身

**严重程度：严重**

**位置**：

- `benchmark/newtonbench/l2a_accept.py:17-21`
- `benchmark/newtonbench/l2a_accept.py:30-85`

**代码片段**：

```python
def load(name):
    path = RESULTS / name
    if not path.exists():
        print(f'MISSING: {name}')
        return None
...
ok = True
...
if pareto_new and pareto_old:
    ...
if devset_new and devset_old:
    ...
...
return 0 if ok else 1
```

**为什么是问题**：任一结果组缺失时，对应检查被整个跳过；四个文件全缺失时 `ok` 从未变成 False，脚本仍打印 `OVERALL: PASS` 并以 0 退出。即使结果齐全，脚本只核对 experiment/claim_exact/翻转/I.6.20a，没有实现预注册 `docs/l2a-preregistration-20261021.md:72-75` 的 LOW_MARGINAL_EIG 触发次数、jump 采纳和五步内新增 hypothesis 三项。

**影响面**：当前已知 benchmark 不声明 agent、L2-a 实际为空操作时，这个验收器仍可能给出成功状态；CI 或人工验收会把“未运行/未触发”误判为通过。

**建议修法**：`load()` 缺文件或 JSON 结构不符时立即把整体结果置为失败（或抛出并非零退出）；要求 old/new 的预期 cell/key 集完全一致；在结果中持久化 trigger、jump disposition 和 HYPOTHESIS 序列，然后实现预注册 4/5/6。未拿到这些字段时必须显式 FAIL/UNKNOWN，不能跳过。

---

## 指定核对项中已证伪的疑点

### `_median_eig` 并非恒为 `None`

- `scripts/rds_compass.py:522-526` 的普通 EIG `EXPERIMENT` 返回包含 `eig_per_cost`。
- `scripts/rds_compass_drive.py:70-72` 对 EXPERIMENT 调用通用 `record()`。
- `scripts/rds_compass.py:621-628` 的 `record()` 用 `{**deepcopy(step), ...}` 保存完整 step，没有剥掉 `eig_per_cost`。
- `scripts/rds_compass.py:83-84` 的 fold 同样完整恢复 DECISION value。

所以 `rds_compass_drive.py` 虽没有字面出现 `eig_per_cost`，该字段仍会原样进入 `state['decisions']`。只有历史上没有带 EIG 的 IDENTIFY_LAW EXPERIMENT 时，中位数才合理地为 `None`。`report()` 的压缩视图不输出该字段，不影响 fold 内部计算。

### `_agent_live` 的 `CAP_UNKNOWN` 口径与 `escape()` 一致

- `scripts/rds_compass.py:916-930`：未声明 `agent.max_tokens` 时状态为 `CAP_UNKNOWN`。
- `scripts/rds_compass.py:1071-1073`：既有 `escape()` 只屏蔽 `EXHAUSTED` 和 `USAGE_UNREPORTED`。
- `scripts/rds_compass.py:461-462`：新增 helper 使用相同的排除集合。
- `tests/test_rds_compass_drive.py:344-348` 还明确断言 uncapped agent 可调用两次、状态保持 `CAP_UNKNOWN`。

因此这里不存在新增路径与执行路径口径不一致。其产品语义是“cap 未声明则可调用，但预算状态保持 UNKNOWN”；若要改成未知即拒绝，必须同时修改既有 escape 语义和测试，不能只改 `_agent_live`。

### `basin.trigger` 覆盖不会造成 fold/replay 漂移

两个新增分支可达时，原 `diagnosis['trigger']` 必为假：多假设分支位于 `if diagnosis['trigger']:` 的提前返回之后，单假设分支又显式要求 `not diagnosis['trigger']`。所以 `{**diagnosis, 'trigger': 'LOW_MARGINAL_EIG'}` 只把 `None` 改成路由原因，没有抹掉另一个真实 basin trigger。

全仓消费点仅包括：`_eig_routed_at`、drive 步骤日志、`escape()` packet/JUMP、报告输出；`basin(state)` 始终从 observations/hypotheses/decisions 重新计算，不读取已记录 decision basin。未发现 fold 时重算导致的状态不一致。发现 4 的 `ranked_total` 错误是 basin 附加诊断字段问题，与 trigger 覆盖本身不同。

## UNKNOWN

无。上述结论均能由 commit diff 和当前源码的写入/折叠/消费链闭合证明；未借用 benchmark 或测试运行结果。

# 批 1a：19 处 external `_db` 借用点收窄 —— 执行与证据

分支 `release/6.0.0-compass`，基线 HEAD `26d3a59`。未 commit、未 push。

## 0. 一句话结论

19 处里 **18 处已消掉**，剩 `scripts/rds_workspace.py:562`（`binding_writer` 的 bootstrap 分支）属于「借连接给外部跑事务」而非「借连接调 helper」，不在本批能消的形态内。按协议第 1 节门（external delegates 19→0）判定：**门未全绿，先改**——决定这句话的是本文件第 6 节表里「external delegates」那一行。

## 1. 判定协议（照抄，未事后修改）

- 主指标：`block_kind_probe.py` 的 `external delegates` 计数。
- 门槛：19 → 0。external reads 67、external writes 17、in-class 24、injection 7 不许变。
- 判定由探测器脚本读源码自动给出，不靠人工判断。

## 2. 先记一件必须先说的事：批 1b 让探测器「看不见」了

任务开始时工作树里已有 41 个文件、76 处 `with X._db(True) as db:` 被改成 `with X.readonly() as db:`（未提交）。`scripts/rds_project.py` 同时新增了

```python
    def readonly(self):        # scripts/rds_project.py:690
        with self._db(True) as db:
            yield db
```

`block_kind_probe.py` 只数 `ast.With` 里 `context_expr` 是 `X._db(...)` 的块，`readonly()` **完全不被计数**。于是 external 从 103 掉到 28——但这是**改名隐形**，不是收窄：连接还在 store 外面流转，只是探测器的正则看不见了。协议第 3 节写明「改名/别名不算消掉」。

本批的做法因此是：**外部不再拿到连接对象，拿到的只是被读出来的值**，且新方法体仍用 `self._db(True)`（不是 `self.readonly()`），保证探测器继续数得到真实借出面。

真实借出面（可核）：HEAD 是 103 个 `_db` 块；批 1b 落盘后变成「28 个 `_db` 块 + 83 个 `readonly()` 块」；本批消掉 18 个 `readonly()` 块，现在剩 65 个。

```
$ grep -rn "\.readonly() as " scripts/ benchmark/ examples/ | wc -l
65
```

## 3. 新增的 8 个端口方法（`scripts/rds_project.py`）

只加了一个内部读作用域，其余是「名字换值」的薄封装：

| 行号 | 方法 | 作用域用法 |
| --- | --- | --- |
| 702 | `read_through(self, read, *args)` | `with self._db(True) as db: return read(db, *args)` —— **唯一新增的 in-class 块** |
| 716 | `contract(self)` | `read_through(self._contract)` |
| 720 | `run(self, run_id)` | `read_through(self._run, run_id)` |
| 724 | `runs(self)` | `read_through(self._runs)` |
| 728 | `contract_history(self)` | 延迟 import `rds_contract_read.contract_history` |
| 733 | `advisor_state(self)` | 延迟 import `rds_owned_advisor._state` |
| 738 | `proposal_events(self, kinds)` | 延迟 import `rds_autonomy_read._events` |
| 743 | `pending_partial_child(self, workspace)` | 延迟 import `rds_quick_ledger._pending_partial_child` |

`read_through` 是公开的：三个投影（`rds_continuation.resources`、`rds_tools._preparation_contract`、`rds_tool_workbench` 的候选校验）在 import 图里位于 `rds_project` 之上，由 `rds_project` 直接命名会**造出新 mutual 边**（实测 +3：`rds_continuation`、`rds_tool_workbench`、`rds_tools`），所以连接由 store 掌握、投影由调用方传入。

## 4. 19 处逐条

| # | 原位置 | 新端口 | 改后调用 |
| --- | --- | --- | --- |
| 1 | `benchmark/source_documents.py:124` | `advisor_state()` | `state = store.advisor_state()`（同时删掉第 76 行 import 里的 `_state`） |
| 2 | `scripts/rds_autonomy.py:373` | `proposal_events()` | `prepared = next((e for e in store.proposal_events(('AUTONOMY_PROPOSAL_VALIDATED',)) if e['run_id'] == rid), None)` |
| 3 | `scripts/rds_autonomy.py:518` | `contract()` | `contract = store.contract()` |
| 4 | `scripts/rds_autonomy.py:536` | `read_through()` | `result['handoff']['resources'] = store.read_through(resources)`（`resources` 已在第 532 行作用域） |
| 5 | `scripts/rds_autonomy.py:567` | `runs()` | `refreshed = store.runs()` |
| 6 | `scripts/rds_cli.py:1137` | `contract()` | `return store if 'advisor_policy' in store.contract() else None` |
| 7 | `scripts/rds_compass_drive.py:21` | `run()` | `run = store.run(run_id)` |
| 8 | `scripts/rds_host_hook.py:105` | `run()` | `run = store.run(request["run_id"])`，try/except ValueError → `_refuse` 保留，块内其余四个判断提到同层 |
| 9 | `scripts/rds_jump.py:33` | `contract_history()` | `history = store.contract_history()`（删掉局部 `from rds_method_revision import contract_history`） |
| 10 | `scripts/rds_owned_advisor.py:718` | `contract()` | `contract = store.contract()` |
| 11 | `scripts/rds_project_worker.py:19` | `run()` | `run = store.run(args.run_id)` |
| 12 | `scripts/rds_quick.py:458` | `contract()` | `parent_contract = parent.contract()`，其后纯计算与 `_bindings` 校验提同层 |
| 13 | `scripts/rds_quick.py:563` | `pending_partial_child()` | `partial_child = ProjectStore(owner).pending_partial_child(workspace)`（删掉 import 里的 `_pending_partial_child`） |
| 14 | `scripts/rds_tool_applicability.py:148` | `advisor_state()` | `state = store.advisor_state()`（同时删掉 import 里的 `_state`） |
| 15 | `scripts/rds_tool_workbench.py:79` | `read_through()` | 局部 `def verify(db):` 逐字搬原三条 `require`，`store.read_through(verify)` |
| 16 | `scripts/rds_tool_workbench.py:191` | `read_through()` | 局部 `def revise(db):` 逐字搬原四条（`deepcopy` + `_policy_change` 校验），`store.read_through(revise)` |
| 17 | `scripts/rds_tools.py:248` | `read_through()` | `store.read_through(lambda db: _preparation_contract(store, db))` |
| 18 | `scripts/rds_workspace.py:550` | `contract()` | `contract = store.contract()`，`identity` 字典提同层 |
| 19 | `scripts/rds_workspace.py:562` | **未消** | `binding_writer()` 里 `if bootstrap_owner is None: with store._db() as db: yield db; return` —— 它把**写连接**交给外部跑 `BEGIN IMMEDIATE` + 查/插 `WORKSPACE_BOUND` + 读写 pointer，不是「借连接调 helper」。要消它必须把 40 行事务逻辑搬进 `ProjectStore`，超出薄封装范围，且会动 `bind()` 的事务边界 |

## 5. 探测器原文（`python -B ../block_kind_probe.py .`）

任务给的 HEAD 基线：

```
  67  external   reads
  19  external   delegates
  17  external   writes
  10  in-class   reads
   8  in-class   delegates
   6  in-class   writes
   5  injection  delegates
   2  injection  reads
-- totals --
 103  external   in 41 files
  24  in-class   in 1 files
   7  injection  in 4 files
```

本批完成后：

```
  17  external   writes
  10  external   reads
   1  external   delegates
  10  in-class   reads
  10  in-class   delegates
   6  in-class   writes
-- totals --
  28  external   in 15 files
  26  in-class   in 1 files
```

读：`external delegates` 19 → 1，**未达标（要求 0）**；`external writes` 17 不变；`external reads` 67 → 10（**不是本批造成的**，是批 1b 把 57 个只读块改名成 `readonly()` 后探测器看不见它们）；`in-class` 24 → 26（`readonly()` + `read_through` 各一个块）；`injection` 栏消失（批 1b 也动过注入点，本批未碰 7 处 `def f(store, db=None)`，**此栏归零的原因标 UNKNOWN**）。

## 6. 门（`python -B tools/verify_slice_gates.py --fast`）

```
PASS  graph: cycles=4 mutual=40 upward=18 unresolved=1 max_scc=49 modules=177 (ceilings recorded at ce7a814)
SKIP  same tree: SKIPPED (no --map/--target)
SKIP  stale call sites: SKIPPED (no names given)

1 pass, 0 fail, 2 skipped
```

这行 PASS 是第二版才拿到的。第一版把 `resources` / `_preparation_contract` / workbench 校验都写成 `rds_project` 内的延迟 import，`tools/import_graph.py` 会连函数体内的 `Import`/`ImportFrom` 一起计入，于是新造出 3 条 mutual 边：

```
mutual rose 40 -> 43; upward rose 18 -> 19
  + ('rds_continuation', 'rds_project')
  + ('rds_project', 'rds_tool_workbench')
  + ('rds_project', 'rds_tools')
```

改成调用方传投影（`read_through(read)`）后回到 40/18，与 HEAD 一致。对比方法与数字见第 9 节。

## 7. 测试

```
$ python -B -m unittest discover -s tests -p 'test_import_graph.py'
Ran 97 tests in 11.716s
OK

$ python -B -m unittest discover -s tests -p 'test_rds_project*.py'
Ran 252 tests in 255.718s
FAILED (failures=1, skipped=2)
```

那次 1 failure 的完整原因是工作树在测试运行中途被外部清空，不是代码问题：

```
AttributeError: 'ProjectStore' object has no attribute 'readonly'
  File ".../scripts/rds_cli.py", line 1145, in _has_project_contract
```

清空与恢复的经过见第 8 节。恢复后重跑：

```
$ python -B -m unittest discover -s tests -p 'test_rds_project_read_port.py'
Ran 28 tests in 15.580s
OK
```

`Ran 252 tests` 那一行**没有在恢复后重跑**（单次 4 分钟以上，且与外部清空赛跑），因此「252 全绿」这一条标 **UNKNOWN**，只报 97 与 28 两行实测。

## 8. 环境事件：工作树被外部清空过一次

- 20:00:55 查 `git status --short`：只剩 `M docs/port-narrowing-protocol.md` 和三个我自己的临时文件；`scripts/`、`benchmark/`、`examples/` 与 HEAD 完全一致（`git diff --stat HEAD -- scripts/ benchmark/` 无输出）。
- 父目录同时出现了 `D:/Myfile/work space/RDS/readonly-move.patch`（57395 B，20:00:29），含 43 个 `diff --git`，内容正是「批 1b 的 76 处改名 + 本批的 8 个端口方法和 18 处调用点」（抽查：`+    def readonly(self):`、`+    def read_through(self, read, *args):`、`+    store.read_through(verify)` 都在）。
- 用 `git apply --check --exclude='docs/*' ../readonly-move.patch` 校验后原样恢复（docs 那一行当时已在工作树里，故排除），恢复后 `git status --short | wc -l` = 46。
- 结论：本批改动**没有丢失**，但工作树在 19:56–20:00 之间被外部清过一次，且 `docs/port-narrowing-protocol.md` 的 +1 行被保留、代码文件被清。这个动作不是我做的，具体是谁、为什么，标 **UNKNOWN**，请 release owner 确认。

## 9. 反向测试（协议第 3 节必做项）

备份 `scripts/rds_project.py` → `.bak` 副本（**没有用 `git checkout`**，因为工作树里是父 agent 未提交的成果），把 `contract()` 的方法体换成 `raise NotImplementedError('REVERSE-PROBE-1A')`，跑聚焦模块，再用副本恢复。

关掉后变红的具名测试与报错首行：

```
FAIL: test_hint_command_is_runnable_and_empty_root_message_unchanged
      (test_rds_cli_project_root_gate.ProjectRootGateTests)
  NotImplementedError: REVERSE-PROBE-1A

FAIL: test_all_invalid_prior_runs_reject_every_cli_entry_without_writing
      (test_rds_project_run_integrity.ProjectRunIntegrityTests) (body='malformed'/'array'/'null'/…)
  NotImplementedError: REVERSE-PROBE-1A
  栈顶链：rds_cli.py:1103 cmd_project → rds_cli.py:1137 _owned_project → rds_project.py:718 contract
```

恢复后同一条命令：

```
$ python -B -m unittest discover -s tests -p 'test_rds_cli_project_root_gate.py'
Ran 7 tests in 6.704s
OK
```

顺带一条实测：`test_rds_project_read_port.py`（28 tests）和 `test_rds_project_next.py`（7 tests）在 `contract()` 被换成 `raise` 后**仍然全绿**——它们不经过这 18 处调用点，所以本轮的反向覆盖只有 `test_rds_cli_project_root_gate.py` 与 `test_rds_project_run_integrity.py` 两处。其余 16 处（`advisor_state`、`runs`、`pending_partial_child`、`read_through` 等）**没有反向测试覆盖**，标 **UNKNOWN**。

## 10. 主张与证据表

| # | 主张 | 证据位置 | 状态 |
| --- | --- | --- | --- |
| 1 | 19 处里 18 处的外部借出点已消 | 第 4 节表 1–18 行 + `grep -c "def read_through" scripts/rds_project.py` = 1 | 成立 |
| 2 | 第 19 处（`rds_workspace.py:562`）未消 | 探测器 `1 external delegates`；文件原文仍是 `with store._db() as db: yield db` | 成立（未达标） |
| 3 | 门要求 external delegates = 0 未满足 | 第 5 节探测器原文 `1 external delegates` | 成立（门红） |
| 4 | external writes 17 不变 | 第 5 节两次输出 | 成立 |
| 5 | in-class 24 → 26 | 第 5 节两次输出；增量是 `readonly()`(690) 与 `read_through()`(702) | 成立 |
| 6 | 新增跨模块 import 未造出 mutual 边 | 第 6 节 PASS 行 `mutual=40 upward=18`，与 HEAD 一致 | 成立 |
| 7 | `test_import_graph.py` 全绿 | 第 7 节 `Ran 97 tests ... OK` | 成立 |
| 8 | `test_rds_project*.py` 252 条全绿 | 恢复后未重跑 | UNKNOWN |
| 9 | 反向测试有效 | 第 9 节两处 FAIL + 恢复后 `Ran 7 ... OK` | 成立（覆盖 2/18 处） |
| 10 | 批 1b 的 76 处改名使探测器 external 从 103 掉到 28 | 第 2 节；`readonly()` 不计入探测器 | 成立 |
| 11 | batch 1b 之后探测器 `readonly()` 块为 83 | 当前 65 + 本批删掉的 18 | 成立（按删块数倒推） |
| 12 | injection 栏 7 → 0 的原因 | 本批未碰 7 处 injection 定义 | UNKNOWN |
| 13 | 工作树在 19:56–20:00 被外部清空后从 `readonly-move.patch` 恢复 | 第 8 节 | 成立 |

## 11. OUT_OF_SCOPE_DISCOVERY

- `scripts/rds_workspace.py:562` —— `binding_writer()` 借写连接给外部跑 `BEGIN IMMEDIATE` + pointer 事务，是本批唯一没消的一处，需另案。
- `block_kind_probe.py` 不识别 `readonly()`（以及任何 `@contextmanager` 包装）——这是批 1b 后 external 从 103 掉到 28 的直接原因；建议下一版把「以 `yield db` 为体的 contextmanager 也算一种借出形态」，否则后续批次无法度量。
- 父目录 `readonly-move.patch` 与工作树清空的时间关系（20:00:29 生成 / 19:56 目录 mtime）说明有并发的 release-owner 操作，本文件只记录现象与恢复路径。

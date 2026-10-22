# 批 2 事务边界地图（port 收窄 · 只读探查产物）

- 生成时间：本次会话，仓库 `release/6.0.0-compass`，HEAD `41eb0bc`，工作区干净。
- 性质：**事实清单**。本文不改源码、不提设计提案、不把块级结论写成「应该可以」。
- 服务对象：`docs/port-narrowing-protocol.md` 的批 2 门槛判定，以及 §10.3「33 处写操作里哪些必须和读同事务」这句悬空论述。

## 0. 判定协议引用（本图受哪些条款约束）

| 条款 | 原文要点 | 对这张图的含义 |
| --- | --- | --- |
| §2 表格 | 批 2 = external writes 17，门槛至少 50% | 覆盖率的分母是 17，不是本文的 32/36 |
| §10.1 | `scripts/rds_workspace.py:562` 的 `binding_writer` 裁定 OUT_OF_SCOPE | 该块不进批 2 分母；但其调用点 `scripts/rds_workspace.py:579` 是真正的写入，必须单列 |
| §10.2 | 参考探测器 `tools/block_kind_probe.py` 的 85/26/7 | 本文的三栏要能逐条对回这三个数 |
| §10.3 | 「33 处写操作里哪些必须和读同事务」 | 本文第三节给出可复现的另三个口径；33 本身无法定位，见 §5 |
| §10.4 | 主指标：external writes 17 → ≤8，每处附具名写方法名 | 具名写方法名在本文表的「写方法」列 |

## 1. 口径与复现（先定口径，后看数字）

三个探测脚本，全部只读、只产出 JSON，放在 gitignored 的 `.rds/`：

| 脚本 | 口径 | 产物 |
| --- | --- | --- |
| `tools/block_kind_probe.py`（参考探测器） | 只认 `_db`/`readonly`/`read_through` 三个 manager 名；`receiver == 'self'` 且文件是 `scripts/rds_project.py` 才算 in-class | 85 external / 26 in-class / 7 injection |
| `.rds/probe_tx.py`（本图主口径） | **不按 manager 名过滤**：任何 `with` 块体内出现 DML 即算写块；嵌套 with 归最近一层；同时记 `order`（块内 read/write/begin 的出现次序） | `.rds/tx_blocks.json`：32 external + 6 in-class 写块 |
| `.rds/probe_helpers.py` | 块内调用的 helper 是否自身写；表 → 写块反查；调用方 grep 反查 | 终端输出 |

粗粒度存在性计数（防「探测器看不见」被读成「没有」）：

```
$ grep -rn "\.execute(" scripts/ benchmark/ examples/ --include=*.py | wc -l
492
$ grep -rn "with .*_db\|with .*readonly()\|with .*read_through(" scripts/ --include=*.py | wc -l
127
$ python -B .rds/probe_tx.py
execute_calls 494 / with_blocks 381 / files 290
exec_literal 293 / exec_fstring 4 / exec_name 1 / exec_concat 1 / exec_ifexp 1
verbs: select 153 / begin 71 / insert 41 / update 13 / create 8 / pragma 7 / none 7
external_writes 32 / in-class_writes 6
```

口径差异必须说清：参考探测器按**名字**抓（只能看见 `_db()`、`readonly()` 这类调用），本图按**DML 是否存在**抓（能看见 IfExp、变量、别名、独立 connect）。前者是收窄指标的分母，后者才是「全部写操作」。

## 2. 三栏计数汇总

| 口径 | reads | writes | delegates | 说明 |
| --- | --- | --- | --- | --- |
| 参考探测器 external | 67 | **17** | 1 | docs §10.2 的分母，本图逐条复核一致 |
| 参考探测器 in-class | 18 | **6** | 2 | 6 个全部复核一致 |
| 参考探测器 injection | 7 | 0 | 0 | 全为只读；无写操作漏进 injection |
| 本图（DML 为准）external | — | **30**（32 减去 1 假阳性、1 误判为 in-class） | — | +13 vs 17 |
| 本图（DML 为准）in-class | — | **7**（6 + 被误判的 `initialize`） | — | |
| 块外裸 DML（不在任何 with 块内） | — | **24 处执行点** | — | 事务在调用方，见 §4 |

写操作总数（可复现口径三个）：
- **块级 37** = 30 external + 7 in-class（其中**读写耦合 21 / 光写 16**，见 §6）。
- **执行点 61** = 37 块 + 24 裸 DML。
- **参考探测器 23** = 17 + 6。

## 3. 全写操作清单（external 30 + in-class 7）

列说明：**事务归属** 取值 = 自开 `BEGIN IMMEDIATE` / manager 层 / 调用方 / sqlite3 隐式（`with connect(...)` 或 `with _db(False)` 的 `with db:`）/ 无。**块内读** 取值 = 先读后写 / 写后读 / 光写。

### 3.1 external：借 ProjectStore 连接（docs 17 处，逐条复核一致）

| # | 位置 | 写方法 | 表 | 事务归属 | 块内读 | 调用方链 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `scripts/rds_autonomy.py:462` | `_claim` | budget, events | 自开(:463) | **写后读**(update@474→select@487→update@492) | `_claim`←`rds_autonomy.py:477`/`:542`、`rds_theory_progression.py:157` | SQL 藏在 `_append`(`:48`/`:475`/`:493`) |
| 2 | `scripts/rds_autonomy.py:711` | `drive` | budget | 自开(:712) | 光写 | `drive`←`benchmark/compass/run.py:37`/`:224` 等 | 同上 `_append@:715` |
| 3 | `scripts/rds_compass_store.py:121` | `append` | events | 自开(:122) | 光写 | — | COMPASS_* 记录，父摘要链 |
| 4 | `scripts/rds_math.py:139` | `put` | proofs, events | 自开(:147) | 先读后写 | — | create 表在 begin 之前(:140) |
| 5 | `scripts/rds_method_revision.py:179` | `apply` | events | 自开(:180) | 先读后写 | `apply`←`rds_autonomy.py:450`、`rds_cli.py:1069` | helper `replace@:219`/`append@:233`/`:246` |
| 6 | `scripts/rds_method_revision.py:274` | `apply` | events | 自开(:275) | 先读后写 | 同上 | |
| 7 | `scripts/rds_owned_advisor.py:431` | `_event` | events | 自开(:432) | 先读后写 | `_event`←`rds_campaign.py:140/187/545/779`、`rds_owned_advisor.py:690/761`、`rds_tool_workbench.py:81/249` | |
| 8 | `scripts/rds_owned_tools.py:243` | `record_consumption` | events | 自开(:244) | 先读后写 | ←`rds_owned_advisor.py:689` | |
| 9 | `scripts/rds_project_lifecycle.py:359` | `enable_advisor` | events | 隐式 | 先读后写 | ←`rds_cli.py:1021` | 块内首条是 `other`(:360) |
| 10 | `scripts/rds_quick.py:752` | `execute` | events | 隐式 | 光写 | ←`rds_autonomy_worker.py:225`、`rds_checkpoints.py:146`、`rds_cli.py:1825`、`rds_probe.py:336`、`rds_tools.py:294` | 17 处里**唯一块内无 `BEGIN`** 的写块 |
| 11 | `scripts/rds_steering.py:127` | `submit` | events | 自开(:128) | 先读后写 | ←`rds_cli.py:1038` | |
| 12 | `scripts/rds_structure.py:225` | `_meter` | budget, events | 自开(:226) | 先读后写 | ←`rds_structure.py:83/337/437/585/717/782/811` | |
| 13 | `scripts/rds_structure.py:261` | `_meter` | budget, events | 自开(:262) | 光写 | 同上 | 余额不足分支 |
| 14 | `scripts/rds_structure.py:273` | `recover_control` | budget, events | 自开(:274) | 光写 | ←`rds_cli.py:1845`、`tests/test_jump_control_budget.py:123/136/178` | |
| 15 | `scripts/rds_tms_store.py:65` | `save` | research_records +2 表 | 自开(:73) | 光写 | — | create 表在 begin 之前(:66) |
| 16 | `scripts/rds_tool_workbench.py:238` | `_prepare` | events | 自开(:239) | 先读后写(3 处) | — | |
| 17 | `scripts/rds_tools.py:170` | `_begin_preparation` | events | 自开(:171) | 先读后写 | — | |

### 3.2 external：写账本但参考探测器看不见（+13）

| # | 位置 | 写方法 | 表 | 事务归属 | 块内读 | 为什么看不见 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 18 | `scripts/rds_quick_ledger.py:34` | `_charge_ledger` | budget, events | **双形态**：`_parent_db is None` 时自开(:36)；否则复用调用方 | 先读后写(6 处) | `with (store._db() if _parent_db is None else nullcontext(_parent_db))` 是 **IfExp 不是 Call** | `:38` 断言 `db.in_transaction and ... == store.path`，报错文本 `Allowance publication requires its owning parent transaction`。←`rds_quick.py:568/603/739`、`rds_tools.py:222` |
| 19 | `scripts/rds_autonomy_worker.py:119` | `execute` | events | **无显式 BEGIN**；`:157` 显式 `db.commit()`，注释「Charge-owning run already RUNNING; delivery intent survives a crash」；退出时 `_AnchoredConnection.__exit__` 再触发 anchor 补写 | 先读后写(3 处) | 直接 `connect(root, root / '.rds/project.sqlite3')`，绕过 `ProjectStore` | ←`rds_autonomy_worker.py:225`、`rds_checkpoints.py:146`、`rds_cli.py:1825`、`rds_probe.py:336`、`rds_tools.py:294` |
| 20 | `scripts/rds_campaign.py:777` | `_bind` | events | **manager 层**：`_open_database` 在 `:111` `BEGIN IMMEDIATE`、`:114` `db.commit()` | 光写（读在 helper：`_lineage@778`/`_event@779`/`_quiescent@785`） | manager 名叫 `_database()`，不在参考探测器白名单 | helper `_event@:779` 自身也写 events |
| 21 | `scripts/rds_workspace.py:579` | `bind` | events | 自开(:582) | **写后读**(insert@587→select@604) | manager 是局部函数 `binding_writer()` | §10.1 只裁定了 `:562`（OUT_OF_SCOPE），`:579` 是真正写入 |
| 22 | `scripts/rds_cli.py:487` | `cmd_run` | receipts, artifacts | **调用方**：`rds.transaction()` = `RDSState._transaction`，BEGIN 在 `scripts/rds_state.py:102` | 光写 | manager 名叫 `rds.transaction()` | helper `artifact@491/492`(artifacts)、`event@503/507`(events) |
| 23 | `scripts/rds_quick.py:632` | `execute` | events | **调用方**：写的是 `locked_parents.values()` 里的父账本连接 | 先读后写(`SELECT body ... 'EXTERNAL_RUN_ALLOWANCE'`@638 → `SELECT 1 FROM sqlite_master`@734 → `INSERT`@735) | manager 是 `admission_context()`（准入上下文，不是连接 manager） | `:638` 的读是**真读**（校验父账本额度），不是存在性检查 |
| 24 | `examples/explanation-loop/worker.py:41` | `main` | events | 隐式 | 先读后写(3 处) | 示例代码，独立 sqlite | 是否算进批 2 分母需协议裁定（UNKNOWN） |
| 25 | `scripts/rds_advisor.py:427` | `ingest_document` | knowledge, metadata | 自开(:435) | **读写交替**(create→read→insert) | 独立库 `knowledge.db`，非 project ledger | ←`rds_cli.py:857` |
| 26 | `scripts/rds_proof_cache.py:34` | `ProofCache.__init__` | proofs | 无（`_write_admission()` 是文件锁，非 SQL 事务） | 写-only（`PRAGMA journal_mode=WAL`@38 是设置型 pragma，不计入 select） | 独立库 `proofs.db` | |
| 27 | `scripts/rds_proof_cache.py:95` | `_store` | proofs | 自开(:98) | 光写 | 同上 | ←`rds_math.py:59/90/137/163/164`、`rds_tms_store.py:33/53/109` |
| 28 | `scripts/rds_state.py:107` | `_transaction` | state | **调用方**：BEGIN 在 `:102`（with 块之外）、commit `:113`、rollback `:115` | 光写 | 遗留第二账本 `state.db`，manager 是 `mutation()` | ←`rds_state.py:95`；`rds_cli.py` 经 `rds.transaction()` 使用 |
| 29 | `scripts/rds_usage.py:151` | `_start` | tracking, calls | 隐式 | 光写 | 独立库 `usage.db` | |
| 30 | `scripts/rds_usage.py:217` | `_run_logged` | calls | 隐式 | 光写 | 同上 | |

### 3.3 in-class：`scripts/rds_project.py` 内部（7）

| # | 位置 | 写方法 | 表 | 事务归属 | 块内读 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 31 | `scripts/rds_project.py:488` | `_advisor_finished` | events | 隐式 | 光写 | ←`rds_project.py:1868`/`:1880`（在 `_finish` 内） |
| 32 | `scripts/rds_project.py:1150` | `initialize` | contract + 7 表 + 触发器 | 自开(:1164) | **读写交替** | **参考探测器误判成 external**：`writer = (self._db() if fresh_workspace is False else self._db(_materialization=...))` 是 IfExp/变量形态。含 `PRAGMA journal_mode=WAL`(:1151)、`executescript` 建表(:1152-1160)、`CREATE TRIGGER ..._no_update/_no_delete`(:1163) |
| 33 | `scripts/rds_project.py:1222` | `theory_allowance` | budget, events | 自开(:1223) | 先读后写(3 处) | ←`rds_advisor.py:125` |
| 34 | `scripts/rds_project.py:1262` | `theory_allowance` | budget, events | 自开(:1263) | 光写 | 早退路径 |
| 35 | `scripts/rds_project.py:1302` | `register` | runs, exposures, output_claims, predecessor, events, set | 自开(:1348) | **写后读**(insert@1377→select@1382→…) | helper `_campaign_deadline@:1389`(insert)、`bind_prepared@:1391`(insert)。←`rds_structure.py:677`、`rds_tools.py:402/406` |
| 36 | `scripts/rds_project.py:1631` | `_execute_admitted` | events | 隐式 | 光写 | ←`rds_project.py:1530` |
| 37 | `scripts/rds_project.py:1841` | `_finish` | runs, receipts, events | 自开(:1842) | 先读后写 | ←`rds_project.py:1473/1544/1607/1628/1651`；helper `_save@:1861` |

### 3.4 假阳性（1，必须从分母里剔掉）

| 位置 | 实际是什么 | 为什么误判 |
| --- | --- | --- |
| `benchmark/formal_performance.py:46` | `with tempfile.TemporaryDirectory():`，里面才有 `connect` + `UPDATE proofs`(:68) | manager 是临时目录，不是连接 manager |

## 4. 块外裸 DML（24 处执行点，事务全在调用方）

这些不在任何 `with` 块内，本图按「执行点」计数，不进块级三栏：

```
benchmark/capability/generate.py:218,228     scripts/rds_checkpoints.py:147,152
scripts/rds_outbox.py:217,223,264,267,290,298,305  (outbox_events/subscriptions/deliveries)
scripts/rds_autonomy.py:48  (_append)        scripts/rds_owned_tools.py:108,109
scripts/rds_postcommit_confirmation.py:81,174 scripts/rds_project.py:172(charge_tokens),569(_campaign_deadline),1424(_save)
scripts/rds_state.py:41,134,140              scripts/rds_structure.py:199
scripts/rds_workspace.py:729 (_anchor_transaction)
```

其中 `scripts/rds_workspace.py:729` 是隐藏写：`_AnchoredConnection.commit()`/`__exit__` 在 `db.total_changes > db.committed_changes` 时补一条 `WORKSPACE_PROGRESS` events 行，再 `_write_pointer`。**任何 `with connect(...) as db:` 提交时都会自动触发**，块级探测器看不出来。

## 5. 偏差清单（本文 vs docs）

### 5.1 external writes：17 → 本图 30（+13）

| 差额来源 | 数量 | 条目 |
| --- | --- | --- |
| IfExp / 变量形态 manager | 1 | #18 `scripts/rds_quick_ledger.py:34` |
| 直接 `connect(...)` 绕过 ProjectStore | 2 | #19 `scripts/rds_autonomy_worker.py:119`、#20 `scripts/rds_campaign.py:777` |
| 局部函数 manager / 非白名单 manager | 3 | #21 `scripts/rds_workspace.py:579`、#22 `scripts/rds_cli.py:487`、#23 `scripts/rds_quick.py:632` |
| 独立库（knowledge/proofs/state/usage/示例） | 7 | #24–#30 |

**注意**：第 4 类 7 条写的是独立库，不是 project ledger。若批 2 的分母严格限定「写 project ledger」，则它们是**口径外的 +7**，external 应为 23；若分母是「全部写操作」，才是 30。这条口径必须先裁定，否则后面算降幅会算错。

### 5.2 写操作总数：docs 33 vs 本图三个口径

| 口径 | 数 | 与 33 的关系 |
| --- | --- | --- |
| 参考探测器（17+6） | 23 | 差 10 |
| 本图块级（30+7） | 37 | 差 4 |
| 本图执行点（37+24） | 61 | 差 28 |

`grep -rn "\b33\b" docs/` 全仓只有 `docs/port-narrowing-protocol.md:178` 一处提到「33 处写操作」，没有任何计算过程、清单或脚本能复现这个数。**结论：33 的来源 UNKNOWN**，不能作为门槛分母使用。任何引用 33 的判定句都必须先标 UNKNOWN，或改用上表任一可复现口径并注明。

### 5.3 行号漂移（docs §10.3 列表 vs 实测）

`scripts/rds_owned_advisor.py:430` → 实测 **431**；`scripts/rds_owned_tools.py:242` → 实测 **243**；`scripts/rds_project_lifecycle.py:358` → 实测 **359**。docs §10.3 的 16 条列表本身也与「17」不符（少 1 条）。

## 6. 「哪些写块必须和读同事务」的初判（只列事实，不给方案）

判据（脚本 `.rds/probe_tx.py` 按块内 `order` 直接判定，非人工数）：块内出现 `select` 即在同事务内读；`PRAGMA` 不计入读。

```
$ python -B .rds/probe_tx.py  # 分栏输出
couple 21  write-only 16  total 37
Counter({'external': 18, 'in-class': 3})  # couple
Counter({'external': 13, 'in-class': 3})  # write-only
```

| 判定 | 数量 | 条目 |
| --- | --- | --- |
| **读写耦合（块内 select + DML）** | **21** | `examples/explanation-loop/worker.py:41`(读@42/45/47)、`rds_advisor.py:427`(读@438/439)、`rds_autonomy.py:462`(写@474→**读@487**→写@492)、`rds_autonomy_worker.py:119`(读@121/126/136)、`rds_math.py:139`(读@148/153)、`rds_method_revision.py:179`(读@183/200)、`rds_method_revision.py:274`(读@279)、`rds_owned_advisor.py:431`(读@433)、`rds_owned_tools.py:243`(读@251)、`rds_project_lifecycle.py:359`(读@365)、`rds_project.py:1150`(读@1165/1190)、`rds_project.py:1222`(读@1231/1232/1245)、`rds_project.py:1302`(读@1359/1368/**1382**)、`rds_project.py:1841`(读@1844)、`rds_quick.py:632`(读@638/734)、`rds_quick_ledger.py:34`(读@49/50/70/127/145/157)、`rds_steering.py:127`(读@131)、`rds_structure.py:225`(读@234/246)、`rds_tool_workbench.py:238`(读@259/265/274)、`rds_tools.py:170`(读@180)、`rds_workspace.py:579`(读@583/**604**) |
| **光写（块内无 select）** | **16** | `rds_autonomy.py:711`、`rds_compass_store.py:121`、`rds_quick.py:752`、`rds_structure.py:261`、`rds_structure.py:273`、`rds_tms_store.py:65`、`rds_campaign.py:777`、`rds_cli.py:487`、`rds_proof_cache.py:34`、`rds_proof_cache.py:95`、`rds_state.py:107`、`rds_usage.py:151`、`rds_usage.py:217`、`rds_project.py:488`、`rds_project.py:1262`、`rds_project.py:1631` |
| 假阳性（不计入） | 1 | `benchmark/formal_performance.py:46` |

三条需要单独盯的**真·写后读**（先写再读自己刚写的行，最不能拆）：
- `scripts/rds_autonomy.py:462`：`UPDATE budget@474` → `SELECT@487` → `UPDATE@492`。
- `scripts/rds_project.py:1302`：`INSERT@1377` → `SELECT@1382` → `INSERT@1388` → `UPDATE@1393` → `INSERT@1396`。
- `scripts/rds_workspace.py:579`：`INSERT@587` → `SELECT MAX(id)@604`。

**限制**：这一栏只说明「读和写在同一个块内」，没有做「读的结果是否真被写消费」的数据流分析。`rds_quick.py:632` 的 `SELECT@638` 读的是父账本的 `EXTERNAL_RUN_ALLOWANCE`，`rds_workspace.py:579` 的 `SELECT@604` 读的是自己刚插入的行 —— 这两处是确定的耦合，其余需要逐块 def-use 才能定性。本栏是**初判**，不是终判。

## 7. 盲区与假绿风险（必须先声明，否则数字会被误读）

1. **IfExp / 变量 / 别名 manager 会隐身**：参考探测器只认调用名，`store._db() if X else nullcontext(Y)`、`writer = self._db()`、`binding_writer()` 三种形态它都看不见。本图能看见，但也只看见「块内有 DML」这一层。
2. **helper 里的 SQL 不在块内**：`_append`/`_event`/`_save`/`_campaign_deadline` 这类 helper 的 DML 在块外定义，块内只能看到调用。本图对 6 组做了 helper 反查（见 §3 备注），但**不是全量**。
3. **anchor 隐藏写不可见**：`_AnchoredConnection.__exit__` 的 `WORKSPACE_PROGRESS` 补写在源码层面属于 `scripts/rds_workspace.py:729`，块级探测器只能把它算成一次裸 DML，看不到它由谁触发。
4. **`mutation()` 不是 SQL 事务**：`scripts/rds_mutation.py:179-211` 只有进程内锁 + flock，没有 BEGIN。把 `with mutation():` 当成事务边界是本批最容易犯的口径错误。
5. **`with db:` 是 sqlite3 隐式事务**：`scripts/rds_project.py:777`、`scripts/rds_workspace.py` 的 `connect` 分支都靠它 commit/rollback。所以「块内没有 BEGIN」不等于「没有事务」。
6. **`rds_quick_ledger.py:34` 是双形态**：`_parent_db is None` 时自开事务，否则复用调用方并断言 `db.in_transaction`。任何「统一收窄」都会撞上 `:38` 的断言。

## 8. 复现命令

```bash
python -B tools/block_kind_probe.py .            # 参考探测器，85/26/7
python -B tools/block_kind_probe.py . --detail   # 17 个 external writes 原文
python -B .rds/probe_tx.py                       # 本图主口径，落 .rds/tx_blocks.json
python -B .rds/probe_helpers.py                  # helper/表/调用方反查
grep -rn "\.execute(" scripts/ benchmark/ examples/ --include=*.py | wc -l   # 494
```

## 9. 一句话结论

external writes 实测 **30**（严格限定 project ledger 时 **23**），写操作总数按块级 **37**、按执行点 **61**；docs 的 **17** 只覆盖借 `ProjectStore._db` 且 manager 名精确匹配的那一类，docs 的 **33** 没有任何可复现来源。**「可以提交批 2 判定」= 先改**：分母口径（是否含独立库/示例）与 33 的出处都还是 UNKNOWN，见 §5.1 与 §5.2。

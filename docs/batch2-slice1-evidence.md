# 批 2 第一刀 · 证据表（slice 1）

协议：[docs/batch2-slice1-protocol.md](batch2-slice1-protocol.md)（预注册，未修改）。
基线代码：`git worktree add --detach .head-check HEAD` = `4b0ee77`。两侧使用同一条探测器命令
（`python -B tools/block_kind_probe.py <repo>`）与同一批 focused 测试命令。

## 0. 环境偏差（先声明）

- 协议称 `docs/batch2-slice1-protocol.md` 已提交；实测 `git status` 里它是未跟踪文件（`??`）。内容按任务书读取，未修改。
- 作业树 HEAD = `4b0ee77`，分支 `release/6.0.0-compass`，与任务书一致。

## 1. 主指标

| 口径 | 基线（HEAD 4b0ee77） | 本刀后（工作区） | 判定 |
| --- | --- | --- | --- |
| 隐身写块 external（协议分母 23 = 17 可见 + 6） | 23 | 17 | **6/6 收敛（本刀收满）** |
| in-class 写块 | 7 | 13 | +6 迁移 |
| reads（external / in-class / injection） | 67 / 18 / 7 | 67 / 18 / 7 | 一致 |
| delegates（external / in-class） | 1 / 2 | 1 / 2 | 一致 |
| hidden 写块总数 | 7（6 目标 + `rds_project.py:1150 initialize`） | 7 行全为 `-> in-class`（6 目标已收敛；`initialize` 自带类内 DML，仍按块身份标行，不属本刀） | 6/6 收敛 |

口径注：任务书投影「20w→17w / 10w→13w」的起点 20/10 是前三块（#19/#20/#21）落地后的中间态（23−3 / 7+3）；本表两列一律从基线 4b0ee77 起算。

命令：基线 `python -B tools/block_kind_probe.py "D:/Myfile/work space/RDS/.head-check"`；
本刀后 `python -B tools/block_kind_probe.py . --baseline=.rds/baseline_hidden.json`。
基线输出存档 `.rds/probe_v2_baseline.txt`，HEAD 隐藏块清单存档 `.rds/baseline_hidden.json`。

DML 全量反查（同命令，`== DML reverse check ==` 段）：块级 37 → 32（-5 = 6 块 direct DML 全部移入具名方法，其中 1 块的块内 UPDATE 残留使该块仍带行）；
`inside with blocks` 62 → 56（-6 迁移写点），`bare` 24 → 26（+2 = `ProjectStore.record_event` 与 `RDSState.record_receipt` 两个方法体的 execute），
总执行点 86 → 82（= −6 迁移 +2 方法体，账目可加；#18 的 UPDATE 行两侧同字留块内，不在这三笔账里）。每一处变化都能指到具体语句，无第三类漂移。

## 2. 六块逐块状态

| # | 块 | 主张 | 证据位置 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | 探测器 v2 | v2 基线复现地图 §2：37 块 / 6 隐身 / reads 67+18+7 / delegates 1+2 | `.rds/probe_v2_baseline.txt` hidden 7 行 + merged 段 | 成立 |
| 2 | #18 `scripts/rds_quick_ledger.py:34` `_charge_ledger` | 收敛为具名写方法且事务边界逐字不变 | `scripts/rds_quick_ledger.py:160`（工作区行号）→ `ProjectStore.record_event(db, event)`；:159 的 `UPDATE budget SET charged=charged+?` 原位不动。`tests/test_rds_quick_ledger_service.py` 的 OLD_AST 哈希 `4d882fdd…` **未变**：`private_reads`（:44）在 41eb0bc 先例下扩了第二个 elif，把 `ProjectStore.record_event(db, X)` 逐字展开回 `db.execute('INSERT INTO events(body) VALUES (?)', (canonical(X),))`，UPDATE 行两侧同字；注释写明沿 65d45a5 原体锚点 | 成立 |
| 3 | #19 `scripts/rds_autonomy_worker.py:120` `execute` | 写块收敛，事务边界（外层 `mutation()`、内层 `with connect(...)`、`db.commit()`）逐字不变 | `scripts/rds_autonomy_worker.py:157-158` → `ProjectStore.record_event(db, intent)`；`:117-118` 新增函数内 import；探测器 in-class | 成立 |
| 4 | #20 `scripts/rds_campaign.py:777` `_bind` | 写块收敛，`_database(write=True)` 的 BEGIN/commit 不动 | `scripts/rds_campaign.py:792` → `store.record_event(db, value)`；探测器 in-class | 成立 |
| 5 | #21 `scripts/rds_workspace.py:579` `bind` | 写块收敛，`binding_writer()` 的 `BEGIN IMMEDIATE` 与写后读 `SELECT MAX(id)` 不动 | `scripts/rds_workspace.py:587-588` → `store.record_event(db, {...identity})`；写完仍读 `MAX(id)`（同文件 :604） | 成立 |
| 6 | #22 `scripts/rds_cli.py:487` `cmd_run` | 收敛为具名写方法且事务边界逐字不变 | `scripts/rds_cli.py:505` → `RDSState.record_receipt(db, run_id, receipt["sha256"], canonical(receipt))`；新方法 = yield 该二元组连接的类上 `scripts/rds_state.py:137-141` `@staticmethod record_receipt(db, run_id, sha256, body)`，方法体逐字原 SQL（含无列名 `INSERT INTO receipts VALUES (?,?,?)`）；:99-102 `_transaction` 的 BEGIN IMMEDIATE/commit/`mutation()` 逐字不动。`tests/test_rds_reference_services.py` OLD_AST["RDSState"] 整类哈希按机制更新 `46914434…`→`90028755…`（真变了，41eb0bc 原体锚点已写进注释），staticmethod 元组 +`record_receipt` | 成立 |
| 7 | #23 `scripts/rds_quick.py:632` `execute` | 写点收敛 | `scripts/rds_quick.py:735-737` → `ProjectStore.record_event(parent_db, {kind/job_root/request_sha256/materialization_state})`（dict 原样）；父账本连接来源 :522 `locks.enter_context(parent._db())`，:527 BEGIN IMMEDIATE 不动；:734 SELECT 守卫不动；`ProjectStore` 为模块级 import（rds_quick.py:18）。无 AST pin：全 tests grep `frozen_ast` 只命中 ledger_service / reference_services / result_record 三文件，均不钉 rds_quick.execute | 成立 |
| 8 | 三栏交叉验证 | reads/delegates 与基线一致，writes 只允许 6 行 external→in-class | 见 §1 表 | 成立 |
| 9 | 反向测试 | 两侧同一条命令跑完 | 见 §3 | 成立 |

和 = 6（6 成立 + 0 UNKNOWN），协议 §4「6/6 全收」达成；前两刀的 #19/#20/#21 行（3-5）不动。

## 3. 反向测试（worktree = HEAD 回退侧）

| 模块 | 本刀后（工作区） | 基线（.head-check @ 4b0ee77） |
| --- | --- | --- |
| `test_rds_workspace.py` | 33 OK | 33 OK |
| `test_rds_campaign_native.py` | 10 OK | 10 OK |
| `test_rds_autonomy.py` | 31 OK | 31 OK |
| `test_rds_project.py` | 85 OK (skip 2) | 85 OK (skip 2) |
| `test_rds_workspace_admission.py` | 19 OK | — |
| `test_rds_quick_ledger_service.py`（pin，A 块 focused） | 5 OK | 5 OK |
| `test_rds_result_record.py`（pin） | 4 OK | — |
| `test_rds_reference_services.py`（pin，B 块 focused） | 18 OK | 18 OK |
| `test_rds_quick_checkpoint_recovery.py`（C 块 focused） | 9 OK | 9 OK |

诚实读出：这些模块在两侧**同绿**，说明它们不区分「内联 SQL」与「具名写方法」两种形态。
因此它们只证明「本次改造没有破坏既有行为」，形态收窄的判定由 §1 的探测器三栏担保，不由这些测试担保。
本刀三行同此（基线侧 = `git stash push -- scripts tests` 回退后跑同一脚本；A 的 OLD_AST 哈希不变、B 的整类哈希按机制更新，都走 §2 行 2/6 的 pin 注记）。

## 4. 落点形态

新增 `ProjectStore.record_event(db, event)`（`scripts/rds_project.py:1421-1430`）：静态方法，只执行
`db.execute('INSERT INTO events(body) VALUES (?)', (canonical(event),))`，不 open / commit / rollback / 加锁。
模板沿用同文件已存在的 `ProjectStore._save(db, run)`（`scripts/rds_project.py:1433`）。
三个调用点分别用 `ProjectStore.record_event`（无 store 实例的 worker）与 `store.record_event`（已有实例的 campaign / workspace）。

本刀三块同形：#18/#23 → 同一个 `ProjectStore.record_event`；#22 的落点 = yield 该二元组连接的那个类本体 `RDSState`：
`scripts/rds_state.py:137-141` 新增 `@staticmethod record_receipt(db, run_id, sha256, body)`，方法体逐字原 SQL（无列名 INSERT），
同样只执行、不 open / commit / 加锁。

`canonical` 字节一致性已核对：`scripts/rds_project.py:82`、`scripts/rds_core.py:6`（经 `rds_project_values` 转发）、
`scripts/rds_autonomy_worker.py:20` 三处实现参数完全一致（`sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False`），
故事件 body 字节不变。

## 5. 改动量

`git diff --stat`（工作区全量，含前两刀）：11 个修改文件 +345/−73，其中 `tools/block_kind_probe.py` +303 净增（协议 §1 要求升级的探测器：`load_baseline` 返回 (rel,func,with序号)→verbs 的 dict，`baseline={}` + `migrated` 判据按块身份翻转为 `-> in-class`）；其余为 8 个产品代码文件（rds_project / rds_autonomy_worker / rds_campaign / rds_workspace / rds_quick_ledger / rds_state / rds_cli / rds_quick 各 2-8 行级改动）与 5 个 tests 文件。

## 6. 结论

6/6 隐身写块全部收敛进具名写方法（`ProjectStore.record_event` ×5、`RDSState.record_receipt` ×1），六处事务边界逐字不动，reads/delegates 栏与基线逐字一致；#18 的 OLD_AST 哈希靠 41eb0bc 的 `private_reads` 机制扩展保住（哈希不变），#22 的整类哈希按同一机制更新并在注释写明依据。
**可以提交**：决定这句话的是 §1 表（23→17 / 7→13，三栏对齐）与 §2 六行全「成立」；未 commit/push，等上级复核。

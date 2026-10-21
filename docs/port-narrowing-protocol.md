# 判定协议：收窄 `ProjectStore._db` 的借出面

先写，后跑，不得事后修改。任何一条门槛要改，先改本文件并说明理由，再重跑。

## 0. 要解决的问题

`ProjectStore._db(...)` 是一个上下文管理器，把 sqlite 连接借给调用方。41 个
`scripts/` 文件里有 103 处业务代码通过它拿连接，其中 48 处直接写 SQL。结果是
schema 变更无法靠检查调用点定位，事务边界分散在 41 个文件里，`rds_project.py`
的公开面被这 103 处钉死。

不是要删掉 `_db`。`def events(store, db=None)` 那种「调用方传事务就复用，不传
就自己借」的注入模式是对的，本协议不动它（今天 7 处，记为 `injection`）。

## 1. 主指标

**`external` 块数**，定义：位于 `scripts/`、`benchmark/`、`examples/` 下、接收者不是
`self`（在 `rds_project.py` 内）、且所在函数签名不接受 `db=None` 的
`with X._db(...)` 块。

今天实测 **103 块 / 41 文件**。复现：

```
python -B <outside-repo>/block_kind_probe.py .
```

分类脚本必须同时打印 `external` / `in-class` / `injection` 三栏，任何一栏消失即
视为口径漂移，本次判定作废。

分母不是 103：分母是「本批开工时实测的 `external` 块数」，每批各测一次。不允许
用上一批的数字推算本批。

## 2. 分批与门槛

按改动的难易，不是按文件大小：

| 批 | 内容 | 今天块数 | 门槛 |
| --- | --- | --- | --- |
| 1a | 只借连接调 helper（块内无 SQL 字面量） | 19 | 全部消掉 |
| 1b | 只读（块内只有 select / pragma / begin） | 67 | 至少 60% |
| 2 | 写账本（insert / update / delete / create / drop / alter） | 17 | 至少 50%，且必须逐处写出语义方法名 |

门槛用相对比例：1a 是「全消」，1b 和 2 是「至少六成/五成」。绝对块数只作为进度
记录，不作为通过条件。

**`begin` 算读。** 它开一个事务，不改任何一行。第一版协议把 `begin select` 算成了
写，于是报出「48 写 / 36 只读」，实际是 17 写 / 67 只读。这个错误把批 2 的工作量
放大了近三倍，也把批 1b 的收益说小了。

批 1a 和 1b 可以并行（不同文件集时）；批 2 必须在 1a/1b 全绿之后开工，因为端口
形状在那一批里才定型。

## 3. 什么叫「消掉」

一层薄封装把 `with store._db(True) as db: x = store._contract(db)` 改成
`store.contract()`，块数减少 1，算消掉。

把 `_db` 改个名字，或者给它加个 `@property` 别名，块数不变，不算消掉。

把块挪进 `rds_project.py` 变成 `in-class`，`external` 减少但总借出面没变，不算
消掉。判定脚本要同时看三栏，就是为了挡这个。

## 4. 安全门（每批都必须全过）

1. `python -B -m unittest discover -s tests -p 'test_rds_project*.py'` 全绿（今天 15 个文件）
2. `python -B -m unittest discover -s tests -p 'test_import_graph.py'` 全绿（今天 97 tests）
3. `python -B tools/verify_slice_gates.py --fast` 无 FAIL（依赖图不劣化）
4. `crg` 图 `head_matches_build == true` 且 `unclassified modules` 为空
5. `external` 块数严格下降

门 1 和门 2 是行为门，门 3 和门 4 是结构门。行为门绿而结构门红，判「先改」；
结构门绿而行为门红，判「回滚」。

## 5. 反向测试

每批至少一条：把某个新端口方法删掉（或在里面 `raise NotImplementedError`），
**必须**有一个具名的既有测试变红。找不到这样的测试，说明这批改的地方没有覆盖，
在证据表里记 `NO_COVERING_TEST` 并补一条，不许用「其他测试还是绿的」代替。

反向测试的 patch 必须用 `git diff > patch` 存档、`git checkout --` 回退、跑同一
条命令、`git apply` 恢复。不许造夹具伪造反向。

## 6. 回滚触发器

- 门 1 或门 2 出现任何 FAIL
- `external` 块数上升
- 需要改 `_execute_admitted` 的事务边界
- 需要改已经通过类名调用 `ProjectStore._role_sha` / `_receipt` / `_runs` / `_contract`
  的任何方法的签名（67 处类名调用）
- 需要让新端口模块 `import rds_project`（成环）

任何一条成立，`git checkout -- scripts/ tests/ tools/` 并记录原因。

## 7. 非目标

- 不引入第二账本、第二调度器、第二 TMS
- 不改 SQL schema、回执字段、准入阈值
- 不改 `_db` 的返回契约（`readonly` 语义、`settlement` 参数、`mutation()` 包裹）
- 不在搬动时顺手重写业务逻辑。语义方法必须是原块的逐字搬运加一个 `with`
- 不动 `def f(store, db=None)` 注入模式的 7 处
- 不做性能优化

## 8. 判定由脚本给出

每批结束时跑：

```
python -B tools/verify_slice_gates.py --fast
python -B <outside-repo>/block_kind_probe.py .
python -B -m unittest discover -s tests -p 'test_rds_project*.py'
```

三份输出贴进证据表，状态列由输出决定，不靠人读代码判断。任何一项没跑，记
`UNKNOWN`，不记作通过。

## 9. 主张与证据表

| # | 主张 | 证据位置 | 状态 |
| --- | --- | --- | --- |
| 1 | `external` 块今天是 103，分布在 41 个文件 | `block_kind_probe.py` 输出 | 成立 |
| 2 | 15 块曾被误算为债，实为连接注入 | `scripts/rds_compass_store.py:84-88`、`scripts/rds_structure.py:151-154` | 成立 |
| 3 | `ProjectStore` 是全仓 betweenness 第一的桥 | crg `get_bridge_nodes_tool` 0.016172 | 成立 |
| 4 | `_db` 的 hub degree 是 366，排第 10 | crg `get_hub_nodes_tool` | 成立 |
| 5 | 批 1a 能全消 | 未跑 | UNKNOWN |
| 6 | 批 1b 能消掉六成 | 未跑 | UNKNOWN |
| 7 | 批 2 能消掉五成 | 未跑 | UNKNOWN |
| 8 | `begin select` 是事务性读，不是写 | `block_kind_probe.py` 修正后 17 写 / 67 读 | 成立（第一版协议算错） |

后续每批一行，状态只填「成立 / 误判 / UNKNOWN」。

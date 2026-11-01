# 批 2 第一刀（6 隐身块）判定协议 — 预注册，先写后跑，不得事后修改

注册日期：2026-10-21。依据：`docs/port-narrowing-protocol.md` §10.3/§10.4（4b0ee77 修订版）、
`docs/batch2-tx-boundary-map.md` §3.2（b9）。本刀只处理 6 个隐身块；17 个探测器可见块不动。

## 1. 范围（6 块，逐块）

| # | 块 | 隐身形态 | 具名写方法落点（原块借谁的连接，方法就进哪个类） |
|---|---|---|---|
| 18 | `scripts/rds_quick_ledger.py:34` `_charge_ledger` | IfExp 双形态（`_parent_db is None` 自开，否则 nullcontext 复用） | 其所属 ledger 类；`:38` 的 `in_transaction` 断言语义必须原样保留 |
| 19 | `scripts/rds_autonomy_worker.py:119` | 直连 `connect(root, '.rds/project.sqlite3')` 绕过 ProjectStore；`:157` 显式 commit | 按其读的表进对应 store 类；若语义要求绕过 store，判 OUT_OF_SCOPE 并给证据 |
| 20 | `scripts/rds_campaign.py:777` `_database()` | manager 名不在白名单；BEGIN@111/commit@114 在 manager 层 | campaign store 类 |
| 21 | `scripts/rds_workspace.py:579` | 局部函数 manager，自开 BEGIN IMMEDIATE | 其所属 store 类（562 `binding_writer` 维持 §10.1 OUT_OF_SCOPE，不在本刀） |
| 22 | `scripts/rds_cli.py:487` `rds.transaction()` | 调用方层 manager，BEGIN 在 `rds_state.py:102` | state/store 类 |
| 23 | `scripts/rds_quick.py:632` | 局部函数 manager | quick 服务类 |

任何一块若改造会改变事务边界（谁开 BEGIN、谁 commit、是否复用父事务），停手记 UNKNOWN，不硬改。

## 2. 主指标与对照

- **主指标**：隐身形态写块 6 → 0。判定由升级后的探测器自动给出，不靠人工。
- **探测器升级（前置，先于任何源码改动完成）**：`tools/block_kind_probe.py` v2 必须同时认
  旧形态（`_db(True)` 字面）与新形态（IfExp、变量 manager、直连 connect、非白名单 manager、
  `readonly()` 系），且按 DML 全量反查给出独立计数（块级 37 / 执行点 61，与地图 §2 一致）
  做交叉验证。探测器先跑基线并保存输出。
- **分母**：23 = 17（探测器可见）+ 6（本刀）。独立库 7 与示例不在口径内。
- **门槛（相对比例）**：本刀 6/6 全收或如实记 OUT_OF_SCOPE+UNKNOWN（两者之和 = 6）；
  三栏交叉验证——reads 栏与 delegates 栏数字必须与基线一致（一个都不能少），
  writes 栏只允许这 6 行从 external 变 in-class。

## 3. 每块验收（逐块三件套）

1. focused 测试按被改符号反查确定（1–2 个模块，不跑全量；已知 pin：
   `tests/test_rds_quick_ledger_service.py` 按字节钉 AST——按 41eb0bc 已落地的规范形态处置，
   不自创第二套 pin）。
2. **反向测试**：`git diff > /tmp/slice1.patch` → `git checkout -- <file>` → 同一探测器
   v2 + 同一 focused 测试跑基线 → `git apply` 恢复 → 复跑。两侧同一条命令。
3. 每块结论进证据表：file:line 或命令输出原文；证据不足写 UNKNOWN，不写「应该」。

## 4. 状态表骨架（执行者逐行填状态，不得改表结构）

| # | 主张 | 证据位置 | 状态 |
|---|---|---|---|
| 1 | 探测器 v2 基线输出 = 地图 §2 口径（37 块/23 ledger/6 隐身） | | |
| 2–7 | #18–#23 各自已收敛为具名写方法且事务边界逐字不变 | | |
| 8 | reads/delegates 栏与基线一致 | | |
| 9 | 反向测试全部完成且两侧同命令 | | |

# C1：token 进 budget 表的设计（2026-10-20）

状态：**设计稿，未实现**。本文件不改任何 `.py`；所有行号对应当前 `HEAD 5c3666d`（`release/6.0.0-compass`）。

目标：让 agent token 从「agent 自报的数字」变成「账本里的一等预算维度」，使得
(a) 「每正确解成本超标自动降级」有可读的成本轴；
(b) EIG 的分母含 token；
(c) 全程不破 Single Authoritative Ledger 与 Fail-Closed Budgets。

一句话结论：**不改 budget 表结构也能记账**（budget 已是多维度表，加一行 `resource` 即可），改结构只为了「一次跳转里 input/output/cached 分列」。建议先做零结构变更的第一阶段，把分列留作可选的第二阶段。

---

## 1. 现状：token 现在怎么流转，哪些环节丢了

### 1.1 流转链（全部已核实）

| 环节 | 位置 | 做了什么 |
| --- | --- | --- |
| agent 输出 | `benchmark/compass/codex_jump.py:108-109` | 从 Codex 事件流取 usage，失败时也打印 |
| 解析 | `scripts/rds_compass.py:829-838` `_usage(stdout)` | 只读 stdout JSON 顶层 `usage`；`input_tokens`/`output_tokens` 必需，`cached_input_tokens` 可选；每个值须 `type(v) is int and 0 <= v < 10**12`；不合法 → `None` |
| 失败分支 | `scripts/rds_compass.py:903-922` `_call_agent` | 超时 → `'usage': None`；进程没起来 → `{'input_tokens': 0, 'output_tokens': 0}` |
| 落 events 表 | `scripts/rds_compass.py:1096-1099` `escape()`（STALE 分支在 `:1062-1065`） | `cstore.append(store, 'DISPOSITION', jump_id, {..., 'agent': evidence, ...})` |
| 汇总 | `scripts/rds_compass.py:886-900` `agent_tokens(state)` | 扫全部 `dispositions` 的 `agent.usage`，`spent = Σ(input_tokens + output_tokens)` |
| 门控 | `scripts/rds_compass.py:1041-1043` | `EXHAUSTED`/`USAGE_UNREPORTED` → 本次跳转不调 agent |
| 展示 | `packet()` `:811-814`、`workset` `:1395`、`report` `:1529-1537` | `budget.agent_tokens{cap, spent, remaining}` / `report.cost.agent_tokens` |

### 1.2 账本现状

`budget` 表（`scripts/rds_project.py:1060-1067`）：

```sql
CREATE TABLE IF NOT EXISTS budget(
  resource TEXT PRIMARY KEY,
  cap REAL NOT NULL,
  spent REAL NOT NULL DEFAULT 0,
  charged REAL NOT NULL DEFAULT 0,
  reserved REAL NOT NULL DEFAULT 0);
```

三件关键事实：

1. **表本来就是多维度的**。行集合完全由 `contract['budget']` 决定（`rds_project.py:1088` 的 `executemany` 按 contract 的键插行）；校验只强制 `wall_seconds` 存在，其它 resource 名 ≤64 字符、值须为 number（`rds_project.py:1018-1022`）。
2. **结算路径是通用的**。`rds_project.py:1755-1762` 按 `resources` 字典逐 resource 做 `reserved -= est; spent += est; charged += est`，`_check_start`（`rds_project.py:502-507`）统一做 `reserved + 1e-9 >= amount` 与 `spent+charged+reserved <= cap+1e-9` 检查。加一个 resource 名不需要新代码。
3. **Compass 只往 contract 里写 `wall_seconds`**（`scripts/rds_compass_declaration.py:322`），实验 run 的 `resource_estimates` 也只有 wall（`scripts/rds_compass.py:608`）。所以 token 从来没进过 budget 表。

### 1.3 丢了什么

1. **token 不进 budget 表**。`spent_measured` 里没有 token 轴，外部只能从 `report.cost.agent_tokens` 读一个派生数，读不到 `cap/spent/remaining`。`ProjectStore.snapshot()`（`rds_project.py:1807-1814`）看不到 token。
2. **cached 被读出来但不进账**。`_usage` 收 `cached_input_tokens`，`spent` 求和只算 `input + output`。缓存命中的输入按原价算，实际成本被系统性高估，而且高估多少没人知道。
3. **按跳转分不清归属**。`agent_tokens` 是全局 `Σ`，一次 jump 花了多少 token 只能自己去解析 `DISPOSITION` 的 `agent.usage`，且 STALE 跳转的消耗在 `agent_tokens` 里算了、却没有对应的实验推进——「每正确解成本」的分母端于是含了废跳转。
4. **无 cap 时不 fail-closed**。`CAP_UNKNOWN` 照常调用（`docs/compass.md:112` 明写）。花出去的钱无人管账，只是列进 `unknown`。
5. **超冲无记账**。cap 在调用间检查，单次可超（`docs/compass.md:114`），于是 `spent` 可以 > `cap`，而 budget 表的 `_check_start` 不允许 `spent+charged+reserved > cap+1e-9`——两套口径在同一个数上会打架。
6. **RSI 完全没碰成本**。`scripts/rds_compass_rsi.py` 里没有 `cost`/`token`/`agent` 任何字样。RSI 触发的 Synthesis 工作不进任何账。
7. **quick owned 路径显式拒绝非 wall**。`scripts/rds_quick_ledger.py:57`、`scripts/rds_owned_tools.py:23-24` 都 `require(set(contract['budget']) == {'wall_seconds'}, ...)`。token 加进 contract 后这两处会**直接报错**，见 §6。

---

## 2. schema 改动

分两阶段。**阶段 A 不动表结构**，只加 resource 行与声明字段；**阶段 B** 才给 budget 表加列。理由是 A 已经能满足「每正确解成本」和 EIG 分母，B 提供的 input/output 分列对那两个目标不是必需的，但 B 会连带改读端口 4 处与 5 个旧测试（§6）。

### 2.1 阶段 A：resource 行（推荐先做）

**contract 增加 budget 维度。** `scripts/rds_compass_declaration.py:322` 的 budget 从

```python
'budget': {'wall_seconds': budget['wall_seconds']}
```

变成（说明性伪代码，不实现）：

```python
'budget': {'wall_seconds': budget['wall_seconds'],
           'agent_tokens': budget['agent_tokens']}   # 仅当 plan.agent.max_tokens 声明时
```

`budget` 表里因此多一行：

| resource | cap | spent | charged | reserved |
| --- | --- | --- | --- | --- |
| `agent_tokens` | （见下） | 0 | 0 | 0 |

**cap 怎么定——这是整个设计里唯一的真选择，三条路必须明写口径**：

- `agent.max_tokens` 已声明且为 int `N` → `cap = N`，`unit = 'tokens'`；
- 已声明但 `agent` 存在 `max_tokens` 缺省（现在的 `CAP_UNKNOWN`）→ **FAIL-CLOSED：拒绝创建 contract**，报 `agent.max_tokens is required once the token ledger is enabled; token spend is never left uncapped`。这是行为变更，旧声明会从「能跑」变成「拒绝创建」，所以**必须由一个显式开关打开**（见 2.4），默认关。
- 没有 agent → 不插 `agent_tokens` 行（无维度 = 无承诺，不是 cap=0）。注意这与「cap=0 的行会立刻 `Insufficient`」不同，后者会让无 agent 的 campaign 一步都走不了。

**谁写 spent。** token 消耗发生在 Compass 的 `_call_agent` 之后，不在 `ProjectStore` 的 run 结算里，所以不能走 `:1755-1762` 那条路。建议新增一条**同库同事务**的记账入口（实现时由主实现者决定放在 `rds_project.py` 还是 `rds_compass_store.py`，本设计只定语义）：

```
charge_tokens(db, *, jump_id, usage, charge, now) ->
    require(db.in_transaction)
    row = SELECT resource, cap, spent, charged, reserved FROM budget WHERE resource='agent_tokens'
    require(row is not None, 'Token ledger row missing; the contract never declared agent_tokens')
    amount = charge(usage)                     # 见 §4 口径
    require(amount >= 0)                       # 负数即篡改
    # 不在这里做 cap 检查：超冲是合法状态（单次调用可超），
    # 检查发生在下一次购买之前（§2.3 的 fail-closed 门）。
    db.execute("UPDATE budget SET spent=spent+?, charged=charged+? WHERE resource='agent_tokens'", (amount, amount))
```

关键点：**`spent` 与 `charged` 同步增加**。预算经济文档（`docs/budget-economy.md`）里 `spent` = 实测、`charged` = 估算；token 只有一个来源（agent 自报），没有「实测 vs 估算」二分解，两个都加，让 `snapshot().remaining` 与 EIG 用同一个数。

**不引入第二本账。** `events` 里的 `DISPOSITION.agent.usage` 保留为**原始证据**，budget 表是它的**汇总投影**。两者由同一次事务写入，且必须满足下面的等式（§5 第一条验收就是查它）：

```
budget('agent_tokens').spent == Σ over events(kind='DISPOSITION') of g(record['agent']['usage'])
```

只落一处而不落另一处，等于第二本账。

### 2.2 阶段 B：budget 表加列（可选，有结构代价）

若要 input/output/cached 分列：

```sql
ALTER TABLE budget ADD COLUMN unit TEXT NOT NULL DEFAULT '';
ALTER TABLE budget ADD COLUMN components TEXT NOT NULL DEFAULT '{}';  -- JSON: {"input_tokens":...,"output_tokens":...,"cached_input_tokens":...}
```

选 JSON 列而不是 `input_tokens`/`output_tokens`/`cached_tokens` 三列的理由：`_projection` 与 `BudgetRow` 是固定列平铺（`scripts/rds_project_read_port.py:42-47`、`:101-102`、`:206` 的 `(self.__row_bytes,)*5`），每加一列要动三处代码加一个长度元组；一个 `components` 列只动一次，且天然容纳「以后 agent 报出 `reasoning_tokens`」。

代价必须写在这：`components` 是 JSON，**不能**作为比较阈值（SQLite 里没有 `<=` 语义），所有 cap 判断必须读行为预算的 `spent` 标量。`unit` 列补上现在靠 `resource.endswith('_seconds')` 硬猜的语义（`rds_project.py:1811-1812`），`agent_tokens` 显式写 `'tokens'`。

**表结构变更的落地点（5 处），一处不改就是 runtime 崩：**

| 文件:行 | 现状 | 要改什么 |
| --- | --- | --- |
| `scripts/rds_project.py:1060-1067` | `CREATE TABLE IF NOT EXISTS budget(...)` | 加两列；旧库由 §3 的幂等升级补 |
| `scripts/rds_project_read_port.py:42-47` | `BudgetRow` 五个字段 | 加 `unit`、`components` |
| `scripts/rds_project_read_port.py:101-102` | `_BUDGET` 列元组 | 列名对齐 |
| `scripts/rds_project_read_port.py:206` | `(self.__row_bytes,)*5` | 长度必须与列数一致，否则 `_read` 参数错位（静默错，不是异常） |
| `scripts/rds_project.py:1807-1814` | `snapshot()` 投影 | 加 `unit`/`components` 或至少保留 `unit` |

### 2.3 events 表：不新增 kind，只加两个**事件内**标记

`KINDS` 已是闭合集合（`scripts/rds_compass_store.py`），加 kind 要改 frozenset 并影响所有按 kind 统计的地方。token 的两个新语义都能塞进现有 kind 的 record 体：

1. **超冲标记**。在 `DISPOSITION` 的 `agent` 里加 `'overrun': True`（当 `spent_after > cap` 时）与 `'spent_after'`。理由：cap 只在调用间检查，单次可超，需要可审计的痕迹；不加 kind 就能让 `docs/compass.md` 里那句话变成可查的。
2. **消耗归属**。`DISPOSITION` 已经按 `jump_id` 做 `ident`，所以「哪次跳转花了多少」不需要新字段——`agent.usage` 就在那条记录里。但**废跳转**（STALE_RESPONSE、未被采纳的 PROPOSALS 空转）需要能被下游排除，否则「每正确解成本」的分母含废跳转。建议在 `DISPOSITION` 的 `agent` 里加 `'outcome': 'ADOPTED' | 'STALE_RESPONSE' | 'NO_ITEMS' | 'AGENT_TIMEOUT' | 'NO_RESPONSE'`，与现有 `escape()` 的返回值一致，避免下游再解析一遍状态字符串。

两项都是**向后兼容的新增键**（旧记录没有，读侧一律当未知，不当 0）。

### 2.4 declaration 新增字段

`scripts/rds_compass_declaration.py` 的 `agent` 对象只加键、不加顶层块，减少对 `_declaration()` 与 canonical hash 的冲击面：

```jsonc
"agent": {
  "argv": [...],
  "timeout_seconds": 600,
  "max_tokens": 2000000,
  "token_ledger": {                 // 新增，可缺省
    "charge": "input_plus_output",  // 计费口径，见 §4
    "cached_input_discount": 1.0    // ∈ (0,1]，cached 输入按此比例折价；1.0 即与普通输入同价
  }
}
```

- `charge` 的取值必须是个**闭集合**，建议 `{"input_plus_output", "output_only"}`：前者是现状口径，后者用于「输入免费」的推理定价。
- 缺省 `token_ledger` 视作 `{"charge": "input_plus_output", "cached_input_discount": 1.0}`——**这不违反 fail-closed**，因为这两个参数是「怎么算」而不是「给多少额度」；额度仍由 `max_tokens` 决定，而 `max_tokens` 缺省在开关打开时是硬拒绝（§2.1）。
- **开关**：建议放在 `CLI`/环境层而不是 declaration 内部（declaration 是冻结产物，跨臂比对不宜混入部署开关）。具体位置留给主实现者，但文档里必须写死一条：**开关默认关，打开后 `agent.max_tokens` 缺省即拒绝创建**。

### 2.5 schema_version 怎么 bump

现状：**整个仓库没有 budget 表级 schema_version**（`schema_version` 只出现在各自协议对象上，如 `rds_admission_read.py:31`、`rds_advancement.py:81-82`，均为 `== 1` 硬校验）。Compass 侧只有 `SCHEMA = 'rds-compass-declaration-v1'`（`rds_compass_declaration.py:21`）与 `PLAN_SCHEMA`/`WORKSET_SCHEMA`/`PACKET_SCHEMA`/`REPORT_SCHEMA`（`rds_compass.py:25-27`）。

bump 规则，按「改了什么」分档，**不做全局版本号**：

| 改动 | 版本动作 | 理由 |
| --- | --- | --- |
| 阶段 A（只加 resource 行 + `agent.token_ledger`） | **不 bump** `rds-compass-declaration-v1`。新增键缺省即旧语义，旧声明在开关关闭下行为逐字节不变 | 声明 schema 的版本号代表「读法变了」，这里读法没变 |
| 阶段 A + 开关打开且 `max_tokens` 缺省改为硬拒绝 | **bump 到 `rds-compass-declaration-v2`** | 这是「旧声明会被拒」的行为变更，必须让读者一眼看见 |
| 阶段 B（budget 表加列） | **bump 账本版本**：contract 里加 `"ledger_schema": 2`（1 = 旧）。写入时 `require(ledger_schema == 2)`，读到 1 走 §3 的升级 | 表结构变了，读法也变了 |

`ledger_schema` 放在 contract 里而不是 PRAGMA：PRAGMA 用户可改且不可审计，contract 是不可变、有 SHA256 的冻结产物。账本自身的完整性仍由现有 hash chain 与 `contract` 表承担。

---

## 3. 兼容性：旧账本怎么迁移

**总原则：不重写历史行。** 旧 `budget` 行上 `unit=''`、`components='{}'`（阶段 B），是「未知」而不是 0。

### 3.1 表结构升级（阶段 B）

升级必须在 `ProjectStore` 打开账本、且 `BEGIN IMMEDIATE` 持有写锁的事务里做，**幂等**：

```
PRAGMA table_info(budget) -> 列名集合
if 'unit' not in cols or 'components' not in cols:
    require(contract.ledger_schema == 1, ...)   # v2 contract 配 v1 表 = 半升级，拒绝
    ALTER TABLE budget ADD COLUMN unit TEXT NOT NULL DEFAULT ''
    ALTER TABLE budget ADD COLUMN components TEXT NOT NULL DEFAULT '{}'
    UPDATE contract SET ledger_schema=2 ...      # 或 contract 的 method revision
```

- `ALTER TABLE ADD COLUMN` 在 SQLite 里是 O(1)（只改元数据），锁窗口毫秒级。
- `budget` 表**没有** append-only trigger（`rds_project.py:1060-1067` 附近只有 contract/receipts/exposures/events 有），所以 ALTER 不会被 trigger 挡。
- 升级失败必须整体回滚，账本保持 v1 可读——**不允许出现「表是 v2、contract 说 v1」的中间态**。

### 3.2 旧 campaign 缺 `agent_tokens` 行：fail-closed 还是默认 0？

**默认 0 是错的，fail-closed 只有一半对。** 分开看两个问题：

1. **「这个旧账本花了多少 token」** —— **答：UNKNOWN，不是 0**。旧账本里 `DISPOSITION.agent.usage` 是有的（这个字段从 `_usage` 上线就在），所以可以**部分重建**：`Σ(input+output)`。但 `cached_input_tokens` 在旧记录里有（`_usage` 一直收），而「agent 到底按什么价付」在旧账本里从未记录 → 折价后的真实成本 UNKNOWN。
   因此迁移后应写 `spent = Σ(input+output)`（可重建部分）并把 `components.cached_input_tokens` 一并重建，但 **`unit` 标 `'tokens'`，`provenance` 标 `'RECONSTRUCTED_FROM_EVENTS'`**，明确它不是记账产物。凡读 token 成本的消费者必须看 `provenance`，`RECONSTRUCTED_*` 不进「每正确解成本」的正式口径。
2. **「旧 campaign 能不能继续买 agent 调用」** —— **答：不可以，fail-closed**。旧 contract 没有 `agent_tokens` 行，就没有 cap。此时任何「token 已花 X，还剩 cap−X」的说法都需要一个凭空来的 cap。按 AGENTS.md 第 3 条，**未知预算不许猜**：读侧遇到「有 agent 声明、无 `agent_tokens` 行」应报 `Token budget UNKNOWN for this campaign; it was created before the token ledger` 并且**不许续跑 agent 调用**。

一句话：**历史消耗可重建（标 `RECONSTRUCTED`），未来承诺必须 fail-closed（不续跑）。** 混成一句「默认 0」会把旧 campaign 变成无限额度。

### 3.3 旧 declaration 的读法

- 开关关闭：`agent.token_ledger` 缺省 → 与今天逐字节相同（`TokenBudgetTests` 五条断言全过，见 §5）。
- 开关打开：`max_tokens` 缺省 → 拒绝创建。这是一条**已知会打红现有测试**的变更，`tests/test_rds_compass_drive.py:346` 的 `test_an_undeclared_cap_is_unknown_and_spend_is_still_counted` 正是「无 cap 也要能跑并计数」——它在新模式下语义反转。这条测试必须**在开关关闭下继续通过**，另写一条开关打开下的拒绝测试，而不是改掉旧的。

---

## 4. EIG 集成：token 进分母的公式

### 4.1 现状

`scripts/rds_compass_model.py:708-716`：

```python
cost = exact(item.get('cost', 1))
require(cost > 0, 'Experiment cost must be positive')
'eig_per_cost': round(gain['eig_bits'] / float(cost), 9)
```

`item['cost']` 来自 declaration 的 candidate `Cost`（`rds_compass_declaration.py:197`，须 >0），是**人工声明的无量纲实验成本**，与 token 无关。`Agent` 侧成本从未进这个分母。

### 4.2 公式

记：
- `c_jump` = 一次跳转的计费 token 数（§4.3）；
- `B` = 该 campaign 的 `agent_tokens` cap；
- `w` = 一个「实验单位」折多少 token，**必须由 declaration 声明**（`weighting.tokens_per_unit`），不得由代码猜；
- `cost_i` = candidate `i` 的声明实验成本（无量纲单位）。

**分母（推荐形式）**

```
cost_total(i) = cost_i + λ · (mean_charged_jump_tokens / tokens_per_unit)
```

其中
- `mean_charged_jump_tokens = budget('agent_tokens').charged / max(1, |ADOPTED 跳转|)`；
- `λ ∈ [0,1]` 由 declaration 的 `eig.token_weighting` 声明，缺省 **0**（关闭），填 1 = agent 成本与实验成本同权重；
- `tokens_per_unit` 缺省 **UNKNOWN**：`λ > 0` 而 `tokens_per_unit` 未声明时，`rank_experiments` 必须报错而不是补 1（补 1 就是发明分母）。

**为什么用 `mean_charged_jump_tokens` 而不是候选自己的 token 预测**：候选级别的 token 消耗**没有证据**——agent 还没为本候选花过 token，预测它就是编数。用**已实现的均值**是「有条件的历史估计」，并且它随 campaign 推进自动收敛。这有一个必须写明的边界：早期跳转少时均值方差大，`|ADOPTED| < k`（建议 k=3）时**不启用** token 项，`λ` 视为 0，理由输出 `TOKEN_WEIGHTING_INSUFFICIENT_JUMPS`。

**结果里必须同时给出两个数**，不能让读者分不清：

```python
'eig_per_cost':        gain / cost_i                      # 原口径，保留，向后兼容
'eig_per_cost_total':  gain / cost_total(i)               # 新口径，仅 λ>0 时出现
'token_weighting': {'lambda': λ, 'tokens_per_unit': ..., 'mean_charged_jump_tokens': ...}
```

`rank_experiments` 的排序键（`:716-717`）用哪个：**当 `λ > 0` 用 `eig_per_cost_total`**，否则用原键。**不要**把两种口径混进同一个排序里有选择地切——同一批候选里排名必须同口径，否则「为什么这个候选排前面」不可复现。

### 4.3 `c_jump` 的计费口径（token 三分类）

```
c_jump(u) = u.input_tokens + u.output_tokens
          - (1 - d) · u.cached_input_tokens        d = cached_input_discount ∈ (0,1]
```

- `d = 1.0`（缺省）→ `c_jump = input + output`，**与今天逐字节相同**，`cached_input_tokens` 不参与。注意 `rds_compass.py:896` 的现状求和就是这个式子，所以缺省行为零变更。
- `d < 1` → 缓存命中的输入按 `d` 折价。**`cached_input_tokens` 必须是 `input_tokens` 的子集**；`cached > input` 时 `_usage` 当前是接受的（只查范围），设计里应新增一条：`require(cached_input_tokens <= input_tokens)`，否则 `c_jump` 可能为负。这条校验是 `_usage` 的收紧，会拒掉现在能通过的构造输入——见 §6。
- `cap` 的比较与 `EXHAUSTED` 判定用 `charged`（= `Σ c_jump`），**不是** `Σ(input+output)`。改 `d` 而不改 cap 会让同一个 cap 值对应不同的实际调用次数——所以 `d` 必须是 declaration 里的冻结值，跑中不许改（同 `docs/budget-economy.md:36` 的冻结精神）。

### 4.4 「每正确解成本」怎么算

`correct` 的口径沿用现有 verifier，不由本设计定义。成本端：

```
cost_per_correct_solution = budget('agent_tokens').charged / max(1, N_GOAL_IDENTIFIED_with_verifier_PASS)
```

- 分子是 **`charged` 全量**（含废跳转），分母只数**通过独立验证的** `GOAL_IDENTIFIED`——这正是 AGENTS.md 第 2 条（task truth ≠ scientific support）的用法。
- 分母为 0 时**不报 `0` 也不报 `∞`**，报 `UNKNOWN` 并附 `N_correct = 0`。报 0 会让「什么都没解决」看起来最便宜。
- 降级触发条件（若将来要用）：`cost_per_correct_solution > threshold` 且 `N_correct >= 3`。`N_correct < 3` 不降级——1 次侥幸不构成成本画像。**threshold 不在本设计里给定值**：它是部署参数，必须进 declaration 才会被审计，代码里不许有缺省常数。

---

## 5. 验收口径：改完后跑什么命令能证明「token 被正确记账」

先写死判据，再跑。每条都能自动判，不靠人眼。

### 5.1 等式（唯一的硬判据）

```
budget('agent_tokens').spent
  == Σ over events(kind='DISPOSITION') of c_jump(record['agent']['usage'])
  （record['agent']['usage'] 为 null 的调用：c_jump 记为 UNKNOWN，等式退化为
     spent == Σ over reported only，且必须同时有 count(unreported) == 0 的断言，
     否则等式不成立）
```

实现期用一条只读的校验命令（不动任何 .py，用 `sqlite3` CLI 或 `python -c`）直接查：

```bash
python -c "
import json,sqlite3,sys
db=sqlite3.connect('.rds/ledger.sqlite'); db.row_factory=sqlite3.Row
want=DBLEDGER  # 见下
spent=db.execute(\"SELECT spent FROM budget WHERE resource='agent_tokens'\").fetchone()[0]
rows=[json.loads(r['record']) for r in db.execute(\"SELECT record FROM events WHERE kind='DISPOSITION'\")]
tot=unrep=0
for r in rows:
    u=(r.get('agent') or {}).get('usage')
    if u is None: unrep+=1; continue
    tot+=u['input_tokens']+u['output_tokens']
print('budget_spent',spent,'events_sum',tot,'unreported',unrep)
sys.exit(0 if abs(spent-tot)<1e-9 and unrep==0 else 1)
"
```

（`DBLEDGER` 只是个占位说明——实际路径以 `ProjectStore.path` 为准，`.rds/ledger.sqlite` 需按仓库实测路径替换。）**判据：`exit 0` 且 `unreported == 0`。**

### 5.2 分维度等式（阶段 B 才有）

```
components.input_tokens  == Σ u.input_tokens
components.output_tokens == Σ u.output_tokens
components.cached_input_tokens == Σ u.cached_input_tokens
```

以及 **`spent == c_jump 求和` 而不是 `input+output` 求和**——当 `d < 1` 时这两条必须能区分开，否则分列为 0 也看不出问题。

### 5.3 现有测试必须继续全绿

```bash
python -m pytest tests/test_rds_compass_drive.py -k TokenBudget -q
```

`tests/test_rds_compass_drive.py:313-364` 的 `TokenBudgetTests` 五条断言（`spent==1100`、`cached_input_tokens==200`、`calls==1`、`provenance=='INPUT_REPORTED'`、无 cap 时 `spent==20` 且调用 2 次）在**开关关闭**下必须逐条不变。这条是「最小反向测试」：关掉新逻辑（开关关），旧行为原样。

### 5.4 端到端一次小 campaign

```bash
python -m scripts.compass run --root <tmp> --declaration <decl> 2>&1 | tail -40
```

看三件事，三个都要有：
1. `report.cost.agent_tokens` 与 `budget('agent_tokens').charged` **相等**（注意：`report` 现在给的是 `spent`，见 `rds_compass.py:1537`；口径统一前两者可以不等，但**差值必须恰好等于 `charged` 与 `spent` 的定义差**，不是随机差）；
2. 每次 `JUMP`/`workset`/`report` 的 `budget.agent_tokens.remaining` 单调不增；
3. 至少一次 `cap` 触达后，下一次跳转的 `status == 'EXHAUSTED'` 且**没有**新 `DISPOSITION` 产生（不重复买）。

### 5.5 失败要能报错

故意把 `max_tokens` 设成 1 跑一次，必须看到 `EXHAUSTED` 而不是静默继续；故意让 agent 不报 usage，必须 `USAGE_UNREPORTED` 且 `unreported == 1`，5.1 的等式**必须以 exit 1 退出**（不被宽容地算过）。

---

## 6. 风险：哪些地方会 break

按「会不会炸」排序。

| # | 风险 | 位置 | 严重度 |
| --- | --- | --- | --- |
| 1 | **quick / owned 路径直接 require** `set(contract['budget']) == {'wall_seconds'}` | `scripts/rds_quick_ledger.py:57`、`scripts/rds_owned_tools.py:23-24` | 高：contract 一旦带 `agent_tokens`，这两条 `require` 立刻 `AssertionError`/`ValueError`，quick exec 与 native tool preparation 全挂。必须同批改这两处的判据（改成「允许 wall + 白名单」而不是「只允许 wall」），否则是硬 break |
| 2 | 读端口列数错位 | `scripts/rds_project_read_port.py:206` `(self.__row_bytes,)*5` | 高且**静默**：阶段 B 加两列后若忘记改长度，`_read` 的字节参数错位，可能不抛异常但读出的限流值全错，或 `max_rows` 判断失准。这类 bug 不报错，只是「限流悄悄失效」 |
| 3 | `BudgetRow` 五字段构造失败 | `scripts/rds_project_read_port.py:42-47, 209` | 高：`BudgetRow(*row)` 在行多两列时直接 `TypeError` |
| 4 | `snapshot()` 的 `unit` 启发式 | `scripts/rds_project.py:1811-1812` `resource.endswith('_seconds')` | 中：`agent_tokens` 会落到 `unit='agent_tokens'`，展示成 `"5.0 agent_tokens"` 而非 `"5.0 tokens"`。不崩，但报告口径难看且难比对。阶段 B 的 `unit` 列就是这个问题的解 |
| 5 | 现有测试语义反转 | `tests/test_rds_compass_drive.py:346` 无 cap 仍要能跑 | 中：开关打开后这条测试的语义（`CAP_UNKNOWN` 也调用）与硬拒绝冲突。修法是**保留原测试在开关关闭下**，新增开关打开下的拒绝测试，而不是改旧断言 |
| 6 | `_usage` 收紧会拒旧输入 | `scripts/rds_compass.py:829-838` 新增 `cached <= input` | 中：现在只查 `0 <= v < 10**12`。收紧要写明是**新校验**，并有测试证明 `cached > input` 被拒；否则某天一个 agent 报错数据会被当成正常 |
| 7 | 旧账本被误当 0 | §3.2 | 高但可控：实现时必须让读侧对「有 agent 声明、无 `agent_tokens` 行」报 `Token budget UNKNOWN`，不能 `COALESCE(...,0)`。一个 `COALESCE` 就把 fail-closed 变成无限额度 |
| 8 | 超冲与 `_check_start` 打架 | `scripts/rds_project.py:502-507` vs `docs/compass.md:114` | 中：budget 表的 `_check_start` 不允许 `spent+charged+reserved > cap`，而 token 单次调用可超。两条路是**不同的门**（token 记账不走 run 结算），但读者会以为冲突。文档和代码注释都必须写死「token 的 cap 检查在调用之前，run 结算的检查不适用于 `agent_tokens`」 |
| 9 | `report` 的 `spent` vs budget 的 `charged` | `scripts/rds_compass.py:1537` `'agent_tokens': spent if plan.get('agent') else None` | 中：`report.cost.agent_tokens` 来自 `agent_tokens()` 的 `spent`（`input+output`），budget 表存 `charged`（`c_jump`）。`d < 1` 时两个数**不同**，且没人会想到去看 `d`。必须让 `report` 同时给出 `charged` 并把 `charge` 口径一起印出来 |
| 10 | CI 超时/环境 | — | 低：本设计零 .py 改动，测试面不变。实现阶段要注意 `TokenBudgetTests` 里的 subprocess 调用在 CI 上偶尔 flake（AGENTS.md 允许重试一次，不许改超时） |
| 11 | RSI 仍不记账 | `scripts/rds_compass_rsi.py`（无 cost/token/agent 字样） | 低但**要让读者知道**：token 进 budget 只覆盖 Compass 的 agent 调用。RSI 触发的 synthesis 若也调用 LLM，它的消耗**不在**这个账里，`cost_per_correct_solution` 会系统性偏低。这条必须写进偏差说明，不能默默当全覆盖 |

### 6.1 明确不做的事

- 不做全局 `budget.schema_version`（见 §2.5，用 contract 里的 `ledger_schema`）。
- 不给 `COMPASS_*` 新增 kind 存 token（`KINDS` 闭合，加 kind 会扩散影响面）。
- 不在代码里给 `threshold` / `tokens_per_unit` 缺省常数（= 发明预算，违反 Fail-Closed）。
- 不把 `components` 的 JSON 当阈值读。
- 不为了「让旧测试过」而放宽 §3.2 的 fail-closed。

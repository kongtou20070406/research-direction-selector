# C1 Token Budget 设计文档独立审查报告

- **审查对象**：`docs/c1-token-budget-design-20261020.md`（commit `f0425c8`，针对分支 `HEAD 6bf1efa`）
- **审查角色**：独立审查者（科研搭子 / 鲸鱼娘·大肥鱼）
- **最终裁定**：**需修改后实施（存在致命逻辑 Bug 与口径冲突，禁止按当前版本直接施工）**

---

## 1. 结论摘要

设计文档对系统现状（`ProjectStore` 多维度 budget 表、`agent_tokens` 现状、两处硬 break 点）的剖析**大部分准确且敏锐**。但文档在核心记账机制、验收脚本和指标定义中存在**致命逻辑硬伤**，如果按当前文档直接实施，会导致：
1. **双重扣款（Double Charging Bug）**：按照文档伪代码更新 `budget` 表，token 额度将被以 **2 倍速** 扣除，任务花到一半就会被误杀；
2. **验收脚本自相矛盾**：§5.1 的验收脚本直接写死 `input + output`，在开启 cached 折价（`d < 1.0`）时必然断言失败（exit 1）；
3. **核心业务指标不可达**：单账本内 `GOAL_IDENTIFIED` 最多仅为 1，文档声称的 `N_correct >= 3` 降级条件为死代码；
4. **EIG 分母加常数偏置可能反噬探索**：给无辜的物理实验加上固定 token 偏置，会抹平实验间的成本差异，诱使系统偏好高物理耗时实验。

---

## 2. 逐条技术断言代码核对

### 2.a 「加 token 不需要改表结构」与 budget 表本质
- **文档声称**：`budget` 表已是多维度表，加一行 resource 即可，阶段 A 零结构变更（§0, §1.2, §2.1）。
- **代码核对结果**：**属实，但表并非 schema-free JSON**。
  - 在 `scripts/rds_project.py:1062`：
    ```sql
    CREATE TABLE IF NOT EXISTS budget(resource TEXT PRIMARY KEY,cap REAL NOT NULL,spent REAL NOT NULL DEFAULT 0,charged REAL NOT NULL DEFAULT 0,reserved REAL NOT NULL DEFAULT 0);
    ```
  - `budget` 表是典型的关系型键值行表，每种 resource 占独立一行。在 `scripts/rds_project.py:1018-1022` 中，只要 `budget` 包含 `wall_seconds`，其余 resource 名满足字符串约束且值为 number 即可通过校验；并在 `1088` 行通过 `executemany` 插入。
  - **关键限制**：在 DDL 层面的确无需 `ALTER TABLE`，但**现有代码没有任何通用结算路径能让 Compass 外部更新该行**。`rds_project.py:1761-1763` 仅在 run 结算路径中更新，而 Compass 的 agent 调用并不属于 ProjectStore 的 run。因此，必须新增记账入口函数，但写入逻辑存在致命缺陷（见下文第 3 节）。

### 2.b 文档声称的「两个硬 break 点」
- **文档声称**：contract 一旦带 `agent_tokens`，`scripts/rds_quick_ledger.py:57` 与 `scripts/rds_owned_tools.py:23-24` 会直接挂掉（§1.3.7, §6.1）。
- **代码核对结果**：**完全属实，核对无误**。
  - `scripts/rds_quick_ledger.py:57`：
    ```python
    require(set(contract['budget']) == {'wall_seconds'}, 'Quick exec supports a wall-only parent ledger; use a full project manifest for other resources')
    ```
  - `scripts/rds_owned_tools.py:23-24`：
    ```python
    require(set(contract['budget']) == {'wall_seconds'},
            'Native tool application currently needs a wall-only budget; other preparation resources are UNKNOWN')
    ```
  - 并在 `scripts/rds_project.py:1086` 中，只要 contract 配置了 `advisor_policy.tool_bindings`，在 `project init` 事务中就会调用 `preparation_costs()`，进而触发 `rds_owned_tools.py:23-24`。
  - **影响**：若不预先将这两个 `require` 放宽为白名单（如包含 `agent_tokens`），任何声明了 token budget 的项目在初始化或快速执行时会立即抛出异常。

### 2.c 验收判据、`c_jump` 与 `unreported`
- **文档声称**：验收判据为 `budget('agent_tokens').spent == Σ c_jump(usage)` 且 `unreported == 0`（§5.1）。
- **代码核对结果**：
  1. **`c_jump(usage)` 在现有代码库中不存在**：全仓检索 0 匹配。它是文档 §4.3 中定义的新概念：`c_jump(u) = input + output - (1 - d) * cached_input`。现有代码 `scripts/rds_compass.py:925` 仅有 `sum(u['input_tokens'] + u['output_tokens'] for u in reported)`。
  2. **`budget('agent_tokens')` 在现有代码中读不出**：现有 budget 表中尚未插入此行。
  3. **`unreported` 现有代码可算**：`rds_compass.py:929` 计算了 `unreported_calls`，从 events 表中检查 `r['agent']['usage'] is None` 亦可统计。
  4. **代码级矛盾**：文档 §5.1 给出的验证脚本第 317 行写的是：
     ```python
     tot += u['input_tokens'] + u['output_tokens']
     ```
     该脚本完全忽略了 §4.3 定义的 `c_jump` 中的 discount `d` 折价！若测试用例设置了 `d < 1.0`，则 `budget.spent` 会与脚本中的 `tot` 不等，导致验收脚本直接 exit 1 失败。

### 2.d 「`agent_tokens` 是函数不是账本键」
- **文档声称**：`agent_tokens` 是函数不是账本键（§1.1, §1.3.1）。
- **代码核对结果**：**完全属实**。
  - `scripts/rds_compass.py:916` 定义为：`def agent_tokens(state):`，它是基于内存状态中 `state['dispositions']` 动态求和的纯派生函数。
  - 现有的 `ProjectStore` / `budget` 表中完全没有 `agent_tokens` 键。在 packet 和 report 中塞入的 `budget.agent_tokens` 纯粹是临时挂载给外层的瞬态数据。若实现者把其当做账本中已有的字段直接读取，会造成严重的死代码或空值异常。

---

## 3. 设计本身的深层缺陷与风险分析

### 缺陷 1：双重扣款（Double Charging Bug）——致命逻辑硬伤
- **位置**：文档 §2.1（第 101-105 行）：
  ```python
  db.execute("UPDATE budget SET spent=spent+?, charged=charged+? WHERE resource='agent_tokens'", (amount, amount))
  ```
  文档解释称：“`spent` 与 `charged` 同步增加……让 `snapshot().remaining` 与 EIG 用同一个数。”
- **反例与证据**：
  查阅 `scripts/rds_project.py:1811`：
  ```python
  "remaining": row["cap"] - row["spent"] - row["charged"] - row["reserved"]
  ```
  查阅 `scripts/rds_compass.py:45-46`：
  ```python
  'used': row['spent'] + row['charged'],
  'remaining': row['cap'] - row['spent'] - row['charged'] - row['reserved']
  ```
  **在 RDS 的核心会计语义中，`used = spent + charged`，`remaining` 同时扣除 `spent` 和 `charged`！**
  若对同一次 agent 调用的花费 `amount` 同时加在 `spent` 和 `charged` 上：
  - `used` 会增加 `2 * amount`；
  - `remaining` 会减少 `2 * amount`。
  这意味着所有 token 预算将以 **2 倍速** 扣减，原本 200 万 token 的预算，在花掉 100 万时账本就判定 `remaining <= 0` 并触发 `EXHAUSTED`。

### 缺陷 2：单账本内「每正确解成本」指标与降级条件脱节
- **位置**：文档 §4.4（第 281-287 行）：
  ```
  cost_per_correct_solution = budget('agent_tokens').charged / max(1, N_GOAL_IDENTIFIED_with_verifier_PASS)
  降级触发条件：cost_per_correct_solution > threshold 且 N_correct >= 3
  ```
- **反例与证据**：
  查阅 `scripts/rds_compass_drive.py:107`：当一个 campaign 的唯一假说通过验证，系统执行 `return finish('GOAL_IDENTIFIED' if unique ...)` 并退出整轮 campaign。
  - 每个 Compass campaign（单个 ProjectStore 账本）只有一个目标（`plan['goal']`），终局要么是 1 次 `GOAL_IDENTIFIED`，要么是 0 次（`BUDGET_EXHAUSTED` / `SEARCH_EXHAUSTED`）。
  - 因此在单账本内，`N_GOAL_IDENTIFIED` **永远至多为 1**！
  - 文档声称的 `N_correct >= 3` 降级门槛在单账本内是**数学上不可能触发的死逻辑**。
  - 此外，文档没有规定该指标持久化到哪个具体的账本表或 report 结构中。

### 缺陷 3：EIG 分母加常数 token 偏置，扭曲实验选择
- **位置**：文档 §4.2（第 245 行）：
  ```
  cost_total(i) = cost_i + λ · (mean_charged_jump_tokens / tokens_per_unit)
  ```
- **分析**：
  1. 物理实验候选（candidate experiment）自身由 run 执行，消耗的是物理实验成本 `cost_i`（耗时），**其执行过程根本不调用 LLM agent**。
  2. 对某轮决定的所有候选物理实验，加在分母上的 token 偏置项 `C = λ · (mean_jump / rate)` 是一个**完全相同的常量**。
  3. 当 `C` 相对较大时，`gain / (cost_i + C)` 抹平了各个候选实验本身的成本差异，使 EIG 排序退化为无视物理成本的纯 `gain` 贪心排序。系统会失去原本“优先挑廉价实验快速证伪”的节俭性，偏好昂贵实验，从而加速消耗本就不宽裕的物理预算。这与经验库中“绝对 cap 反噬多变量物理实验 +28.6%”的教训完全同构。

### 缺陷 4：遗漏路径导致虚假成本信号
- **位置**：文档 §1.3.6、§6 表格 #11。
- **分析**：
  Compass 体系内除了 agent jump 外，还有 RSI worker 的 tool qualification（`rds_compass_rsi.py:219` 独立 subprocess 运行）以及 PySR / Julia 符号回归运算（`benchmark/newtonbench/`）。这些重型计算过程完全不产出 agent token，也不计入 token 账。如果一个任务依靠重度 RSI/PySR 探索，其 token 成本可能极低，但真实机器计算开销巨大。将 `cost_per_correct_solution` 完全绑定在 agent token 上会制造虚假的“廉价”偏差。必须明确此指标仅代表“Agent 交互成本”，不能作为全局科研成本指标。

---

## 4. 必须修改清单（带原文档行号）

实施前必须对设计文档完成以下修订：

1. **【P0】修复双重扣款逻辑（文档 §2.1，第 101-105 行）**
   - **修改前**：`UPDATE budget SET spent=spent+?, charged=charged+? WHERE resource='agent_tokens'`
   - **必须改为**：明确单源计量资源的记账口径。若 token 视为实测消耗，应仅执行 `spent=spent+?`（或统一按计费执行 `charged=charged+?`，另一字段保持 0）。确保 `spent + charged == c_jump`，保证 `remaining` 不被双倍扣减。
2. **【P0】对齐验收脚本与折扣口径（文档 §5.1，第 314-320 行）**
   - **修改前**：`tot+=u['input_tokens']+u['output_tokens']`
   - **必须改为**：脚本中必须引入 discount 参数 `d`，并严格执行 `tot += u['input_tokens'] + u['output_tokens'] - (1 - d) * u.get('cached_input_tokens', 0)`；或者在验收前置条件中明确写死：该脚本仅在默认 `d = 1.0` 下成立，若 `d < 1.0` 需使用通用验算公式。
3. **【P1】修正单账本指标定义与降级触发条件（文档 §4.4，第 281-287 行）**
   - **修改前**：`cost_per_correct_solution > threshold 且 N_correct >= 3`
   - **必须改为**：删除单账本内 `N_correct >= 3` 的假设。明确单账本内 `cost_per_correct_solution` 在成功时为 `charged / 1`，未解出时为 `UNKNOWN`。若需要多样本降级，必须显式定义为跨 campaign / predecessor 链汇总统计，或改为基于连续高成本未解出的自适应降级。
4. **【P1】给出 quick / owned 路径的具体白名单修改方案（文档 §1.3.7, §6.1）**
   - **必须补充**：在文档中明确给出 `scripts/rds_quick_ledger.py:57` 与 `scripts/rds_owned_tools.py:23-24` 的修改代码，将 `== {'wall_seconds'}` 重构为允许 `{'wall_seconds', 'agent_tokens'}` 的白名单校验，避免实现期漏改。
5. **【P2】重构 EIG token 权重的应用场景（文档 §4.2，第 245-255 行）**
   - **建议修改**：阶段 A 严禁默认开启 `λ > 0`；并且在文档中明确指出：该偏置项会稀释物理实验成本差异，仅在候选本身产生 token 差异（如候选携带不同的 LLM 评估方案）时才有真正的排序意义。

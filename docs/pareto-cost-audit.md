# 帕累托成本与每正确解成本审计报告 (Pareto Cost Audit)

> 审计环境：`D:\Myfile\work space\RDS\rds-357`，分支 `release/6.0.0-compass`，HEAD `3864dfa`。Python 3.13。  
> 审计日期：2026-10-22  
> 审计工具脚本：`D:\Myfile\work space\RDS\_cost_audit\audit_single_campaign.py`（未修改仓库 `scripts/`）

---

## 0. 核心审计结论

**系统当前无法自动从账本算出「每获得一个正确解花了多少成本」。**

核心断裂点如下：
1. **单账本成本断裂**：实验次数和 jump 次数能从 `events` 表（通过 `json_extract(body, '$.kind')`）读取；墙钟耗时能从 `budget` 表或 `receipts` 表读到；但 **token 成本三分类（input/output/cached）在 SQLite 关系表和 receipts 表中均无独立列**，只存放在 CAS 文件的 JSON payload 中，且必须由 `rds_compass.agent_tokens()` 扫 `dispositions` 动态聚合；若未启用 agent 则为 `None`/`CAP_UNKNOWN`。
2. **「正确」与「账本」脱节**：账本内部只有 `GOAL_IDENTIFIED`（事件标志）和 `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`（声称状态），**真实的「正确」（`claim_exact: true`）根本不存在于单 campaign 的 SQLite 账本中**，而是由外部 benchmark 驱动脚本在 holdout 数据集上独立 replay 验算后，写在外部 JSON 报告里的。单账本无法自证正确，也无法在单库内完成成本与正确解的 SQL JOIN。
3. **比值计算完全缺失**：全仓代码（`scripts/`、`benchmark/`）没有任何计算 `cost_per_correct`、`per_correct` 或 `token_per_correct` 的代码逻辑。
4. **跨 campaign 无汇总脚本**：多个 campaign 在执行时其临时账本散落在各临时目录中（如 `devset-run.json` 记录的 `AppData\Local\Temp\devset-run-f_0jl6l7`），执行完即废弃或未集中索引，没有汇总多个 `campaign_root` 的脚本。
5. **λ（成本权重）实际为 0**：虽然设计文档 `docs/c1-token-budget-design-20261020.md` 提出了 $\lambda$ 权重公式，但在 `docs/review-c1-design.md` 中被指出具有破坏实验贪心效率的结构缺陷；代码库 `scripts/` 中**完全没有定义 $\lambda$ 或 `token_weighting`**，当前 EIG 排序仅除以单次物理实验成本 `cost`（`rds_compass_model.py:737`），对 token 和墙钟成本的折算权重等价于 $\lambda = 0$。

---

## 1. 现状表：成本项 × 能否读到 × 从哪读 × 阻碍

| 成本项 / 指标 | 能否从单账本自动读到 | 读取来源（表名 / 字段 / API） | 存在阻碍 / 限制 |
| :--- | :---: | :--- | :--- |
| **物理实验次数** | **能** | **SQL**: `SELECT COUNT(*) FROM events WHERE json_extract(body, '$.kind') = 'COMPASS_OBSERVATION'`<br>**API**: `rds_compass.report(store)['cost']['experiments']` (`rds_compass.py:1696`) | `events` 表的事件类型放在 JSON 字符串 `body` 中，需使用 `json_extract` 解析；API 直接返回整数。 |
| **Jump 次数** | **能** | **SQL**: `SELECT COUNT(*) FROM events WHERE json_extract(body, '$.kind') = 'COMPASS_JUMP'`<br>**API**: `rds_compass.report(store)['cost']['jumps']` (`rds_compass.py:1697`) | 同样存储于 `events.body`，API 读取 `state['jumps']` 的长度。 |
| **墙钟耗时 (Wall Seconds)** | **能** | **SQL (总额)**: `SELECT spent FROM budget WHERE resource = 'wall_seconds'`<br>**SQL (细项求和)**: `SELECT SUM(json_extract(body, '$.resources.wall_seconds.measured')) FROM receipts`<br>**API**: `rds_compass.fold(store)['budget']['wall_seconds']['used']` (`rds_compass.py:1837`) | `budget` 表记录的是所有 runs 扣减的累计秒数；`receipts` 表记录每次 run 的实测秒数。但在 `compass.report()` 的根 `cost` 字典中未直接暴露 `wall_seconds`，需从 `fold` 的 `budget` 取。 |
| **Token 总数** | **部分可读** | **SQL**: `SELECT spent, charged FROM budget WHERE resource = 'agent_tokens'`<br>**API**: `rds_compass.agent_tokens(state)['spent']` (`rds_compass.py:916`) | 若未配置 `agent_tokens` 预算，`budget` 表中无此行。`budget` 表只存总量 `spent`/`charged`，不分 input/output/cached。 |
| **Token 三分类**<br>(input/output/cached) | **不能直接查表** | **存储路径**: `.rds/cas/<sha256>.json` (由 `kind='COMPASS_DISPOSITION'` 的 `record.path` 指向)<br>**API**: `rds_compass.agent_tokens(state)` (`rds_compass.py:916-953`) | **SQLite 表和 receipts 表中均无此三项字段**。必须遍历 `dispositions` 读取 CAS 文件中的 `accounting.usage` 字典计算。若无 LLM 介入（如纯启发式/自动跳转 `COMPASS_AUTO`），返回 0 / `CAP_UNKNOWN`。 |
| **正确解 (Correctness)** | **否 (单账本内不可自证)** | **单账本仅有**: `events` 中 `COMPASS_REPORT` 的 `law` 与 `claim` (`scripts/rds_compass.py:1718`)<br>**真实验证**: 外部 benchmark 脚本（如 `benchmark/newtonbench/run.py:270-285`）通过 SymPy / 数值 replay 计算出 `claim_exact`，存在外部 json 报告中 | 单账本内的 `GOAL_IDENTIFIED` 仅代表当前已无反例或触发阈值，无法排除过拟合；单账本无法自证真理，不能在数据库内与成本表做 JOIN。 |
| **每正确解成本** | **否** | **无** | 全仓代码均无此计算逻辑。 |

---

## 2. 五大问题深度审计细节

### 2.1 单个 campaign 结束时总成本读取分析
对 `benchmark/newtonbench/results/sparse-campaign/.rds/project.sqlite3` 账本进行了实测（见第 4 节命令输出）：

1. **实验次数**：
   - 表与字段：`events` 表，`id INTEGER, body TEXT`。
   - SQL: `SELECT COUNT(*) FROM events WHERE json_extract(body, '$.kind') = 'COMPASS_OBSERVATION';`（实测输出：32）。
   - API: `rds_compass.report(store)['cost']['experiments']`（实测输出：32）。
2. **Jump 次数**：
   - 表与字段：`events` 表。
   - SQL: `SELECT COUNT(*) FROM events WHERE json_extract(body, '$.kind') = 'COMPASS_JUMP';`（实测输出：1）。
   - API: `rds_compass.report(store)['cost']['jumps']`（实测输出：1）。
3. **墙钟耗时**：
   - 总额 SQL: `SELECT spent FROM budget WHERE resource = 'wall_seconds';`（实测输出：5.6914s）。
   - 细项累加 SQL: `SELECT SUM(json_extract(body, '$.resources.wall_seconds.measured')) FROM receipts;`（实测输出：5.6914s）。
   - API: `rds_compass.fold(store)['budget']['wall_seconds']['used']`。
4. **Token 消耗**：
   - 关系表：`budget` 表（`resource TEXT PRIMARY KEY, cap, spent, charged, reserved`）。在 `sparse-campaign` 中未配置 token cap，无该行记录。
   - 三分类拆解：`budget` 表和 `receipts` 表**均无** input/output/cached 字段。这些数据存在 `COMPASS_DISPOSITION` 事件指向的 CAS 文件（`.rds/cas/*.json`）内的 `accounting['usage']` 字典。
   - API 调用：`rds_compass.agent_tokens(state)` (`scripts/rds_compass.py:916`) 会提取并汇总 `cached_input_tokens` 与 `spent`，但当没有 LLM 交互时返回 `{'spent': 0, 'cached_input_tokens': 0, 'status': 'CAP_UNKNOWN'}`。

### 2.2 「正确」字段与认证指标分析
1. **`GOAL_IDENTIFIED`**：
   - **位置**：`scripts/rds_compass.py:1685`、`scripts/rds_compass.py:1700-1708`。
   - **谁写的**：`rds_compass.report(store)` 在生成报告时，若 `classes` 只有 1 类且反驳数为 0，或者最佳假设满足容差，将 `status` 标记为 `GOAL_IDENTIFIED`，并写入 `COMPASS_REPORT` 事件的 `record.path` 对应的 CAS 文件。
2. **`scientific_support` / `claim`**：
   - **位置**：`scripts/rds_compass.py:1505-1517`，`scripts/rds_compass.py:1718`。
   - **状态枚举**：`CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`、`CONSISTENT_BUT_UNCONFIRMED`、`INSUFFICIENT_EVIDENCE`、`REFUTED_BY_OBSERVATION`。
   - **本质**：这仅是针对已有观测记录（receipted measurements）的内蕴一致性声明，不是跨分布泛化正确性。
3. **`claim_exact`**：
   - **位置**：`benchmark/newtonbench/run.py:270-285`、`benchmark/newtonbench/pareto.py:108`。
   - **谁写的**：**外部评测框架**在 campaign 结束后，读取 campaign 报告推荐的 `law`（公式字符串），在独立的全部 holdout 验证集上重放评估（`eval_expression`），计算最大残差与绝对误差，并结合 SymPy 符号等价判定。只有满足 `0 counterexamples` 时才赋予 `claim_exact = True`。
   - **存储位置**：保存在外部结果文件（如 `benchmark/newtonbench/results/devset-run.json`）的 `runs` 数组中，**不存入 SQLite 账本**。
4. **`false_certification_rate` 的计算机制**：
   - **位置**：`benchmark/newtonbench/verify_devset.py:126`、`benchmark/newtonbench/pareto.py:112`。
   - **计算公式**：
     $$\text{false\_certification\_rate} = \frac{\sum [ \text{claim} = \text{CONSISTENT} \land \text{claim\_exact} = \text{False} ]}{\sum [ \text{claim} = \text{CONSISTENT} ]}$$
   - **结论**：它依赖对全集外部真值的检验，单个 campaign 的账本**无法在内部闭环算出该指标**。

### 2.3 「每正确解成本」比值代码检索
全仓检索关键词：`per_correct`、`cost_per`、`token_per`、`tokens_per`：
- `scripts/` 代码库中：**匹配为 0**。没有一行代码计算或持久化该指标。
- `docs/c1-token-budget-design-20261020.md:281`：曾在设计草案中提及 `cost_per_correct_solution` 降级门槛，但在 `docs/review-c1-design.md:130` 中被审计指出：“单账本内 `N_correct >= 3` 是数学上不可能触发的死逻辑，且没有规定持久化到哪个表”，因此**未予实现**。
- `benchmark/newtonbench/results/devset-run.json`：仅分别记录了每个 run 的 `experiments`、`seconds`、`claim_exact`，但没有任何聚合比值。

### 2.4 跨 campaign 汇总机制
1. **`campaign_root` 的位置**：
   - 在跑批量评测时（如 `run_suite` 或 `devset-run`），每个 campaign 的 root 往往是临时生成的路径（例如 `C:\Users\XU\AppData\Local\Temp\devset-run-f_0jl6l7\<task>\<budget>\`）。
2. **汇总脚本现状**：
   - 现有的 `benchmark/newtonbench/pareto.py` 只读取运行产物的聚合 JSON（如 `devset-run.json`），仅从 JSON 中读取 `experiments` 和 `claim_exact` 做百分比统计。
   - **全仓没有任何脚本能够遍历多个 `campaign_root` 并逐个打开 `.rds/project.sqlite3` 聚合物理实验、时间与 token 成本**。

### 2.5 λ（成本权重）的定义与取值
1. **代码现状**：
   - 全仓检索 `lambda` 在 `scripts/` 中出现 124 处，除 `rds_cpu_jepa.py:124` 的岭回归正则项 `(X^T X + lambda I) W` 外，其余全部为 Python 匿名函数。
   - 全仓检索 `tradeoff`、`token_weighting`、`eig_per_cost_total`：在 `scripts/` 中匹配为 0。
2. **实验排序成本公式**：
   - 位于 `scripts/rds_compass_model.py:737`：
     ```python
     eig_per_cost = round(gain['eig_bits'] / float(cost), 9)
     ```
   - 这里的 `cost` 直接取自候选实验声明的 `item.get('cost', 1)`（物理测量代价）。
   - **结论：$\lambda$ 未定义，实际等价于 $\lambda = 0$。当前决策层完全忽略 token 与时间开销。**

---

## 3. 「每正确解成本」缺什么（按实现难度升序）

要真正实现帕累托前沿的成本计量，必须补齐以下四层缺陷：

```
[Level 1: 外部结果聚合比值]  --->  [Level 2: 单账本成本提取 API]  --->  [Level 3: 跨 Campaign 遍历器]  --->  [Level 4: 在线决策 λ 折算]
(难度: 极低, ~30 LOC)             (难度: 低, ~40 LOC)                  (难度: 中, ~80 LOC)                 (难度: 高, ~150 LOC, 需理论重构)
```

### 缺陷 1：外部结果文件缺乏比值计算输出（实现难度：★☆☆☆☆，约 30 LOC）
- **缺失**：`benchmark/newtonbench/pareto.py` 只计算并打印了 accuracy（解出率），没有计算 $\frac{\sum \text{experiments}}{N_{\text{correct}}}$ 和 $\frac{\sum \text{seconds}}{N_{\text{correct}}}$。
- **改动范围**：在 `benchmark/newtonbench/pareto.py:120-145` 的汇总报表生成逻辑中，加入对 `sum(experiments)/exact` 与 `sum(seconds)/exact` 的两列输出。

### 缺陷 2：`rds_compass.py` 报告未包含完整的资源细项（实现难度：★★☆☆☆，约 40 LOC）
- **缺失**：`rds_compass.report(store)['cost']` 仅有 `experiments`、`jumps`、`agent_tokens`，缺少 `wall_seconds`；且当 `agent_tokens` 存在时，未暴露 `input_tokens` / `output_tokens` / `cached_input_tokens` 三分类。
- **改动范围**：在 `scripts/rds_compass.py:1695-1698` 中，将 `fold()['budget']` 中的 `wall_seconds['used']` 以及 `agent_tokens(state)` 的三分类字典嵌入 `cost` 结构中。

### 缺陷 3：缺失持久化跨 Campaign 账本汇总聚合器（实现难度：★★★☆☆，约 80 LOC）
- **缺失**：缺少一个标准 CLI 工具（如 `scripts/rds_campaign_summary.py`），能够接受多个 `campaign_root` 路径，读取每个 campaign 的 `project.sqlite3`，提取每个任务的最终 `claim`、总 `experiments`、`wall_seconds`、`agent_tokens`，并与真实验证标签匹配。
- **改动范围**：新建独立脚本 `scripts/rds_aggregate_campaigns.py`，实现多库遍历与跨库成本与正确率统计。

### 缺陷 4：EIG 实验选择缺乏多目标/混合成本权重模型（实现难度：★★★★☆，约 150 LOC + 理论推导）
- **缺失**：目前候选实验只除以标量 `cost`。若要加入 token/墙钟折算（即 $\lambda > 0$），不能采用 `docs/c1-token-budget-design-20261020.md` 中被否决的“分母加常数 token 偏置”做法（该做法会导致 EIG 退化为贪心，反噬物理开销）；需要建立严格的无量纲多目标帕累托前沿选择机制。

---

## 4. 可立刻运行的验证命令与实测输出

我们在外部临时目录 `D:\Myfile\work space\RDS\_cost_audit\audit_single_campaign.py` 编写了独立审计验证脚本，该脚本：
1. 直连单个 campaign SQLite 数据库执行标准 SQL 审计；
2. 调用 `rds_compass.py` 核心接口进行 API 审计；
3. 解析已有 benchmark 汇总报告 `benchmark/newtonbench/results/devset-run.json` 进行宏观成本与正确解比值核算。

### 运行命令
```powershell
python "D:/Myfile/work space/RDS/_cost_audit/audit_single_campaign.py"
```

### 实际运行完整终端输出（无截断真实输出）
```text
======================================================================
1. SQL AUDIT ON SINGLE CAMPAIGN LEDGER
Database: D:\Myfile\work space\RDS\rds-357\benchmark\newtonbench\results\sparse-campaign\.rds\project.sqlite3 (exists: True)
- Experiments count (events WHERE json_extract(body, '$.kind') = 'COMPASS_OBSERVATION'): 32
- Jumps count (events WHERE json_extract(body, '$.kind') = 'COMPASS_JUMP'): 1
- Budget table dump:
  resource=wall_seconds: cap=900.0, spent=5.6913948000001255, charged=0.0, reserved=0.0
- Receipts table: count=32, sum(measured wall_seconds)=5.6914s
- agent_tokens budget row: NOT_PRESENT in budget table (resource unconfigured)
======================================================================
2. API AUDIT (rds_compass.py)
compass.report(store)['cost'] = {
  "experiments": 32,
  "jumps": 1,
  "agent_tokens": null
}
compass.report(store)['solution'] = null
compass.agent_tokens(state) = {
  "cap": null,
  "spent": 0,
  "cached_input_tokens": 0,
  "calls": 0,
  "unreported_calls": 0,
  "status": "CAP_UNKNOWN",
  "provenance": "INPUT_REPORTED"
}
======================================================================
3. BENCHMARK SUMMARY (from benchmark/newtonbench/results/devset-run.json)
Campaign root recorded: C:\Users\XU\AppData\Local\Temp\devset-run-f_0jl6l7
Claim exact rule: status == GOAL_IDENTIFIED and 0 counterexamples on full devset replay
Total benchmark task runs: 15
Correct solutions (claim_exact == True): 12 / 15 (80.0%)
Total physical experiments: 84
Total wall clock seconds: 802.60s
Experiments per correct solution: 7.00
Seconds per correct solution: 66.88s
Agent tokens per correct solution: UNKNOWN (not tracked in devset-run.json)
======================================================================
```

### 输出数据剖析
- **单账本事实**：在 `sparse-campaign` 中，完成了 32 次物理测量实验和 1 次 Jump，累计墙钟开销为 5.6914 秒，未配置 Agent Token 账。
- **宏观 Benchmark 事实**：在包含 15 个运行任务的 `devset-run.json` 中，系统达成 12 个绝对正确解（80.0% 正确率），共消耗 84 次实验与 802.6 秒。
- **每正确解成本核算**：
  - **物理实验成本**：**7.00 次实验 / 正确解**。
  - **墙钟计算成本**：**66.88 秒 / 正确解**。
  - **Agent Token 成本**：**UNKNOWN**（测试过程未记录 token 使用量）。

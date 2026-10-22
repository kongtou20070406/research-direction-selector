# PySR 适配层设计：四个最小接口（草案）

版本依据：PR #357 head `d4538ae5f6cc228f497bc74b8950bc2d37de9550`（RDS 6.0.0 候选分支，未合并、未发布）。定位：设计稿，**适配层代码尚未实现**，本文只依据当前源码与两份输入设计接口：

- 接入方案：《PySR 与 RDS 配合》（`PySR-RDS-integration.zh-CN.md`，2026-10-20 版），下文简称「接入方案 §N」；
- 工具审计：`docs/agent-rds-tools.md`；
- 已有的失效模式清单：`docs/pysr-redteam.md`。

设计原则（接入方案 §6.2）：四个薄接口，不新增调度器、数据库或公式状态机；单一账本（复用 ProjectStore/CAS/TMS）；fail closed，不支持就报 `UNSUPPORTED`，不静默改公式。

## 1. 四个接口的输入、输出与验收标准

### 1.1 `pysr_search`：PySR 候选生成

**输入**

- 冻结开发数据：rows、变量名、单位、数据身份（内容哈希）；
- 冻结搜索设置：算子白名单、`maxsize`、`niterations`、种子集合、PySR / SymbolicRegression.jl / Julia 实际版本；
- 候选选择规则与复杂度口径（看结果之前冻结）。

**输出**

- 原始前沿：每条 `(complexity, loss)` 的全表，不做挑选；
- 显式公式：可机读表达式 + PySR 原始字符串；
- 种子、版本、搜索日志、成本分项（环境准备、首次编译、搜索墙钟分别列）。

**验收标准**

- 同种子同设置可复算；全部产物与成本落盘，不依赖内存态。
- 变量名、单位与项目契约一致（干预参数、输入、输出贯通）。
- PySR 训练 loss 与 RDS 逐点容差回放是两个指标，分开报告，不得互相冒充。
- 候选选择规则先冻结后使用；禁止看结果后改搜索空间、种子或容差保住公式。

### 1.2 `pysr_convert`：公式转换与兼容报告

**输入**：1.1 的显式公式、变量名映射、声明域。

**输出**：逐条给出 RDS 受限 AST（供 `compass tool rank/replay` 消费），或 `UNSUPPORTED`（含原因与原表达式保留；可选等价改写建议及其域条件）。

**验收标准**

- 严格白名单：算子集即 `scripts/rds_compass_model.py:33-38`（`exp/log/sin/cos/pi`、`neg/abs/floor/ceil/sqrt`、四则、`mod/pow/min/max`、比较、`ite`）。白名单外的算子一律 `UNSUPPORTED`。
- 幂仅接受常数有理指数，分母 ≤ 8、绝对值 ≤ 8（`scripts/rds_compass_model.py:29-30,147-148`）。
- 尺寸与精度：rulebook ≤ 64 AST 节点、有理数 ≤ 512 bit（`scripts/rds_compass_model.py:25-26`）；PySR complexity 与 RDS AST 节点数不是同一量，不得换算冒充。
- 无理值以 40 位有效数字外包络表示（`scripts/rds_compass_model.py:42`），跨容差边界判 `UNDECIDED`，不得记为支持或反例。
- 等价改写（如 `x**1.3` → `exp(1.3*log(x))`）必须带声明域并重查定义域与边界；禁止静默丢项、改常数、重排变量。
- `UNSUPPORTED` 候选在最终报告里可见，不得静默排除后声称搜索完整。

### 1.3 `pysr_bind`：产物与执行绑定

**输入**：1.1 的产物、1.2 的转换报告、输入数据身份、PySR 执行命令与回执、成本分项。

**输出**：ProjectStore 记录、CAS 产物绑定、TMS 依赖；PySR 付费执行作为原项目**已声明执行**入账。

**验收标准**

- 单账本：不建旁路账本，不把重型 PySR 调用塞进未记账的 Agent 子进程，不伪装成单整数 RSI 工具（接入方案 §6.3）。
- 纯计算入口（synthesize/rank/replay）不带账本，调用它们**不等于** PySR 搜索已进预算；PySR 搜索必须经已声明执行入账后才可被后续决策消费（接入方案 §6.2）。
- 成本分项：Julia 安装、首次编译、搜索、失败重试、转换、评价、Agent token 分别列；`agent.max_tokens` 只按 Agent 自报 usage 累加且为 `INPUT_REPORTED` 口径（`scripts/rds_compass.py:802-815`），不覆盖 PySR CPU 时间，后者单独计量。
- 失败/超时 fail closed：记录部分输出与已收费成本后停止，不静默重跑；只有已结算的外层 `INTERRUPTED` 且命中预先声明的重试槽才可继续（接入方案 §8.3）。

### 1.4 `pysr_final`：独立最终评价

**输入**：冻结候选（常数已绑定为数值）、未暴露测量（账本 receipted 测量 ID，或已声明的外部评价路线）。

**输出**：原目标口径的结果（误差、反例、覆盖）、适用范围、结论状态。

**验收标准**

- 不采信候选自报 `verified`；科学状态只由原始测量经冻结 verifier 重放决定。
- 走 `compass tool holdout` 时须满足其账本前提：已初始化的 Compass 计划、已记账 hypothesis 与 `MEASURED` 状态测量、无 stale 绑定（`scripts/rds_compass_cli.py:51-73`）；返回 `CONSISTENT` 只写「与这些留出测量相容」，不写 `SUPPORT`。
- 时序要求：未暴露测量必须在候选冻结入账**之后**才开始并结算；晚导入的旧回执不算新测量（接入方案 §8.1）。
- 报告误差、反例、已测/声明覆盖、未测输入与选择过程；「仅剩一个候选」不写成发现定律。

## 2. 现状矩阵：已实现 / 尚未实现 / 不支持

| 能力 | 状态 | 依据 |
| --- | --- | --- |
| `compass tool synthesize/rank/replay` 纯计算入口 | 已实现 | `scripts/rds_compass_cli.py:20-24,36-78`；`docs/agent-rds-tools.md`「四类能力」表 |
| `compass tool holdout` 账本留出入口 | 已实现 | `scripts/rds_compass_cli.py:22,51-73`。注意 `docs/agent-rds-tools.md:26`「holdout 没有独立工具」一行已被 head 源码超越，以源码为准 |
| RDS 公式方言（超越函数、根式、有理幂、分支、取整） | 已实现 | `scripts/rds_compass_model.py:33-38` |
| 幂限制：常数有理指数，分母 ≤ 8、绝对值 ≤ 8 | 已实现（作为硬限制） | `scripts/rds_compass_model.py:29-30,147-148` |
| 64 AST 节点、512 bit 有理、40 位外包络 | 已实现（作为硬限制） | `scripts/rds_compass_model.py:25-26,42` |
| 声明级留出：默认 `holdout=0`，`holdout_seed=0` 等距、非零无放回抽样 | 已实现 | `scripts/rds_compass_declaration.py:32-39,88-96,338-344` |
| `agent.max_tokens` 预算（自报 usage 累加） | 已实现（口径为 `INPUT_REPORTED`，且上限只在调用之间检查） | `scripts/rds_compass.py:802-815` |
| jump 响应 proposals（每响应 ≤ 8） | 已实现 | `scripts/rds_compass.py:853-891` |
| 接口 1.1 PySR 候选生成脚本 | 尚未实现 | 全仓库无 PySR 适配代码；`references/theory-tools.json:87-90` 仅登记 `EXTERNAL_OPTIONAL` 外部工具并声明「never imports it」 |
| 接口 1.2 公式转换器 | 尚未实现 | 接入方案 §3 流程图注明「PySR 转换和产物消费尚未实现」 |
| 接口 1.3 产物与执行绑定层 | 尚未实现 | 接入方案 §6.2：适配层须把实际付费执行及其消费关联起来，当前无此桥接 |
| 接口 1.4 PySR→独立最终评价桥接 | 尚未实现 | 接入方案 §1：「尚未实现 PySR 适配器，未运行联合实验」；账本侧基础设施（holdout 入口、冻结 verifier、receipt 复查）已存在 |
| PySR→jump 桥接 | 尚未实现 | 接入方案 §6.3：入口已存在，受约束桥接没有 |
| 自定义 Julia 算子、任意模板、超限幂或超限常数 | 不支持 | 接入方案 §7 表；白名单见 `scripts/rds_compass_model.py:33-38` |
| `x**1.3` 之类分母 > 8 的直接幂 | 不支持（须等价改写并重查域，或走外部评价） | 接入方案 §5；`scripts/rds_compass_model.py:147-148` |
| 把 PySR complexity 当作 RDS AST 节点数 | 不支持（两种口径不可换算） | 接入方案 §7 |

## 3. 三个最可能让接入失败的场景与对策

### 3.1 指数分母限制使合法候选被静默改写

**场景**：PySR 拟出 `y = d*c**1.3`；直接幂要求有理指数分母 ≤ 8（`13/10` 分母为 10，不合法）。转换器为「尽量成功」把它改成 `4/3` 或丢项——候选从此是伪造的，后续 rank/replay 的所有结论作废且难以察觉。

**对策**：转换严格白名单 + fail closed；`UNSUPPORTED` 保留原表达式与原因；在 `c > 0` 声明域内可等价改写为 `exp(1.3*log(c))`，但必须重查 `log` 定义域与跨容差 `UNDECIDED` 边界；不改写就保留 `UNSUPPORTED` 走已声明外部评价路线（接入方案 §5）。

### 3.2 holdout 泄漏：留出点已参与拟合，或结果回灌后仍当独立确认

**场景**：PySR 拟合或选模用过的点后来传给 `compass tool holdout` 冒充「最终检验」；或 holdout 结果回灌新一轮搜索后仍被引用为独立确认。该入口只复查账本（stale 绑定、`MEASURED` 状态、计划容差），**不证明**这些点从未用于拟合、选择或调参（`scripts/rds_compass_cli.py:51-73` 无此检查；接入方案 §6.1、§8.1）。

**对策**：时序闸门——候选冻结入账之后才开始并结算的测量才可作留出；随机种子与点位看结果前冻结；holdout 结果一旦用于修订假说即降级为开发证据，本轮最终确认必须另有未暴露数据；晚导入的旧回执不算新测量。

### 3.3 搜索成本未入账：纯计算入口 ≠ PySR 搜索已进预算

**场景**：Agent 只调 synthesize/rank/replay（无账本纯计算），PySR 的 Julia 安装、首次编译、多 seed 搜索、失败重试全都不在任何账本里；`agent.max_tokens` 只按 Agent 自报 usage 累加、上限只在调用之间检查且计数是 `INPUT_REPORTED`（`scripts/rds_compass.py:802-815`）。结果：成本被系统性低估，失败后静默重跑造成重复付费，收益评估（每正确解 token）失去分母。

**对策**：PySR 调用作为原项目已声明执行入账（命令、环境与依赖版本、输入身份、种子、产物、成本 → ProjectStore/CAS/TMS）；环境准备、编译、搜索、失败、评价分项列成本；外层超时不因内部超时扩大既有 pilot/campaign 上限；失败 fail closed，记录部分输出与已收费成本后停止（接入方案 §8.3）。

## 4. 试点实施顺序与停止条件

按接入方案 §11：选低维、单位明确、可取得新条件测量的实际问题；在原项目记录一次有限 PySR 搜索 → 导出并转换少量行为不同的候选 → 经现有 rank/replay 推进一次区分实验 → 由原回执与独立最终评价判断。适配前检查干预参数、变量名、单位和输出贯通。触及预算、存在未决原运行、原件无法复核或无未暴露确认数据时，停止新增计算并报告边界。

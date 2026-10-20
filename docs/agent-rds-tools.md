# Agent 可调用的 RDS 科研工具：审计与接口草案

本页依据 RDS 6.0.0 checkout 的 `scripts/rds_cli.py`、`scripts/rds_compass_cli.py`、`scripts/rds_compass.py`、`scripts/rds_compass_model.py`、`scripts/rds_compass_declaration.py` 及现有 `docs/compass.md` 审计。目标是让 agent 按“测一点、看结果、再决定”循环使用 Compass，同时保留 RDS 的 receipt、CAS、ProjectStore/TMS 证据链约束。

## 结论

前三项 primitive 已有 CLI：

```bash
python -B scripts/rds_cli.py --root <project> compass tool synthesize --request request.json
python -B scripts/rds_cli.py --root <project> compass tool rank --request request.json
python -B scripts/rds_cli.py --root <project> compass tool replay --request request.json
```

它们读取 JSON 文件并输出 JSON。`synthesize`、`rank`、`replay` 属于无账本的纯计算；`replay` 的调用者直接提交测量行，并不会验证其来自 RDS receipt。`compass tool holdout` 读取项目账本、复查 receipt bindings 后重放指定 hypothesis 与测量，stale 或未知测量返回 `UNKNOWN`。Compass 另有 `init`、`drive`、`status`、`decide`、`report`、`tools`，其中 `drive` 是整体 campaign 执行器，不能替代 agent 主导的逐步循环。

公开 primitive 的入口在 `scripts/rds_compass_cli.py:20-24,36-55,67-70`。注意文件中 `ACTIONS` 常量没有列 `tool`，但 `add_parser` 确实注册了该 action；实际解析路径应以 parser 注册为准。

## 四类能力与当前暴露面

| 能力 | 当前可调用性 | 输入/输出与边界 |
|---|---|---|
| `synthesize` | CLI `compass tool synthesize`；也可调用纯函数 | 从 `rows` 的测量拟合 expression AST。返回 `fits`、尝试数等。候选只在给定 grammar 和 node/candidate 上界内有效；拟合不是支持结论。 |
| `rank_experiments` | CLI 名称为 `compass tool rank`；纯函数名为 `rank_experiments` | 由候选 rulebooks 对 `candidates` 的预测计算 EIG，并按 EIG/cost 排序，返回 `ranked`、`excluded`。`selection_effect` 明确是 `ADVISORY`。 |
| `replay` | CLI `compass tool replay`；另有账本内自动重放 | 纯函数按 tolerance 比较 hypothesis 与 observations，给 `CONSISTENT`、`REFUTED` 或 `UNTESTED`，反例含测量 id、预测、观测、残差。无账本 CLI 的输入不可证明为 receipted measurement。 |
| `holdout` | 没有独立工具；campaign 有受控 holdout | declaration 的 `verification.holdout` 从候选列表尾部冻结 holdout IDs。它们从 synthesis/普通实验池排除；Compass 在阶段满足时发出 `HOLDOUT` 决策，随后由 drive 处理。不是针对 arbitrary candidate 的独立 holdout API。 |

`rds_compass_model.py` 的最小内部调用签名（数值建议以十进制字符串传入，避免 JSON binary float 的歧义）：

```python
synthesize(observations, inputs, observable, tolerances, *, grammar=None,
           max_nodes=7, max_candidates=20000, probes=(), base=None, keep=4)
rank_experiments(hypotheses, candidates, tolerances, *, observed=(), weights=None, limit=8)
replay(hypothesis, observations, tolerances)
```

最低参数形状：`observations/rows=[{id, inputs:{...}, outputs:{...}}]`；`inputs` 是变量名列表；`observable` 是输出名；`tolerances` 是输出名到 tolerance 的映射；rank 还要 hypotheses（各自有 `id`、`predict` rulebook）和候选点（`{id, inputs, cost?}`）。Replay 还要已编译 hypothesis（`compile_hypothesis(value, inputs, observables, *, goal=None)` 可用来构造）及原始 observations。模型函数只操作值，不读 ledger、不运行实验、不认证 claim。

Holdout 当前没有可复用的 `model.holdout(...)` 签名。账本层的 `decide(state)` 生成 holdout 决策，`verify(store, state)` 按 receipt 重新导出观测并重放全体 hypothesis；这俩要完整 Compass store/state，不能作为无状态单点函数直接供 agent 使用。声明编译 `compile_plan(store, declaration, source_sha256, source=None)` 接受完整 declaration，并由 `verification.holdout` 选择留出候选。

## 建议接口：四个 JSON 工具

推荐使用现有 CLI 的 request-file 模式，沿用一次调用一条命令；前三项保留兼容，新增 `holdout`。CLI 原始调用形态：

```bash
python -B scripts/rds_cli.py --root <project> compass tool <synthesize|rank|replay|holdout> --request request.json
```

前 3 个 primitive 的现有 request 顶层字段可由实现确认：公共字段 `observable`、`tolerances`；synthesize/replay 用 `rows`，synthesize 还用 `inputs`；rank 用 `rivals`、`candidates`、可选 `rows`、`limit`。下面是建议稳定化的 JSON Schema（伪 schema，`$defs` 是共享定义）；现有 CLI 目前没有声明/发布 JSON Schema，因此调用前仍应按其运行时字段约束校验。

### 共享 JSON 定义

```json
{
  "$defs": {
    "scalar": {"oneOf": [{"type":"string"}, {"type":"number"}, {"type":"integer"}]},
    "point": {"type":"object", "additionalProperties":{"$ref":"#/$defs/scalar"}},
    "row": {
      "type":"object", "required":["inputs","outputs"],
      "properties":{"id":{"type":"string"},"inputs":{"$ref":"#/$defs/point"},"outputs":{"$ref":"#/$defs/point"}},
      "additionalProperties":false
    },
    "tolerance": {
      "oneOf":[{"$ref":"#/$defs/scalar"},
        {"type":"object","required":["absolute","relative"],"properties":{"absolute":{"$ref":"#/$defs/scalar"},"relative":{"$ref":"#/$defs/scalar"},"scale":{"$ref":"#/$defs/scalar"}}}]
    },
    "common": {
      "type":"object", "required":["observable","tolerances"],
      "properties":{"observable":{"type":"string"},"tolerances":{"type":"object","additionalProperties":{"$ref":"#/$defs/tolerance"}}}
    }
  }
}
```

### `synthesize`

Request：`common` + `inputs: string[]` + `rows: row[]`；可选 `grammar`（常量/运算符的显式集合）、`max_nodes`、`max_candidates`、`probes: point[]`、`base`（AST）、`keep`。

Response：```json
{"ok":true,"result":{"fits":[{"expression":{},"text":"...","size":1}],"attempted":0}}
```

`fits` 可以为空；这只表示给定 grammar/上界下无拟合。不得把它翻译成“无规律”。

### `rank`

Request：`common` + `rivals: [{id, predict: AST}]` + `candidates: [{id, inputs: point, cost?: scalar}]`；可选 `rows: row[]`、`limit`。

Response：```json
{"ok":true,"result":{"ranked":[{"id":"...","inputs":{},"cost":"1","eig_bits":0.5,"eig_per_cost":0.5,"discriminated_pairs":1,"classes":[],"undetermined":[],"partition_sha256":"...","coverage":"0"}],"ranked_total":1,"excluded":[],"selection_effect":"ADVISORY"}}
```

EIG 是区分当前 rivals 的資訊量代理，不是實驗收益或 claim 的證明；零 EIG 時應回報無區分點，不能偷偷選一個“看起来合理”的点。

### `replay`

Request：`common` + `inputs: string[]` + `law: AST` + `rows: row[]`。

Response：```json
{"ok":true,"result":{"status":"REFUTED","counterexamples":[{"id":"m1","observable":"y","predicted":"3","observed":"4","residual":"1"}],"tested":1,"not_applicable":0,"unknown_measurements":0,"scientific_support":"UNKNOWN"}}
```

当前 `replay` 接受调用方提供 rows，不核验 receipt。agent 只能把此返回当成对这份输入数据的逻辑比较；生产/结论状态必须走账本层冻结 verifier 对 receipt 绑定的原始输出重放。

### `holdout`（建议新增）

Request：必须引用项目和既有 hypothesis/holdout 测量 ID，禁止只传 agent 自己挑的行：

```json
{"project":"<root>","hypothesis_id":"h1","measurement_ids":["m_holdout_1"],"observable":"y"}
```

Response：```json
{"ok":true,"result":{"status":"REFUTED","tested":1,"unknown":0,"counterexamples":[{"measurement_id":"m_holdout_1","observable":"y","predicted":"3","observed":"4","residual":"1","receipt_sha256":"..."}],"scientific_support":"UNKNOWN","ledger_head":"..."}}
```

实现应由 RDS 从 ProjectStore/CAS 找到 measurement 和原始 receipt，并校验输出 bytes 身份后再调用 frozen verifier；调用方不得提交/覆盖 holdout output。若 receipt 丢失、摘要不符、holdout 已被用于拟合/挑选，必须返回 `UNKNOWN`/错误，不得降级成普通行 replay。返回 `CONSISTENT` 仅代表与这些留出测量相容，不代表 `SUPPORT`。

## JSON 错误协议建议

现有 primitive 没有稳定机器错误码。现有 Compass CLI 以 `exit_code` 将非完成状态映射为 2；输入异常经 CLI 错误路径输出，缺少统一 JSON envelope。建议统一：成功 exit 0、参数/schema 错误 exit 2、缺失或无效项目/measurement exit 3、receipt/CAS 完整性失败 exit 4、资源/边界超限 exit 5、内部错误 exit 6。stderr/stdout JSON：

```json
{"ok":false,"error":{"code":"INVALID_REQUEST","message":"Missing required field: rows","retryable":false,"details":{"field":"rows"}}}
```

错误码：`INVALID_REQUEST`（字段、类型、AST、tolerance 无效）、`NO_MEASUREMENTS`（synthesis 空数据）、`BOUNDS_EXCEEDED`（grammar/node/candidate/point 上界）、`PROJECT_NOT_FOUND`、`MEASUREMENT_NOT_FOUND`、`RECEIPT_INVALID`（缺失/篡改/过期）、`HOLDOUT_NOT_INDEPENDENT`（污染或已用于选择）、`BUDGET_EXCEEDED`、`INTERNAL_ERROR`。不能自动重试可能收费/执行实验的操作；这些四个分析工具都不应自行调用外部模型或买实验。

## Agent 循环伪代码

```text
state = compass status(project)                         # 读 ProjectStore/receipts
while budget_remains(state):
    observations = state.receipted_measurements
    hypotheses = synthesize(observations, declared_inputs,
                            observable, frozen_tolerances, grammar)
    if hypotheses.fits is empty:
        request_human_judgment("给定搜索界限无拟合")
        break
    rivals = preserve_distinct_fits_and_declared_rivals(hypotheses)
    ranked = rank(rivals, unmeasured_candidates, tolerances, observations)
    point = ranked.ranked[0]
    receipt = measure_once_through_RDS_execution_tool(point) # 先验声明、原始产物绑定、单 ledger 入账
    if not receipt.valid:
        stop_with_UNKNOWN(receipt)                         # 不静默重买/重跑
    replay_result = replay(candidate, observations + [receipt.original_outputs])
    state = compass status(project)                         # 状态以 receipt/ledger 重算
    if claim_ready(state):
        held = holdout(project, hypothesis_id, frozen_holdout_ids, observable)
        if held.status != CONSISTENT or held.unknown > 0:
            keep_claim_unconfirmed(held)
        else:
            submit_for_independent_verification(held)        # RDS 不自行标 SUPPORT
```

伪代码中的 `measure_once_through_RDS_execution_tool` 和 `claim_ready` 是建议的 agent-facing facade/策略步骤，不是这次审计确认存在的 `compass tool` primitive。实际方案要接现有 ProjectStore、CAS、receipt 与 frozen verifier，不能另建旁路账本。

## 使用阻碍与修复成本

| 排序 | 缺口 | 影响 | 成本 |
|---|---|---|---|
| 1 | holdout 无独立调用入口；当前 holdout 是 declaration 中的候选 ID 切分，由 drive/状态机处理 | agent 不能在自己的循环末尾明确执行“未参与拟合的留出复核”，也难以拿到一条清楚的检验结果 | 中：增加账本感知的 `holdout` primitive，绑定 receipt 并调用现有 frozen verifier，严格防止泄漏 |
| 2 | 缺少单点 measure/ingest 接口；纯 `replay` 可由调用方伪造 rows | agent 能做代数 replay，却无法把一次测量可靠地转成证据；要么手工维护 declaration/campaign，要么绕开 receipt 链 | 高：复用现有 execution contract、原始 receipt、CAS 和 ProjectStore/TMS 入账；拒绝新 ledger |
| 3 | request schema 与错误协议未稳定发布；`rank` 返回 partition/classes/undetermined/hash 等偏内部字段，replay 状态含 `UNTESTED` 且 `scientific_support` 恒为 `UNKNOWN` | LLM 容易把 advisory EIG、拟合或一致性误读成科学支持；错误响应也不利于恢复 | 低：发布 JSON Schema 和稳定错误 envelope；保留机器字段，另加简短 `interpretation`/`next_action`，明确未知与边界 |

Agent 工具带来的科研收益尚未由本接口设计证明。应把验收单独预注册为同模型、同预算对照，主指标用独立复核正确率，安全门槛是假认证率不升，成本按每个正确解 token 计；接口覆盖不等于科研增益。

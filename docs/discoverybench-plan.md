# DiscoveryBench 接入方案

日期：2026-10-20。状态：设计文档，未实现、未运行。

## 1. 资源

- 仓库：`https://github.com/allenai/discoverybench`
- 数据集：`https://huggingface.co/datasets/allenai/discoverybench`
- 许可证：ODC Attribution License (ODC-By v1.0)，要求署名。
- 评测：`eval/eval.py` 的 `run_eval_gold_vs_gen_NL_hypo_workflow`，用 GPT-4 裁判从自然语言假设里抽 `(c, v, r)` 三元组，算 Hypothesis Match Score (HMS)。

## 2. 任务 JSON 字段

| 字段 | 含义 |
| --- | --- |
| `query` | 领域科学发现目标，自然语言 |
| `domain` | 领域：sociology / biology / humanities / economics / engineering / meta-science |
| `datasets` | 数据列表，含 `name`、`description`、`columns.raw`（列名与物理含义） |
| `gold_hypo` | 论文里的真实科学结论 |
| `gold_workflow` | 论文中的验证与统计分析步骤 |

## 3. 分工红线

RDS **不生成假设**。分工：

1. **Agent**：读 `query` 和 `columns.raw`，提出互斥 rivals（H0 / H1 / H2），每个写成可检验的预测形式；
2. **RDS**：把数据集投影成 `inputs` / `observable` / `context slices`，用 EIG 选「哪个数据子集或哪次测量最能分化 rivals」，先跑 pilot 剪枝；
3. **RDS**：在物理隔离的 20% holdout 上 replay 原始数据，CAS 收据绑定统计量；
4. **Agent**：把胜出假设渲染成 `(c, v, r)` 报告，交给官方 HMS 评测。

## 4. 最小接入三步

1. **环境与数据切分**：加载 `metadata.json` 与 CSV，锁定 80% dev / 20% holdout，切分种子预注册；
2. **提案与验证闭环**：Agent 提 rivals，RDS 调度 EIG，在 holdout 上 replay 裁决；通过变异对抗与 holdout 验证的假设才标 `SUPPORT`；
3. **格式化与评测**：胜出假设渲染为 `(c, v, r)`，调用 `eval/eval.py` 算 HMS。

## 5. 边界

- HMS 由外部 GPT-4 裁判计算，RDS 的 `claim` 不直接等于 HMS；两者要分开报告。
- RDS 只对「可重放的数值预测」负责，自然语言假设的语义正确性由 Agent 与 HMS 负责。
- 20% holdout 一旦用于选假设就不能再用作最终确认；需要另留未暴露切分。
- 本文件不证明 RDS 在 DiscoveryBench 上有收益；接入后需独立对照（同模型、同预算、有/无 RDS skill）。

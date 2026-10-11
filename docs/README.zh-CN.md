# Research Direction Selector 文档

[English](README.md) · [项目首页](../README.zh-CN.md) · [贡献指南](../CONTRIBUTING.zh-CN.md)

先读[愿景与范围](rds-purpose.zh-CN.md)：我们的目的是让 AI 更好地辅助人进行科研，RDS 通过连接目标、证据、决策与执行来支撑它。再按实际需要选择入口。

| 你想…… | 从这里开始 |
| --- | --- |
| 与 Agent 一起试用 RDS | [快速入门](quickstart.zh-CN.md)、[CPU 回执示例](../examples/autoresearch-receipts/README.md) |
| 理解科研决策闭环 | [科研工作流](research-workflow.zh-CN.md)、[程序持有的 Advisor](program-owned-advisor.md) |
| 修订失败路线并接续 | [问题结构](problem-structure.md)、[有界研究推进](autonomy-loop.md)、[确定性接续](deterministic-continuation.md) |
| 查询支持的命令或安装方式 | [命令图](command-map.md)、[宿主插件](host-plugins.md) |
| 判断证据与长期声明 | [科研自主程度](research-autonomy.md)、[基准协议](../benchmark/README.md)、[RSI 证据](../references/rsi-evidence.md) |

## 指南目录

希望一起建设这个闭环？[加入 RDS](RECRUITING.zh-CN.md)列出科研、工程、评测与文档的具体参与方向。

这些指南说明 checkout 中有文档记录的接口。带日期的提案、历史对照版本和基准报告保留原范围，不能当作当前整个发布版的验收报告。使用前核对版本、[预览说明](releases/5.9.0-rc.2-preview.md)与相应命令帮助。

| 指南 | 简体中文 | English |
| --- | --- | --- |
| 愿景、当前入口与进展证据 | [愿景与范围](rds-purpose.zh-CN.md) | [Vision and scope](rds-purpose.md) |
| 5.8 提案：目标、架构、状态和可分工事项 | [5.8 完整目标与协作计划](5.8-vision.zh-CN.md) | [5.8 vision](5.8-vision.md) |
| 已有自主分级与当前实现范围 | [原框架与当前能力](research-autonomy.md) | [Research autonomy](research-autonomy.md) |
| 科研职责与单次实验流程 | [科研工作流](research-workflow.zh-CN.md) | [Research workflow](research-workflow.md) |
| 声明、证书与验证范围 | [形式化验证](formal-verification.zh-CN.md) | [Formal verification](formal-verification.md) |
| 复用 Lean4/mathlib 的深度学习科研扩展 | [Lean 兼容](lean-integration.zh-CN.md) | [Lean integration](lean-integration.md) |
| 构建固定原生库与检查条件统计律 | [原生 Lean 与统计](lean-native.md) | [Native Lean and statistics](lean-native.md) |
| 23 节点的前置门禁、可行域与证伪条件 | [规则义务](rule-obligations.zh-CN.md) | [Rule obligations](rule-obligations.md) |
| 统一术语与准确的代码标识 | [术语表](terminology.zh-CN.md) | [Terminology](terminology.md) |
| 复现材料、证据与 PR | [贡献指南](../CONTRIBUTING.zh-CN.md) | [Contributing](../CONTRIBUTING.md) |
| 具体改法、交付物和通过条件 | [开发与验收计划](roadmap.md) | [Development plan](roadmap.md) |
| 真实记录、项目执行、规则采用与开发反馈 | [工具与开发反馈循环](development-loop.md) | [Tool and development loop](development-loop.md) |
| 开放问题模型、拓扑分支与负反馈 | [问题结构](problem-structure.md) | [Evaluation scope](problem-structure-evaluation.md) |
| 有价值的并行批次、闲置容量与预算语义 | [资源利用与并行规划](resource-planning.md) | [Resource-aware planning](resource-planning.md) |
| 研究截止时间、进度监视与维护运行 | [停止策略](stop-policy.md) | [Stop policy](stop-policy.md) |
| 宿主命令钩子、准入身份与绕过范围 | [宿主命令钩子](host-hook.md) | [Host hook](host-hook.md) |
| 回执绑定支持与保留 OR 路线的撤回 | [超图证据绑定](hypergraph-evidence.md) | [Hypergraph evidence](hypergraph-evidence.md) |
| 深度学习验证器、Mathlib 与 Lean 4 接入方案 | [Lean 生态与接入综述](lean4-ecosystem-survey.md) | [Ecosystem survey](lean4-ecosystem-survey.md) |
| 张量算子等价、E-Graph、黎曼与无限维形式化路线 | [张量算子形式化路线](tensor-operator-formalization.zh-CN.md) | [Tensor operator formalization](tensor-operator-formalization.md) |
| 同模型 skill 对比与基准选型 | [基准选型与对比方案](benchmark-plan.md) | [Benchmark plan](benchmark-plan.md) |

[可执行契约](../references/l3-state-machine.md) 定义所用 checkout 的运行行为，[SKILL.md](../SKILL.md) 定义科研协作协议。二者不一致时应报告差异，不能把协议要求解释为程序已实现的保证。

首页示例面向有文档说明的 checkout。数学接口按[形式化验证指南](formal-verification.zh-CN.md)核对，保留实际后端、状态与 assurance。可选依赖不可用或检查被跳过，不能证明对应路径已经验证。

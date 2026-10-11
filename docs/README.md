# Research Direction Selector documentation

[简体中文](README.zh-CN.md) · [Project homepage](../README.md) · [Contributing](../CONTRIBUTING.md)

Start with [vision and scope](rds-purpose.md): our purpose is better AI assistance for human research, and RDS connects goals, evidence, decisions and execution to support it. Then follow the workflow you need.

| You want to… | Start here |
| --- | --- |
| Try RDS with your agent | [Quick start](quickstart.md), [CPU receipt example](../examples/autoresearch-receipts/README.md) |
| Understand the research decision loop | [Research workflow](research-workflow.md), [owned Advisor](program-owned-advisor.md) |
| Change a failed approach and continue | [Problem structure](problem-structure.md), [bounded research drive](autonomy-loop.md), [deterministic continuation](deterministic-continuation.md) |
| Find a supported command or installation path | [Command map](command-map.md), [host plugins](host-plugins.md) |
| Assess evidence and long-term claims | [Research autonomy](research-autonomy.md), [benchmark protocol](../benchmark/README.md), [RSI evidence](../references/rsi-evidence.md) |

## Guide catalogue

Want to help build the loop? See [Join RDS](RECRUITING.md) for research, engineering, evaluation and documentation contributions.

These guides describe the checkout's documented interfaces. Dated proposals, comparison revisions and benchmark reports retain their original scope; they are not a current release-wide acceptance report. Check the revision you use, its [preview notes](releases/5.9.0-rc.2-preview.md), and the relevant command help.

| Guide | English | 简体中文 |
| --- | --- | --- |
| Vision, current entry points and evidence needed for progress | [Vision and scope](rds-purpose.md) | [愿景与范围](rds-purpose.zh-CN.md) |
| Proposed 5.8 goals, architecture, status and collaboration packages | [5.8 vision](5.8-vision.md) | [5.8 完整目标与协作计划](5.8-vision.zh-CN.md) |
| Published autonomy taxonomy and current implementation scope | [Research autonomy](research-autonomy.md) | [原框架与当前能力](research-autonomy.md) |
| Research responsibilities and experiment flow | [Research workflow](research-workflow.md) | [科研工作流](research-workflow.zh-CN.md) |
| Declarations, certificates and verification scope | [Formal verification](formal-verification.md) | [形式化验证](formal-verification.zh-CN.md) |
| Reuse Lean4/mathlib for a deep-learning research extension | [Lean integration](lean-integration.md) | [Lean 兼容](lean-integration.zh-CN.md) |
| Build the pinned native library and audit conditional statistical laws | [Native Lean and statistics](lean-native.md) | [Native Lean and statistics](lean-native.md) |
| Preconditions, feasible domains and falsifiers for all 23 nodes | [Rule obligations](rule-obligations.md) | [规则义务](rule-obligations.zh-CN.md) |
| Shared language and exact code identifiers | [Terminology](terminology.md) | [术语表](terminology.zh-CN.md) |
| Reproducers, evidence and pull requests | [Contributing](../CONTRIBUTING.md) | [贡献指南](../CONTRIBUTING.zh-CN.md) |
| Concrete changes, deliverables and acceptance checks | [Development plan](roadmap.md) | [开发与验收计划](roadmap.md) |
| Real records, locked project runs, rule replay and RDS self-development | [Tool and development loop](development-loop.md) | [工具与开发反馈循环](development-loop.md) |
| Open problem models, experimental topology branches and negative feedback | [Problem structure](problem-structure.md) | [Evaluation scope](problem-structure-evaluation.md) |
| Useful parallel batches, idle capacity and budget semantics | [Resource-aware planning](resource-planning.md) | [资源利用与并行规划](resource-planning.md) |
| Campaign deadline, progress watchdog and maintenance runs | [Stop policy](stop-policy.md) | [停止策略](stop-policy.md) |
| Host command hook, admission identity and bypass coverage | [Host hook](host-hook.md) | [宿主命令钩子](host-hook.md) |
| Receipt-bound support and OR-surviving retraction in the dependency map | [Hypergraph evidence](hypergraph-evidence.md) | [超图证据绑定](hypergraph-evidence.md) |
| DL verifiers, Mathlib and Lean 4 integration options | [Ecosystem survey](lean4-ecosystem-survey.md) | [Lean 生态与接入综述](lean4-ecosystem-survey.md) |
| Tensor-operator equivalence, E-Graphs, and Riemannian/infinite-dimensional formalization | [Tensor operator formalization](tensor-operator-formalization.md) | [张量算子形式化路线](tensor-operator-formalization.zh-CN.md) |
| Same-model skill comparison and benchmark selection | [Benchmark plan](benchmark-plan.md) | [基准选型与对比方案](benchmark-plan.md) |

The [execution contract](../references/l3-state-machine.md) defines the runtime in the checkout being used. [SKILL.md](../SKILL.md) defines the research protocol. When the two differ, report the inconsistency instead of interpreting a protocol instruction as an implemented guarantee.

Homepage examples target the documented checkout. For mathematical interfaces, consult [formal verification](formal-verification.md) and retain the actual backend, status and assurance. Unavailable optional dependencies or skipped checks do not establish validation of those paths.

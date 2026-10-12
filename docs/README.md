# Research Direction Selector documentation

[简体中文](README.zh-CN.md) · [Project homepage](../README.md) · [Contributing](../CONTRIBUTING.md)

Start with [vision and scope](rds-purpose.md): our purpose is better AI assistance for human research, and RDS connects goals, evidence, decisions and execution to support it. Then follow the workflow you need.

| You want to… | Start here |
| --- | --- |
| Make your first change to this repository | [Onboarding](onboarding.md) |
| Try RDS with your agent | [Quick start](quickstart.md), [CPU receipt example](../examples/autoresearch-receipts/README.md) |
| Understand the research decision loop | [Research workflow](research-workflow.md), [owned Advisor](program-owned-advisor.md) |
| Change a failed approach and continue | [Problem structure](problem-structure.md), [bounded research drive](autonomy-loop.md), [deterministic continuation](deterministic-continuation.md) |
| Find a supported command or installation path | [Command map](command-map.md), [host plugins](host-plugins.md) |
| Assess evidence and long-term claims | [Research autonomy](research-autonomy.md), [benchmark protocol](../benchmark/README.md), [RSI evidence](../references/rsi-evidence.md) |

## Current guides

Want to help build the loop? See [Join RDS](RECRUITING.md) for research, engineering, evaluation and documentation contributions.

These guides describe the checkout's documented interfaces. Dated proposals, comparison revisions and benchmark reports retain their original scope; they are not a current release-wide acceptance report. Check the revision you use, its [preview notes](releases/6.0.0-rc.1-preview.md), and the relevant command help.

| Guide | English | 简体中文 |
| --- | --- | --- |
| First run: clone the checkout, run a focused test, open your first pull request | [Onboarding](onboarding.md) | [上手指南](onboarding.zh-CN.md) |
| Vision, current entry points and evidence needed for progress | [Vision and scope](rds-purpose.md) | [愿景与范围](rds-purpose.zh-CN.md) |
| Published autonomy taxonomy and current implementation scope | [Research autonomy](research-autonomy.md) | [原框架与当前能力](research-autonomy.md) |
| Research responsibilities and experiment flow | [Research workflow](research-workflow.md) | [科研工作流](research-workflow.zh-CN.md) |
| Declarations, certificates and verification scope | [Formal verification](formal-verification.md) | [形式化验证](formal-verification.zh-CN.md) |
| Reuse Lean4/mathlib for a deep-learning research extension | [Lean integration](lean-integration.md) | [Lean 兼容](lean-integration.zh-CN.md) |
| Build the pinned native library and audit conditional statistical laws | [Native Lean and statistics](lean-native.md) | [Native Lean and statistics](lean-native.md) |
| Preconditions, feasible domains and falsifiers for all 23 nodes | [Rule obligations](rule-obligations.md) | [规则义务](rule-obligations.zh-CN.md) |
| Shared language and exact code identifiers | [Terminology](terminology.md) | [术语表](terminology.zh-CN.md) |
| Reproducers, evidence and pull requests | [Contributing](../CONTRIBUTING.md) | [贡献指南](../CONTRIBUTING.zh-CN.md) |
| Real records, locked project runs, rule replay and RDS self-development | [Tool and development loop](development-loop.md) | [工具与开发反馈循环](development-loop.md) |
| Open problem models, experimental topology branches and negative feedback | [Problem structure](problem-structure.md) | [Evaluation scope](problem-structure-evaluation.md) |
| Useful parallel batches, idle capacity and budget semantics | [Resource-aware planning](resource-planning.md) | [资源利用与并行规划](resource-planning.md) |
| Campaign deadline, progress watchdog and maintenance runs | [Stop policy](stop-policy.md) | [停止策略](stop-policy.md) |
| Host command hook, admission identity and bypass coverage | [Host hook](host-hook.md) | [宿主命令钩子](host-hook.md) |
| Receipt-bound support and OR-surviving retraction in the dependency map | [Hypergraph evidence](hypergraph-evidence.md) | [超图证据绑定](hypergraph-evidence.md) |
| DL verifiers, Mathlib and Lean 4 integration options | [Ecosystem survey](lean4-ecosystem-survey.md) | [Lean 生态与接入综述](lean4-ecosystem-survey.md) |
| Tensor-operator equivalence, E-Graphs, and Riemannian/infinite-dimensional formalization | [Tensor operator formalization](tensor-operator-formalization.md) | [张量算子形式化路线](tensor-operator-formalization.zh-CN.md) |

### On-demand skill procedures

The [skill entry](../SKILL.md) retains authorization, UNKNOWN, budgets, canonical
campaign discovery, recovery and execution boundaries. Select a procedure only
for its matching operation; loading a procedure grants no authority and does not
initialize a project. Existing command and guide links remain usable.

| Operation | Procedure |
| --- | --- |
| Show a graph or explain a node, then return without research planning | [Read-only display](../references/skill-routes/display.md) |
| Retain a plan/checkpoint or apply current-human steering | [Planning and steering](../references/skill-routes/planning-steering.md) |
| Initialize after discovery or activate Advisor in an existing QUICK project | [Campaign setup](../references/skill-routes/campaign-setup.md) |
| Plan, advance, drive, consume tools or repair within an owned project | [Owned loop](../references/skill-routes/owned-loop.md) |
| Respond to a blocked decomposition or structure/jump packet | [Problem structure](../references/skill-routes/structure.md) |

## Proposals and evaluation plans

These documents contain goals, designs or prospective checks. Their presence
does not establish implementation, authorize experiments or accept a release.
Retain each document's dated baseline and distinguish its current interfaces
from its remaining proposal or evaluation scope.

| Scope | Document |
| --- | --- |
| Proposed 5.8 goals and collaboration packages | [5.8 vision](5.8-vision.md), [简体中文](5.8-vision.zh-CN.md) |
| Development and acceptance plan, including retained 2026-09-30 milestones | [Development plan](roadmap.md) |
| Deferred comparison plan and benchmark selection | [Benchmark plan](benchmark-plan.md) |
| Cross-task implementation and review requirements | [Cross-task acceptance](cross-task-acceptance.md) |
| Prospective structure evaluation; real model trials remain NOT_RUN | [Structure evaluation](problem-structure-evaluation.md) |

## Historical reviews and scoped evidence

These records preserve their original revision, date and workload. Historical
passes, preview notes and fixtures do not establish acceptance of the current
checkout or scientific benefit. Files stay at their original paths so existing
links and evidence locators remain valid.

| Record | Document |
| --- | --- |
| Dated 5.8 release review state and unresolved criteria | [5.8 release review](5.8-release-review.md) |
| Preview scope and evidence for each candidate | [5.9.0-rc.1](releases/5.9.0-rc.1-preview.md), [5.9.0-rc.2](releases/5.9.0-rc.2-preview.md) |
| Bounded 2026-10-01 design/prior-art review | [Guardrails review](guardrails-prior-art-20261001.md) |
| Original autonomy engineering checks and their acceptance limits | [Autonomy validation](autonomy-validation.md) |
| Scoped Advisor fixture and public-task results | [Advisor benchmark](advisor-benchmark.md), [frontier benchmark](advisor-frontier.md) |
| Retained preparation failures and recovery boundaries | [Output recovery](n13-output-recovery.md) |

The [execution contract](../references/l3-state-machine.md) defines the runtime in the checkout being used. [SKILL.md](../SKILL.md) defines the research protocol. When the two differ, report the inconsistency instead of interpreting a protocol instruction as an implemented guarantee.

Homepage examples target the documented checkout. For mathematical interfaces, consult [formal verification](formal-verification.md) and retain the actual backend, status and assurance. Unavailable optional dependencies or skipped checks do not establish validation of those paths.

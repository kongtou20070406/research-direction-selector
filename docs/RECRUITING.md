# Join RDS: open-source contributors and research collaborators

[简体中文](RECRUITING.zh-CN.md) · [Project homepage](../README.md) · [Contribution guide](../CONTRIBUTING.md)

**Help AI better assist people in scientific research.** We welcome open-source contributors and research collaborators who want to work toward that purpose.

Useful research assistance means helping a person clarify a question, compare explanations, design a fair experiment and understand what the result supports. It also means retaining failed approaches and the reasons for a decision, so the researcher can inspect, correct and continue the work.

Research Direction Selector (RDS) is the implementation form we have chosen. You can contribute a research case, an evaluation method, code, an interface improvement or a tutorial. Start with a small result you can check; you do not need to understand every component to participate.

## What we are building

RDS connects researchers and AI assistants such as Codex and Claude Code through a local decision and execution system. Researchers set the question, goals, resources and scientific acceptance. AI contributes hypotheses, analysis and experiment proposals; Advisor connects the declared goal to evidence and candidate routes; the kernel preserves execution, costs, failures and recovery. See [vision and scope](rds-purpose.md).

We want to improve the usefulness of AI assistance in real research: clearer comparisons, conclusions that reflect the evidence, less repeated work and continuity across sessions. Those benefits need evaluation with researchers and independent task evidence. Reporting a confusing suggestion, a burden RDS adds or a case where it does not help is valuable work.

## Why participate

- **Improve assistance where researchers need it.** Turn a difficulty in asking, comparing, interpreting or continuing into a concrete improvement you can evaluate.
- **Produce something others can inspect.** A reproducible failure, a grader, a checked adapter or a clear tutorial gives the next contributor a starting point.
- **Combine research and engineering.** Problem formulation, controlled comparisons, Python execution, transactional state and formal checks meet in the same workflow.
- **Start at the level that suits you.** A clearer installation step or a small public case can be useful before a new algorithm or adapter.

## Directions you can join

| Your interest | A concrete first contribution | Starting material |
| --- | --- | --- |
| **Research workflows and scientific methods** | Turn a failure you have seen into a minimal public or synthetic case: original question, competing explanations, observed failure and the test that distinguishes them. | [Research workflow](research-workflow.md), [problem-structure evaluation](problem-structure-evaluation.md) |
| **Independent evaluation** | Build a task with a known answer and a grader; compare the assistance under matched conditions, recording decision quality, researcher effort and all outcomes. Separate agent-only tests from studies with researchers. | [Benchmark contribution guide](../CONTRIBUTING.md#contribute-a-benchmark-task), [benchmark protocol](../benchmark/README.md) |
| **Advisor and problem reformulation** | Reproduce a case where missing evidence, a changed prerequisite or a rejected route should change the next decision; improve the affected scoped behavior. | [Owned Advisor](program-owned-advisor.md), [problem structure](problem-structure.md) |
| **Execution kernel and recovery** | Build a small interruption or duplicate-call reproducer that checks original receipts, attempts and spent budget; repair a confirmed failure. | [CPU receipt example](../examples/autoresearch-receipts/README.md), [development loop](development-loop.md) |
| **Mathematics and domain adapters** | Choose one precise statement, supported backend and minimal input; retain the actual certificate, counterexample or `UNKNOWN` with its assumptions. | [Formal verification](formal-verification.md), [domain confirmation](domain-confirmation.md) |
| **Human–AI interaction and usability** | Test whether a researcher can understand a recommendation, inspect its evidence and correct it; improve a confusing workflow or installation step. | [Quick start](quickstart.md), [host plugins](host-plugins.md) |
| **Docs, translation, tutorials and design** | Fix one misleading claim or broken step, translate a guide, or make a tutorial that shows an original result changing the next action. | [Documentation](README.md), [contribution guide](../CONTRIBUTING.md) |

You can contribute a case, explanation, tutorial or test report without changing the kernel. Use material you may publish, and retain the relevant failure behavior when making a synthetic example.

## How to start

1. Follow the [two-minute start](../README.md#try-it-in-two-minutes), then read the guide for the direction that interests you.
2. Browse [open issues](https://github.com/kongtou20070406/research-direction-selector/issues). Check the current discussion, related PRs and who is working on the item before starting. An open issue is not automatically unclaimed or ready to implement.
3. For a typo or a focused reproducible bug, a direct PR is welcome. Discuss a larger feature, schema or research-behavior change in an issue first. State the problem, smallest useful scope and how you will check it.
4. Submit the result with what changed, why it matters, the verification you actually ran and any remaining uncertainty. A useful negative result or reproducer is a contribution even when you have no fix.

If you are unsure where to begin, [open an issue](https://github.com/kongtou20070406/research-direction-selector/issues/new/choose) with a short introduction:

```text
I want to contribute to RDS.
My interests or skills:
A research or software problem I have seen:
A small result I could produce:
How I would check it:
```

## Working together

AI-assisted contributions are welcome; the contributor remains responsible for understanding the change and checking the result. Report actual commands, environment, outcomes and limits. Read logs and artifacts before sharing them, and keep credentials, private sessions and research data out of public records.

Respect existing authors and agreed scope. Be kind when disagreeing, and use evidence to resolve technical questions. The [contribution guide](../CONTRIBUTING.md) is the home for detailed implementation, review, evidence and licensing rules.

## Contact

Use a relevant existing issue, a PR discussion, or the [new-issue entry](https://github.com/kongtou20070406/research-direction-selector/issues/new/choose). Describe what you want to work on and the smallest useful first result.

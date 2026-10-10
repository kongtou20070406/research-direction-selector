# RDS in autoresearch workflows

RDS uses evidence-driven autoresearch to help AI better assist the human researcher. It connects the researcher's question to the next decision: agents and researchers propose experiments, Advisor reviews evidence and eligible routes, and the local kernel carries out permitted work while preserving its costs and results. The [vision and scope](rds-purpose.md) describe the longer-term direction-selection goal. A useful result must still address the research question; bookkeeping and a successful process do not establish scientific benefit.

## From a question to a recorded next step

1. State a hypothesis, a control, a distinguishing observation and the available resources with your agent.
2. Bind the permitted command, inputs, evaluator and resource allowance before execution. For a campaign whose next route depends on results, use a frozen [owned Advisor policy](program-owned-advisor.md).
3. Execute a bounded job through the CLI. Read its original outputs and receipt, including failures and timeouts; an identical wrapped request reads the existing job instead of launching it again.
4. Interpret the evidence with the researcher. Continue with the next permitted step, propose a testable revision when an approach fails, or retain an explicit `UNKNOWN` when evidence is missing. [Problem-structure exploration](problem-structure.md) and [bounded research drive](autonomy-loop.md) provide scoped entries for reformulation and method repair; revisions preserve the original goal and accounting.

Start with the [CPU receipt example](../examples/autoresearch-receipts/README.md). It compares a constant and a line on the same six recorded points, using the existing project-runner fixture. The original CLI runs each arm once and then checks identical-call reuse. Its small comparison is an observed fixture result, not a research benchmark or evidence that an agent becomes a better scientist.

## Relation to Karpathy's autoresearch

[Karpathy's autoresearch](https://github.com/karpathy/autoresearch) lets an agent edit a compact single-GPU language-model training program, run a fixed five-minute training budget, assess validation bits per byte and keep or discard changes. Its `program.md` supplies the agent instructions; the training-time budget excludes startup and compilation. Consult that repository for its current hardware and setup requirements.

RDS supplies a local command wrapper, explicit reservations, bound inputs, execution receipts and recovery records. A researcher could use those capabilities around an authorized training command, after binding the actual code, data, evaluator, outputs and resource limits. This repository is an independent project; it is not an autoresearch fork or an official integration, and the CPU example does not test a Karpathy-specific adapter or a GPU training loop.

## Three separate kinds of evidence

| Record | What it supports | What remains open |
| --- | --- | --- |
| Execution receipt | The bound process ran, its status, outputs and recorded costs | Whether a metric improved, a hypothesis holds or the work was useful |
| Imported metric and comparison | A value read from the original bound artifact, within its stated data and protocol | Generalization, causal explanation and independent confirmation |
| Supported verification report | The declared statement checked by the named backend, with its actual status and assurance | Unchecked assumptions, code-to-model correspondence and claims outside that statement |

For a separate CPU verification entry point, from the repository checkout run:

```powershell
python -B scripts/rds_cli.py --root ../rds-autoresearch-demo formal verify --spec examples/formal/affine_dynamics.json
```

Read `status`, `assurance`, assumptions and checked bounds in the original report. This finite affine fixture is separate from the line-fitting comparison; verifying it does not prove the experiment's hypothesis. Other verification requests can fail or remain `UNKNOWN`. See [formal verification](formal-verification.md), [research workflow](research-workflow.md) and [autonomy scope](research-autonomy.md) for supported paths and limits.

## Cite the software and preserve the experiment

Use [CITATION.cff](../CITATION.cff) for software attribution, and retain the actual RDS commit, input/evaluator identities, commands, receipts and original logs with your experiment. The citation file identifies the contributors as a collective software author. It declares no paper, DOI, ORCID or release date. Citation metadata and discoverability keywords are not scientific acceptance.

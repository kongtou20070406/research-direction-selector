# Rolling global planning from current evidence

For an initialized program-owned project, read:

```text
python -B scripts/rds_cli.py --root <project> project plan --shadow
python -B scripts/rds_cli.py --root <project> project plan --shadow --goal <frozen-goal-fact>
```

The first command projects the current final goal, open milestones, available
complete execution plans, local alternatives, unresolved prerequisites and
replanning requests. The second focuses the incoming dependency cone on one
original goal fact. Both reread owned evidence and perform a final state identity
check. They do not save a plan, update TMS, reserve resources or launch work; the
ordinary CLI usage record still applies. `--intent`, `--output` and `--save-as`
remain options for the separate initial `project plan` draft.

`project next` and owned result reception also include `shadow_plan` in the
existing report/CAS. `advise --working-set --brief` retains a compact summary,
comparison and original report locator. Read omitted originals before choosing a
consequential action. Every projection is rebuilt against the current evidence
cut; a previous plan is not automatically adopted.

## How an agent uses the plan

1. Read the open final predicates and the producers that can supply their original
   measurements. A local certificate or SUCCEEDED process can leave other AND
   premises open. The current predicate comparison determines milestone status.
2. Compare declared complete plans and local actions. Complete plans reuse the
   existing feasibility model, including ordered solve/verification steps,
   recovery/delivery overhead and UNKNOWN forecasts. A first step can link to a
   later goal through that declared complete plan; a bounded pilot can link to
   the forecast it is intended to resolve. These links remain conditional.
3. Read `next_small_check` alongside the original Advisor choice. The shadow
   heuristic prefers eligible actions linked to more open goals, then declared
   distinguishing pairs, then the smaller hard wall allowance, then declaration
   order. Its `comparison` states any disagreement. Hard allowance is not a
   completion prediction or a calibrated value-of-information score.
4. Consume `replan_requests`. If only local actions exist, propose a complete
   route; if fewer than two complete strategies exist, propose a materially
   different method. State its unknown premises, result-dependent decisions and
   the first small falsifying check. Do not fill the missing alternative with an
   invented fact or treat two parameter settings as established strategic diversity.
5. If a goal has only terminal producers, inspect their original output and the
   missing bridge, then propose an evidence repair, verifier or changed method.
   Retain original failures. Use the existing authorized structure/method-revision
   interfaces for proposals and admission. A jump request grants no new tool,
   route, budget or execution permission.
6. After real feedback, reread the plan and save a consequential decision through
   the existing checkpoint workflow. Retain earlier source/report identities for
   forecast-versus-observation review. Prefer current user steering and
   reconciliation of original active work before a new suggestion.

The goal cone follows incoming AND/OR premises from the selected goal nodes. It
does not send a growing archive into a second planner or perform a whole-graph
route search. The output gives input and included counts, limits, explicit
truncation and original locators/hashes. Source search or dependency analysis
truncation requests scope inspection; it cannot establish exhaustion,
impossibility or a best global route. Long records use a hash and exact locator
instead of cutting predicates into different meanings.

## Scope of the evidence

This is an evidence-bound planning aid with an explicit heuristic. It derives
questions and candidate priorities from existing declarations; a model still has
to propose new scientific explanations and independent checks still have to test
them. It does not attest that a declared plan will succeed, establish scientific
truth, change the original selected manifest or certify optimality. Eligible
alternatives have passed the existing prerequisite/resource/feasibility/steering
filters; only the original selected manifest can acquire execution admission.
Human pause, priority, active attempts, budget, method and feasibility gates retain
precedence. Missing collection or a concurrent state change returns UNAVAILABLE.

The public software tests cover disagreement with the original local choice,
real feedback closing only part of an AND goal, exhausted producers, preserved
collection failures, human steering, scoped large dependencies and conditional
complete plans. They contain no paid model calls or scientific efficacy trial.
The inspected n=13 trajectories motivate the design and remain development
material; mathematical acceptance stays UNKNOWN. Scientific benefit requires a
future prospective comparison with the same model, information, allowed tools
and total cost across bare, current-RDS and shadow-planner-assisted arms.

See [owned Advisor](program-owned-advisor.md), [feasibility](predictive-feasibility.md),
[working sets](advisor-working-set.md) and [structure proposals](problem-structure.md).

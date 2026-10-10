# Research discipline: detailed contracts and optional modes

Read only the sections needed for the current decision. The [skill entrypoint](../SKILL.md) retains the core rules; [local decision workflow](../docs/lightweight-workflow.md) explains ledger-backed anti-loop review. Advisor uses existing SQLite checkpoints, not a standalone Markdown note. Obelisk preflight is optional enhanced-history setup, not a requirement for the research loop.

Sections: evidence and execution boundaries; decision contract; direction program and control reuse; evidence and human intervention; output contract; CLI execution; conditional formal verification; historical evaluation. These details remain available without loading every mode for every question.

## Evidence and execution boundaries

The reference CLI executes restricted scalar rational ASTs and paired MSE comparisons. The separate `project` runner executes exact authorized argv commands under locked code, configuration, data, evaluator and protocol files, collecting raw logs and receipts. Both separate task gain, mechanism evidence and run status. A project command is trusted code, not an OS sandbox; exit code 0 does not certify a scientific claim. Do not import handwritten success flags or substitute toy outcomes for real experiments. Read [the reference contract](l3-state-machine.md) or [project and development tools](../docs/development-loop.md) for the corresponding runner.

For mathematical declarations, use the separate [formal framework](formal_framework.md).
It checks supported affine matrix/dynamical, dense Linear/ReLU network and concrete
tensor claims, and composes named theorems through registered trusted rules.
A checked mathematical property belongs to the declared model and input domain;
it does not promote task gain, mechanism or research policy evidence.

## Start with the decision contract

Extract from current project files and the user's instructions. Record caller-managed structured decisions through `checkpoint save --decision` in the existing `.rds` SQLite ledger. Program-owned Advisor projects must record decisions through the native `project next`/`project advance` flow, which binds checkpoints to the reserved run; manual decision checkpoint saves are rejected there. Supply the matching question ID, goal revision, scope and facts in `--research-context`; Advisor checks checkpoint identity and contract binding before pruning unchanged rejected routes. Changed facts or conditions reopen review; A→B→A produces a warning. These checks authenticate recorded choices, not scientific conclusions or execution authority. Exact historical wording, parameters or numbers may use the optional existing Obelisk CLI bridge; see [history enhancement](obelisk.md). Do not mirror chats or build a source index, embedding database, second archive or parallel state machine. Fill missing low-risk details internally; ask only when a material goal or cost choice cannot be inferred.

```yaml
GOAL:
  claim: "exact proposition, including quantifiers and required mechanism"
  primary: {metric: null, direction: max, dataset: null, split: null, evaluator: null}
  selection: {development_metric: null, checkpoint_rule: null, final_test_rule: null}
  data_use: []             # each partition's role and prior exposure as of this decision
  baseline: {method: null, recipe: null, compute: null}
  deployment_inputs: []
  protected: []             # properties the claimed method must actually retain
  budget: {remaining_wallclock: null, measured_throughput: null, evaluation_overhead: null}
  success: null              # minimum useful improvement, fixed before inspecting results
  evidence_refs: []          # original logs, code, data, papers
HUMAN:
  acts:                    # one utterance may contain both a proposal and a direction
    - content: null
      likely_intent: proposal | instruction | uncertain
      material_effect: none | reversible | changes_goal_or_large_budget
  changed_priority_or_constraint: null
```

Do not make the human fill this form. Construct it internally and expose only fields that affect the recommendation. If the formal evaluation is absent, propose a minimal protocol before proposing architecture changes. A development/validation metric may select candidates; the locked final test supports the final claim. A test set repeatedly used for choice is no longer independent confirmation.

Keep method restrictions attached to the user's original wording and affected step. Distinguish generating a candidate, checking it, and proving the complete claim; do not expand a ban on one into a ban on all computation. A material unresolved interpretation calls for one short human clarification before dependent work, while unaffected work continues. Store the actual answer alongside the original source in the current decision context; neither historical text nor external rules grants new resources or a weaker proof standard. The optional [method-scope review](../docs/agent-entry.md#clarify-method-limits) checks explicit declarations without interpreting prose.

Record when each data partition was created, trained on, viewed, or used for model selection. Data already used to train a checkpoint cannot become independent validation for that checkpoint by splitting it afterward; retrain from an appropriate starting point with the partition held out. If no clean confirmation set is available, label the current result exploratory and specify what independent confirmation would be needed.

## Direction program

Read relevant nodes of [judgment-graph.yaml](judgment-graph.yaml) as **scoped corrections to intuition**, never universal performance laws. When comparing RSI mechanisms, also read [rsi-evidence.md](rsi-evidence.md). Use source dates and seek later primary evidence before transferring a paper's claim into a new domain.

```text
ENTRY := sourced anomaly | open mechanism question | metric plateau |
         missing comparison/transfer | human proposal/instruction
DECISION := what the next result must change: method, claim, or experiment priority

if HUMAN has a clear authorized instruction:
    execute it; design its fair test; report any changed protocol/claim
else:
    GENERATE internally >=3 causally distinct routes, not three wordings:
      a plausible task-gain change, a rival representation/mechanism,
      and a test of the most consequential alternative explanation;
      replace an irrelevant category with another genuinely distinct route.
    for each route r:
      predict target-metric and mechanism observations under H1 vs H2;
      name the exact property P that separates H1 from H2;
      derive from the executed equation/code the property range in each arm;
      specify a measurement showing whether the intervention actually changes P;
      write NEXT_IF_POSITIVE(r) and NEXT_IF_NEGATIVE(r);
      estimate full-run cost from observed throughput, remaining wall-clock,
      and evaluation overhead; identify a fair comparator.
    discard r if both outcomes leave the next decision unchanged.

HARD_REJECT(r) if it silently changes GOAL.claim/primary metric,
  uses unavailable deployment information, bypasses GOAL.protected,
  has no feasible fair comparison under the authorized budget,
  or the claimed route is absent from the executed computation graph.

EXPLORATORY_SIGNAL := oracle | proxy | toy | short run | reused development set
Use an exploratory signal to motivate or cheaply screen a route;
never promote it to a confirmed task gain or mechanism claim.

MECHANISM_GATE(r): if treatment and comparator stay on the same side of P,
  or the claimed change is erased by the executed graph, the result does
  not decide P. Narrow the claim to the property actually changed or
  redesign the intervention. A task-metric comparison can still be useful,
  but it cannot acquire a mechanism interpretation by naming the knob.

CHOOSE a nondominated route or compatible parallel batch by its expected advancement of GOAL.primary
  AND the value of knowledge that changes the next scientific decision,
  then by evidence quality and total cost. Cheapest is a tie-breaker,
  not the objective. A decisive diagnostic can win when it prevents
  an expensive wrong branch; a promising structural change can win when
  a small diagnostic would not change the choice.

COMPARE schedules as well as individual runs: available capacity, elapsed
  time to the next useful result, total resource consumption and marginal
  charges are different quantities. A lower GPU-hour total does not dominate
  a faster answer unless the relevant trade-offs are comparable. Use actual
  scaling evidence; four GPUs do not imply a fourfold speedup for one job.
  Fill available capacity with independent, decision-relevant work when it
  fits the authorized window and budget. Do not batch a dependent follow-up
  before its deciding result, duplicate an equivalent experiment, consume
  protected confirmation capacity, or ignore CPU/VRAM/I/O interference.
```

Before recommending a material run, compare its whole cost with the remaining authorized budget, including the comparator and evaluation. If the cap or throughput is unknown, give a bounded first stage with a measurement and stop rule, or make the larger run explicitly conditional on a feasibility calculation. Do not treat a qualitative statement that time is finite as authorization for a particular long schedule. A clear human command still follows the instruction rule above; state any feasibility conflict and the resulting trade-off.

Available hardware has an opportunity cost when a useful eligible task could have used it before a deadline. Track unused capacity and its reason separately from billed cost; do not turn every idle device-second into a claimed monetary loss. Missing prerequisites, insufficient memory, dependencies, contention, protected confirmation work and no useful candidate can justify waiting. Existing or prepaid hardware can favor parallel work over a cheap serial plan, while on-demand cloud devices can favor releasing an unused allocation. Neither maximal utilization nor minimal expenditure is the scientific objective. When several independent experiments are justified, prefer an executable batch over a long queue of individually cheap probes. See [resource-aware planning](../docs/resource-planning.md).

### Baseline Control Reuse Principle (空白对照复用原则)

Prefer a completed compatible control over rerunning it. The scalar reference cache checks control AST and data bytes. For a training control, use `project control-check` to compare code/configuration/data hashes, partition, initial state, seed, checkpoint, schedule, sample-work and numerical protocol, and reread hash-bound output artifacts. Identical seeds do not establish identical random paths. Missing identity or changed inputs invalidate reuse; a cache hit does not restore independent confirmation. Hardware and software changes that can affect results belong in the numerical protocol.

For a one-shot request, return **one recommended direction** and at most one serious alternative. State the competing explanation, prediction that separates them, fair baseline, precommitted selection and final test rules, exact metric, budget, reproducibility artifacts, and the result that would stop or revise the idea. Do not rank by novelty, elegance, or apparent mechanism alone. Treat task gain and mechanism knowledge as separate outcomes: either can change the next research decision, while only a fair primary-metric gain supports a performance claim.

## Evidence and human intervention

Track evidence by proposition, not one label for a whole project: `TASK_GAIN` (exploratory/confirmed/refuted), `MECHANISM` (hypothesis/supported/refuted), and `SEARCH_POLICY` (trial/trajectory-confirmed/refuted). Keep `PAPER_REPORTED` separate from local evidence. A published gain on another benchmark is a trial rationale, not a confirmed local gain. Confirm task gain with a predeclared, fair primary-metric comparison and untouched confirmation. Reuse compatible completed controls and prefer a matched-seed comparison. Do not propose a multi-seed campaign by default: consider additional seeds only after observed seed instability could change the decision, and bound its incremental cost. Match the claim to the evidence actually collected. Support a mechanism with a reproducible intervention that distinguishes it from plausible alternatives; its effect can be measured even if the proposed model does not beat the baseline. Confirm a research-search policy only by comparing whole trajectories at equal total budget. A failed local test updates the scope or removes the relevant proposition. Preserve original results and falsifiers through the user's existing system only when asked; this skill does not maintain that record.

Human intervention is allowed at proposal, experiment design, priority, budget, and interpretation. Split mixed utterances into their separate acts. Infer each act's intent from the full conversation, wording, prior authorization, and the cost of acting; do not decide it from a command verb or question mark alone. Use this decision rule internally:

```text
if clear_instruction: execute within authorization;
    if protocol changed: version the claim/metric/inputs and report comparability;
elif likely_proposal: test against goal + raw evidence;
    if contradicted: identify the specific conflict, give one better option or deciding test;
    else: keep it in contention and advance the highest-value useful step;
else: do safe, reversible research now;
    ask at most one focused question only if a material goal/budget choice blocks the next step.
```

The model corrects an **evidence-conflicting proposal**, not the person. Require a specific violated claim, input boundary, result, or reproducibility rule; model confidence alone is insufficient. A counterintuitive human idea that survives these checks stays in contention. A clear command, including one the model would not have recommended, is carried out; the model may briefly state a consequential protocol difference, but must not reclassify the command as advice or make the human defend it. Never present an unconfirmed result as confirmed.

When the human changes priorities, recompute the choice under the new priorities. When the human challenges a graph node, show its applicability condition, best counterexample, and discriminating test; revise or retire it if evidence warrants. Neither human preference nor model preference can silently change an evidence label.

For low-friction collaboration, lead with the conclusion or completed action, then the one piece of evidence that changes the decision. Use natural short prose, no form for the human to fill, no repeated caveats or serial approvals. Surface only decisions that need the researcher's judgment. This adapts the public [Opus 5.5 communication examples](https://www.anthropic.com/claude-opus-5-5), [Opus 5.5 system prompt](https://platform.claude.com/docs/en/release-notes/system-prompts/claude-opus-5-5), and [Anthropic's account of genuine helpfulness](https://www.anthropic.com/constitution); it is not a claim to reproduce that model's behavior.

## Output contract

```yaml
recommendation: {change: null, why_this_goal: null, competing_explanation: null}
test: {prediction: null, baseline: null, budget: null, primary_metric: null,
       manipulation_check: null, selection_rule: null, final_confirmation: null,
       reproducibility: [],
       next_if_positive: null, next_if_negative: null}
evidence: {task_gain: untested | exploratory | confirmed | refuted,
           mechanism: hypothesis | supported | refuted,
           search_policy: trial | trajectory_confirmed | refuted}
human_intervention: {editable: [goal, constraints, priority, budget],
                     disagreement: null, decision_needed: null}
```

## Deterministic CLI Engine (`scripts/rds_cli.py`)

Use the reference commands below for the supported scalar protocol. For an external project, use the separate `project` commands and its real raw artifacts. Do not claim that a scalar reference receipt verified an external experiment.

When developing RDS itself, **use its programs throughout the development iteration**: record the concrete problem and next decision, run the actual regression workload through `project`, import its original outputs through `artifacts`/`advise --artifacts`, checkpoint the decision, and use `meta evaluate-rule` before adopting a rule. Repair observed shortcomings and use the repaired tools in the next iteration. This development feedback loop is part of RSI; passing software regressions alone does not establish improved scientific research policy. The reproducible [self-development example](../examples/self-development/run.py) and [tool guide](../docs/development-loop.md) provide the commands.

```bash
# 1. Initialize research contract & lock SHA-256
python -B scripts/rds_cli.py init --contract contract.json

# 2. Register hypothesis with pre-committed falsifier
python -B scripts/rds_cli.py hypothesis add --spec hypothesis.json

# 3. Check plan against deterministic manipulation, budget & data leakage gates
python -B scripts/rds_cli.py gate check --plan plan.json

# 4. Lock compliant experiment plan
python -B scripts/rds_cli.py plan create --spec plan.json

# 5. Execute the locked plan; use its returned RUN-... identity
python -B scripts/rds_cli.py run execute --id P1
python -B scripts/rds_cli.py decide --run RUN_ID

# 6. Audit live research tree, branch statuses, and budget ledger
python -B scripts/rds_cli.py status

# 7. Inspect candidates, replay declared cases, then adopt on a bound graph
python -B scripts/rds_cli.py meta list-rules
python -B scripts/rds_cli.py meta validate-rule --rule rule.json
python -B scripts/rds_cli.py meta evaluate-rule --rule rule.json --graph isolated-graph.json --cases cases.json --output evaluation.json
python -B scripts/rds_cli.py meta apply-rule --rule rule.json --graph isolated-graph.json --cases cases.json --evaluation evaluation.json
python -B scripts/rds_cli.py meta rollback-rule --record adoption-record.json --graph isolated-graph.json
python -B scripts/rds_cli.py meta reflect [--terms "topic_query"]

# 8. RSI Step 2: Policy Stagnation & Orthogonal Branching (FML-Bench v2)
python -B scripts/rds_cli.py branch status
python -B scripts/rds_cli.py branch fork --spec branch.json
python -B scripts/rds_cli.py branch switch --id branch_id
python -B scripts/rds_cli.py branch list

# 9. Adversarial proposals, heuristic rule linting and repair candidates
python -B scripts/rds_cli.py meta fuzz --plan plan.json
python -B scripts/rds_cli.py meta evaluate-alignment --rule rule.json
python -B scripts/rds_cli.py meta auto-repair [--dry-run]

# 10. Compact training telemetry (no tokenizer-specific compression guarantee)
python -c "from rds_compress import compress_training_log; print(compress_training_log(open('train.log').read()))"
```

## Conditional formal verification

### Standalone declarations and trusted theorem rules

Write the exact mathematical statement in a supported `schema: 1` declaration.
Use `scripts/rds_verify.py` (`verify`, `check_certificate`, `rules`) or the CLI:

```bash
python -B scripts/rds_cli.py --root . formal rules
python -B scripts/rds_cli.py --root . formal verify --spec declaration.json --output proof.json
python -B scripts/rds_cli.py --root . formal check --spec declaration.json --certificate proof.json
```

The framework separates proof generation from certificate checking and rechecks
the full declaration and engine binding on every cache hit. Named modules reuse
declared models with `$ref`; theorem `by.rule` must match a registered rule for
that statement. Cycles, unbound references, unsupported declarations and unknown
rules produce `UNKNOWN`, not a theorem. Do not treat a nonempty rule string or a
YAML judgment-graph node as a trusted inference rule.

Record `PASS`, checked `FAIL`, and `UNKNOWN` separately. Their CLI exit codes are
0, 1, and 2, including `formal check`; checking a valid refutation does not make
the claim pass. An interval network bound that cannot establish the property is
`UNKNOWN` unless a real exact input violates it. Concrete tensor equalities say
nothing about all possible symbolic tensors.

The PyTorch adapter exports only exact eval-mode `Sequential`/`Linear`/`ReLU`
classes with finite parameter snapshots and checked shapes. Hooks, overrides,
parametrizations, unknown layers and arbitrary checkpoint loading are outside
this adapter. Binary parameter ratios denote exact real values; float execution
and arbitrary `forward` equivalence need separate evidence. Do not install a
dependency merely to hide an optional-backend test skip.

Native Lean supports generated closed rational obligations through an explicitly
configured existing binary. Require `LEAN_KERNEL_CHECKED` and an empty axiom audit
for that subtask; Python certificates remain `CERTIFICATE_CHECKED`. Broader
mathlib model translation, ONNX and alpha-beta-CROWN are preparation work. Before
transferring their research or guarantees, consult their
latest primary sources and the [framework's backend boundaries](formal_framework.md).

### Research-run mathematical side conditions

Use `hypothesis.formal: {kind: declarative, statement: ...}` for a supported
mathematical side condition. Admission proves it before reserving compute, and
execution replays the committed certificate. The receipt's
`declared_side_condition_only` scope does not establish equivalence to the runner
or a training graph. Assessment records `formal_status`, sets `manipulation` to
`NOT_APPLICABLE` and leaves mechanism `NOT_TESTED`. Preserve this distinction when
using a proof to select the next experiment.

Do not default to multiple seeds. Consider a budgeted stability experiment only
after observed seed instability could change the research decision.

### Research-run scalar admission

Declare `hypothesis.formal` only when the hypothesis claims a strict algebraic threshold, contraction boundary or dynamical property. Ordinary parameter comparisons and routine experiments omit it and take the lightweight AST path; a numeric hyperparameter or PSNR target is not a formal claim. Do not omit a real mathematical claim to bypass its gate.

The exact adapter separates certificate generation from checking for bounded
affine-rational scalar expressions. The checker validates original denominator
obligations, the universal control bound and an exact treatment crossing witness;
it reports `CERTIFICATE_CHECKED`. Other supported scalar expressions may fall back
to SymPy and remain `SYMBOLIC_CHECKED`. Neither result is a Lean certificate or a
general neural-network stability proof. Unsupported research-run dynamics, singularities,
resource limits and unavailable required solvers return `UNKNOWN`.

Use `formal.statement: threshold_necessity` only for the explicit scalar necessity
model. The default `threshold_separation` establishes feasibility and cannot
refute an unrelated proposition. Execution still has to observe a crossing; a
feasible mathematical witness is not an experimental result. Proof replay binds
the source, formal specification and engine version and checks the certificate
again, while atomic budget and exposure checks remain mandatory.

Advisor output and rule linting are `HEURISTIC_ONLY`. Do not present a scalar-loss
heuristic as a capacity, optimization or generalization diagnosis. Rule lint has
no measured precision or false-positive rate. `meta auto-repair` returns reviewable
candidates from the current ledger; it does not automatically validate or apply
scientific rules. Preserve raw telemetry and original evidence behind summaries.

## Historical evaluation

Use [the five-case benchmark](../benchmark/README.md) for retrospective decision evaluation and `python -B benchmark/run.py` for executable guard regressions. Keep sealed later outcomes out of proposer inputs. Passing scalar guard tests does not establish autonomous research performance. Model roles may be assigned when requested; no particular model or multi-agent delegation is required by this skill.

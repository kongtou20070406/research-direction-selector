# Owned planning, execution and repair

Use only when an existing project has advisor_policy and an owned planning, advance, drive, tool or repair operation is needed.

This procedure assumes the already-loaded root policy, `skill_dir`, `project_root`,
`CLI`, current original evidence and existing authorization. Reading this file
does not initialize state or authorize execution. Read only the applicable
section and its needed guide; reuse unchanged resources already read.

## Owned planning and execution

```text
    IF project has advisor_policy:
        RUN advise --working-set --brief FROM the current ledger and owned dependency graph
        READ shadow_plan.summary and comparison BEFORE a consequential local choice
        USE project plan --shadow [--goal <frozen-goal-fact>] FOR current final milestones, alternatives and jump requests
        READ docs/global-shadow-planning.md WHEN proposing a missing global route or off-route discriminator
        KEEP shadow priority = heuristic proposal; original selection, steering and admission retain authority
        READ working_set goals, final selection, unresolved hypotheses and scoped feedback
        IF jump_packet or structure jump/drive agent_context is present:
            READ its new premises, representation changes, rival predictions and original evidence
            USE these as inputs to the next explanation, experiment or permitted code revision
            EXPLICITLY adopt/adapt/reject/defer each supplied idea with a reason and concrete next_step
            BIND packet_sha256 and item IDs in the next decision checkpoint or returned jump_use_json
            READ omitted originals first; receipt of a packet is not evidence of scientific benefit
        IF working_set is UNAVAILABLE: inspect diagnostic/original report; do not infer a fresh choice
        READ original/details_omitted locators BEFORE a consequential evidence-dependent choice
        KEEP process failure distinct FROM prediction REFUTE; legacy/unbound evidence stays UNKNOWN
        READ selection, coverage gaps/errors and the saved report when needed
        IF advisor_policy declares autonomy:
            READ docs/autonomy-loop.md
            RUN project drive --max-steps <bounded-pass> TO recover and continue the same ledger
            IF jump-generation.json is frozen, drive prepares and collects its selected generator routes
                RETURN verified jump evidence with execution feedback to each actual model request
                KEEP ancestor hypotheses historical after a method revision; do not relabel their support
            TREAT obstacles as inputs for different permitted routes or method/tool proposals
            CONSUME original repair/rejection receipts; do not repeat uncertain delivery
            KEEP scoped exhaustion distinct from scientific impossibility
            REQUIRE program-replayed domain confirmation before claiming goal completion
        ELSE:
            RUN project advance --brief FOR at most one program-selected authorized step
        FOR either policy, optionally continue several already frozen steps WITH project drive --until-judgment
            WITH explicit --controller-wall-seconds FROM existing wall budget for ordinary owned projects
            OR the frozen autonomy allowance; READ docs/deterministic-continuation.md
            CONSUME the exact stop, original evidence and settled resources; NEVER infer new authority
        KEEP original receipt + automatic Advisor update; unknown remains UNKNOWN
        EXPLAIN the actual observation against the frozen prediction/control and evaluation scope
        SAY which explanation it supports or weakens, what remains unknown and what to test next
        KEEP failed, negative and resource-limited outcomes visible; do not infer REFUTE from process failure
        CONTINUE the next admitted step without a default card, menu or repeated approval
        IF advisor_policy declares tool_bindings:
            READ tool_utilization FOR obligation applicability, actual calls and decision consumers
            USE only qualified current-input candidates within frozen routes and budget
            KEEP UNKNOWN and unconsumed outputs visible; do not optimize for command counts
            READ docs/owned-tool-consumers.md FOR the finite native application interface
        IF collection failed: recover collection; NEVER repeat completed training
        IF execution/delivery is uncertain: reconcile original attempt before any replacement
        FOR cross-session Windows work: use the available Task Scheduler runner within its contract
        DO NOT promise durable long-job event wakeups; see docs/experiment-assistant.md for planned boundaries
```

## Blocked method

```text
        IF predictive plan is blocked or a method failed:
            READ docs/predictive-feasibility.md and the returned repair_request
            RUN project improve FOR an already authorized code path
            USE receipt diagnostics + source hints TO create/improve a tool
            EDIT candidate; refresh improve; adopt project revise in the same ledger
            REQUIRE fresh bounded pilots + unchanged independent goal verification
            KEEP failed costs, attempts, exposure and original campaign deadline
            IF no authorized viable method remains: REPORT that outcome
        NEVER replace context/graph/choice OR bypass with quick/theory allowance
```

## Operation guides

- [Owned Advisor](../../docs/program-owned-advisor.md)
- [Global shadow planning](../../docs/global-shadow-planning.md)
- [Autonomy loop](../../docs/autonomy-loop.md)
- [Deterministic continuation](../../docs/deterministic-continuation.md)
- [Owned tool consumers](../../docs/owned-tool-consumers.md)
- [Blocked method repair](../../docs/predictive-feasibility.md)

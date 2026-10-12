# Campaign setup and Advisor activation

Use only when discovery confirms no project or an existing QUICK project needs owned Advisor.

This procedure assumes the already-loaded root policy, `skill_dir`, `project_root`,
`CLI`, current original evidence and existing authorization. Reading this file
does not initialize state or authorize execution. Read only the applicable
section and its needed guide; reuse unchanged resources already read.

## New campaign after discovery

```text
    IF new goal-driven campaign:
        DEFAULT project init TO FULL; require a valid explicit advisor_policy or recipe
        USE project init --recipe <recipe.json> FOR explicit finite routes and qualified tool consumers
            TO assemble hashes/protocol/route associations; see docs/project-assembly.md
        RETAIN project init --contract FOR advanced policies; a prose plan is not a recipe
        FREEZE the actual objective, permitted routes, result readers and budget
               in advisor_policy; see docs/program-owned-advisor.md
        RETAIN serious alternatives/explanations and their distinguishing observations
        USE project init --mode quick --contract <contract.json> only for a deliberately limited workflow
        READ workflow.mode, advisor and capabilities plus the stderr mode banner
        FULL owns collection/selection; optional autonomy, confirmation and tools need their own declarations
```

## Enable Advisor in an existing QUICK project

```text
    IF reusing an existing QUICK project that now needs owned Advisor:
        PREVIEW project enable-advisor --policy <policy.json>
        APPLY only the reviewed policy with --apply --expected-snapshot <snapshot_sha256>
        KEEP genesis contract, old runs/receipts/checkpoints and cumulative budget in the same ledger
        NEVER replace the frozen contract or infer extra execution authority from activation
```

## Operation guides

- [Project assembly](../../docs/project-assembly.md)
- [Frozen contracts](../../docs/project-contract.md)
- [Owned Advisor](../../docs/program-owned-advisor.md)
- [Lifecycle and discovery](../../docs/project-lifecycle.md)

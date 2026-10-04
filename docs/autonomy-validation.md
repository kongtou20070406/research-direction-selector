# Research drive validation scope

The change is tracked in [#198](https://github.com/kongtou20070406/research-direction-selector/issues/198)
and builds on the owned tool/history consumers in
[#197](https://github.com/kongtou20070406/research-direction-selector/pull/197).
The underlying workbench and method-revision behavior already comes from
[#190](https://github.com/kongtou20070406/research-direction-selector/pull/190).
New behavior is bounded continuous drive, original request binding and model
repair triggering, recovery of adoption, explicit controller cost ownership,
and program-replayed domain completion gates. Independent merge/main acceptance
is separate from the engineering evidence described here.

## Requirements and executed checks

| Requirement | Check and actual disposition |
| --- | --- |
| Dynamic repair request is an owned input | Real route/worker/receipt, reserved-CAS tamper, immutable provider and executable checks |
| Failed or uncertain model work is not blindly repeated | Actual local provider exit, malformed response and timeout; original attempts/receipts/fees remain unchanged |
| A rejected method is feedback for another method | First local proposal rejected, next distinct slot adopted, original paid attempt retained |
| Adoption can survive interruption | Crash after ADOPTED and PREPARED partial-copy recovery; no second provider call |
| Repeated passes do not reset limits | Original deadline, budget, step count and receipts; control reservations and measured fees checked |
| Death with no new budget is recoverable | One conservative dead-owner settlement across two blocked admissions; no worker/provider and no stranded reservation |
| Model self-report cannot complete a task | Independent original certificate replay rejects unsigned PASS; domain UNKNOWN cannot become GOAL_CONFIRMED |

The controller test module has 16 cases. A complete frozen execution through
`examples/self-development/run.py --test-pattern test_rds_autonomy.py` passed
all 16 with no skips. This uses the actual RDS CLI, original receipt, checkpoint
restoration, artifact import, cost/Advisor output and bounded rule replay.
Its isolated demonstration-rule apply/rollback is not acceptance or merging of
this feature's source. Earlier development failures are retained privately.

The domain module has 12 checks over actual ProjectStore workers. Default
Python passed with one optional torch skip. An existing Python 3.12 torch
environment passed all 12 without skips, including 150 actual CPU SGD steps
and independent recomputation from the saved weights. Mathematics uses exact
certificate replay; algorithms use finite integer oracle and original matched
input identities/costs. Missing torch remains UNKNOWN without installation.

## Public finite trajectories

The [example](../examples/autonomy/run.py) freezes public source, claim, evaluator,
input data, commands and budget. Its default provider is explicitly a local
fixture. Mathematics has four original run/receipts; the finite algorithm has
five, including a baseline; CPU deep learning has four. A later drive pass
preserves completed attempt IDs and original receipt hashes. Control allowances
can yield a short pass; remaining work continues in the same ledger.

The mathematical trajectory reaches task confirmation PASS for the exact
declared affine-rational statement. The algorithm trajectory reaches task PASS
for its finite integer cases. DL recomputes MSE from trained CPU weights but
ends DOMAIN_CONFIRMATION_UNKNOWN because the current exposure model declares
the confirmation data exposed. All three scientific-support claims remain
UNKNOWN; none estimates general policy gain or speedup.

## Real provider smoke test

One separately authorized request used the actual installed Codex provider,
GPT-6.1 Sol/high/Standard, with a read-only child and fixed structured response
schema. The original request, provider trace, invocation argv, response,
revision, and all five run receipts remain in its private frozen project.
The model proposed multiplicity compression and reuse of squared magnitudes.
The existing structural revision gate adopted it, after which the candidate,
baseline and independent integer oracle completed with GOAL_CONFIRMED.
Recovery retained all original receipts and exactly one provider dispatch.

This is a real model-triggered repair smoke test on supplied public finite
inputs, not an unseen task effectiveness benchmark. Counting/caching can cost
more for mostly distinct values; correctness on these cases does not establish
universal correctness or improved runtime. Original trace token measurements
are distinct from the project's wall-resource cap. Inherited provider hooks or
external integrations need their own host acceptance; fixture checks do not
prove coverage of them.

Applicable full-suite, platform, native and formal CI results must be assessed
at the resulting PR head. This report does not substitute a helper PASS for
those shared-kernel checks, release acceptance or scientific evidence.

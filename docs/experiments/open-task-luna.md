# Open-task Luna evaluation

Status: protocol only. No model call has been made. The result is `UNKNOWN`, not zero.

## Arms

Same model, same task text: `provider=magpie-responses`, `model=codex/gpt-6-luna`.
At most 4 calls per arm. No network, no retry, no O-HAZE, no GPU training.

- Bare arm: task text only.
- RDS arm: task text plus the current card. Answer the card, then submit one command.

The scorer is a fixed local script. Its SHA256 is part of the criterion. Neither arm may edit it.

## Reading

Primary measure: input tokens plus output tokens.

- A missing usage field makes that arm `UNKNOWN`. The comparison must not say RDS used fewer tokens.
- An arm that called no model is `INVALID_ARM`.
- Repeated executions and the final score are secondary.
- Do not say one arm is better unless a noise band was written before the run.

Result JSON belongs in `docs/experiments/`. It is not part of the test suite.

# Inspect original trajectory costs and reported outcomes

`project trajectory` reads explicitly supplied provider responses, host tool events,
evaluator outputs and RDS execution receipts. It produces JSON suitable for export:

```powershell
python -B scripts/rds_cli.py --root <export-directory> project trajectory --manifest trajectory.json
```

The directory need not be an initialized research project. This command does not
dispatch tools, contact a provider, discover host sessions, initialize a research
ledger or modify execution budgets. Ordinary CLI usage logging still applies.
The existing `project costs` command remains the execution-resource report.

This is the first engineering slice of [#239](https://github.com/kongtou20070406/research-direction-selector/issues/239).
It reconciles supplied records. Complete research cost, independently verified
scientific outcomes and research-policy improvement remain unknown. Provider calls
that never acquired a response ID are unresolved records, not zero-cost attempts.

## Minimal manifest

Save a final, non-streaming OpenAI Responses API response as `response.json` and
an original evaluator record as `evaluation.json`. Use only exports you are
authorized to inspect; usage counters do not require hidden reasoning content.
Compute the SHA-256 of each file's bytes and fill the source references below.
The goal, evaluator and evaluation-protocol identities are declared comparison
anchors; their hashes do not certify an evaluator's correctness or independence.

```json
{
  "schema": "rds-trajectory-manifest-v1",
  "goal": {
    "goal_id": "original-task",
    "evaluator_sha256": "<64 hexadecimal characters>",
    "protocol_sha256": "<64 hexadecimal characters>",
    "started_at": "2026-10-08T00:00:00Z"
  },
  "sources": [
    {"id": "model", "path": "response.json", "sha256": "<file SHA-256>"},
    {"id": "evaluation", "path": "evaluation.json", "sha256": "<file SHA-256>"}
  ],
  "providers": [
    {"source": "model", "pointer": "", "format": "openai-responses",
     "namespace": "provider-account-export", "phase": "planning"}
  ],
  "outcomes": [
    {"source": "evaluation", "pointer": "", "fields": {
      "goal_id": "/goal_id", "evaluator_sha256": "/evaluator_sha256",
      "protocol_sha256": "/protocol_sha256", "artifact_sha256": "/artifact_sha256",
      "status": "/status", "observed_at": "/observed_at", "score": "/score",
      "delivered": "/delivered", "incorrect_claims": "/incorrect_claims",
      "human_rescues": "/human_rescues"
    }}
  ],
  "tools": [],
  "receipts": [],
  "coverage": "Declared export scope; omitted preparation and human work remain unknown"
}
```

All source and manifest paths must resolve within `--root`. Sources are strict
UTF-8 JSON (BOM accepted), at most 2 MiB each, with no duplicate keys, non-finite
numbers or nesting beyond 32 levels. A report reads each unique file once. At
most 64 sources and 1,024 record selectors are accepted. A selector's `pointer`
is an RFC 6901 JSON pointer to one original object, including an element of an
array export. There is no automatic scan, arbitrary expression or code evaluator.
Keep JSONL/streaming conversion outside this first adapter and disclose it; this
command cannot reconcile partial streaming usage as final paid-attempt usage.
Selected provider/tool/outcome report records are limited to 32 KiB each; the
complete formatted report is limited to 16 MiB. Oversized records retain an error
and cannot silently contribute a passing outcome or a complete cost total.

## Provider usage

Supported `format` values are `openai-responses`, `openai-chat` and
`anthropic-message`. Each reads the original response `id`, `model`, status and
`usage`. The optional provider `fields` maps `host`, `effort`, `tokenizer` and
`latency_seconds` to pointers relative to that response. Missing identities,
usage fields and measurements are `null`/`UNKNOWN`; missing counters are never
assumed to be zero. No local tokenizer is invoked to fill gaps.

OpenAI input/output totals include their cached/reasoning subsets. Those
breakdowns stay separate and are not added again. Anthropic's input total is
uncached input plus cache reads plus cache creation; an absent component keeps
that total unknown. These adapters follow the original
[OpenAI response usage fields](https://developers.openai.com/api/reference/resources/responses/methods/retrieve)
and [Anthropic cache accounting](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
This implementation neither converts tokens to FLOPs nor guesses prices.

An attempt identity is `(provider, namespace, response id)`. The namespace is a
declared account scope: reuse the same scope across recovery, files and segments;
do not rename it per export. Equal duplicate records count once and retain all
locators. Different records for the same identity are excluded from totals and
listed as conflicts. A missing identity remains an error instead of being assigned
a made-up paid attempt. Distinct retries with distinct response IDs count even if
their status is failure or timeout. Incomplete/inconsistent usage stays unknown.

The `phase` is declared attribution, one of `generation`, `retrieval`, `planning`,
`mechanical`, `execution`, `verification`, `recovery`, `rework` or `UNKNOWN`.
Provider phase totals do not infer stages from prose. Summed provider latency can
overlap across concurrent calls; it is not elapsed campaign time. Per-record
model, host, effort, tokenizer and source locators remain available for comparison.

## Host tools and RDS resources

A `tools` selector uses `source`, `pointer`, a stable host/session `namespace`,
optional `phase`, and `fields` mapping these names to original host pointers:

| Field | Meaning |
| --- | --- |
| `call_id` | Original unique tool-call identity; required |
| `origin` | Original `llm` or `internal`; required, never inferred from CLI counts |
| `round_trip_id` | Host round-trip identity shared by calls dispatched in one batch |
| `status` | Original outcome, including failures |
| `schema_tokens`, `returned_tokens` | Non-overlapping per-call counters, not bytes; require a recorded `tokenizer` |
| `format_error`, `retry`, `regenerated_code` | Original booleans; missing differs from false |

Map existing fields; no new host receipt or runtime state is required. If the host
has only round-level material totals, do not copy them onto each tool call. That
would count the same material repeatedly. Missing round-trip identities keep the
round-trip total unknown. Internal steps are counted separately. Existing host
batching is a valid comparison baseline. Recovery/duplicate exports follow the
same conflict rules as provider records.

`receipts` selectors need only `source` and `pointer`. They pass original RDS
receipts to the existing `rds_costs.summarize_costs`, preserving failed attempts,
charged estimates, original identities and absent CPU/GPU/API measurements.
Receipt resources and provider counters are **not added together**: they can
describe overlapping work. Source locators accompany the existing cost report.
No input changes the original ledger's spent or reserved budget.

## Outcomes and interpretation

Outcome fields are read through the explicit mapping in the example. Goal,
evaluator and protocol identities must match the declared goal; `artifact_sha256`
is required. `status` accepts `PASS`, `FAIL` or `UNKNOWN`. Scores remain separate
original objects, preserving their metric/unit information instead of combining
unrelated quality scales. Delivery, incorrect claims and human rescues stay
source-recorded observations, with missing values unknown.

Duplicate artifacts under the same goal/evaluator/protocol count once even when
copied to another export. Conflicting verdicts are retained as conflicts and
removed from accepted counts. `seconds_to_first_reported_pass` uses timezone-aware
original timestamps and the declared start. Missing, inconsistent or conflicting
times leave it unknown. `supplied_tokens_per_reported_pass` is descriptive only:
it divides known supplied provider tokens by distinct evaluator-reported passing
artifacts. It is not cost per independently verified scientific result.

The report's overall status is `PARTIAL`, or `CONFLICT` when duplicate identities
disagree. Coverage is always `SUPPLIED_RECORDS_ONLY`; unlisted attempts and
overhead cannot be counted or proven absent from a manifest. Field totals expose
both a known subtotal and an unknown complete value where appropriate. An empty
category is unknown, not evidence of zero work. Exit zero means the report was
produced, not that coverage is complete or the research succeeded. Malformed
manifests reject; individual source/record failures remain visible in the report.

Regression fixtures exercise the actual CLI and synthetic native-format exports,
including cache overlap, failure, timeout, recovery duplicates, concurrent reads,
conflicts, source tampering, wrong evaluator, batching and bounds. They do not
constitute live provider measurement or a cheap/frontier model comparison. Such
a comparison still needs prospective thresholds, matched information/tools,
independent evaluation and an explicitly authorized total budget.

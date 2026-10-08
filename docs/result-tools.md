# Reusable finite result tools

The data-only functions in [`scripts/rds_result_tools.py`](../scripts/rds_result_tools.py)
move repeated result reading into deterministic Python. They use the existing
`rsi extract` → `validate` → `register` → `prepare-application` → owned execution
and [decision consumer](owned-tool-consumers.md), with no new registry or command.
They do not open paths, execute log text, install packages or call a model.

| Function | Input | Returned observation |
| --- | --- | --- |
| `extract_json_result(raw_text, pointer, identity)` | Original JSON text, RFC6901 pointer and source identity | One scalar and its original pointer, or `UNKNOWN` with a reason. |
| `compare_metrics(candidate, baseline)` | Two objects containing `value`, `identity` and optionally the extraction `status` | Descriptive `delta = candidate - baseline` and direction-normalized `improvement`, only when identities are compatible. |
| `summarize_failures(raw_text, identity, limit=16)` | The existing self-development test result JSON | Full declared/observed counts and a bounded list of exact case IDs, trace/reason fields and original pointers. |

Source identity requires `run_id`, `source_path`, and lowercase `source_sha256`.
It may also retain `code_sha256` and `config_sha256`. Comparison additionally
requires matching `data_sha256`, `data_split`, `evaluator_sha256`, `metric`,
`metric_definition`, `reduction`, `unit`, and `direction` (`minimize` or
`maximize`). Run/source/code/config identities can differ between arms and are
retained. Reusing one run ID for different source hashes is conservatively
`UNKNOWN`; use distinct result identities or inspect the original multi-output
run. No statistical significance, uncertainty estimate or mechanism is inferred.

Identity is explicitly **caller metadata**. A function does not verify a file
hash merely because the caller supplied it. Bind the original files, exact call
inputs, evaluator cases and code through the existing execution contract; inspect
the original receipts and result consumer. The example does so for every source.
Same-named identity claims at the JSON root or inside `binding`, `protocol` or
`identity` must agree with supplied metadata. These are claims about the result
namespace: retain upstream lineage separately instead of labelling it as the
current file's own hash. Extra input flags such as `verified=true` cannot certify
the source or change `scientific_support=UNKNOWN`.

Missing/null/`UNKNOWN` metadata, conflicting identity, missing/null values,
unresolved extraction status and non-numeric metric values never become a zero,
win or scientific success. Booleans are not numeric metrics. Malformed hash
syntax, duplicate JSON keys, non-finite numbers, invalid pointers/Unicode and
unsupported identity fields raise `ValueError`. Raw JSON is limited to 2 MiB,
32 levels and 20,000 nodes. Array indices use canonical ASCII decimal notation.
Compound extraction results remain `UNKNOWN` with an instruction to expand the
original. Text values above 2,048 UTF-8 bytes are omitted explicitly, not truncated
and presented as complete evidence.

Failure inventory uses the real development format: `case_count`,
`failure_count` (including test errors), `skipped_count`, `failures` entries with
`case_id`/`trace`, and `skipped` entries with `case_id`/`reason`. Absent inventories
stay `UNKNOWN`, while explicit empty lists with consistent zero counts are an
observed empty inventory. Missing counts, duplicate case IDs, invalid records
and conflicting totals stay visible. Every record is checked even beyond the
0–64 entry display limit. Long traces retain their original pointer, byte count
and omission flag; original files remain intact. This is an inventory, not a
root-cause classifier. A known nonempty run and zero unknown records must be
checked separately from the count needed by the research decision.

## Run the original consumer

From the checkout, use a new empty sibling workspace:

```powershell
python -B examples/result-tools/run.py --workspace ../RDS-result-tools-example
```

The labelled synthetic example independently declares three finite oracles,
qualifies and registers the extracted functions, and freezes one original
project with three owned actions. Goal predicates read returned scalar/count/
improvement values at `/cases/0/value/...`, including their result status.
They do not count the driver's qualification `PASS` as the useful result.
The failure inventory directs inspection of preserved original failures.
Preparation and all three executions share a 60-second wall budget; original
qualification costs are charged once. A repeated advance must preserve completed
runs and budget. `cli-transcript.json`, `reports/`, `outputs/`, original sources
and `.rds/project.sqlite3` retain the full chain.

`summary.json` reports applicability, actual use, decision consumption and
source/application byte counts for the same frozen workload. The full returned
application includes provenance and can be larger than a small source; that
negative byte difference is retained. Bytes measure display material only, not
token counts, total storage, processing speed or model effectiveness. Actual
model round trips are `NOT_RUN`, token savings `NOT_MEASURED`, scientific gain
`UNKNOWN`. Qualification and application use the same finite cases, so this
demonstrates reuse and consumer wiring rather than unseen task generalization.

For your own authorized workflow, extract the chosen entry, supply independently
specified finite cases and follow [native tool qualification](native-research.md)
and [owned application preparation](owned-tool-consumers.md). A catalogue match
does not admit new inputs. The current bridge requires exact qualified args/
kwargs and case bytes; changed inputs need fresh qualification and the existing
authorized method-revision route for a live project.

The extractor now admits the narrow standard-library import `from json import
loads` (including a local alias). `import json`, `json.load`, other JSON APIs,
star/relative imports, filesystem access and dynamic imports remain outside its
conservative subset. This static admission is still `purity=NOT_PROVED` and
ordinary trusted project execution, not an OS sandbox or general proof.

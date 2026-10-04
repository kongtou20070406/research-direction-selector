# Output-directory preparation and retained-output recovery

RDS creates the parent directories of declared output files before launching project code. A generator that requires its output directory to be absent can therefore fail before producing anything, even when the directory contains no user data. `scripts/rds_outputs.py` provides `prepare_empty_directory(destination)` for generators that explicitly accept either an absent or an empty destination.

```python
from rds_outputs import prepare_empty_directory

destination = prepare_empty_directory(authorized_output_directory)
```

The helper creates an absent directory, accepts a precreated empty directory, and rejects a populated directory, an existing file, or a symbolic link at the destination. It does not delete, overwrite, or clean existing contents. The caller must supply an authorized output path. This is a directory-preparation contract, not an OS sandbox or an artifact-validity check.

## Two distinct failure boundaries

[Issue #186](https://github.com/kongtou20070406/research-direction-selector/issues/186) concerned a private n=13 preparation script that called `mkdir(exist_ok=False)` on an empty output parent already created by RDS. The retained original failure receipt reported exit 1, about 4.27 seconds, and seven missing outputs. The original task already has a successful separately frozen repair: its preflight receipt reported `SUCCEEDED`, no missing outputs, and a preflight artifact reporting prepared inputs, an exact comparison of 119 equations, and seven output hashes. Those are scoped preparation and engineering observations; they do not establish the n=13 mathematical result.

A second retained failure occurred after an authorized finalizer had written an output file: a GBK console could not encode a status message. The retained console-failure record reported that the file had been written, the new ledger had not been created, and the mathematical computation had not started. A nonzero process exit at this boundary does not prove that no output exists. The output must be inspected and validated before deciding whether any computation needs repeating.

The original script revisions, failure receipt, successful preflight record, console-failure record, and original output hashes remain with the private task evidence. This repository does not contain that task's `input_builder.py` or private inputs. The public helper and tests below exercise the directory and recovery behavior using explicitly synthetic data; they do not claim to replay the private mathematical preparation.

## Recovery preserves output, failure, and spent budget

`prepare_empty_directory` deliberately rejects a partially populated destination. Recovery from a console failure must not call it to erase or reuse that directory blindly. Read the existing output, compare its bytes with the expected identity, and perform its declared structural or mathematical validation. Keep the failed receipt and already spent budget. If validation requires execution, use a separately authorized route with a new output identity; its receipt is additional evidence rather than a replacement for the failed receipt.

Repeated observation or recovery of an already completed attempt must retain its run/attempt identity and receipt. A successful validation route may also be observed again without launching it or the original generator again. Missing or changed bytes remain a blocker; the existence of a file alone is insufficient.

The regression in `tests/test_rds_output_recovery.py` generates a tiny JSON preparer inside a temporary project. Its frozen generator imports the actual helper, verifies that RDS precreated its empty output parent, writes a bound synthetic payload, and then deliberately triggers `UnicodeEncodeError` through a strict GBK stdout wrapper. The test checks the actual failed receipt, original output hash, retained failure and budget, and a separate bound validation route. Actual launch markers and receipt counts establish that recovery and repeated execution observations do not duplicate generation. It also checks absent/empty/populated/file/symlink helper boundaries; symlink creation is explicitly skipped on platforms that lack that capability.

Run this bounded engineering check from the checkout:

```powershell
python -B -m unittest discover -s tests -p test_rds_output_recovery.py -v
```

The synthetic recovery check establishes these execution and evidence-preservation behaviors only. It does not certify arbitrary generated inputs, guarantee that a failed external program is resumable, or measure research-policy improvement.

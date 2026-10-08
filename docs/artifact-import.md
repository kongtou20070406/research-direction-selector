# Shared result document parsing

Artifact import and program-owned observations use the same bounded parsers,
metadata reader and JSON/CSV/log selectors in `rds_source_documents.py`.
`rds_artifacts.py` retains its import, source identity, evidence and cost checks,
and re-exports the existing parsing helpers for compatibility.

One operation owns a `SourceDocument` for each original byte string. It lazily
parses each requested `(kind, format)` view and reuses its metadata. Each source
still checks its own expected digest and binding. Unsupported kind/format pairs
remain unsupported, and JSON/JSONL physical locators retain their original
identity. Metadata and selected structured values are copied for each consumer
so a conflict or mutation cannot contaminate another source's result.

Parsed views are dropped after the last consumer. Original byte caches retain
their existing operation lifetime; there is no global or cross-command cache.
The next import or owned review rereads its original inputs. Receipt checks,
hashing, admission, execution, costs and recovery keep their existing boundaries.
A parsed document establishes neither source integrity nor scientific support.
RSI replay program identity includes this shared parser's source bytes. Changing
them invalidates an older evaluation report for adoption, even when the version
label and other program files are unchanged.

## Reproduce the bounded performance comparison

Use an unchanged parent checkout and candidate checkout, with a fresh workspace
outside both. Run the candidate's benchmark driver for both sources:

```text
python -B <candidate>/benchmark/source_documents.py --source <parent> --workspace <workspace> --label parent
python -B <candidate>/benchmark/source_documents.py --source <candidate> --workspace <workspace> --label candidate --reuse
```

The driver freezes synthetic 408,917-byte JSON containing a scalar and 60,000
integers. It measures one and 64 references, uses five timing repetitions, and
profiles calls and peak traced Python memory in separate passes. Owned inputs
are produced through the original CLI fixture with the declared payload added
before contract initialization. Candidate measurements reuse those completed
projects; they check that runs, receipts and spent/reserved budgets are unchanged.
The public `artifacts import` and `project next` calls and their outputs are saved.

An initial Windows comparison against parent `c81cc4a` observed:

| Operation | References | Parent median | Candidate median | Strict source parses |
| --- | ---: | ---: | ---: | ---: |
| Artifact import | 1 | 22.00 ms | 22.73 ms | 1 → 1 |
| Owned collection | 1 | 32.88 ms | 34.22 ms | 1 → 1 |
| Artifact import | 64 | 1,098.82 ms | 73.37 ms | 64 → 1 |
| Owned collection | 64 | 648.48 ms | 22.33 ms | 64 → 1 |

Complete import and collection result digests matched. Source reads were
unchanged: two for import (manifest plus source) and four original artifacts for
owned collection. The 64-reference peak traced memory observations were
4,221,373 → 3,104,984 bytes for import and 3,064,035 → 3,000,264 bytes for collection.
Single-reference medians rose about 3–4%; five local repetitions do not establish
statistical equivalence or a universal speed ratio. These are function-level
synthetic results, not overall project latency, process RSS, LLM tokens or
scientific gain. Inspect the saved CLI transcript for public-entry timings.

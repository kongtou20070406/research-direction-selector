# Raw NewtonBench runs

`newtonbench-runs.tar.xz` holds every run directory of this benchmark exactly as the harness wrote it. Nothing
was edited, filtered or re-encoded. The only omissions are macOS `.DS_Store` files and extended attributes, and
owner names are set to `root`.

- **SHA-256:** `f88d05b92d3a81a8dacce820e4b4793c2779ae900425a5ca247076a24c5c8d50`
- **Size:** 14,805,916 bytes, or about 470 MB once unpacked.
- **Contents:** 87,469 files.
- **Integrity checked:** unpacked, every file's SHA-256 matched the source directory.

| Directory | Phase | RDS commit at launch | Trials | Arms |
|---|---|---|---|---|
| `pilot/` | held-out pilot, three launches; `superseded.jsonl` says which were rerun and why | `3f4a001`, `b48607f`, `73ab8b7` | 26 | vanilla, code_assisted, compass |
| `formal/` | registered formal run (Amendments 1-4) | `a341289` | 432 | vanilla, code_assisted, compass |
| `formal-rerun/` + `formal-rerun.out` | Amendment 5, POST HOC: the 48 Compass trials rerun with the corrected adapter | `7e9fd63` | 48 | compass |
| `pilot-dialect/` | Amendment 6, PILOT: transcendental dialect; `smoke/` is the harness check, never scored; `orchestrator/` is the Codex operator's brief, events and `run.out` | `457db8c` | 52 + 1 smoke | compass |

NewtonBench is `HKUST-KnowComp/NewtonBench` at `912a4ba` (MIT) for every phase. Its source is not included.

## What a trial directory holds

- **`trial.json`:** the trial record. It holds the task, model settings, status, every measurement with its
  receipt hash, the jump and fallback calls with their reported usage, the judge calls, the submissions with
  NewtonBench's evaluation, and `row_sha256`. That hash is SHA-256 over the canonical JSON of the record without
  `row_sha256` (`json.dumps(sort_keys=True, separators=(',', ':'))`).
- **`project-a<k>/`** (Compass, attempt k): the RDS project root.
  - `.rds/project.sqlite3`: the ledger, with its WAL and SHM files as left on disk.
  - `.rds/cas`: the CAS records.
  - `.rds/project-artifacts`: process streams.
  - `outputs/`: the receipted outputs.
  - Also the declaration (`compass.json`), the protocol and `experiment.py`.
  - The `COMPASS_*` rows are hash-chained, and receipts bind every artifact by SHA-256, so the evidence can be
    checked with RDS itself.
- **`jumps-a<k>/<call>/`:** one directory per Codex jump call. It holds `prompt.txt`, `events.jsonl` (the full
  Codex event stream), `last.txt`, `stderr.txt` and `audit.json` (usage, contamination hits, commands).
- **`codex/`, `fallback/`:** the baseline agents' Codex calls and the Compass fallback call, in the same layout.

## Reproducing the reported analyses

The analyses use only the Python 3.12 standard library. They need only `trial.json`, `manifests.jsonl` and the
`audit.json` files. They also record the NewtonBench commit, so a checkout must sit next to the runs. From the
repository root, put the runs under `nb/runs`:

```bash
mkdir -p nb/runs check && tar -xJf benchmark/newtonbench/raw/newtonbench-runs.tar.xz -C nb/runs
git clone https://github.com/HKUST-KnowComp/NewtonBench nb/NewtonBench && git -C nb/NewtonBench checkout 912a4ba
NB_EVAL_DIR=$PWD/nb python3.12 -B benchmark/newtonbench/analyze.py --phase formal --rerun-phase formal-rerun --out check/newtonbench-compass.json --table check/per-task.md --corrected-table check/per-task-corrected.md
NB_EVAL_DIR=$PWD/nb python3.12 -B benchmark/newtonbench/analyze_pilot.py --out check/pilot-dialect.json --table check/pilot-dialect.md
```

On 2026-10-17 these commands were run on the trial records alone, and the outputs were compared with
`results/`. The per-task tables were identical. The two JSON files and `pilot-dialect.md` differed only in
metadata: `generated_at`, the runs path, `rds_commit_analysis`, and `bundle_sha256`, which hashes those fields.

An independent recount of the pilot was also made. It used its own script and did not import the benchmark
code, and every number matched `results/pilot-dialect.json`:
- per subset and stratum: correct, certified, certified-correct and false-certified counts;
- tokens counted against the cap, recomputed from the on-disk `audit.json` files across all attempts;
- statuses, contamination and `row_sha256`.

One field is narrower than its name suggests. In the one restarted pilot trial
(`m10_be_distribution/medium/v2/noise0.01`), `trial.json` `agent_tokens.spent` covers the second attempt
only. The cap totals count both attempts from the audits.

## Notes for a recomputation

- **Local paths.** Records, prompts and ledgers carry the original absolute paths
  (`/Users/norriswu/Documents/kongtou项目/RDS/...`). They are left as written, because receipts and the ledger
  bind them.
- **Ground truth.** NewtonBench's ground-truth law strings appear in `trial.json` as its evaluator reported them.
  They come from NewtonBench's public source.
- **Not included.** The NewtonBench checkout, the Python environment, and any credentials. A scan of every file,
  including the SQLite ledgers and binary streams, found no API keys, tokens or e-mail addresses.
- **Pilot status.** The pilot ran on release `0fb9e96`. A crash it met (`ValueError` in `rds_compass._fits`) is
  fixed in `de4315d` on `release/6.0.0-compass`. The pilot's data and results stay as run.

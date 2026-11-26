# Terminology and evidence in LaTeX papers

`paper_gate.json` belongs to the paper. Running `paper_gate.py PAPER_DIR` previews corrections. `--fix` applies registered corrections to the source files and backs up their prior contents. The same terminology check runs with the embedding service offline.

| Entry | Use | Effect |
| --- | --- | --- |
| `terms` | `["old name", "canonical name"]` when both names denote the same thing throughout the manuscript | Prefer the longest complete phrase. Report the change or apply it with `--fix`. |
| `acronyms` | Mapping from an abbreviation to its official expansion | Check the first use in the abstract and the body independently. |
| `term_context` | Mapping from an expression to `{ "canonical": "named concept", "note": "when it applies" }` | Report occurrences with their context and keep the author's wording. Move the entry to `terms` after a decision supporting a manuscript-wide replacement. |
| `semantic.term_ignore` | A phrase, or `["candidate", "canonical"]` for a rejected pairing | A phrase suppresses that candidate everywhere. A pair suppresses only that proposed equivalence. |

Run `--semantic` to find additional candidates with the local embedding service configured in `semantic.url` and `semantic.model`. Each candidate includes an example from the manuscript. Compare the definitions and the scope of the terms before editing the glossary. Technical qualifiers such as `1D`, `2D` and `FP32` are part of the phrase.

When files change, the command immediately emits `PAPER_GATE_AUTOFIX_APPLIED`. Read the linked `.gate_backup/<run>/writer_review.json`. Each edit gives its kind, paper-relative file, final line and column, previous wording, replacement and final sentence context. Check meaning and grammar in those passages and carry the approved names into the next draft. Preview runs and unchanged reruns do not emit another event. Each applied run retains its own receipt with the original files.

`semantic.restate_metric_groups` maps a metric family to its spellings. When both sentences name metrics and the families are disjoint, the gate ranks the pair below the other duplicate candidates. The displayed top pairs share a report limit.

`semantic.evidence.files` lists paper-relative experimental sources. `tasks`, `datasets` and `metrics` inside `semantic.evidence` each map a condition name to alternate spellings. Include vocabulary for claims that would need evidence even when no result exists yet. For an empirical clause, the report looks for the stated conditions together in a measured passage from those files. It shows the candidate passage or warns when none is found. Authors check the value, comparator and interpretation against the source. Purely tabular results without a measured sentence or caption can require manual location.

`semantic.evidence.dataset_tasks` maps a benchmark to its task, for example `{"GoPro": "deblurring"}`. A passage that names the benchmark then also counts as naming the task, so an abstract claim about "GoPro deblurring" matches "On GoPro, NSI reaches 31.48 dB". A clause that reports several benchmarks ("31.48 dB on GoPro and 26.79 dB on O-HAZE") is checked once per benchmark. A claim that combines metrics may take each figure from a different sentence of the same study.

The deterministic `scope` check runs without `--semantic` and uses the same `tasks` and `datasets` vocabulary. A task or dataset named in the abstract or conclusion must also appear in one of `semantic.evidence.files`, even as a pending run. Otherwise the gate reports an error, because readers would first meet that experiment in the conclusion. Introduce it in the experiments or remove it from the summary. List deliberate exceptions in `scope_allow`.

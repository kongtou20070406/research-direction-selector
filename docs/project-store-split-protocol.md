# Pre-registered acceptance for slicing `ProjectStore`

Written before any code moves. Nothing below may be revised after the first
slice is measured; a revision is a new protocol.

## The problem

`scripts/rds_project.py` is 2080 lines. `class ProjectStore` occupies
`423-2080` — 1658 lines and 52 methods. It is the authoritative ledger:
SQLite events, the TMS, the content-addressed store, execution receipts, and
the admission rules that decide whether a paid command may run at all.

Size alone is not the complaint. The complaint is that four unrelated
concerns share one class body: database access, admission policy, the two
strategy lanes (advisor, maintenance), and the lifecycle state machine. A
change to any one of them is reviewed against 1658 lines of the others.

The read half is already separate: `scripts/rds_project_read_port.py` is a
documented read-only projection that borrows the caller's transaction, and
`scripts/rds_project_read_services.py` sits on top of it. So the 1658 lines
are mostly the write path plus policy. **Any slice plan must respect that
split rather than duplicating it.**

## Main indicator

| item | value |
|---|---|
| measured | `class ProjectStore` line span after each slice (end minus start) |
| control | 1658 today (423-2080 of a 2080-line file) |
| target | batch 1 ≤ 1400, batch 2 ≤ 1100, batch 3 ≤ 800 |
| non-goal | total line count is NOT the indicator. Moving lines into a second equally large file buys nothing. |

A slice that lowers the class span but creates a module above 400 lines is
rejected: it just relocates the reading cost.

## What counts as a pure move

The move is accepted only if, for every relocated symbol, the syntax tree is
**identical** after normalising the receiver. That is the whole test.

```
python -B tools/verify_pure_move.py --mode method \
    --source scripts/rds_project.py --class ProjectStore \
    --target scripts/rds_project_<topic>.py --map <map.json>
```

`tools/verify_pure_move.py` compares the method body with the first parameter
renamed to one marker (`self.db` and `store.db` normalise equal) and compares
every remaining parameter and default. Exit 0 = identical, 1 = different.

The tool was validated on the `rds_compass_report.py` split that is already in
the tree: all seven relocated functions report `identical tree`, and pointing
the map at the wrong pair reports `body differs` and exits 1. A validator that
has never failed is not a validator.

Moving a method to a function may add a docstring. Docstrings are excluded
from the comparison; `--keep-docstring` restores strict mode for a batch where
nobody added one.

## Safety gates — all of these, before any commit

| # | gate | command |
|---|---|---|
| 1 | the ledger's own tests pass | `python -B -m unittest discover -s tests -p 'test_rds_project*.py'` |
| 2 | the quick ledger still passes | `python -B -m unittest discover -s tests -p 'test_rds_quick.py'` |
| 3 | every module still has a layer | `python -B -m unittest discover -s tests -p 'test_import_graph.py'` |
| 4 | no new cycles, mutual pairs or upward edges | `python -B -c "import sys;sys.path[:0]=['scripts','tools'];import import_graph as g;inv=g.inventory('.');print(len(inv['cycles']),len(inv['mutual_pairs']),inv['counts']['upward_edges'])"` — compared against the same command on the parent commit |
| 5 | every relocated symbol is the same tree | the validator above, all PASS |
| 6 | no stale call site | `grep -rn 'self\._<moved>' scripts/ tests/` returns nothing for each moved name |

Gate 4 has a recorded baseline: 4 cycles, 40 mutual pairs, 18 upward edges at
`ce7a814`. It is measured on the same machine in the same run, not quoted from
memory.

## Reverse test

Deleting the shim proves the shim is load-bearing. For each batch, remove one
relocated symbol's remaining wrapper (or one re-export line) and confirm a
named test fails. Record the test name and the failure text. A batch whose
reverse test does not fail is a batch whose shim nothing depends on — which
means the shim should not exist either.

## Rollback trigger

Any gate failing means `git checkout -- scripts/ tests/ tools/` for that batch
and the slice is not committed. The report still gets committed, as a negative
result, with the failing gate named.

## Explicit non-goals

- Do not touch the transaction boundary in `_execute_admitted`. It opens the
  connection, decides admission, runs the child, and settles the receipt inside
  one transaction. A slice may move helpers out from under it; a slice may not
  move it.
- Do not introduce a second ledger, a second scheduler, or a sidecar database.
- Do not change any schema, receipt field, or admission threshold.
- Do not rewrite a function "while it is being moved". Rewrites get their own
  protocol with their own control.

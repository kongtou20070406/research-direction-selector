"""L1-b acceptance: does the anti-unified operator library help without costing quality?

Pre-registered in docs/rsi-l1l2-plan-20261020.md section 4.1. Reads two devset
result files -- library off and library on -- and decides four gates:

  1. claim_exact(on) >= claim_exact(off), and no cell may turn exact -> inexact.
  2. reuse evidence: claim_exact strictly rises, OR the total experiment count
     strictly falls.  The original wording demanded a per-cell drop; that had to
     go (revision 6, 2026-10-20).  Twelve exact cells already sit at four
     experiments against budgets of 8/16/32, so they never bought exploration and
     no operator can make them cheaper.  A per-cell gate there can only ever
     fail, and failing would get read as "L1 does nothing" rather than "this
     metric has no leverage in this regime".
  3. cost: the total experiment count must stay within 1.25x of the baseline, so
     a quality gain cannot buy itself with unbounded spend.
  4. no cell may add a false certification.

Revision 7 (2026-10-21): the devset grew from five equations to thirty, so the
cell set is read from the registry (`devset.list_equations`) instead of a frozen
five-tuple.  The gates keep their wording and their thresholds; only the
denominator moves from 15 cells to 90.

Missing evidence is not a zero.  A cell the runner never judged -- the baseline
carries 23 such rows, declared and then rejected at declaration time, so they have
an `error` and no `status` -- is UNKNOWN: counted neither as pass nor as fail,
dropped from every gate denominator, and listed separately in the report.  Two
different absences are kept apart:

  * the cell is not in the file at all -> MISSING CELLS, fails closed immediately;
  * the cell is in the file but carries no recognised status -> UNKNOWN, excluded.

If the UNKNOWN cells leave fewer than half of the 90 judged, the script FAILs with
"insufficient sample" instead of passing on thin air.

The I.6.20a cell is recorded separately and does NOT gate: whether anti-unification
can build a template containing pi depends on whether the library already holds a
splittable success case, which is unknown before the run.  Gating on it would make
L1 pass or fail by luck.

Negative condition: if every JUDGED cell behaves byte-identically, the operators
were never reused and the L1 path does not exist -- that is a FAIL with its own
label, not a silent pass.  UNKNOWN cells are not evidence of reuse either way.

Fails closed.  A missing, unreadable, or incomplete result file is a FAIL.  The
kernel never teaches this script what "good" means; every number comes from the two
JSON files and from the pre-registered thresholds below.

Usage:
    python -B benchmark/newtonbench/l1b_accept.py
    python -B benchmark/newtonbench/l1b_accept.py --off results/a.json --on results/b.json
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / 'benchmark' / 'newtonbench' / 'results'
sys.path[:0] = [str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]

import devset  # noqa: E402

# Read from the registry so a new equation joins the gate without editing this file.
EQUATIONS = tuple(devset.list_equations())
BUDGETS = (8, 16, 32)
CELLS = tuple((eq, b) for eq in EQUATIONS for b in BUDGETS)

# Recorded, never gated.  See the module docstring.
EXTRA_EQUATION = 'feynman-I.6.20a'

# The handoff vocabulary a settled cell can carry (scripts/rds_compass.py:32-34).
# Anything else -- a row that crashed before recording a status, or a status this
# gate has never been calibrated against -- is UNKNOWN, never coerced.
KNOWN_STATUSES = frozenset((
    'GOAL_IDENTIFIED', 'IMPROVEMENT_EVALUATED', 'BUDGET_EXHAUSTED', 'SEARCH_EXHAUSTED',
    'JUDGMENT_REQUIRED', 'STEP_LIMIT', 'CONTROLLER_DEADLINE', 'INTERRUPTED',
    'WAITING_FOR_ORIGINAL_ATTEMPT',
    'NO_MEASUREMENTS', 'RUN_FAILED', 'RUN_ERROR',
))
# Provenance: this list is the kernel's own state machine (`scripts/rds_compass.py:32-34`
# HANDOFF_REASONS, plus the three devset-side names in `devset_run.certify_run`).  It is
# NOT part of the pre-registration, so a kernel that grows a new terminal status must
# extend it here or that cell silently degrades to UNKNOWN.  Errs toward UNKNOWN, never
# toward a pass: an unrecognised name is neither a success nor a failure.


def load(path):
    """Return the run index, or raise.  Missing input must never become a pass."""
    if not path.exists():
        raise SystemExit(f'MISSING INPUT: {path}\n'
                         'L1-b cannot be judged without both result files. '
                         'Run the library-off and library-on devset sweeps first.')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise SystemExit(f'UNREADABLE INPUT: {path}: {exc}')
    runs = data.get('runs')
    if not isinstance(runs, list) or not runs:
        raise SystemExit(f'MALFORMED INPUT: {path} has no non-empty "runs" list')
    index = {}
    for run in runs:
        try:
            key = (run['equation'], run['B'])
        except (KeyError, TypeError):
            raise SystemExit(f'MALFORMED RUN in {path}: {run!r}')
        if key in index:
            raise SystemExit(f'DUPLICATE CELL in {path}: {key}')
        index[key] = run
    return index


def judged(run):
    """True when the row carries a status this gate can read.

    A row without a status is the runner telling us it never got as far as judging
    the cell; reading its absent counterexamples or experiments as a zero would turn
    "no evidence" into "bad result".
    """
    return run.get('status') in KNOWN_STATUSES


def flag(run):
    """claim_exact as a bool.  Accepts the bool and the 0/1 spelling both files use."""
    value = run.get('claim_exact')
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise SystemExit(f'MALFORMED claim_exact: {value!r} in {run!r}')


def experiments(run):
    value = run.get('experiments')
    if not isinstance(value, int):
        raise SystemExit(f'MALFORMED experiments: {value!r} in {run!r}')
    return value


def false_certification(run):
    """A claim with a counterexample is a false certification.

    The richer field added by the false-certification work may not exist in older
    runs; fall back to the raw status/counterexample pair rather than assuming 0.
    """
    explicit = run.get('false_certification')
    if isinstance(explicit, bool):
        return explicit
    # Only a claim can be falsely certified: a row claiming nothing is False, not
    # malformed, and a cell that never recorded a counterexample is not a claim.
    counterexamples = run.get('counterexamples')
    if run.get('status') != 'GOAL_IDENTIFIED' or not counterexamples:
        return False
    return counterexamples > 0


def check(label, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {label}' + (f'  {detail}' if detail else ''))
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--off', default=str(RESULTS / 'devset-l1off.json'))
    parser.add_argument('--on', default=str(RESULTS / 'devset-l1on.json'))
    args = parser.parse_args(argv)

    off = load(Path(args.off))
    on = load(Path(args.on))

    absent = [c for c in CELLS if c not in off or c not in on]
    if absent:
        raise SystemExit(f'MISSING CELLS: {absent}\n'
                         f'Both files must cover all {len(CELLS)} pre-registered cells; '
                         'a cell absent from the file is a truncated run, not UNKNOWN.')

    ok = True
    print(f'library off: {args.off}')
    print(f'library on:  {args.on}')
    print(f'equations:   {len(EQUATIONS)} (from the registry), budgets: {list(BUDGETS)}')
    print()

    unknown = [c for c in CELLS if not (judged(off[c]) and judged(on[c]))]
    cells = [c for c in CELLS if c not in unknown]
    print(f'UNKNOWN  {len(unknown)}/{len(CELLS)} cells carry no recognised status '
          f'(declaration-time rejection); judged {len(cells)}')
    for eq, b in unknown:
        side = '+'.join(s for s, files in (('off', off), ('on', on))
                        if not judged(files[(eq, b)]))
        reason = str((on.get((eq, b)) if 'on' in side else off.get((eq, b))).get('error'))[:80]
        print(f'         {eq} B={b} [{side}] {reason}')
    print()

    # Gate 0 -- UNKNOWN is not a pass.  Below half of the design, say so.
    # "At least half": 45/90 is judgeable.  A stricter bar would need an effect size
    # and a power calculation, and inventing one here would pin a threshold nobody
    # pre-registered; half is the only line this script can defend.
    majority = len(cells) * 2 >= len(CELLS)
    ok &= check('gate 0: at least half of the pre-registered cells are judgeable',
                majority,
                f'{len(cells)}/{len(CELLS)} judged'
                + ('' if majority else ' -- insufficient sample, cannot judge'))

    # Gate 1 -- quality must not regress, cell by cell.
    regressed = [c for c in cells if flag(off[c]) and not flag(on[c])]
    off_total = sum(1 for c in cells if flag(off[c]))
    on_total = sum(1 for c in cells if flag(on[c]))
    ok &= check('gate 1a: claim_exact(on) >= claim_exact(off)',
                on_total >= off_total, f'{on_total} >= {off_total}')
    ok &= check('gate 1b: no cell turns exact -> inexact',
                not regressed, f'regressed={regressed}' if regressed else '0 regressed')

    # Gate 2 -- reuse evidence: either quality went up, or the total spend went down.
    off_total_exp = sum(experiments(off[c]) for c in cells)
    on_total_exp = sum(experiments(on[c]) for c in cells)
    quality_up = on_total > off_total
    cost_down = on_total_exp < off_total_exp
    ok &= check('gate 2: reuse evidence (claim_exact up, or total experiments down)',
                quality_up or cost_down,
                f'claim_exact {off_total}->{on_total}, '
                f'experiments {off_total_exp}->{on_total_exp}')

    # Gate 3 -- a quality gain may not pay for itself with unbounded spend.
    ceiling = 1.25 * off_total_exp
    ok &= check('gate 3: total experiments <= 1.25x baseline',
                on_total_exp <= ceiling,
                f'{on_total_exp} <= {ceiling:.1f}')

    # Gate 4 -- safety: no new false certification anywhere.
    added = [c for c in cells if false_certification(on[c]) and not false_certification(off[c])]
    ok &= check('gate 4: no cell adds a false certification',
                not added, f'added={added}' if added else '0 added')

    # Recorded, not gating.
    extra = [(b, off[(EXTRA_EQUATION, b)], on[(EXTRA_EQUATION, b)]) for b in BUDGETS]
    improved = [(b, o, n) for b, o, n in extra
                if judged(o) and judged(n)
                and ((o.get('status') == 'SEARCH_EXHAUSTED'
                      and n.get('status') != 'SEARCH_EXHAUSTED')
                     or experiments(n) < experiments(o))]
    extra_unknown = [b for b, o, n in extra if not (judged(o) and judged(n))]
    print(f'NOTE  {EXTRA_EQUATION} (recorded, not a gate): '
          + (f'{len(improved)} budget(s) improved' if improved else 'no change')
          + (f'; UNKNOWN budgets {extra_unknown}' if extra_unknown else ''))

    # Negative condition -- identical behaviour everywhere means zero reuse.
    identical = [c for c in cells
                 if flag(off[c]) == flag(on[c])
                 and experiments(off[c]) == experiments(on[c])
                 and off[c].get('status') == on[c].get('status')
                 and off[c].get('law') == on[c].get('law')]
    zero_reuse = bool(cells) and len(identical) == len(cells)
    ok &= check('negative condition: operators were actually reused',
                not zero_reuse,
                f'{len(identical)}/{len(cells)} judged cells byte-identical'
                + (f' ({len(unknown)} UNKNOWN not evidence)' if unknown else '')
                + (' -- L1 PATH DOES NOT EXIST' if zero_reuse else ''))

    print()
    print('OVERALL: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

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

The I.6.20a cell is recorded separately and does NOT gate: whether anti-unification
can build a template containing pi depends on whether the library already holds a
splittable success case, which is unknown before the run.  Gating on it would make
L1 pass or fail by luck.

Negative condition: if all 15 cells behave byte-identically, the operators were
never reused and the L1 path does not exist -- that is a FAIL with its own label,
not a silent pass.

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

# Frozen by the protocol: five equations times three budgets is the whole design.
EQUATIONS = ('feynman-I.12.1', 'feynman-I.14.3', 'feynman-I.6.20a',
             'feynman-I.9.18', 'feynman-II.2.42')
BUDGETS = (8, 16, 32)
CELLS = tuple((eq, b) for eq in EQUATIONS for b in BUDGETS)

# Recorded, never gated.  See the module docstring.
EXTRA_EQUATION = 'feynman-I.6.20a'


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
    if run.get('status') != 'GOAL_IDENTIFIED':
        return False
    counterexamples = run.get('counterexamples')
    if counterexamples is None:
        raise SystemExit(f'MALFORMED counterexamples: None in {run!r}; '
                         'cannot judge the safety gate on a claim cell')
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

    missing = [c for c in CELLS if c not in off] + [c for c in CELLS if c not in on]
    if missing:
        raise SystemExit(f'MISSING CELLS: {missing}\n'
                         'Both files must cover all 15 pre-registered cells.')

    ok = True
    print(f'library off: {args.off}')
    print(f'library on:  {args.on}')
    print()

    # Gate 1 -- quality must not regress, cell by cell.
    regressed = [c for c in CELLS if flag(off[c]) and not flag(on[c])]
    off_total = sum(1 for c in CELLS if flag(off[c]))
    on_total = sum(1 for c in CELLS if flag(on[c]))
    ok &= check('gate 1a: claim_exact(on) >= claim_exact(off)',
                on_total >= off_total, f'{on_total} >= {off_total}')
    ok &= check('gate 1b: no cell turns exact -> inexact',
                not regressed, f'regressed={regressed}' if regressed else '0 regressed')

    # Gate 2 -- reuse evidence: either quality went up, or the total spend went down.
    off_total_exp = sum(experiments(off[c]) for c in CELLS)
    on_total_exp = sum(experiments(on[c]) for c in CELLS)
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
    added = [c for c in CELLS if false_certification(on[c]) and not false_certification(off[c])]
    ok &= check('gate 4: no cell adds a false certification',
                not added, f'added={added}' if added else '0 added')

    # Recorded, not gating.
    extra = [(b, off[(EXTRA_EQUATION, b)], on[(EXTRA_EQUATION, b)]) for b in BUDGETS]
    improved = [(b, o, n) for b, o, n in extra
                if (o.get('status') == 'SEARCH_EXHAUSTED'
                    and n.get('status') != 'SEARCH_EXHAUSTED')
                or experiments(n) < experiments(o)]
    print(f'NOTE  {EXTRA_EQUATION} (recorded, not a gate): '
          + (f'{len(improved)} budget(s) improved' if improved else 'no change'))

    # Negative condition -- identical behaviour everywhere means zero reuse.
    identical = [c for c in CELLS
                 if flag(off[c]) == flag(on[c])
                 and experiments(off[c]) == experiments(on[c])
                 and off[c].get('status') == on[c].get('status')
                 and off[c].get('law') == on[c].get('law')]
    zero_reuse = len(identical) == len(CELLS)
    ok &= check('negative condition: operators were actually reused',
                not zero_reuse,
                f'{len(identical)}/{len(CELLS)} cells byte-identical'
                + (' -- L1 PATH DOES NOT EXIST' if zero_reuse else ''))

    print()
    print('OVERALL: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

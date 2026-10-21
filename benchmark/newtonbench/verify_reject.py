"""Judge `results/source-reject-20261021.json` against the pre-registered protocol.

    python -B benchmark/newtonbench/verify_reject.py results/source-reject-20261021.json

Exit 0 on PASS, 1 on FAIL. Nothing here chooses a threshold; every number comes
from `benchmark/newtonbench/source-reject-protocol.md` section 3.

The whole point of this script is that the verdict is computed from the result
file, not typed by hand. `verify_reach.py` used to hardcode "8 of 8" while the
list it printed held six entries, so the count and the string disagreed.
"""

import json
import sys
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text())


def outcome(row):
    """A wrong-only cell passes when the kernel did not accept the wrong law."""
    if 'error' in row or row.get('status') in (None, 'TIMEOUT'):
        return 'UNKNOWN'
    if row.get('false_certification') is True:
        return 'FAIL'
    if row.get('claim_exact') is True:
        return 'FAIL'
    return 'PASS'


def mixed_outcome(row):
    if 'error' in row or row.get('status') in (None, 'TIMEOUT'):
        return 'UNKNOWN'
    if row.get('claim_exact') is True:
        return 'RECOVERED'
    if row.get('claim') and row.get('claim') != 'NOT_ESTABLISHED':
        return 'FALSE_RECOVERY'
    return 'NO_CLAIM'


def main(argv):
    rows = load(argv[1] if len(argv) > 1 else 'results/source-reject-20261021.json')
    wrong = [row for row in rows if row.get('variant') == 'wrong-only']
    mixed = [row for row in rows if row.get('variant') == 'mixed']

    print(f'wrong-only cells: {len(wrong)}   mixed cells: {len(mixed)}')
    print()
    print(f"{'equation':24s} {'wrong-only':12s} {'mixed':14s} {'claim_exact':11s} {'false_cert':10s} {'secs':>7s}")
    for row in wrong:
        mate = next((m for m in mixed if m.get('equation') == row.get('equation')), {})
        print(f"{row.get('equation'):24s} {outcome(row):12s} {mixed_outcome(mate):14s} "
              f"{str(row.get('claim_exact')):11s} {str(row.get('false_certification')):10s} "
              f"{row.get('seconds', 0):7.1f}")

    applicable = [row for row in wrong if outcome(row) != 'UNKNOWN']
    passed = [row for row in applicable if outcome(row) == 'PASS']
    unknown = [row.get('equation') for row in wrong if outcome(row) == 'UNKNOWN']
    false_recovery = [m.get('equation') for m in mixed if mixed_outcome(m) == 'FALSE_RECOVERY']
    false_cert = [row.get('equation') for row in wrong if row.get('false_certification') is True]

    print()
    print(f'main metric: {len(passed)} / {len(applicable)} wrong-only cells did not accept the wrong law')
    if unknown:
        print(f'UNKNOWN: {unknown}')
    if false_cert:
        print(f'false certification rows: {false_cert} -- protocol section 3 voids the table')
    if false_recovery:
        print(f'FALSE_RECOVERY cells: {false_recovery}')

    if false_cert or false_recovery or len(passed) != len(applicable):
        verdict, code = 'OVERALL: FAIL', 1
    elif unknown:
        # Protocol section 4: an unfinished cell is neither a pass nor a failure.
        # It shrinks the denominator, so the table cannot be called PASS either.
        verdict, code = 'OVERALL: INCOMPLETE', 2
    else:
        verdict, code = 'OVERALL: PASS', 0
    print()
    print(verdict)
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv))

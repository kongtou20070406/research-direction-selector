"""How many devset equations sit above the synthesis bound, and what happened to them.

`scripts/rds_compass_declaration.py` defaults `synthesis.max_nodes` to 7 and every
escalation mode in `scripts/rds_compass.py:779` reuses that same bound, so a truth
tree larger than 7 nodes is unreachable no matter how many candidates are bought.
This reads the two stored result files and says which equations that affects.

    python -B benchmark/newtonbench/size_ceiling.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS = ROOT / 'benchmark/newtonbench/results'
BOUND = 7


def load(name):
    path = RESULTS / name
    if not path.is_file():
        sys.exit(f'FAIL missing {path}')
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    truths = load('truth-size.json')
    runs = load('devset-run-30.json')['runs']

    cells = {}
    for row in runs:
        cells.setdefault(row['equation'], {})[row['B']] = row

    oversized = [r for r in truths if r['size'] > BOUND]
    print(f'truth trees: {len(truths)} total, {len(oversized)} above the {BOUND}-node bound')
    print()

    solved, exhausted, unmeasured = [], [], []
    for truth in sorted(oversized, key=lambda r: -r['size']):
        equation = truth['equation']
        rows = cells.get(equation, {})
        statuses = {rows[b]['status'] for b in (8, 16, 32) if b in rows and rows[b].get('status')}
        exact = any(rows[b].get('claim_exact') for b in rows)
        if exact:
            bucket = solved
        elif statuses == {'SEARCH_EXHAUSTED'}:
            bucket = exhausted
        elif not statuses:
            bucket = unmeasured
        else:
            bucket = exhausted
        bucket.append((equation, truth['size'], truth['law'], statuses, exact))

    print(f'solved at least once: {len(solved)}')
    for equation, size, law, statuses, _ in solved:
        found = next((cells[equation][b].get('law') for b in (8, 16, 32)
                      if cells.get(equation, {}).get(b, {}).get('claim_exact')), None)
        print(f'  {equation} size={size}  truth={law}')
        print(f'    found {found}')
    print()
    print(f'enumerated every tree up to {BOUND} nodes and found none: {len(exhausted)}')
    for equation, size, law, statuses, _ in exhausted:
        print(f'  {equation} size={size}  {sorted(statuses)}  truth={law}')
    print()
    print(f'never searched (the declaration rejected the cell): {len(unmeasured)}')
    for equation, size, law, _, _ in unmeasured:
        print(f'  {equation} size={size}  truth={law}')
    print()
    print('A solved equation above the bound found an equivalent tree that fits; an')
    print('exhausted one had no such equivalent. Raising the bound therefore helps only')
    print('if the enumeration can still reach the larger tree inside its candidate cap.')


if __name__ == '__main__':
    main()

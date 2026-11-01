"""Field-by-field A/B of two schema-2 pareto.json files.

Usage: python compare_pareto.py <pre-stage2.json> <stage2.json>

Rows are matched on (task, budget, arm): the task/budget pair alone is not
unique (every cell has one rds row and one baseline-uniform row).  Time-ish
fields are excluded; everything else is compared recursively.
"""
import json
import sys
from pathlib import Path

EXCLUDE = {'seconds', 'elapsed_seconds', 'campaign_root', 'started_at', 'finished_at'}


def load(path):
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    if not (isinstance(raw, dict) and 'rows' in raw):
        raise SystemExit(f'{path}: not schema 2 (top-level {type(raw).__name__})')
    return raw


def diff(a, b, path=''):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in EXCLUDE:
                continue
            if k not in a:
                out.append((f'{path}.{k}', '<absent>', b[k]))
            elif k not in b:
                out.append((f'{path}.{k}', a[k], '<absent>'))
            else:
                out += diff(a[k], b[k], f'{path}.{k}')
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append((path or '<root>', f'len={len(a)}', f'len={len(b)}'))
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out += diff(x, y, f'{path}[{i}]')
    elif a != b:
        out.append((path or '<root>', a, b))
    return out


def rowkey(r):
    return (r.get('task'), r.get('budget'), r.get('arm'))


def main(pre_path, head_path):
    pre, head = load(pre_path), load(head_path)
    print(f'pre : {pre_path}  keys={list(pre)}')
    print(f'head: {head_path}  keys={list(head)}')
    pre_rows = {rowkey(r): r for r in pre['rows']}
    head_rows = {rowkey(r): r for r in head['rows']}
    print(f'rows: pre={len(pre_rows)} head={len(head_rows)}')
    print('key sets equal:', set(pre_rows) == set(head_rows))
    only_pre = sorted(set(pre_rows) - set(head_rows), key=str)
    only_head = sorted(set(head_rows) - set(pre_rows), key=str)
    print('only in pre :', only_pre)
    print('only in head:', only_head)

    diffs = []
    for k in sorted(set(pre_rows) & set(head_rows), key=str):
        diffs += [(k, p, x, y) for (p, x, y) in diff(pre_rows[k], head_rows[k], '/'.join(str(i) for i in k))]
    print(f'\n== rows differing fields: {len(diffs)} across {len({d[0] for d in diffs})} rows')
    for (k, p, x, y) in diffs:
        print(f'{p} | {x!r} -> {y!r}')

    print('\n== aggregate blocks ==')
    for block in ('rds', 'baseline-uniform'):
        d = diff(pre[block], head[block], block)
        print(f'{block}: {len(d)} differing field(s)')
        for (p, x, y) in d:
            print(f'  {p} | {x!r} -> {y!r}')

    print('\n== top-level scalars and rules ==')
    for k in sorted(set(pre) | set(head)):
        if k in ('rows', 'rds', 'baseline-uniform'):
            continue
        if k not in pre:
            print(f'  {k} | <absent> -> {head[k]!r}')
        elif k not in head:
            print(f'  {k} | {pre[k]!r} -> <absent>')
        elif pre[k] != head[k]:
            print(f'  {k} | {pre[k]!r} -> {head[k]!r}')

    return pre, head, pre_rows, head_rows, diffs


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])

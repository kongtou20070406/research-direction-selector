"""Render the markdown tables for docs/pareto-stage2-ab.md from the two results.

Usage: python render_report.py <pre.json> <head.json>

Prints a markdown fragment: row-level differing fields, aggregate-block diffs
and top-level scalar diffs.  The narrative judgement is written by hand.
"""
import json
import sys
from pathlib import Path

EXCLUDE = {'seconds', 'elapsed_seconds', 'campaign_root', 'started_at', 'finished_at'}


def diff(a, b, path=''):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in EXCLUDE:
                continue
            if k not in a:
                out.append((f'{path}.{k}' if path else k, '<absent>', b[k]))
            elif k not in b:
                out.append((f'{path}.{k}' if path else k, a[k], '<absent>'))
            else:
                out += diff(a[k], b[k], f'{path}.{k}' if path else k)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append((path or '<root>', f'len={len(a)}', f'len={len(b)}'))
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out += diff(x, y, f'{path}[{i}]')
    elif a != b:
        out.append((path or '<root>', a, b))
    return out


def cell(v):
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, str):
        return v.replace('|', '\\|')
    return json.dumps(v, ensure_ascii=False)


def rowkey(r):
    return (r.get('task'), r.get('budget'), r.get('arm'))


pre = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
head = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
pre_rows = {rowkey(r): r for r in pre['rows']}
head_rows = {rowkey(r): r for r in head['rows']}

print('### rows')
print(f"pre rows: {len(pre_rows)}  head rows: {len(head_rows)}  "
      f"same key set: {set(pre_rows) == set(head_rows)}")
changed, same = [], []
for k in sorted(set(pre_rows) & set(head_rows)):
    d = diff(pre_rows[k], head_rows[k])
    (changed if d else same).append((k, d))
print(f'rows with at least one differing field: {len(changed)} / {len(same) + len(changed)}')
print(f'identical rows: {len(same)}')
print()
print('| task | budget | arm | field | pre-stage2 (5cb73ff) | HEAD (601bc53) |')
print('|---|---|---|---|---|---|')
for k, d in changed:
    for (p, x, y) in d:
        print(f'| {k[0]} | {k[1]} | {k[2]} | {p} | {cell(x)} | {cell(y)} |')
print()
print('### aggregate blocks and top-level')
print('| block | field | pre-stage2 (5cb73ff) | HEAD (601bc53) |')
print('|---|---|---|---|')
for block in ('rds', 'baseline-uniform'):
    for (p, x, y) in diff(pre[block], head[block], block):
        print(f'| {block} | {p.split(".", 1)[-1]} | {cell(x)} | {cell(y)} |')
for k in sorted(set(pre) | set(head)):
    if k in ('rows', 'rds', 'baseline-uniform'):
        continue
    if k not in pre:
        print(f'| top-level | {k} | <absent> | {cell(head[k])} |')
    elif k not in head:
        print(f'| top-level | {k} | {cell(pre[k])} | <absent> |')
    elif pre[k] != head[k]:
        print(f'| top-level | {k} | {cell(pre[k])} | {cell(head[k])} |')

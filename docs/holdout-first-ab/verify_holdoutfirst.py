"""Authoritative cross-check: three pareto files, one field set, no derived printing."""
import json
import sys

FILES = {
    'stage2': 'benchmark/newtonbench/results/pareto-stage2.json',
    'costfix': 'benchmark/newtonbench/results/pareto-stage2-costfix.json',
    'holdoutfirst': 'benchmark/newtonbench/results/pareto-holdoutfirst.json',
}
WANT = ('claim', 'claim_exact', 'exact_match', 'best_effort_exact', 'claimed',
        'false_certification', 'experiments', 'status')


def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


docs = {k: load(v) for k, v in FILES.items()}

print('=== TOP-LEVEL, all three ===')
for k, d in docs.items():
    print(f"{k}: correct_count={d.get('correct_count')} "
          f"fc_count={d.get('false_certification_count')} "
          f"claimed={d.get('claimed_count')} "
          f"epc={d.get('experiments_per_correct')} "
          f"exp_total={d.get('experiments_total')}")
print()

print('=== true-false_certification cells per file ===')
for k, d in docs.items():
    trues = [(r['task'], r['arm'], r['budget']) for r in d['rows']
             if r.get('false_certification') is True]
    print(f'{k}: {len(trues)} -> {trues}')
print()

print('=== correct_count recomputed: claim_exact>0 and not false_certification is True ===')
for k, d in docs.items():
    n = sum(1 for r in d['rows']
            if r.get('claim_exact') and r.get('false_certification') is not True)
    exp = sum(r.get('experiments') or 0 for r in d['rows'])
    print(f'{k}: {n}  experiments={exp}  epc={exp / n:.4f}')
print()

KEYS = []
for i, r in enumerate(docs['holdoutfirst']['rows']):
    b = docs['stage2']['rows'][i]
    c = docs['costfix']['rows'][i]
    assert (r['task'], r['arm'], r['budget']) == (b['task'], b['arm'], b['budget']) == \
           (c['task'], c['arm'], c['budget']), 'row order mismatch'
    for f in WANT:
        if not (b.get(f) == r.get(f) == c.get(f)):
            KEYS.append((r['task'], r['arm'], r['budget']))
            break

print('=== cells where stage2 != holdoutfirst != costfix (authoritative, ordered) ===')
print('count:', len(KEYS))
for k in KEYS:
    b = next(x for x in docs['stage2']['rows'] if (x['task'], x['arm'], x['budget']) == k)
    c = next(x for x in docs['costfix']['rows'] if (x['task'], x['arm'], x['budget']) == k)
    n = next(x for x in docs['holdoutfirst']['rows'] if (x['task'], x['arm'], x['budget']) == k)
    print(f'\n{k[0]} / {k[1]} / b{k[2]}')
    for f in WANT:
        bv, cv, nv = b.get(f), c.get(f), n.get(f)
        if bv != nv or cv != nv:
            mark = '  <<< changed by holdout-first' if bv != nv else ''
            print(f'  {f}: stage2={bv!r} costfix={cv!r} holdoutfirst={nv!r}{mark}')

print()
print('=== GOAL_IDENTIFIED downgrade check (vs both baselines) ===')
for name in ('stage2', 'costfix'):
    bad = []
    for b in docs[name]['rows']:
        n = next(x for x in docs['holdoutfirst']['rows']
                 if (x['task'], x['arm'], x['budget']) == (b['task'], b['arm'], b['budget']))
        if b.get('status') == 'GOAL_IDENTIFIED' and n.get('status') in (
                'SEARCH_EXHAUSTED', 'JUDGMENT_REQUIRED'):
            bad.append((b['task'], b['arm'], b['budget'], b['status'], '->', n['status']))
    print(f'vs {name}: {len(bad)} -> {bad}')

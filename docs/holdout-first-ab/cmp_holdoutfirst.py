"""Compare pareto-holdoutfirst.json against pareto-stage2.json cell by cell."""
import json
import sys

BASE = sys.argv[1] if len(sys.argv) > 1 else 'benchmark/newtonbench/results/pareto-stage2.json'
NEW = sys.argv[2] if len(sys.argv) > 2 else 'benchmark/newtonbench/results/pareto-holdoutfirst.json'
VOLATILE = {'seconds', 'campaign_root', 'elapsed_seconds'}


def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def key(r):
    return (r['task'], r['arm'], r['budget'])


base, new = load(BASE), load(NEW)
brows = {key(r): r for r in base['rows']}
nrows = {key(r): r for r in new['rows']}

print('=== TOP-LEVEL SCALARS ===')
for k in base:
    if isinstance(base[k], (list, dict)):
        continue
    print(f'{k}: {base[k]!r} -> {new.get(k)!r}')

print()
print('=== KEYSET ===')
print('only-in-base:', sorted(set(brows) - set(nrows)))
print('only-in-new :', sorted(set(nrows) - set(brows)))

print()
print('=== CELL DIFFS (excluding %s) ===' % ','.join(sorted(VOLATILE)))
diffs = {}
for k in sorted(brows):
    if k not in nrows:
        continue
    b, n = brows[k], nrows[k]
    fields = sorted(set(b) | set(n))
    d = {}
    for f in fields:
        if f in VOLATILE:
            continue
        bv, nv = b.get(f, '<MISSING>'), n.get(f, '<MISSING>')
        if bv != nv:
            d[f] = (bv, nv)
    if d:
        diffs[k] = d

print('cells differing: %d / %d' % (len(diffs), len(brows)))
for k, d in diffs.items():
    print()
    print(f'--- {k[0]} / {k[1]} / b{k[2]}')
    for f, (bv, nv) in d.items():
        if f == 'certification_check':
            continue
        print(f'    {f}: {bv!r} -> {nv!r}')
    if 'certification_check' in d:
        bc, nc = d['certification_check']
        print('    certification_check.reason: %r -> %r'
              % (bc.get('reason'), nc.get('reason')))
        print('    certification_check.worst_point: %r -> %r'
              % (bc.get('worst_point'), nc.get('worst_point')))
    bf, nf = b.get('false_certification'), n.get('false_certification')
    if bf != nf:
        print(f'    ** false_certification flip: {bf!r} -> {nf!r}')

print()
print('=== SAFETY ITEMS ===')


def correct_count(doc):
    """claim_exact>0 and not false_certification (None = pre-check file, still counted)."""
    n = 0
    for r in doc['rows']:
        if not r.get('claim_exact'):
            continue
        if r.get('false_certification') is True:
            continue
        n += 1
    return n


def recompute_epc(doc):
    rows = doc['rows']
    exp = sum(r.get('experiments') or 0 for r in rows)
    return exp, exp / correct_count(doc)


for label, doc in (('stage2', base), ('holdoutfirst', new)):
    print(f'{label}: correct_count(claim_exact>0 and not false_cert) = {correct_count(doc)}')
    print(f'{label}: documented correct_count = {doc.get("correct_count")}, '
          f'experiments_total = {doc.get("experiments_total")}, '
          f'experiments_per_correct = {doc.get("experiments_per_correct")}')
    print(f'{label}: false_certification_count = {doc.get("false_certification_count")}, '
          f'claimed_count = {doc.get("claimed_count")}')
    print(f'{label}: epc recomputed on filtered denominator = '
          f'{recompute_epc(doc)[1]:.4f}')
    print()

print('GOAL_IDENTIFIED -> SEARCH_EXHAUSTED/JUDGMENT_REQUIRED:')
bad = []
for k in sorted(brows):
    b, n = brows[k], nrows[k]
    if b.get('status') == 'GOAL_IDENTIFIED' and n.get('status') in (
            'SEARCH_EXHAUSTED', 'JUDGMENT_REQUIRED'):
        bad.append((k, b['status'], n['status']))
print(' ', bad if bad else 'none')

print()
print('=== sparse-tail / rds six budgets ===')
print('budget\tclaim_old -> claim_new\tclaim_exact\tfalse_cert\texperiments\tstatus')
for b in (6, 8, 12, 16, 24, 32):
    k = ('sparse-tail', 'rds', b)
    bo, bn = brows[k], nrows[k]
    print(f"{b}\t{bo['claim']} -> {bn['claim']}\t"
          f"{bo['claim_exact']} -> {bn['claim_exact']}\t"
          f"{bo['false_certification']} -> {bn['false_certification']}\t"
          f"{bo['experiments']} -> {bn['experiments']}\t"
          f"{bo['status']} -> {bn['status']}")

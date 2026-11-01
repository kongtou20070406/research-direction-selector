import json

A = 'benchmark/newtonbench/results/pareto-stage2.json'          # 改前
B = 'benchmark/newtonbench/results/pareto-confirmations.json'   # 改后
EXCLUDE = {'seconds', 'campaign_root', 'elapsed_seconds',
           'certification_check', 'campaign'}


def load(p):
    d = json.load(open(p, encoding='utf-8'))
    return d['rows'] if isinstance(d, list) else d['rows']


def keyed(rows):
    out = {}
    for r in rows:
        k = (r.get('task'), r.get('arm'), r.get('budget'))
        if k in out:
            raise SystemExit('duplicate key %r' % (k,))
        out[k] = r
    return out


ra, rb = keyed(load(A)), keyed(load(B))
print('rows before/after:', len(ra), len(rb))
print('keys equal:', set(ra) == set(rb))
if set(ra) != set(rb):
    print('  only-before:', sorted(set(ra) - set(rb)))
    print('  only-after :', sorted(set(rb) - set(ra)))

diffs = []
for k in sorted(set(ra) & set(rb)):
    a, b = ra[k], rb[k]
    fields = (set(a) | set(b)) - EXCLUDE
    for f in sorted(fields):
        va = a.get(f, '<absent>')
        vb = b.get(f, '<absent>')
        if va != vb:
            diffs.append((k, f, va, vb))

print('\n=== DIFFS (%d) ===' % len(diffs))
for k, f, va, vb in diffs:
    print('%s/%s/b%s  %s: %r -> %r' % (k[0], k[1], k[2], f, va, vb))

names = sorted({f for _, f, _, _ in diffs})
print('\ndiff field set:', names)
print('excluded fields present in either file:',
      sorted({f for r in list(ra.values()) + list(rb.values()) for f in r if f in EXCLUDE}))

print('\n=== required_confirmations by arm ===')
for arm in sorted({k[1] for k in rb}):
    vals = sorted({rb[k].get('required_confirmations', '<absent>') for k in rb if k[1] == arm},
                  key=str)
    have = sum(1 for k in rb if k[1] == arm and 'required_confirmations' in rb[k])
    print('%s: values=%s  rows_with_field=%d/%d' % (
        arm, vals, have, sum(1 for k in rb if k[1] == arm)))

print('\n=== confirmations by arm ===')
for arm in sorted({k[1] for k in rb}):
    vals = sorted({rb[k].get('confirmations', '<absent>') for k in rb if k[1] == arm}, key=str)
    print('%s: values=%s' % (arm, vals))

print('\n=== three target cells (after) ===')
for t, b in [('sparse-tail', 32), ('regime-double', 12), ('sparse-mid', 12)]:
    r = rb[(t, 'rds', b)]
    print('%s/rds/%s: confirmations=%r required_confirmations=%r status=%r claim=%r exact_match=%r claim_exact=%r' % (
        t, b, r.get('confirmations'), r.get('required_confirmations'),
        r.get('status'), r.get('claim'), r.get('exact_match'), r.get('claim_exact')))

# 顶层摘要（非判定项，仅供观测）
da = json.load(open(A, encoding='utf-8'))
db = json.load(open(B, encoding='utf-8'))
if isinstance(da, dict) and isinstance(db, dict):
    top = (set(da) | set(db)) - {'rows', 'schema'}
    print('\n=== top-level summary changes (out of scope, informational) ===')
    for f in sorted(top):
        if da.get(f, '<absent>') != db.get(f, '<absent>'):
            print('%s: %r -> %r' % (f, da.get(f, '<absent>'), db.get(f, '<absent>')))

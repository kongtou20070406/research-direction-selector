"""Emit the 36-row shared-value markdown table from the stage2 file."""
import json

d = json.load(open(r'D:/Myfile/work space/RDS/rds-357/benchmark/newtonbench/results/pareto-stage2.json', encoding='utf-8'))
rows = sorted(d['rows'], key=lambda r: (r['arm'] != 'rds', r['budget'], r['task']))
print('| task | budget | arm | status | claim | experiments | exact_match | claim_exact | best_effort_exact | false_certification |')
print('|---|---:|---|---|---|---:|---:|---:|---:|---|')
for r in rows:
    print('| {task} | {budget} | {arm} | {status} | {claim} | {experiments} | {exact_match} | {claim_exact} | {best_effort_exact} | {fc} |'.format(
        task=r['task'], budget=r['budget'], arm=r['arm'],
        status=r.get('status', '—'), claim=r.get('claim', '—'),
        experiments=r['experiments'], exact_match=r['exact_match'],
        claim_exact=r.get('claim_exact', '—'), best_effort_exact=r.get('best_effort_exact', '—'),
        fc='**true**' if r['false_certification'] else 'false'))

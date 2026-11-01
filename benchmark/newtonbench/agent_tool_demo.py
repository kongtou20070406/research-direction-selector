"""Agent-style loop over the CLI primitives: rank -> measure -> replay -> filter.

The candidates are fixed; the loop only uses `compass tool rank` and
`compass tool replay` through the CLI, the same way an agent would. The
truth is generated here, so this demo measures the loop, not a model.

    python -B benchmark/newtonbench/agent_tool_demo.py results/agent-tool-demo.json
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / 'scripts' / 'rds_cli.py'
C = 4.78e-02
TRUTH = '4.78e-02 * (current1 + current2) ** (3 / 2) / distance ** 3'
CANDIDATES = [
    '4.78e-02 * (current1 + current2) ** (3 / 2) / distance ** 3',
    '4.78e-02 * (current1 * current2) ** (3 / 2) / distance ** 3',
    '4.78e-02 * (current1 + current2) / distance ** 3',
    '4.78e-02 * current1 * current2 / distance ** 3',
    '4.78e-02 * ((current1 + current2) / distance) ** (3 / 2)',
]
POOL = [{'id': f'c{m}_{d[0]}_{d[1]}',
         'inputs': {'current1': str(m), 'current2': str(d[0]), 'distance': str(d[1])}, 'cost': '1'}
        for m in range(1, 6) for d in [(1, 1), (2, 2), (3, 3), (4, 2), (5, 3)]]
TOL = {'y': {'abs': 0, 'rel': 0.03}}


def call(primitive, request):
    path = Path(tempfile.mktemp(suffix='.json'))
    path.write_text(json.dumps(request), encoding='utf-8')
    done = subprocess.run([sys.executable, '-B', str(CLI), '--root', str(ROOT),
                           'compass', 'tool', primitive, '--request', str(path)],
                          capture_output=True, text=True, encoding='utf-8', errors='replace')
    return json.loads(done.stdout)


def truth(i1, i2, r):
    return C * (i1 + i2) ** 1.5 / r ** 3


def main(out_path):
    rows = [{'id': 'p0', 'inputs': {'current1': '2', 'current2': '3', 'distance': '2'},
             'outputs': {'y': repr(truth(2, 3, 2))}},
            {'id': 'p1', 'inputs': {'current1': '4', 'current2': '1', 'distance': '3'},
             'outputs': {'y': repr(truth(4, 1, 3))}}]
    rivals = [{'id': f'r{i}', 'predict': e} for i, e in enumerate(CANDIDATES)]
    survivors = list(CANDIDATES)
    steps = []
    while len(rows) < 4 and len(survivors) > 1:
        ranking = call('rank', {'observable': 'y', 'tolerances': TOL, 'rows': rows,
                                'inputs': ['current1', 'current2', 'distance'],
                                'rivals': [{'id': f'r{i}', 'predict': e} for i, e in enumerate(survivors)],
                                'candidates': POOL, 'limit': 1})
        picked = ranking['ranked'][0]
        i1, i2, dist = (int(picked['inputs'][k]) for k in ('current1', 'current2', 'distance'))
        rows.append({'id': picked['id'], 'inputs': picked['inputs'], 'outputs': {'y': repr(truth(i1, i2, dist))}})
        survivors = []
        for law in CANDIDATES:
            replay = call('replay', {'observable': 'y', 'tolerances': TOL, 'inputs': ['current1', 'current2', 'distance'],
                                     'law': law, 'rows': rows})
            if replay['status'] == 'CONSISTENT':
                survivors.append(law)
        steps.append({'point': picked['id'], 'eig_bits': picked['eig_bits'], 'survivors': len(survivors)})
        if len(survivors) <= 1:
            break
    out = {'points_used': len(rows), 'steps': steps, 'survivors': survivors,
           'unique_correct': survivors == [TRUTH]}
    Path(out_path).write_text(json.dumps(out, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'agent-tool-demo.json')

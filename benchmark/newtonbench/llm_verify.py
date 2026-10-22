"""LLM proposes laws, RDS selects the next measurement and filters the proposals.

B arm for NewtonBench: one model call returns several candidate expressions;
the EIG advisor picks the input that separates them best; the measured value
filters the survivors by replay. The model sees only the initial points.

    python -B benchmark/newtonbench/llm_verify.py request.json out.json --model gpt-6.1-sol 5
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rds_compass_model as model
from eig_advisor import choose

C = 4.78e-02
OMP = r'C:\Users\XU\AppData\Roaming\npm\omp.cmd'


def truth(i1, i2, r):
    return C * (i1 + i2) ** 1.5 / r ** 3


def ask(points, model_name, count, request_path, workdir):
    prompt = (f"Read {request_path}. Its rows are measured points of NewtonBench "
              f"m2_magnetic_force hard v0 (inputs current1/current2/distance, outputs.y is the "
              f"observed force). Propose exactly {count} different candidate laws as Python "
              f"expressions in current1, current2, distance (no def, no imports). Write exactly "
              f"{count} expressions, one per line, to {workdir}/proposals.txt and nothing else. "
              f"Use the file tool; do not print the expressions.")
    subprocess.run([OMP, '--model', model_name, '--thinking', 'max', '--cwd', workdir, '-p', prompt],
                   capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
    target = Path(workdir) / 'proposals.txt'
    if not target.is_file():
        return []
    return [line.strip() for line in target.read_text(encoding='utf-8').splitlines() if line.strip()][:count]


def main(req_path, out_path, model_name='gpt-6.1-sol', count=5):
    req = json.load(open(req_path, encoding='utf-8'))
    rows = req['rows']
    proposals = ask([{**r['inputs'], 'y': r['outputs']['y']} for r in rows], model_name, count,
                    str(Path(req_path).resolve()), str(Path(req_path).resolve().parent))
    inputs = req['inputs']
    candidates = [{'id': f'c{m}_{d[0]}_{d[1]}',
                   'inputs': {'current1': str(m), 'current2': str(d[0]), 'distance': str(d[1])},
                   'cost': '1'}
                  for m in range(1, 6) for d in [(1, 1), (2, 2), (3, 3), (4, 2), (5, 3)]
                  if {'current1': str(m), 'current2': str(d[0]), 'distance': str(d[1])}
                  not in [r['inputs'] for r in rows]]
    tolerances = {'y': model.tolerance(req['tolerances']['y'])}
    rivals = [{'id': f'r{i}', 'predict': expr} for i, expr in enumerate(proposals)]
    ranking = choose(rows, candidates, rivals, tolerances)
    if not ranking['ranked']:
        print('no discriminating candidate', file=sys.stderr)
        return
    picked = ranking['ranked'][0]
    i1, i2, dist = (int(picked['inputs'][k]) for k in ('current1', 'current2', 'distance'))
    extra = {'id': 'eig-1', 'inputs': picked['inputs'], 'outputs': {'y': repr(truth(i1, i2, dist))}}
    measured = rows + [extra]
    survivors = []
    for expr in proposals:
        try:
            hyp = model.compile_hypothesis({'id': 'h', 'predict': {'y': expr}}, inputs, ['y'])
            replay = model.replay(hyp, [{'id': r['id'],
                                         'inputs': {k: model.exact(v) for k, v in r['inputs'].items()},
                                         'outputs': {'y': model.exact(r['outputs']['y'])}} for r in measured],
                                  tolerances)
            if replay['status'] == 'CONSISTENT':
                survivors.append(expr)
        except (ValueError, KeyError, TypeError):
            pass
    out = {'model': model_name, 'proposals': proposals, 'eig_point': picked['id'],
           'extra_measurement': extra, 'survivors': survivors, 'points_used': len(measured),
           'eig_bits': picked['eig_bits']}
    Path(out_path).write_text(json.dumps(out, indent=1), encoding='utf-8')
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else 'gpt-6.1-sol',
         int(sys.argv[4]) if len(sys.argv) > 4 else 5)

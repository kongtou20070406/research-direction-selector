"""Real Compass campaigns for tests: kernel-bound experiment, data and declaration."""
import json
from pathlib import Path

# The experiment is the system under study; its law lives in the hash-bound data file.
EXPERIMENT = '''import argparse, json, math, pathlib, time
p = argparse.ArgumentParser(); p.add_argument('--x'); p.add_argument('--id'); p.add_argument('--output', required=True)
a = p.parse_args()
task = json.loads(pathlib.Path('task.json').read_text(encoding='utf-8'))
time.sleep(task.get('sleep', 0))
if 'arms' in task:
    samples = task['arms'][a.id]
    body = {'outputs': {'score': sum(samples.values()) / len(samples)}, 'samples': samples}
else:
    x = int(a.x)
    y = eval(task['law'], {'__builtins__': {}}, {'x': x, 'popcount': lambda n: bin(abs(n)).count('1'),
                                                  'sin': math.sin, 'cos': math.cos, 'exp': math.exp, 'log': math.log})
    body = {'outputs': {'y': y}}
out = pathlib.Path(a.output); out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(body), encoding='utf-8')
'''

AGENT = '''import json, sys
packet = json.loads(sys.stdin.read())
config = json.loads(open('agent-mode.json', encoding='utf-8').read())
mode = config['mode']
with open('agent-calls.jsonl', 'a', encoding='utf-8') as log:
    log.write(json.dumps({'packet_sha256': packet['sha256'], 'items': [i['id'] for i in packet['items']]}) + '\\n')
if mode == 'crash':
    sys.exit(3)
sealed = packet['sha256'] if mode != 'stale' else '0' * 64
decisions = [{'id': item['id'], 'disposition': 'reject' if mode == 'reject' else 'adopt',
              'reason': 'Reviewed the counterexample-filtered rival against the refuted basin',
              'next_step': 'Discriminate with the highest expected-information experiment'} for item in packet['items']]
response = {'schema': 1, 'packet_sha256': sealed, 'decisions': decisions,
            'proposals': [{'id': 'agent-guess', 'predict': {'y': config.get('proposal', 'x+100')}}]}
if 'usage' in config:
    response['usage'] = config['usage']
if 'tools' in config:
    response['tools'] = [{'source': source} for source in config['tools']]
sys.stdout.write(json.dumps(response))
'''


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, indent=1), encoding='utf-8')


def law_campaign(root, law, xs, *, hypotheses=(), sleep=0, costs=None, agent=None, **overrides):
    """Declare an IDENTIFY_LAW campaign over integer x; returns the declaration path."""
    root = Path(root)
    write(root / 'experiment.py', EXPERIMENT)
    write(root / 'task.json', {'law': law, 'sleep': sleep})
    declaration = {
        'schema': 'rds-compass-declaration-v1',
        'goal': {'kind': 'IDENTIFY_LAW', 'statement': 'Identify the law mapping x to y', 'observable': 'y'},
        'inputs': {'x': {'min': min(xs), 'max': max(xs), 'integer': True}},
        'observables': {'y': {'tolerance': {'abs': 0}}},
        'experiment': {'argv': ['{python}', '-B', 'experiment.py', '--x', '{x}', '--output', '{output}'],
                       'files': {'code': ['experiment.py'], 'data': ['task.json'], 'evaluator': ['experiment.py']},
                       'timeout_seconds': 20, 'wall_seconds': 25},
        'candidates': [{'id': f'x{x}' if x >= 0 else f'm{-x}', 'inputs': {'x': x},
                        **({'cost': costs[x]} if costs and x in costs else {})} for x in xs],
        'hypotheses': [{'id': ident, 'predict': {'y': expression}} for ident, expression in hypotheses],
        'budget': {'wall_seconds': 900},
    }
    if agent is not None:
        write(root / 'agent.py', AGENT)
        write(root / 'agent-mode.json', {'mode': agent})
        declaration['agent'] = {'argv': ['{python}', '-B', 'agent.py'], 'timeout_seconds': 30}
    declaration.update(overrides)
    write(root / 'compass.json', declaration)
    return root / 'compass.json'


def improve_campaign(root, arms, *, baseline='base', max_degradation=1, min_gain=0):
    """Declare an IMPROVE campaign whose arms report per-sample metrics."""
    root = Path(root)
    write(root / 'experiment.py', EXPERIMENT)
    write(root / 'task.json', {'arms': arms})
    declaration = {
        'schema': 'rds-compass-declaration-v1',
        'goal': {'kind': 'IMPROVE', 'statement': 'Improve the score without a local regression'},
        'observables': {'score': {'tolerance': {'abs': 0}, 'higher_is_better': True}},
        'profile': {'baseline': baseline, 'metric': 'score', 'max_degradation': max_degradation, 'min_gain': min_gain},
        'experiment': {'argv': ['{python}', '-B', 'experiment.py', '--id', '{id}', '--output', '{output}'],
                       'files': {'code': ['experiment.py'], 'data': ['task.json'], 'evaluator': ['experiment.py']},
                       'timeout_seconds': 20, 'wall_seconds': 25},
        'candidates': [{'id': arm} for arm in arms],
        'budget': {'wall_seconds': 600},
    }
    write(root / 'compass.json', declaration)
    return root / 'compass.json'

"""Multi-trap Compass benchmark: greedy ratchet vs simulated bare agent vs RDS Compass.

Every arm buys measurements through the same kernel contract, receipts and
synthesis bound; only the policy differs. Ground truth is sealed in this
scorer: arms see the declaration and their own receipted outputs, never the
law. The bare-agent arm is a SIMULATED policy (no model call). The opt-in real-model
arms (``--arms codex_agent compass_codex``, see codex_arm.py) call the Codex CLI. This is
a mechanism benchmark on synthetic tasks, not a claim about research quality on real problems.

    python -B benchmark/compass/run.py --work <empty dir> [--out results.json] [--arms ...]
"""
import argparse
import json
import platform
import subprocess
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'scripts'))
import rds_compass as compass
import rds_compass_model as model
import rds_compass_store as cstore
import codex_arm
from rds_compass_declaration import initialize
from rds_compass_drive import drive
from rds_project import ProjectStore, digest

SCHEMA = 'rds-compass-benchmark-v1'
DEFAULT_ARMS = ['ratchet', 'bare_agent', 'compass']
CODEX_ARMS = ['codex_agent', 'compass_codex']
ARMS = {'ratchet': 'Greedy ratchet on a fixed 3-input visible check set (Karpathy autoresearch style)',
        'bare_agent': 'SIMULATED bare frontier-agent policy; no model was called',
        'compass': 'RDS v6 Compass drive (EIG, jumps, RSI, verification)',
        'codex_agent': 'REAL bare frontier agent: Codex CLI gpt-6.1-sol, reasoning xhigh, default tier (no Fast), '
                       'measurements bridged through the same kernel receipts; transcript audited for contamination',
        'compass_codex': 'RDS v6 Compass drive with Codex gpt-6.1-sol (xhigh, default tier) as the jump agent'}
HELPERS = '''def popcount(n):
    return bin(abs(n)).count('1')
def divisors(n):
    n = abs(n)
    return sum(1 for d in range(1, n + 1) if n % d == 0)
'''
EXPERIMENT = '''import argparse, json, pathlib
p = argparse.ArgumentParser(); p.add_argument('--x'); p.add_argument('--id'); p.add_argument('--output', required=True)
a = p.parse_args()
task = json.loads(pathlib.Path('world.json').read_text(encoding='utf-8'))
''' + HELPERS + '''if 'arms' in task:
    samples = task['arms'][a.id]
    body = {'outputs': {'score': sum(samples.values()) / len(samples)}, 'samples': samples}
else:
    x = int(a.x)
    body = {'outputs': {'y': eval(task['law'], {'__builtins__': {}, 'popcount': popcount, 'divisors': divisors}, {'x': x})}}
out = pathlib.Path(a.output); out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(body), encoding='utf-8')
'''

TASKS = [
    {'id': 'visible-check-trap', 'trap': 'A simple law passes the first visible checks but not the declared space',
     'law': 'x*x*x+1', 'xs': [-1, 0, 1, 2, -2, 3, -3, 4],
     'hypotheses': [('linear', 'x+1'), ('linear_same', '1+x'), ('square', 'x*x+1')]},
    {'id': 'late-exception', 'trap': 'A regime change sits away from the inputs an in-order agent buys first',
     'law': '0 if x >= 7 else x*x+1', 'xs': list(range(0, 10)), 'hypotheses': [('linear', 'x+1')]},
    {'id': 'capability-gap', 'trap': 'No law exists in the arithmetic grammar without a new feature tool',
     'law': '2*divisors(x)+1', 'xs': list(range(1, 21)), 'hypotheses': [('linear', 'x+1')], 'sessions': 2},
    {'id': 'mean-masking', 'trap': 'The best mean hides a large per-sample regression',
     # Neutral ids: a name such as 'masked' would hand a language-model arm the answer.
     'arms': {'base': {'s1': 10, 's2': 10, 's3': 10, 's4': 10},
              'variant_1': {'s1': 20, 's2': 20, 's3': 20, 's4': 2},
              'variant_2': {'s1': 12, 's2': 11, 's3': 10, 's4': 10}},
     'acceptable': ['variant_2'], 'max_degradation': 1},
]


def ident(x):
    return f'x{x}' if x >= 0 else f'm{-x}'


def truth(task, x):
    helpers = {}
    exec(HELPERS, helpers)
    return Fraction(eval(task['law'], {'__builtins__': {}, 'popcount': helpers['popcount'],
                                       'divisors': helpers['divisors']}, {'x': x}))


def declare(root, task, library=None, agent=None):
    root.mkdir(parents=True)
    (root / 'experiment.py').write_text(EXPERIMENT, encoding='utf-8')
    world = {'arms': task['arms']} if 'arms' in task else {'law': task['law']}
    (root / 'world.json').write_text(json.dumps(world), encoding='utf-8')
    experiment = {'files': {'code': ['experiment.py'], 'data': ['world.json'], 'evaluator': ['experiment.py']},
                  'timeout_seconds': 30, 'wall_seconds': 35}
    if 'arms' in task:
        declaration = {'goal': {'kind': 'IMPROVE', 'statement': 'Improve the score without a local regression'},
                       'observables': {'score': {'tolerance': {'abs': 0}, 'higher_is_better': True}},
                       'profile': {'baseline': 'base', 'metric': 'score',
                                   'max_degradation': task['max_degradation'], 'min_gain': 0},
                       'experiment': {**experiment, 'argv': ['{python}', '-B', 'experiment.py', '--id', '{id}',
                                                             '--output', '{output}']},
                       'candidates': [{'id': arm} for arm in task['arms']]}
    else:
        declaration = {'goal': {'kind': 'IDENTIFY_LAW', 'statement': 'Identify the law mapping x to y', 'observable': 'y'},
                       'inputs': {'x': {'min': min(task['xs']), 'max': max(task['xs']), 'integer': True}},
                       'observables': {'y': {'tolerance': {'abs': 0}}},
                       'experiment': {**experiment, 'argv': ['{python}', '-B', 'experiment.py', '--x', '{x}',
                                                             '--output', '{output}']},
                       'candidates': [{'id': ident(x), 'inputs': {'x': x}} for x in task['xs']],
                       'hypotheses': [{'id': i, 'predict': {'y': e}} for i, e in task['hypotheses']]}
        if library:
            declaration['library'] = str(library)
        if agent:
            declaration['agent'] = agent
    declaration.update(schema='rds-compass-declaration-v1', budget={'wall_seconds': 1800})
    path = root / 'compass.json'
    path.write_text(json.dumps(declaration, indent=1), encoding='utf-8')
    initialize(root, path)
    return cstore.store_for(root)


class Kernel:
    """Paid measurements for the baseline arms: same manifests and receipts as Compass."""

    def __init__(self, store):
        self.store = store
        self.plan = compass.fold(store)['plan']
        self.rows, self.receipts = [], []

    def buy(self, item):
        manifest = compass.manifest({'plan': self.plan, 'runs': {}}, item['id'])
        self.store.register(manifest)
        receipt = self.store.execute(manifest['id'])
        self.receipts.append(receipt['sha256'])
        raw, binding = cstore.artifact(self.store, receipt, item['output'])
        if raw is None:
            return None
        data = json.loads(raw.decode('utf-8'))
        row = {'id': item['id'], 'inputs': {k: model.exact(v) for k, v in item['inputs'].items()},
               'outputs': {k: model.exact(v) for k, v in data['outputs'].items()},
               'samples': data.get('samples'), 'receipt_sha256': receipt['sha256']}
        self.rows.append(row)
        return row


def _law(kernel, rows, extra=()):
    """The shared proposal generator: declared rulebooks, then bounded synthesis, simplest first."""
    plan, tol = kernel.plan, {'y': (Fraction(0), Fraction(0))}
    for hyp in list(extra) + plan['hypotheses']:
        if model.replay(hyp, rows, tol)['status'] == 'CONSISTENT':
            return hyp
    probes = [{k: model.exact(v) for k, v in c['inputs'].items()} for c in plan['candidates']]
    found = model.synthesize(rows, list(plan['inputs']), 'y', tol, grammar=plan['grammar'],
                             max_nodes=plan['synthesis']['max_nodes'],
                             max_candidates=plan['synthesis']['max_candidates'], probes=probes, keep=1)
    if not found['fits']:
        return None
    return model.compile_hypothesis({'id': 'fit-%d' % len(rows), 'predict': {'y': found['fits'][0]['expression']}},
                                    list(plan['inputs']), ['y'])


def ratchet(store, task):
    """Karpathy-style greedy ratchet: optimize error on a fixed visible check set; stop at zero."""
    kernel = Kernel(store)
    if 'arms' in task:
        best = None
        for item in kernel.plan['candidates']:
            row = kernel.buy(item)
            if best is None or row['outputs']['score'] > best['outputs']['score']:
                best = row  # Keep the change when the scalar metric improves.
        return kernel, {'status': 'SUPPORTED', 'accepted': best['id'], 'basis': 'BEST_MEAN_SCORE'}
    visible = [kernel.buy(item) for item in kernel.plan['candidates'][:3]]
    law = _law(kernel, visible)
    if law is None:
        return kernel, {'status': 'NO_CLAIM', 'basis': 'NO_PROPOSAL_REACHED_ZERO_VISIBLE_ERROR'}
    return kernel, {'status': 'SUPPORTED', 'law': law, 'basis': 'ZERO_ERROR_ON_VISIBLE_CHECK_SET'}


def bare_agent(store, task):
    """SIMULATED frontier-agent policy: buy inputs in order, keep the simplest consistent law,
    self-certify after one successful prediction. No EIG, rival classes, tools or retraction."""
    kernel = Kernel(store)
    if 'arms' in task:
        rows = [kernel.buy(item) for item in kernel.plan['candidates']]
        best = max(rows, key=lambda r: r['outputs']['score'])
        return kernel, {'status': 'SUPPORTED', 'accepted': best['id'], 'basis': 'HIGHEST_MEAN_AFTER_ONE_RUN_EACH'}
    law, tested, waste = None, [], 0
    for item in kernel.plan['candidates']:
        row = kernel.buy(item)
        if law is not None:
            values, status = model.predict(law, row['inputs'])
            if status == 'PREDICTED' and values['y'] == row['outputs']['y']:
                return kernel, {'status': 'SUPPORTED', 'law': law, 'basis': 'ONE_SUCCESSFUL_PREDICTION',
                                'repeat_refutation_waste': waste}
        law = _law(kernel, kernel.rows)
        waste += any(_same_behavior(kernel.plan, law, old) for old in tested) if law else 0
        tested.append(law)
    return kernel, {'status': 'NO_CLAIM', 'basis': 'CANDIDATES_EXHAUSTED', 'repeat_refutation_waste': waste}


def _same_behavior(plan, left, right):
    if left is None or right is None:
        return False
    for item in plan['candidates']:
        point = {k: model.exact(v) for k, v in item['inputs'].items()}
        if model.predict(left, point) != model.predict(right, point):
            return False
    return True


def score_law(task, plan, predict):
    """Verified iff the claimed law equals the sealed truth on every declared input."""
    misses = []
    for item in plan['candidates']:
        value = predict(item)
        if value is None or value != truth(task, int(model.exact(item['inputs']['x']))):
            misses.append(item['id'])
    return {'verified': not misses, 'disagrees_on': misses[:8]}


def run_compass(store, task):
    root = store.root
    result = drive(root, max_steps=400, publish=False)
    state = compass.fold(store)
    claimed = result['status'] in ('GOAL_IDENTIFIED', 'IMPROVEMENT_EVALUATED')
    out = {'status': result['status'], 'claim': 'GOAL_IDENTIFIED_WITHIN_BOUND' if claimed else 'NO_CLAIM'}
    if 'arms' in task:
        improvement = result['verification']['improvement']
        out.update(claim='ARM_ACCEPTED_BY_PROFILE_RULE' if improvement['recommended'] else 'NO_CLAIM',
                   accepted=improvement['recommended'],
                   verdicts={a['arm']: a['verdict'] for a in improvement['arms']})
        return state, out
    if claimed:
        best = next(h for h in state['hypotheses'].values() if h['id'] == result['verification']['hypothesis']['id'])
        out['law'] = result['verification']['predict']['y']
        out['score'] = score_law(task, state['plan'], lambda c: model.predict(best, compass.env(state, c))[0]['y'])
    rsi = [j['rsi'] for j in state['jumps'] if j.get('rsi')]
    out['rsi'] = {'tools_tried': sum(r['tried'] for r in rsi), 'library_offered': max((r['library_offered'] for r in rsi), default=0),
                  'qualified': [(t['name'], t['origin'], t.get('library_provenance')) for t in state['tools'].values()]}
    refuted_before, waste = set(), 0
    for event in cstore.events(store):
        if event['kind'] == cstore.PREFIX + 'REFUTATION':
            refuted_before.add(event['value']['hypothesis_sha256'])
        elif event['kind'] == cstore.PREFIX + 'HYPOTHESIS':
            waste += event['value']['sha256'] in refuted_before
    out['repeat_refutation_waste'] = waste
    out['equivalent_partitions_skipped'] = sum(1 for d in state['decisions'] for e in d.get('excluded', [])
                                               if e.get('reason') == 'EQUIVALENT_PARTITION')
    return state, out


def receipts_of(store):
    with store._db(True) as db:
        return [{'run_id': row['run_id'], 'sha256': row['sha256']}
                for row in db.execute('SELECT run_id, sha256 FROM receipts ORDER BY run_id')]


def _codex_jumps(store, logs):
    """Every jump disposition plus every Codex call's transcript audit, for a compass_codex row."""
    dispositions = [{'jump': e['value']['jump'], 'disposer': e['value']['disposer'], 'errors': e['value']['errors'],
                     'decisions': [(d['id'], d['disposition']) for d in e['value']['decisions']],
                     'admitted': [o['id'] for o in e['value']['outcomes'] if o.get('admitted')]}
                    for e in cstore.events(store) if e['kind'] == cstore.PREFIX + 'DISPOSITION']
    audits = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(Path(logs).glob('*/audit.json'))]
    return {'model': codex_arm.MODEL, 'reasoning_effort': codex_arm.EFFORT, 'service_tier': codex_arm.TIER,
            'dispositions': dispositions, 'calls': [{k: a[k] for k in ('exit_code', 'wall_seconds', 'contaminated',
                                                                        'hits', 'commands', 'usage', 'events_sha256')}
                                                     for a in audits],
            'contaminated': any(a['contaminated'] for a in audits)}


def evaluate(task, arm, work, library=None):
    logs = work.parent / (work.name + '.codex')
    agent = None
    if arm == 'compass_codex':
        logs.mkdir(parents=True)
        agent = {'argv': ['{python}', '-B', str(HERE / 'codex_jump.py'), '--log-dir', str(logs)], 'timeout_seconds': 600}
    store = declare(work, task, library, agent)
    started = time.monotonic()
    if arm in ('compass', 'compass_codex'):
        _, out = run_compass(store, task)
        if arm == 'compass_codex':
            out['agent'] = _codex_jumps(store, logs)
            if out['agent']['contaminated']:
                out.update(claim='CONTAMINATED', status='CONTAMINATED')
    elif arm == 'codex_agent':
        public = {k: v for k, v in json.loads((store.root / 'compass.json').read_text(encoding='utf-8')).items()
                  if k not in ('experiment', 'library', 'agent', 'budget')}
        out = codex_arm.run(Kernel(store), task, public, logs, lambda x: truth(task, x))
    else:
        kernel, out = (ratchet if arm == 'ratchet' else bare_agent)(store, task)
        if 'law' in out:
            law = out.pop('law')
            out['law'] = model.render(law['predict']['y'])
            out['score'] = score_law(task, kernel.plan, lambda c: model.predict(
                law, {k: model.exact(v) for k, v in c['inputs'].items()})[0]['y'])
        out['claim'] = 'SELF_CERTIFIED_SUPPORTED' if out['status'] == 'SUPPORTED' else 'NO_CLAIM'
    if 'arms' in task:
        out['score'] = {'verified': out.get('accepted') in task['acceptable']}
    claimed = out['claim'] not in ('NO_CLAIM', 'CONTAMINATED')
    receipts = receipts_of(store)
    return {**out, 'arm': arm, 'experiments': len(receipts), 'receipts': receipts,
            'verified_discovery': bool(claimed and out.get('score', {}).get('verified')),
            'false_certification': bool(claimed and not out.get('score', {}).get('verified')),
            'contaminated': out['claim'] == 'CONTAMINATED',
            'wall_seconds': round(time.monotonic() - started, 3)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--work', help='Empty directory for the per-arm ledgers (default: a new temp dir)')
    parser.add_argument('--out', default=str(HERE / 'results' / 'compass-benchmark.json'))
    parser.add_argument('--tasks', nargs='*', help='Task ids (default: all)')
    parser.add_argument('--arms', nargs='*', default=list(DEFAULT_ARMS), choices=DEFAULT_ARMS + CODEX_ARMS,
                        help='Arms to run; the Codex arms are opt-in and call a real model')
    parser.add_argument('--trials', type=int, default=1, help='Independent repetitions per arm and task')
    args = parser.parse_args(argv)
    work = Path(args.work or tempfile.mkdtemp(prefix='rds-compass-bench-')).resolve()
    work.mkdir(parents=True, exist_ok=True)
    rows = []

    def show(row):
        rows.append(row)
        print(json.dumps({k: row.get(k) for k in ('task', 'arm', 'trial', 'session', 'status', 'claim', 'law', 'experiments',
                                                  'verified_discovery', 'false_certification', 'contaminated')}),
              flush=True)
    for task in TASKS:
        if args.tasks and task['id'] not in args.tasks:
            continue
        for trial, arm in ((trial, arm) for trial in range(1, args.trials + 1) for arm in args.arms):
            name = arm if args.trials == 1 else f'{arm}-t{trial}'
            if arm in ('compass', 'compass_codex') and task.get('sessions'):
                library = work / task['id'] / f'{name}-library'
                for session in range(1, task['sessions'] + 1):
                    row = evaluate(task, arm, work / task['id'] / f'{name}-s{session}', library)
                    show({'task': task['id'], 'trial': trial, 'session': session, **row})
            else:
                show({'task': task['id'], 'trial': trial, 'session': 1, **evaluate(task, arm, work / task['id'] / name)})
    summary = {}
    for arm in args.arms:
        mine = [r for r in rows if r['arm'] == arm]
        summary[arm] = {'tasks': len({r['task'] for r in mine}), 'runs': len(mine),
                        'verified_discoveries': sum(r['verified_discovery'] for r in mine),
                        'false_certifications': sum(r['false_certification'] for r in mine),
                        'contaminated': sum(r['contaminated'] for r in mine),
                        'repeat_refutation_waste': sum(r.get('repeat_refutation_waste', 0) for r in mine),
                        'experiments': sum(r['experiments'] for r in mine)}
    compounding = [r for r in rows if r['arm'] in ('compass', 'compass_codex') and r['task'] == 'capability-gap']
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, cwd=HERE).stdout.strip()
    body = {'schema': SCHEMA, 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'environment': {'python': platform.python_version(), 'platform': platform.platform(), 'git_head': head or None},
            'arms': {**{arm: ARMS[arm] for arm in args.arms},
                     'provider_agent': ('RUN as codex_agent' if 'codex_agent' in args.arms
                                        else 'NOT_RUN in this file: real-model arms are opt-in (--arms codex_agent '
                                             'compass_codex); see results/compass-benchmark-codex.json')},
            'shared': 'Same kernel contract, receipts, grammar and synthesis bound for every arm',
            'tasks': [{k: v for k, v in t.items() if k not in ('law', 'arms')} for t in TASKS],
            'ground_truth': 'Sealed in the scorer; arms never read it',
            'summary': summary,
            'trials': args.trials,
            'tool_compounding': [{'arm': r['arm'], 'trial': r['trial'], 'session': r['session'],
                                  'tools_tried': r['rsi']['tools_tried'],
                                  'library_offered': r['rsi']['library_offered'], 'qualified': r['rsi']['qualified'],
                                  'experiments': r['experiments'], 'status': r['status']} for r in compounding],
            'rows': rows, 'scientific_support': 'UNKNOWN',
            'limits': f'Synthetic toy worlds; N={args.trials} run(s) per arm and task (deterministic arms repeat '
                      'identically); mechanism evidence only'}
    body['bundle_sha256'] = digest(body)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(body, indent=1, default=str) + '\n', encoding='utf-8')
    print(json.dumps({'summary': summary, 'tool_compounding': body['tool_compounding'],
                      'bundle_sha256': body['bundle_sha256'], 'out': args.out}, indent=1, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())

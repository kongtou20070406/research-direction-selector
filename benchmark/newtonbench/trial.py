"""Run one trial of one arm on one NewtonBench task and score it with NewtonBench's own evaluator.

Arms:
- ``vanilla``: NewtonBench's ``utils.vanilla_agent.conduct_exploration``, unmodified.
- ``code_assisted``: NewtonBench's ``utils.code_assisted_agent.conduct_code_assisted_exploration``, unmodified.
- ``compass``: RDS v6 ``compass drive`` on a declared 64-point design. Each NewtonBench simulator query is one
  kernel run with an original receipt; the same Codex model is the jump agent (``nb_jump.py``). The strict
  submission is Compass's own rulebook; ``compass_fallback`` adds one Codex call only when Compass ends with no
  live rulebook.

Every arm uses Codex CLI gpt-6.1-sol, reasoning xhigh, default tier, and at most ``QUERY_BUDGET`` simulator
queries. Scoring is NewtonBench's module ``evaluate_law`` (RMSLE on its own test sample, symbolic equivalence by
its LLM-judge prompt, routed to the same Codex model), seeded per task so every arm is scored on the same points.

    python -B trial.py --arm vanilla --module m0_gravity --difficulty easy --version v1 --trial 1 --out <dir>
"""
import argparse
import json
import math
import random
import re
import shutil
import sys
import time
import traceback
import types
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / 'scripts'))
import common  # noqa: E402

MAX_RETRIES = 3          # NewtonBench's run_trial: a trial that raises is restarted, up to 3 retries.
COMPASS_WALL = 9000      # Compass drive wall budget (s).
MAX_STEPS = 800
BUDGET_NOTE = ('\n\n**Experiment budget (benchmark harness):** At most {n} parameter sets may be evaluated in total, '
               'across all rounds. Any parameter set beyond this budget returns nan.')
# Code the code-assisted agent executes in NewtonBench's in-process executor is scanned for these: reaching
# the simulator, the law registry or the interpreter's module table would leak the ground truth.
CODE_TOKENS = ('modules', 'laws', 'LAW_REGISTRY', 'HIDDEN', 'ground_truth', 'importlib', 'inspect', 'sys.modules',
               '__import__', 'builtins', 'gc.', 'pathlib', 'glob', 'NewtonBench', 'getattr(', '__dict__',
               '__globals__', '__subclasses__', 'run_experiment')


def params_of(signature):
    return [p.strip() for p in signature[signature.index('(') + 1:signature.rindex(')')].split(',') if p.strip()]


class Simulator:
    """NewtonBench's simulator behind a query budget; every query is logged and hash-chained."""

    def __init__(self, module, budget):
        self.module, self.budget = module, budget
        self.log, self.head = [], '0' * 64

    def __call__(self, **kwargs):
        index = len(self.log) + 1
        inputs = {k: v for k, v in kwargs.items() if k not in ('noise_level', 'difficulty', 'system', 'law_version')}
        if index > self.budget:
            value, status = float('nan'), 'OVER_BUDGET'
        else:
            value, status = self.module.run_experiment_for_module(**kwargs), 'MEASURED'
        entry = {'index': index, 'inputs': inputs, 'value': repr(value), 'status': status, 'prev': self.head}
        self.head = entry['sha256'] = common.sha256_json(entry)
        self.log.append(entry)
        return value

    def summary(self):
        return {'queries': sum(e['status'] == 'MEASURED' for e in self.log),
                'over_budget_requests': sum(e['status'] == 'OVER_BUDGET' for e in self.log),
                'query_chain_head': self.head}


def evaluate(module, task, law, trial_info):
    """NewtonBench's module evaluator, unchanged, on the task's seeded test sample."""
    import numpy as np
    seed = common.eval_seed(task)
    np.random.seed(seed)
    random.seed(seed)
    started = time.monotonic()
    result = module.evaluate_law(law, param_description=module.PARAM_DESCRIPTION, difficulty=task['difficulty'],
                                 law_version=task['version'], judge_model_name='gpt41', trial_info=trial_info)
    result = {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in result.items()}
    return {**result, 'eval_seed': seed, 'eval_seconds': round(time.monotonic() - started, 1)}


def _code_blocks(history):
    blocks = []
    for message in history:
        if message.get('role') == 'assistant':
            blocks += re.findall(r'<python>(.*?)</python>', message.get('content') or '', flags=re.S)
    return blocks


def run_baseline(arm, task, out, chat, nb):
    module = common.nb_module(task['module'])
    attempts, result, sim = [], None, None
    for attempt in range(MAX_RETRIES + 1):
        sim = Simulator(module, common.QUERY_BUDGET)
        proxy = types.SimpleNamespace(**vars(module))
        proxy.run_experiment_for_module = sim
        proxy.get_task_prompt = lambda system, _m=module, **kw: (_m.get_task_prompt(system, **kw)
                                                                 + BUDGET_NOTE.format(n=common.QUERY_BUDGET))
        trial_info = {'trial_id': f'{arm}-{attempt}', 'trial_dir': str(out)}
        try:
            if arm == 'vanilla':
                result = nb['vanilla'].conduct_exploration(
                    module=proxy, model_name=common.MODEL, noise_level=task['noise'], difficulty=task['difficulty'],
                    system=task['system'], law_version=task['version'], trial_info=trial_info)
            else:
                result = nb['code_assisted'].conduct_code_assisted_exploration(
                    module=proxy, model_name=common.MODEL, noise_level=task['noise'], difficulty=task['difficulty'],
                    system=task['system'], law_version=task['version'], trial_info=trial_info)
            attempts.append({'attempt': attempt + 1, 'status': 'COMPLETED', **sim.summary()})
            break
        except Exception as exc:  # NewtonBench's runner retries any exception the same way.
            attempts.append({'attempt': attempt + 1, 'status': 'EXCEPTION', 'error': f'{type(exc).__name__}: {exc}'[:500],
                             'traceback': traceback.format_exc()[-2000:], **sim.summary(), 'query_log': sim.log})
            result = None
    history = (result or {}).get('chat_history', [])
    (out / 'chat_history.json').write_text(json.dumps(history, indent=1), encoding='utf-8')
    (out / 'queries.json').write_text(json.dumps(sim.log, indent=1), encoding='utf-8')
    blocks = _code_blocks(history)
    code_hits = sorted({t for t in CODE_TOKENS for b in blocks if t in b})
    if result is None:
        law = f"{module.FUNCTION_SIGNATURE} return float('nan')"
        record = {'status': 'FAILED', 'submitted_law': law}
    else:
        law = result['submitted_law']
        record = {'status': result['status'], 'submitted_law': law, 'rounds': result.get('rounds'),
                  'nb_num_experiments': result.get('num_experiments'),
                  'python_tags_used_total': result.get('python_tags_used_total')}
    agent_calls = list(chat.calls)
    evaluation = evaluate(module, task, law, {'trial_id': arm, 'trial_dir': str(out)})
    contaminated = bool(code_hits) or any(c['contaminated'] for c in agent_calls)
    # The chat backend has no tools; any tool event would break the pure-chat protocol and is reported.
    tool_events = sum(c['commands'] + c['other_tool_calls'] for c in chat.calls)
    return {**record, 'attempts': attempts, **sim.summary(), 'code_blocks': len(blocks), 'code_audit_hits': code_hits,
            'chat_tool_events': tool_events,
            'transcript_sha256': common.sha256_json(history), 'agent_calls': _calls(agent_calls),
            'judge_calls': _calls(chat.calls[len(agent_calls):]), 'contaminated': contaminated,
            'submissions': {arm: {'law': law, 'certified': None, 'evaluation': evaluation}}}


def _calls(calls):
    keys = ('index', 'role', 'attempt', 'exit_code', 'wall_seconds', 'usage', 'contaminated', 'hits', 'commands',
            'other_tool_calls', 'events_sha256', 'prompt_sha256', 'last_sha256')
    return [{k: c.get(k) for k in keys} for c in calls]


# --- Compass arm --------------------------------------------------------------------------------------------

def _py(node):
    """Compass AST -> Python expression (float arithmetic; constants stay exact ratios until evaluated)."""
    if 'const' in node:
        value = Fraction(node['const'])
        return f'({value.numerator}/{value.denominator})' if value.denominator != 1 else f'({value.numerator})'
    if 'var' in node:
        return node['var']
    op, args = node['op'], [_py(a) for a in node['args']]
    infix = {'add': '+', 'sub': '-', 'mul': '*', 'div': '/', 'mod': '%', 'lt': '<', 'le': '<=', 'eq': '=='}
    if op in infix:
        return f'({args[0]} {infix[op]} {args[1]})'
    if op == 'pow':
        return f'({args[0]} ** {args[1]})'
    if op == 'neg':
        return f'(-{args[0]})'
    if op in ('sqrt', 'floor', 'ceil'):
        return f'math.{op}({args[0]})'
    if op in ('abs', 'min', 'max'):
        return f'{op}({", ".join(args)})'
    if op == 'ite':
        return f'({args[1]} if {args[0]} != 0 else {args[2]})'
    raise ValueError('Untranslatable operator: ' + op)


def rulebook_to_python(hyp, signature, names):
    used = set()

    def walk(node):
        if 'var' in node:
            used.add(node['var'])
        for arg in node.get('args', ()):
            walk(arg)
    walk(hyp['predict']['y'])
    if hyp.get('applies'):
        walk(hyp['applies'])
    if used - set(names):
        return None  # Reads a derived tool feature; no closed Python form is submitted.
    body = _py(hyp['predict']['y'])
    if hyp.get('applies'):
        body = f"{body} if ({_py(hyp['applies'])}) != 0 else float('nan')"
    return f'{signature}\n    import math\n    return {body}'


def declare(task, root, names, background, logs):
    import rds_compass_store as cstore
    from rds_compass_declaration import initialize
    kinds, points = common.design(task['module'], names)
    root.mkdir(parents=True)
    shutil.copyfile(HERE / 'nb_experiment.py', root / 'experiment.py')
    import site
    (root / 'task.json').write_text(json.dumps({'newtonbench': str(common.NEWTONBENCH), 'module': task['module'],
                                                'site_packages': site.getsitepackages(),
                                                'difficulty': task['difficulty'], 'version': task['version'],
                                                'noise': task['noise'], 'inputs': names}), encoding='utf-8')
    (logs / 'background.txt').write_text(background, encoding='utf-8')
    argv = ['{python}', '-B', 'experiment.py', '--id', '{id}']
    for name in names:
        argv += ['--' + name, '{' + name + '}']
    argv += ['--output', '{output}']
    declaration = {
        'schema': 'rds-compass-declaration-v1',
        'goal': {'kind': 'IDENTIFY_LAW', 'observable': 'y',
                 'statement': 'Identify the law that maps the controlled inputs (' + ', '.join(names) +
                              ') to the measured value y of the simulated apparatus.'},
        'inputs': {n: {'min': common.frac_text(min(p[n] for p in points)),
                       'max': common.frac_text(max(p[n] for p in points))} for n in names},
        'observables': {'y': {'tolerance': {'rel': common.REL_TOLERANCE}}},
        'experiment': {'argv': argv, 'files': {'code': ['experiment.py'], 'data': ['task.json'],
                                               'evaluator': ['experiment.py']},
                       'timeout_seconds': 60, 'wall_seconds': 90},
        'candidates': [{'id': f'c{i:02d}', 'inputs': {n: common.frac_text(p[n]) for n in names}}
                       for i, p in enumerate(points)],
        'escape': {'probe_points': 6, 'max_jumps': 8},
        'agent': {'argv': ['{python}', '-B', str(HERE / 'nb_jump.py'), '--log-dir', str(logs),
                           '--background', str(logs / 'background.txt')], 'timeout_seconds': 600},
        'budget': {'wall_seconds': COMPASS_WALL, 'experiments': common.QUERY_BUDGET}}
    (root / 'compass.json').write_text(json.dumps(declaration, indent=1), encoding='utf-8')
    initialize(root, root / 'compass.json')
    return cstore.store_for(root), kinds


FALLBACK = '''{background}

**Benchmark harness note:** You cannot run experiments in this session. `data.json` in your working directory
holds every measurement available to you: a list of rows {{"inputs": {{...}}, "value": ...}} from the noise-free
apparatus described above. You may run Python here to analyze them. Then answer with the <final_law> block
exactly as the submission requirements above describe.
'''


def compass_fallback(module, rows, background, logs):
    data = [{'inputs': {k: float(Fraction(v)) for k, v in r['inputs'].items()}, 'value': float(Fraction(r['y']))}
            for r in rows]
    message, record = common.run_codex(FALLBACK.format(background=background), logs, chat=False,
                                       files={'data.json': json.dumps(data, indent=1)})
    law = None
    if message is not None and not record['contaminated']:
        from utils.vanilla_agent import _extract_final_law
        ok, law = _extract_final_law(message, module.FUNCTION_SIGNATURE)
        law = law if ok else None
    return law, record


def run_compass(task, out, chat, nb):
    import rds_compass as compass
    import rds_compass_store as cstore
    from rds_compass_drive import drive
    module = common.nb_module(task['module'])
    names = params_of(module.FUNCTION_SIGNATURE)
    background = (module.get_task_prompt('vanilla_equation', noise_level=task['noise'])
                  + '\n\n(For this campaign the experiment protocol and submission format above do not apply: the '
                    'campaign buys measurements, and you answer only in the JSON form requested below.)')
    attempts, result, store = [], None, None
    for attempt in range(MAX_RETRIES + 1):
        root, logs = out / f'project-a{attempt + 1}', out / f'jumps-a{attempt + 1}'
        logs.mkdir(parents=True)
        started = time.monotonic()
        try:
            store, kinds = declare(task, root, names, background, logs)
            result = drive(root, max_steps=MAX_STEPS, wall_seconds=COMPASS_WALL, publish=False)
            attempts.append({'attempt': attempt + 1, 'status': result['status'],
                             'wall_seconds': round(time.monotonic() - started, 1)})
            break
        except Exception as exc:
            attempts.append({'attempt': attempt + 1, 'status': 'EXCEPTION', 'error': f'{type(exc).__name__}: {exc}'[:500],
                             'traceback': traceback.format_exc()[-3000:],
                             'wall_seconds': round(time.monotonic() - started, 1)})
            result = None
    jump_audits = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(logs.glob('*/audit.json'))]
    contaminated = any(a['contaminated'] for a in jump_audits)
    if result is None:
        law = f"{module.FUNCTION_SIGNATURE} return float('nan')"
        record = {'status': 'FAILED', 'compass_status': 'EXCEPTION'}
        state, chosen, rows, receipts = None, None, [], []
    else:
        state = compass.fold(store)
        status = result['status']
        chosen = None
        if status == 'GOAL_IDENTIFIED':
            ident = result['verification']['hypothesis']['id']
            chosen = next(h for h in state['hypotheses'].values() if h['id'] == ident)
        elif compass.live(state):
            chosen = compass.live(state)[0]
        rows = [{'id': r['id'], 'inputs': {k: common.frac_text(v) for k, v in r['inputs'].items()},
                 'y': common.frac_text(r['outputs']['y'])} for r in compass.measured(state) if 'y' in r['outputs']]
        with store._db(True) as db:
            receipts = [{'run_id': r['run_id'], 'sha256': r['sha256']}
                        for r in db.execute('SELECT run_id, sha256 FROM receipts ORDER BY run_id')]
        law = rulebook_to_python(chosen, module.FUNCTION_SIGNATURE, names) if chosen else None
        record = {'status': 'COMPLETED', 'compass_status': status,
                  'chosen': {'id': chosen['id'], 'statement': chosen['statement'], 'origin': chosen.get('origin'),
                             'status': chosen.get('status'), 'sha256': chosen['sha256']} if chosen else None,
                  'verification': {k: (result.get('verification') or {}).get(k) for k in
                                   ('claim', 'uniqueness', 'distinct_live_classes', 'novel_confirmations',
                                    'receipts_rechecked', 'equivalence_class')},
                  'polynomial': ((result.get('verification') or {}).get('polynomial') or {}).get('status'),
                  'hypotheses': len(state['hypotheses']), 'live': len(compass.live(state)),
                  'refuted': sum(h['status'] == 'REFUTED' for h in state['hypotheses'].values()),
                  'jumps': [{'status': j['status'], 'trigger': (j.get('trigger') or {}).get('trigger'),
                             'observations': j.get('observations')} for j in state['jumps']],
                  'dispositions': [{'jump': d['jump'], 'disposer': d['disposer'], 'errors': d['errors'],
                                    'admitted': [o['id'] for o in d['outcomes'] if o.get('admitted')],
                                    'rejected': [(o['id'], o.get('reason')) for o in d['outcomes'] if not o.get('admitted')]}
                                   for d in state['dispositions'].values()],
                  'steps': len(result.get('steps') or []),
                  'actions': _count([s['action'] for s in result.get('steps') or []]),
                  'ledger_head': state['head'], 'report_sha256': result.get('report_sha256'),
                  'non_finite_measurements': sum(1 for o in state['observations'].values()
                                                 if o['status'] == 'MEASURED' and 'y' not in o['outputs'])}
        if chosen is not None and law is None:
            record['untranslatable'] = True
    certified = record.get('compass_status') == 'GOAL_IDENTIFIED' and law is not None
    strict_law = law or f"{module.FUNCTION_SIGNATURE} return float('nan')"
    evaluation = evaluate(module, task, strict_law, {'trial_id': 'compass', 'trial_dir': str(out)})
    submissions = {'compass': {'law': strict_law, 'certified': certified, 'evaluation': evaluation}}
    fallback = None
    if state is not None and chosen is None and not contaminated:
        fb_law, fallback = compass_fallback(module, rows, module.get_task_prompt('vanilla_equation',
                                                                                  noise_level=task['noise']),
                                            out / 'fallback')
        contaminated_fb = fallback['contaminated']
        fb_law = fb_law or f"{module.FUNCTION_SIGNATURE} return float('nan')"
        submissions['compass_fallback'] = {'law': fb_law, 'certified': False, 'fallback_used': True,
                                           'contaminated': contaminated_fb,
                                           'evaluation': evaluate(module, task, fb_law,
                                                                  {'trial_id': 'compass_fallback', 'trial_dir': str(out)})}
    else:
        submissions['compass_fallback'] = {**submissions['compass'], 'certified': False, 'fallback_used': False}
    return {**record, 'attempts': attempts, 'queries': len(receipts), 'receipts': receipts,
            'measurements': rows, 'jump_calls': [{k: a.get(k) for k in ('exit_code', 'wall_seconds', 'usage',
                                                                        'contaminated', 'hits', 'commands',
                                                                        'events_sha256', 'packet_sha256', 'request',
                                                                        'items', 'evidence_rows')}
                                                 for a in jump_audits],
            'fallback_call': {k: fallback.get(k) for k in ('exit_code', 'wall_seconds', 'usage', 'contaminated', 'hits',
                                                          'commands', 'events_sha256')} if fallback else None,
            'judge_calls': _calls(chat.calls), 'contaminated': contaminated, 'submissions': submissions}


def _count(items):
    out = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--arm', required=True, choices=['vanilla', 'code_assisted', 'compass'])
    parser.add_argument('--module', required=True)
    parser.add_argument('--difficulty', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--trial', type=int, required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    task = {'module': args.module, 'difficulty': args.difficulty, 'version': args.version,
            'system': 'vanilla_equation', 'noise': 0.0}
    chat = common.ChatBackend(out / 'codex')
    nb = common.load_newtonbench(chat)
    started = time.monotonic()
    if args.arm == 'compass':
        body = run_compass(task, out, chat, nb)
    else:
        body = run_baseline(args.arm, task, out, chat, nb)
    row = {'schema': 'rds-newtonbench-trial-v1', 'arm': args.arm, 'trial': args.trial, 'task': task,
           'task_id': common.task_id(args.module, args.difficulty, args.version),
           'model': {'cli': 'codex', 'model': common.MODEL, 'reasoning_effort': common.EFFORT,
                     'service_tier': common.TIER},
           'query_budget': common.QUERY_BUDGET, 'wall_seconds': round(time.monotonic() - started, 1), **body,
           'scientific_support': 'UNKNOWN'}
    row['row_sha256'] = common.sha256_json(row)
    (out / 'trial.json').write_text(json.dumps(row, indent=1, default=str), encoding='utf-8')
    print(json.dumps({k: row.get(k) for k in ('arm', 'task_id', 'trial', 'status', 'compass_status', 'queries',
                                              'contaminated', 'wall_seconds')}))
    return 0


if __name__ == '__main__':
    sys.exit(main())

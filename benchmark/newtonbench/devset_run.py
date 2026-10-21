"""Feynman devset Compass sweep: every registered equation x budgets B in {8, 16, 32}.

Each campaign declares the devset rows as candidates (exact rational text), an
experiment that evaluates the equation's law in a subprocess, and an empty
hypothesis list. Scored per the pre-registered rule:

    claim_exact = drive status == GOAL_IDENTIFIED and the reported law replays
                  with 0 counterexamples over ALL devset rows under the
    devset tolerances (model.replay). A PARTIAL_FIT / best-effort law is
    never claim_exact.

    experiments = drive steps with action in {EXPERIMENT, PROBE, CONFIRM,
                  HOLDOUT}; a declaration failure is recorded and the sweep
                  moves on.

    python -B benchmark/newtonbench/devset_run.py [out.json] [equation]
"""
import json
import random
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(HERE)]
import devset
import rds_compass_model as model
from rds_compass_declaration import initialize
from rds_compass_drive import drive

BUDGETS = (8, 16, 32)
EXPERIMENTS = ('EXPERIMENT', 'PROBE', 'CONFIRM', 'HOLDOUT')

# --- False certification: does a claim survive points it was never fitted on? --
#
# claimed              = status is GOAL_IDENTIFIED (as in claim_exact, devset_run.py:114)
# false_certification  = claimed and the reported law differs from the truth law
#                        beyond tolerance on INDEPENDENT uniform samples
#                        (an equation with no truth law left is UNKNOWN, never False)
# One-off replays never stored which rows a campaign measured (devset_run.py:118-140),
# so the only window that cannot be empty is the whole declared grid: a sample is
# used iff it is not one of spec['rows']. That is stronger than excluding fitted
# points alone and needs no campaign root.
CHECKS = 32
DEFAULT_SEED = 357
PI = (Fraction('3.141592653589793238462643383279502884197'),
      Fraction('3.141592653589793238462643383279502884198'))
MAX_ABS_ERROR = Fraction(10 ** 6)
BOOLEAN_OPS = frozenset(('lt', 'le', 'eq', 'gt', 'ge', 'ne', 'ite'))


def truth_ast(law, variables):
    """Read a devset `law` into the model's exact AST through the model's own reader.

    `low if cond else high` becomes `ite(cond, low, high)`. `!=` and `not in` are the
    only Python forms that reader rejects; both are rewritten to `not(<cond>)` first.
    """
    text = law.strip()
    if ' not in ' in text:
        head, rest = text.split(' not in ', 1)
        values, elses = rest.split(' else ', 1)
        text = f'{head} if not ({head} in {values}) else {elses}'
    if '!=' in text:
        head, elses = text.split(' else ', 1)
        left, cond = head.split(' if ', 1)
        text = f'{left} if not ({cond.replace("!=", "==")}) else {elses}'
    return model.parse(text, set(variables))


EXPERIMENT = '''import argparse, json, math, pathlib
from fractions import Fraction
p = argparse.ArgumentParser()
p.add_argument('--output', required=True)
task = json.loads(pathlib.Path('task.json').read_text(encoding='utf-8'))
for name in task['variables']:
    p.add_argument('--' + name, required=True)
a = p.parse_args()
env = {name: Fraction(getattr(a, name)) for name in task['variables']}
env.update(exp=math.exp, sqrt=math.sqrt, log=math.log, sin=math.sin, cos=math.cos, pi=math.pi)
value = eval(task['law'], {'__builtins__': {}}, env)
text = str(value) if isinstance(value, Fraction) else repr(float(value))
out = pathlib.Path(a.output)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({'outputs': {'y': text}}), encoding='utf-8')
'''


def campaign(name, budget, base, sources=()):
    """Write one campaign root; return (root, spec)."""
    spec = devset.load(name)
    variables = spec['grammar']['variables']
    rows = spec['rows']
    root = base / name / ('B' + str(budget))
    root.mkdir(parents=True)
    (root / 'experiment.py').write_text(EXPERIMENT, encoding='utf-8')
    (root / 'task.json').write_text(json.dumps({'law': spec['truth']['law'],
                                                'variables': variables}), encoding='utf-8')
    inputs = {}
    for var in variables:
        values = [model.exact(row['inputs'][var]) for row in rows]
        inputs[var] = {'min': model.text(min(values)), 'max': model.text(max(values)),
                       'integer': all(v.denominator == 1 for v in values)}
    argv = ['{python}', '-B', 'experiment.py', '--output', '{output}']
    for var in variables:
        # Glue the value onto the flag: a negative rational such as -9/4 passed
        # as a separate token is read by argparse as an option, not a value.
        argv += ['--' + var + '={' + var + '}']
    # One budget unit is one attempt, and a campaign declares two slots per candidate (the run and
    # its single retry). A row set smaller than B/2 cannot fill the declared budget, so cap it.
    experiments = min(budget, 2 * len(rows))
    declaration = {
        'schema': 'rds-compass-declaration-v1',
        'goal': {'kind': 'IDENTIFY_LAW',
                 'statement': f'Identify the law behind {name} over the declared candidate points',
                 'observable': spec['truth']['observable']},
        'inputs': inputs,
        'observables': {spec['truth']['observable']:
                        {'tolerance': spec['tolerances'][spec['truth']['observable']]}},
        'experiment': {'argv': argv,
                       'files': {'code': ['experiment.py'], 'data': ['task.json'],
                                 'evaluator': ['experiment.py']},
                       'timeout_seconds': 20, 'wall_seconds': 25},
        'candidates': [{'id': row['id'], 'inputs': dict(row['inputs'])} for row in rows],
        'hypotheses': [],
        'sources': [dict(source) for source in sources],
        'grammar': spec['grammar'],
        'budget': {'wall_seconds': 900, 'experiments': experiments, 'max_experiments': experiments},
    }
    (root / 'compass.json').write_text(json.dumps(declaration, indent=1), encoding='utf-8')
    return root, spec


def counterexamples(expr, spec):
    """Replay the reported law over all devset rows; count counterexamples."""
    observable = spec['truth']['observable']
    hyp = model.compile_hypothesis({'id': 'h', 'predict': {observable: expr}},
                                   spec['grammar']['variables'], [observable], goal=observable)
    observations = [{'id': row['id'],
                     'inputs': {k: model.exact(v) for k, v in row['inputs'].items()},
                     'outputs': {k: model.exact(v) for k, v in row['outputs'].items()}}
                    for row in spec['rows']]
    return len(model.replay(hyp, observations,
                            {observable: model.tolerance(spec['tolerances'][observable])})['counterexamples'])


def run_one(name, budget, base, sources=()):
    root, spec = campaign(name, budget, base, sources)
    initialize(root, root / 'compass.json')
    result = drive(root, publish=False)
    steps = result.get('steps', [])
    verification = result.get('verification') or {}
    expr = (verification.get('predict') or {}).get(spec['truth']['observable'])
    bad = counterexamples(expr, spec) if expr else None
    return certify_run({'equation': name, 'B': budget, 'status': result['status'],
                        'claim': verification.get('claim'), 'law': expr, 'counterexamples': bad,
                        'claim_exact': result['status'] == 'GOAL_IDENTIFIED' and bad == 0,
                        'experiments': sum(1 for s in steps if s.get('action') in EXPERIMENTS)},
                       spec)


def sample_points(spec, count=CHECKS, seed=DEFAULT_SEED):
    """`count` uniform points in the declared box, none of them a declared devset row."""
    variables = spec['grammar']['variables']
    seen = {tuple(model.exact(row['inputs'][v]) for v in variables) for row in spec['rows']}
    span = {}
    for var in variables:
        values = [model.exact(row['inputs'][var]) for row in spec['rows']]
        low, high = min(values), max(values)
        if low == high:
            return ({'variable': var, 'reason': 'ZERO_WIDTH_DOMAIN',
                     'detail': None}, [], 0)
        span[var] = (low, high)
    rng = random.Random(seed)
    # Attempts are a deterministic function of the domain, then of the seed: the
    # reported end state never depends on the Python version's RNG implementation.
    attempts = min(10 ** 6, 4000 * count)
    points, clashes = [], 0
    for _ in range(attempts):
        point = {var: low + (high - low) * Fraction(rng.random()).limit_denominator(10 ** 9)
                 for var, (low, high) in span.items()}
        if tuple(point[v] for v in variables) in seen:
            clashes += 1
            continue
        points.append(point)
        if len(points) == count:
            return None, points, clashes
    return ({'variable': None, 'reason': 'TOO_FEW_INDEPENDENT_POINTS',
             'detail': f'{len(points)} of {count} points clear the {len(seen)} declared rows '
                       f'after {attempts} draws ({clashes} clashes)'}, [], clashes)


def _evaluate(ast, variables):
    """(prediction, failure) on one point; a failure is a fixed token, not a value."""
    try:
        hyp = model.compile_hypothesis({'id': 'p', 'predict': {'y': ast}}, variables, ['y'], goal='y')
    except (ValueError, TypeError) as exc:
        return None, 'UNEVALUABLE_EXPRESSION: ' + str(exc)[:120]
    if hyp['applies'] is not None or any(rule.get('op') in BOOLEAN_OPS for rule in hyp['predict'].values()):
        return None, 'COMPARISON_EXPRESSION: not a numeric law'
    return hyp, None


def certification(expr, spec, tolerance=None, count=CHECKS, seed=DEFAULT_SEED):
    """Compare one reported law with the truth law on independent samples.

    Returns {'false_certification', 'certification_check'}; False cert is None (UNKNOWN)
    rather than nothing, so a rate over it can never read as zero by default.
    """
    observable = spec['truth']['observable']
    variables = spec['grammar']['variables']
    absolute, relative = model.tolerance(tolerance) if tolerance is not None else (0, 0)
    check = {'points_checked': 0, 'max_abs_error': None, 'tolerance': model.text(absolute),
             'tolerance_rel': model.text(relative), 'seed': seed, 'sampling': 'uniform-in-box',
             'independence': 'excludes every declared devset row', 'reason': ''}
    problem, points, clashes = sample_points(spec, count, seed)
    if problem:
        check['reason'] = problem['reason'] + (': ' + problem['detail'] if problem['detail'] else '')
        return {'false_certification': None, 'certification_check': check}
    claimed, err = _evaluate(expr, variables)
    truth, terr = _evaluate(truth_ast(spec['truth']['law'], variables), variables)
    if err or terr:
        check['reason'] = (err or '') + (terr or '')
        return {'false_certification': None, 'certification_check': check}
    failed, worst, worst_point, first = 0, Fraction(0), None, None
    for point in points:
        got, _ = model.predict(claimed, point)
        want, _ = model.predict(truth, point)
        if got.get(observable) is None or want.get(observable) is None:
            failed += 1  # singular or out-of-domain on this point: not a disagreement
            continue
        error = abs(got[observable] - want[observable])
        if error > worst or worst_point is None:
            worst, worst_point = error, point
        if not model.matches(got[observable], want[observable], (absolute, relative)):
            first = first or point
    check['points_checked'] = len(points)
    check['evaluation_failures'] = failed
    check['row_clashes_redrawn'] = clashes
    check['worst_point'] = {k: model.text(v) for k, v in worst_point.items()} if worst_point else None
    # An exact residual can be a fraction with 30-digit parts; past MAX_ABS_ERROR only the
    # bound is reported, never a truncated number that reads like a measurement.
    check['max_abs_error'] = model.text(worst) if worst <= MAX_ABS_ERROR else None
    if check['max_abs_error'] is None:
        check['max_abs_error_note'] = f'> {MAX_ABS_ERROR}, exact value not stored'
    if first:
        check['reason'] = ('DIFFERS_BEYOND_TOLERANCE on ' +
                           ', '.join(f'{k}={model.text(v)}' for k, v in first.items()))
        return {'false_certification': True, 'certification_check': check}
    measured = len(points) - failed
    if not measured:
        check['reason'] = 'NO_EVALUABLE_POINT: every sample was singular or out of domain'
        return {'false_certification': None, 'certification_check': check}
    check['reason'] = f'EQUIVALENT on {measured} sampled points'
    if failed:
        check['reason'] += f' ({failed} sample(s) unevaluable)'
    return {'false_certification': False, 'certification_check': check}


def certify_run(run, spec=None):
    """Fill claimed / false_certification / certification_check into one run row."""
    try:
        spec = devset.load(run['equation']) if spec is None else spec
    except (KeyError, TypeError) as exc:
        # An equation with no loader is the one case where a claim cannot be checked:
        # claimed still follows its stored status, the verdict stays UNKNOWN.
        run['claimed'] = run.get('status') == 'GOAL_IDENTIFIED'
        run['false_certification'] = None
        run['certification_check'] = {'points_checked': 0, 'max_abs_error': None, 'tolerance': None,
                                      'reason': 'NO_TRUTH_LAW: ' + str(exc)[:120]}
        return run
    observable = spec['truth']['observable']
    expr = run.get('law')
    verdict = None
    if run.get('status') == 'GOAL_IDENTIFIED' and expr:
        verdict = certification(expr, spec,
                                tolerance=(spec.get('tolerances') or {}).get(observable))
    elif run.get('status') == 'GOAL_IDENTIFIED':
        verdict = {'false_certification': None,
                   'certification_check': {'points_checked': 0, 'max_abs_error': None,
                                           'tolerance': None,
                                           'reason': 'NO_LAW_EXPRESSION: claimed without a law'}}
    run['claimed'] = bool(run.get('status') == 'GOAL_IDENTIFIED')
    if verdict:
        run['false_certification'] = verdict['false_certification']
        run['certification_check'] = verdict['certification_check']
    else:
        run['false_certification'] = False
        run['certification_check'] = {'points_checked': 0, 'max_abs_error': None, 'tolerance': None,
                                      'reason': 'NOT_CLAIMED'}
    return run


#: Fields that cannot repeat between two runs of the same sweep, by construction.
#: A wall-clock reading and a mkdtemp() root differ every time; an acceptance
#: check that compares them byte for byte fails a correct seam every time.
UNSTABLE_FIELDS = ('seconds', 'campaign_root', 'elapsed_seconds',
                   'seconds_total', 'seconds_per_correct')


def comparable(document):
    """Strip the unrepeatable fields so two sweeps can be compared directly.

    Everything else, including every per-run verdict, must still match exactly.
    A row that crashed carries no `status`, so it survives here as itself.
    """
    kept = {k: v for k, v in document.items() if k not in UNSTABLE_FIELDS}
    kept['runs'] = [{k: v for k, v in run.items() if k not in UNSTABLE_FIELDS}
                    for run in document.get('runs', [])]
    return kept


def cost_per_correct(runs):
    """What one correct solution actually cost, in the two units the sweep records.

    `claim_exact` is the only correctness signal a sweep has, and it is assigned
    outside the campaign, so this ratio belongs in the result file rather than in
    the ledger. Both ratios stay null when nothing was solved: a cheap failure is
    not a cheap success, and dividing by a zero correct count would say it was.

    A run that `backfill` marked as a false certification is excluded: its law
    matched the declaration's own points but not the independent ones. Runs from
    before that field existed carry `None` and stay counted, so an old file
    keeps the ratio it shipped with.
    """
    solved = [r for r in runs if r.get('claim_exact') and not r.get('false_certification')]
    experiments = sum(r.get('experiments') or 0 for r in runs)
    seconds = sum(r.get('seconds') or 0 for r in runs)
    return {'correct_count': len(solved),
            'experiments_total': experiments,
            'seconds_total': round(seconds, 1),
            'experiments_per_correct': round(experiments / len(solved), 3) if solved else None,
            'seconds_per_correct': round(seconds / len(solved), 1) if solved else None,
            'cost_per_correct_rule': 'totals over the whole sweep divided by the number of '
                                     'runs whose law reproduced truth with 0 counterexamples and '
                                     'survived the independent certification points'}


def summarize(runs):
    """Top-level rate; null + NO_CLAIMS when nothing was claimed (never 0.0).

    A crashed cell and an honest null are different things. `main` keeps going
    when one cell raises, so a sweep that lost most of its cells still exits 0
    with a verdict-shaped file. Reporting those rows as NO_CLAIMS would read
    like "no false certifications were found"; they are reported as degraded.
    """
    degraded = [r for r in runs if 'error' in r and 'status' not in r]
    claimed = sum(1 for r in runs if r.get('claimed'))
    known = [r for r in runs if r.get('claimed') and r.get('false_certification') is not None]
    false_cert = sum(1 for r in known if r['false_certification'])
    return {'false_certification_rate': false_cert / len(known) if known else None,
            'false_certification_count': false_cert,
            'claimed_count': claimed,
            'checkable_claimed_count': len(known),
            'degraded_count': len(degraded),
            'false_certification_rate_rule': 'false_certification_count / checkable_claimed_count',
            'false_certification_status': 'NO_RUNS' if not runs else
            'DEGRADED' if len(degraded) * 2 > len(runs) else
            'NO_CLAIMS' if not known else
            'ZERO_FALSE_CERTIFICATIONS' if not false_cert else 'FALSE_CERTIFICATIONS_FOUND',
            **cost_per_correct(runs)}


def backfill(document):
    """Re-derive claimed / false_certification for a stored runs document.

    The stored `law` and the equation's truth law are both still available, so an
    old file can be re-certified without re-running the sweep. A run that crashed
    before recording a status is left UNKNOWN, never counted as checkable.
    """
    runs = []
    for run in document.get('runs', []):
        if 'status' not in run:
            runs.append({**run, 'claimed': False, 'false_certification': None,
                         'certification_check': {'points_checked': 0, 'max_abs_error': None,
                                                 'tolerance': None,
                                                 'reason': 'RUN_ERROR: ' + str(run.get('error'))[:120]}})
            continue
        runs.append(certify_run(dict(run)))
    return {**document, 'runs': runs, **summarize(runs)}


def main(out_path, only=None):
    base = Path(tempfile.mkdtemp(prefix='devset-run-'))
    runs = []
    start = time.monotonic()
    for name in devset.list_equations():
        if only and name != only:
            continue
        for budget in BUDGETS:
            t0 = time.monotonic()
            try:
                row = run_one(name, budget, base)
            except Exception as exc:  # one failed declaration must not sink the sweep
                row = {'equation': name, 'B': budget, 'claim_exact': False,
                       'error': f'{type(exc).__name__}: {exc}'}
            row['seconds'] = round(time.monotonic() - t0, 1)
            runs.append(row)
            print(json.dumps(row), flush=True)
    out = {'schema': 1, 'budgets': list(BUDGETS), 'campaign_root': str(base),
           'claim_exact_rule': 'status == GOAL_IDENTIFIED and 0 counterexamples on full devset replay',
           'false_certification_rule': (
               'status == GOAL_IDENTIFIED and the law differs from truth on 32 independent '
               'uniform samples in the declared box, beyond the devset tolerance; '
               'null (UNKNOWN) when no checkable claim exists'),
           'runs': runs, **summarize(runs),
           'elapsed_seconds': round(time.monotonic() - start, 1)}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(out, indent=1) + '\n', encoding='utf-8')
    print('wrote ' + str(out_path), flush=True)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else HERE / 'results' / 'devset-run.json',
         sys.argv[2] if len(sys.argv) > 2 else None)

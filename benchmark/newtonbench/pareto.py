"""Cost/correctness Pareto probe: RDS with an experiment budget vs a fixed six-point baseline.

Three worlds that need more evidence than the six initial points. The RDS arm
runs with a hard experiment ceiling; the baseline arm only sees the same six
points and fits a straight line. Each run records experiments used and exact
match on the 32-point truth. Every arm also records whether its claim holds
beyond the points it fitted, on independent samples in the same box.

    python -B benchmark/newtonbench/pareto.py results/pareto.json
"""
import json
import random
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests'), str(Path(__file__).resolve().parent)]
import compass_fixture as fx
from rds_compass_declaration import initialize
import rds_compass as compass
import rds_compass_store as cstore
import rds_compass_model as model
from rds_compass_drive import drive
from sparse_suite import score

WORLDS = [
    ('sparse-tail', 'x+1 if x != 31 else 1000'),
    ('sparse-mid', 'x+1 if x != 20 else 1000'),
    ('regime-double', 'x+1 if x < 10 else 2*x'),
]
BUDGETS = [6, 8, 12, 16, 24, 32]

# --- False certification on the 32-point toy worlds -------------------------
#
# claimed  = the arm states a law AND asserts it holds (RDS: status is
#            GOAL_IDENTIFIED and the claim covers all receipted measurements;
#            the baseline: fitting a line IS the assertion).
# false_certification = claimed and that law differs from the world's truth
#            (the same `law` string sparse_suite.score reads) beyond tolerance
#            on CHECKS independent samples.
# The two arms only ever see the six initial points or a uniformly spread grid,
# never range(32), so "not x0..x5" is exactly the set of points neither arm saw.
CHECKS = 32
DEFAULT_SEED = 357
MAX_ABS_ERROR = Fraction(10 ** 6)
BASELINE_ARM = 'baseline-uniform'
FALSE_CERTIFICATION_RULE = (
    'an arm that states a law differs from the world truth on 32 independent samples in '
    'the declared box, beyond the zero tolerance; null (UNKNOWN) when no checkable claim '
    'exists, never 0.0')


def _law(expr, temperature='T'):
    """Read a toy-world expression (Python arithmetic) into the model's exact AST.

    `x != k` does not survive the model's reader, but a point exception is exactly
    the spike `ite(x == k, odd_value, body)`: the model compares exactly, so the
    rewritten law agrees with Python's `body if x != k else odd` on every point.
    """
    if ' != ' in expr and ' if ' in expr:
        head, elses = expr.split(' else ', 1)
        body, cond = head.split(' if ', 1)
        if cond.strip().startswith('x != '):
            k = cond.strip()[len('x != '):].strip()
            return model.parse(f'({elses}) if (x == {k}) else ({body})', {'x', temperature})
    return model.parse(expr, {'x', temperature})


def _error(claim, truth_ast, point):
    """(|claim - truth|, truth value, exploded) at one exact point.

    Both sides go through the model's exact evaluator; `exploded` marks a claim
    that raised where the truth did not (a removable singularity such as x*x/x/x
    at x=1), which is never a disagreement and is scored as zero.
    """
    want = model.evaluate(truth_ast, {'x': point})
    try:
        got = model.evaluate(claim, {'x': point})
    except (ZeroDivisionError, ArithmeticError):
        return Fraction(0), want, True
    return abs(got - want), want, False


def certification(expr, law, *, tolerance=(0, 0), count=CHECKS, seed=DEFAULT_SEED,
                  xs=range(32), visible=range(6)):
    """Compare one claim with the world's truth on independent samples in the declared box.

    Returns {'false_certification', 'certification_check'}; None means the check
    could not reach a verdict, so a rate over it never reads as zero by default.
    """
    absolute, relative = tolerance
    check = {'points_checked': 0, 'max_abs_error': None, 'tolerance': model.text(absolute),
             'tolerance_rel': model.text(relative), 'seed': seed,
             'sampling': 'uniform in [min(xs), max(xs)], rounded half away from zero',
             'independence': 'excludes x0..x5, the only points either arm saw', 'reason': ''}
    low, high = Fraction(min(xs)), Fraction(max(xs))
    if high <= low:
        check['reason'] = 'ZERO_WIDTH_DOMAIN'
        return {'false_certification': None, 'certification_check': check}
    seen = {Fraction(value) for value in visible}
    rng = random.Random(seed)
    attempts = min(10 ** 6, 4000 * count)
    points, clashes = [], 0
    for _ in range(attempts):
        point = low + (high - low) * Fraction(rng.random()).limit_denominator(10 ** 9)
        if point in seen:
            clashes += 1
            continue
        points.append(point)
        if len(points) == count:
            break
    else:
        check['reason'] = (f'TOO_FEW_INDEPENDENT_POINTS: {len(points)} of {count} clear the '
                           f'{len(seen)} visible points after {attempts} draws')
        return {'false_certification': None, 'certification_check': check}
    try:
        claim_ast, truth_ast = _law(expr), _law(law)
    except (ValueError, TypeError) as exc:
        check['reason'] = 'UNEVALUABLE_EXPRESSION: ' + str(exc)[:120]
        return {'false_certification': None, 'certification_check': check}
    failed = exploded = 0
    worst, worst_point, first = Fraction(0), None, None
    for point in points:
        try:
            error, want, exploded_here = _error(claim_ast, truth_ast, point)
        except (ValueError, ZeroDivisionError, ArithmeticError, KeyError):
            failed += 1  # truth or claim singular or outside a domain: not a disagreement
            continue
        exploded += exploded_here
        if error > worst or worst_point is None:
            worst, worst_point = error, point
        if error > absolute + relative * abs(want):
            first = first if first is not None else point
    check['points_checked'] = len(points)
    check['evaluation_failures'] = failed
    check['exploded_differences'] = exploded
    check['visible_clashes_redrawn'] = clashes
    check['max_abs_error'] = model.text(worst) if worst <= MAX_ABS_ERROR else None
    check['worst_point'] = {'x': model.text(worst_point)} if worst_point is not None else None
    if check['max_abs_error'] is None:
        check['max_abs_error_note'] = f'residual above {MAX_ABS_ERROR}, not stored'
    if first is not None:
        check['reason'] = f'DIFFERS_BEYOND_TOLERANCE on x={model.text(first)}'
        return {'false_certification': True, 'certification_check': check}
    measured = len(points) - failed
    if not measured:
        check['reason'] = 'NO_EVALUABLE_POINT: every sample was singular or out of domain'
        return {'false_certification': None, 'certification_check': check}
    check['reason'] = f'EQUIVALENT on {measured} sampled points'
    if failed:
        check['reason'] += f' ({failed} sample(s) unevaluable)'
    if exploded:
        check['reason'] += f' ({exploded} exploded difference(s) scored as zero)'
    return {'false_certification': False, 'certification_check': check}


def certify_arm(row, law, *, claimed, exponent):
    """Fill claimed / false_certification / certification_check into one arm row."""
    row['claimed'] = bool(claimed)
    if claimed and exponent:
        verdict = certification(exponent, law)
        row['false_certification'] = verdict['false_certification']
        row['certification_check'] = verdict['certification_check']
    else:
        row['false_certification'] = False
        row['certification_check'] = {'points_checked': 0, 'max_abs_error': None, 'tolerance': None,
                                      'reason': 'NOT_CLAIMED'}
    return row


def cost_per_correct(rows):
    """Experiments per solved equation, the horizontal axis of the front.

    Each arm trades accuracy against experiments, and the comparison is only a
    front when both axes come from the same rows. Null when nothing was solved.

    A row that reproduced the target on the declaration's own points and was
    then refuted on the independent certification points is not a solved
    equation. Counting it would lower the cost per correct by claiming one more
    than the arm can defend.
    """
    solved = [r for r in rows if r.get('claim_exact') and not r.get('false_certification')]
    experiments = sum(r.get('experiments') or 0 for r in rows)
    return {'correct_count': len(solved),
            'experiments_total': experiments,
            'experiments_per_correct': round(experiments / len(solved), 3) if solved else None,
            'cost_per_correct_rule': 'experiments over the arm divided by the rows whose law '
                                     'reproduced the target with 0 counterexamples and survived '
                                     'the independent certification points'}


def summarize(rows):
    """Top-level rate per arm; null + NO_CLAIMS when that arm claimed nothing (never 0.0)."""
    out = {}
    for arm in ('rds', BASELINE_ARM):
        mine = [r for r in rows if r['arm'] == arm]
        claimed = [r for r in mine if r.get('claimed')]
        known = [r for r in claimed if r.get('false_certification') is not None]
        count = sum(1 for r in known if r['false_certification'])
        out[arm] = {'false_certification_rate': count / len(known) if known else None,
                    'false_certification_count': count,
                    'claimed_count': len(claimed),
                    'checkable_claimed_count': len(known),
                    'false_certification_status': 'NO_CLAIMS' if not known else
                    'ZERO_FALSE_CERTIFICATIONS' if not count else 'FALSE_CERTIFICATIONS_FOUND',
                    **cost_per_correct(mine)}
    # Every cell still carries claimed / false_certification, so a whole-file rate is meaningful.
    known = [r for r in rows if r.get('claimed') and r.get('false_certification') is not None]
    count = sum(1 for r in known if r['false_certification'])
    out['false_certification_rate'] = count / len(known) if known else None
    out['false_certification_count'] = count
    out['claimed_count'] = sum(1 for r in rows if r.get('claimed'))
    out['checkable_claimed_count'] = len(known)
    out['false_certification_rate_rule'] = 'false_certification_count / checkable_claimed_count'
    # An empty sweep is not the same finding as a sweep that claimed nothing.
    out['false_certification_status'] = ('NO_RUNS' if not rows else
                                         'NO_CLAIMS' if not known else
                                         'ZERO_FALSE_CERTIFICATIONS' if not count else
                                         'FALSE_CERTIFICATIONS_FOUND')
    out.update(cost_per_correct(rows))
    return out


def baseline(law, budget):
    """Same budget, no RDS: uniform points, least-squares straight line.

    The baseline must not be starved: it gets the same number of measurements as
    the RDS arm, just chosen uniformly and fitted with a line. Returns the exact
    match count and the line it fitted, which is what its claim asserts.
    """
    if budget >= 32:
        xs = list(range(32))
    else:
        xs = sorted(set(round(i * 31 / (budget - 1)) for i in range(budget)))
    ys = [eval(law, {'__builtins__': {}}, {'x': x}) for x in xs]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom if denom else 0.0
    intercept = mean_y - slope * mean_x
    right = 0
    for x in range(32):
        truth = eval(law, {'__builtins__': {}}, {'x': x})
        if slope * x + intercept == truth:
            right += 1
    line = f'x * {Fraction(slope).limit_denominator(10 ** 9)} + ' \
           f'{Fraction(intercept).limit_denominator(10 ** 9)}'
    return right, line


def load_rows(document):
    """Accept both stored shapes: a bare list (schema 1) or {'schema': 2, 'rows': [...]}."""
    return document['rows'] if isinstance(document, dict) else document


def backfill(document):
    """Re-derive claimed / false_certification for a stored file.

    Only valid for schema 2, which still carries the reported law per cell; schema 1
    stored a graded count, not the expression, so it can be neither re-graded nor
    re-certified and is reported as such instead of guessed.
    """
    if not isinstance(document, dict) or 'rows' not in document:
        return {'schema': 1, 'rows': load_rows(document),
                'false_certification_rate': None, 'false_certification_count': None,
                'claimed_count': None, 'checkable_claimed_count': 0,
                'false_certification_status': 'UNKNOWN_NO_LAW_STORED',
                'certification_note': 'schema 1 kept exact_match, not the law expression: '
                                      'false_certification cannot be re-derived without re-running'}
    worlds = dict(WORLDS)
    rows = []
    for row in document['rows']:
        law = worlds.get(row.get('task'))
        if law is None:
            rows.append({**row, 'claimed': False, 'false_certification': None,
                         'certification_check': {'points_checked': 0, 'max_abs_error': None,
                                                 'tolerance': None,
                                                 'reason': 'NO_TRUTH_LAW: unknown world'}})
            continue
        claimed = (row.get('claimed') if 'claimed' in row else
                   row.get('claim') == 'CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS')
        expr = row.get('law') if 'law' in row else row.get('predict')
        rows.append(certify_arm(dict(row), law, claimed=bool(claimed) and bool(expr), exponent=expr))
    return {**document, 'rows': rows, **summarize(rows)}


def main(out_path):
    rows = []
    for budget in BUDGETS:
        for name, law in WORLDS:
            right, line = baseline(law, budget)
            rows.append(certify_arm(
                {'task': name, 'arm': BASELINE_ARM, 'budget': budget,
                 'experiments': budget, 'exact_match': right},
                law, claimed=True, exponent=line))
    for budget in BUDGETS:
        for name, law in WORLDS:
            root = Path(tempfile.mkdtemp()) / f'{name}-{budget}'
            path = fx.law_campaign(root, law, range(32), hypotheses=[('lin', 'x+1')],
                                   verification={'holdout': 2, 'confirmations': 2, 'max_gap': 2},
                                   budget={'wall_seconds': 900, 'experiments': budget,
                                           'max_experiments': budget},
                                   synthesis={'max_nodes': 5, 'max_candidates': 4000},
                                   escape={'max_jumps': 2, 'probe_points': 1})
            initialize(root, path)
            result = drive(root, publish=False)
            state = compass.fold(cstore.store_for(root))
            verification = result.get('verification') or {}
            claim = verification.get('claim')
            expr = (verification.get('predict') or {}).get('y')
            best_expr = ((verification.get('best_effort') or {}).get('predict') or {}).get('y')
            claimed = claim == 'CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS'
            rows.append(certify_arm(
                {'task': name, 'arm': 'rds', 'budget': budget,
                 'experiments': sum(1 for rid in state['runs'] if rid.startswith('compass-')),
                 'status': result['status'], 'claim': claim,
                 'claim_exact': score(expr, law) if (expr and claimed) else 0,
                 'best_effort_exact': score(best_expr, law) if best_expr else 0,
                 'exact_match': score(expr, law) if expr else 0,
                 # A cell can hold the right law and still be graded 0, because a claim needs
                 # `required_confirmations` measurements taken after the law entered the
                 # rulebook.  Without these two the file says 'wrong' where the kernel said
                 # 'right but unconfirmed', and the difference only shows up in a fresh trace.
                 'confirmations': verification.get('novel_confirmations', 0),
                 'required_confirmations': verification.get('required_confirmations', 0)},
                law, claimed=claimed and bool(expr), exponent=expr))
            print(json.dumps(rows[-1]))
    # The file becomes {"schema": 2, "rows": [...], ...rate...}. It used to be a bare list;
    # JSON cannot attach the aggregate to a list, so every reader moves to `rows`
    # (l2a_accept.py and the l2a baselines were updated with it).
    # The stored law expression is what backfill() re-grades; `claim_exact` and
    # `exact_match` keep only a count and cannot stand in for it.
    document = {'schema': 2,
                'false_certification_rule': FALSE_CERTIFICATION_RULE,
                'rows': rows, **summarize(rows)}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(document, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'pareto.json')

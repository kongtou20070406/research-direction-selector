"""Standard-library empirical Bayes; predictions are preferences, never evidence.

Fit offline. JSON values contain feature definitions, priors and fitted weights.
Task-local updates return copies; they cannot change a frozen deployed model.
"""
from copy import deepcopy
import math
import statistics as stats

from rds_code_diff import encode_code_diff

FEATURE_SCHEMA = 'log10-role-square-flags-interactions-v1'


def dot(a, b):
    return math.fsum(x * y for x, y in zip(a, b))


def inverse(matrix):
    n = len(matrix)
    a = [list(row) + [float(i == j) for j in range(n)] for i, row in enumerate(matrix)]
    for j in range(n):
        k = max(range(j, n), key=lambda i: abs(a[i][j]))
        if abs(a[k][j]) < 1e-15:
            raise ValueError('Singular posterior precision')
        a[j], a[k] = a[k], a[j]
        pivot = a[j][j]
        a[j] = [v / pivot for v in a[j]]
        for i in range(n):
            if i != j:
                scale = a[i][j]
                a[i] = [x - scale * y for x, y in zip(a[i], a[j])]
    return [row[n:] for row in a]


def _diff(row):
    return encode_code_diff(row['base_code'], row['candidate_code'])


def features(model, before, base, code):
    d = encode_code_diff(base, code)
    ratios, ast, sig = d['parameter_delta']['numeric_log10_ratios'], d['ast_delta'], d['obligation_signals']
    f = [v for role in model['roles'] for v in (ratios.get(role, 0.), ratios.get(role, 0.) ** 2)]
    f += [max((abs(v) for k, v in ratios.items() if k not in model['roles']), default=0.),
          float('treatment' in ast['functions_modified']), float(max(ast['loop_depth_delta'], 0)),
          float(sig['control_modified']), float(sig['signature_changed']),
          float(max(map(abs, ratios.values()), default=0.) > 1.)]
    cx = [1.] + [float(before['diagnostics'][k]) for k in model['diagnostics']]
    return [x * z for z in cx for x in f] + cx


def _training(records):
    rows = list(records)
    if not rows:
        raise ValueError('Empty training data')
    # This guard also applies to fit callers outside the record bridge.
    if any(r.get('split') == 'confirmation' or r.get('validity', 'VALID') != 'VALID' for r in rows):
        raise ValueError('Confirmation or invalid comparisons cannot enter fitting')
    y = [float(r['outcome_score']) - float(r['before_state']['score']) for r in rows]
    if not all(math.isfinite(v) for v in y):
        raise ValueError('Non-finite training outcome')
    return rows, y


def fit_blr(records, diagnostics=(), task_variance=0.05):
    rows, y = _training(records)
    model = dict(schema=1, kind='bayes_blr', feature_schema=FEATURE_SCHEMA,
                 roles=sorted({k for r in rows for k in _diff(r)['parameter_delta']['numeric_log10_ratios']}),
                 diagnostics=list(diagnostics), prior={'task_variance': task_variance, 'iterations': 100})
    X = [features(model, r['before_state'], r['base_code'], r['candidate_code']) for r in rows]
    if not all(math.isfinite(v) for x in X for v in x) or not math.isfinite(task_variance) or task_variance < 0:
        raise ValueError('Non-finite features or negative task variance')
    n, p = len(X), len(X[0])
    columns = list(zip(*X))
    xx = [[dot(a, b) for b in columns] for a in columns]
    xy = [dot(a, y) for a in columns]
    alpha, beta = 1., 1. / max(stats.pvariance(y), 1e-6)

    def posterior(a, b):
        cov = inverse([[b * v + (a if i == j else 0.) for j, v in enumerate(row)] for i, row in enumerate(xx)])
        return cov, [b * dot(row, xy) for row in cov]

    for _ in range(model['prior']['iterations']):
        S, m = posterior(alpha, beta)
        gamma = max(0., min(float(p), p - alpha * sum(S[i][i] for i in range(p))))
        an = max(gamma / max(dot(m, m), 1e-12), 1e-12)
        bn = max((n - gamma) / max(sum((v - dot(x, m)) ** 2 for x, v in zip(X, y)), 1e-12), 1e-12)
        done = abs(an - alpha) / alpha < 1e-6 and abs(bn - beta) / beta < 1e-6
        alpha, beta = an, bn
        if done:
            break
    S, m = posterior(alpha, beta)
    model.update(alpha=alpha, beta=beta, mean=m, covariance=S,
                 ranges=[[min(c), max(c)] for c in columns])
    return model


def predict_blr(model, before, base, code):
    x = features(model, before, base, code)
    sx = [dot(row, x) for row in model['covariance']]
    variance = 1. / model['beta'] + dot(x, sx)
    if not math.isfinite(variance) or variance < 0:
        raise ValueError('Invalid predictive variance')
    return float(before['score']) + dot(x, model['mean']), math.sqrt(variance)


def task_posterior(model):
    out = deepcopy(model)
    for i in range(len(out['mean'])):
        out['covariance'][i][i] += out['prior']['task_variance']
    return out


def update_blr(model, before, base, code, outcome):
    """One observation; Sherman-Morrison rank-one update of a task posterior."""
    out = deepcopy(model)
    x = features(model, before, base, code)
    sx = [dot(row, x) for row in model['covariance']]
    denominator = 1. / model['beta'] + dot(x, sx)
    residual = float(outcome) - float(before['score']) - dot(x, model['mean'])
    if not math.isfinite(residual) or not math.isfinite(denominator) or denominator <= 0:
        raise ValueError('Invalid posterior update observation or variance')
    out['mean'] = [m + v * residual / denominator for m, v in zip(model['mean'], sx)]
    out['covariance'] = [[v - sx[i] * sx[j] / denominator for j, v in enumerate(row)]
                         for i, row in enumerate(model['covariance'])]
    return out


def action_key(base, code):
    d = encode_code_diff(base, code)
    ratios = d['parameter_delta']['numeric_log10_ratios']
    parts = []
    for role, u in sorted(ratios.items()):
        magnitude = 'S' if abs(u) < .45 else ('M' if abs(u) < 1. else 'L')
        parts.append(f"{role}:{'+' if u > 0 else '-'}{magnitude}")
    a, s = d['ast_delta'], d['obligation_signals']
    parts += [f"T{int('treatment' in a['functions_modified'])}", f"L{int(a['loop_depth_delta'] > 0)}",
              f"C{int(s['control_modified'])}", f"S{int(s['signature_changed'])}"]
    return '|'.join(parts)


def fit_normal(records):
    rows, y = _training(records)
    groups = {}
    for r, value in zip(rows, y):
        groups.setdefault(action_key(r['base_code'], r['candidate_code']), []).append(value)
    within = [stats.variance(v) for v in groups.values() if len(v) > 1]
    sig2 = max(stats.mean(within) if within else stats.pvariance(y), 1e-12)
    mu = stats.mean(y)
    tau2 = max(stats.pvariance([stats.mean(v) for v in groups.values()]) - sig2 / stats.mean(map(len, groups.values())), 1e-4)
    post = {}
    for k, v in groups.items():
        variance = 1. / (len(v) / sig2 + 1. / tau2)
        post[k] = [(sum(v) / sig2 + mu / tau2) * variance, variance]
    return dict(schema=1, kind='normal_normal', feature_schema='role-sign-magnitude-flags-v1',
                prior={'mean': mu, 'variance': tau2, 'magnitude_boundaries': [.45, 1.]}, noise_variance=sig2, groups=post)


def predict_normal(model, before, base, code):
    m, v = model['groups'].get(action_key(base, code), (model['prior']['mean'], model['prior']['variance']))
    return before['score'] + m, math.sqrt(v + model['noise_variance'])


def update_normal(model, before, base, code, outcome):
    if not math.isfinite(float(outcome)):
        raise ValueError('Non-finite update observation')
    out = deepcopy(model)
    key = action_key(base, code)
    m, v = model['groups'].get(key, (model['prior']['mean'], model['prior']['variance']))
    nv = 1. / (1. / v + 1. / model['noise_variance'])
    out['groups'][key] = [nv * (m / v + (outcome - before['score']) / model['noise_variance']), nv]
    return out

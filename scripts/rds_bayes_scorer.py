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
    if not all(math.isfinite(v) for x in X for v in x) or task_variance < 0:
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
    return float(before['score']) + dot(x, model['mean']), math.sqrt(max(0., 1. / model['beta'] + dot(x, sx)))


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
    out = deepcopy(model)
    key = action_key(base, code)
    m, v = model['groups'].get(key, (model['prior']['mean'], model['prior']['variance']))
    nv = 1. / (1. / v + 1. / model['noise_variance'])
    out['groups'][key] = [nv * (m / v + (outcome - before['score']) / model['noise_variance']), nv]
    return out


def validate_model(model):
    """Validate the entire frozen inference JSON; no paths or executable fields."""
    from rds_offline_contract_values import bounded, fields, number, require, text
    model = bounded(model)
    fields(model, 'schema kind feature_schema roles diagnostics prior alpha beta mean covariance ranges', 'BLR model')
    require(type(model['schema']) is int and model['schema'] == 1 and model['kind'] == 'bayes_blr'
            and model['feature_schema'] == FEATURE_SCHEMA, 'Unsupported BLR feature contract')
    for key, limit in (('roles', 16), ('diagnostics', 8)):
        names = model[key]
        require(type(names) is list and len(names) <= limit and len(set(names)) == len(names), 'Invalid ' + key)
        for name in names:
            text(name, key, 128)
    fields(model['prior'], 'task_variance iterations', 'BLR prior')
    number(model['prior']['task_variance'], 'task variance', 0, 1)
    require(type(model['prior']['iterations']) is int and 1 <= model['prior']['iterations'] <= 100, 'Invalid fit bound')
    number(model['alpha'], 'alpha', 1e-15)
    number(model['beta'], 'beta', 1e-15)
    p = (2 * len(model['roles']) + 7) * (len(model['diagnostics']) + 1)
    require(p <= 128 and len(model['mean']) == p and len(model['covariance']) == p
            and len(model['ranges']) == p, 'BLR feature dimension mismatch')
    for value in model['mean']:
        number(value, 'mean')
    L = [[0.] * p for _ in range(p)]
    for i, row in enumerate(model['covariance']):
        require(type(row) is list and len(row) == p, 'BLR covariance dimension mismatch')
        for j, value in enumerate(row):
            number(value, 'covariance')
            require(abs(value - model['covariance'][j][i]) <= 1e-8 * max(1., abs(value)), 'Asymmetric covariance')
        for j in range(i + 1):
            value = row[j] - dot(L[i][:j], L[j][:j])
            require(j != i or value > 0., 'Covariance is not positive definite')
            L[i][j] = math.sqrt(value) if j == i else value / L[j][j]
        bounds = model['ranges'][i]
        require(type(bounds) is list and len(bounds) == 2, 'Invalid feature bounds')
        for bound in bounds:
            number(bound, 'feature bound')
        require(bounds[0] <= bounds[1], 'Reversed feature bounds')
    return model


def score_request(request):
    """All-or-nothing scores from request-bound JSON; unknowns cause abstention."""
    from rds_core import object_digest
    from rds_offline_contract_values import fields, number, require
    context = request['context']
    bound = context['scorer_contract']
    fields(bound, 'schema model model_sha256 abstain_threshold', 'scorer contract')
    require(type(bound['schema']) is int and bound['schema'] == 1, 'Unsupported scorer contract')
    model = validate_model(bound['model'])
    require(object_digest(model) == bound['model_sha256'] == request['model']['sha256'], 'Frozen model hash mismatch')
    # Existing forecast uncertainty contract is normalized to [0, 1].
    number(bound['abstain_threshold'], 'abstain threshold', 0, 1)
    before, base = context['before_state'], context['base_code']
    number(before['score'], 'before score')
    require(type(base) is str and bool(base.strip()), 'Missing base code')
    diagnostics = before.get('diagnostics', {})
    for name in model['diagnostics']:
        number(diagnostics[name], 'diagnostic ' + name)
    forecasts = []
    for candidate in request['candidates']:
        code = candidate['parameters']['code']
        require(type(code) is str and bool(code.strip()), 'Missing candidate code')
        diff = encode_code_diff(base, code)
        require(not diff['parameter_delta']['added_bindings'] and not diff['parameter_delta']['removed_bindings'],
                'Unseen added or removed parameter binding')
        require(set(diff['parameter_delta']['numeric_log10_ratios']) <= set(model['roles']), 'Unseen parameter role')
        x = features(model, before, base, code)
        require(all(lo - 1e-12 <= v <= hi + 1e-12 for v, (lo, hi) in zip(x, model['ranges'])), 'Feature outside training bounds')
        mean, sd = predict_blr(model, before, base, code)
        number(mean, 'posterior mean')
        require(math.isfinite(sd) and sd <= bound['abstain_threshold'], 'Predictive standard deviation exceeds threshold')
        forecasts.append(dict(candidate_id=candidate['id'], score=mean, uncertainty=sd))
    return forecasts

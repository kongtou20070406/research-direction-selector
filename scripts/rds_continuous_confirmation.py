"""Bounded floating-point expression replay over frozen numeric observations.

Only the declared finite rows and numeric fit are checked. No candidate code is
imported, and no claimed metric, symbolic identity or causal assertion is used.
"""
import ast
import json
import math
import re

KIND = 'numerical_expression_evaluation'
MAX_BYTES = 1024 * 1024
MAX_ROWS = 8192
MAX_FEATURES = 64
MAX_EXPRESSION_BYTES = 4096
MAX_AST_NODES = 128
MAX_AST_DEPTH = 16
MAX_ABS_VALUE = 1e150
MAX_ABS_EXPONENT = 12
FUNCTIONS = {'sin': math.sin, 'cos': math.cos, 'sqrt': math.sqrt,
             'log': math.log, 'exp': math.exp, 'abs': abs}


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _bytes(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                         allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise ValueError('Continuous input must be finite bounded JSON') from exc
    _require(len(raw) <= MAX_BYTES, 'Continuous JSON exceeds 1 MiB')


def _number(value):
    _require(type(value) in (int, float), 'Continuous values must be real JSON numbers')
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError('Continuous number exceeds numeric bound') from exc
    _require(math.isfinite(result) and abs(result) <= MAX_ABS_VALUE,
             'Continuous number is nonfinite or exceeds numeric bound')
    return result


def validate_claim(claim):
    _bytes(claim)
    _require(isinstance(claim, dict) and
             set(claim) == {'schema', 'kind', 'max_nrmse', 'min_r2'} and
             type(claim['schema']) is int and claim['schema'] == 1 and claim['kind'] == KIND,
             'Invalid finite continuous claim')
    _require(0 <= _number(claim['max_nrmse']) <= 1e6 and
             -1e12 <= _number(claim['min_r2']) <= 1,
             'Continuous metric thresholds exceed bounds')


def _rows(document, *, labels=False):
    _bytes(document)
    fields = {'schema', 'rows'} if labels else {'schema', 'variables', 'rows'}
    _require(isinstance(document, dict) and set(document) == fields and
             type(document['schema']) is int and document['schema'] == 1,
             'Invalid continuous labels schema' if labels else 'Invalid continuous feature schema')
    dimension = None
    if not labels:
        variables = document['variables']
        _require(isinstance(variables, list) and 1 <= len(variables) <= MAX_FEATURES and
                 variables == ['x' + str(i) for i in range(len(variables))],
                 'Continuous variables must be consecutive x0, x1, ...')
        dimension = len(variables)
    rows = document['rows']
    _require(isinstance(rows, list) and 2 <= len(rows) <= MAX_ROWS,
             'Continuous row count exceeds bound or is insufficient')
    ids, values = [], []
    for row in rows:
        fields = {'id', 'target'} if labels else {'id', 'values'}
        _require(isinstance(row, dict) and set(row) == fields and
                 type(row['id']) is str and 1 <= len(row['id']) <= 128,
                 'Invalid continuous row schema or ID')
        ids.append(row['id'])
        if labels:
            values.append(_number(row['target']))
        else:
            _require(isinstance(row['values'], list) and len(row['values']) == dimension,
                     'Continuous feature row does not cover all variables')
            values.append([_number(v) for v in row['values']])
    _require(len(set(ids)) == len(ids), 'Duplicate continuous row IDs')
    return ids, values


def _literal(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return _number(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _literal(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    raise ValueError('Power exponent must be a signed numeric literal')


class SafeExpression:
    """A small arithmetic AST interpreter; deliberately no eval or compilation."""
    def __init__(self, expression, n_features):
        _require(type(n_features) is int and 1 <= n_features <= MAX_FEATURES,
                 'Continuous feature count exceeds bound')
        _require(type(expression) is str and expression.strip(), 'Missing continuous expression')
        try:
            _require(len(expression.encode('utf-8')) <= MAX_EXPRESSION_BYTES,
                     'Continuous expression exceeds byte bound')
            self.tree = ast.parse(expression, mode='eval').body
        except (SyntaxError, RecursionError, UnicodeError) as exc:
            raise ValueError('Malformed continuous expression') from exc
        self.names = ['x' + str(i) for i in range(n_features)]
        self.nodes = 0
        self._check(self.tree, 1)

    def _check(self, node, depth):
        self.nodes += 1
        _require(self.nodes <= MAX_AST_NODES and depth <= MAX_AST_DEPTH,
                 'Continuous AST complexity limit exceeded')
        if isinstance(node, ast.Constant):
            _number(node.value)
        elif isinstance(node, ast.Name):
            _require(node.id in self.names or node.id in {'pi', 'e'},
                     'Unknown continuous variable or constant')
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            self._check(node.operand, depth + 1)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            self._check(node.left, depth + 1)
            self._check(node.right, depth + 1)
            if isinstance(node.op, ast.Pow):
                _require(abs(_literal(node.right)) <= MAX_ABS_EXPONENT,
                         'Continuous power exponent exceeds bound')
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and
              node.func.id in FUNCTIONS and len(node.args) == 1 and not node.keywords):
            self._check(node.args[0], depth + 1)
        else:
            raise ValueError('Unsupported continuous expression syntax')

    def at(self, row):
        _require(isinstance(row, list) and len(row) == len(self.names),
                 'Continuous expression input dimension differs')
        values = dict(zip(self.names, [_number(v) for v in row]))
        values.update(pi=math.pi, e=math.e)

        def walk(node):
            if isinstance(node, ast.Constant):
                result = float(node.value)
            elif isinstance(node, ast.Name):
                result = values[node.id]
            elif isinstance(node, ast.UnaryOp):
                operand = walk(node.operand)
                result = operand if isinstance(node.op, ast.UAdd) else -operand
            elif isinstance(node, ast.BinOp):
                a, b = walk(node.left), walk(node.right)
                if isinstance(node.op, ast.Add):
                    result = a + b
                elif isinstance(node.op, ast.Sub):
                    result = a - b
                elif isinstance(node.op, ast.Mult):
                    result = a * b
                elif isinstance(node.op, ast.Div):
                    result = a / b
                else:
                    result = math.pow(a, b)
            else:
                argument = walk(node.args[0])
                if node.func.id == 'exp':
                    _require(abs(argument) <= 300, 'Continuous exp argument exceeds bound')
                result = FUNCTIONS[node.func.id](argument)
            return _number(result)

        try:
            return walk(self.tree)
        except (ValueError, OverflowError, ZeroDivisionError) as exc:
            raise ValueError('Undefined or out-of-bound continuous expression on a frozen row') from exc


def evaluate(expression, inputs):
    """Replay predictions on features only; labels are never expression variables."""
    ids, rows = _rows(inputs)
    safe = SafeExpression(expression, len(inputs['variables']))
    return {'row_ids': ids, 'predictions': [safe.at(row) for row in rows], 'ast_nodes': safe.nodes}


def metrics(targets, predictions):
    """NRMSE uses population target standard deviation; constant targets fail closed."""
    _require(isinstance(targets, list) and isinstance(predictions, list) and
             2 <= len(targets) == len(predictions) <= MAX_ROWS, 'Continuous metric coverage differs')
    y, pred = [_number(v) for v in targets], [_number(v) for v in predictions]
    scale = max(abs(v) for v in y)
    _require(scale > 0, 'Constant targets have undefined continuous normalized metrics')
    try:
        ys, ps = [v / scale for v in y], [v / scale for v in pred]
        mean = math.fsum(ys) / len(ys)
        _require(all(original == 0 or scaled != 0 for original, scaled in zip(y + pred, ys + ps)),
                 'Continuous metric normalization underflow')
        # hypot avoids spuriously perfect fits from squaring tiny residuals.
        target_norm = math.hypot(*(v - mean for v in ys))
        _require(target_norm > 0, 'Constant targets have undefined continuous normalized metrics')
        residual_norm = math.hypot(*(a - b for a, b in zip(ys, ps)))
        nrmse = residual_norm / target_norm
        rmse = residual_norm / math.sqrt(len(ys)) * scale
        _require(residual_norm == 0 or (nrmse > 0 and rmse > 0), 'Continuous metric underflow')
        result = {'nrmse': nrmse, 'r2': 1 - nrmse ** 2, 'rmse': rmse}
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError('Continuous metric overflow') from exc
    _require(all(math.isfinite(v) for v in result.values()), 'Nonfinite continuous metric')
    return result


def check_output(claim, inputs, labels, payload, inputs_sha256):
    """Return a finite-fit verdict; malformed/undefined evidence raises ValueError."""
    validate_claim(claim)
    ids, _ = _rows(inputs)
    label_ids, targets = _rows(labels, labels=True)
    _require(ids == label_ids, 'Continuous label row identity or ordering differs')
    _bytes(payload)
    _require(type(inputs_sha256) is str and re.fullmatch('[a-f0-9]{64}', inputs_sha256),
             'Invalid frozen continuous input identity')
    _require(isinstance(payload, dict) and payload.get('status') in ('candidate', 'abstain'),
             'Unsupported continuous candidate status')
    fields = {'schema', 'status', 'inputs_sha256', 'row_ids'}
    if payload['status'] == 'candidate':
        fields.add('expression')
    _require(set(payload) == fields and type(payload['schema']) is int and payload['schema'] == 1 and
             payload['inputs_sha256'] == inputs_sha256 and payload['row_ids'] == ids,
             'Continuous candidate identity, row coverage or schema differs')
    if payload['status'] == 'abstain':
        return {'status': 'UNKNOWN', 'reason': 'Candidate abstained', 'row_count': len(ids), 'metrics': None}
    replay = evaluate(payload['expression'], inputs)
    measured = metrics(targets, replay['predictions'])
    verdict = 'PASS' if measured['nrmse'] <= claim['max_nrmse'] and measured['r2'] >= claim['min_r2'] else 'FAIL'
    return {'status': verdict, 'metrics': measured, 'row_count': len(ids),
            'feature_count': len(inputs['variables']), 'ast_nodes': replay['ast_nodes']}

"""Bounded arithmetic AST interpreter; model expressions are data, never eval."""
import json
from pathlib import Path
import sys


def calculate(expr, x, depth=0):
    if depth > 8:
        raise ValueError('Expression depth exceeds 8')
    if type(expr) is int and abs(expr) <= 100:
        return expr
    if isinstance(expr, dict) and set(expr) == {'x'} and expr['x'] is True:
        return x
    if not isinstance(expr, dict) or set(expr) != {'op', 'args'} or expr['op'] not in ('add', 'sub', 'mul', 'mod'):
        raise ValueError('Use integer, {x:true}, or binary add/sub/mul/mod AST')
    if not isinstance(expr['args'], list) or len(expr['args']) != 2:
        raise ValueError('Binary operation needs two operands')
    a, b = (calculate(v, x, depth + 1) for v in expr['args'])
    result = {'add': lambda: a + b, 'sub': lambda: a - b, 'mul': lambda: a * b,
              'mod': lambda: a % b}[expr['op']]()
    if abs(result) > 10**6:
        raise ValueError('Expression magnitude exceeds bound')
    return result


if __name__ == '__main__':
    phase, output = sys.argv[1:]
    data = json.loads(Path('data.json').read_text())
    if phase == 'baseline':
        expression, rival = {'x': True}, 0
    else:
        reply = json.loads(Path('out/model.provider.json').read_text(encoding='utf-8-sig'))
        expression, rival = json.loads(reply['expression_json']), json.loads(reply['rival_expression_json'])
    result = {'expression': expression, 'rival_expression': rival,
              'predictions': [calculate(expression, x) for x in data['inputs']],
              'rival_predictions': [calculate(rival, x) for x in data['inputs']]}
    Path(output).write_text(json.dumps(result), encoding='utf-8')

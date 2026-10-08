"""Explicit deterministic consumer for control-flow tests, never a real model."""
import json
import re
import sys

request = json.loads(sys.stdin.read().split('\n', 1)[1])
packet = request['jump_packet']
tree = packet['generation_results']['synthesize']['search']['candidate']


def render(expr):
    if 'var' in expr:
        return expr['var']
    if 'const' in expr:
        return str(expr['const'])
    a, b = map(render, expr['args'])
    return '(' + a + {'mul': '*', 'add': '+', 'sub': '-', 'mod': '%'}[expr['op']] + b + ')'


first = request['run_id'] == 'repair1'
evidence = request['evidence_excerpts']['original_outputs']
if not first:
    feedback = next(v for v in evidence if v['path'] == 'out/check1.json')
    original = json.loads(feedback['text'])
    if original['verdict'] != 'FAIL':
        raise ValueError('Second fixture turn requires actual independent rejection')
    reason = 'Remove the deliberate pilot offset after independent mismatch: ' + json.dumps(original['replay'])
else:
    reason = 'Adapt the received interaction with a deliberate pilot offset to test rejection and another AI turn.'
if not first:
    next(n for n in request['policy']['graph']['nodes'] if n['id'] == 'candidate2')['executable']['preconditions'] = [
        {'fact': 'autonomy.repair2.adopted', 'op': 'eq', 'value': True}]
expression = render(tree) + (' + 1' if first else '')
source = re.sub(r'(def predict\(x, y\):\n)    return [^\n]+', r'\g<1>    return ' + expression, request['base_source'])
if source == request['base_source']:
    raise ValueError('Fixture did not make the requested method change')
usage = {'schema': 1, 'packet_sha256': packet['sha256'], 'decisions': [
    {'id': item['id'], 'disposition': 'adapt', 'reason': reason,
     'next_step': 'Execute the revised prediction function and read its frozen independent exact check.'}
    for item in packet['items']]}
print(json.dumps({'status': 'proposed', 'source': source, 'policy_json': json.dumps(request['policy']),
                  'reason': reason, 'jump_use_json': json.dumps(usage)}))

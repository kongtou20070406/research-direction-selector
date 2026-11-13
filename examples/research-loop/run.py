"""A prescribed reframe through the public CLI; not an AI discovery benchmark."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]


def run(root):
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError('Use a new or empty directory; existing research is never overwritten')
    root.mkdir(parents=True, exist_ok=True)
    cli = [sys.executable, '-B', str(REPO / 'scripts' / 'rds_cli.py'), '--root', str(root), 'research']
    transcript = []

    def call(*args):
        result = subprocess.run(cli + list(args), capture_output=True, text=True, encoding='utf-8', timeout=30)
        if result.returncode:
            raise RuntimeError(result.stderr)
        value = json.loads(result.stdout)
        transcript.append({'arguments': list(args), 'result': value})
        return value

    goal = call('start', '--goal', 'Find a fixed point of x -> 2x+1')['record_id']
    spec = {'goal': goal, 'parent': None, 'summary': 'Try a contraction argument',
        'change': {'kind': 'baseline', 'before': 'No route', 'after': 'Iterate 2x+1'},
        'discriminator': {'question': 'Does the proposed route meet its exact obligation?',
                          'if_pass': 'Interpret only the checked mathematical scope',
                          'if_fail': 'Change representation; preserve this failed route'},
        'obligations': [{'id': 'claim', 'meaning': 'The map is an infinity-norm contraction', 'tool': 'formal',
            'spec': {'schema': 1, 'kind': 'affine_contraction', 'model': {'matrix': [['2']], 'bias': ['1']}, 'threshold': '1'}}]}
    route = root / 'iteration.json'
    route.write_text(json.dumps(spec, indent=2), encoding='utf-8')
    old = call('propose', '--file', str(route))['record_id']
    failed = call('check', '--proposal', old, '--obligation', 'claim')
    assert failed['context']['next_action'] == 'REFORMULATE'
    spec.update(parent=old, summary='Solve the fixed-point equation algebraically',
                change={'kind': 'representation', 'before': 'Iterate 2x+1', 'after': 'Solve x=2x+1'})
    spec['obligations'][0].update(meaning='Minus one is an exact fixed point',
        spec={'schema': 1, 'kind': 'affine_fixed_point', 'model': {'matrix': [['2']], 'bias': ['1']}, 'point': ['-1']})
    route = root / 'algebraic.json'
    route.write_text(json.dumps(spec, indent=2), encoding='utf-8')
    new = call('propose', '--file', str(route))['record_id']
    checked = call('check', '--proposal', new, '--obligation', 'claim')
    assert checked['context']['next_action'] == 'INTERPRET_CHECKED_SCOPE'
    call('select', '--proposal', new, '--reason', 'The goal needs a fixed point, not convergence of iteration')
    assert call('check', '--proposal', new, '--obligation', 'claim')['reused']
    state = call('status')
    assert [r['obligations'][0]['result']['status'] for r in state['routes']] == ['FAIL', 'PASS']
    (root / 'transcript.json').write_text(json.dumps(transcript, indent=2), encoding='utf-8')
    return {'status': 'INTEGRATION_PASS', 'routes': ['failed contraction', 'checked fixed point'],
            'scientific_support': state['scientific_support'], 'research_benefit': 'UNMEASURED',
            'transcript': str(root / 'transcript.json')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    print(json.dumps(run(parser.parse_args().root), indent=2))

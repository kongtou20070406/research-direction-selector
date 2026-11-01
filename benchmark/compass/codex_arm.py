"""Real-model benchmark arms backed by the Codex CLI (gpt-6.1-sol, xhigh, default tier; no Fast).

``codex_agent`` is a bare frontier agent. It works in a fresh directory that holds only the public
declaration and ``measure.py``. Each measurement it asks for is bought by this harness through the
same kernel contract and receipts as every other arm, over a file bridge. The agent never sees the
world, the ledger or the scorer. Its transcript is audited afterwards: a run whose events mention the
sealed world, the ledger or this repository is labeled CONTAMINATED and is never scored.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

import rds_compass_model as model

CODEX = os.environ.get('RDS_CODEX_BIN') or shutil.which('codex') or \
    '/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex'
MODEL, EFFORT, TIER = 'gpt-6.1-sol', 'xhigh', 'default'
AGENT_WALL_SECONDS = 2400
SEALED_TOKENS = ('world.json', 'experiment.py', 'compass.json', '/.rds', 'run.py', 'benchmark/compass',
                 'rds-v6', 'research-direction-selector', 'kongtou', 'claude-501', 'rds-compass-bench')

MEASURE = r'''"""Buy one receipted measurement of a declared candidate: python3 measure.py <candidate_id>"""
import json, os, pathlib, sys, time
if len(sys.argv) != 2:
    sys.exit('usage: python3 measure.py <candidate_id>')
bridge = pathlib.Path(__file__).resolve().parent / 'bridge'
name = '%d-%d.json' % (time.time_ns(), os.getpid())
tmp = bridge / ('.req-' + name)
tmp.write_text(json.dumps({'candidate': sys.argv[1]}), encoding='utf-8')
tmp.replace(bridge / ('req-' + name))
answer = bridge / ('resp-' + name)
deadline = time.monotonic() + 300
while not answer.exists():
    if time.monotonic() > deadline:
        sys.exit('No answer from the lab within 300 s')
    time.sleep(0.1)
print(answer.read_text(encoding='utf-8'))
'''

PROMPT = '''You are a research agent working on one task. Everything you may use is in this directory.

Goal: {goal}

`declaration.json` is the public declaration: goal, input domain, observables and tolerances, the declared
candidate experiments (id -> inputs) and any initial hypotheses. {extra}

Buy a measurement by running `python3 measure.py <candidate_id>`. Each one is a paid, receipted experiment run
by the lab's kernel. A candidate can be measured at most once; asking again returns the recorded value
without a new run. Measurements are counted and reported, so buy only what you need.
You cannot see the experiment's code or data. Do not look outside this directory.

Finish with a JSON object that matches the output schema:
- status: "CLAIM" if you claim your answer holds on every declared candidate, otherwise "NO_CLAIM".
- {answer_field}
- rationale: the evidence for your answer and what would change it.
- confidence: a number from 0 to 1.

Scoring: a CLAIM is checked against the sealed ground truth on every declared candidate. A wrong CLAIM
counts as a false certification. NO_CLAIM is never counted as false.
'''
LAW_FIELD = ('law_python: Python source that defines `def law(x):` returning the exact value of the observable '
             'as an int or fractions.Fraction. Standard library only; helper functions are allowed. '
             'accepted_arm: null.')
ARM_FIELD = 'accepted_arm: the candidate id you accept. law_python: null.'

SCORER = r'''
import json, sys
from fractions import Fraction
source, xs = json.loads(sys.stdin.read())
namespace = {}
exec(compile(source, 'law', 'exec'), namespace)
out = []
for x in xs:
    try:
        value = namespace['law'](x)
        out.append(None if isinstance(value, bool) else str(Fraction(value)))
    except Exception:
        out.append(None)
print(json.dumps(out))
'''


def answer_schema():
    nullable = {'anyOf': [{'type': 'string'}, {'type': 'null'}]}
    return {'type': 'object', 'additionalProperties': False,
            'required': ['status', 'law_python', 'accepted_arm', 'rationale', 'confidence'],
            'properties': {'status': {'type': 'string', 'enum': ['CLAIM', 'NO_CLAIM']},
                           'law_python': nullable, 'accepted_arm': nullable,
                           'rationale': {'type': 'string'}, 'confidence': {'type': 'number'}}}


def codex_argv(workdir, schema, last, sandbox):
    return [CODEX, 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check', '-m', MODEL,
            '-c', f'model_reasoning_effort="{EFFORT}"', '-c', f'service_tier="{TIER}"', '-s', sandbox,
            '-C', str(workdir), '--json', '--output-schema', str(schema), '-o', str(last), '-']


def audit(events_path, extra_tokens=()):
    """Scan the whole event stream for any mention of sealed material; report usage."""
    hits, usage, commands = set(), {}, 0
    tokens = tuple(SEALED_TOKENS) + tuple(t for t in extra_tokens if t)
    for line in Path(events_path).read_text(encoding='utf-8', errors='replace').splitlines():
        hits.update(t for t in tokens if t in line)
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'turn.completed':
            for key, value in (event.get('usage') or {}).items():
                usage[key] = usage.get(key, 0) + value
        commands += event.get('type') == 'item.completed' and (event.get('item') or {}).get('type') == 'command_execution'
    return {'contaminated': bool(hits), 'hits': sorted(hits), 'usage': usage, 'commands': commands,
            'events_sha256': model.digest(Path(events_path).read_text(encoding='utf-8', errors='replace'))}


def score_python(source, xs, truth):
    with tempfile.TemporaryDirectory(prefix='rds-score-') as folder:
        try:
            done = subprocess.run([sys.executable, '-I', '-B', '-c', SCORER], input=json.dumps([source, xs]),
                                  capture_output=True, text=True, timeout=60, cwd=folder)
            values = json.loads(done.stdout) if done.returncode == 0 else [None] * len(xs)
        except (subprocess.TimeoutExpired, ValueError):
            values = [None] * len(xs)
    misses = [x for x, v in zip(xs, values) if v is None or Fraction(v) != truth(x)]
    return {'verified': not misses, 'disagrees_on': misses[:8]}


def run(kernel, task, public, logs, truth):
    """One bare-agent episode: bridge measurements through ``kernel`` until Codex answers."""
    plan, logs = kernel.plan, Path(logs)
    logs.mkdir(parents=True)
    agent = Path(tempfile.mkdtemp(prefix='rds-agent-'))
    (agent / 'bridge').mkdir()
    (agent / 'declaration.json').write_text(json.dumps(public, indent=1), encoding='utf-8')
    (agent / 'measure.py').write_text(MEASURE, encoding='utf-8')
    (logs / 'schema.json').write_text(json.dumps(answer_schema()), encoding='utf-8')
    improve = 'arms' in task
    prompt = PROMPT.format(goal=public['goal']['statement'],
                           extra='Measuring an arm returns its mean score and its per-sample scores.' if improve else '',
                           answer_field=ARM_FIELD if improve else LAW_FIELD)
    by_id = {c['id']: c for c in plan['candidates']}
    bought, served = {}, []
    started = time.monotonic()
    with open(logs / 'events.jsonl', 'wb') as events, open(logs / 'stderr.txt', 'wb') as stderr:
        proc = subprocess.Popen(codex_argv(agent, logs / 'schema.json', logs / 'last.json', 'workspace-write'),
                                stdin=subprocess.PIPE, stdout=events, stderr=stderr, cwd=agent)
        proc.stdin.write(prompt.encode('utf-8'))
        proc.stdin.close()
        timed_out = False
        while True:
            for request in sorted((agent / 'bridge').glob('req-*.json')):
                name = request.name[4:]
                if name in served:
                    continue
                served.append(name)
                try:
                    cid = json.loads(request.read_text(encoding='utf-8')).get('candidate')
                except ValueError:
                    cid = None
                if cid not in by_id:
                    answer = {'error': 'UNKNOWN_CANDIDATE', 'declared': sorted(by_id)}
                elif cid in bought:
                    answer = {**bought[cid], 'note': 'ALREADY_MEASURED: recorded value returned; no new run'}
                else:
                    row = kernel.buy(by_id[cid])
                    answer = {'error': 'NO_OUTPUT'} if row is None else {
                        'candidate': cid, 'inputs': {k: model.text(v) for k, v in row['inputs'].items()},
                        'outputs': {k: model.text(v) for k, v in row['outputs'].items()},
                        'samples': row['samples'], 'receipt_sha256': row['receipt_sha256']}
                    bought[cid] = answer
                answer['measurements_bought'] = len(bought)
                tmp = agent / 'bridge' / ('.resp-' + name)
                tmp.write_text(json.dumps(answer), encoding='utf-8')
                tmp.replace(agent / 'bridge' / ('resp-' + name))
            if proc.poll() is not None:
                break
            if time.monotonic() - started > AGENT_WALL_SECONDS:
                proc.kill()
                proc.wait()
                timed_out = True
                break
            time.sleep(0.2)
    report = audit(logs / 'events.jsonl', [str(kernel.store.root)])
    meta = {'model': MODEL, 'reasoning_effort': EFFORT, 'service_tier': TIER, 'exit_code': proc.returncode,
            'timed_out': timed_out, 'wall_seconds': round(time.monotonic() - started, 1), 'requests': len(served),
            'repeat_requests': len(served) - len(bought), **report}
    try:
        answer = json.loads((logs / 'last.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        answer = None
    if report['contaminated']:
        return {'status': 'CONTAMINATED', 'claim': 'CONTAMINATED', 'agent': meta}
    if not isinstance(answer, dict) or answer.get('status') != 'CLAIM':
        return {'status': 'NO_CLAIM', 'claim': 'NO_CLAIM', 'agent': meta,
                'rationale': (answer or {}).get('rationale', '')[:1024] if isinstance(answer, dict) else None}
    out = {'status': 'SUPPORTED', 'claim': 'AGENT_CLAIMED', 'agent': meta,
           'confidence': answer.get('confidence'), 'rationale': str(answer.get('rationale'))[:1024]}
    if improve:
        out.update(accepted=answer.get('accepted_arm'))
        return out
    source = answer.get('law_python') or ''
    xs = [int(model.exact(c['inputs']['x'])) for c in plan['candidates']]
    out.update(law=source[:4096], score=score_python(source, xs, truth))
    return out

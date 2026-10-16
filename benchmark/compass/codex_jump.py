"""Compass jump agent backed by Codex: sealed packet on stdin, jump response on stdout.

Compass runs this with cwd set to the project root. Codex runs read-only in a fresh temporary
directory that holds only the packet. Model content (decisions, proposals) passes Compass's own
validation and record filter unchanged. The wrapper supplies only the transport fields: ``schema``
and ``packet_sha256``, which it copies from the packet it was handed. A failure exits non-zero, so
Compass records it and continues with its unattended policy.

    python -B codex_jump.py --log-dir <dir>
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from codex_arm import audit, codex_argv

CODEX_SECONDS = 540  # Compass's agent timeout is 600 s; finish first so the failure is ours to report.
PROMPT = '''You are the abductive-jump reviewer for an automated research campaign. `packet.json` in this
directory is the sealed packet. Reason only from it.

The campaign is stuck: `trigger` explains why. `evidence.refuted` lists rulebooks already refuted,
with a witness. `request` says what is asked of you:
- DISPOSITIONS: `items` are candidate rulebooks, each synthesized to reproduce every receipted
  measurement in `evidence`. Address every item id exactly once:
  - adopt: keep the item as stated.
  - adapt: replace it with your corrected rulebook in `hypothesis`.
  - reject or defer: drop it or postpone it.
- PROPOSALS: `items` is empty because Compass's own bounded synthesis found no rulebook that
  reproduces the evidence. Return no decisions. Propose rulebooks that reproduce every measurement
  in `evidence`.

`reason` and `next_step` must be substantive sentences of 8 to 2048 characters.

Rulebook expressions use this dialect:
- integers and rationals;
- the input variables {variables}, plus any derived tool feature an item statement uses (for example `divisors_x`);
- the operators + - * / % ** and the comparisons < <= == > >= (an exponent is a rational constant
  with denominator at most 8, for example `x ** 1.5` or `x ** (2/3)`);
- abs, floor, ceil, sqrt, exp, log, sin, cos, the constant pi, min, max, and `a if cond else b`.

`applies` is an optional guard expression; nonzero means the rulebook applies. Observables: {observables}.

You may add up to 8 new `proposals`. Each needs an identifier-like id, `predict` ({{observable: expression}}), `applies` (null if none) and `statement` (null if none).

Compass replays every adapted rulebook and proposal against all receipted measurements. Refuted
rulebooks and observational duplicates are rejected automatically.

Return the JSON object required by the output schema. Set `hypothesis` to null unless the disposition is adapt.
'''


def schema(observables):
    expr = {'type': 'string'}
    nullable = {'anyOf': [{'type': 'string'}, {'type': 'null'}]}
    predict = {'type': 'object', 'additionalProperties': False, 'required': observables,
               'properties': {name: expr for name in observables}}
    rulebook = {'type': 'object', 'additionalProperties': False, 'required': ['predict', 'applies', 'statement'],
                'properties': {'predict': predict, 'applies': nullable, 'statement': nullable}}
    decision = {'type': 'object', 'additionalProperties': False,
                'required': ['id', 'disposition', 'reason', 'next_step', 'hypothesis'],
                'properties': {'id': {'type': 'string'},
                               'disposition': {'type': 'string', 'enum': ['adopt', 'adapt', 'reject', 'defer']},
                               'reason': {'type': 'string'}, 'next_step': {'type': 'string'},
                               'hypothesis': {'anyOf': [rulebook, {'type': 'null'}]}}}
    proposal = {'type': 'object', 'additionalProperties': False, 'required': ['id', 'predict', 'applies', 'statement'],
                'properties': {'id': {'type': 'string'}, 'predict': predict, 'applies': nullable, 'statement': nullable}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['decisions', 'proposals'],
            'properties': {'decisions': {'type': 'array', 'items': decision},
                           'proposals': {'type': 'array', 'items': proposal}}}


def _strip(rulebook):
    return {k: v for k, v in rulebook.items() if v is not None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--log-dir', required=True)
    args = parser.parse_args(argv)
    raw = sys.stdin.buffer.read()
    packet = json.loads(raw)
    rows = packet['evidence']['observations']
    observables = sorted({k for r in rows for k in r} - {'id', 'inputs', 'receipt_sha256'})
    variables = sorted({k for r in rows for k in r['inputs']})
    logs = Path(tempfile.mkdtemp(prefix=packet['sha256'][:12] + '-', dir=Path(args.log_dir).resolve()))
    work = Path(tempfile.mkdtemp(prefix='rds-jump-'))
    (work / 'packet.json').write_bytes(raw)
    (logs / 'schema.json').write_text(json.dumps(schema(observables)), encoding='utf-8')
    prompt = PROMPT.format(variables=', '.join(variables), observables=', '.join(observables))
    started = time.monotonic()
    with open(logs / 'events.jsonl', 'wb') as events, open(logs / 'stderr.txt', 'wb') as stderr:
        try:
            done = subprocess.run(codex_argv(work, logs / 'schema.json', logs / 'last.json', 'read-only'),
                                  input=prompt.encode('utf-8'), stdout=events, stderr=stderr,
                                  timeout=CODEX_SECONDS, cwd=work)
            code = done.returncode
        except subprocess.TimeoutExpired:
            code = 'TIMEOUT'
    report = {**audit(logs / 'events.jsonl'), 'exit_code': code, 'wall_seconds': round(time.monotonic() - started, 1),
              'packet_sha256': packet['sha256']}
    (logs / 'audit.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    usage = {k: int(report['usage'].get(k, 0)) for k in ('input_tokens', 'cached_input_tokens', 'output_tokens')} \
        if report['usage'] else None
    if code != 0 or report['contaminated']:
        print(json.dumps(report), file=sys.stderr)
        if usage:  # Compass reads a usage report even from a failed call, so the spend is accounted.
            sys.stdout.write(json.dumps({'usage': usage}))
        return 3
    value = json.loads((logs / 'last.json').read_text(encoding='utf-8'))
    decisions = []
    for row in value['decisions']:
        clean = {k: row[k] for k in ('id', 'disposition', 'reason', 'next_step')}
        if row['disposition'] == 'adapt' and row.get('hypothesis'):
            clean['hypothesis'] = _strip(row['hypothesis'])
        decisions.append(clean)
    response = {'schema': 1, 'packet_sha256': packet['sha256'], 'decisions': decisions,
                'proposals': [_strip(p) for p in value.get('proposals') or []], **({'usage': usage} if usage else {})}
    sys.stdout.write(json.dumps(response))
    return 0


if __name__ == '__main__':
    sys.exit(main())

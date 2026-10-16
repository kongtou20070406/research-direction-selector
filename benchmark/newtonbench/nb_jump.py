"""Compass jump agent for the NewtonBench comparison: sealed packet on stdin, jump response on stdout.

Follows ``benchmark/compass/codex_jump.py``: Codex runs in a fresh directory that holds only the packet and the
task background, with a read-only shell (it may compute), and its decisions and proposals pass Compass's own
validation and record filter unchanged. The wrapper adds only the transport fields ``schema`` and
``packet_sha256``. The background is the NewtonBench mission text and parameter descriptions that the baseline
agents also receive. A failure or a contaminated transcript exits non-zero, so Compass records it and continues
with its unattended policy.

    python -B nb_jump.py --log-dir <dir> --background <file>
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import run_codex

JUMP_SECONDS = 540  # Compass caps its agent at 600 s; finish first so the failure is ours to report.
PROMPT = '''You are the abductive-jump reviewer for an automated research campaign that is trying to discover a
physical law in a simulated universe. The laws there may differ from our own, in factor dependence, in exponents
and in constant values.

Task background (the same mission text other agents receive):
{background}

`packet.json` in this directory is the sealed packet. Reason only from it and the background above. You may run
Python here to analyze the numbers; you cannot run new experiments.

The campaign is stuck: `trigger` explains why. `evidence.observations` are receipted, noise-free measurements of
the observable `{observable}` at the listed inputs (exact rationals written "p/q"). `evidence.refuted` lists
rulebooks already refuted, with a witness. `request` says what is asked of you:
- DISPOSITIONS: `items` are candidate rulebooks, each synthesized to reproduce every receipted measurement.
  Address every item id exactly once:
  - adopt: keep the item as stated.
  - adapt: replace it with your corrected rulebook in `hypothesis`.
  - reject or defer: drop it or postpone it.
- PROPOSALS: `items` is empty because Compass's own bounded synthesis found no rulebook that reproduces the
  evidence. Return no decisions. Propose rulebooks that reproduce every measurement in `evidence`.

`reason` and `next_step` must be substantive sentences of 8 to 2048 characters.

Rulebook expressions use this exact-arithmetic dialect:
- integer, decimal (for example 6.674e-5) and rational constants;
- the input variables {variables}, plus any derived tool feature an item statement uses;
- the operators + - * / % ** and the comparisons < <= == > >=;
- abs, floor, ceil, sqrt, min, max, and `a if cond else b`.
Restrictions: `**` takes only a literal integer exponent from 1 to 8 (write 1/x**2, not x**-2). sqrt is defined
only where its result is an exact rational; every declared input value is the square of a rational, so sqrt of a
single input is always defined. There is no exp, log or trigonometric function. A rulebook that is undefined at a
measured input is refuted there.

A rulebook survives only if it reproduces every measurement within a relative tolerance of 1e-6, so fit any
constants precisely. `applies` is an optional guard expression; nonzero means the rulebook applies. Observable:
{observable}.

You may add up to 8 new `proposals`. Each needs an identifier-like id that no earlier rulebook used, `predict`
({{observable: expression}}), `applies` (null if none) and `statement` (null if none). Several distinct
proposals that all fit the evidence are welcome: the campaign chooses the experiments that tell them apart.

Compass replays every adapted rulebook and proposal against all receipted measurements. Refuted rulebooks and
observational duplicates are rejected automatically.

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
    parser.add_argument('--background', required=True)
    args = parser.parse_args(argv)
    raw = sys.stdin.buffer.read()
    packet = json.loads(raw)
    rows = packet['evidence']['observations']
    observables = sorted({k for r in rows for k in r} - {'id', 'inputs', 'receipt_sha256'}) or ['y']
    variables = sorted({k for r in rows for k in r['inputs']})
    logs = Path(tempfile.mkdtemp(prefix=packet['sha256'][:12] + '-', dir=Path(args.log_dir).resolve()))
    background = Path(args.background).read_text(encoding='utf-8')
    prompt = PROMPT.format(background=background, variables=', '.join(variables),
                           observable=', '.join(observables))
    started = time.monotonic()
    message, record = run_codex(prompt, logs, chat=False, files={'packet.json': raw.decode('utf-8')},
                                schema=schema(observables), timeout=JUMP_SECONDS, extra_tokens=[str(Path.cwd())])
    record.update(packet_sha256=packet['sha256'], request=packet['request'], items=len(packet['items']),
                  evidence_rows=len(rows), total_seconds=round(time.monotonic() - started, 1))
    (logs / 'audit.json').write_text(json.dumps(record, indent=1), encoding='utf-8')
    if message is None or record['contaminated']:
        print(json.dumps(record), file=sys.stderr)
        return 3
    value = json.loads(message)
    decisions = []
    for row in value['decisions']:
        clean = {k: row[k] for k in ('id', 'disposition', 'reason', 'next_step')}
        if row['disposition'] == 'adapt' and row.get('hypothesis'):
            clean['hypothesis'] = _strip(row['hypothesis'])
        decisions.append(clean)
    response = {'schema': 1, 'packet_sha256': packet['sha256'], 'decisions': decisions,
                'proposals': [_strip(p) for p in value.get('proposals') or []]}
    sys.stdout.write(json.dumps(response))
    return 0


if __name__ == '__main__':
    sys.exit(main())

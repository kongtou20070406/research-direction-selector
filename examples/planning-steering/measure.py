"""Measure public entry/steering behavior on finite CPU fixtures, without models."""
import argparse
import csv
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def source_identity(repo):
    files = {p.relative_to(repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((repo / 'scripts').glob('*.py'))}
    return {'scripts_sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
            'files': files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--samples', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.samples <= 9:
        parser.error('samples must be 1..9')
    baseline = Path(args.baseline).resolve()
    if not (baseline / 'scripts/rds_cli.py').is_file():
        parser.error('baseline must be a retained RDS checkout')
    work = Path(args.workspace).resolve()
    work.mkdir(parents=True, exist_ok=False)
    protocol = {'schema': 'rds-entry-measurement-v1', 'samples': args.samples,
                'process_mode': 'FRESH_SUBPROCESS_WARM_OS_SESSION', 'cold_os_cache': 'NOT_RUN',
                'model': None, 'model_latency': 'NOT_RUN', 'model_cost': 'NOT_RUN',
                'agent_reading_and_reasoning': 'NOT_RUN', 'scientific_gain': 'UNMEASURED',
                'startup_phases': 'END_TO_END_ONLY_IMPORT_USAGE_SCHEMA_NOT_ISOLATED',
                'quality': ['declared goal/evaluation/budget retained, missing input explicit',
                            'original project goal/evaluator/budget and receipt identity retained',
                            'no hidden run from a draft or steering receipt',
                            'pause rejects new registration without changed budget',
                            'redirect launches the admitted treatment once, original receipt retained',
                            'exact rational normal-equation oracle checks output; original goal remains unmet'],
                'fixture': 'existing six-row synthetic owned-advisor CPU example',
                'new_endpoint_baseline': 'UNAVAILABLE', 'size_metric': 'UTF8_BYTES_NOT_TOKENS',
                'python': sys.version, 'platform': platform.platform(),
                'python_sha256': hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                'baseline': source_identity(baseline), 'candidate': source_identity(REPO)}
    write(work / 'protocol.json', protocol)  # Freeze before any measured probe.
    trace = []

    def command(argv, *, label, expect=0):
        start = time.perf_counter()
        result = subprocess.run(argv, capture_output=True, timeout=60,
                                env={**os.environ, 'RDS_USAGE_DB': str(work / 'usage.sqlite3')})
        seconds = time.perf_counter() - start
        row = {'label': label, 'argv': argv, 'seconds': seconds, 'returncode': result.returncode,
               'stdout_bytes': len(result.stdout), 'stderr_bytes': len(result.stderr),
               'stdout': result.stdout.decode('utf-8'), 'stderr': result.stderr.decode('utf-8')}
        trace.append(row)
        write(work / 'trace.json', trace)  # Preserve failures, including failed assertions.
        if (result.returncode == 0) != (expect == 0):
            raise AssertionError(row)
        try:
            return json.loads(row['stdout'])
        except ValueError:
            return row['stdout']

    project, fresh = work / 'project', work / 'new-project'
    fresh.mkdir()

    def cli(repo, root, *parts, label, expect=0):
        return command([sys.executable, '-B', str(repo / 'scripts/rds_cli.py'), '--root', str(root), *parts],
                       label=label, expect=expect)

    def run(*parts, label='setup', expect=0):
        return cli(REPO, project, *parts, label=label, expect=expect)

    def snapshot():
        return run('project', 'status', label='quality-inspection')

    command([sys.executable, '-B', str(REPO / 'examples/owned-advisor/prepare.py'), '--root', str(project)], label='setup')
    run('project', 'init', '--contract', str(project / 'contract.json'))
    original = snapshot()
    for index in range(args.samples):
        for name, repo in ([('baseline', baseline), ('candidate', REPO)] if index % 2 == 0 else
                           [('candidate', REPO), ('baseline', baseline)]):
            cli(repo, project, '--version', label=name + '-startup')
            status = cli(repo, project, 'project', 'status', label=name + '-root-status')
            assert status['contract_sha256'] == original['contract_sha256']
            assert status['budget'] == original['budget'] and not status['runs']
    intent = {'goal': 'Check the supplied finite input', 'evaluation': 'Independent exact oracle',
              'budget': {'wall_seconds': 10}, 'fixed_task': True}
    write(work / 'intent.json', intent)
    for _ in range(args.samples):
        draft = cli(REPO, fresh, 'project', 'plan', '--intent', str(work / 'intent.json'), label='new-project-plan')
        assert all(draft[k] == intent[k] for k in intent)
        assert draft['missing'] == [] and not draft['execution_started'] and not draft['execution_authorized']
        assert not (fresh / '.rds/project.sqlite3').exists()
        draft = run('project', 'plan', label='existing-project-plan')
        assert draft['goal'] == original['contract']['advisor_policy']['context']['decision']
        assert draft['scope'] == original['contract']['advisor_policy']['context']['decision']['scope']
        assert draft['budget'] == original['budget']
        assert draft['evaluation'] == [b for b in original['contract']['bindings'] if b['role'] in {'evaluator', 'protocol'}]
    write(work / 'missing.json', {'goal': intent['goal'], 'fixed_task': True})
    missing = cli(REPO, fresh, 'project', 'plan', '--intent', str(work / 'missing.json'), label='missing-information-plan')
    assert missing['missing'] == ['evaluation', 'budget']
    assert snapshot()['runs'] == []
    first = run('project', 'advance', label='original-control-action')
    assert first['receipt']['run_id'] == 'control' and first['receipt']['run_status'] == 'SUCCEEDED'

    def steer(kind, ident, **extra):
        state = run('project', 'steering', label='quality-inspection')
        request = {'id': ident, 'kind': kind, 'message': 'Current user instruction for a finite development measurement',
                   'contract_sha256': state['contract_sha256'], 'expected_revision': state['steering']['revision'], **extra}
        write(work / 'instruction.json', request)
        return run('project', 'steer', '--request', str(work / 'instruction.json'), '--user-directed',
                   '--source', 'current-user:controlled-measurement', label=kind + '-receipt')

    for index in range(args.samples):
        receipt = steer('pause', 'pause-' + str(index))
        assert receipt['steering']['paused'] and not receipt['execution_started']
    paused = snapshot()
    run('project', 'recover', '--id', 'control', label='recover-original')
    for _ in range(args.samples):
        draft = run('project', 'plan', label='post-steering-plan')
        assert draft['steering']['paused'] and draft['budget'] == paused['budget']
        assert draft['evidence'][0]['receipt_sha256'] == first['receipt']['sha256']
    manifests = list(project.glob('*treatment*.json'))
    manifest = next(p for p in manifests if json.loads(p.read_text(encoding='utf-8')).get('schema') == 1)
    run('project', 'create', '--manifest', str(manifest), label='paused-registration', expect=1)
    held = snapshot()
    assert all(held[k] == paused[k] for k in ('runs', 'receipts', 'budget'))
    received = steer('redirect', 'next-treatment', withdraw=['control'], prefer=['treatment'])
    assert not received['steering']['paused']
    final = run('project', 'advance', label='redirected-first-valid-action')
    assert final['receipt']['run_id'] == 'treatment' and final['receipt']['run_status'] == 'SUCCEEDED'
    after = snapshot()
    assert len(after['receipts']) == 2
    assert next(r for r in after['receipts'] if r['run_id'] == 'control')['sha256'] == first['receipt']['sha256']
    # The noisy six-row example does not meet its 0.01 goal. Check the actual
    # arithmetic independently; a correct completed check is not goal success.
    with (project / 'data.csv').open(newline='', encoding='utf-8') as stream:
        data = [(Fraction(r['x']), Fraction(r['y'])) for r in csv.DictReader(stream)]
    n = len(data)
    sx, sy = sum(x for x, y in data), sum(y for x, y in data)
    slope = (n * sum(x*y for x, y in data) - sx*sy) / (n * sum(x*x for x, y in data) - sx*sx)
    intercept = (sy - slope*sx) / n
    mse = sum((y - slope*x - intercept)**2 for x, y in data) / n
    observed = json.loads((project / 'outputs/treatment.json').read_text())
    assert all(math.isclose(observed[k], float(v), rel_tol=1e-12, abs_tol=1e-12)
               for k, v in [('slope', slope), ('intercept', intercept), ('mse', mse)])
    assert mse > Fraction(1, 100)
    groups = {}
    for label in dict.fromkeys(r['label'] for r in trace):
        rows = [r for r in trace if r['label'] == label]
        times = [r['seconds'] for r in rows]
        groups[label] = {'count': len(rows), 'seconds': times, 'median_seconds': statistics.median(times),
                         'max_seconds': max(times), 'stdout_bytes': [r['stdout_bytes'] for r in rows]}
    protocol_digest = hashlib.sha256(json.dumps(protocol, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()
    report = {'quality': 'PASS', 'protocol_sha256': protocol_digest,
              'protocol_digest_format': 'UTF8_JSON_SORTED_KEYS_COMPACT_UNESCAPED_UNICODE',
              'groups': groups, 'cli_and_setup_processes': len(trace),
              'total_subprocess_wall_seconds': sum(r['seconds'] for r in trace),
              'research_budget': after['budget'], 'original_receipt_sha256': first['receipt']['sha256'],
              'new_receipt_sha256': final['receipt']['sha256'],
              'oracle_mse': str(mse), 'original_goal': 'NOT_MET',
              'model_latency': 'NOT_RUN', 'agent_latency': 'NOT_RUN', 'scientific_gain': 'UNMEASURED'}
    write(work / 'report.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

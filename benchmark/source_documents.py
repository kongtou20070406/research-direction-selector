"""Measure shared result parsing on fixed synthetic inputs; no LLM/science claim.

Run parent first, then candidate with --reuse against the same completed projects.
Timing, profiling and tracemalloc are separate passes. No completed job is rerun.
"""
import argparse
import cProfile
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import tracemalloc


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def measure(call, repeats):
    elapsed, expected = [], None
    for _ in range(repeats):
        started = time.perf_counter()
        result = call()
        elapsed.append(time.perf_counter() - started)
        identity = sha(result)
        if expected is not None and identity != expected:
            raise RuntimeError('Repeated observation changed the returned result')
        expected = identity
    profile = cProfile.Profile()
    profile.runcall(call)
    counts = {}
    for entry in profile.getstats():
        code = entry.code
        if isinstance(code, str) or code.co_name not in ('strict_json', '_parse', '_metadata', '_read', '_read_original'):
            continue
        counts[code.co_name] = counts.get(code.co_name, 0) + entry.callcount
    tracemalloc.start()
    call()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {'seconds': elapsed, 'median_seconds': statistics.median(elapsed),
            'profile_counts': counts, 'peak_traced_bytes_separate_pass': peak,
            'result_sha256': expected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--reuse', action='store_true')
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    source, root = args.source.resolve(), args.workspace.resolve()
    if root.is_relative_to(source) or not 1 <= args.repeats <= 10:
        raise ValueError('Use a sibling workspace and 1..10 timing repeats')
    root.mkdir(parents=True, exist_ok=True)
    if not args.reuse and any(root.iterdir()):
        raise ValueError('New measurements require an empty workspace')
    destination = root / (args.label + '.json')
    transcript = root / (args.label + '-cli.json')
    if destination.exists() or transcript.exists() or Path(args.label).name != args.label:
        raise ValueError('Use a new simple label; never overwrite evidence')
    sys.path.insert(0, str(source / 'scripts'))
    from rds_artifacts import ingest_manifest
    from rds_project import ProjectStore
    from rds_owned_advisor import _state, _collect
    spec = importlib.util.spec_from_file_location('original_owned_fixture', source / 'tests/test_rds_owned_advisor.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    old = 'json.dumps({"score": score, "run_id": run_id})'
    if old not in fixture.SCRIPT:
        raise RuntimeError('Original owned fixture changed; review comparison before running')
    fixture.SCRIPT = fixture.SCRIPT.replace(old, 'json.dumps({"score": score, "run_id": run_id, "payload": list(range(60000))})')
    report = {'scope': 'SYNTHETIC_RESULT_PARSING', 'source': str(source), 'cases': {},
              'model_calls': 'NOT_RUN', 'tokens': 'NOT_MEASURED', 'scientific_gain': 'UNKNOWN'}
    traces = []

    def cli(project, *words):
        started = time.perf_counter()
        result = subprocess.run([sys.executable, '-B', str(source / 'scripts/rds_cli.py'),
            '--root', str(project), *words], capture_output=True, text=True, encoding='utf-8',
            timeout=60, env={**os.environ, 'RDS_USAGE_DB': str(root / 'usage.sqlite3')})
        traces.append({'root': str(project), 'args': words, 'elapsed_seconds': time.perf_counter() - started,
                       'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        transcript.write_text(json.dumps(traces, indent=2), encoding='utf-8')
        if result.returncode:
            raise RuntimeError('Public CLI failed; inspect retained transcript')
        return json.loads(result.stdout)

    for count in (1, 64):
        imported, owned = root / ('import-' + str(count)), root / ('owned-' + str(count))
        if not args.reuse:
            imported.mkdir()
            owned.mkdir()
            raw = json.dumps({'loss': .25, 'payload': list(range(60000))}).encode()
            (imported / 'result.json').write_bytes(raw)
            binding = {'run_id': 'original', 'code_sha256': 'a' * 64, 'config_sha256': 'b' * 64,
                       'data_sha256': 'c' * 64, 'data_split': 'synthetic-development',
                       'metric': {'definition': 'synthetic loss', 'reduction': 'one scalar'}}
            sources = [{'id': 'source-' + str(i), 'path': 'result.json', 'kind': 'metric', 'format': 'json',
                        'expected_sha256': hashlib.sha256(raw).hexdigest(), 'binding': binding,
                        'facts': [{'id': 'loss-' + str(i), 'pointer': '/loss'}]} for i in range(count)]
            (imported / 'manifest.json').write_text(json.dumps({'schema': 'rds-artifact-manifest-v1', 'sources': sources}), encoding='utf-8')
            case = fixture.OwnedAdvisorCLITests(methodName='runTest')
            case.root, case.env = owned, {**os.environ, 'RDS_USAGE_DB': str(root / 'usage.sqlite3')}
            def observations(policy):
                policy['observations'].extend({'fact': 'baseline.extra-' + str(i), 'run_id': 'baseline',
                    'path': 'outputs/baseline.json', 'selector': {'pointer': '/score'}, 'format': 'json'} for i in range(1, count))
            case.initialize(mutate_policy=observations)
            case.create()
            case.execute()
        store = ProjectStore(owned)
        before = store.snapshot()
        with store._db(True) as db:
            state = _state(store, db)
        imported_result = measure(lambda: ingest_manifest(imported / 'manifest.json'), args.repeats)
        owned_result = measure(lambda: _collect(store, state), args.repeats)
        public_import = cli(imported, 'artifacts', 'import', '--manifest', str(imported / 'manifest.json'))
        public_next = cli(owned, 'project', 'next')
        after = store.snapshot()
        if any(before[k] != after[k] for k in ('runs', 'receipts', 'budget')):
            raise RuntimeError('Read-only measurements changed completed work or cost')
        report['cases'][str(count)] = {'import': imported_result, 'owned_collect': owned_result,
            'input_bytes': (imported / 'result.json').stat().st_size,
            'input_sha256': hashlib.sha256((imported / 'result.json').read_bytes()).hexdigest(),
            'public_import_sha256': sha(public_import), 'selected_run': public_next.get('selected_run'),
            'receipt_ids': [r['sha256'] for r in before['receipts']], 'runs': len(before['runs']),
            'budget': before['budget'], 'recovery_preserved': True}
    destination.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

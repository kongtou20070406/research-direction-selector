"""Matched exact blocker timings; --baseline-source must be trusted repository code.

The original analyzer is executed locally, never downloaded. Raw samples, source
hashes, completion differences and a generous-cap semantic check are retained.
"""
import argparse
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import time
import tracemalloc
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_hypergraph as current


def load_baseline(path):
    """Load trusted adjacent historical sources, never the candidate helper."""
    modules, hashes = {}, {}
    for name, source in (("rds_hypergraph_blockers", path.with_name("rds_hypergraph_blockers.py")),
                         ("rds_hypergraph", path)):
        if name == "rds_hypergraph_blockers" and not source.exists():
            modules[name], hashes[name] = None, None
            continue
        raw = source.read_bytes()
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("baseline module exceeds 2 MiB")
        module = types.ModuleType(name)
        module.__file__ = str(source)
        with patch.dict(sys.modules, modules):
            exec(compile(raw, str(source), 'exec'), module.__dict__)
        modules[name] = module
        hashes[name] = hashlib.sha256(raw).hexdigest()
    return modules['rds_hypergraph'], modules, hashes


def graph(names, rules, goal, limits=None):
    return {'schema': 1, 'nodes': [{'id': n, 'status': 'UNKNOWN', 'source': 'synthetic'} for n in names],
            'hyperedges': [{'id': str(i), 'premises': p, 'conclusion': c, 'status': 'SUPPORTED', 'source': 'synthetic'}
                          for i, (p, c) in enumerate(rules)], 'goals': [goal], 'limits': limits or {}}


def chain(length, reverse=False, supported=False):
    names = [str(i) for i in range(length + 1)]
    spec = graph(names, [([names[i]], names[i + 1]) for i in range(length)], names[-1],
                 {'max_nodes': max(256, length + 1), 'max_hyperedges': max(512, length)})
    if supported:
        spec['nodes'][0]['status'] = 'SUPPORTED'
    if reverse:
        spec['hyperedges'].reverse()
    return spec


def choices(count, *, shared=False, dominated=False, cap=128):
    leaves = ['a', 'b'] if shared else [p + str(i) for i in range(count) for p in ('a', 'b')]
    mids = ['m' + str(i) for i in range(count)]
    rules = [([p if shared else p + str(i)], mids[i]) for i in range(count) for p in ('a', 'b')]
    names = leaves + mids + ['goal']
    if dominated:
        names.append('anchor')
        rules.insert(0, (['anchor'], 'goal'))
    rules.append((mids + (['anchor'] if dominated else []), 'goal'))
    return graph(names, rules, 'goal', {'max_blocker_sets': cap})


def semantic(result):
    return {k: v for k, v in result.items() if k not in ('combinations_examined', 'limits')}


def summary(result):
    return {'truncated': result['truncated'], 'reason': result['truncation_reason'],
            'combinations': result['combinations_examined'],
            'blocker_count': sum(len(g['minimal_missing_evidence_sets']) for g in result['goals'].values())}


def measure(before, after, spec, repeats, *, baseline_modules=None):
    def scope(side):
        return patch.dict(sys.modules, baseline_modules or {}) if side == 0 else nullcontext()

    expected = []
    for side, fn in enumerate((before, after)):
        with scope(side):
            expected.append(fn(spec))
    # A cap stops work, not the mathematical problem. Check a common larger cap
    # separately when the original and optimized paths exhaust different work.
    check_spec = deepcopy(spec)
    check_spec.setdefault('limits', {}).update(max_blocker_sets=2048, max_combinations=1000000)
    checked = []
    for side, fn in enumerate((before, after)):
        with scope(side):
            checked.append(fn(check_spec))
    if any(r['truncated'] for r in checked) or semantic(checked[0]) != semantic(checked[1]):
        raise AssertionError('Complete reference results differ or are unresolved')
    samples = [[], []]
    for iteration in range(repeats):
        for side in (iteration % 2, 1 - iteration % 2):
            with scope(side):
                start = time.perf_counter()
                actual = (before, after)[side](spec)
                samples[side].append((time.perf_counter() - start) * 1000)
            if actual != expected[side]:
                raise AssertionError('Repeated full output changed')
    rows = []
    for side, fn in enumerate((before, after)):
        with scope(side):
            tracemalloc.start()
            try:
                fn(spec)
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
        rows.append({**summary(expected[side]), 'median_ms': statistics.median(samples[side]),
                     'min_ms': min(samples[side]), 'max_ms': max(samples[side]),
                     'samples_ms': samples[side], 'python_peak_bytes_separate_call': peak})
    both_complete = not any(r['truncated'] for r in expected)
    return {'nodes': len(spec['nodes']), 'edges': len(spec['hyperedges']), 'limits': expected[0]['limits'],
            'baseline': rows[0], 'candidate': rows[1], 'complete_reference_parity': True,
            'same_cap_semantic_parity': semantic(expected[0]) == semantic(expected[1]),
            'complete_result_speedup': rows[0]['median_ms'] / rows[1]['median_ms'] if both_complete else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-source', required=True, type=Path)
    parser.add_argument('--repeats', type=int, default=9)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 30:
        parser.error('repeats must be 1..30')
    baseline, baseline_modules, source_hashes = load_baseline(args.baseline_source)
    sparse = choices(3)
    for i in range(200):
        sparse['nodes'].append({'id': 'unused' + str(i), 'status': 'UNKNOWN', 'source': 'synthetic'})
        sparse['hyperedges'].append({'id': 'unused' + str(i), 'premises': ['unused' + str((i + 1) % 200)],
                                    'conclusion': 'unused' + str(i), 'status': 'PROPOSED', 'source': 'synthetic'})
    workloads = {'public_7': json.loads((ROOT / 'examples/goal-linked-hypergraph.json').read_text()),
                 'supported_forward_513': chain(512, supported=True),
                 'unknown_forward_201': chain(200), 'unknown_reverse_201': chain(200, True),
                 'unknown_reverse_257': chain(256, True), 'shared_20_premises': choices(20, shared=True),
                 'head_dominated_256_combinations': choices(8, dominated=True),
                 'independent_128': choices(7), 'independent_256_default_cap': choices(8),
                 'independent_256_explicit_cap': choices(8, cap=256), 'mostly_irrelevant': sparse}
    result = {'environment': {'python': platform.python_version(), 'platform': platform.platform()},
              'baseline_sha256': source_hashes['rds_hypergraph'],
              'baseline_source_sha256': source_hashes,
              'candidate_sha256': {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
                                   for name in ('rds_hypergraph.py', 'rds_hypergraph_blockers.py')},
              'repeats': args.repeats, 'workloads': {},
              'assurance': 'LOCAL_SYNTHETIC_ENGINEERING_NOT_LLM_OR_SCIENTIFIC_GAIN'}
    for name, spec in workloads.items():
        row = measure(baseline.analyze_hypergraph, current.analyze_hypergraph, spec, args.repeats,
                      baseline_modules=baseline_modules)
        result['workloads'][name] = row
        print(name, json.dumps({side: {key: row[side][key] for key in ('median_ms', 'combinations', 'truncated')}
                                for side in ('baseline', 'candidate')}), flush=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

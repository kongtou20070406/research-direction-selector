"""Performance samples must reject a changed result, without timing assertions."""
from pathlib import Path
import hashlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark import dependency_performance as dependency
from benchmark import hypergraph_blockers as blockers
from benchmark.dependency_performance import paired


HELPER = b"def missing_families(): return 'historical'\n"
ANALYZER = b"""from rds_hypergraph_blockers import missing_families
def analyze_hypergraph(spec):
    import rds_hypergraph_blockers as lazy
    assert lazy.missing_families() == missing_families()
    return {'truncated': False, 'truncation_reason': None, 'combinations_examined': 1,
            'goals': {}, 'limits': {}, 'value': missing_families()}
"""


class DependencyPerformanceTests(unittest.TestCase):
    def baseline(self, *, helper=HELPER, analyzer=ANALYZER):
        sources = {name: b'' for name in ('rds_advisor_search', 'rds_experiments', 'rds_advisor')}
        sources['rds_hypergraph'] = analyzer
        if helper is not None:
            sources['rds_hypergraph_blockers'] = helper

        def git(argv, **kwargs):
            if argv[1] == 'ls-tree':
                return b'scripts/rds_hypergraph_blockers.py\n' if helper is not None else b''
            self.assertEqual(argv[:2], ['git', 'show'])
            return sources[Path(argv[2].split(':', 1)[1]).stem]

        with patch.object(dependency.subprocess, 'check_output', side_effect=git):
            return dependency.load_baseline('a' * 40)

    def test_commit_baseline_binds_historical_helper_and_restores_candidate(self):
        candidate = sys.modules['rds_hypergraph_blockers']
        modules, hashes = self.baseline()
        old = lambda: modules['rds_hypergraph'].analyze_hypergraph({})
        expected = {'truncated': False, 'truncation_reason': None, 'combinations_examined': 1,
                    'goals': {}, 'limits': {}, 'value': 'historical'}

        def current():
            self.assertIs(sys.modules['rds_hypergraph_blockers'], candidate)
            return expected

        paired(old, current, modules, repeats=2, inner=1)
        self.assertEqual(hashes['rds_hypergraph_blockers'], hashlib.sha256(HELPER).hexdigest())
        self.assertEqual(dependency.analysis_calls(old, modules['rds_hypergraph'],
                         patch.dict(sys.modules, modules)), 1)
        changed, changed_hashes = self.baseline(helper=HELPER.replace(b'historical', b'changed'))
        with patch.dict(sys.modules, changed):
            self.assertEqual(changed['rds_hypergraph'].analyze_hypergraph({})['value'], 'changed')
        self.assertNotEqual(hashes['rds_hypergraph_blockers'], changed_hashes['rds_hypergraph_blockers'])
        self.assertIs(sys.modules['rds_hypergraph_blockers'], candidate)

    def test_old_commit_without_helper_and_missing_required_helper(self):
        candidate = sys.modules['rds_hypergraph_blockers']
        modules, hashes = self.baseline(helper=None, analyzer=b'def analyze_hypergraph(spec): return 7\n')
        with patch.dict(sys.modules, modules):
            self.assertEqual(modules['rds_hypergraph'].analyze_hypergraph({}), 7)
            self.assertIsNone(sys.modules['rds_hypergraph_blockers'])
        self.assertIsNone(hashes['rds_hypergraph_blockers'])
        with self.assertRaises(ImportError):
            self.baseline(helper=None)
        self.assertIs(sys.modules['rds_hypergraph_blockers'], candidate)

    def test_file_baseline_isolates_helper_in_every_measurement_phase(self):
        candidate = sys.modules['rds_hypergraph_blockers']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rds_hypergraph.py'
            path.write_bytes(ANALYZER)
            path.with_name('rds_hypergraph_blockers.py').write_bytes(HELPER)
            baseline, modules, hashes = blockers.load_baseline(path)
            calls = []

            def current(spec):
                self.assertIs(sys.modules['rds_hypergraph_blockers'], candidate)
                calls.append(spec)
                return {'truncated': False, 'truncation_reason': None, 'combinations_examined': 1,
                        'goals': {}, 'limits': {}, 'value': 'historical'}

            row = blockers.measure(baseline.analyze_hypergraph, current, {'nodes': [], 'hyperedges': []},
                                   repeats=2, baseline_modules=modules)
            self.assertTrue(row['complete_reference_parity'])
            self.assertEqual(len(calls), 5)  # warmup, parity, two timings, allocation sample
            self.assertEqual(hashes['rds_hypergraph_blockers'], hashlib.sha256(HELPER).hexdigest())
            with self.assertRaisesRegex(RuntimeError, 'failure'):
                blockers.measure(lambda spec: (_ for _ in ()).throw(RuntimeError('failure')),
                                 current, {}, repeats=1, baseline_modules=modules)
            self.assertIs(sys.modules['rds_hypergraph_blockers'], candidate)

    def test_file_baseline_never_falls_back_to_candidate_helper(self):
        candidate = sys.modules['rds_hypergraph_blockers']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rds_hypergraph.py'
            path.write_bytes(b'def analyze_hypergraph(spec): return 7\n')
            baseline, modules, hashes = blockers.load_baseline(path)
            with patch.dict(sys.modules, modules):
                self.assertEqual(baseline.analyze_hypergraph({}), 7)
                self.assertIsNone(sys.modules['rds_hypergraph_blockers'])
            self.assertIsNone(hashes['rds_hypergraph_blockers'])
            path.write_bytes(ANALYZER)
            with self.assertRaises(ImportError):
                blockers.load_baseline(path)
            self.assertIs(sys.modules['rds_hypergraph_blockers'], candidate)

    def test_changed_output_after_warmup_cannot_report_parity(self):
        values = iter(({"value": 0}, {"value": 1}))
        with self.assertRaisesRegex(AssertionError, "sample output"):
            paired(lambda: {"value": 0}, lambda: next(values), {}, repeats=1, inner=1)


if __name__ == "__main__":
    unittest.main()

"""Real regular-file boundaries in replay, graph viewer and blind manifest CLI."""
from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_hypergraph_view as viewer
import rds_blind_runner as blind
from rds_bounded_io import read_regular_bytes
spec = importlib.util.spec_from_file_location('regular_input_replay', ROOT / 'benchmark/result-methods/replay.py')
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)
RECORDS = ROOT / 'benchmark/result-methods/results/20261010'


class RegularInputsTests(unittest.TestCase):
    def test_exact_limit_and_file_growth_use_original_descriptor(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'input'
            target.write_bytes(b'abc')
            self.assertEqual(read_regular_bytes(target, 3), b'abc')
            real_stat = os.fstat
            def grow(fd):
                original = real_stat(fd)
                with target.open('ab') as stream:
                    stream.write(b'd')
                return original
            with patch.object(os, 'fstat', side_effect=grow):
                with self.assertRaisesRegex(ValueError, 'byte bound'):
                    read_regular_bytes(target, 3)
            self.assertEqual(target.read_bytes(), b'abcd')

    def test_actual_original_replay_and_viewer_still_pass(self):
        before = {name: (RECORDS / name).read_bytes() for name in replay.FILES}
        result = replay.replay(RECORDS)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['training_executions'], 0)
        self.assertEqual(result['decision_requirements']['status'], 'FAIL')
        self.assertEqual({name: (RECORDS / name).read_bytes() for name in replay.FILES}, before)
        graph = viewer.read_graph(ROOT, ROOT / 'examples/hypergraph-view.json')
        self.assertEqual(graph['status'], 'AVAILABLE')

    def test_viewer_device_refuses_regular_file_before_json(self):
        result = viewer.read_graph(ROOT, os.devnull)
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertIn('regular file', result['reason'])

    def test_blind_manifest_device_refuses_before_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()) as output:
            target = Path(temporary) / 'uncreated-output'
            with patch.object(blind, 'run_blind', side_effect=AssertionError('no dispatch')) as dispatch:
                self.assertEqual(blind.main(['--manifest', os.devnull, '--output-dir', str(target)]), 2)
            dispatch.assert_not_called()
            self.assertIn('regular file', output.getvalue())
            self.assertFalse(target.exists())

    def test_replay_source_size_refuses_before_unbounded_read_or_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'records'
            shutil.copytree(RECORDS, root)
            target = root / 'source-benchmark-run.py.txt'
            with target.open('wb') as stream:
                stream.truncate(2 * 1024 * 1024 + 1)
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('unbounded file allocation')), \
                    patch.object(replay, '_document', side_effect=AssertionError('no JSON parse')) as parser:
                with self.assertRaisesRegex(ValueError, 'byte|limit|bound'):
                    replay.replay(root)
            parser.assert_not_called()
            self.assertEqual(target.stat().st_size, 2 * 1024 * 1024 + 1)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO unavailable')
    def test_actual_fifo_entrypoints_refuse_without_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fifo = root / 'unwritten'
            os.mkfifo(fifo)
            viewer_code = "import sys;sys.path.insert(0,sys.argv[1]);import rds_hypergraph_view as v;r=v.read_graph(sys.argv[1],sys.argv[2]);assert r['status']=='UNAVAILABLE' and 'regular file' in r['reason'],r"
            graph = subprocess.run([sys.executable, '-B', '-c', viewer_code, str(ROOT / 'scripts'), str(fifo)], capture_output=True, text=True, timeout=3)
            self.assertEqual(graph.returncode, 0, graph.stdout + graph.stderr)
            blind_result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_blind_runner.py'), '--manifest', str(fifo), '--output-dir', str(root / 'output')], capture_output=True, text=True, timeout=3)
            self.assertEqual(blind_result.returncode, 2, blind_result.stdout + blind_result.stderr)
            self.assertIn('regular file', blind_result.stdout)
            self.assertFalse((root / 'output').exists())
            records = root / 'records'
            shutil.copytree(RECORDS, records)
            (records / 'evaluator.txt').unlink()
            os.mkfifo(records / 'evaluator.txt')
            result = subprocess.run([sys.executable, '-B', str(ROOT / 'benchmark/result-methods/replay.py'), '--records', str(records), '--output', str(root / 'replay-output.json')], capture_output=True, text=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('regular file', result.stderr)
            self.assertFalse((root / 'replay-output.json').exists())


if __name__ == '__main__':
    unittest.main()

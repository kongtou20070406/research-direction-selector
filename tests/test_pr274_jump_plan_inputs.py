"""Frozen Jump plans refuse non-regular inputs before hash or JSON work."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_jump

spec = importlib.util.spec_from_file_location('jump_plan_example', ROOT / 'examples/jump-generation/run.py')
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class JumpPlanInputTests(unittest.TestCase):
    def test_device_refuses_before_json_without_changing_ledger(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, store = example.build(Path(temporary) / 'project')
            state = store.snapshot()
            with patch.object(store, '_path', return_value=Path(os.devnull).absolute()), \
                    patch.object(rds_jump, 'strict_json', side_effect=AssertionError('no JSON')) as decode:
                with self.assertRaisesRegex(ValueError, 'Jump plan must be a regular file'):
                    rds_jump.load_plan(store, state)
            decode.assert_not_called()
            self.assertEqual(store.snapshot(), state)

    def test_exact_limit_valid_plan_and_oversize_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, store = example.build(Path(temporary) / 'project')
            state = store.snapshot()
            target = root / rds_jump.POLICY_PATH
            original = target.read_bytes()
            raw = original + b' ' * (32768 - len(original))
            target.write_bytes(raw)
            binding = next(b for b in state['contract']['bindings'] if b['path'] == rds_jump.POLICY_PATH)
            binding['sha256'] = hashlib.sha256(raw).hexdigest()
            self.assertEqual(len(rds_jump.load_plan(store, state)['stages']), 3)
            target.write_bytes(raw + b' ')
            with patch.object(rds_jump, 'strict_json', side_effect=AssertionError('no JSON')) as decode:
                with self.assertRaisesRegex(ValueError, 'Jump plan exceeds 32 KiB'):
                    rds_jump.load_plan(store, state)
            decode.assert_not_called()
            self.assertEqual(target.read_bytes(), raw + b' ')

    def test_changed_frozen_bytes_refuse_before_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, store = example.build(Path(temporary) / 'project')
            state = store.snapshot()
            target = root / rds_jump.POLICY_PATH
            target.write_bytes(target.read_bytes() + b' ')
            with patch.object(rds_jump, 'strict_json', side_effect=AssertionError('no JSON')) as decode:
                with self.assertRaisesRegex(ValueError, 'Jump plan binding changed'):
                    rds_jump.load_plan(store, state)
            decode.assert_not_called()

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO unavailable')
    def test_bound_plan_fifo_without_writer_refuses_within_three_seconds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, store = example.build(Path(temporary) / 'project')
            target = root / rds_jump.POLICY_PATH
            target.unlink()
            os.mkfifo(target)
            code = "import sys;sys.path.insert(0,sys.argv[1]);from rds_project import ProjectStore;import rds_jump; s=ProjectStore(sys.argv[2]);\ntry:rds_jump.load_plan(s,s.snapshot())\nexcept ValueError as e:assert 'regular file' in str(e),str(e)\nelse:raise AssertionError('FIFO accepted')"
            result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'scripts'), str(root)],
                                    capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()

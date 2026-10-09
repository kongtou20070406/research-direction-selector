"""Freeze admission controls without dispatching a module or generator."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_jump_interpreter_boundary as fixtures

jump = fixtures.jump


class PythonModuleBoundaryTests(unittest.TestCase):
    def build(self, directory, options, *, legacy=False, frozen_worker=False,
              literal_main=None):
        def change(store, contract, plan):
            if not frozen_worker:
                (store.root / 'helper.py').write_text('pass\n', encoding='utf-8')
                binding = next(b for b in contract['bindings'] if b['path'] == 'worker.py')
                binding.update(path='helper.py', sha256=fixtures.file_sha(store.root / 'helper.py'))
                plan['generator_code_paths'][0] = 'helper.py'
            if literal_main is not None:
                (store.root / literal_main).write_bytes((store.root / 'worker.py').read_bytes())
                contract['bindings'].append({'path': literal_main, 'role': 'code',
                                            'sha256': fixtures.file_sha(store.root / literal_main)})
                plan['generator_code_paths'].append(literal_main)
            if legacy:
                plan.pop('generator_code_paths')
            for stage in plan['stages']:
                command = next(a for a in contract['allowed_commands'] if a == stage['run']['argv'])
                command[:] = [sys.executable, *options, *command[3:]]
                stage['run']['argv'] = command
        return fixtures.InterpreterBoundaryTests().freeze(Path(directory) / 'project', change)

    def refuse(self, options, *, legacy=False, frozen_worker=False):
        with self.subTest(options=options, legacy=legacy, frozen_worker=frozen_worker), tempfile.TemporaryDirectory() as directory:
            root, store = self.build(directory, options, legacy=legacy, frozen_worker=frozen_worker)
            if not frozen_worker:
                # The original defect admits this changed, unbound actual module.
                (root / 'worker.py').write_text('raise AssertionError("module executed")\n', encoding='utf-8')
            before = store.snapshot()
            plan = fixtures.json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
            with patch.object(store, 'execute', side_effect=AssertionError('dispatch forbidden')), \
                    patch.object(store, 'register', side_effect=AssertionError('registration forbidden')):
                with self.assertRaisesRegex(ValueError, 'Python module execution'):
                    jump.load_plan(store, before)
                result = jump.prepare_owned(store, plan['stages'][0]['run'])
            self.assertEqual(result['status'], 'JUMP_UNAVAILABLE')
            self.assertIn('Python module execution', result['diagnostic'])
            self.assertFalse(result['changed'])
            self.assertFalse(result['execution_started'])
            self.assertFalse(result['retry_authorized'])
            self.assertEqual(store.snapshot(), before)
            self.assertEqual(before['runs'], [])
            self.assertEqual(before['receipts'], [])

    def test_separated_attached_and_clustered_module_modes_refuse_before_dispatch(self):
        for legacy in (False, True):
            for options in (['-m', 'worker'], ['-mworker'], ['-Bmworker']):
                self.refuse(options, legacy=legacy)

    def test_module_mode_after_isolation_and_consumed_options_refuses(self):
        for options in (['-I', '-m', 'worker'], ['-S', '-B', '-mworker'],
                        ['-W', 'ignore', '-m', 'worker'], ['-X', 'utf8', '-Bmworker'],
                        ['-BWignore', '-mworker'], ['--check-hash-based-pycs', 'always', '-m', 'worker']):
            self.refuse(options)

    def test_frozen_module_file_still_requires_explicit_file_entrypoint(self):
        self.refuse(['-m', 'worker'], frozen_worker=True)

    def test_option_values_literal_main_and_postscript_module_arguments_stay_admissible(self):
        controls = [(['-W', '-m', 'worker.py'], None),
                    (['-X', '-m', 'worker.py'], None),
                    (['-W-m', 'worker.py'], None),
                    (['-BX-m', 'worker.py'], None),
                    (['-B', 'worker.py', '-m', 'worker'], None),
                    (['--', '-m'], '-m')]
        for options, literal in controls:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                root, store = self.build(directory, options, frozen_worker=True, literal_main=literal)
                before = store.snapshot()
                with patch.object(store, 'execute', side_effect=AssertionError('dispatch forbidden')), \
                        patch.object(store, 'register', side_effect=AssertionError('registration forbidden')):
                    plan = jump.load_plan(store, before)
                self.assertEqual(len(plan['stages']), 3)
                self.assertEqual(store.snapshot(), before)
        # Inline code and stdin retain their existing static admission semantics.
        self.assertIsNone(jump._python_script_operand(['python', '-c', 'print(1)', '-m']))
        self.assertIsNone(jump._python_script_operand(['python', '-', '-m']))


if __name__ == '__main__':
    unittest.main()

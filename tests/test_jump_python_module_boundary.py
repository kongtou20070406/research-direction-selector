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

    def test_noop_t_cannot_hide_module_mode_in_frozen_explicit_or_legacy_plan(self):
        for legacy in (False, True):
            for options in (['-t', '-m', 'worker'], ['-tmworker'], ['-Btmworker']):
                self.refuse(options, legacy=legacy)

    def test_noop_t_preserves_explicit_file_identity_and_unbound_refusal(self):
        for options in (['-t', 'worker.py'], ['-Bt', 'worker.py'], ['-tB', 'worker.py']):
            for frozen_worker in (False, True):
                with self.subTest(options=options, frozen_worker=frozen_worker), tempfile.TemporaryDirectory() as directory:
                    root, store = self.build(directory, options, frozen_worker=frozen_worker)
                    before = store.snapshot()
                    plan = fixtures.json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
                    with patch.object(fixtures.ProjectStore, 'execute', side_effect=AssertionError('dispatch forbidden')), \
                            patch.object(fixtures.ProjectStore, 'register', side_effect=AssertionError('registration forbidden')):
                        if frozen_worker:
                            loaded = jump.load_plan(store, before)
                            self.assertEqual(len(loaded['stages']), 3)
                        else:
                            with self.assertRaisesRegex(ValueError, 'entrypoint.*frozen code'):
                                jump.load_plan(store, before)
                            result = jump.prepare_owned(store, plan['stages'][0]['run'])
                            self.assertEqual(result['status'], 'JUMP_UNAVAILABLE')
                            self.assertIn('frozen code', result['diagnostic'])
                            self.assertFalse(result['changed'])
                            self.assertFalse(result['execution_started'])
                    self.assertEqual(store.snapshot(), before)

    def test_all_cpython_continuing_short_flags_preserve_later_module_refusal(self):
        # CPython 3.11/3.13 flags that continue; interactive i is refused.
        for flag in 'bBdEIOPqRsStuvx':
            for options in ([f'-{flag}', '-m', 'worker'], [f'-{flag}mworker']):
                with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'Python module execution'):
                    jump._python_script_operand(['python', *options])
            self.assertEqual(jump._python_script_operand(['python', f'-{flag}', 'worker.py']), 2)
        for options, index in ((['-tW', '-m', 'worker.py'], 3),
                               (['-tX', '-m', 'worker.py'], 3),
                               (['-tW-m', 'worker.py'], 2),
                               (['-tX-m', 'worker.py'], 2),
                               (['-t', '--', '-m'], 3),
                               (['-t', 'worker.py', '-m', 'worker'], 2)):
            self.assertEqual(jump._python_script_operand(['python', *options]), index)
        self.assertIsNone(jump._python_script_operand(['python', '-tc', 'pass', '-m']))

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

    def test_unknown_options_fail_closed_in_frozen_explicit_and_legacy_plans(self):
        for legacy in (False, True):
            for options in (['-J', 'worker.py'], ['-BU', 'worker.py'],
                            ['--future-option', 'worker.py']):
                with self.subTest(options=options, legacy=legacy), tempfile.TemporaryDirectory() as directory:
                    root, store = self.build(directory, options, legacy=legacy)
                    before = store.snapshot()
                    plan = fixtures.json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
                    with patch.object(fixtures.ProjectStore, 'execute', side_effect=AssertionError('dispatch forbidden')), \
                            patch.object(fixtures.ProjectStore, 'register', side_effect=AssertionError('registration forbidden')):
                        with self.assertRaisesRegex(ValueError, 'Unsupported Python option'):
                            jump.load_plan(store, before)
                        result = jump.prepare_owned(store, plan['stages'][0]['run'])
                    self.assertEqual(result['status'], 'JUMP_UNAVAILABLE')
                    self.assertFalse(result['changed'])
                    self.assertFalse(result['execution_started'])
                    self.assertFalse(result['retry_authorized'])
                    self.assertEqual(store.snapshot(), before)

    def test_known_stopping_options_and_consumed_unknown_looking_values(self):
        for option in ('-h', '-?', '-V', '-VV', '--help', '--help-env',
                       '--help-xoptions', '--help-all', '--version'):
            self.assertIsNone(jump._python_script_operand(['python', option, 'worker.py']))
        for options, index in ((['-W', '-J', 'worker.py'], 3), (['-X', '--future-option', 'worker.py'], 3),
                               (['-BW-J', 'worker.py'], 2), (['--', '-J'], 2),
                               (['worker.py', '--future-option'], 1)):
            self.assertEqual(jump._python_script_operand(['python', *options]), index)

    def test_effective_interactive_flags_refuse_before_registration_or_dispatch(self):
        for legacy in (False, True):
            for options in (['-i', 'worker.py'], ['-Bi', 'worker.py'], ['-ti', 'worker.py'],
                            ['-Ii', 'worker.py'], ['-Si', 'worker.py'], ['-iB', 'worker.py']):
                with self.subTest(options=options, legacy=legacy), tempfile.TemporaryDirectory() as directory:
                    root, store = self.build(directory, options, legacy=legacy, frozen_worker=True)
                    (root / 'readline.py').write_text('raise AssertionError("hook executed")\n', encoding='utf-8')
                    before = store.snapshot()
                    plan = fixtures.json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
                    with patch.object(fixtures.ProjectStore, 'execute', side_effect=AssertionError('dispatch forbidden')), \
                            patch.object(fixtures.ProjectStore, 'register', side_effect=AssertionError('registration forbidden')):
                        with self.assertRaisesRegex(ValueError, 'Python interactive startup'):
                            jump.load_plan(store, before)
                        result = jump.prepare_owned(store, plan['stages'][0]['run'])
                    self.assertEqual(result['status'], 'JUMP_UNAVAILABLE')
                    self.assertFalse(result['changed'])
                    self.assertFalse(result['execution_started'])
                    self.assertFalse(result['retry_authorized'])
                    self.assertEqual(store.snapshot(), before)

    def test_interactive_looking_values_literal_files_and_postscript_remain_admissible(self):
        controls = [(['-W', '-i', 'worker.py'], None), (['-X', '-i', 'worker.py'], None),
                    (['-BW-i', 'worker.py'], None), (['-BX-i', 'worker.py'], None),
                    (['--', '-i'], '-i'), (['worker.py', '-i'], None)]
        for options, main in controls:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                root, store = self.build(directory, options, frozen_worker=True, literal_main=main)
                before = store.snapshot()
                with patch.object(fixtures.ProjectStore, 'execute', side_effect=AssertionError('dispatch forbidden')), \
                        patch.object(fixtures.ProjectStore, 'register', side_effect=AssertionError('registration forbidden')):
                    self.assertEqual(len(jump.load_plan(store, before)['stages']), 3)
                self.assertEqual(store.snapshot(), before)
        self.assertIsNone(jump._python_script_operand(['python', '-ci']))
        self.assertIsNone(jump._python_script_operand(['python', '-', '-i']))


if __name__ == '__main__':
    unittest.main()

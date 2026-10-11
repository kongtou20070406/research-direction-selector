"""Actual frozen plan admission with native state, without executing R."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_jump_interpreter_boundary as fixtures

jump = fixtures.jump


class RscriptStartupBoundaryTests(unittest.TestCase):
    def build(self, directory, options, *, legacy=False, main='worker.R', bound=True):
        def change(store, contract, plan):
            (store.root / main).write_text('stop("R execution forbidden")\n', encoding='utf-8')
            (store.root / '.Rprofile').write_text('stop("profile execution forbidden")\n', encoding='utf-8')
            (store.root / '.Renviron').write_text('R_PROFILE_USER=mutable.R\n', encoding='utf-8')
            binding = next(b for b in contract['bindings'] if b['path'] == 'worker.py')
            if bound:
                binding.update(path=main, sha256=fixtures.file_sha(store.root / main))
                plan['generator_code_paths'][0] = main
            else:
                (store.root / 'helper.R').write_text('# frozen helper\n', encoding='utf-8')
                binding.update(path='helper.R', sha256=fixtures.file_sha(store.root / 'helper.R'))
                plan['generator_code_paths'][0] = 'helper.R'
            if legacy:
                plan.pop('generator_code_paths')
            for stage in plan['stages']:
                command = next(a for a in contract['allowed_commands'] if a == stage['run']['argv'])
                command[:] = ['Rscript', *options, *command[3:]]
                stage['run']['argv'] = command
        return fixtures.InterpreterBoundaryTests().freeze(Path(directory) / 'project', change)

    def admission(self, options, *, legacy=False, main='worker.R', bound=True, refusal=None):
        with self.subTest(options=options, legacy=legacy, main=main, bound=bound), tempfile.TemporaryDirectory() as directory:
            # Mock only resolved interpreter metadata. No R runtime is required
            # or launched; contract, binding, SQLite and budget state are real.
            resolved = str(Path(directory) / 'Rscript.exe')
            with patch.object(fixtures.ProjectStore, '_command', return_value=resolved):
                root, store = self.build(directory, options, legacy=legacy, main=main, bound=bound)
                (root / '.Rprofile').write_text('stop("changed unbound profile")\n', encoding='utf-8')
                before = store.snapshot()
                plan = fixtures.json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
                with patch.object(fixtures.ProjectStore, 'execute', side_effect=AssertionError('dispatch forbidden')), \
                        patch.object(fixtures.ProjectStore, 'register', side_effect=AssertionError('registration forbidden')):
                    if refusal is None:
                        self.assertEqual(len(jump.load_plan(store, before)['stages']), 3)
                    else:
                        with self.assertRaisesRegex(ValueError, refusal):
                            jump.load_plan(store, before)
                        result = jump.prepare_owned(store, plan['stages'][0]['run'])
                        self.assertEqual(result['status'], 'JUMP_UNAVAILABLE')
                        self.assertRegex(result['diagnostic'], refusal)
                        self.assertFalse(result['changed'])
                        self.assertFalse(result['execution_started'])
                        self.assertFalse(result['retry_authorized'])
                self.assertEqual(store.snapshot(), before)
                self.assertEqual(before['runs'], [])
                self.assertEqual(before['receipts'], [])

    def test_bare_partial_and_postscript_vanilla_refuse_before_dispatch(self):
        for legacy in (False, True):
            for options in (['worker.R'], ['--no-init-file', 'worker.R'],
                            ['--no-site-file', '--no-environ', 'worker.R'],
                            ['worker.R', '--vanilla']):
                self.admission(options, legacy=legacy, refusal='Rscript startup requires')

    def test_effective_vanilla_preserves_frozen_main_and_postscript_arguments(self):
        for legacy in (False, True):
            for options in (['--vanilla', 'worker.R'],
                            ['--verbose', '--vanilla', '--no-init-file', 'worker.R', '--restore'],
                            ['--vanilla', 'worker.R', '--vanilla', '-e', '--future-option']):
                self.admission(options, legacy=legacy)

    def test_isolation_does_not_replace_exact_code_binding_or_literal_main_identity(self):
        for legacy in (False, True):
            self.admission(['--vanilla', 'worker.R'], legacy=legacy, bound=False,
                           refusal='entrypoint.*frozen code')
            for main in ('-literal', '-eopaque'):
                self.admission(['--vanilla', main], legacy=legacy, main=main)
                self.admission(['--vanilla', main], legacy=legacy, main=main, bound=False,
                               refusal='entrypoint.*frozen code')

    def test_unsupported_or_reenabled_startup_options_fail_closed(self):
        for options in (['--vanilla=1', 'worker.R'], ['--vanilla', '--restore', 'worker.R'],
                        ['--vanilla', '--default-packages=custom', 'worker.R'],
                        ['--help', 'worker.R'], ['--version', 'worker.R'],
                        ['--vanilla', '--', 'worker.R'], ['--vanilla --restore', 'worker.R']):
            self.admission(options, refusal='Unsupported Rscript startup option')

    def test_inline_expression_boundary_cannot_supply_late_vanilla(self):
        for argv in (['Rscript', '-e', '--vanilla'], ['Rscript', '-e', '1', '--vanilla']):
            with self.assertRaisesRegex(ValueError, 'Rscript startup requires'):
                jump._interpreter_script_operand(argv)
        self.assertIsNone(jump._interpreter_script_operand(['Rscript', '--vanilla', '-e', '1', '--restore']))
        self.assertIsNone(jump._interpreter_script_operand(['Rscript', '--help']))
        self.assertIsNone(jump._interpreter_script_operand(['Rscript', '--version']))


if __name__ == '__main__':
    unittest.main()

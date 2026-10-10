"""Scoped startup-selector admission; resolver metadata is not worker execution."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from test_jump_interpreter_boundary import example
import rds_jump as jump
import rds_structure as structure
from rds_project import ProjectStore, file_sha


class StartupControlsTests(unittest.TestCase):
    def test_bash_login_and_interactive_modes_cannot_bind_frozen_main(self):
        for options in (['--login'], ['--login=force'], ['-l'], ['-i'], ['-il'], ['-li'], ['-xil']):
            with self.subTest(options=options), self.assertRaisesRegex(
                    ValueError, 'Bash login or interactive startup'):
                jump._interpreter_script_operand(['bash', *options, 'worker.sh'])
        self.assertEqual(jump._interpreter_script_operand(['bash', 'worker.sh']), (1, 'worker.sh'))
        self.assertEqual(jump._interpreter_script_operand(['bash', '--noprofile', 'worker.sh']), (2, 'worker.sh'))
        self.assertEqual(jump._interpreter_script_operand(['bash', '-o', '-i', 'worker.sh']), (3, 'worker.sh'))
        self.assertEqual(jump._interpreter_script_operand(['bash', '+o', '-l', 'worker.sh']), (3, 'worker.sh'))
        self.assertEqual(jump._interpreter_script_operand(['bash', '--', '-l']), (2, '-l'))
        self.assertEqual(jump._interpreter_script_operand(['bash', 'worker.sh', '--login', '-i']), (1, 'worker.sh'))
        self.assertIsNone(jump._interpreter_script_operand(['bash', '-ilc', 'inline']))

    def test_effective_startup_selectors_and_inline_shortcuts_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            before = store.snapshot()
            original = json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
            cases = {
                'ruby': [['-e', '0', '-r', 'Hook'], ['-e0', '-rHook'],
                         ['-e', '0', '-e0', '-rHook']],
                'perl': [['-I', str(root.parent), '-MHook'], ['-m', 'Hook'], ['-wmHook'],
                         ['-m', 'Shelper'], ['-mShelper'], ['-M', 'Shelper'], ['-MShelper'],
                         ['-d:Shelper'], ['-dt:Shelper'], ['-d:Module=argument with space'],
                         ['-d'], ['-dt'], ['-d:Hook'], ['-dt:Hook'], ['-d=Hook'],
                         ['-we0', '-MHook'], ['-E0', '-d:Hook'], ['-V:S', '-MHook']],
                'julia': [['-L', str(root.parent / 'hook.jl')], ['-L./hook.opaque'],
                          ['--load', './hook.opaque'], ['--load=./hook.opaque'],
                          ['-J', './hook.opaque'], ['-J./hook.opaque'],
                          ['--sysimage=./hook.opaque'], ['--sysimage', './hook.opaque'],
                          ['-mHook'], ['--module', 'Hook'],
                          ['-e', '0', '-L./hook.opaque'], ['--eval=0', '-J./hook.opaque']],
                'lua': [['-l', 'Hook'], ['-lHook'], ['-l', 'g=Hook'],
                        ['-e', 'a=1', '-lHook'], ['-ea=1', '-l', 'Hook']],
            }
            for language, options_list in cases.items():
                worker = root / ('worker.' + {'ruby':'rb', 'perl':'pl', 'julia':'jl', 'lua':'lua'}[language])
                worker.write_text('frozen main', encoding='utf-8')
                hook = root / 'hook.opaque'
                hook.write_text('frozen explicit hook', encoding='utf-8')
                contract = deepcopy(before['contract'])
                binding = next(b for b in contract['bindings'] if b['path'] == 'worker.py')
                binding.update(path=worker.name, sha256=file_sha(worker))
                contract['bindings'].append({'path':hook.name, 'role':'code', 'sha256':file_sha(hook)})
                for resolved_name in (language, language + {'ruby':'3.4', 'perl':'5.44', 'julia':'1.12', 'lua':'5.4'}[language] + '.exe'):
                    executable = str(Path(sys.executable).with_name(resolved_name))
                    for declaration in ('explicit', 'legacy'):
                        for options in options_list:
                            with self.subTest(language=language, resolved_name=resolved_name,
                                              declaration=declaration, options=options):
                                plan = deepcopy(original)
                                if declaration == 'legacy':
                                    plan.pop('generator_code_paths')
                                else:
                                    plan['generator_code_paths'] = [worker.name, hook.name]
                                for stage in plan['stages']:
                                    # Raw alias deliberately differs from the resolved numeric family.
                                    stage['run']['argv'] = [language, *options, worker.name]
                                with patch.object(store, '_command', return_value=executable):
                                    with self.assertRaisesRegex(ValueError, 'unsupported|Unsupported'):
                                        jump._generator_bindings(store, contract, plan)
            self.assertEqual(store.snapshot(), before)

    def test_consumed_values_literal_main_and_binding_roles_stay_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            before = store.snapshot()
            original = json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
            cases = {
                'ruby3.4': [[], ['-I', '-rHook'], ['-E', '-rHook'], ['-W:rHook']],
                'perl5.44': [[], ['-I', '-MHook'], ['-I-d:Hook'], ['-iMbackup'], ['-FM'], ['-DS']],
                'julia1.12': [[], ['-H', '--load'], ['--home', '-LHook'], ['--home=-JHook'],
                              ['--sysimage-native-code=no'], ['-t2']],
                'lua5.4': [[], ['-e', "print('-lHook')"], ["-eprint('-lHook')"], ['-E', '-W']],
            }
            for name, options_list in cases.items():
                executable = str(Path(sys.executable).with_name(name))
                worker = root / 'worker.opaque'
                worker.write_text('literal frozen code', encoding='utf-8')
                for original_options in options_list + [['--']]:
                    isolation = (['-E'] if name.startswith('lua') else
                                 ['--startup-file=no'] if name.startswith('julia') else [])
                    options = isolation + original_options
                    if original_options == ['--']:
                        worker = root / '-lworker.opaque'
                        worker.write_text('literal dash main', encoding='utf-8')
                    else:
                        worker = root / 'worker.opaque'
                    for declaration in ('explicit', 'legacy'):
                        for role in ('missing', 'data', 'undeclared', 'code'):
                            contract, plan = deepcopy(before['contract']), deepcopy(original)
                            binding = next(b for b in contract['bindings'] if b['path'] == 'worker.py')
                            binding.update(path=worker.name, sha256=file_sha(worker))
                            if role == 'missing':
                                contract['bindings'].remove(binding)
                            elif role == 'data':
                                binding['role'] = 'data'
                            if declaration == 'legacy':
                                plan.pop('generator_code_paths')
                            else:
                                other = next(b['path'] for b in contract['bindings'] if b['role'] == 'code' and b['path'] != worker.name)
                                plan['generator_code_paths'] = [other, worker.name] if role == 'code' else [other]
                            main = worker.name if original_options == ['--'] else str(worker)
                            argv = [name, *options, main, '-rHook', '-MHook', '-lHook', '--load=outside.js']
                            self.assertEqual(jump._interpreter_script_operand([executable, *argv[1:]]),
                                             (len(options) + 1, main))
                            for stage in plan['stages']:
                                stage['run']['argv'] = argv
                            with self.subTest(name=name, options=options, declaration=declaration, role=role):
                                with patch.object(store, '_command', return_value=executable):
                                    if role in ('missing', 'data'):
                                        with self.assertRaisesRegex(ValueError, 'entrypoint.*frozen code'):
                                            jump._generator_bindings(store, contract, plan)
                                    elif role == 'undeclared' and declaration == 'explicit':
                                        with self.assertRaisesRegex(ValueError, 'entrypoint.*generator dependencies'):
                                            jump._generator_bindings(store, contract, plan)
                                    else:
                                        self.assertTrue(jump._generator_bindings(store, contract, plan))
            # Bash is blocked by the actual ProjectStore boundary already.
            shell = root.parent / 'bash.exe'
            shell.write_bytes(b'nonexecuted resolver metadata')
            with patch('rds_project.shutil.which', return_value=str(shell)):
                with self.assertRaisesRegex(ValueError, 'cannot invoke a shell'):
                    store._command(['bash', '--rcfile', str(root.parent / 'hook.sh'), '-i', 'main.sh'])
            for flag in ('--rcfile', '--init-file'):
                for options in ([flag, str(root.parent / 'hook.sh')], [flag + '=outside.sh']):
                    with self.assertRaisesRegex(ValueError, 'Shell explicit startup files are unsupported'):
                        jump._interpreter_script_operand(['bash', *options, '-i', 'main.sh'])
            self.assertEqual(store.snapshot(), before)

    def test_frozen_deep_plan_is_unavailable_without_dispatch_or_state_change(self):
        for depth in (33, 1000, 4000):
            with self.subTest(depth=depth), tempfile.TemporaryDirectory() as directory:
                initialize = ProjectStore.initialize
                def frozen(store, contract):
                    contract = deepcopy(contract)
                    raw = '[' * depth + '0' + ']' * depth
                    (store.root / 'jump-generation.json').write_text(raw, encoding='utf-8')
                    next(b for b in contract['bindings'] if b['path'] == 'jump-generation.json')['sha256'] = file_sha(store.root / 'jump-generation.json')
                    protocol = json.loads((store.root / 'protocol.json').read_text(encoding='utf-8'))
                    protocol.update({role + '_sha256': ProjectStore._role_sha(contract, role) for role in ('code', 'config', 'data')})
                    example.write(store.root / 'protocol.json', protocol)
                    next(b for b in contract['bindings'] if b['role'] == 'protocol')['sha256'] = file_sha(store.root / 'protocol.json')
                    example.write(store.root / 'contract.json', contract)
                    return initialize(store, contract)
                with patch.object(ProjectStore, 'initialize', frozen):
                    root, store = example.build(Path(directory) / 'project')
                before, events = store.snapshot(), structure._events(store)
                with self.assertRaisesRegex(ValueError, 'nesting'):
                    jump.load_plan(store, before)
                unavailable = jump.prepare_owned(store, None)
                self.assertEqual(unavailable['status'], 'JUMP_UNAVAILABLE')
                self.assertFalse(unavailable['changed'])
                self.assertFalse(unavailable['execution_started'])
                self.assertFalse(unavailable['retry_authorized'])
                self.assertIn('nesting', unavailable['diagnostic'])
                self.assertEqual(store.snapshot(), before)
                self.assertEqual(structure._events(store), events)


if __name__ == '__main__':
    unittest.main()

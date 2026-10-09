"""Frozen interpreter operands and sealed parser failures over original records."""
from copy import deepcopy
import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import rds_jump as jump
import rds_artifacts
import rds_structure as structure
from rds_project import ProjectStore, canonical, digest, file_sha

spec = importlib.util.spec_from_file_location('interpreter_boundary_example', REPO / 'examples/jump-generation/run.py')
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class InterpreterBoundaryTests(unittest.TestCase):
    def freeze(self, root, change):
        initialize = ProjectStore.initialize
        def frozen(store, contract):
            contract = deepcopy(contract)
            plan = json.loads((store.root / 'jump-generation.json').read_text(encoding='utf-8'))
            change(store, contract, plan)
            example.write(store.root / 'jump-generation.json', plan)
            next(b for b in contract['bindings'] if b['path'] == 'jump-generation.json')['sha256'] = file_sha(store.root / 'jump-generation.json')
            protocol = json.loads((store.root / 'protocol.json').read_text(encoding='utf-8'))
            protocol.update({role + '_sha256': ProjectStore._role_sha(contract, role) for role in ('code', 'config', 'data')})
            example.write(store.root / 'protocol.json', protocol)
            next(b for b in contract['bindings'] if b['role'] == 'protocol')['sha256'] = file_sha(store.root / 'protocol.json')
            example.write(store.root / 'contract.json', contract)
            return initialize(store, contract)
        with patch.object(ProjectStore, 'initialize', frozen):
            return example.build(root)

    def test_interpreter_extensionless_and_other_suffix_scripts_require_frozen_code(self):
        interpreter = shutil.which('node')
        if interpreter is None:
            self.skipTest('Native Node interpreter unavailable')
        for option in ((), ('--',), ('-r', 'node:fs')):
            for filename in ('worker', 'worker.opaque'):
                for declaration in ('explicit', 'legacy'):
                    for role in ('missing', 'data'):
                        with self.subTest(option=option, filename=filename, declaration=declaration, role=role), tempfile.TemporaryDirectory() as directory:
                            def unfrozen(store, contract, plan):
                                (store.root / filename).write_bytes((store.root / 'worker.py').read_bytes())
                                binding = next(b for b in contract['bindings'] if b['path'] == 'worker.py')
                                binding['path'] = filename
                                if role == 'missing':
                                    contract['bindings'].remove(binding)
                                else:
                                    binding['role'] = 'data'
                                if declaration == 'legacy':
                                    plan.pop('generator_code_paths')
                                else:
                                    plan['generator_code_paths'].remove('worker.py')
                                # The native initializer validates the real Node
                                # executable; this unfrozen script must not launch.
                                for stage in plan['stages']:
                                    command = next(a for a in contract['allowed_commands'] if a == stage['run']['argv'])
                                    command[:] = [interpreter, *option, filename, *command[3:]]
                                    stage['run']['argv'] = command
                            root, store = self.freeze(Path(directory) / 'project', unfrozen)
                            before = store.snapshot()
                            (root / filename).write_text('changed unbound executable source', encoding='utf-8')
                            with self.assertRaisesRegex(ValueError, 'entrypoint.*frozen code'):
                                jump.load_plan(store, store.snapshot())
                            after = store.snapshot()
                            self.assertEqual(after['runs'], before['runs'])
                            self.assertEqual(after['receipts'], before['receipts'])
                            self.assertEqual(after['budget'], before['budget'])

    def test_inline_interpreter_existing_unbound_file_is_rejected(self):
        interpreter = shutil.which('node')
        if interpreter is None:
            self.skipTest('Native Node interpreter unavailable')
        with tempfile.TemporaryDirectory() as directory:
            def unfrozen(store, contract, plan):
                (store.root / 'source.opaque').write_text('unbound source', encoding='utf-8')
                for stage in plan['stages']:
                    command = next(a for a in contract['allowed_commands'] if a == stage['run']['argv'])
                    command[:] = [interpreter, '--eval', 'void 0', '--', 'source.opaque', *command[3:]]
                    stage['run']['argv'] = command
            root, store = self.freeze(Path(directory) / 'project', unfrozen)
            before = store.snapshot()
            with self.assertRaisesRegex(ValueError, 'entrypoint.*frozen code'):
                jump.load_plan(store, store.snapshot())
            self.assertEqual(store.snapshot(), before)

    def test_frozen_node_script_data_and_existing_output_remain_valid(self):
        interpreter = shutil.which('node')
        if interpreter is None:
            self.skipTest('Native Node interpreter unavailable')
        with tempfile.TemporaryDirectory() as directory:
            def frozen_node(store, contract, plan):
                example.write(store.root / 'inputs.opaque', {'frozen_data': True})
                worker = store.root / 'worker.py'
                worker.write_text(worker.read_text(encoding='utf-8').replace(
                    'kind, output = sys.argv[1:]', "kind, output, data = sys.argv[1:]\n    assert read(data) == {'frozen_data': True}"), encoding='utf-8')
                next(b for b in contract['bindings'] if b['path'] == 'worker.py')['sha256'] = file_sha(worker)
                entry = store.root / 'launch-node'
                entry.write_text("const {spawnSync} = require('node:child_process');\n"
                    + 'const result = spawnSync(' + json.dumps(sys.executable)
                    + ", ['-B', 'worker.py', ...process.argv.slice(2)], {stdio: 'inherit'});\n"
                    + 'if (result.error) throw result.error;\n'
                    + 'process.exit(result.status === null ? 1 : result.status);\n', encoding='utf-8')
                contract['bindings'] += [{'path': 'launch-node', 'role': 'code', 'sha256': file_sha(entry)},
                                         {'path': 'inputs.opaque', 'role': 'data', 'sha256': file_sha(store.root / 'inputs.opaque')}]
                plan['generator_code_paths'].append('launch-node')
                for stage in plan['stages']:
                    command = next(a for a in contract['allowed_commands'] if a == stage['run']['argv'])
                    output = command[4].replace('.json', '.opaque')
                    command[:] = ['node', '-r', 'node:fs', '--', 'launch-node', stage['kind'], output, 'inputs.opaque']
                    stage['run']['argv'] = command
                    stage['output'] = output
                    stage['run']['outpaths'] = [output]
            root, store = self.freeze(Path(directory) / 'project', frozen_node)
            self.assertIsNotNone(jump.load_plan(store, store.snapshot()))
            self.assertEqual(jump.generate(root, 1)['status'], 'JUMP_STEP_LIMIT')
            self.assertEqual(len(store.snapshot()['receipts']), 1)
            self.assertEqual(store.snapshot()['receipts'][0]['run_status'], 'SUCCEEDED')
            self.assertTrue((root / 'out/probe.opaque').is_file())
            self.assertIsNotNone(jump.load_plan(store, store.snapshot()))
            self.assertEqual(store.snapshot()['budget']['wall_seconds']['reserved'], 0)

    def test_retained_nested_stage_is_sealed_unavailable_at_parser_depth_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            def nested_result(store, contract, plan):
                # Produce original metadata through the frozen real worker,
                # keeping source-document depth and all native budgets unchanged.
                worker = store.root / 'worker.py'
                worker.write_text(worker.read_text(encoding='utf-8').replace(
                    "    Path(output).write_text(canonical({'schema': 1",
                    "    if kind == 'refresh':\n"
                    "        nested = 'leaf'\n"
                    "        for _ in range(20):\n"
                    "            nested = [nested]\n"
                    "        result['original_metadata'] = nested\n"
                    "    Path(output).write_text(canonical({'schema': 1"), encoding='utf-8')
                next(b for b in contract['bindings'] if b['path'] == 'worker.py')['sha256'] = file_sha(worker)
            root, store = self.freeze(Path(directory) / 'project', nested_result)
            generated = jump.generate(root, 3)
            self.assertEqual(generated['status'], 'JUMP_PROPOSED')
            original = store.snapshot()
            events = structure._events(store)
            raw = (root / 'out/refresh.json').read_text(encoding='utf-8')
            self.assertLess(len(raw.encode('utf-8')), 128 * 1024)
            self.assertEqual(jump.strict_json(raw)['kind'], 'refresh')
            parser = rds_artifacts.strict_json
            rejected = []
            def limited_reader(text):
                if text != raw:
                    return parser(text)
                # Inject a parser-depth boundary only after the original native
                # artifact's receipt and SHA check. The stored data still obeys
                # the unchanged source depth32 / byte cap and ran successfully.
                frame, depth = sys._getframe(), 0
                while frame is not None:
                    depth += 1
                    frame = frame.f_back
                limit = sys.getrecursionlimit()
                try:
                    sys.setrecursionlimit(depth + 15)
                    try:
                        return parser(text)
                    except RecursionError:
                        rejected.append(hashlib.sha256(text.encode('utf-8')).hexdigest())
                        raise
                finally:
                    sys.setrecursionlimit(limit)
            with patch.object(rds_artifacts, 'strict_json', limited_reader):
                context = jump.packet(store)
            self.assertEqual(rejected, [hashlib.sha256(raw.encode('utf-8')).hexdigest()])
            self.assertEqual(context['status'], 'UNAVAILABLE')
            self.assertEqual(context['items'], [])
            self.assertEqual(context['sources'], [])
            self.assertEqual(context['sha256'], digest({k: v for k, v in context.items() if k != 'sha256'}))
            self.assertEqual(store.snapshot()['receipts'], original['receipts'])
            self.assertEqual(store.snapshot()['runs'], original['runs'])
            self.assertEqual(store.snapshot()['budget'], original['budget'])
            self.assertEqual(structure._events(store), events)
            self.assertEqual(jump.packet(store)['status'], 'CURRENT')

    def test_post_assignment_recursion_failure_returns_shallow_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            self.assertEqual(jump.generate(root, 3)['status'], 'JUMP_PROPOSED')
            original, events = store.snapshot(), structure._events(store)
            seal = jump._seal
            def nested_scope(value):
                if value['status'] == 'CURRENT':
                    # Isolated post-assignment serialization fault; no native
                    # contract, successful artifact, receipt or scope is edited.
                    nested = 'leaf'
                    for _ in range(2000):
                        nested = [nested]
                    value['original_scope'] = nested
                    raise RecursionError('Injected post-assignment serializer depth boundary')
                return seal(value)
            with patch.object(jump, '_seal', nested_scope):
                context = jump.packet(store)
            self.assertEqual(context['status'], 'UNAVAILABLE')
            self.assertIsNone(context['original_scope'])
            self.assertEqual(context['items'], [])
            self.assertEqual(context['sources'], [])
            self.assertNotIn('method_context', context)
            self.assertLess(len(canonical(context).encode('utf-8')), jump.PACKET_BYTES)
            self.assertEqual(context['sha256'], digest({k: v for k, v in context.items() if k != 'sha256'}))
            self.assertEqual(store.snapshot()['receipts'], original['receipts'])
            self.assertEqual(store.snapshot()['runs'], original['runs'])
            self.assertEqual(store.snapshot()['budget'], original['budget'])
            self.assertEqual(structure._events(store), events)
            self.assertEqual(jump.packet(store)['status'], 'CURRENT')

    def test_ruby_cwd_options_reject_before_frozen_main_binding(self):
        # Parser/admission over a real native project; no Ruby execution claim.
        # Only absent Ruby resolver metadata is stubbed, as in the PHP boundary.
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            before = store.snapshot()
            (root / 'worker.rb').write_text('puts "frozen root worker"\n', encoding='utf-8')
            (root / 'sub').mkdir()
            (root / 'sub' / 'worker.rb').write_text('puts "unbound child worker"\n', encoding='utf-8')
            contract = deepcopy(before['contract'])
            binding = next(b for b in contract['bindings'] if b['path'] == 'worker.py')
            binding.update(path='worker.rb', sha256=file_sha(root / 'worker.rb'))
            original = json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
            ruby = shutil.which('ruby')
            executable = ruby or str(Path(sys.executable).with_name('ruby.exe' if sys.platform == 'win32' else 'ruby'))
            available = {path for name in ('ruby', 'ruby3.3', 'Ruby3.4.exe')
                         if (path := shutil.which(name))}
            executables = [executable, *[shutil.which(name) or str(Path(sys.executable).with_name(name))
                                        for name in ('ruby3.3', 'Ruby3.4.exe')]]
            options = [['-Csub'], ['-C', 'sub'], ['-Csub', '-C.'],
                       ['-C', 'sub', '-C', '.'], ['-C..'], ['-C', '..'],
                       ['-Xsub'], ['-X', 'sub'], ['-wCsub'], ['-anC', 'sub'],
                       ['-W0Csub'], ['-KUCsub'], ['-000Csub'], ['-xsub'], ['-wxsub'],
                       ['-S'], ['-wS']]
            for executable, option in [(entry, option) for entry in executables for option in options]:
                argv = [executable, *option, 'worker.rb']
                with self.subTest(executable=executable, option=option, boundary='parser'):
                    with self.assertRaisesRegex(ValueError, 'lookup cwd'):
                        jump._interpreter_script_operand(argv)
                for declaration in ('explicit', 'legacy'):
                    with self.subTest(executable=executable, option=option, boundary='binding', declaration=declaration):
                        plan = deepcopy(original)
                        if declaration == 'legacy':
                            plan.pop('generator_code_paths')
                        else:
                            plan['generator_code_paths'][0] = 'worker.rb'
                        for stage in plan['stages']:
                            stage['run']['argv'] = argv
                        if executable in available:
                            with self.assertRaisesRegex(ValueError, 'lookup cwd'):
                                jump._generator_bindings(store, contract, plan)
                        else:
                            with patch.object(store, '_command', return_value=executable):
                                with self.assertRaisesRegex(ValueError, 'lookup cwd'):
                                    jump._generator_bindings(store, contract, plan)
            self.assertEqual(store.snapshot(), before)

    def test_ruby_literal_main_and_option_values_preserve_frozen_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            before = store.snapshot()
            original = json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
            ruby = shutil.which('ruby')
            executable = ruby or str(Path(sys.executable).with_name('ruby.exe' if sys.platform == 'win32' else 'ruby'))
            available = {path for name in ('ruby', 'ruby3.3', 'Ruby3.4.exe')
                         if (path := shutil.which(name))}
            executables = [executable, *[shutil.which(name) or str(Path(sys.executable).with_name(name))
                                        for name in ('ruby3.3', 'Ruby3.4.exe')]]
            cases = [('worker.rb', []), ('worker.rb', ['-r', 'Chelper']),
                     ('worker.rb', ['-rChelper']), ('worker.rb', ['-ICdirectory']),
                     ('worker.rb', ['-I', '-Cdirectory']), ('worker.rb', ['-FC']),
                     ('worker.rb', ['-iCbackup']), ('worker.rb', ['-W:Ccategory']),
                     ('worker.rb', ['-KU']), ('worker.rb', ['-K']), ('worker.rb', ['-x']),
                     ('-Cworker.rb', ['--']), ('-Xworker.rb', ['--']), ('-xworker.rb', ['--']),
                     ('worker.rb', ['-rShelper']), ('worker.rb', ['-ISdirectory']),
                     ('-Sworker.rb', ['--'])]
            for executable, (filename, options) in [(entry, case) for entry in executables for case in cases]:
                (root / filename).write_text('puts "frozen literal worker"\n', encoding='utf-8')
                # A cwd-looking argument after the main is a script argument;
                # after -- even the main filename itself must remain literal.
                argv = [executable, *options, filename, '-Csub', '-S']
                self.assertEqual(jump._interpreter_script_operand(argv), (len(argv) - 3, filename))
                for declaration in ('explicit', 'legacy'):
                    for role in ('missing', 'data', 'code'):
                        with self.subTest(executable=executable, filename=filename, options=options, declaration=declaration, role=role):
                            contract, plan = deepcopy(before['contract']), deepcopy(original)
                            binding = next(b for b in contract['bindings'] if b['path'] == 'worker.py')
                            binding.update(path=filename, sha256=file_sha(root / filename))
                            if role == 'missing':
                                contract['bindings'].remove(binding)
                            else:
                                binding['role'] = role
                            if declaration == 'legacy':
                                plan.pop('generator_code_paths')
                            elif role == 'code':
                                plan['generator_code_paths'][0] = filename
                            else:
                                plan['generator_code_paths'].remove('worker.py')
                            for stage in plan['stages']:
                                stage['run']['argv'] = argv
                            def check():
                                if role == 'code':
                                    self.assertTrue(jump._generator_bindings(store, contract, plan))
                                else:
                                    with self.assertRaisesRegex(ValueError, 'entrypoint.*frozen code'):
                                        jump._generator_bindings(store, contract, plan)
                            if executable in available:
                                check()
                            else:
                                with patch.object(store, '_command', return_value=executable):
                                    check()
            self.assertEqual(store.snapshot(), before)

    def test_php_attached_file_operand_preserves_code_role_and_declaration(self):
        # Parser/admission unit boundary over a native project. PHP execution is
        # not claimed or required; real Node execution is covered above.
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            before = store.snapshot()
            (root / 'worker').write_bytes((root / 'worker.py').read_bytes())
            original_plan = json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
            parser_executable = str(Path(sys.executable).with_name('php.exe' if sys.platform == 'win32' else 'php'))
            cases = [('worker', [option]) for option in
                     ('--file=worker', '-fworker', '--process-file=worker', '-Fworker')]
            cases += [(filename, argv) for filename in ('-fworker', '--file=worker')
                      for argv in (['--', filename], ['-f', filename])]
            for filename, option in cases:
                (root / filename).write_bytes((root / 'worker.py').read_bytes())
                for declaration in ('explicit', 'legacy'):
                    for role in ('missing', 'data', 'code'):
                        with self.subTest(filename=filename, option=option, declaration=declaration, role=role):
                            contract, plan = deepcopy(before['contract']), deepcopy(original_plan)
                            binding = next(b for b in contract['bindings'] if b['path'] == 'worker.py')
                            binding['path'] = filename
                            if role == 'missing':
                                contract['bindings'].remove(binding)
                            else:
                                binding['role'] = role
                            if declaration == 'legacy':
                                plan.pop('generator_code_paths')
                            elif role == 'code':
                                plan['generator_code_paths'][0] = filename
                            else:
                                plan['generator_code_paths'].remove('worker.py')
                            if filename != 'worker':
                                # A separate frozen worker must not mask the
                                # literal main named -fworker / --file=worker.
                                contract['bindings'].append({'path': 'worker', 'role': 'code',
                                                             'sha256': file_sha(root / 'worker')})
                                if declaration == 'explicit':
                                    plan['generator_code_paths'].append('worker')
                            for stage in plan['stages']:
                                stage['run']['argv'] = [parser_executable, *option, '--', stage['kind'], stage['output']]
                            # Only resolver metadata is a stub for this PHP
                            # parser-only case; native Node uses the real kernel.
                            with patch.object(store, '_command', return_value=parser_executable):
                                if role == 'code':
                                    self.assertTrue(jump._generator_bindings(store, contract, plan))
                                else:
                                    with self.assertRaisesRegex(ValueError, 'entrypoint.*frozen code'):
                                        jump._generator_bindings(store, contract, plan)
            self.assertEqual(store.snapshot(), before)


if __name__ == '__main__':
    unittest.main()

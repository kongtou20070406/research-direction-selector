"""Frozen interpreter operands and sealed parser failures over original records."""
from copy import deepcopy
from contextlib import nullcontext
import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
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
    def test_command_wrapper_cannot_hide_the_effective_generator_interpreter(self):
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            before = store.snapshot()
            self.assertTrue(jump.load_plan(store, before))
            original = json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
            policy = root / 'jump-generation.json'
            external_timeout = Path(directory) / 'timeout.exe'
            cases = (
                ('env', root / 'bin' / 'launcher.exe', ['env', 'python', 'worker.py']),
                ('launcher', root / 'bin' / 'env.exe', ['launcher', 'python', 'worker.py']),
                ('timeout', external_timeout, ['timeout', '10', sys.executable, '-B', 'worker.py']),
                ('launcher', external_timeout, ['launcher', '10', sys.executable, '-B', 'worker.py']),
            )
            for command, resolved, argv in cases:
                plan = deepcopy(original)
                plan['stages'][0]['run']['argv'] = argv
                policy.write_text(canonical(plan), encoding='utf-8')
                state = deepcopy(before)
                state['contract'] = deepcopy(before['contract'])
                state['contract']['allowed_commands'].append(argv)
                binding = next(b for b in state['contract']['bindings'] if b['path'] == 'jump-generation.json')
                binding['sha256'] = file_sha(policy)
                with self.subTest(command=command, resolved=resolved.name):
                    with patch.object(store, '_command', return_value=str(resolved)):
                        with self.assertRaisesRegex(ValueError, 'Command-launching wrapper'):
                            jump.load_plan(store, state)

    def test_node_preloads_require_project_frozen_code_and_declaration(self):
        interpreter = shutil.which('node')
        if interpreter is None:
            self.skipTest('Native Node interpreter unavailable')
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            before = store.snapshot()
            worker, loader, external = root / 'worker.js', root / 'loader.opaque', Path(directory) / 'hook.js'
            worker.write_text('console.log(globalThis.preloaded)', encoding='utf-8')
            loader.write_text('globalThis.preloaded = 1;', encoding='utf-8')
            external.write_text('globalThis.preloaded = 1;', encoding='utf-8')
            observed = []
            for value in (1, 2):
                external.write_text(f'globalThis.preloaded = {value};', encoding='utf-8')
                observed.append(subprocess.check_output([interpreter, '-r', str(external), str(worker)],
                                                       text=True, timeout=5).strip())
            self.assertEqual(observed, ['1', '2'])  # Actual mutable external code precedes unchanged main.
            original = json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
            contract = deepcopy(before['contract'])
            binding = next(b for b in contract['bindings'] if b['path'] == 'worker.py')
            binding.update(path='worker.js', sha256=file_sha(worker))
            named = root / 'hook.js'
            named.write_text('globalThis.preloaded = 1;', encoding='utf-8')
            modules = root / 'node_modules'
            modules.mkdir()
            (modules / 'hook.js').write_text('globalThis.preloaded = 3;', encoding='utf-8')
            self.assertEqual(subprocess.check_output([interpreter, '-r', 'hook.js', str(worker)],
                                                    cwd=root, text=True, timeout=5).strip(), '3')
            self.assertEqual(subprocess.check_output([interpreter, '-p', "new URL('file:./tmp/hook.mjs').pathname"],
                                                    text=True, timeout=5).strip(), '/tmp/hook.mjs')
            for declaration in ('explicit', 'legacy'):
                def make(options, arguments=()):
                    plan = deepcopy(original)
                    if declaration == 'legacy':
                        plan.pop('generator_code_paths')
                    else:
                        plan['generator_code_paths'] = ['worker.js']
                    for stage in plan['stages']:
                        stage['run']['argv'] = [interpreter, *options, 'worker.js', *arguments]
                    return plan
                for flag in ('-r', '--require', '--import', '--loader', '--experimental-loader'):
                    forms = [[flag, str(external)]]
                    forms += [[flag + '=' + external.as_uri()]] if flag.startswith('--') else [['-r' + str(external)]]
                    for options in forms:
                        with self.subTest(declaration=declaration, options=options):
                            with self.assertRaisesRegex(ValueError, 'preload.*project-relative frozen code'):
                                jump._generator_bindings(store, contract, make(options))
                for options in (['--import=data:text/javascript,globalThis.preloaded=3'], ['--require', 'unbound-package']):
                    with self.assertRaisesRegex(ValueError, 'preload.*frozen code'):
                        jump._generator_bindings(store, contract, make(options))
                for name in ('hook.js', 'package', '#hook'):
                    path = root / name
                    if name != 'hook.js':
                        path.write_text('globalThis.preloaded = 1;', encoding='utf-8')
                    frozen = deepcopy(contract)
                    frozen['bindings'].append({'path': name, 'role': 'code', 'sha256': file_sha(path)})
                    for flag in ('-r', '--require', '--import', '--loader', '--experimental-loader'):
                        plan = make([flag, name])
                        if declaration == 'explicit':
                            plan['generator_code_paths'].append(name)
                        with self.subTest(declaration=declaration, flag=flag, bare=name):
                            with self.assertRaisesRegex(ValueError, 'preload.*explicit frozen code file'):
                                jump._generator_bindings(store, frozen, plan)
                for role in ('missing', 'data', 'code'):
                    frozen = deepcopy(contract)
                    if role != 'missing':
                        frozen['bindings'].append({'path': 'loader.opaque', 'role': role, 'sha256': file_sha(loader)})
                    for options in (['-r', './loader.opaque'], ['-r./loader.opaque'],
                                    ['--import=' + loader.as_uri()], ['--loader', loader.as_uri()]):
                        plan = make(options)
                        if role == 'code' and declaration == 'explicit':
                            with self.assertRaisesRegex(ValueError, 'preload.*generator dependencies'):
                                jump._generator_bindings(store, frozen, plan)
                            plan['generator_code_paths'].append('loader.opaque')
                        if role == 'code':
                            self.assertTrue(jump._generator_bindings(store, frozen, plan))
                            for uri in ('file:./loader.opaque', 'file:loader.opaque',
                                        loader.as_uri().replace('file:///', 'file:\\\\\\')):
                                malformed = make(['--import=' + uri])
                                if declaration == 'explicit':
                                    malformed['generator_code_paths'].append('loader.opaque')
                                with self.subTest(declaration=declaration, uri=uri):
                                    with self.assertRaisesRegex(ValueError, 'preload.*project-relative frozen code'):
                                        jump._generator_bindings(store, frozen, malformed)
                        else:
                            with self.assertRaisesRegex(ValueError, 'preload.*frozen code'):
                                jump._generator_bindings(store, frozen, plan)
                for options, arguments in ((['-r', 'node:fs'], ()), (['--title', '-r'], ()),
                                           ([], ('-r', str(external)))):
                    self.assertTrue(jump._generator_bindings(store, contract, make(options, arguments)))
            self.assertEqual(store.snapshot(), before)

    def test_node_shared_scan_consumes_values_aliases_and_rejects_esm_ambiguity(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Native Node interpreter unavailable')
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            before = store.snapshot()
            worker = root / 'worker.js'
            worker.write_text('console.log(globalThis.preloadValue)', encoding='utf-8')
            external = Path(directory) / 'external_hook.js'
            external.write_text('globalThis.preloadValue="external-code";', encoding='utf-8')
            warning = root / 'warnings.log'
            warning.write_text('frozen nonexecuted root file', encoding='utf-8')
            original = json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
            contract = deepcopy(before['contract'])
            binding = next(b for b in contract['bindings'] if b['path'] == 'worker.py')
            binding.update(path='worker.js', sha256=file_sha(worker))
            contract['bindings'].append({'path':'warnings.log','role':'code','sha256':file_sha(warning)})
            observed = subprocess.check_output([node, '--redirect-warnings', 'warnings.log', '--require',
                                                str(external), 'worker.js'], cwd=root, text=True, timeout=5).strip()
            self.assertEqual(observed, 'external-code')
            inside = root / '_inside_hook.js'
            inside.write_text('globalThis.preloadValue=7;', encoding='utf-8')
            contract['bindings'].append({'path':inside.name,'role':'code','sha256':file_sha(inside)})
            self.assertEqual(subprocess.check_output([node, '--redirect_warnings', 'warnings.log', '--require',
                                                      './_inside_hook.js', 'worker.js'], cwd=root, text=True, timeout=5).strip(), '7')
            for declaration in ('explicit', 'legacy'):
                def make(options, arguments=()):
                    plan = deepcopy(original)
                    if declaration == 'legacy':
                        plan.pop('generator_code_paths')
                    else:
                        plan['generator_code_paths'] = ['worker.js', 'warnings.log', inside.name]
                    for stage in plan['stages']:
                        stage['run']['argv'] = [node, *options, 'worker.js', *arguments]
                    return plan
                for options in (['--redirect-warnings','warnings.log','--require',str(external)],
                                ['--redirect_warnings=warnings.log','--experimental_loader='+external.as_uri()],
                                ['--title','--require','--import',external.as_uri()]):
                    with self.subTest(declaration=declaration, options=options):
                        with self.assertRaisesRegex(ValueError,'preload.*project-relative frozen code'):
                            jump._generator_bindings(store, contract, make(options))
                for options in (['--future-option','warnings.log','--require',str(external)],
                                ['--future_option=warnings.log','--require',str(external)], ['--eval','1']):
                    with self.assertRaisesRegex(ValueError,'Unsupported Node option'):
                        jump._generator_bindings(store, contract, make(options))
                for specifier in ('./%2e%2e/hook.js','./hook.js#shadow','./hook.js?version=1'):
                    frozen = deepcopy(contract)
                    if '?' not in specifier:
                        fake = root / specifier[2:]
                        fake.parent.mkdir(parents=True, exist_ok=True)
                        fake.write_text('literal misleading frozen file', encoding='utf-8')
                        frozen['bindings'].append({'path':specifier[2:],'role':'code','sha256':file_sha(fake)})
                    # Query rejection is a parser boundary: '?' is not a valid
                    # Windows filename, so no fictitious file/hash is invented.
                    for flag in ('--import','--loader','--experimental_loader'):
                        plan = make([flag,specifier])
                        if declaration == 'explicit' and '?' not in specifier:
                            plan['generator_code_paths'].append(specifier[2:])
                        with self.assertRaisesRegex(ValueError,'ESM preload.*unambiguous'):
                            jump._generator_bindings(store, frozen, plan)
                for options, arguments in ((['--redirect_warnings','warnings.log','--require','./_inside_hook.js'],()),
                                           (['--no_warnings','--require','node:fs'],()),
                                           (['--title','--experimental_loader'],()),
                                           (['--'],('--experimental_loader='+external.as_uri(),))):
                    plan = make(options, arguments)
                    argv = plan['stages'][0]['run']['argv']
                    index = len(argv)-len(arguments)-1
                    self.assertEqual(jump._interpreter_script_operand(argv),(index,'worker.js'))
                    self.assertTrue(jump._generator_bindings(store,contract,plan))
            self.assertEqual(store.snapshot(),before)

    def test_php_startup_configuration_cannot_load_mutable_unbound_code(self):
        # Parser/admission over a real ProjectStore; absent PHP metadata only is stubbed.
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            before = store.snapshot()
            worker = root / 'worker.php'
            worker.write_text('<?php echo "frozen";', encoding='utf-8')
            contract = deepcopy(before['contract'])
            binding = next(b for b in contract['bindings'] if b['path']=='worker.py')
            binding.update(path='worker.php',sha256=file_sha(worker))
            original=json.loads((root/'jump-generation.json').read_text(encoding='utf-8'))
            php=shutil.which('php')
            executable=php or str(Path(sys.executable).with_name('php.exe' if sys.platform=='win32' else 'php'))
            def make(options, executable):
                plan=deepcopy(original);plan['generator_code_paths']=['worker.php']
                for stage in plan['stages']:stage['run']['argv']=[executable,*options,'worker.php']
                return plan
            def make_implicit_stdin(options, executable):
                plan=deepcopy(original);plan['generator_code_paths']=['worker.php']
                for stage in plan['stages']:stage['run']['argv']=[executable,*options]
                return plan
            options=[['-d','auto_prepend_file=/tmp/hook.php'],['-dauto_prepend_file=/tmp/hook.php'],
                     ['--define=auto_prepend_file=/tmp/hook.php'],['-c','/tmp/php.ini'],['-c/tmp/php.ini'],
                     ['--php-ini=/tmp/php.ini'],['-nc/tmp/php.ini']]
            with patch.object(store,'_command',return_value=executable) if php is None else nullcontext():
                for option in options:
                    with self.subTest(option=option):
                        with self.assertRaisesRegex(ValueError,'PHP startup configuration'):
                            jump._generator_bindings(store,contract,make(option, executable))
                with self.assertRaisesRegex(ValueError, 'PHP startup configuration'):
                    jump._generator_bindings(store,contract,make([], executable))
                for option in (['-n'],['--no-php-ini'],['-n','-f']):
                    self.assertTrue(jump._generator_bindings(store,contract,make(option, executable)))
                for implicit_options in ([], ['-q']):
                    with self.subTest(implicit_stdin=implicit_options):
                        self.assertIsNone(jump._interpreter_script_operand([executable,*implicit_options]))
                        with self.assertRaisesRegex(ValueError, 'PHP startup configuration'):
                            jump._interpreter_script_operand([executable,*implicit_options], frozen_startup=True)
                        with self.assertRaisesRegex(ValueError, 'PHP startup configuration'):
                            jump._generator_bindings(store, contract,
                                                     make_implicit_stdin(implicit_options, executable))
                for implicit_options in (['-n'], ['--no-php-ini'], ['-q', '-n']):
                    with self.subTest(implicit_stdin_no_ini=implicit_options):
                        self.assertIsNone(jump._interpreter_script_operand(
                            [executable,*implicit_options], frozen_startup=True))
                        self.assertTrue(jump._generator_bindings(
                            store, contract, make_implicit_stdin(implicit_options, executable)))
                for argv in ([executable, '-r', 'echo "frozen";'],
                             [executable, '-n', '-r', 'echo "frozen";']):
                    with self.assertRaisesRegex(ValueError, 'PHP startup configuration/unsupported options'):
                        jump._interpreter_script_operand(argv, frozen_startup=True)
                with self.assertRaisesRegex(ValueError, 'PHP startup configuration'):
                    jump._interpreter_script_operand([executable, '-'], frozen_startup=True)
                self.assertIsNone(jump._interpreter_script_operand(
                    [executable, '-n', '-'], frozen_startup=True))
            # Deterministic resolved metadata covers direct versioned names and
            # raw php aliases. This does not claim native PHP execution.
            for name in ('php8.3', 'php8.5.exe'):
                resolved = str(Path(sys.executable).with_name(name))
                for raw in (resolved, 'php'):
                    with patch.object(store, '_command', return_value=resolved):
                        for option in options:
                            with self.subTest(raw=raw, resolved=name, option=option):
                                with self.assertRaisesRegex(ValueError, 'PHP startup configuration'):
                                    jump._generator_bindings(store, contract, make(option, raw))
                        for file_option in (['-f', 'worker.php'], ['-fworker.php'], ['--file=worker.php'],
                                            ['-F', 'worker.php'], ['--process-file=worker.php']):
                            for option in options:
                                with self.subTest(raw=raw, file_option=file_option, startup_tail=option):
                                    with self.assertRaisesRegex(ValueError, 'PHP startup configuration'):
                                        jump._generator_bindings(store, contract, make(file_option+option, raw))
                            for no_ini in (['-n'], ['--no-php-ini']):
                                self.assertTrue(jump._generator_bindings(
                                    store, contract, make(no_ini + file_option, raw)))
                                self.assertTrue(jump._generator_bindings(
                                    store, contract, make(file_option + no_ini, raw)))
                            # Only -- or an ordinary runtime argument ends PHP
                            # option parsing after an explicit main file.
                            for arguments in (['--', '-n'], ['-', '-n'], ['literal', '-n']):
                                with self.assertRaisesRegex(ValueError, 'PHP startup configuration'):
                                    jump._generator_bindings(store, contract, make(file_option+arguments, raw))
                        with self.assertRaisesRegex(ValueError, 'PHP startup configuration'):
                            jump._generator_bindings(store, contract, make([], raw))
                        for option in (['-n'], ['--no-php-ini'], ['-n', '-f'], ['-n', '-F']):
                            self.assertTrue(jump._generator_bindings(store, contract, make(option, raw)))
            self.assertEqual(store.snapshot(),before)

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
            cases = [('worker.rb', []), ('worker.rb', ['-ICdirectory']),
                     ('worker.rb', ['-I', '-Cdirectory']), ('worker.rb', ['-FC']),
                     ('worker.rb', ['-iCbackup']), ('worker.rb', ['-W:Ccategory']),
                     ('worker.rb', ['-KU']), ('worker.rb', ['-K']), ('worker.rb', ['-x']),
                     ('-Cworker.rb', ['--']), ('-Xworker.rb', ['--']), ('-xworker.rb', ['--']),
                     ('worker.rb', ['-ISdirectory']), ('worker.rb', ['-I', '-rvalue']),
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

    def test_ruby_effective_preloads_reject_without_reclassifying_values_or_script_arguments(self):
        # Admission against a real ProjectStore, with resolver metadata only.
        # Ruby startup lookup is unsupported, including apparently local files.
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            before = store.snapshot()
            worker = root / 'worker.rb'
            worker.write_text('puts "frozen"\n', encoding='utf-8')
            local = root / 'hook.rb'
            local.write_text('puts "preload"\n', encoding='utf-8')
            external = root.parent / 'outside.rb'
            external.write_text('puts "external preload"\n', encoding='utf-8')
            contract = deepcopy(before['contract'])
            binding = next(b for b in contract['bindings'] if b['path'] == 'worker.py')
            binding.update(path='worker.rb', sha256=file_sha(worker))
            contract['bindings'].append({'path': 'hook.rb', 'role': 'code', 'sha256': file_sha(local)})
            original = json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
            for name in ('ruby', 'ruby3.3', 'Ruby3.4.exe'):
                executable = str(Path(sys.executable).with_name(name))
                for declaration in ('explicit', 'legacy'):
                    def make(options, arguments=()):
                        plan = deepcopy(original)
                        if declaration == 'legacy':
                            plan.pop('generator_code_paths')
                        else:
                            plan['generator_code_paths'] = ['worker.rb', 'hook.rb']
                        for stage in plan['stages']:
                            stage['run']['argv'] = [executable, *options, 'worker.rb', *arguments]
                        return plan
                    with patch.object(store, '_command', return_value=executable):
                        for value in (str(external), './hook.rb', 'mutable-package'):
                            for options in (['-r', value], ['-r'+value], ['-wr'+value], ['-W2r', value]):
                                with self.subTest(name=name, declaration=declaration, options=options):
                                    with self.assertRaisesRegex(ValueError, 'Ruby preloads are unsupported'):
                                        jump._generator_bindings(store, contract, make(options))
                        for options, arguments in ((['-I', '-rvalue'], ()), (['-W:rvalue'], ()),
                                                   (['--'], ('-r', str(external), './unbound.js'))):
                            self.assertTrue(jump._generator_bindings(store, contract, make(options, arguments)))
            self.assertEqual(store.snapshot(), before)

    def test_perl_cwd_options_reject_before_frozen_main_binding(self):
        # Parser/admission over a real native project; no Perl execution claim.
        # Only absent Perl resolver metadata is stubbed, as in the PHP boundary.
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            before = store.snapshot()
            (root / 'worker.pl').write_text('print "frozen root worker"\n', encoding='utf-8')
            (root / 'sub').mkdir()
            (root / 'sub' / 'worker.pl').write_text('print "unbound child worker"\n', encoding='utf-8')
            contract = deepcopy(before['contract'])
            binding = next(b for b in contract['bindings'] if b['path'] == 'worker.py')
            binding.update(path='worker.pl', sha256=file_sha(root / 'worker.pl'))
            original = json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
            perl = shutil.which('perl')
            executable = perl or str(Path(sys.executable).with_name('perl.exe' if sys.platform == 'win32' else 'perl'))
            available = {path for name in ('perl', 'perl5.42', 'Perl5.44.exe')
                         if (path := shutil.which(name))}
            executables = [executable, *[shutil.which(name) or str(Path(sys.executable).with_name(name))
                                        for name in ('perl5.42', 'Perl5.44.exe')]]
            options = [['-S'], ['-wS'], ['-nS'], ['-wS', '-n'],
                       ['-xsub'], ['-wxsub'], ['-x..'], ['-xsub', '-x.'],
                       ['-0xsub'], ['-0777S'], ['-l0777S'], ['-dS'],
                       ['-iSbackup -S'], ['-iSbackup -xsub'], ['-FS -S'],
                       ['-FS -xsub'], ['-wiSbackup -S']]
            for executable, option in [(entry, option) for entry in executables for option in options]:
                argv = [executable, *option, 'worker.pl']
                with self.subTest(executable=executable, option=option, boundary='parser'):
                    with self.assertRaisesRegex(ValueError, 'lookup cwd|Perl module/debugger startup'):
                        jump._interpreter_script_operand(argv)
                for declaration in ('explicit', 'legacy'):
                    with self.subTest(executable=executable, option=option, boundary='binding', declaration=declaration):
                        plan = deepcopy(original)
                        if declaration == 'legacy':
                            plan.pop('generator_code_paths')
                        else:
                            plan['generator_code_paths'][0] = 'worker.pl'
                        for stage in plan['stages']:
                            stage['run']['argv'] = argv
                        if executable in available:
                            with self.assertRaisesRegex(ValueError, 'lookup cwd|Perl module/debugger startup'):
                                jump._generator_bindings(store, contract, plan)
                        else:
                            with patch.object(store, '_command', return_value=executable):
                                with self.assertRaisesRegex(ValueError, 'lookup cwd|Perl module/debugger startup'):
                                    jump._generator_bindings(store, contract, plan)
            for options in (['-we', '1'], ['-nE1'], ['-V:S']):
                with self.assertRaisesRegex(ValueError, 'Perl inline/configuration execution is unsupported'):
                    jump._interpreter_script_operand([executables[0], *options, 'worker.pl'])
            self.assertIsNone(jump._interpreter_script_operand([executables[0], '--help', 'worker.pl']))
            self.assertEqual(store.snapshot(), before)

    def test_perl_literal_main_and_option_values_preserve_frozen_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            before = store.snapshot()
            original = json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
            perl = shutil.which('perl')
            executable = perl or str(Path(sys.executable).with_name('perl.exe' if sys.platform == 'win32' else 'perl'))
            available = {path for name in ('perl', 'perl5.42', 'Perl5.44.exe')
                         if (path := shutil.which(name))}
            executables = [executable, *[shutil.which(name) or str(Path(sys.executable).with_name(name))
                                        for name in ('perl5.42', 'Perl5.44.exe')]]
            cases = [('worker.pl', []), ('worker.pl', ['-w']),
                     ('worker.pl', ['-I', 'Sdirectory']), ('worker.pl', ['-ISdirectory']), ('worker.pl', ['-IS directory']),
                     ('worker.pl', ['-FS']), ('worker.pl', ['-F']),
                     ('worker.pl', ['-iSbackup']), ('worker.pl', ['-iS']), ('worker.pl', ['-i']),
                     ('worker.pl', ['-CS']), ('worker.pl', ['-CSDL']),
                     ('worker.pl', ['-0x53']), ('worker.pl', ['-0777']),
                     ('worker.pl', ['-l0777']), ('worker.pl', ['-x']),
                     ('worker.pl', ['-wx']),
                     ('worker.pl', ['-DS']), ('-Sworker.pl', ['--']),
                     ('-xworker.pl', ['--']), ('worker with space.pl', ['--'])]
            for executable, (filename, options) in [(entry, case) for entry in executables for case in cases]:
                (root / filename).write_text('print "frozen literal worker"\n', encoding='utf-8')
                # A cwd-looking argument after the main is a script argument;
                # after -- even the main filename itself must remain literal.
                argv = [executable, *options, filename, '-xsub', '-S']
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
                     ('--file=worker', '-fworker', '-f=worker', '--process-file=worker', '-Fworker', '-F=worker')]
            cases += [('=worker', ['-f==worker']), ('=worker', ['-F==worker'])]
            cases += [(filename, argv) for filename in ('-fworker', '--file=worker', '=worker')
                      for argv in (['--', filename], ['-f', filename])]
            cases = [(entry, filename, option)
                     for entry in (parser_executable, str(Path(sys.executable).with_name('php8.3')),
                                   str(Path(sys.executable).with_name('php8.5.exe')))
                     for filename, option in cases]
            for parser_executable, filename, option in cases:
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
                            if option in (['-f=worker'], ['-F=worker']):
                                # A frozen file named =worker must not mask the
                                # actual main worker consumed by PHP getopt.
                                (root / '=worker').write_bytes((root / 'worker.py').read_bytes())
                                contract['bindings'].append({'path': '=worker', 'role': 'code',
                                                             'sha256': file_sha(root / '=worker')})
                                if declaration == 'explicit':
                                    plan['generator_code_paths'].append('=worker')
                            if filename != 'worker':
                                # A separate frozen worker must not mask the
                                # literal main named -fworker / --file=worker.
                                contract['bindings'].append({'path': 'worker', 'role': 'code',
                                                             'sha256': file_sha(root / 'worker')})
                                if declaration == 'explicit':
                                    plan['generator_code_paths'].append('worker')
                            for stage in plan['stages']:
                                stage['run']['argv'] = [parser_executable, '-n', *option, '--', stage['kind'], stage['output']]
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

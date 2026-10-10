"""Actual plan admission; resolver-only Lua/Julia metadata, real Node launch."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_jump_interpreter_boundary as f
import test_rds_autonomy as af


class AmbientStartupTests(unittest.TestCase):
    def admission(self, family, options, *, legacy=False, bound=True, main='worker.opaque', refusal=None, no_main=False):
        with tempfile.TemporaryDirectory() as directory:
            def change(store, contract, plan):
                (store.root / main).write_text('inert frozen main', encoding='utf-8')
                binding = next(b for b in contract['bindings'] if b['path'] == 'worker.py')
                if bound:
                    binding.update(path=main, sha256=f.file_sha(store.root / main))
                    plan['generator_code_paths'][0] = main
                if legacy:
                    plan.pop('generator_code_paths')
                for stage in plan['stages']:
                    argv = next(a for a in contract['allowed_commands'] if a == stage['run']['argv'])
                    argv[:] = [family, *options, *([] if no_main else argv[3:])]
                    stage['run']['argv'] = argv
            resolved = str(Path(directory) / (family + ('.exe' if os.name == 'nt' else '')))
            with patch.object(f.ProjectStore, '_command', return_value=resolved):
                root, store = f.InterpreterBoundaryTests().freeze(Path(directory) / 'project', change)
                before = store.snapshot()
                with patch.dict(os.environ, {'LUA_INIT_5_4': 'error("must not execute")',
                                             'JULIA_DEPOT_PATH': str(root.parent / 'unbound')}), \
                        patch.object(store, 'register', side_effect=AssertionError('registration forbidden')), \
                        patch.object(store, 'execute', side_effect=AssertionError('execution forbidden')):
                    if refusal is None:
                        self.assertEqual(len(f.jump.load_plan(store, before)['stages']), 3)
                    else:
                        with self.assertRaisesRegex(ValueError, refusal):
                            f.jump.load_plan(store, before)
                self.assertEqual(store.snapshot(), before)

    def test_lua_effective_environment_isolation_consumed_and_literal_boundaries(self):
        for legacy in (False, True):
            for argv in (['worker.opaque'], ['worker.opaque', '-E'],
                         ['-e', '-E', 'worker.opaque'], ['-e-E', 'worker.opaque'],
                         ['--', 'worker.opaque', '-E']):
                self.admission('lua5.4', argv, legacy=legacy, refusal='startup isolation')
            for argv in (['-E', 'worker.opaque', '-lHook'],
                         ['-e', 'x=1', '-E', 'worker.opaque'],
                         ['-Ex=1', 'worker.opaque'], ['-E', '--', '-literal']):
                if argv[0] == '-Ex=1':
                    self.admission('lua5.4', argv, legacy=legacy, refusal='Unsupported Lua')
                else:
                    main = '-literal' if argv[-1] == '-literal' else 'worker.opaque'
                    self.admission('lua5.4', argv, legacy=legacy, main=main)
                    self.admission('lua5.4', argv, legacy=legacy, main=main, bound=False,
                                   refusal='entrypoint.*frozen code')

    def test_lua_inline_and_stdin_also_require_effective_environment_isolation(self):
        for legacy in (False, True):
            for argv in (['-e', 'print(1)'], ['-e', '-E'], ['-e-E'], ['-']):
                self.admission('lua5.4', argv, legacy=legacy, no_main=True, refusal='startup isolation')
            for argv in (['-E', '-e', 'print(1)'], ['-e', 'print(1)', '-E'], ['-E', '-']):
                self.admission('lua5.4', argv, legacy=legacy, no_main=True)

    def test_julia_effective_startup_no_rejects_defaults_overrides_and_consumed_values(self):
        for legacy in (False, True):
            for argv in (['worker.opaque'], ['worker.opaque', '--startup-file=no'],
                         ['-H', '--startup-file=no', 'worker.opaque'],
                         ['--startup-file=yes', 'worker.opaque'],
                         ['--startup-file=no', '--startup-file=yes', 'worker.opaque'],
                         ['--startup-file', 'future', 'worker.opaque']):
                self.admission('julia1.12', argv, legacy=legacy, refusal='startup')
            for argv in (['--startup-file=no', 'worker.opaque', '--startup-file=yes'],
                         ['--startup-file', 'no', '-t2', 'worker.opaque'],
                         ['--home', '--startup-file=yes', '--startup-file=no', 'worker.opaque'],
                         ['--startup-file=no', '--', '-literal']):
                main = '-literal' if argv[-1] == '-literal' else 'worker.opaque'
                self.admission('julia1.12', argv, legacy=legacy, main=main)
                self.admission('julia1.12', argv, legacy=legacy, main=main, bound=False,
                               refusal='entrypoint.*frozen code')

    def test_node_generator_launch_clears_post_admission_environment_and_keeps_frozen_preload(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Native Node unavailable')
        with tempfile.TemporaryDirectory() as directory:
            def change(store, contract, plan):
                preload = store.root / 'preload.js'
                preload.write_text("global.rdsFrozenPreload = true;", encoding='utf-8')
                entry = store.root / 'entry.js'
                entry.write_text("if (!global.rdsFrozenPreload) throw Error('missing frozen preload');\n"
                    "if (process.env.NODE_OPTIONS) throw Error('ambient startup retained');\n"
                    "const r = require('child_process').spawnSync(" + json.dumps(sys.executable) +
                    ", ['-B', 'worker.py', ...process.argv.slice(2)], {stdio:'inherit'});\n"
                    "if (r.error) throw r.error; process.exit(r.status === null ? 1 : r.status);\n", encoding='utf-8')
                for name in ('entry.js', 'preload.js'):
                    contract['bindings'].append({'path': name, 'role': 'code', 'sha256': f.file_sha(store.root / name)})
                    plan['generator_code_paths'].append(name)
                for stage in plan['stages']:
                    argv = next(a for a in contract['allowed_commands'] if a == stage['run']['argv'])
                    argv[:] = [node, '-r', './preload.js', 'entry.js', *argv[3:]]
                    stage['run']['argv'] = argv
            root, store = f.InterpreterBoundaryTests().freeze(Path(directory) / 'project', change)
            self.assertIsNotNone(f.jump.load_plan(store, store.snapshot()))
            hook = Path(directory) / 'unbound.js'
            hook.write_text("throw Error('unbound startup must never run');", encoding='utf-8')
            ambient = '--require ' + json.dumps(str(hook))
            with patch.dict(os.environ, {'NODE_OPTIONS': ambient, 'RDS_STARTUP_CONTROL': 'retained'}):
                self.assertEqual(f.jump.generate(root, 1)['status'], 'JUMP_STEP_LIMIT')
                self.assertEqual(os.environ['NODE_OPTIONS'], ambient)
                self.assertEqual(os.environ['RDS_STARTUP_CONTROL'], 'retained')
            state = store.snapshot()
            self.assertEqual(len(state['runs']), 1)
            self.assertEqual(len(state['receipts']), 1)
            self.assertEqual(state['receipts'][0]['run_status'], 'SUCCEEDED')
            self.assertEqual(state['receipts'][0]['argv'][1:3], ['-r', './preload.js'])
            self.assertEqual(state['budget']['wall_seconds']['reserved'], 0)

    def test_actual_frozen_adapter_node_launch_preserves_runtime_and_parent_environment(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Native Node unavailable')
        helper = af.AutonomyTests('runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        initialize = f.ProjectStore.initialize
        def prepare(store, contract):
            contract = deepcopy(contract)
            helper.write('node-fixture.js', "if (process.env.NODE_OPTIONS) throw Error('unbound preload');\n"
                "if (!process.env.RDS_RUNTIME_SCRIPTS) throw Error('runtime lost');\n"
                "process.stdin.resume(); process.stdin.on('end', () => process.stdout.write(JSON.stringify("
                "{status:'unknown',source:'',policy_json:'',reason:'bounded local node fixture'})));\n")
            helper.write('frozen-preload.js', "global.frozen = true;")
            worker = store.root / 'worker.py'
            raw = worker.read_text(encoding='utf-8')
            # Bounded local provider-command fixture exercises the actual adapter
            # launch. It makes no claim of a Node provider admission or model call.
            raw = raw.replace("if __name__ == '__main__':", "def provider_command(provider, paths, root):\n    return "
                              + repr([node, '-r', './frozen-preload.js', 'node-fixture.js']) +
                              "\n\nif __name__ == '__main__':")
            worker.write_text(raw, encoding='utf-8')
            next(b for b in contract['bindings'] if b['path'] == 'worker.py')['sha256'] = f.file_sha(worker)
            for name in ('node-fixture.js', 'frozen-preload.js'):
                contract['bindings'].append({'path':name, 'role':'code', 'sha256':f.file_sha(store.root / name)})
            protocol = json.loads((store.root / 'protocol.json').read_text(encoding='utf-8'))
            protocol.update({role + '_sha256': f.ProjectStore._role_sha(contract, role)
                             for role in ('code', 'config', 'data')})
            helper.write('protocol.json', protocol)
            protocol_ref = {'path':'protocol.json', 'sha256':f.file_sha(store.root / 'protocol.json')}
            next(b for b in contract['bindings'] if b['path'] == 'protocol.json').update(protocol_ref)
            for route in contract['advisor_policy']['routes']:
                route['manifest']['protocol'] = protocol_ref
            helper.contract = contract
            return initialize(store, contract)
        with patch.object(f.ProjectStore, 'initialize', prepare):
            helper.build()
        hook = helper.root / 'unbound.js'
        hook.write_text("throw Error('unbound NODE_OPTIONS executed');", encoding='utf-8')
        ambient = '--require ' + json.dumps(str(hook))
        with patch.dict(os.environ, {'NODE_OPTIONS': ambient}):
            event, receipt = helper.run_repair()
            self.assertEqual(os.environ['NODE_OPTIONS'], ambient)
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        envelope = json.loads((helper.root / 'outputs/repair1.json').read_text(encoding='utf-8'))
        self.assertEqual(envelope['returncode'], 0)
        self.assertEqual(envelope['reason'], 'bounded local node fixture')
        self.assertEqual(envelope['status'], 'unknown')
        self.assertEqual(helper.calls(), [])
        self.assertEqual(len(helper.store.snapshot()['receipts']), 1)


if __name__ == '__main__':
    unittest.main()

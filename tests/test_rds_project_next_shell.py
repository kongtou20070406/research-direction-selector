"""#95: execute the printed continuation commands in the actual host shell.

Fixtures only write tiny synthetic JSON files; no scientific workload is run.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore, file_sha


class PrintedProjectCommandTests(unittest.TestCase):
    def shells(self):
        if os.name == 'nt':
            shells = [shutil.which(name) for name in ('pwsh', 'powershell')]
            self.assertTrue(any(shells), 'Windows acceptance requires PowerShell')
            return [shell for shell in shells if shell]
        return ['/bin/sh']

    def fixture(self, root, mode='ok', ids=('control-_1', 'treatment-_1')):
        root.mkdir()
        # Deliberately boring software fixture, including a controllable live child.
        (root / 'code.py').write_text(
            "import json, pathlib, sys, time\n"
            "mode, arm, output = sys.argv[1:]\n"
            "if mode == 'fail': sys.exit(3)\n"
            "if mode == 'wait':\n"
            "    while not pathlib.Path('release').exists(): time.sleep(0.02)\n"
            "pathlib.Path(output).write_text(json.dumps({'mean': 4 if arm == 'control' else 3}))\n",
            encoding='utf-8')
        for name in ('config.json', 'data.json', 'evaluate.py'):
            (root / name).write_text('{}', encoding='utf-8')
        protocol = {key: 'synthetic' for key in
                    ('data_split', 'init', 'seed', 'checkpoint', 'schedule', 'sample_work', 'numeric_protocol')}
        protocol.update({role + '_sha256': file_sha(root / filename)
                         for role, filename in (('code', 'code.py'), ('config', 'config.json'), ('data', 'data.json'))})
        (root / 'protocol.json').write_text(json.dumps(protocol), encoding='utf-8')
        files = {'code': 'code.py', 'config': 'config.json', 'data': 'data.json',
                 'evaluator': 'evaluate.py', 'protocol': 'protocol.json'}
        commands = [[sys.executable, '-B', 'code.py', mode, arm, f'outputs/{arm}.json']
                    for arm in ('control', 'treatment')]
        contract = {'schema': 1,
                    'bindings': [{'role': role, 'path': path, 'sha256': file_sha(root / path)}
                                 for role, path in files.items()],
                    'allowed_commands': commands, 'output_roots': ['outputs'],
                    'budget': {'wall_seconds': 40, 'cpu_seconds': 40},
                    'primary_metric': {'name': 'mean', 'direction': 'min', 'min_useful_delta': '1'}}
        store = ProjectStore(root)
        store.initialize(contract)
        manifests = []
        for arm, argv, run_id in zip(('control', 'treatment'), commands, ids):
            spec = {'schema': 1, 'id': run_id, 'arm': arm,
                    'control_id': ids[0] if arm == 'treatment' else None,
                    'protocol': {'path': 'protocol.json', 'sha256': file_sha(root / 'protocol.json')},
                    'argv': argv, 'outpaths': [f'outputs/{arm}.json'],
                    'resource_estimates': {'wall_seconds': 15, 'cpu_seconds': 15}, 'timeout_seconds': 14}
            path = root / (arm + ' manifest.json')
            path.write_text(json.dumps(spec), encoding='utf-8')
            manifests.append(path)
        return store, manifests

    def cli(self, store, *args):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'),
                                 '--root', str(store.root), *args], cwd=ROOT,
                                capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def emitted(self, store, brief=False, live=False):
        before = store.snapshot()
        result = self.cli(store, 'project', 'status', '--brief') if brief else self.cli(store, 'project', 'next')
        after = store.snapshot()
        if live:
            # The worker legitimately updates elapsed time while this command is read.
            self.assertEqual(after['budget'], before['budget'])
            self.assertEqual([(run['id'], run['attempt_id'], run['status']) for run in after['runs']],
                             [(run['id'], run['attempt_id'], run['status']) for run in before['runs']])
        else:
            self.assertEqual(after, before, 'Printing guidance must not change the project ledger')
        return result['next_move']['command'] if brief else result['command']

    def run_printed(self, command, shell, expect=0):
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
        if os.name == 'nt':
            script = "$ErrorActionPreference='Stop'; " + command + '; exit $LASTEXITCODE'
            argv = [shell, '-NoProfile', '-NonInteractive', '-Command', script]
        else:
            argv = [shell, '-c', command]
        result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True,
                                text=True, encoding='utf-8', timeout=25)
        self.assertEqual(result.returncode, expect, command + '\n' + result.stdout + result.stderr)
        return json.loads(result.stdout) if expect == 0 else result

    def fill(self, command, placeholder, value, shell):
        # Substitute only the documented template slot; preserve the actual emitted prefix.
        # POSIX and PowerShell each receive a literal argument, never an evaluated string.
        import shlex
        quote = (lambda text: "'" + str(text).replace("'", "''").replace('‘', '‘‘').replace('’', '’’') + "'") \
            if os.name == 'nt' else lambda text: shlex.quote(str(text))
        rendered = quote(placeholder)
        if rendered in command:
            return command.replace(rendered, quote(value))
        return command.replace(placeholder, quote(value))

    def test_printed_campaign_commands_space_plain_and_shell_metacharacters(self):
        for shell in self.shells():
            for name in ('plain', "project space ' $cash; & back`tick ’ smart"):
                with self.subTest(shell=shell, name=name), tempfile.TemporaryDirectory() as folder:
                    ids = ('control-_1', 'treatment-_1') if name == 'plain' else ('-控制_1', 'treatment_1')
                    store, manifests = self.fixture(Path(folder) / name, ids=ids)
                    for arm, manifest, run_id in zip(('control', 'treatment'), manifests, ids):
                        template = self.emitted(store)
                        template = self.fill(template, f'<{arm}-manifest.json>', manifest, shell)
                        self.run_printed(template, shell)
                        self.assertTrue(all(run['attempt_id'] is None for run in store.snapshot()['runs']
                                            if run['status'] == 'RESERVED'))
                        command = self.emitted(store, brief=True)
                        self.run_printed(command, shell)
                        run = next(run for run in store.snapshot()['runs'] if run['id'] == run_id)
                        self.assertEqual(run['status'], 'COMPLETED')
                    output = store.root / 'outputs/treatment.json'
                    original = output.read_bytes()
                    output.write_text('{"mean":100}', encoding='utf-8')
                    before = store.snapshot()
                    compare = self.run_printed(self.emitted(store), shell)
                    self.assertEqual(compare['status'], 'UNKNOWN')
                    self.assertEqual(store.snapshot(), before)
                    output.write_bytes(original)
                    checkpoint = self.emitted(store)
                    self.assertIn('checkpoint save', checkpoint)
                    checkpoint = self.fill(checkpoint, '<decision-id>', 'decision-1', shell)
                    decision = store.root / 'decision context.json'
                    decision.write_text('{"note":"synthetic software acceptance"}', encoding='utf-8')
                    checkpoint = self.fill(checkpoint, '<decision; see project compare>', decision, shell)
                    before = store.snapshot()
                    saved = self.run_printed(checkpoint, shell)
                    self.assertEqual(saved['status'], 'SAVED')
                    self.assertEqual(store.snapshot()['budget'], before['budget'])
                    status = self.run_printed(self.emitted(store, brief=True), shell)
                    self.assertEqual([run['status'] for run in status['runs']], ['COMPLETED', 'COMPLETED'])

    def test_printed_settled_failure_review_does_not_rerun_or_spend(self):
        for shell in self.shells():
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as folder:
                store, manifests = self.fixture(Path(folder) / "failed project ' $literal", mode='fail')
                store.register(json.loads(manifests[0].read_text(encoding='utf-8')))
                self.run_printed(self.emitted(store), shell, expect=1)
                before = store.snapshot()
                self.assertEqual(before['runs'][0]['status'], 'FAILED')
                review = self.emitted(store, brief=True)
                self.assertIn('project status --brief', review)
                status = self.run_printed(review, shell)
                self.assertEqual(status['run_states']['FAILED'], 1)
                self.assertEqual(status['next_move']['disposition'], 'REVIEW_EXECUTION_FAILURE')
                self.assertEqual(status['next_move']['receipt_sha256'], before['receipts'][0]['sha256'])
                self.assertEqual(store.snapshot(), before)

    def test_printed_running_status_is_read_only(self):
        for shell in self.shells():
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as folder:
                store, manifests = self.fixture(Path(folder) / 'running project space', mode='wait')
                store.register(json.loads(manifests[0].read_text(encoding='utf-8')))
                outcome = []
                worker = threading.Thread(target=lambda: outcome.append(store.execute('control-_1')))
                worker.start()
                try:
                    deadline = time.monotonic() + 5
                    while (store.snapshot()['runs'][0]['status'] != 'RUNNING'
                           or store.snapshot()['runs'][0].get('pid') is None) and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertEqual(store.snapshot()['runs'][0]['status'], 'RUNNING')
                    before = store.snapshot()
                    status = self.run_printed(self.emitted(store, live=True), shell)
                    self.assertEqual(status['run_states']['RUNNING'], 1)
                    after = store.snapshot()
                    self.assertEqual(after['budget'], before['budget'])
                    self.assertEqual(after['runs'][0]['attempt_id'], before['runs'][0]['attempt_id'])
                    self.assertEqual(after['runs'][0]['status'], 'RUNNING')
                finally:
                    (store.root / 'release').touch()
                    worker.join(timeout=20)
                self.assertFalse(worker.is_alive())
                self.assertEqual(outcome[0]['run_status'], 'SUCCEEDED')

    def test_rejected_stale_printed_command_does_not_start_another_attempt_or_spend(self):
        for shell in self.shells():
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as folder:
                store, manifests = self.fixture(Path(folder) / 'rejected project space')
                store.register(json.loads(manifests[0].read_text(encoding='utf-8')))
                command = self.emitted(store)
                store.execute('control-_1')
                before = store.snapshot()
                result = self.run_printed(command, shell, expect=1)
                self.assertIn('[RDS-REJECT]', result.stderr + result.stdout)
                self.assertEqual(store.snapshot(), before)
                self.assertEqual(store.snapshot()['runs'][0]['attempt_id'], before['runs'][0]['attempt_id'])


if __name__ == '__main__':
    unittest.main()

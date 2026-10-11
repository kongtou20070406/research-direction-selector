"""Synthetic directory-preparation and partial-output recovery through real CLI."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_outputs import prepare_empty_directory
from rds_project import ProjectStore, digest, file_sha


PREPARER = r'''import io, json, pathlib, sys
from rds_outputs import prepare_empty_directory
mode, destination = sys.argv[1:]
parent = pathlib.Path(destination).parent
# A declared output file makes RDS create this parent before the child starts.
assert parent.is_dir() and not any(parent.iterdir()), "RDS must precreate an empty output parent"
prepare_empty_directory(parent)
with pathlib.Path("outputs/expensive-starts.txt").open("a", encoding="utf-8") as stream:
    stream.write("expensive-generation\n")
config = json.loads(pathlib.Path("config.json").read_text(encoding="utf-8"))
pathlib.Path(destination).write_bytes(config["payload"].encode("utf-8"))
if mode == "gbk-failure":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="gbk", errors="strict")
    print("\U0001f680", flush=True)
else:
    print("synthetic preparation complete", flush=True)
'''


RECOVERER = r'''import hashlib, json, pathlib, sys
source, destination = map(pathlib.Path, sys.argv[1:])
config = json.loads(pathlib.Path("config.json").read_text(encoding="utf-8"))
raw = source.read_bytes()
actual = hashlib.sha256(raw).hexdigest()
assert actual == config["expected_sha256"], "retained generated output hash differs"
payload = json.loads(raw)
assert payload == json.loads(config["payload"]), "retained generated output is invalid"
destination.write_text(json.dumps({"validated_sha256": actual, "rows": len(payload["rows"]),
                                  "generation_repeated": False}), encoding="utf-8")
print("validated retained output; no generator launched", flush=True)
'''


class OutputDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-output-directory-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_absent_directory_is_created_without_changing_other_data(self):
        existing = self.root / 'keep.txt'
        existing.write_bytes(b'keep')
        destination = self.root / 'nested/output'
        self.assertEqual(prepare_empty_directory(destination), destination)
        self.assertTrue(destination.is_dir())
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual(existing.read_bytes(), b'keep')

    def test_precreated_empty_directory_is_accepted_idempotently(self):
        destination = self.root / 'output'
        destination.mkdir()
        for _ in range(2):
            self.assertEqual(prepare_empty_directory(destination), destination)
        self.assertEqual(list(destination.iterdir()), [])

    def test_populated_directory_is_rejected_and_contents_retained(self):
        destination = self.root / 'output'
        destination.mkdir()
        original = destination / '.hidden-user-data'
        original.write_bytes(b'original bytes')
        with self.assertRaisesRegex(FileExistsError, 'absent or empty'):
            prepare_empty_directory(destination)
        self.assertEqual(original.read_bytes(), b'original bytes')
        self.assertEqual(list(destination.iterdir()), [original])

    def test_existing_file_is_rejected_and_bytes_retained(self):
        destination = self.root / 'output'
        destination.write_bytes(b'user file')
        with self.assertRaisesRegex(FileExistsError, 'absent or empty'):
            prepare_empty_directory(destination)
        self.assertEqual(destination.read_bytes(), b'user file')

    def test_directory_symlink_is_rejected_without_touching_target(self):
        target = self.root / 'target'
        target.mkdir()
        destination = self.root / 'output-link'
        try:
            destination.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest('platform cannot create directory symlink: ' + str(exc))
        with self.assertRaisesRegex(FileExistsError, 'symbolic link'):
            prepare_empty_directory(destination)
        self.assertTrue(destination.is_symlink())
        self.assertEqual(list(target.iterdir()), [])


class OutputRecoveryCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-output-recovery-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, 'RDS_USAGE_DB': str(self.root / 'usage.sqlite3')}
        payload = json.dumps({'rows': [1, 2, 3]}, sort_keys=True)
        self.expected_sha = hashlib.sha256(payload.encode('utf-8')).hexdigest()
        files = {'code': ('prepare.py', PREPARER), 'config': ('config.json', json.dumps(
            {'payload': payload, 'expected_sha256': self.expected_sha})),
            'data': ('data.json', '[1,2,3]'), 'evaluator': ('evaluator.json', '{}')}
        for name, raw in files.values():
            (self.root / name).write_text(raw, encoding='utf-8')
        (self.root / 'recover.py').write_text(RECOVERER, encoding='utf-8')
        shutil.copyfile(ROOT / 'scripts/rds_outputs.py', self.root / 'rds_outputs.py')
        code_identity = [{'path': name, 'sha256': file_sha(self.root / name)}
                         for name in sorted(('prepare.py', 'recover.py', 'rds_outputs.py'))]
        protocol = {'code_sha256': digest(code_identity),
                    'config_sha256': file_sha(self.root / 'config.json'),
                    'data_sha256': file_sha(self.root / 'data.json'), 'data_split': 'synthetic-development',
                    'init': 'none', 'seed': 0, 'checkpoint': 'none', 'schedule': 'synthetic generation and recovery',
                    'sample_work': {'rows': 3}, 'numeric_protocol': 'Python integer'}
        self.write_json('protocol.json', protocol)
        files['protocol'] = ('protocol.json', '')
        bindings = [{'role': role, 'path': name, 'sha256': file_sha(self.root / name)}
                    for role, (name, _) in files.items()]
        bindings.extend({'role': 'code', 'path': name, 'sha256': file_sha(self.root / name)}
                        for name in ('recover.py', 'rds_outputs.py'))
        self.commands = {
            'success': [sys.executable, '-B', 'prepare.py', 'success', 'outputs/generated/input.json'],
            'generate': [sys.executable, '-B', 'prepare.py', 'gbk-failure', 'outputs/generated/input.json'],
            'recover': [sys.executable, '-B', 'recover.py', 'outputs/generated/input.json', 'outputs/recovery.json']}
        contract = {'schema': 1, 'bindings': bindings, 'allowed_commands': list(self.commands.values()),
                    'output_roots': ['outputs'], 'budget': {'wall_seconds': 30, 'cpu_seconds': 10},
                    'execution_policy': {'schema': 1, 'max_attempts': 2}}
        self.call('project', 'init', '--mode', 'quick', '--contract', self.write_json('contract.json', contract))

    def write_json(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding='utf-8')
        return str(path)

    def call(self, *args, status=0):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'),
                                 '--root', str(self.root), *args], env=self.env,
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, status, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def create(self, run_id):
        output = 'outputs/recovery.json' if run_id == 'recover' else 'outputs/generated/input.json'
        spec = {'schema': 1, 'id': run_id, 'arm': 'tool', 'control_id': None,
                'protocol': {'path': 'protocol.json', 'sha256': file_sha(self.root / 'protocol.json')},
                'argv': self.commands[run_id], 'outpaths': [output],
                'resource_estimates': {'wall_seconds': 4, 'cpu_seconds': 1}, 'timeout_seconds': 3}
        self.call('project', 'create', '--manifest', self.write_json(run_id + '.json', spec))

    def starts(self):
        return (self.root / 'outputs/expensive-starts.txt').read_text(encoding='utf-8').splitlines()

    def test_real_cli_accepts_rds_precreated_empty_output_parent(self):
        self.assertFalse((self.root / 'outputs/generated').exists())
        self.create('success')
        receipt = self.call('project', 'execute', '--id', 'success')
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        self.assertEqual(file_sha(self.root / 'outputs/generated/input.json'), self.expected_sha)
        self.assertEqual(self.starts(), ['expensive-generation'])
        before = ProjectStore(self.root).snapshot()
        observed = self.call('project', 'execute', '--id', 'success')
        self.assertEqual(observed['sha256'], receipt['sha256'])
        self.assertFalse(observed['execution_started'])
        self.assertEqual(ProjectStore(self.root).snapshot()['budget'], before['budget'])
        self.assertEqual(self.starts(), ['expensive-generation'])

    def test_gbk_failure_retains_output_and_recovers_by_validation_without_regeneration(self):
        self.create('generate')
        failed = self.call('project', 'execute', '--id', 'generate', status=1)
        self.assertEqual(failed['run_status'], 'FAILED')
        self.assertEqual(failed['exit_code'], 1)
        self.assertTrue(failed['process_started'])
        self.assertIn('UnicodeEncodeError', (self.root / '.rds/project-artifacts/generate/stderr.bin').read_text())
        retained = self.root / 'outputs/generated/input.json'
        self.assertEqual(file_sha(retained), self.expected_sha)
        output = next(a for a in failed['artifacts'] if a['path'] == 'outputs/generated/input.json')
        self.assertEqual(output['sha256'], self.expected_sha)
        self.assertEqual(failed['sha256'], digest({k: v for k, v in failed.items() if k != 'sha256'}))
        before = ProjectStore(self.root).snapshot()
        reconciled = self.call('project', 'recover', '--id', 'generate', status=1)
        self.assertEqual(reconciled['sha256'], failed['sha256'])
        self.assertEqual(ProjectStore(self.root).snapshot()['budget'], before['budget'])
        self.assertEqual(self.starts(), ['expensive-generation'])
        # Validation is a separately preauthorized, bound cheap route; it never
        # repurposes the original failed receipt or clears the partial directory.
        self.create('recover')
        recovered = self.call('project', 'execute', '--id', 'recover')
        self.assertEqual(recovered['run_status'], 'SUCCEEDED')
        result = json.loads((self.root / 'outputs/recovery.json').read_text(encoding='utf-8'))
        self.assertEqual(result['validated_sha256'], self.expected_sha)
        self.assertEqual(result['rows'], 3)
        self.assertFalse(result['generation_repeated'])
        before_reuse = ProjectStore(self.root).snapshot()
        repeated = self.call('project', 'execute', '--id', 'recover')
        self.assertEqual(repeated['sha256'], recovered['sha256'])
        self.assertFalse(repeated['execution_started'])
        final = ProjectStore(self.root).snapshot()
        self.assertEqual(final['budget'], before_reuse['budget'])
        self.assertEqual(len(final['runs']), 2)
        self.assertEqual(len(final['receipts']), 2)
        self.assertEqual(final['budget']['cpu_seconds']['charged_estimate'], 2)
        self.assertEqual(self.starts(), ['expensive-generation'])
        self.assertEqual(file_sha(retained), self.expected_sha)

    def test_recovery_rejects_changed_output_without_repeating_generator(self):
        self.create('generate')
        failed = self.call('project', 'execute', '--id', 'generate', status=1)
        self.assertEqual(failed['run_status'], 'FAILED')
        retained = self.root / 'outputs/generated/input.json'
        retained.write_bytes(b'{"rows": [999]}')
        changed_sha = file_sha(retained)
        self.assertNotEqual(changed_sha, self.expected_sha)
        self.create('recover')
        rejected = self.call('project', 'execute', '--id', 'recover', status=1)
        self.assertEqual(rejected['run_status'], 'FAILED')
        error = (self.root / '.rds/project-artifacts/recover/stderr.bin').read_text()
        self.assertIn('retained generated output hash differs', error)
        self.assertFalse((self.root / 'outputs/recovery.json').exists())
        self.assertEqual(file_sha(retained), changed_sha)
        final = ProjectStore(self.root).snapshot()
        self.assertEqual(len(final['receipts']), 2)
        self.assertEqual(final['budget']['cpu_seconds']['charged_estimate'], 2)
        self.assertEqual(self.starts(), ['expensive-generation'])


if __name__ == '__main__':
    unittest.main()

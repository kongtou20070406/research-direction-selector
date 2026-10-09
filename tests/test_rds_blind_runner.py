"""Fail-closed orchestration and real isolation canaries, never model scores.

Successful kernel-isolation tests are skipped when Linux/bubblewrap namespaces
are unavailable. A separate live test still asserts refusal and no dispatch.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_blind_runner as blind


GOOD = "import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({'status':'proposed','source':'','policy_json':'','reason':r['prompt']}))\n"


def file_spec(path, destination):
    return {'source': str(path.resolve()), 'path': destination,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def system_test_runtime():
    """Test-only inventory of the distro's trusted Python, not host-directory binds.

    Do not point this helper at untrusted executables: ldd is a trusted-software
    dependency inspection tool. Production inventories need independent review.
    """
    python = Path('/usr/bin/python3')
    ldd = Path('/usr/bin/ldd')
    if sys.platform != 'linux' or not python.exists() or not ldd.exists():
        raise unittest.SkipTest('Live canaries require the distro Python and ldd on Linux')
    python = python.resolve()
    info = json.loads(subprocess.check_output([str(python), '-I', '-S', '-c',
        "import json,sysconfig; print(json.dumps(sysconfig.get_paths()))"], text=True,
        env={'LANG': 'C.UTF-8'}, timeout=10))
    stdlib = Path(info['stdlib'])
    files = {str(python): python}
    native = [python]
    for source in sorted(stdlib.rglob('*')):
        relative = source.relative_to(stdlib)
        if any(part in {'site-packages', 'dist-packages', '__pycache__', 'test', 'tests', 'ensurepip'}
               for part in relative.parts) or source.is_symlink() or not source.is_file():
            continue
        if source.suffix not in ('.py', '.so'):
            continue
        files[str(source)] = source
        if source.suffix == '.so':
            native.append(source)
    linked = subprocess.check_output([str(ldd), *map(str, native)], text=True,
                                     env={'LANG': 'C.UTF-8'}, timeout=20)
    for line in linked.splitlines():
        if '=>' in line:
            match = re.search(r'=>\s+(/\S+)', line)
        else:
            match = re.match(r'\s+(/\S+)\s+\(', line)
        if match:
            destination = match.group(1)
            files[destination] = Path(destination).resolve()
    return {'python': str(python), 'files': [file_spec(source, destination)
                                            for destination, source in sorted(files.items())]}


class ProjectionTests(unittest.TestCase):
    def test_only_selected_scalar_leaves_reach_public_request(self):
        full = {'run_id': 'r1', 'task': {'prompt': 'find an explanation', 'labels': 'SECRET'},
                'evaluator': {'source': 'ORACLE'}, 'results': [{'observation': 3, 'answer': 9}],
                'provider': {'token': 'CREDENTIAL'}}
        projected = blind.project_request(full, {'id': ('run_id',), 'prompt': ('task', 'prompt'),
                                                 'observation': ('results', 0, 'observation')})
        self.assertEqual(projected, {'id': 'r1', 'prompt': 'find an explanation', 'observation': 3})
        self.assertNotIn('SECRET', json.dumps(projected))
        self.assertNotIn('ORACLE', json.dumps(projected))
        self.assertNotIn('CREDENTIAL', json.dumps(projected))
        full['task']['prompt'] = 'changed'
        self.assertEqual(projected['prompt'], 'find an explanation')

    def test_no_implicit_nested_export_or_missing_field(self):
        for fields in ({}, {'all': ('task',)}, {'bad': ('missing',)}, {'bad': ()}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                blind.project_request({'task': {'labels': 'secret'}}, fields)

    def test_nonfinite_and_oversized_input_rejected(self):
        for value in (float('nan'), float('inf'), 'x' * blind.MAX_INPUT_BYTES):
            with self.subTest(value=type(value)), self.assertRaises(ValueError):
                blind.project_request({'value': value}, {'value': ('value',)})

    def test_strict_response(self):
        accepted = {'status': 'proposed', 'source': 'print(1)', 'policy_json': '{}', 'reason': 'test'}
        self.assertEqual(blind.validate_response(json.dumps(accepted).encode()), accepted)
        for raw in (b'{}', b'{"status":"proposed","status":"proposed"}', b'[]',
                    b'NaN', b'{} trailing', b'\xff',
                    json.dumps(dict(accepted, reason=2)).encode(),
                    json.dumps(dict(accepted, extra='no')).encode(),
                    json.dumps(dict(accepted, status='no_feasible_method')).encode(),
                    json.dumps(dict(accepted, reason='\ud800')).encode()):
            with self.subTest(raw=raw[:80]), self.assertRaises((ValueError, UnicodeError)):
                blind.validate_response(raw)

    def test_paths_are_canonical_and_scoped(self):
        for path in ('../private', '/etc/passwd', '.', 'a/../b', 'a//b', 'a\\b', 'a/'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                blind._destination(path)
        for path in ('//', '//usr/foo', '/etc/passwd', '/proc/self/environ', '/usr/../private', '/usr//file', 'usr/file'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                blind._destination(path, runtime=True)


    def test_probe_environment_accepts_only_fixed_bwrap_pwd(self):
        statement = next(node for node in ast.parse(blind._PROBE).body if isinstance(node, ast.Assign)
                         and "checks['clean_environment']" in ast.unparse(node))
        code = compile(ast.Module(body=[statement], type_ignores=[]), '<probe-environment-test>', 'exec')
        base = {'HOME': '/nonexistent', 'TMPDIR': '/work', 'PATH': '/nonexistent',
                'LANG': 'C.UTF-8', 'PWD': '/work'}
        for environment, expected in ((base, True), (dict(base, PWD='/host/private'), False),
                                      (dict(base, SECRET_TOKEN='secret'), False)):
            namespace = {'checks': {}, 'os': SimpleNamespace(environ=environment)}
            exec(code, namespace)
            self.assertIs(namespace['checks']['clean_environment'], expected)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        # Unit boundary checks mock the probe/capture; this is not live isolation.
        account = patch.object(blind.os, 'geteuid', return_value=1000, create=True)
        account.start()
        self.addCleanup(account.stop)
        self.temp = tempfile.TemporaryDirectory(prefix='rds-blind-unit-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker = self.root / 'worker.py'
        self.worker.write_text(GOOD, encoding='utf-8')
        self.fake_runtime = self.root / 'python'
        self.fake_runtime.write_bytes(b'trusted test inventory; not executed')
        self.runtime = {'python': '/runtime/python', 'files': [file_spec(self.fake_runtime, '/runtime/python')]}

    def run_worker(self, **overrides):
        values = dict(request={'prompt': 'public problem'}, worker=file_spec(self.worker, 'worker.py'),
                      runtime=self.runtime, output_dir=self.root / 'out')
        values.update(overrides)
        return blind.run_blind(**values)

    def test_unavailable_backend_is_refusal_without_dispatch(self):
        with patch.object(blind.shutil, 'which', return_value=None), patch.object(blind, '_capture') as capture:
            result = self.run_worker()
        self.assertEqual(result['status'], 'refused')
        self.assertIn('ISOLATION_UNAVAILABLE', result['reason'])
        self.assertFalse(result['worker_dispatched'])
        capture.assert_not_called()
        self.assertTrue((self.root / 'out' / 'result.json').is_file())
        self.assertFalse((self.root / 'out' / 'dispatch.json').exists())

    @unittest.skipUnless(sys.platform == 'linux', 'Linux file inventory staging')
    def test_failed_probe_never_launches_candidate_or_falls_back(self):
        with patch.object(blind.sys, 'platform', 'linux'), patch.object(blind.shutil, 'which', return_value=str(self.fake_runtime)), \
                patch.object(blind, '_probe', return_value={'passed': False, 'checks': None}), \
                patch.object(blind, '_capture') as capture:
            result = self.run_worker()
        self.assertEqual(result['status'], 'refused')
        self.assertFalse(result['worker_dispatched'])
        self.assertIn('ISOLATION_UNAVAILABLE', result['reason'])
        capture.assert_not_called()
        self.assertFalse((self.root / 'out' / 'dispatch.json').exists())

    @unittest.skipUnless(sys.platform == 'linux', 'openat/O_NOFOLLOW inventory validation is Linux-only')
    def test_declared_private_files_and_symlink_exports_are_refused_before_probe(self):
        private = self.root / 'private'
        private.mkdir()
        oracle = private / 'oracle.py'
        oracle.write_text('answer = 1729', encoding='utf-8')
        link = self.root / 'innocent.txt'
        link.symlink_to(oracle)
        spec = file_spec(oracle, 'observations.txt')
        spec['source'] = str(link)
        with patch.object(blind.shutil, 'which', return_value=str(self.fake_runtime)), patch.object(blind, '_probe') as probe:
            result = self.run_worker(public_files=[spec], private_roots=[private])
        self.assertEqual(result['status'], 'refused')
        self.assertIn('private file', result['reason'])
        probe.assert_not_called()

    @unittest.skipUnless(sys.platform == 'linux', 'openat/O_NOFOLLOW inventory validation is Linux-only')
    def test_changed_worker_identity_is_refused_before_probe(self):
        spec = file_spec(self.worker, 'worker.py')
        self.worker.write_text('print("changed")', encoding='utf-8')
        with patch.object(blind.shutil, 'which', return_value=str(self.fake_runtime)), patch.object(blind, '_probe') as probe:
            result = self.run_worker(worker=spec)
        self.assertEqual(result['status'], 'refused')
        self.assertIn('identity', result['reason'])
        probe.assert_not_called()

    def test_unsupported_platform_refuses_before_linux_api_or_dispatch(self):
        with patch.object(blind.sys, 'platform', 'win32'), patch.object(blind, '_probe') as probe, \
                patch.object(blind, '_capture') as capture:
            result = self.run_worker()
        self.assertEqual(result['status'], 'refused')
        self.assertIn('Linux bubblewrap', result['reason'])
        self.assertFalse(result['worker_dispatched'])
        probe.assert_not_called()
        capture.assert_not_called()
        self.assertTrue((self.root / 'out' / 'result.json').is_file())

    @unittest.skipUnless(sys.platform == 'linux', 'Linux inventory staging')
    def test_hostile_response_surrogate_preserves_error_result(self):
        raw = json.dumps({'status': 'proposed', 'source': '', 'policy_json': '', 'reason': '\ud800'}).encode()
        def fake_capture(*args, **kwargs):
            (self.root / 'out' / 'stdout.bin').write_bytes(raw)
            (self.root / 'out' / 'stderr.bin').write_bytes(b'')
            return {'returncode': 0, 'timed_out': False, 'output_limit': False, 'error': None}
        with patch.object(blind.shutil, 'which', return_value=str(self.fake_runtime)), \
                patch.object(blind, '_probe', return_value={'passed': True, 'checks': 'mock, not isolation evidence'}), \
                patch.object(blind, '_capture', side_effect=fake_capture):
            result = self.run_worker()
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['response'])
        self.assertIn('INVALID_WORKER_RESPONSE', result['reason'])
        self.assertEqual((self.root / 'out' / 'stdout.bin').read_bytes(), raw)
        self.assertEqual(json.loads((self.root / 'out' / 'result.json').read_text())['status'], 'error')

    @unittest.skipUnless(sys.platform == 'linux', 'Linux sealed anonymous descriptors')
    def test_stdin_descriptor_is_immutable_even_when_reopened(self):
        fd = blind._sealed_fd('rds-blind-unit-canary', b'original public input')
        try:
            self.assertNotIn(str(self.root), os.readlink('/proc/self/fd/' + str(fd)))
            reopened = os.open('/proc/self/fd/' + str(fd), os.O_WRONLY)
            try:
                with self.assertRaises(OSError):
                    os.write(reopened, b'changed')
            finally:
                os.close(reopened)
            self.assertEqual(os.read(fd, 100), b'original public input')
        finally:
            os.close(fd)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux sealed descriptor/process collection')
    def test_explicit_filter_descriptor_is_rewound_between_launches(self):
        source = self.root / 'stdin'
        source.write_bytes(b'')
        fd = blind._sealed_fd('rds-blind-unit-filter', b'bounded filter fixture')
        try:
            code = "import os,sys; sys.stdout.buffer.write(os.read(int(sys.argv[1]),1024))"
            for index in range(2):
                result = blind._capture([sys.executable, '-I', '-S', '-c', code, str(fd)],
                                        source, self.root, str(index) + '.', 2, 1024,
                                        512 * 1024 * 1024, seccomp_fd=fd)
                self.assertEqual(result['returncode'], 0, result)
                self.assertEqual((self.root / (str(index) + '.stdout.bin')).read_bytes(), b'bounded filter fixture')
        finally:
            os.close(fd)

    def test_previous_attempt_is_not_overwritten_or_retried(self):
        with patch.object(blind.shutil, 'which', return_value=None):
            self.run_worker()
        original = (self.root / 'out' / 'result.json').read_bytes()
        with self.assertRaises(FileExistsError):
            self.run_worker()
        self.assertEqual((self.root / 'out' / 'result.json').read_bytes(), original)

    def test_cli_malformed_deep_manifest_is_structured_refusal(self):
        path = self.root / 'bad.json'
        path.write_text('[' * 20000 + '0' + ']' * 20000, encoding='utf-8')
        process = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_blind_runner.py'),
                                  '--manifest', str(path), '--output-dir', str(self.root / 'bad-out')],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)['status'], 'refused')
        self.assertNotIn('Traceback', process.stderr)

    def test_sandbox_command_has_no_ambient_host_mount_or_network_fallback(self):
        command = blind._command('/usr/bin/bwrap', '/STAGED_RUNTIME', '/STAGED_PUBLIC', '/STAGED_CODE',
                                 '/runtime/python', '/rds/worker.py', 7)
        for flag in ('--unshare-user', '--unshare-pid', '--unshare-net', '--disable-userns',
                     '--assert-userns-disabled', '--clearenv', '--cap-drop', '--die-with-parent', '--remount-ro', '--seccomp'):
            self.assertIn(flag, command)
        for flag in ('--share-net', '--unshare-user-try', '--bind', '--bind-try', '--ro-bind-try', '--not-a-security-boundary'):
            self.assertNotIn(flag, command)
        mounts = [command[index + 1:index + 3] for index, arg in enumerate(command) if arg == '--ro-bind']
        # Host source paths follow pathlib on the platform inspecting the
        # command; sandbox destinations remain Linux absolute paths.
        empty = str(Path('/STAGED_CODE') / 'empty')
        empty_proc = str(Path('/STAGED_CODE') / 'empty-proc-one')
        self.assertEqual(mounts, [['/STAGED_RUNTIME', '/'], [empty, '/proc/keys'],
                                  [empty, '/proc/key-users'],
                                  [empty_proc, '/proc/1'],
                                  ['/STAGED_PUBLIC', '/public'], ['/STAGED_CODE', '/rds']])

    @unittest.skipUnless(sys.platform == 'linux', 'Linux resource limits/process-group collection')
    def test_seccomp_denies_keys_network_uring_and_compat_architectures(self):
        raw, calls = blind._seccomp_program()
        program = [struct.unpack('=HBBI', raw[i:i + 8]) for i in range(0, len(raw), 8)]
        native_arch = program[1][3]
        def evaluate(arch, number):
            pc, accumulator = 0, 0
            while pc < len(program):
                code, jt, jf, value = program[pc]
                if code == 0x20:
                    accumulator = arch if value == 4 else number
                elif code == 0x15:
                    pc += jt if accumulator == value else jf
                elif code == 0x35:
                    pc += jt if accumulator >= value else jf
                elif code == 0x06:
                    return value
                else:
                    self.fail('Unexpected BPF opcode')
                pc += 1
            self.fail('Missing BPF terminal action')
        for number in calls.values():
            self.assertEqual(evaluate(native_arch, number), 0x00050001)
        self.assertEqual(evaluate(native_arch, 1), 0x7FFF0000)
        self.assertEqual(evaluate(0x40000003, 1), 0x80000000)
        self.assertEqual(evaluate(native_arch, 0x40000001), 0x80000000)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux resource limits/process-group collection')
    def test_real_output_limits_preserve_prefixes_and_nonzero_exit(self):
        source = self.root / 'stdin'
        source.write_bytes(b'')
        for index, code in enumerate(("import os; os.write(1,b'x'*1000000); os.write(2,b'y'*1000000)",
                                      "import sys; print('original error',file=sys.stderr); sys.exit(7)")):
            result = blind._capture([sys.executable, '-I', '-S', '-c', code], source, self.root,
                                    str(index) + '.', 2, 1024, 512 * 1024 * 1024)
            if index == 0:
                self.assertTrue(result['output_limit'])
                self.assertLessEqual((self.root / '0.stdout.bin').stat().st_size, 1024)
                self.assertLessEqual((self.root / '0.stderr.bin').stat().st_size, 1024)
            else:
                self.assertEqual(result['returncode'], 7)
                self.assertIn(b'original error', (self.root / '1.stderr.bin').read_bytes())

    @unittest.skipUnless(sys.platform == 'linux', 'Linux resource limits/process-group collection')
    def test_real_timeout_retains_original_output(self):
        source = self.root / 'stdin'
        source.write_bytes(b'')
        result = blind._capture([sys.executable, '-I', '-S', '-c',
                                 "import time; print('before timeout',flush=True); time.sleep(20)"],
                                source, self.root, '', .25, 1024, 512 * 1024 * 1024)
        self.assertTrue(result['timed_out'])
        self.assertIsNotNone(result['returncode'])
        self.assertLess(result['wall_seconds'], 3)
        self.assertEqual((self.root / 'stdout.bin').read_bytes(), b'before timeout\n')


class LiveIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        unavailable = (sys.platform != 'linux' or not shutil.which('bwrap', path='/usr/bin:/bin') or os.geteuid() == 0)
        if unavailable:
            if os.environ.get('RDS_REQUIRE_BLIND_ISOLATION') == '1':
                raise RuntimeError('Required live isolation backend/account is unavailable')
            raise unittest.SkipTest('Live canaries require Linux, bubblewrap and an unprivileged account')
        cls.runtime = system_test_runtime()
        cls.temp = tempfile.TemporaryDirectory(prefix='rds-blind-live-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.worker = cls.root / 'worker.py'
        cls.worker.write_text(GOOD, encoding='utf-8')
        cls.result = blind.run_blind(request={'prompt': 'a public observation'},
                                    worker=file_spec(cls.worker, 'worker.py'), runtime=cls.runtime,
                                    output_dir=cls.root / 'live', timeout_seconds=3)

    def test_live_boundary_or_fail_closed_no_worker_dispatch(self):
        result = self.result
        if result['isolation'] is None:
            self.fail('Runtime inventory failed before the live probe: ' + result['reason'])
        if result['isolation']['passed']:
            self.assertEqual(result['status'], 'completed', result)
            self.assertTrue(all(result['isolation']['checks'].values()))
            self.assertEqual(result['response']['reason'], 'a public observation')
        else:
            self.assertEqual(result['status'], 'refused')
            self.assertFalse(result['worker_dispatched'])
            self.assertFalse((self.root / 'live' / 'dispatch.json').exists())
            self.assertEqual((self.root / 'live' / 'stdout.bin').read_bytes(), b'')
            self.assertTrue((self.root / 'live' / 'probe.stderr.bin').is_file())

    def test_successful_real_canaries_require_available_kernel_isolation(self):
        if not self.result['isolation']['passed']:
            if os.environ.get('RDS_REQUIRE_BLIND_ISOLATION') == '1':
                self.fail('Required kernel isolation probe did not pass')
            self.skipTest('Kernel isolation unavailable; live refusal tested separately: ' +
                          (self.root / 'live' / 'probe.stderr.bin').read_text(encoding='utf-8', errors='replace')[:300])
        self.assertEqual(set(self.result['isolation']['checks'].values()), {True})

    def test_setsid_descendant_does_not_hold_capture_open(self):
        if not self.result['isolation']['passed']:
            self.skipTest('Descendant cleanup requires the real namespace boundary')
        for mode in ('timeout', 'exit'):
            worker = self.root / ('descendant-' + mode + '.py')
            worker.write_text("import os,time\n"
                              "if os.fork()==0:\n"
                              "    os.setsid()\n"
                              "    time.sleep(30)\n"
                              "    os._exit(0)\n"
                              "print('forked descendant holds stdout',flush=True)\n" +
                              ("time.sleep(30)\n" if mode == 'timeout' else "raise SystemExit(9)\n"),
                              encoding='utf-8')
            result = blind.run_blind(request={'prompt': 'trusted lifecycle test'},
                                     worker=file_spec(worker, 'worker.py'), runtime=self.runtime,
                                     output_dir=self.root / ('descendant-' + mode), timeout_seconds=2)
            self.assertEqual(result['status'], 'error', result)
            self.assertIsNotNone(result['process']['returncode'])
            self.assertLess(result['process']['wall_seconds'], 5)
            self.assertIs(result['process']['timed_out'], mode == 'timeout')
            self.assertIsNone(result['process']['error'])
            self.assertIn(b'forked descendant', (self.root / ('descendant-' + mode) / 'stdout.bin').read_bytes())

    def test_cli_reaches_real_fail_closed_boundary(self):
        manifest = {'schema': 1, 'request': {'prompt': 'CLI public observation'},
                    'worker': file_spec(self.worker, 'worker.py'), 'runtime': self.runtime, 'timeout_seconds': 3}
        path = self.root / 'manifest.json'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        process = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_blind_runner.py'),
                                  '--manifest', str(path), '--output-dir', str(self.root / 'cli')],
                                 capture_output=True, text=True, timeout=20)
        summary = json.loads(process.stdout)
        retained = json.loads((self.root / 'cli' / 'result.json').read_text())
        self.assertEqual(summary['status'], retained['status'])
        if retained['isolation']['passed']:
            self.assertEqual(process.returncode, 0)
            self.assertEqual(retained['status'], 'completed')
        else:
            self.assertEqual(process.returncode, 2)
            self.assertEqual(retained['status'], 'refused')
            self.assertFalse(retained['worker_dispatched'])
            self.assertFalse((self.root / 'cli' / 'dispatch.json').exists())


if __name__ == '__main__':
    unittest.main()

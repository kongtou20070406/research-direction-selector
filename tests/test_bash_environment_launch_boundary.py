"""Production launch environment controls and a benign native Bash worker."""
import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'scripts/rds_project.py'


def launch_code():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    project_store = next(node for node in tree.body
                         if isinstance(node, ast.ClassDef) and node.name == 'ProjectStore')
    execute = next(node for node in project_store.body
                   if isinstance(node, ast.FunctionDef) and node.name == '_execute_admitted')
    body = next(node.body for node in ast.walk(execute)
                if isinstance(getattr(node, 'body', None), list)
                and any(isinstance(statement, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == 'worker_options'
                                for target in statement.targets)
                        for statement in node.body)
                and any(isinstance(statement, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == 'process'
                                for target in statement.targets)
                        and isinstance(statement.value, ast.Call)
                        and isinstance(statement.value.func, ast.Attribute)
                        and statement.value.func.attr == 'Popen'
                        for statement in node.body))
    start = next(i for i, statement in enumerate(body)
                 if isinstance(statement, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'worker_options'
                         for target in statement.targets))
    end = next(i for i, statement in enumerate(body)
               if isinstance(statement, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == 'process'
                       for target in statement.targets)
               and isinstance(statement.value, ast.Call)
               and isinstance(statement.value.func, ast.Attribute)
               and statement.value.func.attr == 'Popen')
    module = ast.fix_missing_locations(ast.Module(body=body[start:end + 1], type_ignores=[]))
    return compile(module, str(SOURCE), 'exec')


class InterpreterEnvironmentLaunchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = launch_code()

    def observe(self, executable, autonomy):
        parent = {'PATH': 'retained-path', 'BASH_ENV': 'late-bash-startup',
                  'bash_env': 'case-variant-bash-startup', 'BASHOPTS': 'extdebug',
                  'BaShOpTs': 'extdebug', 'SHELLOPTS': 'xtrace', 'ShElLoPtS': 'xtrace',
                  'RUBYOPT': 'retained-ruby-control', 'NODE_OPTIONS': 'retained-node-control',
                  'RUBYLIB': 'late-library', 'RUBYGEMS_GEMDEPS': 'late-Gemfile',
                  'PERL5OPT': 'late-perl-startup', 'perl5opt': 'case-variant-perl-startup',
                  'PHPRC': 'late-php.ini', 'phprc': 'case-variant-php.ini',
                  'PHP_INI_SCAN_DIR': 'late-php-conf.d', 'php_ini_scan_dir': 'case-variant-php-conf.d',
                  'RDS_RUNTIME_SCRIPTS': 'parent-runtime'}
        before = dict(parent)
        captured = {}

        def popen(argv, **options):
            captured['argv'] = list(argv)
            captured['options'] = options
            return object()  # Observe only; no interpreter, worker or hook is invoked.

        argv = [str(Path('bin') / executable), '--noprofile', '--norc', 'worker.sh', 'exact-tail']
        namespace = {
            'argv': argv,
            'current': {'autonomy_request': {}} if autonomy else {},
            'os': SimpleNamespace(name=os.name, environ=parent),
            'Path': Path,
            're': re,
            'subprocess': SimpleNamespace(CREATE_NO_WINDOW=1, CREATE_NEW_PROCESS_GROUP=2,
                                          DEVNULL=-3, Popen=popen),
            'self': SimpleNamespace(root=Path('frozen-project')),
            'flags': 0,
            'out': object(),
            'err': object(),
            '__file__': str(SOURCE),
        }
        exec(self.launch, namespace)
        return captured, argv, parent, before

    def assert_filtered(self, executable, blocked, autonomy):
        all_startup = {'bash_env', 'bashopts', 'shellopts', 'perl5opt', 'phprc',
                       'php_ini_scan_dir', 'rubyopt', 'rubylib', 'rubygems_gemdeps', 'node_options'}
        for executable in (executable,):
            with self.subTest(executable=executable):
                captured, argv, parent, before = self.observe(executable, autonomy)
                options = captured['options']
                self.assertEqual(captured['argv'], argv)
                self.assertIs(options['shell'], False)
                env = options['env']
                for key in all_startup:
                    matching = [name for name in env if name.casefold() == key]
                    if key in blocked:
                        self.assertEqual(matching, [])
                    else:
                        self.assertTrue(matching)
                self.assertEqual(env['PATH'], 'retained-path')
                expected_runtime = str(SOURCE.resolve().parent) if autonomy else 'parent-runtime'
                self.assertEqual(env['RDS_RUNTIME_SCRIPTS'], expected_runtime)
                self.assertEqual(parent, before)

    def assert_all_interpreters(self, autonomy):
        cases = (
            ('bash', {'bash_env', 'bashopts', 'shellopts'}), ('BASH.EXE', {'bash_env', 'bashopts', 'shellopts'}),
            ('BaSh5.2.exe', {'bash_env', 'bashopts', 'shellopts'}),
            ('bash-5.2', {'bash_env', 'bashopts', 'shellopts'}), ('perl', {'perl5opt'}), ('Perl5.44.exe', {'perl5opt'}),
            ('perl5.42', {'perl5opt'}), ('php', {'phprc', 'php_ini_scan_dir'}),
            ('PHP8.5.exe', {'phprc', 'php_ini_scan_dir'}), ('php8.3', {'phprc', 'php_ini_scan_dir'}),
            ('ruby3.4.exe', {'rubyopt', 'rubylib', 'rubygems_gemdeps'}), ('nodejs.exe', {'node_options'}),
        )
        for executable, blocked in cases:
            self.assert_filtered(executable, blocked, autonomy)

    def test_ordinary_frozen_launch_filters_interpreter_startup_environment(self):
        self.assert_all_interpreters(autonomy=False)

    def test_trusted_autonomy_frozen_launch_filters_interpreter_startup_environment(self):
        self.assert_all_interpreters(autonomy=True)

    def test_native_bash_launch_ignores_late_debugger_and_startup_options(self):
        bash = shutil.which('bash')
        if os.name == 'nt' and bash and 'WindowsApps' in bash:
            bash = None  # WSL app alias is not a native Bash interpreter.
        if bash is None and os.name == 'nt':
            candidate = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe'
            bash = str(candidate) if candidate.is_file() else None
        if bash is None:
            self.skipTest('Native Bash unavailable')
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hook = root / 'late-startup.sh'
            hook.write_text('echo ambient-startup-executed; exit 9\n', encoding='utf-8')
            worker = root / 'worker.sh'
            worker.write_text('[[ :$BASHOPTS: != *:extdebug:* ]] || exit 10\n'
                              '[[ :$SHELLOPTS: != *:xtrace:* ]] || exit 11\n'
                              'printf frozen-bash-ok\n', encoding='utf-8')
            for autonomy in (False, True):
                with self.subTest(autonomy=autonomy), patch.dict(os.environ,
                        {'BASH_ENV': str(hook), 'BASHOPTS': 'extdebug', 'SHELLOPTS': 'xtrace'}):
                    before = dict(os.environ)
                    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                        namespace = {'argv': [bash, str(worker)],
                                     'current': {'autonomy_request': {}} if autonomy else {},
                                     'os': os, 'Path': Path, 're': re, 'subprocess': subprocess,
                                     'self': SimpleNamespace(root=root), 'flags': 0,
                                     'out': out, 'err': err, '__file__': str(SOURCE)}
                        exec(self.launch, namespace)
                        process = namespace['process']
                        try:
                            process.wait(timeout=8)
                        finally:
                            if process.poll() is None:
                                process.kill()
                                process.wait()
                        out.seek(0); err.seek(0)
                        self.assertEqual(process.returncode, 0, err.read())
                        self.assertEqual(out.read(), b'frozen-bash-ok')
                    self.assertEqual(dict(os.environ), before)


if __name__ == '__main__':
    unittest.main()

"""Frozen ProjectStore interpreter startup filtering; never invoke workers or hooks."""
import ast
import os
from pathlib import Path
import re
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
                  'bash_env': 'case-variant-bash-startup', 'BASHOPTS': 'retained-options',
                  'RUBYOPT': 'retained-ruby-control', 'NODE_OPTIONS': 'retained-node-control',
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
        all_startup = {'bash_env', 'perl5opt', 'phprc', 'php_ini_scan_dir', 'rubyopt', 'node_options'}
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
                self.assertEqual(env['BASHOPTS'], 'retained-options')
                expected_runtime = str(SOURCE.resolve().parent) if autonomy else 'parent-runtime'
                self.assertEqual(env['RDS_RUNTIME_SCRIPTS'], expected_runtime)
                self.assertEqual(parent, before)

    def assert_all_interpreters(self, autonomy):
        cases = (
            ('bash', {'bash_env'}), ('BASH.EXE', {'bash_env'}), ('BaSh5.2.exe', {'bash_env'}),
            ('bash-5.2', {'bash_env'}), ('perl', {'perl5opt'}), ('Perl5.44.exe', {'perl5opt'}),
            ('perl5.42', {'perl5opt'}), ('php', {'phprc', 'php_ini_scan_dir'}),
            ('PHP8.5.exe', {'phprc', 'php_ini_scan_dir'}), ('php8.3', {'phprc', 'php_ini_scan_dir'}),
            ('ruby3.4.exe', {'rubyopt'}), ('nodejs.exe', {'node_options'}),
        )
        for executable, blocked in cases:
            self.assert_filtered(executable, blocked, autonomy)

    def test_ordinary_frozen_launch_filters_interpreter_startup_environment(self):
        self.assert_all_interpreters(autonomy=False)

    def test_trusted_autonomy_frozen_launch_filters_interpreter_startup_environment(self):
        self.assert_all_interpreters(autonomy=True)


if __name__ == '__main__':
    unittest.main()

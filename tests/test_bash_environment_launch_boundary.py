"""Frozen ProjectStore Bash startup filtering; never invoke Bash or hooks."""
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


class BashEnvironmentLaunchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = launch_code()

    def observe(self, executable, autonomy):
        parent = {'PATH': 'retained-path', 'BASH_ENV': 'late-unbound-hook',
                  'bash_env': 'case-variant-hook', 'BASHOPTS': 'retained-options',
                  'RUBYOPT': 'retained-ruby-control', 'NODE_OPTIONS': 'retained-node-control',
                  'RDS_RUNTIME_SCRIPTS': 'parent-runtime'}
        before = dict(parent)
        captured = {}

        def popen(argv, **options):
            captured['argv'] = list(argv)
            captured['options'] = options
            return object()  # Observe the launch only; never invoke Bash or a hook.

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

    def assert_filtered_bash_env(self, autonomy):
        for executable in ('bash', 'BASH.EXE', 'BaSh5.2.exe', 'bash-5.2'):
            with self.subTest(executable=executable):
                captured, argv, parent, before = self.observe(executable, autonomy)
                options = captured['options']
                self.assertEqual(captured['argv'], argv)
                self.assertIs(options['shell'], False)
                self.assertFalse(any(key.casefold() == 'bash_env' for key in options['env']))
                for key, value in (('PATH', 'retained-path'), ('BASHOPTS', 'retained-options'),
                                   ('RUBYOPT', 'retained-ruby-control'),
                                   ('NODE_OPTIONS', 'retained-node-control')):
                    self.assertEqual(options['env'][key], value)
                expected_runtime = str(SOURCE.resolve().parent) if autonomy else 'parent-runtime'
                self.assertEqual(options['env']['RDS_RUNTIME_SCRIPTS'], expected_runtime)
                self.assertEqual(parent, before)

    def test_ordinary_frozen_launch_filters_post_admission_bash_env(self):
        self.assert_filtered_bash_env(autonomy=False)

    def test_trusted_autonomy_frozen_launch_filters_post_admission_bash_env(self):
        self.assert_filtered_bash_env(autonomy=True)


if __name__ == '__main__':
    unittest.main()

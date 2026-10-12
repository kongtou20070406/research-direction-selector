"""Inventory fixtures must not execute imports or hide unresolved dependencies."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("import_graph", ROOT / "tools/import_graph.py")
graph = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(graph)


class ImportGraphTests(unittest.TestCase):
    def fixture(self, files):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, text in files.items():
            path = root / "scripts" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return root

    def test_static_conditional_and_deferred_sites(self):
        root = self.fixture({"rds_project.py": "import rds_owned_advisor\nif False:\n import rds_quick\ndef f():\n from rds_owned_advisor import x\n",
                             "rds_owned_advisor.py": "raise RuntimeError('never execute')",
                             "rds_quick.py": ""})
        report = graph.inventory(root)
        self.assertEqual(report["counts"]["edges"], 2)
        self.assertEqual(report["counts"]["sites"], 3)
        self.assertEqual(report["counts"]["upward_edges"], 2)
        self.assertEqual(report["edges"][-1]["scope"], "f")
        self.assertEqual(report["unresolved"], [])

    def test_dynamic_alias_keyword_registry_and_unresolved(self):
        root = self.fixture({"rds_project.py": "from importlib import import_module as load\nimport importlib as il\nmodules={'x':'rds_quick'}\nload('rds_owned_advisor')\nil.import_module(name='rds_quick')\nload(modules[name])\nload(variable)\n__import__('rds_quick')\n",
                             "rds_owned_advisor.py": "", "rds_quick.py": ""})
        report = graph.inventory(root)
        self.assertEqual([e["kind"] for e in report["edges"]],
                         ["dynamic_literal", "dynamic_literal", "dynamic_registry_candidate", "dynamic_literal"])
        self.assertEqual(len(report["unresolved"]), 2)
        self.assertEqual(report["unresolved"][0]["candidates"], ["rds_quick"])

    def test_provider_calls_are_edges_not_just_hook_imports(self):
        root = self.fixture({"rds_project.py": "from rds_project_hooks import provider as p\np('advisor').check_admission()\np(name)\n",
                             "rds_project_hooks.py": "from importlib import import_module\n_modules={'advisor':'rds_owned_advisor'}\ndef provider(name):\n return import_module(_modules[name])\n",
                             "rds_owned_advisor.py": "import rds_project"})
        report = graph.inventory(root)
        self.assertIn(["rds_owned_advisor", "rds_project"], report["mutual_pairs"])
        self.assertEqual(len(report["cycles"]), 1)
        callback = [e for e in report["edges"] if e["kind"] == "provider_callback"]
        self.assertEqual(callback[0]["target"], "rds_owned_advisor")
        self.assertTrue(any(u["reason"] == "runtime provider selection" for u in report["unresolved"]))

    def test_module_loader_uses_preceding_import_not_later_rebinding(self):
        for source in (
            "from importlib import import_module as load\nload('rds_quick')\nload = None",
            "import importlib as load\nload.import_module('rds_quick')\nload = None",
            "from importlib import import_module as load\nfor item in items:\n load('rds_quick')\nload = None",
        ):
            with self.subTest(source=source):
                report = graph.inventory(self.fixture({'rds_project.py': source, 'rds_quick.py': ''}))
                self.assertEqual([e['target'] for e in report['edges']], ['rds_quick'])
                self.assertEqual(report['unresolved'], [])

    def test_module_loader_deferred_or_loop_rebinding_stays_unknown(self):
        for body in (
            "def run():\n load('rds_quick')\nload = None",
            "for item in items:\n load('rds_quick')\n load = None",
            "while condition:\n load('rds_quick')\n load = None",
        ):
            with self.subTest(body=body):
                source = "from importlib import import_module as load\n" + body
                report = graph.inventory(self.fixture({'rds_project.py': source, 'rds_quick.py': ''}))
                self.assertEqual(report['edges'], [])
                self.assertEqual([u['reason'] for u in report['unresolved']], ['ambiguous loader binding'])

    def test_builtin_import_before_later_module_binding_is_an_edge(self):
        for suffix in ("__import__ = other", "def __import__():\n pass",
                       "from helper import __import__"):
            with self.subTest(suffix=suffix):
                source = "__import__('rds_quick')\n" + suffix
                report = graph.inventory(self.fixture({'rds_project.py': source, 'rds_quick.py': ''}))
                self.assertEqual([e['target'] for e in report['edges']], ['rds_quick'])
                self.assertEqual(report['unresolved'], [])

    def test_rebound_global_builtin_keeps_uncertainty_not_a_definite_edge(self):
        for source in (
            "def run():\n __import__('rds_quick')\n__import__ = other",
            "for item in items:\n __import__('rds_quick')\n __import__ = other",
            "if condition:\n __import__ = other\n__import__('rds_quick')",
        ):
            with self.subTest(source=source):
                report = graph.inventory(self.fixture({'rds_project.py': source, 'rds_quick.py': ''}))
                self.assertEqual(report['edges'], [])
                self.assertEqual([u['reason'] for u in report['unresolved']], ['ambiguous loader binding'])

    def test_function_local_builtin_shadow_does_not_fall_back_to_builtin(self):
        root = self.fixture({'rds_project.py':
            "def run():\n __import__('rds_quick')\n __import__ = other", 'rds_quick.py': ''})
        report = graph.inventory(root)
        self.assertEqual(report['edges'], [])
        self.assertEqual(report['unresolved'], [])

    def test_assignment_rhs_uses_binding_before_target_store(self):
        for statement in ("load = load('rds_quick')", "load: object = load('rds_quick')",
                          "(load := load('rds_quick'))", "load, other = load('rds_quick')"):
            for scope in ('', 'class C:\n '):
                with self.subTest(statement=statement, scope=scope):
                    indent = ' ' if scope else ''
                    source = scope + "from importlib import import_module as load\n" + indent + statement
                    report = graph.inventory(self.fixture({'rds_project.py': source, 'rds_quick.py': ''}))
                    self.assertEqual([e['target'] for e in report['edges']], ['rds_quick'])
                    self.assertEqual(report['unresolved'], [])

    def test_assignment_rhs_in_rebinding_loop_remains_unknown(self):
        root = self.fixture({'rds_project.py':
            "from importlib import import_module as load\nfor item in items:\n load = load('rds_quick')",
            'rds_quick.py': ''})
        report = graph.inventory(root)
        self.assertEqual(report['edges'], [])
        self.assertEqual([u['reason'] for u in report['unresolved']], ['ambiguous loader binding'])

    def test_completed_nested_rhs_store_is_not_delayed_with_outer_assignment(self):
        root = self.fixture({'rds_project.py':
            "from importlib import import_module as load\nload = ((load := other), load('rds_quick'))",
            'rds_quick.py': ''})
        report = graph.inventory(root)
        self.assertEqual(report['edges'], [])
        self.assertEqual([u['reason'] for u in report['unresolved']], ['ambiguous loader binding'])

    def test_shadowed_registry_does_not_emit_module_table_candidates(self):
        for body in (
            "def run(name):\n modules = {'x': 'external'}\n return load(modules[name])",
            "def run(modules, name):\n return load(modules[name])",
            "class C:\n modules = {'x': 'external'}\n value = load(modules[name])",
            "def run(name):\n return load(modules[name])\nmodules = other",
            "modules = other\nload(modules[name])",
        ):
            with self.subTest(body=body):
                source = "from importlib import import_module as load\nmodules = {'x': 'rds_quick'}\n" + body
                report = graph.inventory(self.fixture({'rds_project.py': source, 'rds_quick.py': ''}))
                self.assertEqual(report['edges'], [])
                self.assertEqual([u['candidates'] for u in report['unresolved']], [[]])

    def test_unshadowed_registry_still_emits_candidates(self):
        for body in ("load(modules[name])", "def run(name):\n return load(modules[name])",
                     "class C:\n value = load(modules[name])"):
            with self.subTest(body=body):
                source = "from importlib import import_module as load\nmodules = {'x': 'rds_quick'}\n" + body
                report = graph.inventory(self.fixture({'rds_project.py': source, 'rds_quick.py': ''}))
                self.assertEqual([(e['target'], e['kind']) for e in report['edges']],
                                 [('rds_quick', 'dynamic_registry_candidate')])
                self.assertEqual(report['unresolved'][0]['candidates'], ['rds_quick'])

    def test_registry_uses_assignment_identity_not_last_same_spelled_table(self):
        root = self.fixture({'rds_project.py':
            "from importlib import import_module as load\nmodules = {'x': 'rds_quick'}\n"
            "load(modules[name])\nmodules = {'x': 'rds_owned_advisor'}\nload(modules[name])",
            'rds_quick.py': '', 'rds_owned_advisor.py': ''})
        report = graph.inventory(root)
        self.assertEqual([e['target'] for e in report['edges']], ['rds_quick', 'rds_owned_advisor'])
        self.assertEqual([u['candidates'] for u in report['unresolved']],
                         [['rds_quick'], ['rds_owned_advisor']])

    def test_deferred_registry_multiple_bindings_remain_unknown(self):
        root = self.fixture({'rds_project.py':
            "from importlib import import_module as load\nmodules = {'x': 'rds_quick'}\n"
            "def run(name):\n return load(modules[name])\nmodules = {'x': 'external'}",
            'rds_quick.py': ''})
        report = graph.inventory(root)
        self.assertEqual(report['edges'], [])
        self.assertEqual(report['unresolved'][0]['candidates'], [])

    def test_aliases_in_unrelated_scopes_do_not_leak(self):
        sources = [
            "from importlib import import_module as load\nload('rds_quick')\ndef f():\n from json import loads as load",
            "from json import loads as load\nload('rds_quick')\ndef f():\n from importlib import import_module as load",
        ]
        for source, expected in zip(sources, [1, 0]):
            root = self.fixture({"rds_project.py": source, "rds_quick.py": ""})
            report = graph.inventory(root)
            self.assertEqual(len(report["edges"]), expected)
            self.assertEqual(report["unresolved"], [])

    def test_parameter_shadowing_and_ambiguous_rebinding(self):
        root = self.fixture({"rds_project.py": "from importlib import import_module as load\ndef f(load):\n load('rds_quick')\n",
                             "rds_quick.py": ""})
        self.assertEqual(graph.inventory(root)["edges"], [])
        (root / 'scripts/rds_project.py').write_text("from importlib import import_module as load\nload=other\nload('rds_quick')", encoding='utf-8')
        self.assertEqual(graph.inventory(root)["unresolved"][0]["reason"], "ambiguous loader binding")

    def test_wildcard_import_module_remains_unresolved(self):
        for imports in ("from importlib import *\n",
                        "from importlib import *\nfrom other import *\n",
                        "from importlib import import_module\nfrom other import *\n"):
            with self.subTest(imports=imports):
                root = self.fixture({'rds_project.py': imports + "import_module('rds_quick')",
                                     'rds_quick.py': ''})
                report = graph.inventory(root)
                self.assertEqual(report['edges'], [])
                self.assertEqual([u['reason'] for u in report['unresolved']],
                                 ['ambiguous loader binding'])

    def test_wildcard_loader_respects_function_shadowing(self):
        root = self.fixture({'rds_project.py':
            "from importlib import *\ndef f(import_module):\n import_module('rds_quick')\n"
            "def g():\n import_module('rds_quick')",
            'rds_quick.py': ''})
        report = graph.inventory(root)
        self.assertEqual(report['edges'], [])
        self.assertEqual([(u['scope'], u['reason']) for u in report['unresolved']],
                         [('g', 'ambiguous loader binding')])

    def test_wildcard_reexported_loader_is_explicitly_unknown(self):
        root = self.fixture({'helper.py': 'from importlib import import_module',
                             'consumer.py': "from helper import *\nimport_module('rds_quick')",
                             'rds_quick.py': ''})
        report = graph.inventory(root)
        self.assertFalse([e for e in report['edges'] if e['kind'] == 'dynamic_literal'])
        self.assertEqual([(u['source'], u['reason']) for u in report['unresolved']],
                         [('consumer', 'ambiguous loader binding')])

    def test_definition_expressions_use_outer_scope(self):
        definitions = [
            "def f(x=load('rds_quick')):\n load = None",
            "def f(*, x=load('rds_quick')):\n load = None",
            "def f(x: load('rds_quick')):\n load = None",
            "def f() -> load('rds_quick'):\n load = None",
            "@load('rds_quick')\ndef f():\n load = None",
            "async def f(x=load('rds_quick')):\n load = None",
            "f = lambda load=load('rds_quick'): load",
            "class C(load('rds_quick')):\n load = None",
            "class C(metaclass=load('rds_quick')):\n load = None",
            "@load('rds_quick')\nclass C:\n load = None",
        ]
        for definition in definitions:
            with self.subTest(definition=definition):
                root = self.fixture({"rds_project.py": "from importlib import import_module as load\n" + definition,
                                     "rds_quick.py": ""})
                report = graph.inventory(root)
                self.assertEqual([(e['target'], e['kind']) for e in report['edges']],
                                 [('rds_quick', 'dynamic_literal')])
                self.assertEqual(report['unresolved'], [])

    def test_exception_target_does_not_inherit_loader_binding(self):
        root = self.fixture({"rds_project.py": "from importlib import import_module as load\ntry:\n pass\nexcept Exception as load:\n load('rds_quick')",
                             "rds_quick.py": ""})
        report = graph.inventory(root)
        self.assertEqual(report['edges'], [])
        self.assertEqual(report['unresolved'][0]['reason'], 'ambiguous loader binding')

    def test_match_pattern_captures_shadow_loader_aliases(self):
        for pattern in ('{"loader": load}', '[*load]', '{**load}'):
            with self.subTest(pattern=pattern):
                root = self.fixture({'rds_project.py':
                    "from importlib import import_module as load\n"
                    "match value:\n case " + pattern + ":\n  load('rds_quick')",
                    'rds_quick.py': ''})
                report = graph.inventory(root)
                self.assertEqual(report['edges'], [])
                self.assertEqual(report['unresolved'][0]['reason'], 'ambiguous loader binding')

    def test_class_body_rebinding_keeps_outer_loader_uncertainty(self):
        for body in ("value = load('rds_quick'); load = None",
                     "load = None; value = load('rds_quick')",
                     "if condition:\n  load = None\n value = load('rds_quick')"):
            with self.subTest(body=body):
                root = self.fixture({'rds_project.py':
                    "from importlib import import_module as load\nclass C:\n " + body,
                    'rds_quick.py': ''})
                report = graph.inventory(root)
                self.assertEqual(report['edges'], [])
                self.assertEqual(report['unresolved'][0]['reason'], 'ambiguous loader binding')

    def test_preceding_class_import_is_a_definite_loader(self):
        for body in (
            "from importlib import import_module as load; value = load('rds_quick')",
            "import importlib as loader; value = loader.import_module('rds_quick')",
            "from importlib import import_module as load\n value = load('rds_quick')\n load = None",
            "if condition:\n  load = None\n from importlib import import_module as load\n value = load('rds_quick')",
        ):
            with self.subTest(body=body):
                root = self.fixture({'rds_project.py': "class C:\n " + body,
                                     'rds_quick.py': ''})
                report = graph.inventory(root)
                self.assertEqual([e['target'] for e in report['edges']], ['rds_quick'])
                self.assertEqual(report['unresolved'], [])

    def test_class_import_with_intervening_mutation_remains_unknown(self):
        for mutation in ("if condition:\n  load = other", "del load", "load = other",
                         "from helper import *"):
            with self.subTest(mutation=mutation):
                root = self.fixture({'rds_project.py':
                    "class C:\n from importlib import import_module as load\n " + mutation +
                    "\n value = load('rds_quick')", 'rds_quick.py': ''})
                report = graph.inventory(root)
                self.assertEqual(report['edges'], [])
                self.assertEqual(report['unresolved'][0]['reason'], 'ambiguous loader binding')

    def test_class_loop_rebinding_remains_unknown(self):
        root = self.fixture({'rds_project.py':
            "class C:\n from importlib import import_module as load\n"
            " for item in items:\n  load('rds_quick')\n  load = other\n",
            'rds_quick.py': ''})
        report = graph.inventory(root)
        self.assertEqual(report['edges'], [])
        self.assertEqual(report['unresolved'][0]['reason'], 'ambiguous loader binding')

    def test_class_non_mutating_loop_keeps_definite_loader(self):
        for loop in ('for item in items:', 'while condition:'):
            with self.subTest(loop=loop):
                root = self.fixture({'rds_project.py':
                    "class C:\n from importlib import import_module as load\n " + loop +
                    "\n  load('rds_quick')\n", 'rds_quick.py': ''})
                report = graph.inventory(root)
                self.assertEqual([e['target'] for e in report['edges']], ['rds_quick'])
                self.assertEqual(report['unresolved'], [])

    def test_class_loader_does_not_leak_into_method_scope(self):
        root = self.fixture({'rds_project.py':
            "class C:\n from importlib import import_module as load\n"
            " def method(self):\n  load('rds_quick')\n", 'rds_quick.py': ''})
        self.assertEqual(graph.inventory(root)['edges'], [])

    def test_literal_run_module_targets_are_edges(self):
        for call in ("runpy.run_module('rds_quick')",
                     "runpy.run_module(mod_name='rds_quick')",
                     "run(mod_name='rds_quick')"):
            with self.subTest(call=call):
                root = self.fixture({'rds_project.py':
                    "import runpy\nfrom runpy import run_module as run\n" + call,
                    'rds_quick.py': ''})
                report = graph.inventory(root)
                self.assertEqual([(e['target'], e['kind']) for e in report['edges']],
                                 [('rds_quick', 'dynamic_literal')])
                self.assertEqual(report['unresolved'], [])

    def test_computed_run_module_and_file_targets_remain_unknown(self):
        root = self.fixture({'rds_project.py':
            "import runpy\nrunpy.run_module(name)\nrunpy.run_module(**options)\n"
            "runpy.run_path('rds_quick.py')", 'rds_quick.py': ''})
        report = graph.inventory(root)
        self.assertEqual(report['edges'], [])
        self.assertEqual(len(report['unresolved']), 3)

    def test_comprehension_targets_do_not_shadow_enclosing_loaders(self):
        expressions = ["[x for load in loaders]", "{x for load in loaders}",
                       "{x: x for load in loaders}", "(x for load in loaders)"]
        for expression in expressions:
            with self.subTest(expression=expression):
                root = self.fixture({'rds_project.py':
                    "from importlib import import_module as load\ndef f():\n"
                    " load('rds_quick')\n " + expression, 'rds_quick.py': ''})
                report = graph.inventory(root)
                self.assertEqual([e['target'] for e in report['edges']], ['rds_quick'])
                self.assertEqual(report['unresolved'], [])

    def test_comprehension_first_iterable_is_outer_but_body_is_local(self):
        root = self.fixture({'rds_project.py':
            "from importlib import import_module as load\n"
            "[load('rds_quick') for load in load('rds_quick')]",
            'rds_quick.py': ''})
        report = graph.inventory(root)
        self.assertEqual([e['target'] for e in report['edges']], ['rds_quick'])
        self.assertEqual(report['unresolved'], [])

    def test_comprehension_assignment_expression_binds_enclosing_scope(self):
        root = self.fixture({'rds_project.py':
            "from importlib import import_module as load\n"
            "[(load := other) for x in items]\nload('rds_quick')",
            'rds_quick.py': ''})
        report = graph.inventory(root)
        self.assertEqual(report['edges'], [])
        self.assertEqual(report['unresolved'][0]['reason'], 'ambiguous loader binding')

    def test_keyword_provider_matches_positional_edge(self):
        root = self.fixture({"rds_project.py": "from rds_project_hooks import provider\nprovider(name='advisor')",
                             "rds_project_hooks.py": "_modules={'advisor':'rds_owned_advisor'}",
                             "rds_owned_advisor.py": ""})
        callbacks = [e for e in graph.inventory(root)['edges'] if e['kind'] == 'provider_callback']
        self.assertEqual([e['target'] for e in callbacks], ['rds_owned_advisor'])

    def test_keyword_proof_rule_records_target_or_unknown(self):
        root = self.fixture({"rds_verify.py": "from rds_verify_registry import ProofRule\nProofRule(name='x', kinds=('k',), module='rds_quick')\nProofRule(name='x', kinds=('k',), module=unknown)",
                             "rds_quick.py": ""})
        report = graph.inventory(root)
        self.assertEqual([(e['target'], e['kind']) for e in report['edges']],
                         [('rds_quick', 'declared_plugin_candidate')])
        self.assertEqual(report['unresolved'][0]['reason'], 'runtime plugin declaration')

    def test_module_local_proof_rule_class_declarations_and_shadowing(self):
        root = self.fixture({'rds_verify.py':
            "from dataclasses import dataclass\n"
            "from rds_verify_registry import get_verifier\n"
            "@dataclass(frozen=True)\nclass ProofRule:\n"
            " name: str\n kinds: tuple\n module: str\n"
            " version: str = '1'\n support_files: tuple = ()\n"
            "class RuleRegistry:\n def __init__(self, rules):\n  self.rules = rules\n"
            "REGISTRY = RuleRegistry((ProofRule('x', ('k',), 'rds_quick'),\n"
            " ProofRule(name='y', kinds=('j',), module='rds_quick')))\n"
            "def f(ProofRule):\n ProofRule('x', ('k',), 'rds_quick')\n"
            "def g():\n ProofRule('x', ('k',), 'rds_quick')\n ProofRule = other\n"
            "def h():\n from other import ProofRule\n ProofRule('x', ('k',), 'rds_quick')\n"
            "class Nested:\n class ProofRule:\n  pass\n"
            " value = ProofRule('x', ('k',), 'rds_quick')\n",
            'rds_verify_registry.py': '', 'rds_quick.py': ''})
        report = graph.inventory(root)
        declarations = [e for e in report['edges'] if e['kind'] == 'declared_plugin_candidate']
        self.assertEqual([(e['target'], e['scope']) for e in declarations],
                         [('rds_quick', ''), ('rds_quick', '')])
        self.assertFalse([u for u in report['unresolved'] if u['scope'] in ('f', 'g', 'h')])
        self.assertEqual([(u['scope'], u['reason']) for u in report['unresolved']],
                         [('Nested', 'ambiguous plugin binding')])

    def test_module_local_proof_rule_rebinding_is_unknown(self):
        root = self.fixture({'rds_verify.py':
            "class ProofRule:\n pass\nProofRule = other\n"
            "ProofRule('x', ('k',), 'rds_quick')", 'rds_quick.py': ''})
        report = graph.inventory(root)
        self.assertEqual(report['edges'], [])
        self.assertEqual([u['reason'] for u in report['unresolved']],
                         ['ambiguous plugin binding'])

    def test_proof_rule_shadowing_does_not_declare_plugins(self):
        for definition in (
                "def f(ProofRule):\n ProofRule('x', ('k',), 'rds_quick')",
                "def f():\n ProofRule('x', ('k',), 'rds_quick')\n ProofRule = other",
                "def f():\n from other import ProofRule\n ProofRule('x', ('k',), 'rds_quick')"):
            with self.subTest(definition=definition):
                root = self.fixture({'rds_verify.py':
                    "from rds_verify_registry import ProofRule\n" + definition,
                    'rds_quick.py': ''})
                report = graph.inventory(root)
                self.assertEqual(report['edges'], [])
                self.assertEqual(report['unresolved'], [])

    def test_proof_rule_alias_and_qualified_bindings_declare_plugins(self):
        for source in (
                "from rds_verify_registry import ProofRule as Rule\nRule('x', ('k',), 'rds_quick')",
                "import rds_verify_registry as registry\nregistry.ProofRule('x', ('k',), 'rds_quick')",
                "from rds_verify_registry import ProofRule\ndef f(ProofRule=ProofRule('x', ('k',), 'rds_quick')):\n pass"):
            with self.subTest(source=source):
                root = self.fixture({'rds_verify.py': source, 'rds_quick.py': ''})
                report = graph.inventory(root)
                self.assertEqual([(e['target'], e['kind']) for e in report['edges']],
                                 [('rds_quick', 'declared_plugin_candidate')])
                self.assertEqual(report['unresolved'], [])

    def test_ambiguous_proof_rule_binding_remains_unresolved(self):
        for source in (
                "from rds_verify_registry import ProofRule\nProofRule = other\nProofRule('x', ('k',), 'rds_quick')",
                "from rds_verify_registry import *\nProofRule('x', ('k',), 'rds_quick')",
                "from rds_verify_registry import ProofRule\nclass C:\n ProofRule = other\n value = ProofRule('x', ('k',), 'rds_quick')"):
            with self.subTest(source=source):
                root = self.fixture({'rds_verify.py': source, 'rds_quick.py': ''})
                report = graph.inventory(root)
                self.assertEqual(report['edges'], [])
                self.assertEqual([u['reason'] for u in report['unresolved']],
                                 ['ambiguous plugin binding'])

    def test_unbound_proof_rule_is_unknown_not_declared(self):
        root = self.fixture({'rds_verify.py': "ProofRule('x', ('k',), 'rds_quick')",
                             'rds_quick.py': ''})
        report = graph.inventory(root)
        self.assertEqual(report['edges'], [])
        self.assertEqual([u['reason'] for u in report['unresolved']],
                         ['unresolved plugin binding'])

    def test_import_level_is_not_mistaken_for_absolute_import(self):
        root = self.fixture({"rds_project.py": "__import__('rds_quick', level=1)\n__import__('rds_quick', globals(), locals(), [], 1)\n__import__('rds_quick', level=unknown)\n__import__('rds_quick', level=0)",
                             "rds_quick.py": ""})
        report = graph.inventory(root)
        self.assertEqual(len(report["unresolved"]), 3)
        self.assertEqual(len(report["edges"]), 1)

    def test_unpacking_import_level_is_unknown(self):
        for call in ("__import__('child', globals(), locals(), (), **{'level': 1})",
                     "__import__('child', **options)",
                     "__import__('child', level=0, **options)"):
            with self.subTest(call=call):
                root = self.fixture({'consumer.py': call, 'child.py': ''})
                report = graph.inventory(root)
                self.assertEqual(report['edges'], [])
                self.assertEqual([u['reason'] for u in report['unresolved']],
                                 ['relative or unknown import level'])

    def test_starred_import_level_is_unknown(self):
        for call in ("__import__('child', *[globals(), locals(), (), 1])",
                     "__import__('child', *arguments)",
                     "__import__('child', *arguments, level=0)"):
            with self.subTest(call=call):
                root = self.fixture({'pkg/__init__.py': '', 'pkg/consumer.py': call,
                                     'pkg/child.py': '', 'child.py': ''})
                report = graph.inventory(root)
                self.assertEqual(report['edges'], [])
                self.assertEqual([u['reason'] for u in report['unresolved']],
                                 ['relative or unknown import level'])

    def test_import_fromlist_retains_local_member_cycle_candidates(self):
        for call in ("__import__('pkg', fromlist=['child'])",
                     "__import__('pkg', globals(), locals(), ('child',))",
                     "__import__(name='pkg', fromlist=['child', 'child', 'attribute'])"):
            with self.subTest(call=call):
                root = self.fixture({'consumer.py': call, 'pkg/__init__.py': '',
                                     'pkg/child.py': 'import consumer'})
                report = graph.inventory(root)
                self.assertEqual([(e['target'], e['kind']) for e in report['edges']
                                  if e['source'] == 'consumer'],
                                 [('pkg', 'dynamic_literal'), ('pkg.child', 'dynamic_fromlist_candidate')])
                self.assertEqual(report['cycles'], [['consumer', 'pkg.child']])
                self.assertEqual(report['unresolved'], [])

    def test_computed_or_star_fromlist_keeps_base_edge_and_unknown(self):
        for value in ("names", "['*']", "[name]", "[1]", "'child'"):
            with self.subTest(value=value):
                root = self.fixture({'consumer.py': "__import__('pkg', fromlist=" + value + ")",
                                     'pkg/__init__.py': '', 'pkg/child.py': ''})
                report = graph.inventory(root)
                self.assertEqual([(e['source'], e['target']) for e in report['edges']], [('consumer', 'pkg')])
                self.assertEqual([u['reason'] for u in report['unresolved']], ['runtime import fromlist'])

    def test_empty_fromlist_does_not_invent_member_dependencies(self):
        for value in ("[]", "()", "None"):
            with self.subTest(value=value):
                root = self.fixture({'consumer.py': "__import__('pkg', fromlist=" + value + ")",
                                     'pkg/__init__.py': '', 'pkg/child.py': ''})
                report = graph.inventory(root)
                self.assertEqual([e['target'] for e in report['edges']], ['pkg'])
                self.assertEqual(report['unresolved'], [])

    def test_relative_imports_and_package_members(self):
        root = self.fixture({"pkg/__init__.py": "from . import child", "pkg/child.py": "from .. import invalid",
                             "consumer.py": "from pkg import child"})
        report = graph.inventory(root)
        pairs = {(e["source"], e["target"]) for e in report["edges"]}
        self.assertIn(("consumer", "pkg.child"), pairs)
        self.assertIn(("pkg", "pkg.child"), pairs)
        self.assertEqual(len(report["unresolved"]), 1)
        self.assertTrue(report["unclassified_edges"])

    def test_relative_package_member_does_not_create_self_cycle(self):
        root = self.fixture({'pkg/__init__.py': 'from . import child', 'pkg/child.py': ''})
        report = graph.inventory(root)
        self.assertEqual([(e['source'], e['target']) for e in report['edges']], [('pkg', 'pkg.child')])
        self.assertEqual(report['cycles'], [])

    def test_explicit_self_import_remains_a_cycle(self):
        root = self.fixture({'pkg/__init__.py': 'from pkg import member'})
        report = graph.inventory(root)
        self.assertEqual([(e['source'], e['target']) for e in report['edges']], [('pkg', 'pkg')])
        self.assertEqual(report['cycles'], [['pkg']])

    def test_string_is_reference_not_executed_edge(self):
        root = self.fixture({"rds_project.py": "module='rds_quick'", "rds_quick.py": ""})
        report = graph.inventory(root)
        self.assertEqual(report["edges"], [])
        self.assertEqual(len(report["module_string_references"]), 1)

    def test_declared_builtin_targets_do_not_hide_dynamic_uncertainty(self):
        root = self.fixture({"rds_verify.py": "import importlib\nfrom rds_verify_registry import ProofRule\nProofRule('rule', ('kind',), 'rds_lean_verify')\nimportlib.import_module(rule.module)",
                             "rds_lean_verify.py": ""})
        report = graph.inventory(root)
        self.assertEqual(report["edges"][0]["kind"], "declared_plugin_candidate")
        self.assertEqual(report["edges"][0]["target"], "rds_lean_verify")
        self.assertEqual(len(report["unresolved"]), 1)

    def test_opaque_loaders_and_relative_dynamic_remain_unknown(self):
        root = self.fixture({"rds_project.py": "import importlib.util as u\nfrom importlib import import_module\nu.spec_from_file_location('x', path)\nimport_module('.other', 'pkg')\nloader.exec_module(mod)"})
        self.assertEqual(len(graph.inventory(root)["unresolved"]), 3)

    def test_fingerprint_is_order_and_root_independent_but_content_sensitive(self):
        files = {"rds_project.py": "import rds_core", "rds_core.py": ""}
        one, two = self.fixture(files), self.fixture(dict(reversed(list(files.items()))))
        first = graph.inventory(one)
        self.assertEqual(first, graph.inventory(two))
        (two / "scripts/rds_core.py").write_text("# changed\n", encoding="utf-8")
        self.assertNotEqual(first["source_sha256"], graph.inventory(two)["source_sha256"])

    def test_shared_contracts_are_not_exempt_from_upward_edges(self):
        root = self.fixture({"rds_core.py": "import rds_project", "rds_project.py": ""})
        self.assertEqual(graph.inventory(root)["counts"]["upward_edges"], 1)

    def test_cli_bad_sources_fail_instead_of_empty_success(self):
        for files in ({}, {"bad.py": "def invalid("}):
            with self.subTest(files=files):
                root = self.fixture(files)
                result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/import_graph.py"), "--root", str(root)],
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_real_repository_is_covered(self):
        report = graph.inventory(ROOT)
        self.assertFalse([n for n, m in report["modules"].items() if m["layer"] == "UNKNOWN"])
        self.assertEqual(len(report["modules"]), len(list((ROOT / "scripts").rglob("*.py"))))
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(len(report["source_sha256"]), 64)
        self.assertEqual(json.loads(json.dumps(report)), report)

    def test_real_proof_rule_inventory_retains_every_declaration(self):
        tree = ast.parse((ROOT / 'scripts/rds_verify.py').read_bytes())
        registry = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'REGISTRY' for t in node.targets))
        declarations = [node for node in ast.walk(registry) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == 'ProofRule']
        expected = sorted((node.lineno, graph.call_argument(node, 2, 'module').value)
                          for node in declarations)
        self.assertEqual(len(expected), 18)
        report = graph.inventory(ROOT)
        actual = sorted((e['line'], e['target']) for e in report['edges']
                        if e['source'] == 'rds_verify' and e['kind'] == 'declared_plugin_candidate')
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()

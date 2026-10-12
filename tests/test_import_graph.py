"""Inventory fixtures must not execute imports or hide unresolved dependencies."""
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

    def test_keyword_provider_matches_positional_edge(self):
        root = self.fixture({"rds_project.py": "from rds_project_hooks import provider\nprovider(name='advisor')",
                             "rds_project_hooks.py": "_modules={'advisor':'rds_owned_advisor'}",
                             "rds_owned_advisor.py": ""})
        callbacks = [e for e in graph.inventory(root)['edges'] if e['kind'] == 'provider_callback']
        self.assertEqual([e['target'] for e in callbacks], ['rds_owned_advisor'])

    def test_keyword_proof_rule_records_target_or_unknown(self):
        root = self.fixture({"rds_verify.py": "ProofRule(name='x', kinds=('k',), module='rds_quick')\nProofRule(name='x', kinds=('k',), module=unknown)",
                             "rds_quick.py": ""})
        report = graph.inventory(root)
        self.assertEqual([(e['target'], e['kind']) for e in report['edges']],
                         [('rds_quick', 'declared_plugin_candidate')])
        self.assertEqual(report['unresolved'][0]['reason'], 'runtime plugin declaration')

    def test_import_level_is_not_mistaken_for_absolute_import(self):
        root = self.fixture({"rds_project.py": "__import__('rds_quick', level=1)\n__import__('rds_quick', globals(), locals(), [], 1)\n__import__('rds_quick', level=unknown)\n__import__('rds_quick', level=0)",
                             "rds_quick.py": ""})
        report = graph.inventory(root)
        self.assertEqual(len(report["unresolved"]), 3)
        self.assertEqual(len(report["edges"]), 1)

    def test_relative_imports_and_package_members(self):
        root = self.fixture({"pkg/__init__.py": "from . import child", "pkg/child.py": "from .. import invalid",
                             "consumer.py": "from pkg import child"})
        report = graph.inventory(root)
        pairs = {(e["source"], e["target"]) for e in report["edges"]}
        self.assertIn(("consumer", "pkg.child"), pairs)
        self.assertIn(("pkg", "pkg.child"), pairs)
        self.assertEqual(len(report["unresolved"]), 1)
        self.assertTrue(report["unclassified_edges"])

    def test_string_is_reference_not_executed_edge(self):
        root = self.fixture({"rds_project.py": "module='rds_quick'", "rds_quick.py": ""})
        report = graph.inventory(root)
        self.assertEqual(report["edges"], [])
        self.assertEqual(len(report["module_string_references"]), 1)

    def test_declared_builtin_targets_do_not_hide_dynamic_uncertainty(self):
        root = self.fixture({"rds_verify.py": "import importlib\nProofRule('rule', ('kind',), 'rds_lean_verify')\nimportlib.import_module(rule.module)",
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


if __name__ == "__main__":
    unittest.main()

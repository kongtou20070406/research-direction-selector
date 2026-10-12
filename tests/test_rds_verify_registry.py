"""Unified verifier registration, dispatch and fail-closed input handling."""
import ast
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import rds_core
import rds_verify_registry as registry
from rds_verify_registry import VerifierSpec, get_verifier, register_verifier


def make_spec(name, kinds, semantics="declared_rational_semantics", assurance="CERTIFICATE_CHECKED"):
    """Build a fresh VerifierSpec subclass so each test owns its routing."""
    return type(name, (VerifierSpec,), {
        "name": name, "kinds": set(kinds), "semantics": semantics,
        "assurance_level": assurance,
        "read_spec": lambda self, spec: spec,
        "verify": lambda self, spec: {"status": "PASS"},
        "check_certificate": lambda self, spec, certificate: bool(certificate),
    })


class RegistryIsolatedTest(unittest.TestCase):
    """Restore the process-global registry after every routing mutation."""

    def setUp(self):
        snapshot = (dict(registry._REGISTRY), dict(registry._BY_NAME))
        self.addCleanup(self._restore, snapshot)
        registry._REGISTRY.clear()
        registry._BY_NAME.clear()

    def _restore(self, snapshot):
        registry._REGISTRY.clear()
        registry._REGISTRY.update(snapshot[0])
        registry._BY_NAME.clear()
        registry._BY_NAME.update(snapshot[1])


class RegistrationTests(RegistryIsolatedTest):
    def test_registration_routes_every_declared_kind(self):
        @register_verifier
        class Scalar(VerifierSpec):
            name = "extension_threshold"
            kinds = {"extension_threshold", "extension_bounds"}
            semantics = "exact_real_scalar_with_rational_parameters"
            assurance_level = "CERTIFICATE_CHECKED"

            def read_spec(self, spec):
                return spec

            def verify(self, spec):
                return {"status": "PASS"}

            def check_certificate(self, spec, certificate):
                return bool(certificate)

        self.assertIs(get_verifier("extension_threshold"), get_verifier("extension_bounds"))
        self.assertIsInstance(get_verifier("extension_threshold"), Scalar)
        self.assertEqual(get_verifier("extension_bounds").semantics,
                         "exact_real_scalar_with_rational_parameters")
        self.assertEqual(get_verifier("extension_threshold").assurance_level, "CERTIFICATE_CHECKED")
        self.assertEqual(registry.registered_kinds(), ("extension_bounds", "extension_threshold"))
        self.assertIn("extension_threshold", registry.registered_kinds())

    def test_describe_and_registry_id_are_stable_and_declaration_bound(self):
        first = make_spec("alpha_kind", {"alpha_kind"})
        second = make_spec("beta_kind", {"beta_kind"})
        register_verifier(second)
        register_verifier(first)
        described = registry.describe()
        self.assertEqual([row["name"] for row in described], ["alpha_kind", "beta_kind"])
        self.assertEqual(described[0]["kinds"], ["alpha_kind"])
        self.assertNotIn("body", described[0])
        stable = registry.registry_id()
        self.assertEqual(stable, registry.registry_id())
        register_verifier(make_spec("gamma_kind", {"gamma_kind"}))
        self.assertNotEqual(stable, registry.registry_id())

    def test_default_version_is_one_and_declared_version_is_retained(self):
        register_verifier(make_spec("plain_kind", {"plain_kind"}))
        self.assertEqual(get_verifier("plain_kind").version, "1")
        register_verifier(make_spec("versioned_kind", {"versioned_kind"}))
        registry.get_verifier("versioned_kind").version = "2"
        self.assertEqual(get_verifier("versioned_kind").version, "2")

    def test_missing_name_falls_back_to_the_class_name(self):
        class UnnamedVerifier(VerifierSpec):
            kinds = {"unnamed_kind"}
            semantics = "unnamed_scope"
            assurance_level = "AST_ONLY"

            def read_spec(self, spec):
                return spec

            def verify(self, spec):
                return {"status": "PASS"}

            def check_certificate(self, spec, certificate):
                return True

        register_verifier(UnnamedVerifier)
        self.assertEqual(get_verifier("unnamed_kind").name, "UnnamedVerifier")
        self.assertEqual(registry.describe()[-1]["name"], "UnnamedVerifier")

    def test_unregister_removes_every_owned_kind_and_name(self):
        verifier = make_spec("multi_kind", {"multi_a", "multi_b"})
        register_verifier(verifier)
        instance = get_verifier("multi_a")
        self.assertIs(instance, get_verifier("multi_b"))
        self.assertIs(registry.unregister_verifier("multi_a"), instance)
        with self.assertRaisesRegex(ValueError, "Unsupported verifier kind: multi_a"):
            get_verifier("multi_a")
        with self.assertRaisesRegex(ValueError, "Unsupported verifier kind: multi_b"):
            get_verifier("multi_b")
        self.assertNotIn(verifier, registry.registered_verifiers())


class DuplicateAndAmbiguityTests(RegistryIsolatedTest):
    def test_duplicate_name_is_rejected(self):
        register_verifier(make_spec("dup_name", {"dup_name"}))
        with self.assertRaisesRegex(ValueError, "Duplicate verifier name: dup_name"):
            register_verifier(make_spec("dup_name", {"dup_name_other"}))
        self.assertNotIn("dup_name_other", registry.registered_kinds())

    def test_ambiguous_kind_is_rejected_without_partial_registration(self):
        register_verifier(make_spec("owner", {"shared_kind"}))
        with self.assertRaisesRegex(ValueError, "Ambiguous verifier kind: shared_kind"):
            register_verifier(make_spec("challenger", {"shared_kind", "fresh_kind"}))
        # The rejected registration must not leave its free kind routed.
        self.assertNotIn("fresh_kind", registry.registered_kinds())
        self.assertEqual(get_verifier("shared_kind").name, "owner")

    def test_self_duplicate_kind_set_is_accepted_once(self):
        register_verifier(make_spec("unique_kind", {"unique_kind"}))
        self.assertEqual(registry.registered_kinds().count("unique_kind"), 1)


class DispatchTests(RegistryIsolatedTest):
    def test_dispatch_selects_the_declared_reader_and_checker(self):
        calls = []

        class Recording(VerifierSpec):
            name = "recording_kind"
            kinds = {"recording_kind"}
            semantics = "recording_scope"
            assurance_level = "AST_ONLY"

            def read_spec(self, spec):
                calls.append("read")
                return {"normalized": spec}

            def verify(self, spec):
                calls.append("verify")
                return {"status": "PASS", "spec": self.read_spec(spec)}

            def check_certificate(self, spec, certificate):
                calls.append("check")
                return certificate == {"ok": True}

        register_verifier(Recording)
        verifier = get_verifier("recording_kind")
        self.assertEqual(verifier.verify({"kind": "recording_kind"})["status"], "PASS")
        self.assertTrue(verifier.check_certificate({}, {"ok": True}))
        self.assertFalse(verifier.check_certificate({}, {"ok": False}))
        self.assertEqual(calls[0], "verify")

    def test_unknown_kind_and_invalid_route_input_are_rejected(self):
        for candidate in ("no_such_kind", "", "   ", None, 7, {"kind": "x"}):
            with self.subTest(kind=candidate):
                with self.assertRaises(ValueError):
                    get_verifier(candidate)
        with self.assertRaisesRegex(ValueError, "Unsupported verifier kind: missing_kind"):
            get_verifier("missing_kind")
        with self.assertRaisesRegex(ValueError, "bounded nonempty text"):
            get_verifier("k" * 81)


class InvalidDeclarationTests(RegistryIsolatedTest):
    def test_non_class_and_non_subclass_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "registered as a class"):
            register_verifier(lambda spec: spec)

        class Loose:
            name = "loose_kind"
            kinds = {"loose_kind"}
            semantics = "loose"
            assurance_level = "AST_ONLY"
            read_spec = staticmethod(lambda spec: spec)
            verify = staticmethod(lambda spec: {"status": "PASS"})
            check_certificate = staticmethod(lambda spec, certificate: True)

        with self.assertRaisesRegex(ValueError, "must subclass VerifierSpec"):
            register_verifier(Loose)
        self.assertEqual(registry.registered_kinds(), ())

    def test_abstract_incomplete_and_empty_kind_declarations_are_rejected(self):
        class Missing(VerifierSpec):
            name = "missing_kind"
            kinds = {"missing_kind"}
            semantics = "missing"
            assurance_level = "AST_ONLY"

            def read_spec(self, spec):
                return spec

            def verify(self, spec):
                return {"status": "PASS"}

        with self.assertRaisesRegex(ValueError, "every abstract method"):
            register_verifier(Missing)
        with self.assertRaisesRegex(ValueError, "nonempty set"):
            register_verifier(make_spec("empty_kind", set()))

    def test_invalid_name_kind_semantics_and_assurance_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Verifier name"):
            register_verifier(make_spec("bad name", {"bad_name"}))
        with self.assertRaisesRegex(ValueError, "bounded ASCII statement kinds"):
            register_verifier(make_spec("bad_kind", {"not a kind"}))
        with self.assertRaisesRegex(ValueError, "bounded nonempty text"):
            register_verifier(make_spec("blank_semantics", {"blank_semantics"}, semantics="   "))
        with self.assertRaisesRegex(ValueError, "uppercase category token"):
            register_verifier(make_spec("lower_assurance", {"lower_assurance"},
                                        assurance="certificate_checked"))
        self.assertEqual(registry.registered_kinds(), ())

    def test_nonconstructible_verifier_is_rejected(self):
        class NeedsArgument(VerifierSpec):
            name = "needs_argument"
            kinds = {"needs_argument"}
            semantics = "needs_argument"
            assurance_level = "AST_ONLY"

            def __init__(self, required):
                self.required = required

            def read_spec(self, spec):
                return spec

            def verify(self, spec):
                return {"status": "PASS"}

            def check_certificate(self, spec, certificate):
                return True

        with self.assertRaisesRegex(ValueError, "constructible without arguments"):
            register_verifier(NeedsArgument)


class LeafContractTests(unittest.TestCase):
    def test_module_imports_only_stdlib_and_rds_core(self):
        tree = ast.parse((SCRIPTS / "rds_verify_registry.py").read_text(encoding="utf-8"))
        modules = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                   for alias in node.names}
        modules.update(node.module for node in ast.walk(tree)
                       if isinstance(node, ast.ImportFrom))
        self.assertEqual(set(modules), {"abc", "re", "rds_core"})
        self.assertNotIn("rds_verify", modules)

    def test_import_does_not_pull_domain_verifiers(self):
        script_id = rds_core.bytes_digest((SCRIPTS / "rds_verify_registry.py").read_bytes())
        code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                "import rds_verify_registry as r; "
                "assert 'rds_verify' not in sys.modules, 'reverse dependency'; "
                "assert r.VerifierSpec.__name__ == 'VerifierSpec'; "
                "assert r.registered_kinds() == (); print('isolated PASS')")
        run = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", code, str(SCRIPTS)],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("isolated PASS", run.stdout)
        self.assertEqual(script_id, rds_core.bytes_digest((SCRIPTS / "rds_verify_registry.py").read_bytes()))


if __name__ == "__main__":
    unittest.main()

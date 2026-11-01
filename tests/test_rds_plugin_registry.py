import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rds_plugin_registry import PluginManifest, PluginRegistry, PluginUnavailable
import rds_verify_registry as verifiers
import rds_verify  # Install trusted routes without metadata-driven loading.


def declaration(kind="predictor", name="test.plugin", version="1"):
    return {"kind": kind, "name": name, "version": version,
            "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
            "resources": {}, "availability": {"status": "AVAILABLE", "reason": ""}}


def selection(value):
    return {field: value[field] for field in ("kind", "name", "version")}


class PluginRegistryTests(unittest.TestCase):
    def test_all_roles_select_exact_version_with_no_default_authority(self):
        registry = PluginRegistry()
        for kind in ("verifier", "retrieval", "predictor", "graph"):
            value = declaration(kind)
            registry.register(value)
            selected = registry.select(selection(value)).to_dict()
            self.assertEqual(selected["permissions"], {"write": False, "execute": False})
            self.assertEqual(selected["resources"], {})
            with self.assertRaisesRegex(PluginUnavailable, "not registered"):
                registry.select({**selection(value), "version": "2"})
        self.assertEqual(len(registry.describe()), 4)

    def test_unavailable_reason_is_preserved_without_substitution(self):
        registry = PluginRegistry()
        unavailable = declaration(version="2")
        unavailable["availability"] = {"status": "UNAVAILABLE", "reason": "GPU backend not installed"}
        registry.register(declaration())
        registry.register(unavailable)
        with self.assertRaisesRegex(PluginUnavailable, "^GPU backend not installed$"):
            registry.select(selection(unavailable))
        for reason in ("", "   "):
            with self.assertRaisesRegex(ValueError, "explicit reason"):
                PluginManifest.from_dict({**unavailable, "availability": {
                    "status": "UNAVAILABLE", "reason": reason}})

    def test_configuration_cannot_load_or_register_code(self):
        registry = PluginRegistry()
        value = declaration()
        with patch.object(importlib, "import_module", side_effect=AssertionError("code load")), \
                patch.object(subprocess, "Popen", side_effect=AssertionError("execute")):
            registry.register(value)
            registry.select(selection(value))
            for field in ("module", "entrypoint", "command", "path", "permissions"):
                with self.assertRaisesRegex(ValueError, "selects"):
                    registry.select({**selection(value), field: "os.system"})
                if field != "permissions":
                    with self.assertRaisesRegex(ValueError, "metadata only"):
                        registry.register({**value, field: "os.system"})

    def test_registration_is_atomic_and_metadata_cannot_be_mutated_through_aliases(self):
        registry = PluginRegistry()
        value = declaration()
        manifest = registry.register(value)
        value["input_schema"]["type"] = "string"
        exposed = manifest.to_dict()
        exposed["permissions"]["execute"] = True
        registry.describe()[0]["availability"]["status"] = "UNAVAILABLE"
        self.assertEqual(registry.select(selection(value)).to_dict()["input_schema"], {"type": "object"})
        self.assertFalse(manifest.to_dict()["permissions"]["execute"])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            registry.register(declaration())
        self.assertEqual(len(registry.describe()), 1)

    def test_malformed_unbounded_and_non_json_metadata_rejected(self):
        value = declaration()
        malformed = [
            {**value, "kind": []}, {**value, "name": "../evil"},
            {**value, "version": " "}, {**value, "input_schema": "schema.py"},
            {**value, "resources": []}, {**value, "output_schema": {"bad": object()}},
            {**value, "resources": {"cpu": float("nan")}},
            {**value, "resources": {"text": "x" * 16384}},
            {**value, "permissions": {"write": 0, "execute": False}},
            {**value, "availability": {"status": "AUTO", "reason": ""}},
        ]
        for candidate in malformed:
            with self.subTest(candidate=str(candidate)[:90]), self.assertRaises(ValueError):
                PluginManifest.from_dict(candidate)

    def test_installed_verifier_metadata_keeps_old_routes_and_permissions(self):
        before = verifiers.registry_id()
        manifests = verifiers.plugin_manifests()
        registry = PluginRegistry()
        for manifest in manifests:
            registry.register(manifest)
            self.assertEqual(registry.select(selection(manifest)).to_dict(), manifest)
            self.assertFalse(manifest["permissions"]["write"])
            self.assertFalse(manifest["permissions"]["execute"])
        self.assertEqual({m["name"] for m in manifests}, {v.name for v in verifiers.registered_verifiers()})
        self.assertEqual(before, verifiers.registry_id())

    def test_schema_documents_runtime_fields_and_all_roles(self):
        schema = json.loads((ROOT / "schemas/rds-plugin-manifest.schema.json").read_text())
        from rds_plugin_registry import FIELDS, KINDS, REQUIRED
        self.assertEqual(set(schema["properties"]), FIELDS)
        self.assertEqual(set(schema["required"]), REQUIRED)
        self.assertEqual(set(schema["properties"]["kind"]["enum"]), KINDS)
        self.assertFalse(schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()

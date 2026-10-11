"""Check procedural routes and their relocation without changing runtime policy."""
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unicodedata
from urllib.parse import unquote, urlsplit
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROUTES = Path("references/skill-routes")


def prose(text):
    return re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$", "", text)


def local_links(text):
    text = prose(text)
    definitions = dict(re.findall(r"(?m)^ {0,3}\[([^]]+)\]:\s*(\S+)", text))
    targets = re.findall(r"\]\(([^)\n]+)\)", text)
    for label, reference in re.findall(r"\[([^]\n]+)\]\[([^]\n]*)\]", text):
        key = reference or label
        if key in definitions:
            targets.append(definitions[key])
    for target in targets:
        target = target.strip().split(' "', 1)[0].strip("<>")
        if not urlsplit(target).scheme and not target.startswith("//"):
            yield unquote(target)


def anchors(text):
    result = set(re.findall(r"(?:id|name)=[\"']([^\"']+)", text))
    seen = {}
    for heading in re.findall(r"(?m)^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", prose(text)):
        heading = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", heading).lower()
        slug = "".join(char for char in heading
                       if char in "-_ " or unicodedata.category(char)[0] in "LN").replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(slug + ("-" + str(count) if count else ""))
    return result


def link_errors(root, documents):
    errors = []
    root = root.resolve()
    for document in documents:
        for target in local_links(document.read_text(encoding="utf-8-sig")):
            path, _, fragment = target.partition("#")
            destination = (document.parent / path).resolve() if path else document.resolve()
            if not destination.is_relative_to(root) or not destination.exists():
                errors.append((document.relative_to(root).as_posix(), target, "missing/escaping target"))
            elif fragment and destination.suffix == ".md":
                if fragment not in anchors(destination.read_text(encoding="utf-8-sig")):
                    errors.append((document.relative_to(root).as_posix(), target, "missing fragment"))
    return errors


def route_documents(root):
    return sorted((root / ROUTES).glob("*.md"))


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProgressiveRouteTests(unittest.TestCase):
    def test_trigger_index_reaches_every_procedure_and_its_local_guides(self):
        routes = route_documents(ROOT)
        self.assertTrue(routes, "No on-demand procedures")
        root_text = (ROOT / "SKILL.md").read_text(encoding="ascii")
        table = root_text.split("## Routes\n", 1)[1].split("\n## ", 1)[0]
        indexed = {target.split("#", 1)[0] for target in local_links(table)
                   if target.startswith(ROUTES.as_posix() + "/")}
        self.assertEqual(indexed, {path.relative_to(ROOT).as_posix() for path in routes})
        self.assertEqual(link_errors(ROOT, [ROOT / "SKILL.md", ROOT / "docs/README.md", *routes]), [])
        for path in routes:
            with self.subTest(route=path.name):
                self.assertTrue(list(local_links(path.read_text(encoding="ascii"))), "No operation guides")

    def test_procedures_do_not_reload_root_or_all_routes(self):
        # Check executable-looking load directives, not mentions of the root policy.
        forbidden = re.compile(r"(?im)^\s*(?:READ|LOAD)\s+[^\n]*(?:\bSKILL\.md\b|\ball\s+(?:the\s+)?routes\b)")
        self.assertIsNotNone(forbidden.search("    LOAD this SKILL.md"))
        self.assertIsNotNone(forbidden.search("    READ all routes"))
        for path in route_documents(ROOT):
            with self.subTest(route=path.name):
                self.assertIsNone(forbidden.search(path.read_text(encoding="ascii")))

    def test_link_checker_detects_nested_missing_files_and_fragments(self):
        with tempfile.TemporaryDirectory(prefix="rds route links ") as temporary:
            root = Path(temporary)
            nested = root / "references/skill-routes/route.md"
            nested.parent.mkdir(parents=True)
            guide = root / "docs/guide.md"
            guide.parent.mkdir()
            guide.write_text("# Evidence and UNKNOWN\n\n## Repeat\n\n## Repeat\n", encoding="ascii")
            nested.write_text("[ok](../../docs/guide.md#evidence-and-unknown)\n"
                              "[duplicate](../../docs/guide.md#repeat-1)\n"
                              "[directory](../../docs/)\n"
                              "[missing](../../docs/absent.md)\n"
                              "[bad fragment](../../docs/guide.md#absent)\n"
                              "```text\n[example](nonexistent.md)\n```\n", encoding="ascii")
            self.assertEqual([row[2] for row in link_errors(root, [nested])],
                             ["missing/escaping target", "missing fragment"])

    def test_indexed_procedures_survive_host_package_relocation(self):
        builder = module_at("progressive_package_builder", ROOT / "scripts/build_plugins.py")
        with tempfile.TemporaryDirectory(prefix="rds progressive package ") as temporary:
            output = Path(temporary) / "export"
            builder.build(output, ROOT)
            moved = output.with_name("relocated package")
            output.rename(moved)
            for host in ("claude", "codex", "omp"):
                with self.subTest(host=host):
                    plugin = moved / host / builder.NAME
                    skill = plugin / "skills" / builder.NAME
                    if host == "codex":
                        manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
                        self.assertEqual((plugin / manifest["skills"] / builder.NAME).resolve(), skill.resolve())
                    self.assertEqual((skill / "SKILL.md").read_bytes(), (ROOT / "SKILL.md").read_bytes())
                    for original in route_documents(ROOT):
                        self.assertEqual((skill / original.relative_to(ROOT)).read_bytes(), original.read_bytes())
                    self.assertEqual(link_errors(skill, [skill / "SKILL.md", skill / "docs/README.md",
                                                         *route_documents(skill)]), [])
                    self.assertFalse((skill / ".rds").exists())

    def test_development_snapshot_keeps_indexed_route_bytes_and_hashes(self):
        development = module_at("progressive_development", ROOT / "examples/self-development/run.py")
        with tempfile.TemporaryDirectory(prefix="rds progressive snapshot ") as temporary:
            target = Path(temporary)
            entries = development.freeze_public_source(ROOT, target)
            indexed = {entry["path"]: entry for entry in entries}
            for original in route_documents(ROOT):
                relative = original.relative_to(ROOT).as_posix()
                with self.subTest(route=relative):
                    self.assertIn(relative, indexed)
                    self.assertEqual((target / relative).read_bytes(), original.read_bytes())
                    self.assertEqual(indexed[relative]["sha256"], development.file_sha(original))
            self.assertEqual(link_errors(target, [target / "SKILL.md", *route_documents(target)]), [])
            self.assertFalse((target / ".rds").exists())


if __name__ == "__main__":
    unittest.main()

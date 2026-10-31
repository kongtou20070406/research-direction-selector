"""Exercise exported host packages away from the source checkout."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_plugins
import rds_conversation_hook


class PluginPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="rds plugin ")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.workspace = Path(cls.temp.name)
        cls.output = cls.workspace / "export"
        build_plugins.build(cls.output)
        # Move the whole export: host paths must not retain checkout locations.
        cls.moved = cls.workspace / "relocated 包"
        cls.output.rename(cls.moved)

    def test_each_package_runs_the_real_cli_and_packaged_advisor_resources(self):
        for host in ("claude", "codex", "omp"):
            with self.subTest(host=host):
                skill = self.moved / host / build_plugins.NAME / "skills" / build_plugins.NAME
                project = self.workspace / (host + " project")
                project.mkdir()
                env = {**os.environ, "RDS_USAGE_DB": str(project / "usage.sqlite3")}
                cli = [sys.executable, "-B", str(skill / "scripts/rds_cli.py"), "--root", str(project)]
                version = subprocess.run(cli + ["--version"], cwd=project, env=env,
                                         capture_output=True, text=True, encoding="utf-8", timeout=15)
                self.assertEqual(version.returncode, 0, version.stderr)
                result = subprocess.run(cli + ["advise", "--context", str(skill / "examples/advisor-search/boundary-context.json"),
                           "--graph", str(skill / "references/judgment-graph.yaml")], cwd=project, env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                answer = json.loads(result.stdout)
                self.assertTrue(any(row.get("type") == "EXECUTABLE_DIRECTION_SEARCH" for row in answer["recommendations"]))
                self.assertFalse((skill / ".rds").exists())
                self.assertFalse((project / ".rds").exists())
                self.assertTrue((skill / "formal/lakefile.lean").is_file())
                self.assertTrue((skill / "references/theory-tools.json").is_file())

    def test_manifest_hook_command_runs_from_unrelated_cwd_after_relocation(self):
        for host, variable in (("claude", "CLAUDE_PLUGIN_ROOT"), ("codex", "PLUGIN_ROOT")):
            with self.subTest(host=host):
                plugin = self.moved / host / build_plugins.NAME
                config = json.loads((plugin / "integrations/hooks.json").read_text(encoding="utf-8"))
                command = config["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
                argv = shlex.split(command)
                argv[0] = sys.executable
                entry = plugin / "skills" / build_plugins.NAME / "scripts/rds_cli.py"
                payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": str(self.workspace),
                           "tool_input": {"command": f'python -B "{entry}" --version'},
                           "tool_response": {"exit_code": 0}}
                result = subprocess.run(argv, input=json.dumps(payload), env={**os.environ, variable: str(plugin)},
                                        cwd=self.workspace, capture_output=True, encoding="utf-8", timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("RDS | ", context)
                self.assertEqual("RDS | CLI: returned", context.splitlines()[0])
                self.assertNotIn(str(ROOT), command)
                self.assertFalse((self.workspace / ".rds").exists())

    def test_relocated_packages_include_and_load_external_presentation_assets(self):
        assets = [p.relative_to(ROOT) for p in (ROOT / "scripts/assets").rglob("*") if p.is_file()]
        self.assertTrue(assets)
        for host in ("claude", "codex", "omp"):
            with self.subTest(host=host):
                skill = self.moved / host / build_plugins.NAME / "skills" / build_plugins.NAME
                for relative in assets:
                    self.assertEqual((skill / relative).read_bytes(), (ROOT / relative).read_bytes())
                for script in ("rds_dashboard.py", "rds_hypergraph_view.py"):
                    output = self.workspace / (host + "-" + script + ".html")
                    result = subprocess.run([sys.executable, "-S", "-B", str(skill / "scripts" / script),
                                             "--root", str(self.workspace), "--demo", "--output", str(output)],
                                            cwd=self.workspace, capture_output=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(b"<!doctype html>", output.read_bytes())

    def test_hosts_do_not_load_each_others_hooks_and_paths_resolve(self):
        omp = self.moved / "omp" / build_plugins.NAME
        self.assertFalse((omp / ".claude-plugin").exists())
        self.assertFalse((omp / "hooks/hooks.json").exists())
        package = json.loads((omp / "package.json").read_text(encoding="utf-8"))
        for path in package["omp"]["extensions"]:
            self.assertTrue((omp / path).is_file())
        for host, manifest in (("claude", ".claude-plugin/plugin.json"), ("codex", ".codex-plugin/plugin.json")):
            root = self.moved / host / build_plugins.NAME
            config = json.loads((root / manifest).read_text(encoding="utf-8"))
            self.assertTrue((root / config["hooks"]).is_file())
            self.assertFalse((root / "hooks/hooks.json").exists(), "avoid duplicate default and manifest hooks")

    def test_existing_output_is_preserved(self):
        marker = self.moved / "keep.txt"
        marker.write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "never overwritten"):
            build_plugins.build(self.moved)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_codex_uses_native_manifest_without_portable_hook_suppression(self):
        plugin = self.moved / "codex" / build_plugins.NAME
        self.assertFalse((plugin / "plugin.json").exists(),
                         "Codex 0.160.0 skips bundled hooks for portable root manifests")
        manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], build_plugins.NAME)
        self.assertTrue((plugin / manifest["skills"] / build_plugins.NAME / "SKILL.md").is_file())
        hooks = json.loads((plugin / manifest["hooks"]).read_text(encoding="utf-8"))
        self.assertEqual(set(hooks["hooks"]), {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse"})
        self.assertIsNone(rds_conversation_hook.respond({'hook_event_name': 'UserPromptSubmit'}, 'codex'))

    def test_omp_reminder_events(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node unavailable')
        code = r'''
import assert from 'node:assert/strict';
import {createRdsStatusExtension} from './integrations/omp/rds-status.mjs';
const handlers = new Map(), calls = [];
const ctx = {cwd: process.cwd(), hasUI: false};
createRdsStatusExtension({runHelper: async p => {
  calls.push(p); return {additionalContext: 'reminder'};
}})({on: (name, fn) => handlers.set(name, fn)});
const event = {toolName:'bash', toolCallId:'a', input:{command:'python -m train --private'},
  content:[{type:'text',text:'private output'}], details:{exitCode:2}, isError:true};
const [first, duplicate] = await Promise.all([handlers.get('tool_result')(event, ctx), handlers.get('tool_result')(event, ctx)]);
assert.deepEqual(first, {additionalContext:'reminder'}); assert.equal(duplicate, undefined);
assert.equal(calls.length, 1); assert.equal(calls[0].tool_response.exitCode, 2);
assert.equal(calls[0].tool_response.isError, true); assert.ok(!JSON.stringify(calls).includes('private output'));
assert.equal(await handlers.get('tool_result')({...event, toolName:'read', toolCallId:'b'}, ctx), undefined);
const prompt = await handlers.get('before_agent_start')({prompt:'private prompt'}, ctx);
assert.deepEqual(prompt.message, {customType:'rds-reminder', content:'reminder', display:false});
assert.ok(!JSON.stringify(calls).includes('private prompt'));
'''
        result = subprocess.run([node, '--input-type=module', '-e', code], cwd=ROOT,
                                capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_omp_real_helper_records_concurrent_results_once(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node unavailable')
        import rds_notes
        with tempfile.TemporaryDirectory() as project:
            rds_notes.append(project, 'constraint', 'initial', source='user')
            code = r'''
import assert from 'node:assert/strict';
import {createHelperRunner, createRdsStatusExtension} from './integrations/omp/rds-status.mjs';
const handlers = new Map(), cwd = process.env.RDS_TEST_PROJECT;
createRdsStatusExtension({runHelper:createHelperRunner({helperPath:process.env.RDS_TEST_HELPER})})
  ({on:(name, fn) => handlers.set(name, fn)});
const event = id => ({toolName:'bash',toolCallId:id,input:{command:'python -m train --private'},isError:false});
const results = await Promise.all(['a','b','c','c'].map(id => handlers.get('tool_result')(event(id), {cwd})));
assert.ok(results.some(r => r?.additionalContext?.includes('goal')));
'''
            env = {**os.environ, 'RDS_PYTHON': sys.executable, 'RDS_TEST_PROJECT': project,
                   'RDS_TEST_HELPER': str(ROOT / 'scripts' / 'rds_conversation_hook.py')}
            result = subprocess.run([node, '--input-type=module', '-e', code], cwd=ROOT, env=env,
                                    capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            notes = rds_notes.read(project)
            self.assertEqual([(n['route'], n['outcome']) for n in notes if n['type'] == 'attempt'],
                             [('train', 'ok')] * 3)
            self.assertEqual(len([n for n in notes if n['type'] == 'shown']), 1)

    def test_untracked_private_content_does_not_enter_export(self):
        source = self.workspace / "minimal source"
        source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True, capture_output=True)
        for relative in ("SKILL.md", "LICENSE", "scripts/rds_cli.py", "scripts/rds_conversation_hook.py",
                         "integrations/omp/rds-status.mjs", "docs/public.md"):
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('VERSION = "5.8.0"\n' if relative.endswith("rds_cli.py") else "public", encoding="utf-8")
        subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
        (source / "docs/private.md").write_text("private research", encoding="utf-8")
        (source / ".rds").mkdir()
        (source / ".rds/private.json").write_text("private state", encoding="utf-8")
        target = self.workspace / "minimal export"
        build_plugins.build(target, source)
        for host in ("claude", "codex", "omp"):
            skill = target / host / build_plugins.NAME / "skills" / build_plugins.NAME
            self.assertTrue((skill / "docs/public.md").is_file())
            self.assertFalse((skill / "docs/private.md").exists())
            self.assertFalse((skill / ".rds").exists())


if __name__ == "__main__":
    unittest.main()

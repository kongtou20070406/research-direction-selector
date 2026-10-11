"""Real hook entry tests: truthful, bounded context without execution or state."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/rds_conversation_hook.py"
CLI = ROOT / "scripts/rds_cli.py"
spec = importlib.util.spec_from_file_location("rds_conversation_hook", SCRIPT)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class ConversationHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="RDS hook space ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def payload(self, event="PreToolUse", command=None, **extra):
        return {"session_id": "session-a", "cwd": str(ROOT),
                "hook_event_name": event, "tool_name": "Bash", "tool_use_id": "tool-a",
                "tool_input": {"command": command or f'python -B "{CLI}" advise'}, **extra}

    def run_hook(self, payload=None, host="codex", raw=None, script=SCRIPT, args=None):
        if raw is None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        result = subprocess.run([sys.executable, "-B", str(script), *(args or ["--host", host])],
                                input=raw, capture_output=True, cwd=self.root, timeout=5,
                                env={**os.environ, "PYTHONIOENCODING": "ascii"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertLess(len(result.stdout), 1600)
        return json.loads(result.stdout) if result.stdout else None

    def context(self, payload, **kwargs):
        result = self.run_hook(payload, **kwargs)
        self.assertEqual(set(result), {"hookSpecificOutput"})
        output = result["hookSpecificOutput"]
        self.assertEqual(set(output), {"hookEventName", "additionalContext"})
        self.assertEqual(output["hookEventName"], payload["hook_event_name"])
        self.assertTrue(output["additionalContext"].isascii())
        return output["additionalContext"]

    def test_session_start_injects_no_model_context_on_any_host(self):
        # Availability text was model context the operator cannot see and did not
        # request. Until the session gate can answer first (phase R), inject none.
        for host in ("codex", "claude", "omp"):
            with self.subTest(host=host):
                output = self.run_hook({"hook_event_name": "SessionStart"}, host=host)
                self.assertNotIn("plugin: available", json.dumps(output or {}))
                self.assertNotIn("hookSpecificOutput", json.dumps(output or {}))

    def test_session_start_shows_the_operator_state_only_where_verified(self):
        # Claude Code keeps top-level systemMessage on SessionStart and renders it
        # to the person. Codex and OMP have no verified operator surface yet.
        output = self.run_hook({"hook_event_name": "SessionStart"}, host="claude")
        self.assertEqual(set(output), {"systemMessage"})
        self.assertEqual(output["systemMessage"], hook.OPERATOR_STATUS)
        self.assertIn("not enabled", output["systemMessage"])
        self.assertLessEqual(len(output["systemMessage"]), 160)
        for host in ("codex", "omp"):
            with self.subTest(host=host):
                self.assertIsNone(self.run_hook({"hook_event_name": "SessionStart"}, host=host))

    def test_direct_absolute_and_relative_calls_use_exact_entry(self):
        for command in (f'python -B "{CLI}" advise',
                        'python -u scripts/rds_cli.py status',
                        'py -3 -B scripts/rds_cli.py project status',
                        'python3.13 scripts/rds_cli.py --root ./private project status'):
            with self.subTest(command=command):
                text = self.context(self.payload(command=command))
                self.assertIn(": pending", text)
                self.assertNotIn("private", text)
                self.assertNotIn(str(ROOT), text)

    def test_tool_workdir_overrides_session_cwd(self):
        payload = self.payload(tool_name="exec_command", cwd=str(self.root),
                               tool_input={"cmd": "python scripts/rds_cli.py advise", "workdir": str(ROOT)})
        self.assertIn("RDS | advise: pending", self.context(payload))

    def test_direct_structured_argv_and_python_executable_spaces(self):
        payload = self.payload(tool_input={"argv": [sys.executable, "-B", str(CLI), "formal", "verify"]})
        self.assertIn("RDS | formal: pending", self.context(payload))
        self.assertEqual(hook.literal_argv('"C:\\Program Files\\Python\\python.exe" -B "D:\\research space\\rds_cli.py" status'),
                         ["C:\\Program Files\\Python\\python.exe", "-B", "D:\\research space\\rds_cli.py", "status"])

    def test_shell_programs_and_mentions_are_not_calls(self):
        direct = f'python -B "{CLI}" status'
        commands = [f"echo '{direct}'", f"grep rds_cli.py '{CLI}'", f"rg '{direct}' .",
                    f"{direct}; echo done", f"{direct} && echo done", f"{direct} | cat",
                    f"{direct} > result.json", f"{direct} &", f"{direct}\necho done",
                    f"cd '{ROOT}' && python scripts/rds_cli.py status", f"env {direct}",
                    f"python -c '{direct}'", "python -m rds_cli status",
                    f"$x = '{direct}'", f"bash -c '{direct}'", f"& {direct}",
                    f"python \"{CLI}\"'ignored' status", "python $(find-rds) status",
                    f"python `find-rds` status", "python %RDS_CLI% status"]
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(self.run_hook(self.payload(command=command)))

    def test_code_mode_source_is_not_parsed_as_an_invocation(self):
        payload = self.payload(tool_name="functions.exec", tool_input={
            "code": f'await tools.exec_command({{cmd: "python {CLI} status"}})'
        })
        self.assertIsNone(self.run_hook(payload))
        # A real nested exec call gets its own Bash event and is handled above.

    def test_same_filename_in_another_directory_is_not_this_plugin(self):
        other = self.root / "rds_cli.py"
        other.write_text("raise AssertionError('must never run')", encoding="utf-8")
        self.assertIsNone(self.run_hook(self.payload(command=f'python "{other}" status')))

    def test_packaged_hook_binds_its_colocated_cli_and_does_not_run_it(self):
        folder = self.root / "plugin space" / "skills" / "research-direction-selector" / "scripts"
        folder.mkdir(parents=True)
        packaged_hook, packaged_cli = folder / SCRIPT.name, folder / CLI.name
        shutil.copyfile(SCRIPT, packaged_hook)
        packaged_cli.write_text("from pathlib import Path\nPath(__file__).with_name('executed.txt').write_text('unexpected execution')\nraise AssertionError('must never run')", encoding="utf-8")
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        text = self.context(self.payload(command=f'python -B "{packaged_cli}" status'), script=packaged_hook)
        self.assertIn("RDS | status: pending", text)
        self.assertIsNone(self.run_hook(self.payload(), script=packaged_hook))
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_structured_exit_codes_and_errors_are_truthful(self):
        cases = [({"exit_code": 0}, "returned"), ({"exitCode": 0}, "returned"),
                 ({"exit_code": 7}, "nonzero exit"), ({"exit_code": 130}, "nonzero exit"),
                 ({"exit_code": 0, "stderr": "a warning"}, "returned"),
                 ({"exit_code": 0, "isError": True}, "error"),
                 ({"interrupted": True}, "error"),
                 ({"error": "private error payload"}, "error")]
        for response, expected in cases:
            with self.subTest(response=response):
                text = self.context(self.payload("PostToolUse", tool_response=response))
                self.assertEqual(text.splitlines()[0], "RDS | advise: " + expected)
                self.assertNotIn("private error payload", text)
                self.assertNotIn(": running", text)

    def test_formal_nonzero_verdict_is_not_reported_as_tool_error(self):
        for code in (1, 2):
            payload = self.payload("PostToolUse", command=f'python -B "{CLI}" formal verify',
                                   tool_response={"exit_code": code})
            text = self.context(payload)
            self.assertEqual(text.splitlines()[0], "RDS | formal: nonzero exit")
            self.assertNotIn(": error", text)
            self.assertIn("call_returned != scientific_acceptance", text)

    def test_missing_or_textual_exit_is_unknown_not_success(self):
        for response in (None, "Process exited with code 0", '{"exit_code":0}',
                         {"stdout": "PASS: exit_code=0"}, {"exit_code": False},
                         {"exit_code": "0"}, {"exit_code": None, "session_id": 42},
                         {"stdout": "ok", "stderr": "", "interrupted": False}):
            with self.subTest(response=response):
                text = self.context(self.payload("PostToolUse", tool_response=response))
                self.assertEqual(text.splitlines()[0], "RDS | advise: result unknown")
                self.assertNotIn(": returned", text)

    def test_claude_failure_event_does_not_echo_error_or_require_exit_code(self):
        payload = self.payload("PostToolUseFailure", error="private failed command", is_interrupt=True)
        self.assertIn("RDS | advise: error", self.context(payload, host="claude"))
        self.assertNotIn("private failed command", self.context(payload, host="claude"))
        self.assertIsNone(self.run_hook(payload, host="codex"))

    def test_other_events_and_tools_are_silent(self):
        for event in ("SessionEnd", "Stop", "UserPromptSubmit", "PreCompact", "unknown"):
            self.assertIsNone(self.run_hook(self.payload(event)))
        self.assertIsNone(self.run_hook(self.payload(tool_name="Read")))

    def test_omp_bridge_returns_same_fixed_status_and_no_session_banner(self):
        payload = self.payload()
        output = self.run_hook(payload, host="omp")
        self.assertEqual(set(output), {"status", "additionalContext"})
        self.assertEqual(output["status"], "RDS | advise: running")
        self.assertEqual(output["additionalContext"].splitlines()[0], output["status"])
        self.assertEqual(self.context(payload).splitlines()[0], "RDS | advise: pending")
        self.assertIsNone(self.run_hook({"hook_event_name": "SessionStart"}, host="omp"))

    def test_pre_hooks_describe_pending_not_started_calls_and_shell_aliases(self):
        for host in ("claude", "codex"):
            for tool in ("Bash", "bash", "PowerShell", "exec_command", "functions.exec_command", "shell_command"):
                text = self.context(self.payload(tool_name=tool), host=host)
                self.assertEqual(text.splitlines()[0], "RDS | advise: pending")
                self.assertIn("phase=before_execution", text)
                self.assertNotIn("phase=execution_started", text)

    def test_no_shared_session_state_or_stale_running_marker(self):
        self.context(self.payload(session_id="a"))
        self.context(self.payload("PostToolUse", session_id="b", tool_response={"exit_code": 0}))
        self.assertIsNone(self.run_hook({"hook_event_name": "SessionEnd", "session_id": "a"}))
        text = self.context(self.payload("PostToolUse", session_id="a", tool_response=None))
        self.assertIn(": result unknown", text)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_input_is_bounded_silent_and_nonblocking(self):
        inputs = [b"", b"null", b"[]", b"invalid", b"\xff", b"[" * 1500,
                  b'{"hook_event_name":"SessionStart","hook_event_name":"PreToolUse"}',
                  b'{"hook_event_name":"SessionStart","bad":NaN}',
                  b" " * (hook.MAX_INPUT_BYTES + 1),
                  b'{"hook_event_name":"PreToolUse","tool_name":[]}']
        for raw in inputs:
            with self.subTest(raw=raw[:50]):
                self.assertIsNone(self.run_hook(raw=raw))
        self.assertIsNone(self.run_hook({}, args=["--unknown"]))

    def test_ambiguous_command_fields_and_network_paths_are_ignored(self):
        payload = self.payload()
        payload["tool_input"]["argv"] = ["python", str(CLI), "status"]
        self.assertIsNone(self.run_hook(payload))
        self.assertIsNone(self.run_hook(self.payload(cwd="\\\\unavailable\\share")))
        self.assertIsNone(self.run_hook(self.payload(command='python "//unavailable/share/rds_cli.py" status')))


if __name__ == "__main__":
    unittest.main()

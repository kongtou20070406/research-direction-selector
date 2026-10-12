"""Plan 6.6: parallel sessions must not share state, authority or a gate.

Two sessions are simulated with two independent spaces. The checks are the
observable ones from the acceptance table: a closed session does not borrow an
enabled one's authority, copying a session's locator does not confer a
capability, and an abnormal end leaves a coverage gap rather than an inferred
pass.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/rds_session.py"
HOOK = ROOT / "scripts/rds_conversation_hook.py"
spec = importlib.util.spec_from_file_location("rds_session", SCRIPT)
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)


class SessionIsolationTests(unittest.TestCase):
    def setUp(self):
        self.spaces = []
        for prefix in ("RDS session A ", "RDS session B "):
            temporary = tempfile.TemporaryDirectory(prefix=prefix)
            self.addCleanup(temporary.cleanup)
            self.spaces.append(Path(temporary.name))
        self.a, self.b = self.spaces

    def snapshot(self, folder):
        return sorted(str(path.relative_to(folder)) for path in folder.rglob("*"))

    def run_session(self, folder, *arguments, environment=None):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *arguments],
                              capture_output=True, encoding="utf-8", timeout=30,
                              cwd=folder, env={**os.environ, **(environment or {})})

    def test_each_session_reads_its_own_off_state_without_touching_the_other(self):
        # A and B are separate spaces; neither observation may create anything in
        # the other, and both must report off for their own scope.
        before_a, before_b = self.snapshot(self.a), self.snapshot(self.b)
        reads = {}
        for folder in (self.a, self.b):
            result = self.run_session(folder, "--json", "observe",
                                      "--host", "codex", "--instance", "i1",
                                      "--session", folder.name)
            self.assertEqual(result.returncode, 0, result.stderr)
            reads[folder] = json.loads(result.stdout)
        for folder, report in reads.items():
            self.assertEqual(report["switch_state"], "INSTALLED_OFF", folder)
            self.assertEqual(report["new_admission"], "OFF", folder)
            self.assertFalse(report["wrote"], folder)
        self.assertEqual(self.snapshot(self.a), before_a)
        self.assertEqual(self.snapshot(self.b), before_b)

    def test_a_locator_copied_from_another_session_grants_nothing(self):
        # Plan 6.4/6.6: session locators are lookup keys only. Copying A's
        # locator into B's environment must not enable B, and must not make the
        # two observations agree on a scope they do not share.
        copied = {"RDS_SESSION": "session-a", "RDS_HOST": "codex",
                  "RDS_INSTANCE": "i1", "RDS_ENABLED": "1"}
        report = json.loads(self.run_session(self.b, "--json", "observe",
                                             environment=copied).stdout)
        self.assertEqual(report["switch_state"], "INSTALLED_OFF")
        self.assertEqual(report["new_admission"], "OFF")
        self.assertIsNone(report["scope_id"])
        self.assertEqual(report["enable_epoch"], 0)

    def test_persisted_enable_environment_cannot_arm_a_session(self):
        # Plan 6.4: authorization must not propagate through RDS_ENABLED,
        # RDS_DISABLE, a marker file or a shared shell environment.
        marker = self.a / ".rds-enabled"
        marker.write_text("1", encoding="utf-8")
        environment = {"RDS_ENABLED": "1", "RDS_DISABLE": "0",
                       "RDS_SESSION": "s1", "RDS_HOST": "codex"}
        for folder in (self.a, self.b):
            with self.subTest(folder=folder.name):
                report = json.loads(self.run_session(folder, "--json", "observe",
                                                     environment=environment).stdout)
                self.assertEqual(report["new_admission"], "OFF")
                self.assertEqual(report["switch_state"], "INSTALLED_OFF")
                self.assertEqual(report["enable_epoch"], 0)
        # The marker file is not an enable record and observation must not have
        # touched it either.
        self.assertEqual(marker.read_text(encoding="utf-8"), "1")

    def test_an_enable_request_in_one_session_does_not_enable_the_other(self):
        arguments = ("--json", "enable", "--host", "codex", "--instance", "i1",
                     "--session", "s1", "--root", str(self.a), "--mode", "research",
                     "--confirm-ui")
        requested = json.loads(self.run_session(self.a, *arguments).stdout)
        self.assertEqual(requested["enable_result"], "PENDING")
        self.assertNotEqual(requested["new_admission"], "ON")
        other = json.loads(self.run_session(self.b, "--json", "observe").stdout)
        self.assertEqual(other["new_admission"], "OFF")
        self.assertEqual(other["switch_state"], "INSTALLED_OFF")

    def test_direct_module_observation_is_the_same_function_the_verb_uses(self):
        # The verb and the module-level report must not diverge: a second guess
        # would be a place where two sessions could disagree.
        direct = session.observe()
        via_cli = json.loads(self.run_session(self.a, "--json", "observe").stdout)
        for key in ("switch_state", "new_admission", "installation", "usage_policy",
                    "skill_auto_application", "authority_status"):
            self.assertEqual(direct[key], via_cli[key], key)

    def test_abnormal_end_keeps_a_coverage_gap_and_reports_no_session(self):
        # Plan 6.6: killing the host while a supervisor survives must yield an
        # abnormal-termination record; when no supervisor can attest it, the
        # result stays UNKNOWN and SessionEnd is not evidence of a clean stop.
        # This build has no supervisor, so the honest reading is a gap.
        environment = {"RDS_SESSION": "killed-session", "RDS_HOST": "codex"}
        report = json.loads(self.run_session(self.a, "--json", "observe",
                                             environment=environment).stdout)
        self.assertEqual(report["switch_state"], "INSTALLED_OFF")
        self.assertEqual(report["active_action_ids"], [])
        self.assertEqual(report["last_authority_sequence"], 0)
        self.assertEqual(report["authority_status"], "UNKNOWN")

        # SessionEnd is not a model-context event and must not be turned into a
        # closing acknowledgement: the hook contributes nothing for it.
        payload = json.dumps({"hook_event_name": "SessionEnd", "session_id": "killed-session"}).encode("utf-8")
        result = subprocess.run([sys.executable, "-B", str(HOOK), "--host", "claude"],
                                input=payload, capture_output=True, cwd=self.a, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), b"")
        self.assertEqual(self.snapshot(self.a), [])


if __name__ == "__main__":
    unittest.main()

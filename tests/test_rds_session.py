"""Session inspection must answer "is RDS on?" without creating anything.

The operator-facing guarantee in plan sections 6.2/6.6/6.7 is that the
self-check command has no usage, directory or database side effect. Without
that property the command cannot be used to verify a closed state, because
asking the question would itself leave a trace.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/rds_session.py"


class SessionInspectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="RDS session space ")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def run_session(self, *arguments, environment=None):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *arguments],
                              capture_output=True, encoding="utf-8", timeout=30,
                              cwd=self.folder, env={**os.environ, **(environment or {})})

    def snapshot(self):
        return sorted(str(path.relative_to(self.folder)) for path in self.folder.rglob("*"))

    def test_observation_creates_no_file_directory_or_usage_record(self):
        forced = self.folder / "forced-usage.sqlite3"
        self.assertEqual(self.snapshot(), [])
        for arguments in (("observe",), ("list",), ("--json", "observe"),
                          ("observe", "--json"), ("list", "--json")):
            with self.subTest(arguments=arguments):
                result = self.run_session(*arguments, environment={"RDS_USAGE_DB": str(forced)})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
        self.assertEqual(self.snapshot(), [])
        self.assertFalse(forced.exists())

    def test_observation_reports_the_truth_for_this_checkout(self):
        report = json.loads(self.run_session("--json", "observe").stdout)
        self.assertEqual(report["switch_state"], "INSTALLED_OFF")
        self.assertEqual(report["new_admission"], "OFF")
        self.assertFalse(report["wrote"])
        # Fields with no trusted source stay UNKNOWN rather than being guessed
        # from the fact that this command ran.
        self.assertEqual(report["host_registration"], "UNKNOWN")
        self.assertEqual(report["authority_status"], "UNKNOWN")
        self.assertEqual(report["usage_health"], "NOT_PROBED")
        self.assertEqual(report["skill_load"], "UNKNOWN")
        self.assertIsNone(report["mode"])
        self.assertEqual(report["enable_epoch"], 0)

    def test_text_output_states_the_state_in_one_obvious_line(self):
        lines = self.run_session("observe").stdout.splitlines()
        self.assertIn("NOT enabled", lines[0])
        self.assertIn("wrote nothing", lines[-1])

    def test_list_and_observe_do_not_disagree(self):
        listed = json.loads(self.run_session("list", "--json").stdout)
        observed = json.loads(self.run_session("observe", "--json").stdout)
        for key in ("switch_state", "new_admission", "installation", "usage_policy"):
            self.assertEqual(listed[key], observed[key], key)

    def test_enable_without_a_session_authority_stays_pending_and_writes_nothing(self):
        arguments = ("--json", "enable", "--host", "codex", "--instance", "i1",
                     "--session", "s1", "--root", str(self.folder), "--mode", "research",
                     "--confirm-ui")
        report = json.loads(self.run_session(*arguments).stdout)
        self.assertEqual(report["enable_result"], "PENDING")
        self.assertEqual(report["operator_attested"], "UNKNOWN")
        self.assertEqual(report["switch_state"], "INSTALLED_OFF")
        self.assertFalse(report["wrote"])
        # --confirm-ui requests a flow; it is not a credential and does not enable.
        self.assertNotEqual(report["new_admission"], "ON")
        self.assertEqual(self.snapshot(), [])

    def test_disable_without_an_epoch_is_a_no_op_that_writes_nothing(self):
        report = json.loads(self.run_session("disable", "--host", "codex",
                                             "--instance", "i1", "--session", "s1",
                                             "--json").stdout)
        self.assertEqual(report["disable_result"], "NO_OP")
        self.assertEqual(report["active_action_ids"], [])
        self.assertEqual(self.snapshot(), [])

    def test_missing_locators_are_refused_not_defaulted(self):
        for verb in ("enable", "disable"):
            with self.subTest(verb=verb):
                result = self.run_session(verb, "--host", "codex")
                self.assertEqual(result.returncode, 1)
                self.assertIn("Missing required session locator", result.stderr)
                self.assertEqual(self.snapshot(), [])

    def test_bad_mode_is_refused(self):
        result = self.run_session("enable", "--host", "codex", "--instance", "i1",
                                  "--session", "s1", "--root", str(self.folder), "--mode", "admin")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--mode must be observe or research", result.stderr)


if __name__ == "__main__":
    unittest.main()

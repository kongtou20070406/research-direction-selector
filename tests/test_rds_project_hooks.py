"""Import-order coverage for the optional project lifecycle providers."""

from pathlib import Path
import subprocess
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class ProjectHookTests(unittest.TestCase):
    def test_project_import_loads_providers_only_on_demand(self):
        code = """import sys
sys.path.insert(0, sys.argv[1])
from rds_project import ProjectStore
assert 'rds_owned_advisor' not in sys.modules
assert 'rds_quick' not in sys.modules
from rds_project_hooks import provider
assert provider('advisor').prepare_admission
assert 'rds_owned_advisor' in sys.modules
assert 'rds_quick' not in sys.modules
assert provider('quick')._recover_prospective
"""
        result = subprocess.run([sys.executable, "-B", "-c", code, str(SCRIPTS)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_providers_can_import_before_project_hooks_are_used(self):
        code = """import sys
sys.path.insert(0, sys.argv[1])
import rds_quick
import rds_owned_advisor
from rds_project_hooks import provider
assert provider('quick') is rds_quick
assert provider('advisor') is rds_owned_advisor
"""
        result = subprocess.run([sys.executable, "-B", "-c", code, str(SCRIPTS)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

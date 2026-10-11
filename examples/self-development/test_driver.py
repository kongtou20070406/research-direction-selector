"""Run real RDS regression cases and emit machine-readable observations."""
import json
from pathlib import Path
import sys
import unittest


def main():
    config = json.loads(Path("development-config.json").read_text(encoding="utf-8"))
    suite = unittest.TestSuite()
    for pattern in config["test_patterns"]:
        if not (Path('tests') / pattern).is_file():
            raise ValueError('Selected test module is missing from the frozen workload: ' + pattern)
        selected = unittest.defaultTestLoader.discover('tests', pattern=pattern)
        if not selected.countTestCases():
            raise ValueError('Selected test module contains no regression cases: ' + pattern)
        suite.addTests(selected)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    observations = {"case_count": result.testsRun,
                    "failure_count": len(result.failures) + len(result.errors),
                    "skipped_count": len(result.skipped),
                    "failures": [{"case_id": case.id(), "trace": trace} for case, trace in result.failures + result.errors],
                    "skipped": [{"case_id": case.id(), "reason": reason} for case, reason in result.skipped]}
    Path("out").mkdir(exist_ok=True)
    Path("out/test-results.json").write_text(json.dumps(observations, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if result.wasSuccessful() and result.testsRun else 1


if __name__ == "__main__":
    sys.exit(main())

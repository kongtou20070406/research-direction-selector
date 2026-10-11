"""The development snapshot preserves public inputs without copying private state."""
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("self_development_example", ROOT / "examples/self-development/run.py")
development = importlib.util.module_from_spec(spec)
spec.loader.exec_module(development)


class DevelopmentSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rds development snapshot ")
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.source, self.target = base / "source", base / "copy"
        self.source.mkdir()
        self.target.mkdir()
        development.git(self.source, "init", "--quiet", "--template=")

    def tracked(self, name, content="public"):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        development.git(self.source, "add", "--force", "--", name)
        return path

    def test_mixed_missing_or_empty_test_module_never_reports_success(self):
        tests=self.target/'tests'
        tests.mkdir()
        (tests/'test_present.py').write_text('import unittest\nclass Present(unittest.TestCase):\n def test_real(self): self.assertEqual(2+2,4)\n',encoding='utf-8')
        (tests/'test_empty.py').write_text('# no regression cases\n',encoding='utf-8')
        for pattern in ('test_missing.py','test_empty.py'):
            with self.subTest(pattern=pattern):
                (self.target/'development-config.json').write_text(json.dumps({'test_patterns':['test_present.py',pattern]}),encoding='utf-8')
                result=subprocess.run([sys.executable,'-B',str(ROOT/'examples/self-development/test_driver.py')],cwd=self.target,capture_output=True,text=True,timeout=10)
                self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn(pattern,result.stderr)

    def test_closure_preserves_worktree_bytes_and_excludes_untracked_private_and_git_configuration(self):
        public = ["docs/command-map.md", "SKILL.md", "LICENSE", "formal/lean-toolchain",
                  "formal/lakefile.lean", "integrations/omp/rds-status.mjs", "tests/public.py",
                  ".github/assets/public.svg"]
        for name in public:
            self.tracked(name)
        changed = self.source / "docs/command-map.md"
        changed.write_text("current working-tree revision", encoding="utf-8")
        for name in [".rds/private.json", ".codex/instructions.md", "docs/.env.secret",
                     "native/project/target/private", "formal/.lake/private", "tests/__pycache__/secret.pyc"]:
            self.tracked(name, "PRIVATE_MARKER")
        (self.source / "docs/untracked.md").write_text("PRIVATE_MARKER", encoding="utf-8")
        development.git(self.source, "config", "remote.private.url", "PRIVATE_MARKER")
        entries = development.freeze_public_source(self.source, self.target)
        self.assertEqual({row["path"] for row in entries}, set(public))
        self.assertEqual((self.target / "docs/command-map.md").read_bytes(), changed.read_bytes())
        tracked = development.git(self.target, "ls-files", "-z").stdout.decode().split("\0")[:-1]
        self.assertEqual(set(tracked), set(public))
        snapshot = json.loads((self.target / "source-snapshot.json").read_text(encoding="utf-8"))
        self.assertIsNone(snapshot["source_head"], "An index-only source must not invent a commit")
        self.assertNotIn("PRIVATE_MARKER", (self.target / ".git/config").read_text(encoding="utf-8"))
        for entry in snapshot["files"]:
            self.assertEqual(entry["sha256"], hashlib.sha256((self.target / entry["path"]).read_bytes()).hexdigest())
        self.assertFalse((self.target / ".rds").exists())
        self.assertFalse((self.target / "docs/untracked.md").exists())
        self.assertFalse((self.target / ".codex").exists())
        self.assertFalse((self.target / "formal/.lake").exists())

    def test_source_commit_is_recorded_without_copying_commit_history(self):
        self.tracked("LICENSE")
        development.git(self.source, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                        "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "public fixture")
        head = development.git(self.source, "rev-parse", "HEAD").stdout.decode().strip()
        development.freeze_public_source(self.source, self.target)
        snapshot = json.loads((self.target / "source-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["source_head"], head)
        self.assertFalse((self.target / ".git/refs/heads").exists() and
                         any((self.target / ".git/refs/heads").iterdir()))

    def test_missing_tracked_public_input_is_rejected_before_copy(self):
        path = self.tracked("docs/missing.md")
        path.unlink()
        with self.assertRaisesRegex(ValueError, "Missing, linked or escaping"):
            development.freeze_public_source(self.source, self.target)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_git_symlink_mode_is_rejected_even_without_an_os_symlink(self):
        self.tracked("tests/link.py")
        blob = development.git(self.source, "hash-object", "-w", "tests/link.py").stdout.decode().strip()
        development.git(self.source, "update-index", "--cacheinfo", "120000", blob, "tests/link.py")
        with self.assertRaisesRegex(ValueError, "non-regular"):
            development.freeze_public_source(self.source, self.target)
        self.assertEqual(list(self.target.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

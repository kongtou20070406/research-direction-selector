"""Binding must leave prepared descendant method revisions recoverable."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_workspace as workspace
import rds_method_revision as revision
import test_rds_method_revision as method_fixture
import test_pr301_descendant_binding as fixture


class PendingRevisionBindingTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.DescendantBindingTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.child = method_fixture.MethodRevisionTests()
        temporary = tempfile.TemporaryDirectory
        with patch.object(method_fixture.tempfile, 'TemporaryDirectory',
                          side_effect=lambda *a, **k: temporary(*a, dir=self.f.root, **k)):
            self.child.setUp()
        self.addCleanup(self.child.doCleanups)

    def interrupted_revision(self, stop):
        proposal = self.child.proposal()
        replace, calls = revision._replace, []

        def interrupted(path, data):
            calls.append(path)
            if len(calls) == stop:
                raise OSError('interrupted descendant revision')
            replace(path, data)

        with patch.object(revision, '_replace', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'interrupted descendant'):
                revision.apply(self.child.store, proposal)
        snapshot = self.child.store.snapshot()
        self.assertEqual(snapshot['method_revision_pending']['id'], proposal['id'])
        self.assertEqual(snapshot['runs'], [])
        self.assertEqual(snapshot['budget']['wall_seconds']['reserved'], 0)
        before = self.f.dump(self.f.store), self.f.dump(self.child.store)
        files = {name: (self.child.root / name).read_bytes() for name in ('code.py', 'protocol.json')}
        with self.assertRaisesRegex(ValueError, 'active descendant'):
            workspace.bind(self.f.root)
        self.assertEqual((self.f.dump(self.f.store), self.f.dump(self.child.store)), before)
        self.assertEqual({name: (self.child.root / name).read_bytes() for name in files}, files)
        (self.child.root / 'replacement.py').unlink()
        self.assertEqual(revision.apply(self.child.store, proposal)['status'], 'ADOPTED')
        self.assertIsNone(self.child.store.snapshot()['method_revision_pending'])
        self.assertEqual(workspace.bind(self.f.root)['status'], 'BOUND')

    def test_prepared_revision_can_resume_after_binding_refusal(self):
        self.interrupted_revision(1)

    def test_partially_copied_revision_can_resume_after_binding_refusal(self):
        self.interrupted_revision(2)


if __name__ == '__main__':
    unittest.main()

"""Advisor knowledge publication must respect inherited workspace admission."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_advisor
import rds_workspace as workspace
import test_pr301_descendant_binding as fixture


class AdvisorWorkspaceAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.DescendantBindingTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.child = self.f.child()
        self.advisor = rds_advisor.RDSAdvisor(self.child.root)
        self.document = self.child.root / 'guide.txt'
        self.document.write_text('Consider learning rate and overfitting.', encoding='utf-8')

    def test_new_and_existing_knowledge_refuse_after_binding(self):
        self.advisor.ingest_document(self.document)
        before = self.advisor.knowledge_db.read_bytes(), self.f.dump(self.child)
        other = self.f.child('other')
        fresh = rds_advisor.RDSAdvisor(other.root)
        workspace.bind(self.f.root)
        for advisor in (self.advisor, fresh):
            with self.subTest(root=advisor.root_dir):
                with self.assertRaisesRegex(ValueError, 'identity does not match'):
                    advisor.ingest_document(self.document, topic='new topic')
        self.assertFalse(fresh.knowledge_db.exists())
        self.assertEqual((self.advisor.knowledge_db.read_bytes(), self.f.dump(self.child)), before)

    def test_real_advise_doc_refuses_without_creating_database(self):
        workspace.bind(self.f.root)
        before = self.f.dump(self.child)
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.child.root),
             'advise', '--doc', str(self.document)],
            capture_output=True, text=True, timeout=15, env={**os.environ, 'RDS_USAGE_LOG': '0'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('identity does not match', result.stderr + result.stdout)
        self.assertFalse(self.advisor.knowledge_db.exists())
        self.assertEqual(self.f.dump(self.child), before)

    def test_binding_during_parsing_is_rechecked_before_publication(self):
        read = rds_advisor._read_document

        def bind_after_read(path):
            result = read(path)
            workspace.bind(self.f.root)
            return result

        with patch.object(rds_advisor, '_read_document', side_effect=bind_after_read):
            with self.assertRaisesRegex(ValueError, 'identity does not match'):
                self.advisor.ingest_document(self.document)
        self.assertFalse(self.advisor.knowledge_db.exists())

    def test_canonical_knowledge_ingestion_remains_supported(self):
        workspace.bind(self.f.root)
        result = rds_advisor.RDSAdvisor(self.f.root).ingest_document(self.document)
        self.assertEqual(result['status'], 'INGESTED')
        self.assertEqual(result['excerpts_extracted'], 1)
        self.assertEqual(workspace.coverage(self.f.root)['status'], 'BOUND')


if __name__ == '__main__':
    unittest.main()

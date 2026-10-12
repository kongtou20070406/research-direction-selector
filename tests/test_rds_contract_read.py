"""Frozen 0993571 lineage outcomes and the ProjectStore read boundary."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import rds_contract_read as reader
import rds_method_revision as revision
import rds_project
from rds_project_values import canonical, digest


class ContractReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-contract-read-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        state = self.root / '.rds'
        state.mkdir()
        self.db = sqlite3.connect(state / 'project.sqlite3')
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE contract (id INTEGER PRIMARY KEY, body TEXT, sha256 TEXT)')
        self.db.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, body TEXT)')
        self.old_bytes, self.new_bytes = b'old code', b'new code'
        self.old_sha = hashlib.sha256(self.old_bytes).hexdigest()
        self.new_sha = hashlib.sha256(self.new_bytes).hexdigest()
        self.genesis = {'schema': 1, 'bindings': [{'path': 'code.py', 'role': 'code',
                                                  'sha256': self.old_sha}],
                        'method_evolution': {'schema': 1, 'max_revisions': 2,
                                             'code_paths': ['code.py']},
                        'advisor_policy': {'context': {'decision': 'frozen'}, 'graph': []}}
        self.db.execute('INSERT INTO contract VALUES (1, ?, ?)',
                        (canonical(self.genesis), digest(self.genesis)))
        self.db.commit()

    def event(self, value):
        value['sha256'] = reader._event_hash(value)
        self.db.execute('INSERT INTO events(body) VALUES (?)', (canonical(value),))
        self.db.commit()
        return value

    def prepare(self):
        revised = deepcopy(self.genesis)
        revised['bindings'][0]['sha256'] = self.new_sha
        revised['advisor_policy']['graph'] = ['revised']
        cas = self.root / '.rds' / 'cas'
        cas.mkdir()
        (cas / (self.old_sha + '.method-bytes')).write_bytes(self.old_bytes)
        (cas / (self.new_sha + '.method-bytes')).write_bytes(self.new_bytes)
        prepared = self.event({'kind': reader.PREPARED, 'id': 'r1',
                               'genesis_sha256': digest(self.genesis),
                               'parent_sha256': digest(self.genesis),
                               'proposal_sha256': 'proposal', 'contract': revised,
                               'contract_sha256': digest(revised),
                               'changes': [{'path': 'code.py', 'before_sha256': self.old_sha,
                                            'after_sha256': self.new_sha}]})
        return prepared

    def adopt(self, prepared):
        return self.event({'kind': reader.ADOPTED, 'id': 'r1',
                           'genesis_sha256': digest(self.genesis),
                           'parent_sha256': digest(self.genesis),
                           'prepared_sha256': prepared['sha256'],
                           'contract_sha256': prepared['contract_sha256'],
                           'proposal_sha256': prepared['proposal_sha256']})

    def test_genesis_pending_adopted_and_actual_store_boundary(self):
        self.assertEqual(reader.effective_contract(self.db), self.genesis)
        self.assertIsNone(reader.pending_revision(self.db))
        prepared = self.prepare()
        self.assertEqual(reader.contract_history(self.db),
                         [{'sha256': digest(self.genesis), 'contract': self.genesis}])
        self.assertEqual(reader.pending_revision(self.db), prepared)
        self.assertEqual(reader.effective_contract(self.db), self.genesis)
        self.adopt(prepared)
        self.assertEqual(reader.effective_contract(self.db), prepared['contract'])
        self.assertIsNone(reader.pending_revision(self.db))
        self.assertEqual(len(reader.contract_history(self.db)), 2)
        with patch.object(revision, 'effective_contract', side_effect=AssertionError('high reader used')):
            with patch.object(rds_project, 'effective_contract', wraps=reader.effective_contract) as low:
                self.assertEqual(rds_project.ProjectStore._contract(self.db), prepared['contract'])
                low.assert_called_once_with(self.db)

    def test_activation_and_original_authority_boundary(self):
        self.genesis.pop('advisor_policy')
        self.db.execute('UPDATE contract SET body=?, sha256=? WHERE id=1',
                        (canonical(self.genesis), digest(self.genesis)))
        policy = {'schema': 1, 'context': {}, 'graph': {}, 'routes': [], 'observations': []}
        activated = {**self.genesis, 'advisor_policy': policy}
        self.event({'kind': reader.ACTIVATED, 'schema': 1,
                    'genesis_sha256': digest(self.genesis),
                    'parent_sha256': digest(self.genesis),
                    'contract': activated, 'contract_sha256': digest(activated),
                    'policy_sha256': digest(policy)})
        self.assertEqual(reader.effective_contract(self.db), activated)
        self.assertEqual(len(reader.contract_history(self.db)), 2)
        self.db.execute('DELETE FROM events')
        activated['budget'] = {'wall_seconds': 999}
        self.event({'kind': reader.ACTIVATED, 'schema': 1,
                    'genesis_sha256': digest(self.genesis),
                    'parent_sha256': digest(self.genesis),
                    'contract': activated, 'contract_sha256': digest(activated),
                    'policy_sha256': digest(policy)})
        with self.assertRaisesRegex(ValueError, '^Advisor activation must add exactly one policy to a non-owned contract$'):
            reader.effective_contract(self.db)

    def test_frozen_parent_errors_for_corrupt_lineage_and_cas(self):
        with self.assertRaisesRegex(ValueError, '^Genesis contract differs$'):
            reader.contract_history(self.db, {})
        prepared = self.prepare()
        cas = self.root / '.rds' / 'cas' / (self.old_sha + '.method-bytes')
        cas.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, '^Method CAS integrity failure$'):
            reader.effective_contract(self.db)
        cas.write_bytes(self.old_bytes)
        bad = {**prepared, 'parent_sha256': '0' * 64}
        bad['sha256'] = reader._event_hash(bad)
        self.db.execute('UPDATE events SET body=? WHERE id=1', (canonical(bad),))
        with self.assertRaisesRegex(ValueError, '^Method revision parent mismatch or cycle$'):
            reader.effective_contract(self.db)
        bad['sha256'] = '0' * 64
        self.db.execute('UPDATE events SET body=? WHERE id=1', (canonical(bad),))
        with self.assertRaisesRegex(ValueError, '^Method revision event integrity failure$'):
            reader.effective_contract(self.db)

    def test_import_isolation_and_readonly_cas(self):
        code = ("import sys; import rds_contract_read; "
                "assert not {'rds_project','rds_method_revision','rds_project_lifecycle',"
                "'rds_project_hooks','rds_cli','rds_quick'} & set(sys.modules)")
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT / 'scripts',
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        prepared = self.prepare()
        before = sorted((p.name, p.read_bytes()) for p in (self.root / '.rds' / 'cas').iterdir())
        self.assertEqual(reader.pending_revision(self.db), prepared)
        self.assertEqual(sorted((p.name, p.read_bytes()) for p in (self.root / '.rds' / 'cas').iterdir()), before)


if __name__ == '__main__':
    unittest.main()

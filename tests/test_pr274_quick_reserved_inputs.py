"""Reserved native state names must refuse before a QUICK allowance is charged."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import rds_quick as quick
from rds_project import ProjectStore
import test_rds_quick as fixtures


class ReservedQuickInputTests(unittest.TestCase):
    def test_actual_explicit_data_refuses_before_budget_or_child(self):
        f=fixtures.QuickTests('runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.initialize_policy_ledger()
        target=f.root/'fixtures/.rds/project.sqlite3'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'original data, not a native ledger')
        before=ProjectStore(f.ledger).snapshot()
        result=f.job('reserved-input',False,*f.policy_options(),'--bind','data=fixtures/.rds/project.sqlite3')
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('operational state',result.stderr)
        self.assertFalse((f.root/'.rds/exec/reserved-input').exists())
        after=ProjectStore(f.ledger).snapshot()
        for name in ('budget','runs','receipts','contract_sha256'):
            self.assertEqual(after[name],before[name],name)
        self.assertEqual(target.read_bytes(),b'original data, not a native ledger')

    def test_nested_state_implicit_code_refuses_but_similar_name_remains_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            native=root/'fixtures/.rds/probe.py'
            native.parent.mkdir(parents=True)
            native.write_bytes(b'print("unexecuted reserved input")\n')
            with self.assertRaisesRegex(ValueError,'operational state'):
                quick._inputs(root,[sys.executable,'-B','fixtures/.rds/probe.py'],[])
            ordinary=root/'fixtures/.rds.retained/probe.py'
            ordinary.parent.mkdir()
            ordinary.write_bytes(native.read_bytes())
            files,raw=quick._inputs(root,[sys.executable,'-B','fixtures/.rds.retained/probe.py'],[])
            self.assertIn(ordinary,files)
            self.assertEqual(raw[ordinary],native.read_bytes())
            self.assertEqual(ordinary.read_bytes(),native.read_bytes())


if __name__=='__main__':
    unittest.main()

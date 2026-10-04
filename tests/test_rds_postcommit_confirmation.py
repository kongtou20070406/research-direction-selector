"""Actual CPU training and program-owned confirmation challenge boundaries."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore, file_sha, canonical
from rds_domain_confirmation import inspect_confirmation
import rds_postcommit_confirmation as post

TORCH = importlib.util.find_spec('torch') is not None


class PostcommitConfirmationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='rds-postcommit-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'campaign'
        spec = importlib.util.spec_from_file_location('postcommit_example', ROOT / 'examples/autonomy/run.py')
        self.example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.example)
        self.trace = []

    def build(self, *, zero=False, trained=False, forged=False, wrong_samples=False, premature=False):
        contract = self.example.build(self.root, 'deep_learning', dl_profile='postcommit_mlp')
        if zero or trained:
            claim = json.loads((self.root/'claim.json').read_text(encoding='utf-8'))
            if zero:
                claim['max_mse'] = 0
            if trained:
                self.example.write(self.root,'candidate.py',self.example.postcommit_sources()[1])
            self.example.write(self.root, 'claim.json', claim)
            nodes = contract['advisor_policy']['graph']['nodes']
            next(n for n in nodes if n['id']=='failed-pilot')['executable']['preconditions'] = [
                {'fact':'run.confirmation.succeeded','op':'eq','value':True}]
            next(n for n in nodes if n['id']=='candidate')['executable']['preconditions'] = []
            nodes.sort(key=lambda n:n['id'] != 'candidate')
        if premature:
            nodes = contract['advisor_policy']['graph']['nodes']
            next(n for n in nodes if n['id']=='confirmation')['executable']['preconditions'] = []
            nodes.sort(key=lambda n:n['id'] != 'confirmation')
        if forged or wrong_samples:
            text = (self.root/'evaluator.py').read_text(encoding='utf-8')
            if forged:
                text = text.replace("'verdict':'PASS' if loss<=claim['max_mse'] else 'FAIL'", "'verdict':'PASS'")
            if wrong_samples:
                text = text.replace("data=samples(claim,challenge['seed'])", "data=samples(claim,challenge['seed'])\ndata['x'][0][0]+=0.01")
            self.example.write(self.root, 'evaluator.py', text)
        # Freeze these intentional negative declarations before genesis.
        for b in contract['bindings']:
            b['sha256'] = file_sha(self.root/b['path'])
        protocol = json.loads((self.root/'protocol.json').read_text(encoding='utf-8'))
        protocol.update({r+'_sha256':ProjectStore._role_sha(contract,r) for r in ('code','config','data')})
        self.example.write(self.root,'protocol.json',protocol)
        for b in contract['bindings']:
            if b['path']=='protocol.json':
                b['sha256']=file_sha(self.root/'protocol.json')
        declaration=contract['advisor_policy']['confirmation']
        for field in ('claim','evaluator'):
            declaration[field]['sha256']=file_sha(self.root/declaration[field]['path'])
        for route in contract['advisor_policy']['routes']:
            route['manifest']['protocol']['sha256']=file_sha(self.root/'protocol.json')
        self.example.write(self.root,'contract.json',contract)
        self.cli('project','init','--contract','contract.json')
        self.store=ProjectStore(self.root)
        return contract

    def cli(self,*args, allow_failure=False):
        p=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/rds_cli.py'),'--root',str(self.root),*args],
                         cwd=self.root,capture_output=True,text=True,encoding='utf-8',timeout=100,
                         env={**os.environ,'RDS_USAGE_DB':str(self.root/'.rds/usage.sqlite3')})
        self.trace.append({'argv':list(args),'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
        evidence=os.environ.get('RDS_AUTONOMY_EVIDENCE')
        if evidence:
            directory=Path(evidence)/self._testMethodName
            directory.mkdir(parents=True,exist_ok=True)
            (directory/'cli-transcript.json').write_text(json.dumps(self.trace),encoding='utf-8')
        if not allow_failure:
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        if allow_failure and not p.stdout.strip():
            return {'returncode':p.returncode,'stderr':p.stderr}
        return json.loads(p.stdout)

    def events(self):
        with self.store._db(True) as db:
            return [json.loads(r['body']) for r in db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')=? ORDER BY id",(post.EVENT,))]

    @unittest.skipUnless(TORCH,'optional CPU torch dependency unavailable')
    def test_actual_training_and_new_samples_through_owned_execution(self):
        self.build(trained=True)
        contract=self.store.snapshot()['contract']
        routes={r['manifest']['id']:r['manifest'] for r in contract['advisor_policy']['routes']}
        self.store.register(routes['candidate'])
        candidate=self.store.execute('candidate')
        self.assertEqual(candidate['run_status'],'SUCCEEDED',candidate)
        self.assertEqual(self.events(),[])
        self.store.register(routes['confirmation'])
        original_events=self.events()
        confirmation=self.store.execute('confirmation')
        self.assertEqual(confirmation['run_status'],'SUCCEEDED',confirmation)
        result=inspect_confirmation(self.store,contract)
        self.assertEqual(result['task_confirmation'],'PASS',result)
        self.assertEqual(result['scientific_support'],'UNKNOWN')
        self.assertEqual(result['confirmation_independence'],'GENERATED_AFTER_WEIGHT_COMMIT')
        receipts=self.store.snapshot()['receipts']
        self.store.recover('confirmation')
        self.assertEqual(self.store.snapshot()['receipts'],receipts)
        self.assertEqual(self.events(),original_events)

    @unittest.skipUnless(TORCH,'optional CPU torch dependency unavailable')
    def test_real_mlp_repair_and_reserved_challenge_recover_with_one_seed(self):
        self.build()
        first=self.cli('project','drive','--max-steps','3')
        self.assertEqual(len(first['executed']),3,first)
        state=self.store.snapshot()
        self.assertEqual(len(state['contract_history']),2)
        self.assertEqual(self.events(),[])
        candidate=next(r for r in state['receipts'] if r['run_id']=='candidate')
        self.assertEqual(candidate['run_status'],'SUCCEEDED')
        spec=next(r['manifest'] for r in state['contract']['advisor_policy']['routes'] if r['manifest']['id']=='confirmation')
        self.example.write(self.root,'confirm-manifest.json',spec)
        self.cli('project','create','--manifest','confirm-manifest.json')
        original_event=self.events()
        self.assertEqual(len(original_event),1)
        resumed=self.cli('project','drive','--max-steps','1')
        self.assertEqual(resumed['status'],'GOAL_CONFIRMED',resumed)
        after=self.store.snapshot()
        result=inspect_confirmation(self.store,after['contract'])
        self.assertEqual(result['task_confirmation'],'PASS',result)
        self.assertEqual(result['confirmation_independence'],'GENERATED_AFTER_WEIGHT_COMMIT')
        self.assertEqual(result['scientific_support'],'UNKNOWN')
        self.assertEqual(result['population_generalization'],'UNKNOWN')
        report=json.loads((self.root/'outputs/confirmation.json').read_text(encoding='utf-8'))
        training=json.loads((self.root/'data.json').read_text(encoding='utf-8'))
        self.assertNotEqual(report['samples'],training)
        self.assertEqual(self.events(),original_event)
        self.assertEqual(len(after['runs']),4)
        self.cli('project','recover','--id','confirmation')
        again=self.cli('project','drive','--max-steps','1')
        self.assertEqual(again['status'],'GOAL_CONFIRMED',again)
        self.assertEqual(self.store.snapshot()['receipts'],after['receipts'])
        self.assertEqual(self.store.snapshot()['exposures'],after['exposures'])
        self.assertEqual(self.events(),original_event)
        # Damage the committed CAS bytes; no new seed or attempt may conceal it.
        from rds_math import blob
        ref=original_event[0]['challenge']
        path=self.root/ref['path']
        original=blob(self.root,ref)
        path.write_bytes(original.replace(b'"seed":"',b'"seed":"f',1))
        unknown=inspect_confirmation(self.store,after['contract'])
        self.assertEqual(unknown['task_confirmation'],'UNKNOWN',unknown)
        path.write_bytes(original)
        self.assertEqual(self.events(),original_event)
        self.assertEqual(inspect_confirmation(self.store,after['contract'])['task_confirmation'],'PASS')

    def test_premature_confirmation_allocates_no_challenge_or_attempt(self):
        self.build(premature=True)
        spec=next(r['manifest'] for r in self.store.snapshot()['contract']['advisor_policy']['routes'] if r['manifest']['id']=='confirmation')
        self.example.write(self.root,'premature.json',spec)
        self.cli('project','create','--manifest','premature.json',allow_failure=True)
        self.assertEqual(self.events(),[])
        self.assertEqual(self.store.snapshot()['runs'],[])
        self.assertEqual(self.store.snapshot()['receipts'],[])
        self.assertEqual(self.store.snapshot()['budget']['wall_seconds']['reserved'],0)

    @unittest.skipUnless(TORCH,'optional CPU torch dependency unavailable')
    def test_original_wrong_weights_are_a_finite_counterexample(self):
        self.build(zero=True)
        self.cli('project','advance')
        self.cli('project','advance')
        state=self.store.snapshot()
        result=inspect_confirmation(self.store,state['contract'])
        self.assertEqual(result['execution'],{'candidate':'SUCCEEDED','confirmation':'SUCCEEDED'},result)
        self.assertEqual(result['task_confirmation'],'FAIL',result)
        self.assertGreater(result['measured_mse'],0)
        event=self.events()[0]
        forged=deepcopy(event)
        with self.store._db() as db:
            db.execute('INSERT INTO events(body) VALUES (?)',(canonical(forged),))
        self.assertEqual(inspect_confirmation(self.store,state['contract'])['task_confirmation'],'UNKNOWN')
        self.assertEqual(len(self.store.snapshot()['receipts']),2)

    @unittest.skipUnless(TORCH,'optional CPU torch dependency unavailable')
    def test_signed_self_verdict_and_changed_samples_do_not_confirm(self):
        for mode in ('forged','wrong_samples'):
            with self.subTest(mode=mode):
                # Each declaration/attempt is independent and preserves its originals.
                if mode=='wrong_samples':
                    self.root=self.root.parent/'wrong-samples'
                self.build(zero=True,**{mode:True})
                self.cli('project','advance')
                self.cli('project','advance')
                state=self.store.snapshot()
                self.assertEqual(len(state['receipts']),2)
                result=inspect_confirmation(self.store,state['contract'])
                self.assertEqual(result['execution']['confirmation'],'SUCCEEDED',result)
                self.assertEqual(result['task_confirmation'],'UNKNOWN',result)

    def test_architecture_and_seed_bounds_cannot_expand_scope(self):
        _,_,claim,_,_=self.example.postcommit_sources()
        invalid=deepcopy(claim)
        invalid['layers']=[1,8,8,8,1]
        with self.assertRaises(ValueError):
            post.validate_claim(invalid)
        with self.assertRaises(ValueError):
            post.samples(claim,'0'*63)
        self.assertEqual(post.samples(claim,'1'*64),post.samples(claim,'1'*64))


if __name__=='__main__':
    unittest.main()

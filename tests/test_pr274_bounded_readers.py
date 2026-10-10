"""Real sidecar and native CAS reads reject before blocking or allocation."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / 'scripts'))
import rds_campaign as campaign
import rds_hypergraph_readable as readable
import rds_jump as jump
from rds_dialogue import _report
from rds_project import ProjectStore, canonical
from rds_quick import cas_json
import test_jump_interpreter_boundary as interpreter_fixture
import test_rds_campaign_binding as campaign_fixture

REPORT_LIMIT = 32 * 1024 * 1024

class UnsafeAdmissionTests(unittest.TestCase):
    def test_unsafe_bound_shebang(self):
        for shebang in ('#!/usr/bin/env python3', '#!/bin/bash --login'):
            for legacy in (False,True):
                with self.subTest(shebang=shebang,legacy=legacy), tempfile.TemporaryDirectory() as directory:
                    def change(store,contract,plan):
                        worker=store.root/'worker.exec'
                        worker.write_text(shebang+'\nprint("frozen body")\n',encoding='utf-8')
                        binding=next(b for b in contract['bindings'] if b['path']=='worker.py')
                        binding.update(path='worker.exec',sha256=interpreter_fixture.file_sha(worker))
                        plan['generator_code_paths'][0]='worker.exec'
                        if legacy:
                            plan.pop('generator_code_paths')
                        for stage in plan['stages']:
                            argv=next(a for a in contract['allowed_commands'] if a==stage['run']['argv'])
                            argv[:]=['worker.exec',*argv[3:]]
                            stage['run']['argv']=argv
                    with patch.object(ProjectStore,'_command',lambda store,argv:str(store.root/'worker.exec')):
                        root,store=interpreter_fixture.InterpreterBoundaryTests().freeze(Path(directory)/'project',change)
                        before=store.snapshot()
                        with patch.object(store,'register',side_effect=AssertionError('no registration')):
                            with self.assertRaisesRegex(ValueError,'shebang'):
                                jump.load_plan(store,before)
                        self.assertEqual(store.snapshot(),before)

    def test_unsafe_native_state_file(self):
        helper=campaign_fixture.CampaignBindingTests('runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        sibling=helper.workspace/'damaged-sibling'
        sibling.mkdir()
        state=sibling/'.rds'
        state.write_bytes(b'original damaged native scope')
        before=helper.store.snapshot()
        with self.assertRaisesRegex(ValueError,'state directory'):
            campaign.bind(helper.store,helper.workspace)
        self.assertFalse(helper.marker.exists())
        self.assertEqual(helper.events(),[])
        self.assertEqual(helper.store.snapshot(),before)
        self.assertEqual(state.read_bytes(),b'original damaged native scope')

    def test_unsafe_native_state_dangling_symlink(self):
        helper=campaign_fixture.CampaignBindingTests('runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        sibling=helper.workspace/'dangling-sibling'
        sibling.mkdir()
        state=sibling/'.rds'
        try:
            state.symlink_to(sibling/'missing-state',target_is_directory=True)
        except (OSError,NotImplementedError) as exc:
            self.skipTest('Symlink privilege unavailable: '+str(exc))
        before=helper.store.snapshot()
        with self.assertRaisesRegex(ValueError,'state directory'):
            campaign.bind(helper.store,helper.workspace)
        self.assertFalse(helper.marker.exists())
        self.assertEqual(helper.events(),[])
        self.assertEqual(helper.store.snapshot(),before)
        self.assertTrue(state.is_symlink())

class BoundedReaderTests(unittest.TestCase):
    def test_readable_rejects_character_device(self):
        with self.assertRaisesRegex(ValueError,'regular file'):
            readable.load_readable(os.devnull,{})

    @unittest.skipUnless(hasattr(os,'mkfifo'),'POSIX FIFO unavailable')
    def test_readable_rejects_fifo_without_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / 'sidecar.json'
            os.mkfifo(fifo)
            code = "import sys;sys.path.insert(0,sys.argv[1]);import rds_hypergraph_readable as r\ntry:r.load_readable(sys.argv[2],{})\nexcept ValueError as e:\n assert 'regular file' in str(e),str(e)\nelse:raise AssertionError('FIFO was accepted')"
            result=subprocess.run([sys.executable,'-B','-c',code,str(ROOT/'scripts'),str(fifo)],capture_output=True,text=True,timeout=3)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_native_readers_reject_oversize_before_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            raw=canonical({'padding':'x' * REPORT_LIMIT}).encode('utf-8')
            sha=hashlib.sha256(raw).hexdigest()
            path=root/'.rds/cas'/(sha+'.json')
            path.parent.mkdir(parents=True)
            path.write_bytes(raw)
            ref={'path':str(path),'sha256':sha,'bytes':len(raw)}
            for reader in (lambda:campaign._json_cas(root,ref),lambda:_report(ProjectStore(root),ref)):
                with patch.object(Path,'open',side_effect=AssertionError('oversize opened')) as opened, patch.object(os,'open',side_effect=AssertionError('oversize descriptor opened')) as descriptor_opened:
                    with self.assertRaisesRegex(ValueError,'binding|byte|limit'):
                        reader()
                    opened.assert_not_called()
                    descriptor_opened.assert_not_called()

    def test_native_publication_exact_limit_and_plus_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            overhead=len(canonical({'padding':''}).encode('utf-8'))
            value={'padding':'x'*(REPORT_LIMIT-overhead)}
            ref=cas_json(root,value,max_bytes=REPORT_LIMIT)
            self.assertEqual(ref['bytes'],REPORT_LIMIT)
            self.assertEqual(campaign._json_cas(root,ref),value)
            self.assertEqual(_report(ProjectStore(root),ref),value)
            files=set((root/'.rds/cas').iterdir())
            value['padding']+='x'
            with self.assertRaisesRegex(ValueError,'byte limit'):
                cas_json(root,value,max_bytes=REPORT_LIMIT)
            self.assertEqual(set((root/'.rds/cas').iterdir()),files)

if __name__=='__main__':
    unittest.main()

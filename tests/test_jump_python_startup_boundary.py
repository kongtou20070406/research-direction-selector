"""Effective frozen Python startup flags over native plan, argv and receipts."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import test_jump_interpreter_boundary as f


class PythonStartupBoundaryTests(unittest.TestCase):
    def admission(self, options, *, family='python3.13', legacy=False, bound=True, literal=None, refusal=None):
        with tempfile.TemporaryDirectory() as directory:
            def change(store, contract, plan):
                main=literal or 'worker.py'
                if literal:
                    (store.root/main).write_bytes((store.root/'worker.py').read_bytes())
                    contract['bindings'].append({'path':main,'role':'code','sha256':f.file_sha(store.root/main)})
                    plan['generator_code_paths'].append(main)
                if not bound:
                    plan['generator_code_paths'].remove('worker.py')
                    contract['bindings']=[b for b in contract['bindings'] if b['path']!='worker.py']
                if legacy:plan.pop('generator_code_paths')
                for stage in plan['stages']:
                    argv=next(a for a in contract['allowed_commands'] if a==stage['run']['argv'])
                    argv[:]=[family,*options,main,*argv[3:]]
                    stage['run']['argv']=argv
            resolved=str(Path(directory)/(family+'.exe' if os.name=='nt' else family))
            with patch.object(f.ProjectStore,'_command',return_value=resolved):
                root,store=f.InterpreterBoundaryTests().freeze(Path(directory)/'project',change)
                before=store.snapshot()
                with patch.dict(os.environ,{'PYTHONPATH':str(Path(directory)/'unbound'),'PYTHONINSPECT':'1'}), \
                     patch.object(store,'register',side_effect=AssertionError('registration forbidden')), \
                     patch.object(store,'execute',side_effect=AssertionError('execution forbidden')):
                    if refusal:
                        with self.assertRaisesRegex(ValueError,refusal):f.jump.load_plan(store,before)
                    else:self.assertEqual(len(f.jump.load_plan(store,before)['stages']),3)
                self.assertEqual(store.snapshot(),before)

    def test_plain_numeric_and_windowed_python_refuse_missing_effective_startup_flags(self):
        for family in ('python','python3.13','pythonw'):
            for legacy in (False,True):
                for options in (['-B'],['-I'],['-S'],['-E'],['-sE']):
                    self.admission(options,family=family,legacy=legacy,refusal='startup isolation')

    def test_consumed_and_post_main_flags_cannot_supply_startup_isolation(self):
        for legacy in (False,True):
            for options in (['-E','-W','-S'],['-E','-XS'],['-S','-W','-E'],['-S','-XE'],['-S','-X','-E']):
                self.admission(options,legacy=legacy,refusal='startup isolation')
        # Explicit main consumes all subsequent arguments, including -E/-S.
        for argv in (['python','-B','worker.py','-ES'],['python','-E','worker.py','-S'],
                     ['python','-S','worker.py','-E'],['python','-E','--','-S']):
            with self.assertRaisesRegex(ValueError,'startup isolation'):
                f.jump._python_script_operand(argv,frozen_startup=True)

    def test_effective_clusters_values_literal_main_and_binding_identity_remain_distinct(self):
        for legacy in (False,True):
            for options,literal in ((['-BES'],None),(['-SE'],None),(['-IS'],None),
                                    (['-tBES'],None),(['-ES','-W','-S'],None),
                                    (['-ES','-XS'],None),(['-ES','--'],'-literal.py')):
                self.admission(options,legacy=legacy,literal=literal)
            self.admission(['-ES'],legacy=legacy,bound=False,refusal='entrypoint.*frozen code')

    def test_inline_stdin_and_debug_presite_do_not_bypass_frozen_startup(self):
        for argv in (['python','-c','pass'],['python','-S','-c','pass'],['python','-'],['python','-B']):
            with self.assertRaisesRegex(ValueError,'startup isolation'):
                f.jump._python_script_operand(argv,frozen_startup=True)
        for argv in (['python','-ES','-c','pass'],['python','-ES','-'],['python','-ES']):
            self.assertIsNone(f.jump._python_script_operand(argv,frozen_startup=True))
        for option in (['-X','presite=unbound'],['-Xpresite=unbound']):
            with self.assertRaisesRegex(ValueError,'presite startup'):
                f.jump._python_script_operand(['python','-ES',*option,'worker.py'],frozen_startup=True)

    def test_actual_generator_ignores_post_init_host_startup_and_keeps_local_helpers_and_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root,store=f.example.build(Path(directory)/'project')
            plan=f.jump.load_plan(store,store.snapshot())
            original=list(plan['stages'][0]['run']['argv'])
            self.assertEqual(original[1],'-BES')
            external=Path(directory)/'unbound';external.mkdir();marker=external/'executed.txt'
            (external/'sitecustomize.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("unbound executed")\n',encoding='utf-8')
            host={'PYTHONPATH':str(external),'PYTHONINSPECT':'1','PYTHONHOME':str(external),
                  'PYTHON_PRESITE':'unbound','RDS_STARTUP_CONTROL':'retained'}
            with patch.dict(os.environ,host):
                before={k:os.environ[k] for k in host}
                result=f.jump.generate(root,1)
                self.assertEqual(result['status'],'JUMP_STEP_LIMIT')
                self.assertEqual({k:os.environ[k] for k in host},before)
            self.assertFalse(marker.exists())
            state=store.snapshot();self.assertEqual(len(state['runs']),1);self.assertEqual(len(state['receipts']),1)
            receipt=state['receipts'][0];self.assertEqual(receipt['argv'],original)
            self.assertEqual(receipt['run_status'],'SUCCEEDED');self.assertEqual(state['budget']['wall_seconds']['reserved'],0)
            self.assertTrue(any(a['path']=='out/probe.json' for a in receipt['artifacts']))
            # Probe's instrument helper and result projection actually completed.
            self.assertIsNotNone(f.jump.load_plan(store,state))

if __name__ == '__main__':
    unittest.main()

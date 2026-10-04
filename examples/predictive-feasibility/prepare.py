"""Prepare a small public CPU campaign; no mathematical or policy-gain claim."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

WORKER = '''import json,pathlib,sys,time
run,mode,output = sys.argv[1:]
with pathlib.Path("outputs/launches.txt").open("a",encoding="utf-8") as h: h.write(run+"\\n")
if mode.startswith("pilot"):
    time.sleep(.08 if mode == "pilot-slow" else .01)
    result = {"score": 1}
elif mode == "slow":
    time.sleep(15)
    result = {"value": sum(range(100))}
elif mode == "fast":
    result = {"value": 99*100//2}
pathlib.Path(output).write_text(json.dumps(result),encoding="utf-8")
'''

VERIFIER = '''import json,pathlib,sys,time
run,mode,output = sys.argv[1:]
with pathlib.Path("outputs/launches.txt").open("a",encoding="utf-8") as h: h.write(run+"\\n")
if mode == "pilot-verify":
    time.sleep(.01)
    result = {"score": 1}
else:
    path = pathlib.Path("outputs/fast.json")
    if not path.is_file(): path = pathlib.Path("outputs/slow.json")
    found = json.loads(path.read_text(encoding="utf-8"))["value"]
    result = {"verified": found == sum(range(100)), "observed": found}
pathlib.Path(output).write_text(json.dumps(result),encoding="utf-8")
'''


def prepare(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError('Use a new empty demonstration directory')
    def write(name, obj):
        (root / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    def sha(name):
        return hashlib.sha256((root / name).read_bytes()).hexdigest()
    (root / 'solver.py').write_text(WORKER, encoding='utf-8')
    write('config.json', {'size': 100})
    write('data.json', {'domain': 'integers 0..99; public synthetic fixture'})
    (root / 'verifier.py').write_text(VERIFIER, encoding='utf-8')
    bindings = [{'path': path, 'role': role, 'sha256': sha(path)} for role,path in
                [('code','solver.py'),('config','config.json'),('data','data.json'),('evaluator','verifier.py')]]
    routes, nodes, observations = [], [], []
    previous_pilot = None
    for run_id, mode, algorithm, units in [
            ('pilot-slow','pilot-slow','enumeration',1), ('pilot-fast','pilot-fast','closed-form',1),
            ('pilot-verify','pilot-verify','independent-verifier',1),
            ('slow','slow','enumeration',10000), ('fast','fast','closed-form',2),
            ('verify','verify','independent-verifier',1)]:
        protocol = {'code_sha256': sha('solver.py'), 'config_sha256': sha('config.json'),
                    'data_sha256': sha('data.json'), 'data_split':'public-synthetic',
                    'init':'none', 'seed':0, 'checkpoint':'none', 'schedule':'one operation',
                    'sample_work':{'units':units}, 'numeric_protocol':'Python exact integers',
                    'runtime_model_identity':{'algorithm':algorithm,'precision':'integer','hardware':'local-cpu'}}
        path = 'protocol-' + run_id + '.json'
        write(path, protocol)
        bindings.append({'path':path,'role':'protocol','sha256':sha(path)})
        # Final forecasts include process startup and a conservative safety
        # factor. Freeze enough hard headroom before any run; pilot limits and
        # the total campaign budget remain independently bounded.
        timeout = 7 if run_id.startswith('pilot') else 20
        manifest = {'schema':1,'id':run_id,'arm':'tool','control_id':None,
                    'protocol':{'path':path,'sha256':sha(path)},
                    'argv':[sys.executable,'-B','verifier.py' if 'verify' in run_id else 'solver.py',run_id,mode,'outputs/'+run_id+'.json'],
                    'outpaths':['outputs/'+run_id+'.json'], 'timeout_seconds':timeout,
                    'resource_estimates':{'wall_seconds':timeout+.2}}
        routes.append({'candidate':run_id,'manifest':manifest})
        conditions = []
        if run_id.startswith('pilot'):
            if previous_pilot:
                conditions = [{'fact':'run.'+previous_pilot+'.succeeded','op':'eq','value':True}]
            previous_pilot = run_id
        nodes.append({'id':run_id,'sources':['public synthetic fixture'], 'executable':{
            'decisions':['next'], 'preconditions':conditions,
            'action':{'id':run_id,'kind':'PAIRED_TEST','target':'verified','operation':mode,
                      'description':'Measure or independently check the integer-sum fixture',
                      'competing_explanations':['conditional scaling applies','scaling is inapplicable'],
                      'required_observables':['verified' if run_id=='verify' else run_id+'.result'],
                      'outcomes':[{'observation':'usable evidence','next_decision':'reassess complete-plan feasibility'},
                                  {'observation':'failure','next_decision':'preserve failure and revise method'}]}}})
        observations.append({'fact':'verified' if run_id=='verify' else run_id+'.result',
                             'run_id':run_id,'path':'outputs/'+run_id+'.json',
                             'selector':{'pointer':'/verified' if run_id=='verify' else '/score' if run_id.startswith('pilot') else '/value'}})
    model = lambda pilot: {'pilot_runs':[pilot], 'exponent':1, 'safety_factor':3,
                          'max_scale':20000, 'assumptions':['Constant cost per declared work unit on this unchanged runtime; conditional, not a guarantee']}
    feasibility = {'schema':1,'pilots':['pilot-slow','pilot-fast','pilot-verify'],
                   'max_pilot_wall_seconds':10, 'models':{r:model('pilot-'+r) for r in ('slow','fast','verify')},
                   'plans':[{'id':name,'steps':[name,'verify'],'goal_facts':['verified'],
                             'recovery_wall_seconds':.5,'delivery_wall_seconds':.2} for name in ('slow','fast')]}
    policy = {'schema':1,'context':{'decision':{'id':'next','goal_revision':'exact-sum-v1',
                'scope':{'domain':'public software fixture'},'goal_conditions':[{'fact':'verified','op':'eq','value':True}]}},
              'graph':{'nodes':nodes,'edges':[]},'routes':routes,'observations':observations,'feasibility':feasibility}
    contract = {'schema':1,'bindings':bindings,'allowed_commands':[r['manifest']['argv'] for r in routes],
                'output_roots':['outputs'],'budget':{'wall_seconds':100},'advisor_policy':policy,
                'method_evolution':{'schema':1,'max_revisions':4,'code_paths':['solver.py']},
                'stop_policy':{'schema':1,'wall_seconds':180,
                               'progress':{'window_seconds':30,'min_bytes':0}}}
    write('contract.json', contract)
    return deepcopy(contract)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    prepare(args.root)
    print(json.dumps({'contract':str(Path(args.root)/'contract.json'),'scientific_claim':None}))

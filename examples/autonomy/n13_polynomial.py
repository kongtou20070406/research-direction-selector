"""Informed real cover13 polynomial/Jacobian tool benchmark, not a blind solve.

Read only pinned public cover13 inputs; use a new empty sibling workspace.
Prepare the original baseline and request without external dispatch by default.
"""
import argparse
from collections import defaultdict
from fractions import Fraction
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import ProjectStore, canonical, file_sha
from rds_autonomy_worker import output_paths
from rds_autonomy import REQUESTED

PUBLIC_COMMIT = '089584973f41e6374ce110f6e5b5ee456df19352'
PUBLIC_FILES = {
    'cover13_core_layout.json': ('6d1a3456b38520ee79f94e19541ad6e8fa5398b5d052f77310121ffb696a7b72', 'f21cb90392d9e03688b59dd8ce2995c46fa4eac2'),
    'cover13_krawczyk_cert.json': ('d7f5d30fbeb1196f4232b6b7cc2509c67bdaf48cbd969389459c91e58dc360ee', '99117da4b33cf95f1901dd5e578de9c01396d0e7'),
    'verify_root_repaired.py': ('11ffe555d2df42b976f2d2485f16efc781c3550c5bd192e08cac73da7dc5c58a', '1069d7196f146cba5380c62c8d0ba76f5fa7bf81'),
}


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else canonical(value), encoding='utf-8')


def polynomial_data(core):
    for name, (sha256, git_sha) in PUBLIC_FILES.items():
        raw = (core / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha256 or hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() != git_sha:
            raise ValueError('Input is not the pinned public Git blob: ' + name)
    spec = importlib.util.spec_from_file_location('public_cover13_reference', core / 'verify_root_repaired.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    layout = json.loads((core / 'cover13_core_layout.json').read_text(encoding='utf-8-sig'))
    cert = json.loads((core / 'cover13_krawczyk_cert.json').read_text(encoding='utf-8-sig'))
    constraints, _ = module.build(layout)
    if len(constraints) != 56 or cert['N'] != 119 or len(cert['xnum']) != 119:
        raise ValueError('Pinned cover13 dimension changed')
    polynomials = []
    def append(terms):
        terms = [{'coefficient': str(value), 'powers': [list(pair) for pair in powers]}
                 for powers, value in sorted(terms.items()) if value]
        polynomials.append({'id': 'F' + str(len(polynomials)), 'terms': terms})
    for hessian, linear, constant in constraints:
        terms = defaultdict(Fraction)
        terms[()] += constant
        for i, value in linear.items():
            terms[((i, 1),)] += value
        for (i, j), value in hessian.items():
            powers = ((i, 2),) if i == j else tuple(sorted(((i, 1), (j, 1))))
            terms[powers] += value / 2
        append(terms)
    sign, objective = Fraction(layout['lambda_sign']), Fraction(layout['objective_sign'])
    for j in range(63):
        terms = defaultdict(Fraction)
        terms[()] = objective if j == 62 else Fraction(0)
        for i, (hessian, linear, _) in enumerate(constraints):
            terms[((63 + i, 1),)] += sign * linear.get(j, 0)
            for (row, k), value in hessian.items():
                if row == j:
                    terms[tuple(sorted(((k, 1), (63 + i, 1))))] += sign * value
        append(terms)
    center = [Fraction(int(a), int(cert['Qx'])) for a in cert['xnum']]
    points = [{'id': 'certificate-midpoint', 'coordinates': list(map(str, center))}]
    for index in (0, 118):
        point = center.copy()
        point[index] += Fraction(1, 10 ** 18)
        points.append({'id': 'declared-perturb-' + str(index), 'coordinates': list(map(str, point))})
    return {'schema': 1, 'variables': ['z' + str(i) for i in range(119)],
            'polynomials': polynomials, 'points': points}


REFERENCE_KERNEL = '''import json,pathlib
from fractions import Fraction as F
from public_reference import build,val,grad

def evaluate_geometry(data):
    layout=json.loads(pathlib.Path('cover13_core_layout.json').read_text(encoding='utf-8-sig'))
    cons,_=build(layout); ls=F(layout['lambda_sign']); os=F(layout['objective_sign'])
    pairs=sorted({(i,v) for i,p in enumerate(data['polynomials']) for term in p['terms'] for v,_ in term['powers']})
    values=[]; jacobian=[]
    for point in data['points']:
        z=list(map(F,point['coordinates'])); x=z[:63]; lam=z[63:]
        gradients=[grad(c,x) for c in cons]
        residuals=[val(c,x) for c in cons]
        residuals += [(os if j==62 else F(0))+ls*sum(lam[i]*gradients[i][j] for i in range(56)) for j in range(63)]
        # Preserve the public dense geometric construction as the old tool.
        matrix=[[F(0) for _ in range(119)] for _ in range(119)]
        for i in range(56):
            for j in range(63):
                matrix[i][j]=gradients[i][j]; matrix[56+j][63+i]=ls*gradients[i][j]
        for j in range(63):
            for k in range(63):
                matrix[56+j][k]=ls*sum(lam[i]*cons[i][0].get((j,k),F(0)) for i in range(56))
        values.append(list(map(str,residuals)))
        jacobian.append([[i,j,str(matrix[i][j])] for i,j in pairs])
    return {'values':values,'jacobian':jacobian}
'''

ORIGINAL_TOOL = '''"""Improve this public cover13 exact polynomial tool by a structurally different method.
Expose evaluate(data)->{values,jacobian}; keep every rational result, point and
structural derivative position, including evaluated zeros. The fixed outer
driver owns the workload; parent receipts own trusted timing. Use data.json sparse terms to avoid
rebuilding the dense geometric Jacobian. Scientific/global-proof claims remain
UNKNOWN. No float substitution, canned result table, or evaluator modification.
"""
from reference_kernel import evaluate_geometry

def evaluate(data):
    return evaluate_geometry(data)
'''

DRIVER = '''import hashlib,json,pathlib,sys,time
from candidate import evaluate
data=json.loads(pathlib.Path('data.json').read_text(encoding='utf-8-sig')); config=json.loads(pathlib.Path('config.json').read_text(encoding='utf-8-sig'))
if config['repetitions']!=1: raise ValueError('Every measured workload needs its own complete retained result')
started=time.perf_counter(); result=evaluate(data); diagnostic_seconds=time.perf_counter()-started
answer={'schema':1,'inputs_sha256':hashlib.sha256(pathlib.Path('data.json').read_bytes()).hexdigest(),
        'point_ids':[p['id'] for p in data['points']], 'polynomial_ids':[p['id'] for p in data['polynomials']],
        'values':result['values'],'jacobian':result['jacobian']}
target=pathlib.Path(sys.argv[1]); target.write_text(json.dumps(answer),encoding='utf-8')
target.with_suffix('.timing.json').write_text(json.dumps({'seconds':[diagnostic_seconds],'repetitions':1,
    'clock':'candidate-process perf_counter; untrusted diagnostic only',
    'authoritative_timing':'owning parent receipt.resources.wall_seconds.measured',
    'scope':'one complete exact evaluation of all three declared points'}),encoding='utf-8')
'''

EVALUATOR = '''import hashlib,json,pathlib,sqlite3
from reference_kernel import evaluate_geometry
sha=lambda p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
data=json.loads(pathlib.Path('data.json').read_text(encoding='utf-8-sig')); expected=evaluate_geometry(data)
candidate=json.loads(pathlib.Path('outputs/candidate.json').read_text(encoding='utf-8-sig')); baseline=json.loads(pathlib.Path('outputs/baseline.json').read_text(encoding='utf-8-sig'))
ok=all(output.get('values')==expected['values'] and output.get('jacobian')==expected['jacobian'] and
    output.get('inputs_sha256')==sha('data.json') for output in (candidate,baseline))
with sqlite3.connect('.rds/project.sqlite3') as db:
    receipt=json.loads(db.execute("SELECT body FROM receipts WHERE run_id='candidate'").fetchone()[0])
answer={'candidate_receipt_sha256':receipt['sha256'],'claim_sha256':sha('claim.json'),
        'evaluator_sha256':sha('evaluator.py'),'inputs_sha256':sha('data.json'),'verdict':'PASS' if ok else 'FAIL',
        'scope':'Finite rational F/J evaluation only; minpoly, root, coverage and global optimality UNKNOWN'}
pathlib.Path('outputs/confirmation.json').write_text(json.dumps(answer),encoding='utf-8')
'''


def build(root, core, codex_path, local_proposal=None):
    if root == REPO or root.is_relative_to(REPO) or (root.exists() and any(root.iterdir())):
        raise ValueError('Choose a new empty sibling workspace')
    data = polynomial_data(core)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'outputs').mkdir()
    for path in sorted((REPO / 'scripts').glob('*.py')):
        (root / 'engine').mkdir(exist_ok=True)
        shutil.copyfile(path, root / 'engine' / path.name)
    shutil.copyfile(core / 'verify_root_repaired.py', root / 'public_reference.py')
    shutil.copyfile(core / 'cover13_core_layout.json', root / 'cover13_core_layout.json')
    write(root, 'data.json', data)
    write(root, 'claim.json', {'schema': 1, 'kind': 'polynomial_rational_evaluation', 'quantities': ['values', 'sparse_jacobian']})
    write(root, 'config.json', {'repetitions': 1, 'scope': 'informed public finite n13 polynomial F/J benchmark'})
    for name, code in [('candidate.py', ORIGINAL_TOOL), ('benchmark_driver.py', DRIVER),
                       ('reference_kernel.py', REFERENCE_KERNEL), ('evaluator.py', EVALUATOR)]:
        write(root, name, code)
    if local_proposal is not None:
        source = local_proposal.read_text(encoding='utf-8-sig')
        write(root, 'local_provider.py', 'import json,sys\nrequest=json.loads(sys.stdin.read().split("\\n",1)[1])\n' +
              'print(json.dumps({"status":"proposed","source":' + repr(source) +
              ',"policy_json":json.dumps(request["policy"]),"reason":"ROOT_AUTHORED_TOOL_FIXTURE: compile sparse quadratics and use a common-denominator integer evaluation; no external model dispatch"}))\n')
    write(root, 'source-provenance.json', {'repository': 'https://github.com/VonEquinox/DiskCoveringSolve',
        'commit': PUBLIC_COMMIT, 'files': PUBLIC_FILES, 'blind_run': False,
        'previous_campaign_restarted': False, 'prior_private_answers_used': False,
        'minimum_polynomial': 'UNKNOWN', 'global_proof_review': 'UNKNOWN'})
    files = [('candidate.py', 'code'), ('benchmark_driver.py', 'code'), ('reference_kernel.py', 'code'),
             ('public_reference.py', 'code'), ('evaluator.py', 'evaluator'), ('data.json', 'data'),
             ('cover13_core_layout.json', 'data'), ('claim.json', 'config'), ('config.json', 'config'), ('source-provenance.json', 'config')]
    if local_proposal is not None:
        files.append(('local_provider.py', 'code'))
    bindings = [{'path': p.relative_to(root).as_posix(), 'role': 'code', 'sha256': file_sha(p)} for p in sorted((root / 'engine').glob('*.py'))]
    bindings += [{'path': p, 'role': role, 'sha256': file_sha(root / p)} for p, role in files]
    protocol = {role + '_sha256': ProjectStore._role_sha({'bindings': bindings}, role) for role in ('code', 'config', 'data')}
    protocol.update(data_split='public-finite-informed', init='fresh', seed=0, checkpoint='same-ledger',
                    schedule='original-tool/model-repair/new-tool/independent-geometric-confirmation',
                    sample_work={'variables': 119, 'polynomials': 119, 'points': 3, 'repetitions': 1}, numeric_protocol='exact QQ; no floating arithmetic')
    write(root, 'protocol.json', protocol)
    bindings.append({'path': 'protocol.json', 'role': 'protocol', 'sha256': file_sha(root / 'protocol.json')})
    python = sys.executable
    provider = {'kind': 'codex_exec', 'argv': [str(codex_path)], 'executable_sha256': file_sha(codex_path),
                'model': 'gpt-6.1-sol', 'effort': 'high', 'service_tier': 'default'} if local_proposal is None else {
                    'kind': 'fixture', 'argv': [python, '-B', 'local_provider.py'], 'executable_sha256': file_sha(Path(python)),
                    'model': None, 'effort': None, 'service_tier': None}
    specs = [('baseline', [python, '-B', 'benchmark_driver.py', 'outputs/baseline.json'],
              ['outputs/baseline.json', 'outputs/baseline.timing.json'], []),
             ('repair', [python, '-B', 'engine/rds_autonomy_worker.py', '--run', 'repair'], output_paths('outputs/repair.json'),
              [{'fact': 'autonomy.repair.ready', 'op': 'eq', 'value': True}]),
             ('candidate', [python, '-B', 'benchmark_driver.py', 'outputs/candidate.json'],
              ['outputs/candidate.json', 'outputs/candidate.timing.json'], [{'fact': 'autonomy.repair.adopted', 'op': 'eq', 'value': True}]),
             ('confirmation', [python, '-B', 'evaluator.py'], ['outputs/confirmation.json'],
              [{'fact': 'run.candidate.succeeded', 'op': 'eq', 'value': True}])]
    routes, nodes = [], []
    for rid, argv, outputs, preconditions in specs:
        seconds = 180 if rid == 'repair' else 45
        manifest = {'schema': 1, 'id': rid, 'arm': 'tool', 'control_id': None, 'argv': argv, 'outpaths': outputs,
            'protocol': {'path': 'protocol.json', 'sha256': file_sha(root / 'protocol.json')},
            'resource_estimates': {'wall_seconds': seconds + 2}, 'timeout_seconds': seconds}
        routes.append({'candidate': rid, 'manifest': manifest})
        nodes.append({'id': rid, 'sources': ['pinned-public-cover13'], 'executable': {
            'decisions': ['n13-tool-improvement'], 'preconditions': preconditions, 'action': {
                'id': rid, 'kind': 'PAIRED_TEST', 'target': 'mathematics', 'operation': 'measure-' + rid,
                'description': 'Execute ' + rid + ' preserving public polynomial arithmetic and independent confirmation',
                'competing_explanations': ['structural tool improvement works', 'new tool fails or does not improve timing'],
                'required_observables': ['run.' + rid + '.succeeded'],
                'outcomes': [{'observation': 'positive', 'next_decision': 'consume bounded result'},
                             {'observation': 'negative', 'next_decision': 'retain failure and change method'}]}}})
    policy = {'schema': 1, 'context': {'decision': {'id': 'n13-tool-improvement', 'goal_revision': 'finite-FJ-v1',
        'scope': {'domain': 'mathematics', 'benchmark': 'n13 finite polynomial tool improvement'},
        'goal_conditions': [{'fact': 'final.verdict', 'op': 'eq', 'value': 'PASS'}]}}, 'graph': {'nodes': nodes, 'edges': []},
        'routes': routes, 'observations': [{'fact': 'final.verdict', 'run_id': 'confirmation', 'path': 'outputs/confirmation.json', 'selector': {'pointer': '/verdict'}}],
        'autonomy': {'schema': 1, 'max_steps': 6, 'repair_slots': [{'run_id': 'repair', 'code_path': 'candidate.py',
            'worker_path': 'engine/rds_autonomy_worker.py', 'response_path': 'outputs/repair.json'}],
            'provider': provider},
        'confirmation': {'schema': 1, 'domain': 'mathematics', 'candidate_runs': ['candidate'], 'confirmation_runs': ['confirmation'],
            'scope': 'Exact finite polynomial residuals and sparse derivatives, not root/coverage/optimality/minpoly',
            'claim': {'path': 'claim.json', 'sha256': file_sha(root / 'claim.json')},
            'evaluator': {'path': 'evaluator.py', 'sha256': file_sha(root / 'evaluator.py')},
            'data': [{'path': 'data.json', 'sha256': file_sha(root / 'data.json')}],
            'rules': {'kind': 'polynomial_rational_evaluation', 'candidate_output': 'outputs/candidate.json', 'confirmation_output': 'outputs/confirmation.json'}}}
    contract = {'schema': 1, 'bindings': bindings, 'allowed_commands': [r['manifest']['argv'] for r in routes],
        'output_roots': ['outputs'], 'budget': {'wall_seconds': 600}, 'advisor_policy': policy,
        'stop_policy': {'schema': 1, 'wall_seconds': 900, 'progress': {'window_seconds': 60, 'min_bytes': 0}},
        'method_evolution': {'schema': 1, 'max_revisions': 2, 'code_paths': ['candidate.py']}}
    write(root, 'contract.json', contract)
    return contract


def entry(root, name, args):
    command = [sys.executable, '-B', str(root / 'engine/rds_cli.py'), '--root', str(root), *args]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=550)
    write(root, 'entry-' + name + '.json', {'argv': command, 'exit_code': completed.returncode, 'stdout': completed.stdout, 'stderr': completed.stderr})
    if completed.returncode:
        raise ValueError('CLI failed; inspect entry-' + name + '.json')
    return json.loads(completed.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--core-dir', required=True)
    parser.add_argument('--codex-path', help='Required for external Codex mode; existing installed native executable')
    parser.add_argument('--local-proposal', help='Explicit local tool fixture source; does not measure an external model')
    parser.add_argument('--execute-approved-request', action='store_true', help='Dispatch the exact prepared public input once through project drive')
    args = parser.parse_args()
    if not args.codex_path and not args.local_proposal:
        parser.error('Choose --codex-path or --local-proposal')
    if args.codex_path and args.local_proposal:
        parser.error('Choose only one provider')
    root, core = Path(args.workspace).resolve(), Path(args.core_dir).resolve()
    codex = Path(args.codex_path).resolve() if args.codex_path else None
    proposal = Path(args.local_proposal).resolve() if args.local_proposal else None
    if not args.execute_approved_request:
        contract = build(root, core, codex, proposal)
        entry(root, 'init', ['project', 'init', '--contract', str(root / 'contract.json')])
        entry(root, 'baseline', ['project', 'drive', '--max-steps', '1'])
        entry(root, 'prepare-model', ['project', 'drive', '--prepare-only', '--max-steps', '6'])
        store = ProjectStore(root)
        with store._db(True) as db:
            event = next(json.loads(row['body']) for row in db.execute('SELECT body FROM events')
                         if json.loads(row['body']).get('kind') == REQUESTED)
        from rds_math import blob
        request = blob(store.root, event['request'])
        write(root, 'model-request-for-review.json', request.decode('utf-8'))
        print(canonical({'workspace': str(root), 'prepared_only': True, 'provider_dispatched': False,
                         'request_sha256': file_sha(root / 'model-request-for-review.json'),
                         'data_sha256': file_sha(root / 'data.json'), 'scientific_support': 'UNKNOWN'}))
    else:
        before = ProjectStore(root).snapshot()
        with ProjectStore(root)._db(True) as db:
            t0 = next(json.loads(row['body'])['started_at'] for row in db.execute('SELECT body FROM events')
                      if json.loads(row['body']).get('kind') == 'CAMPAIGN_STARTED')
        first = entry(root, 'approved-drive', ['project', 'drive', '--max-steps', '6'])
        recovered = entry(root, 'recovery', ['project', 'drive', '--max-steps', '6'])
        after = ProjectStore(root).snapshot()
        for receipt in before['receipts']:
            if receipt not in after['receipts']:
                raise ValueError('Original receipt changed')
        summary = {'first_status': first['status'], 'recovery_status': recovered['status'],
                   'receipt_sha256': [r['sha256'] for r in after['receipts']],
                   'original_T0': t0, 'budget': after['budget'],
                   'scientific_support': 'UNKNOWN', 'minimum_polynomial': 'UNKNOWN',
                   'scope': 'Finite public exact F/J evaluation, informed starting materials'}
        summary['provider'] = after['contract']['advisor_policy']['autonomy']['provider']['kind']
        summary['external_model_ability_measured'] = False
        summary['worker_wall_seconds'] = {r['run_id']: r['resources']['wall_seconds'] for r in after['receipts']}
        for rid in ('baseline', 'candidate'):
            if (root / ('outputs/' + rid + '.timing.json')).exists():
                summary[rid + '_timing'] = json.loads((root / ('outputs/' + rid + '.timing.json')).read_text(encoding='utf-8-sig'))
        write(root, 'trajectory-summary.json', summary)
        print(canonical(summary))


if __name__ == '__main__':
    main()

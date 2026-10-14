"""Isolated real agent loop; transcripts are audit outputs, never RDS authority."""
import json
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

from prototypes.memento_rds.observations import read_observations
from prototypes.rds_agent_eval.loop import response_turn, save, sha


def tool(root, arm, manifest):
    audit = root / 'audit'
    audit.mkdir(exist_ok=True)
    raw_path, done = audit / (manifest['id'] + '-raw.json'), audit / (manifest['id'] + '-tool.json')
    if done.exists():
        return {**json.loads(done.read_text(encoding='utf-8')), 'reused_original': True}
    if raw_path.exists():
        raise ValueError('Prior attempt lacks verified feedback; no rerun')
    if arm == 'RDS':
        from rds_cli import cmd_project
        from rds_project import ProjectStore
        store = ProjectStore(root)
        advice = cmd_project(SimpleNamespace(root=str(root), action='next'))
        if advice.get('selected_manifest') != manifest:
            return {'status': 'NATIVE_NOT_SELECTED', 'selected_run': advice.get('selected_run')}
        result = cmd_project(SimpleNamespace(root=str(root), action='advance', background=False))
        save(raw_path, result)  # Native execution/timeout/costs are already ledger-settled.
        receipt = result['receipt']
        originals = read_observations(store, [{'run_id': receipt['run_id'], 'receipt_sha256': receipt['sha256']}])
        measured = [a for a in originals['observations'][0]['artifacts'] if a['kind'] == 'project_output']
        result = {'status': receipt['run_status'], 'receipt': receipt,
                  'measurement': json.loads(measured[0]['bytes']) if receipt['run_status'] == 'SUCCEEDED' else None}
    else:
        # Bare is the ordinary-tool control: raw audit files have no kernel authority.
        began = time.monotonic()
        try:
            process = subprocess.run(manifest['argv'], cwd=root, capture_output=True,
                                     timeout=manifest['timeout_seconds'])
            stdout, stderr = process.stdout, process.stderr
            result = {'status': 'SUCCEEDED' if process.returncode == 0 else 'FAILED', 'exit_code': process.returncode}
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = exc.stdout or b'', exc.stderr or b''
            result = {'status': 'TIMEOUT_UNKNOWN', 'exit_code': None, 'phase': 'BARE_TOOL', 'attempt': manifest['id']}
        for name, raw in (('stdout', stdout), ('stderr', stderr)):
            (audit / (manifest['id'] + '-' + name + '.bin')).write_bytes(raw)
        result.update(wall_seconds=time.monotonic() - began, cost_status='UNKNOWN', argv=manifest['argv'])
        save(raw_path, result)  # Preserve partial bytes/phase/cost before output decoding.
        output = root / manifest['outpaths'][0]
        result['measurement'] = json.loads(output.read_bytes()) if result['status'] == 'SUCCEEDED' else None
        result['measurement_sha256'] = sha(output) if result['status'] == 'SUCCEEDED' else None
    save(done, result)
    return result


def run_arm(root, seed, arm, declaration, recipe, endpoint):
    """One fresh arm, at most 20 requests; the host must inspect/freeze all sources."""
    root, seed = Path(root).resolve(), Path(seed).resolve()
    if arm not in {'Bare', 'RDS'} or declaration['per_arm_calls'] != 20 or root.exists():
        raise ValueError('Require a fresh declared arm and unchanged 20-call cap')
    if declaration['completion_protocol'] != 'finish-function-or-stop-text-v2':
        raise ValueError('Uninspected completion protocol')
    shutil.copytree(seed, root)
    audit = root / 'audit'
    audit.mkdir()
    routes = {r['run']['argv'][-2]: r['run'] for r in recipe['routes']}
    if arm == 'RDS':
        from rds_project_assembly import initialize
        initialize(__import__('rds_project').ProjectStore(root), root / 'recipe.json')
        # Native initialization adds schema/protocol/control identities to manifests.
        with __import__('rds_project').ProjectStore(root)._db(readonly=True) as db:
            contract = __import__('rds_project').ProjectStore._contract(db)
        routes = {r['manifest']['argv'][-2]: r['manifest'] for r in contract['advisor_policy']['routes']}
    task = (root / declaration['task_file']).read_text(encoding='utf-8')
    schema = [{'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': {field: {'type': 'string'}},
                       'required': [field], 'additionalProperties': False}}}
        for name, field, description in (('read', 'file', 'Read one declared task/data input'),
            ('execute', 'method', 'Execute one declared finite method'), ('finish', 'answer', 'Finish with evidence and limitations'))]
    instruction = ('Solve the task using measured evidence. Use function tools serially; ordinary stop-text also finishes. '
        'Methods: ' + json.dumps(list(routes)) + '. Inputs: ' + json.dumps(declaration['agent_files']) + '. Task: ' + task +
        ' Finish with a JSON object containing answer, earliest_observed_main_period, main_periods, '
        'exact_crossover_year (null when unresolved), scope=RECORDED_PERIOD_COUNTS. Do not infer exact years or causal claims from bins.')
    if arm == 'RDS':
        from rds_cli import cmd_project
        advice = cmd_project(SimpleNamespace(root=str(root), action='next'))
        save(audit / 'initial-advice.json', advice)
        instruction += ' Native RDS Advisor selected_run=' + str(advice.get('selected_run')) + '; execute only its selected method. Operational success is not science.'
    messages = [{'role': 'system', 'content': instruction}]
    end = min(declaration['deadline_epoch'], time.time() + recipe['budget']['wall_seconds'])
    report = {'arm': arm, 'calls': 0, 'status': 'IN_PROGRESS', 'final_answer': None,
              'arm_deadline_epoch': end, 'scientific_support': 'UNKNOWN'}
    for turn in range(20):
        if time.time() >= end:
            report['status'] = 'DEADLINE'
            break
        request = {'model': declaration['model'], 'messages': messages, 'tools': schema, 'max_tokens': 3000}
        save(audit / f'request-{turn}.json', request)
        report.update(calls=turn + 1, status='REQUESTING')
        save(audit / 'summary.json', report)
        try:
            req = urllib.request.Request(endpoint.rstrip('/') + '/chat/completions', data=json.dumps(request).encode(),
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer magpie'})
            with urllib.request.urlopen(req, timeout=min(120, end - time.time())) as response:
                raw = response.read()
            (audit / f'response-{turn}.json').write_bytes(raw)
            response = json.loads(raw)
            if response['model'] != declaration['response_model']:
                raise ValueError('Resolved model identity changed')
            actions, final = response_turn(response['choices'][0])
            messages.append(response['choices'][0]['message'])
            report['status'] = 'CALL_CAP'
            if final is not None:
                report.update(status='AGENT_FINISHED', final_answer=final, completion_mode='TEXT')
                break
            for ident, command in actions:
                if command['tool'] == 'finish':
                    report.update(status='AGENT_FINISHED', final_answer=command['answer'], completion_mode='FUNCTION')
                    break
                if command['tool'] == 'read' and command['file'] in declaration['agent_files']:
                    feedback = (root / command['file']).read_text(encoding='utf-8-sig')[:12000]
                elif command['tool'] == 'execute' and command['method'] in routes:
                    if end - time.time() < 50:
                        report['status'] = 'INSUFFICIENT_REMAINING_TOOL_BUDGET'
                        break
                    feedback = tool(root, arm, routes[command['method']])
                    if feedback['status'] in {'TIMEOUT_UNKNOWN', 'FAILED'}:
                        report['status'] = 'TOOL_FAILURE_NO_RETRY'
                        break
                else:
                    raise ValueError('Undeclared input or method')
                messages.append({'role': 'tool', 'tool_call_id': ident, 'content': json.dumps(feedback, ensure_ascii=False)})
            if report['status'] != 'CALL_CAP':
                break
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError):
                (audit / f'response-{turn}.json').write_bytes(exc.read())
            save(audit / f'failure-{turn}.json', {'type': type(exc).__name__, 'error': str(exc),
                'phase': report['status'], 'paid_cost': 'UNKNOWN', 'call': report['calls']})
            report['status'] = 'UNKNOWN_NO_RETRY'
            break
    save(audit / 'summary.json', report)
    return report

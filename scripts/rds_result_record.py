"""Publish original full results and read their bounded operational summary."""
from collections import Counter
from pathlib import Path
import sqlite3

from rds_cas import cas_json
from rds_project import ProjectStore
from rds_output_values import _brief_move


def brief(root, value, version, formal=False):
    """Persist full output and expose a bounded, truthful operational digest."""
    ref = cas_json(root, value)
    summary = {'status': value.get('status', value.get('run_status', 'RECORDED')), 'sha256': ref['sha256'], 'record': ref['path']}
    for key in ('id', 'name', 'source_sha256', 'module', 'assurance'):
        if key in value:
            summary[key] = value[key]
    # Wrapped results from project advance carry the Advisor owner under 'advisor';
    # direct advise results are the owner. Select recommendations from that owner.
    owner = value.get('advisor') or value
    if 'dependency_map' in value and 'input_review' in value:
        summary['authorization'] = 'UNCHANGED'
        if 'snapshot_sha256' in value:
            summary['snapshot_sha256'] = value['snapshot_sha256']
        goals = value.get('goals', {})
        summary['goals'] = {name: row['status'] for name, row in list(goals.items())[:3]}
        summary['omitted_goals'] = max(0, len(goals) - 3)
        review = value['input_review']
        summary['input_repairs'] = len(review['repairs']) + len(review.get('format_repairs', []))
        summary['input_warnings'] = len(review['warnings'])
        summary['input_issues'] = len(review['errors'])
        step = value.get('next_step', {})
        # Full field diagnostics and tables remain at the existing CAS locator.
        summary['next_step'] = {k: v for k, v in step.items() if k != 'fields'}
        if step.get('fields'):
            summary['next_step']['fields'] = step['fields'][:3]
            summary['next_step']['omitted_fields'] = max(0, len(step['fields']) - 3)
        if 'revision' in value:
            summary['lost_support_count'] = len(value['revision']['lost_support'])
            summary['gained_support_count'] = len(value['revision']['gained_support'])
            summary['lost_support'] = value['revision']['lost_support'][:3]
            summary['gained_support'] = value['revision']['gained_support'][:3]
        if 'support_cone' in value:
            cone = value['support_cone']
            summary['support_cone'] = {key: cone[key] for key in ('node_id', 'supported', 'derivation_rule')}
            for key in ('support_cone_nodes', 'support_cone_rules'):
                summary['support_cone'][key] = cone[key][:3]
                summary['support_cone']['omitted_' + key] = max(0, len(cone[key]) - 3)
    if value.get('status') == 'LOCAL_CATALOG' and isinstance(value.get('tools'), list):
        registered = {row['data']['name'] for row in value['tools'] if row['kind'] == 'tool-adoption'}
        candidates = sorted((row['data'] for row in value['tools'] if row['kind'] == 'tool'),
                            key=lambda item: item['name'])
        summary['tool_count'] = len(candidates)
        summary['omitted_tools'] = max(0, len(candidates) - 3)
        summary['reuse_checked'] = False
        summary['tools'] = []
        for item in candidates[:3]:
            entry = item['entry']
            row = {'name': item['name'], 'entry': entry if len(entry) <= 128 else None,
                   'recorded_registration': item['name'] in registered}
            if len(entry) > 128:
                row['entry_omitted'] = True
            summary['tools'].append(row)
    if 'affected' in value:
        summary['affected_count'] = len(value['affected'])
    if 'recommendations' in owner:
        recommendations = owner['recommendations']
        summary['gaps'] = sum(len(r.get('frontier', {}).get('gaps', [])) for r in recommendations)
        summary['candidates'] = [c.get('id') for r in recommendations for c in r.get('search', {}).get('candidates', [])][:3]
        selection = next((r['search']['selection_review'] for r in recommendations
                          if 'selection_review' in r.get('search', {})), None)
        flags = [f.get('kind') for r in recommendations for f in r.get('review', {}).get('flags', [])]
        if selection is not None:
            summary['selection_basis'] = selection['basis']
            flags += [f['kind'] for f in selection['flags']]
            if 'next_move' in selection:
                _brief_move(summary, selection['next_move'])
            if 'goal' in selection:
                summary['goal_input_status'] = selection['goal']['status']
            if 'planning' in selection:
                owner = value.get('advisor') or value
                planning = (owner.get('planning', selection['planning'])
                            if owner.get('assurance') == 'PROGRAM_OWNED_EVIDENCE_NOT_SCIENTIFIC_PROOF'
                            else selection['planning'])
                checks = [{'candidate': row['candidate'][:64],
                           **({'candidate_truncated': True} if len(row['candidate']) > 64 else {}),
                           'target': row['target'][:128],
                           **({'target_truncated': True} if len(row['target']) > 128 else {}),
                           **({'goal_target': row['goal_target'][:128],
                               **({'goal_target_truncated': True} if len(row['goal_target']) > 128 else {})}
                              if 'goal_target' in row else {})}
                          for row in planning.get('local_checks', [])[:8]]
                open_predicates = [name[:128] for name in planning.get('open_predicates', [])[:8]]
                summary['planning'] = {'scope': planning.get('scope'),
                                       'local_checks': checks,
                                       'local_checks_omitted': max(0, len(planning.get('local_checks', [])) - 8),
                                       'open_predicates': open_predicates,
                                       'open_predicates_omitted': max(0, len(planning.get('open_predicates', [])) - 8),
                                       'ready_obligations': list(planning.get('ready_obligations', [])),
                                       'omitted_ready_obligations': planning.get('omitted_ready_obligations', 0)}
                projected = summary['planning']
                truncated = sum(len(name) > 128 for name in planning.get('open_predicates', [])[:8])
                if truncated:
                    projected['open_predicates_truncated'] = truncated
                for key, omitted in (('local_checks', 'local_checks_omitted'),
                                     ('open_predicates', 'open_predicates_omitted'),
                                     ('ready_obligations', 'omitted_ready_obligations')):
                    if not projected[key]:
                        projected.pop(key)
                    if not projected[omitted] and key != 'open_predicates':
                        projected.pop(omitted)
                if not open_predicates and not projected['open_predicates_omitted']:
                    projected.pop('open_predicates_omitted')
        advisory_moves = {'GOAL_CONTRIBUTION_UNDECLARED': 'REVIEW_GOAL_LINK',
                          'GOAL_CONTRIBUTION_INVALID': 'REVIEW_GOAL_LINK'}
        relevant = [kind for kind in flags if kind in advisory_moves and advisory_moves[kind] == summary.get('next_move')]
        flags = relevant + [kind for kind in flags if kind not in advisory_moves]
        summary['flags'] = list(dict.fromkeys(flags))[:3]
    owned = value.get('advisor') or value
    graph_reviews = [r['search'].get('analysis_coverage') for r in owned.get('recommendations', [])
                     if 'search' in r]
    analysis = owned.get('analysis_coverage') or next((r for r in graph_reviews if r), None)
    if analysis:
        # Per-graph identities and diagnostics remain in the hashed full record.
        # Keep coverage visible without repeating hashes alongside decision detail.
        summary['analysis_coverage'] = {key: analysis[key] for key in ('status', 'full')}
        summary['analysis_coverage']['graph_count'] = len(analysis['graphs'])
        for key in ('node_count', 'edge_count'):
            counts = [graph.get(key) for graph in analysis['graphs']]
            summary['analysis_coverage'][key] = sum(counts) if all(type(n) is int for n in counts) else None
        if analysis['reasons']:
            summary['analysis_coverage']['reasons'] = analysis['reasons'][:3]
            summary['analysis_coverage']['omitted_reasons'] = max(0, len(analysis['reasons']) - 3)
    if 'steering' in owned:
        state = owned['steering']
        summary['steering'] = {key: state.get(key) for key in ('revision', 'paused', 'kind', 'instruction_id')}
        for key in ('withdrawn_runs', 'preferred_runs'):
            summary['steering'][key] = state.get(key, [])[:3]
            summary['steering']['omitted_' + key] = max(0, len(state.get(key, [])) - 3)
    if 'working_set' in owned:
        summary['working_set'] = owned['working_set']
    if 'shadow_plan' in owned:
        plan = owned['shadow_plan']
        summary['shadow_plan'] = {'status': plan['status'], 'summary': plan.get('summary'),
                                  'comparison': plan.get('comparison'), 'selection_applied': False,
                                  'expand': 'project plan --shadow'}
    if 'advisor' in value and 'receipt' in value:
        summary.update(status=value['receipt'].get('run_status', 'UNKNOWN'),
                       run_id=value['receipt'].get('run_id'), receipt_sha256=value['receipt'].get('sha256'),
                       advisor_status=owned.get('status'), scientific_support='UNKNOWN')
        if owned.get('reason'):
            summary['advisor_reason'] = str(owned['reason'])[:512]
    if owned.get('assurance') == 'PROGRAM_OWNED_EVIDENCE_NOT_SCIENTIFIC_PROOF':
        summary.update(selected_run=owned.get('selected_run'), snapshot_sha256=owned.get('snapshot_sha256'),
                       authorization='UNCHANGED', scientific_support='UNKNOWN', assurance=owned['assurance'])
        if owned.get('graph_ranker'):
            ranker = owned['graph_ranker']
            summary['graph_ranker'] = {key: ranker.get(key) for key in (
                'status', 'mode', 'selection_applied', 'precedence', 'reason')}
            summary['graph_ranker']['preferred'] = ranker.get('preferred', [])[:4]
            summary['graph_ranker']['scope_count'] = len(ranker.get('scope', []))
        if owned.get('next_move'):
            move = owned['next_move']
            _brief_move(summary, move)
        if owned.get('feasibility'):
            forecast = owned['feasibility']
            summary['feasibility'] = {'next_action':forecast['next_action'],
                'plans':[{'id':p['id'],'status':p['status']} for p in forecast['plans'][:4]],
                'pilot_budget':forecast['pilot_budget']}
            repair = forecast.get('repair_request')
            if repair:
                summary['tool_workbench_command'] = repair['tool_workbench_command']
        coverage = owned.get('coverage', {})
        summary['coverage'] = {key: coverage.get(key, 0) for key in ('runs', 'receipts', 'artifacts', 'parsed_observations')}
        summary['coverage']['unparsed_outputs'] = len(coverage.get('unparsed_outputs', []))
        summary['coverage']['errors'] = len(coverage.get('errors', []))
        summary['coverage']['gaps'] = len(coverage.get('gaps', []))
        summary['coverage']['declared_outputs'] = len(coverage.get('declared_outputs', []))
        summary['coverage']['missing_outputs'] = sum(row.get('status') == 'MISSING'
                                                     for row in coverage.get('declared_outputs', []))
        summary['coverage_errors'] = coverage.get('errors', [])[:3]
        summary['coverage_gaps'] = coverage.get('gaps', [])[:3]
        if 'tool_utilization' in owned:
            use = owned['tool_utilization']
            summary['tool_utilization'] = {key: use[key] for key in
                ('scope', 'counts', 'applicable_use_rate', 'applicable_consumption_rate')}
        warnings = [row['kind'] for row in owned.get('warnings', []) if 'kind' in row]
        summary['flags'] = list(dict.fromkeys(warnings + summary.get('flags', [])))[:5]
        summary['omitted_flags'] = max(0, len(set(warnings)) - len(summary['flags']))
    if 'job_root' in value:
        summary['job_root'] = value['job_root']
        summary['run_status'] = (value.get('receipt') or {}).get('run_status', 'UNKNOWN')
    if 'regression_review' in value:
        summary['regression_status'] = value['regression_review'].get('status', 'UNKNOWN')
        summary['promotion_eligible'] = value['regression_review'].get('promotion_eligible') is True
    if 'checkpoint' in value:
        summary['checkpoint'] = value['checkpoint'].get('id')
        summary['route'] = value['checkpoint'].get('candidate_id', value.get('route'))
    if isinstance(value.get('runs'), list):
        summary['runs'] = len(value['runs'])
        summary['receipts'] = len(value.get('receipts', []))
        summary['run_states'] = dict(Counter(run.get('status', run.get('run_status', 'UNKNOWN')) for run in value['runs']))
        latest = max((receipt for receipt in value.get('receipts', [])
                      if isinstance(receipt, dict) and type(receipt.get('ended_at')) in (int, float)
                      and isinstance(receipt.get('cwd'), str) and Path(receipt['cwd']).is_absolute()),
                     key=lambda receipt: receipt['ended_at'], default=None)
        if latest is not None:
            summary['latest_receipt'] = {key: latest.get(key) for key in ('run_id', 'run_status', 'exit_code')}
            if latest.get('errors'):
                error = latest['errors'][0]
                summary['latest_receipt']['error_count'] = len(latest['errors'])
                summary['latest_receipt']['errors'] = [error[:197] + '...' if len(error) > 200 else error]
            stderr = next((artifact for artifact in latest.get('artifacts', [])
                           if artifact.get('kind') == 'stderr.bin' and artifact.get('size', 0) > 0), None)
            if stderr is not None:
                summary['latest_receipt']['stderr_path'] = str((Path(latest['cwd']) / stderr['path']).resolve())
    ledger_root = Path(value.get('ledger_root', root)).resolve()
    ledger = ledger_root / '.rds' / 'project.sqlite3'
    if ledger.is_file() and isinstance(value.get('runs'), list) and isinstance(value.get('contract'), dict):
        try:
            summary['next_move'] = ({'next_move': 'inspect current program-owned evidence and selection',
                                     'command': 'python -B scripts/rds_cli.py project next'}
                                    if 'advisor_policy' in value['contract'] else ProjectStore(ledger_root).next_move())
        except (ValueError, OSError, sqlite3.Error):
            summary['next_move'] = {'next_move': 'inspect recorded project state', 'command': 'python -B scripts/rds_cli.py project status'}
    assurance = value.get('assurance') if formal else None
    formal_status = value.get('status', 'UNKNOWN') if formal and assurance in {'CERTIFICATE_CHECKED', 'LEAN_KERNEL_CHECKED'} else 'UNKNOWN'
    if formal:
        summary['formal_status'] = formal_status
        summary['formal_assurance'] = assurance or 'NONE'
        summary['application_status'] = value.get('application_status', 'UNKNOWN')
    if ledger.is_file():
        store = ProjectStore(ledger_root)
        with store.readonly() as db:
            summary['ledger_checkpoints'] = 0
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone():
                summary['ledger_checkpoints'] = db.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0]
    return summary

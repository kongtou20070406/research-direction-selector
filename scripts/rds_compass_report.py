"""Compass report-time receipt checks and TMS projection."""
import rds_compass_model as model
import rds_compass_store as cstore
from rds_project import canonical


def _tolerances(plan):
    return {name: model.tolerance(spec) for name, spec in plan['tolerances'].items() if spec is not None}


def _replicates(plan):
    """Declared runs per original arm: the arm first, then its replicates by (cost, id)."""
    runs = {c['id']: [c['id']] for c in plan['candidates'] if 'replicates' not in c}
    for c in sorted(plan['candidates'], key=lambda c: (model.exact(c['cost']), c['id'])):
        if 'replicates' in c:
            runs[c['replicates']].append(c['id'])
    return runs


def _run_verdict(state, ident, base, stale):
    plan, profile = state['plan'], state['plan']['profile']
    obs = state['observations'][ident]
    if obs['status'] != 'MEASURED' or set(obs['samples']) != set(base['samples']):
        return {'run': ident, 'verdict': 'UNMEASURED_OR_SAMPLE_MISMATCH'}
    if {ident, profile['baseline']} & stale:
        # Recorded samples whose receipt bytes no longer verify are never evidence.
        return {'run': ident, 'verdict': 'STALE_EVIDENCE', 'receipt_sha256': obs['receipt_sha256']}
    higher = plan['higher_is_better'].get(profile['metric'])
    if higher is None or plan['tolerances'].get(profile['metric']) is None:
        # No declared direction or tolerance: neither the sign nor the size of a per-sample delta
        # is settled, and an untoleranced observable is never adjudicated.
        return {'run': ident, 'verdict': 'JUDGMENT_REQUIRED', 'receipt_sha256': obs['receipt_sha256']}
    prof = model.sample_profile(base['samples'], obs['samples'], higher_is_better=higher,
                                tolerance=_tolerances(plan)[profile['metric']])
    if 'max_degradation' not in profile or 'min_gain' not in profile:
        verdict = 'JUDGMENT_REQUIRED'
    elif model.exact(prof['mean_gain']) < model.exact(profile['min_gain']):
        verdict = 'NO_MEAN_GAIN'
    else:
        verdict = model.profile_verdict(prof, profile['max_degradation'])
    return {'run': ident, 'verdict': verdict, 'profile': prof, 'receipt_sha256': obs['receipt_sha256']}


# A failed run decides its arm; the order says which failure is reported first.
UNSETTLED = ('STALE_EVIDENCE', 'UNMEASURED_OR_SAMPLE_MISMATCH', 'JUDGMENT_REQUIRED')


def _improve_verdicts(state, stale=()):
    plan = state['plan']
    profile = plan['profile']
    base = state['observations'].get(profile['baseline'])
    if base is None or base['status'] != 'MEASURED' or not base['samples']:
        return {'baseline': profile['baseline'], 'status': 'BASELINE_UNMEASURED', 'arms': []}
    stale = {row['candidate'] for row in stale}
    arms = []
    for ident, runs in sorted(_replicates(plan).items()):
        if ident == profile['baseline']:
            continue
        if not any(r in state['observations'] for r in runs):
            # A declared arm the budget never reached was not evaluated: it stays unsettled.
            arms.append({'arm': ident, 'verdict': 'UNMEASURED_OR_SAMPLE_MISMATCH'})
            continue
        results = [_run_verdict(state, r, base, stale) for r in runs if r in state['observations']]
        failed = next((r for kind in UNSETTLED for r in results if r['verdict'] == kind), None) or \
            next((r for r in results if r['verdict'] != 'ACCEPT'), None)
        if failed is not None:
            verdict = failed['verdict']
        elif len(results) < len(runs):
            verdict = 'UNREPLICATED'  # A declared re-run was never bought: one run is not the arm.
        else:
            verdict = 'ACCEPT'
        first = results[0]
        entry = {'arm': ident, 'verdict': verdict, **{k: first[k] for k in ('profile', 'receipt_sha256') if k in first}}
        if 'profile' in first:
            entry['baseline_receipt_sha256'] = base['receipt_sha256']
        if len(runs) > 1:
            entry['runs'] = results
            entry['declared_runs'] = runs
            gains = [model.exact(r['profile']['mean_gain']) for r in results if 'profile' in r]
            entry['min_mean_gain'] = model.text(min(gains)) if gains else None
        arms.append(entry)

    def gain(arm):
        return model.exact(arm.get('min_mean_gain') or arm['profile']['mean_gain'])
    accepted = sorted((a for a in arms if a['verdict'] == 'ACCEPT'), key=lambda a: (-gain(a), a['arm']))
    return {'baseline': profile['baseline'], 'status': 'EVALUATED', 'arms': arms,
            'recommended': accepted[0]['arm'] if accepted else None,
            'rule': 'ACCEPT iff, on every declared run of the arm, mean gain > 0 and >= min_gain and '
                    'worst per-sample degradation <= max_degradation; a sample gain counts only beyond '
                    'the metric tolerance, a degradation in full'}


def _rechecked(state, now):
    """The latest verification, with its receipt bindings re-checked at report time (never trusted as stored).

    Evidence that no longer verifies cannot carry a stored conclusion. A law claim falls to
    NOT_ESTABLISHED; IMPROVE verdicts are recomputed on the current stale set and no arm is
    recommended until a new verification. The stored conclusion stays under ``*_at_verification``.
    """
    if not state['verifications']:
        return None
    stored = state['verifications'][-1]
    value = {**stored, 'receipts_rechecked': stored['receipts_rechecked'] and not now,
             'stale': stored['stale'] + [row for row in now if row not in stored['stale']],
             'report_recheck': {'status': 'STALE' if now else 'BOUND', 'stale': now}}
    if now and stored['goal'] == 'IMPROVE':
        value['improvement'] = {**_improve_verdicts(state, value['stale']), 'recommended': None,
                                'recommended_at_verification': stored['improvement'].get('recommended')}
    elif now:
        value.update(claim='NOT_ESTABLISHED', claim_at_verification=stored['claim'])
    return value


def stale_bindings(store, state):
    """Every measurement's receipt, re-read now: the declared output and every other bound artifact."""
    receipts, stale = cstore.receipts(store), []
    for ident, obs in sorted(state['observations'].items()):
        if obs['status'] != 'MEASURED':
            continue
        receipt = receipts.get(obs['run_id'])
        if receipt is None:
            # A retained observation whose receipt row is gone binds to nothing: never evidence.
            stale.append({'candidate': ident, 'binding': 'RECEIPT_MISSING'})
            continue
        _, binding = cstore.artifact(store, receipt, obs['artifact']['path'])
        if binding != 'BOUND' or receipt['sha256'] != obs['receipt_sha256']:
            stale.append({'candidate': ident, 'binding': binding})
        stale.extend({'candidate': ident, 'binding': row['binding'], 'artifact': row['kind']}
                     for row in cstore.recheck(store, receipt) if row['path'] != obs['artifact']['path'])
    return stale


def publish_tms(store, state, stale=None):
    """Mirror Compass records into the program-owned TMS map (owned:compass:*).

    Receipts are re-checked first (or ``stale`` is a fresh ``stale_bindings`` result): a
    measurement that no longer verifies is published UNKNOWN, and so is a refutation or a tool
    retraction whose every recorded witness is such a measurement.
    """
    from rds_tms_store import SnapshotConflict, current, save
    root = str(store.root)
    stale = {row['candidate'] for row in (stale_bindings(store, state) if stale is None else stale)}
    nodes, edges = [], []
    goal = 'owned:compass:goal'
    nodes.append({'id': goal, 'status': 'UNKNOWN', 'source': {'locator': 'compass goal: ' + state['plan']['goal']['statement'][:200]}})
    for ident, obs in sorted(state['observations'].items()):
        if obs['status'] == 'MEASURED' and ident in stale:
            nodes.append({'id': 'owned:compass:obs:' + ident, 'status': 'UNKNOWN',
                          'source': {'locator': 'STALE_EVIDENCE: receipt binding failed recheck'}})
        elif obs['status'] == 'MEASURED':
            nodes.append({'id': 'owned:compass:obs:' + ident, 'status': 'SUPPORTED',
                          'source': {'locator': 'INPUT_REPORTED measurement ' + canonical(obs['outputs'])[:200]},
                          'evidence': {'receipt': {'project_root': root, 'sha256': obs['receipt_sha256']}}})
    unbound = lambda sha: ((state['tools'][sha].get('retraction') or {}).get('witness') or {}).get('id') in stale
    for sha, tool in sorted(state['tools'].items()):
        retracted = tool['status'] == 'RETRACTED' and not unbound(sha)
        node = {'id': 'owned:compass:tool:' + sha[:16], 'status': 'CONTRADICTED' if retracted else 'UNKNOWN',
                'source': {'locator': f"compass tool {tool['name']} ({tool['status']}"
                                      f"{'; witness stale' if tool['status'] == 'RETRACTED' and not retracted else ''}; "
                                      f"oracle {tool['oracle']['kind']})"}}
        witness = (tool.get('retraction') or {}).get('witness') or {}
        if retracted and witness.get('receipt_sha256'):
            node['evidence'] = {'receipt': {'project_root': root, 'sha256': witness['receipt_sha256']}}
        nodes.append(node)
    for ident, hyp in sorted(state['hypotheses'].items()):
        node_id = 'owned:compass:hyp:' + ident
        # Replay keeps every counterexample, so a refutation stands while any of them still verifies.
        cx = [c for c in (hyp['replay'] or {}).get('counterexamples') or [] if c['id'] not in stale]
        refuted = hyp['status'] == 'REFUTED' and bool(cx)
        tools = [p[5:] for p in hyp['premises'] if p.startswith('tool:')]
        # Retracted with a premise tool that is unqualified on evidence that still verifies.
        withdrawn = hyp['status'] == 'RETRACTED' and any(
            sha not in state['tools'] or state['tools'][sha]['status'] != 'QUALIFIED' and not unbound(sha) for sha in tools)
        label = hyp['status'] if refuted or hyp['status'] != 'REFUTED' else 'REFUTED_BY_STALE_EVIDENCE_ONLY'
        node = {'id': node_id, 'status': 'CONTRADICTED' if refuted else 'UNKNOWN',
                'source': {'locator': f"compass {label}: {hyp['statement'][:180]}"}}
        if refuted and cx[0].get('receipt_sha256'):
            node['evidence'] = {'receipt': {'project_root': root, 'sha256': cx[0]['receipt_sha256']}}
        nodes.append(node)
        premises = ['owned:compass:tool:' + p[5:21] for p in hyp['premises'] if p.startswith('tool:')]
        if premises:
            edges.append({'id': 'owned:compass:uses:' + ident, 'premises': premises, 'conclusion': node_id,
                          'status': 'CONTRADICTED' if withdrawn else 'PROPOSED',
                          'source': {'locator': 'hypothesis depends on qualified tool features'}})
        edges.append({'id': 'owned:compass:explains:' + ident, 'premises': [node_id], 'conclusion': goal,
                      'status': 'CONTRADICTED' if refuted or withdrawn else 'PROPOSED',
                      'source': {'locator': 'rival explanation for the Compass goal'}})
    for _ in range(3):
        saved = current(store.root)
        old = saved['dependency_map'] if saved else {'schema': 1, 'nodes': [], 'hyperedges': [], 'goals': []}
        keep = lambda row: not row['id'].startswith('owned:compass:')
        spec = {'schema': 1, 'nodes': [r for r in old['nodes'] if keep(r)] + nodes,
                'hyperedges': [r for r in old['hyperedges'] if keep(r)] + edges,
                'goals': [g for g in old.get('goals', []) if not g.startswith('owned:compass:')] + [goal],
                'limits': {'max_nodes': 4096, 'max_hyperedges': 16384}}
        if saved and canonical(saved['dependency_map']) == canonical(spec):
            return saved['sha256']
        try:
            return save(store.root, spec, expected=saved['sha256'] if saved else None,
                        revision={'source': 'compass', 'head': state['head']})
        except SnapshotConflict:
            continue
    raise RuntimeError('TMS publication kept conflicting; retry after concurrent writers settle')

"""Compass Advisor: receipt-grounded rival discrimination and basin escape.

State is folded on every read from ``COMPASS_*`` ledger records plus original
run receipts. Hypothesis status is recomputed by exact replay over measurements
whose output bytes still match their receipts; a stored status is never trusted.

Status vocabulary is deliberately split. A measurement is ``INPUT_REPORTED``
(the task's own evaluator printed it, RDS bound its bytes). Replay yields
``CONSISTENT`` / ``REFUTED`` / ``UNTESTED`` relative to those measurements and a
declared tolerance. ``scientific_support`` stays ``UNKNOWN``: Compass never
certifies a law; uniqueness is reported only within a stated search bound.
"""
from copy import deepcopy
import hashlib
import json
import subprocess

import rds_compass_model as model
import rds_compass_store as cstore
import rds_fit_trend as fit
from rds_artifacts import strict_json
from rds_project import canonical, digest, require

WORKSET_SCHEMA = 'rds-compass-workset-v1'
PACKET_SCHEMA = 'rds-jump-packet-v1'
REPORT_SCHEMA = 'rds-compass-report-v1'
PACKET_BYTES = 32 * 1024
AGENT_BYTES = 256 * 1024
DISPOSITIONS = ('adopt', 'adapt', 'reject', 'defer')
LIVE = ('CONSISTENT', 'UNTESTED')
HANDOFF_REASONS = ('GOAL_IDENTIFIED', 'IMPROVEMENT_EVALUATED', 'BUDGET_EXHAUSTED', 'SEARCH_EXHAUSTED',
                   'JUDGMENT_REQUIRED', 'STEP_LIMIT', 'CONTROLLER_DEADLINE', 'INTERRUPTED',
                   'WAITING_FOR_ORIGINAL_ATTEMPT')


# --- State fold --------------------------------------------------------------

def _tolerances(plan):
    return {name: model.tolerance(spec) for name, spec in plan['tolerances'].items() if spec is not None}


def _budget(db):
    rows = db.execute('SELECT resource, cap, spent, charged, reserved FROM budget').fetchall()
    return {row['resource']: {'cap': row['cap'], 'used': row['spent'] + row['charged'], 'reserved': row['reserved'],
                              'remaining': row['cap'] - row['spent'] - row['charged'] - row['reserved']}
            for row in rows}


def _anchor(event):
    """Where a rulebook entered the ledger: its row id and write time."""
    return {'event_id': event['event_id'], 'at': event.get('at')}


def fold(store):
    with store._db(True) as db:
        db.execute('BEGIN')
        evs = cstore.events(store, db)
        runs = cstore.runs(store, db)
        budget = _budget(db)
        settled = cstore.settlement_order(db)
    require(evs and evs[0]['kind'] == cstore.PREFIX + 'DECLARED', 'Ledger holds no Compass declaration')
    plan = evs[0]['value']
    state = {'plan': plan, 'head': digest({k: v for k, v in evs[-1].items() if k not in ('event_id', 'value')}),
             'events': len(evs), 'hypotheses': {}, 'observations': {}, 'refutations': {}, 'decisions': [],
             'jumps': [], 'dispositions': {}, 'tools': {}, 'tool_rejections': [], 'proposed_tools': {}, 'verifications': [],
             'handoffs': [], 'interruptions': [], 'interrupted': {}, 'reports': [], 'budget': budget,
             'runs': {rid: run['status'] for rid, run in runs.items()},
             'run_order': {rid: {'started_at': run.get('started_at'), 'settled_event': settled.get(rid)}
                           for rid, run in runs.items()}}
    for hyp in plan['hypotheses']:
        state['hypotheses'][hyp['id']] = {**hyp, 'seq': 0, 'anchor': _anchor(evs[0])}
    for event in evs[1:]:
        kind, value = event['kind'][len(cstore.PREFIX):], event['value']
        if kind == 'HYPOTHESIS':
            state['hypotheses'][value['id']] = {**value, 'seq': event['seq'], 'anchor': _anchor(event)}
        elif kind == 'OBSERVATION' and value['status'] == 'INTERRUPTED':
            state['interrupted'][value['run_id']] = {**value, 'seq': event['seq']}
        elif kind == 'OBSERVATION':
            state['observations'][value['candidate']] = {**value, 'seq': event['seq']}
        elif kind == 'REFUTATION':
            state['refutations'][value['hypothesis']] = value
        elif kind == 'DECISION':
            state['decisions'].append({**value, 'seq': event['seq']})
        elif kind == 'JUMP':
            state['jumps'].append({**value, 'seq': event['seq']})
        elif kind == 'DISPOSITION':
            state['dispositions'][value['packet_sha256']] = value
        elif kind == 'TOOL_QUALIFIED':
            state['tools'][value['sha256']] = {**value, 'status': 'QUALIFIED', 'uses': [], 'seq': event['seq'],
                                              'record_sha256': event['sha256']}
        elif kind == 'TOOL_REJECTED':
            state['tool_rejections'].append(value)
        elif kind == 'TOOL_PROPOSED':
            state['proposed_tools'].setdefault(value['sha256'], {**value, 'seq': event['seq']})
        elif kind == 'TOOL_USED':
            state['tools'][value['tool']]['uses'].append(value['hypothesis'])
        elif kind == 'TOOL_RETRACTED':
            state['tools'][value['tool']].update(status='RETRACTED', retraction=value)
        elif kind == 'VERIFICATION':
            state['verifications'].append(value)
        elif kind == 'HANDOFF':
            state['handoffs'].append(value)
        elif kind == 'INTERRUPTED':
            state['interruptions'].append(value)
        elif kind == 'REPORT':
            state['reports'].append(value)
    _derive(state)
    return state


def variables(state):
    names = list(state['plan']['inputs'])
    for tool in state['tools'].values():
        if tool['status'] == 'QUALIFIED':
            names.extend(sorted(tool['features']))
    return names


def env(state, candidate):
    out = {k: model.exact(v) for k, v in candidate['inputs'].items()}
    for tool in state['tools'].values():
        if tool['status'] == 'QUALIFIED':
            for name, table in tool['features'].items():
                if candidate['id'] in table:
                    out[name] = model.exact(table[candidate['id']])
    return out


def candidate(state, ident):
    return next(c for c in state['plan']['candidates'] if c['id'] == ident)


def measured(state):
    """Replay rows: only MEASURED cells on observables with a stated tolerance."""
    tolerances, rows = _tolerances(state['plan']), []
    for ident, obs in sorted(state['observations'].items(), key=lambda kv: kv[1]['seq']):
        if obs['status'] != 'MEASURED':
            continue
        outputs = {k: model.exact(v) for k, v in obs['outputs'].items() if k in tolerances}
        rows.append({'id': ident, 'inputs': env(state, candidate(state, ident)), 'outputs': outputs,
                     'receipt_sha256': obs['receipt_sha256']})
    return rows


def _derive(state):
    tolerances, rows = _tolerances(state['plan']), measured(state)
    for hyp in state['hypotheses'].values():
        tools = [p[5:] for p in hyp['premises'] if p.startswith('tool:')]
        if any(state['tools'].get(sha, {}).get('status') != 'QUALIFIED' for sha in tools):
            hyp.update(status='RETRACTED', replay=None)
            continue
        # An observable without a declared tolerance is never adjudicated: replay counts it UNKNOWN.
        result = model.replay(hyp, rows, {k: tolerances[k] for k in hyp['predict'] if k in tolerances})
        hyp.update(status=result['status'], replay=result)
    receipts = {row['id']: row['receipt_sha256'] for row in rows}
    for hyp in state['hypotheses'].values():
        for item in (hyp['replay'] or {}).get('counterexamples', []):
            item['receipt_sha256'] = receipts.get(item['id'])


def live(state):
    return sorted((h for h in state['hypotheses'].values() if h['status'] in LIVE),
                  key=lambda h: (-model.prior(h), h['id']))


def attempts(item):
    """The declared attempt slots of one candidate: the original and one retry slot."""
    return [{k: item[k] for k in ('run_id', 'output', 'argv')}] + ([item['retry']] if 'retry' in item else [])


def next_attempt(state, item):
    """The slot to buy next; only an outer interruption (no measurement) frees the retry slot."""
    for slot in attempts(item):
        status = state['runs'].get(slot['run_id'])
        if status is None:
            return slot
        if status != 'INTERRUPTED':
            return None
    return None


def remaining(state):
    holdout = set(state['plan'].get('holdout') or [])
    return [c for c in state['plan']['candidates']
            if c['id'] not in holdout and next_attempt(state, c) is not None]


# --- Receipt ingestion --------------------------------------------------------

def _observation(store, plan, item, receipt, output):
    base = {'candidate': item['id'], 'run_id': receipt['run_id'], 'receipt_sha256': receipt['sha256'],
            'inputs': item['inputs'], 'run_status': receipt['run_status'], 'provenance': 'INPUT_REPORTED',
            'scientific_support': 'UNKNOWN', 'outputs': {}, 'samples': {}}
    if receipt['process_status'] == 'INTERRUPTED':
        # An outer stop is not an experimental outcome: no measurement and no failure of
        # the system under study. The paid partial receipt stays; the retry slot opens.
        return {**base, 'status': 'INTERRUPTED'}
    if receipt['run_status'] != 'SUCCEEDED':
        return {**base, 'status': 'RUN_FAILED'}
    raw, binding = cstore.artifact(store, receipt, output)
    if raw is None:
        return {**base, 'status': binding}
    try:
        data = strict_json(raw.decode('utf-8'))
        require(isinstance(data, dict) and isinstance(data.get('outputs'), dict), 'Output needs an outputs object')
        outputs = {k: model.text(v) for k, v in sorted(data['outputs'].items()) if k in plan['observables']}
        samples = {}
        if 'samples' in data:
            require(isinstance(data['samples'], dict) and len(data['samples']) <= 4096, 'Invalid samples')
            samples = {str(k): model.text(v) for k, v in sorted(data['samples'].items())}
    except (ValueError, UnicodeDecodeError) as exc:
        return {**base, 'status': 'OUTPUT_INVALID', 'error': str(exc)[:256]}
    artifact = next(a for a in receipt['artifacts'] if a['path'] == output)
    return {**base, 'status': 'MEASURED', 'outputs': outputs, 'samples': samples,
            'artifact': {'path': output, 'sha256': artifact['sha256']}}


def ingest(store):
    """Turn every unread Compass receipt into an OBSERVATION, then record new refutations."""
    state = fold(store)
    plan = state['plan']
    by_run = {slot['run_id']: (c, index, slot) for c in plan['candidates'] for index, slot in enumerate(attempts(c))}
    receipts = cstore.receipts(store)
    fresh = []
    for run_id in sorted(receipts):
        item, index, slot = by_run.get(run_id, (None, None, None))
        if item is None or item['id'] in state['observations'] or run_id in state['interrupted']:
            continue
        ident = item['id'] if index == 0 else f"{item['id']}-a{index + 1}"
        cstore.append(store, 'OBSERVATION', ident, _observation(store, plan, item, receipts[run_id], slot['output']))
        fresh.append(item['id'])
    state = fold(store)
    tolerances = state['plan']['tolerances']
    for hyp in sorted(state['hypotheses'].values(), key=lambda h: h['id']):
        if hyp['status'] == 'REFUTED' and hyp['id'] not in state['refutations']:
            cstore.append(store, 'REFUTATION', hyp['id'], {
                'hypothesis': hyp['id'], 'hypothesis_sha256': hyp['sha256'], 'verdict': 'REFUTED',
                'basis': 'RECEIPTED_MEASUREMENT_COUNTEREXAMPLE', 'measurement_provenance': 'INPUT_REPORTED',
                'counterexamples': hyp['replay']['counterexamples'][:8],
                'tolerance': {k: tolerances.get(k) for k in hyp['predict']}, 'scientific_support': 'UNKNOWN'})
    from rds_compass_rsi import retract_unbound
    retract_unbound(store, fold(store))
    return fold(store), fresh


# --- Decision ----------------------------------------------------------------

def _weights(hyps):
    return {h['id']: model.prior(h) for h in hyps}


def _since_jump(state):
    last = max((j['seq'] for j in state['jumps']), default=0)
    return [d for d in state['decisions'] if d['seq'] > last and d['action'] in ('EXPERIMENT', 'CONFIRM')]


def _novel_for(state, obs, hyp):
    """The measured run started and settled after the rulebook entered the ledger.

    Ingestion order is not enough: a receipt bought before adoption and ingested
    later is old evidence, never a novel prediction.
    """
    order, anchor = state['run_order'].get(obs['run_id'], {}), hyp.get('anchor') or {}
    settled, started = order.get('settled_event'), order.get('started_at')
    return (settled is not None and anchor.get('event_id') is not None and settled > anchor['event_id']
            and started is not None and anchor.get('at') is not None and started > anchor['at'])


def confirmations(state, hyp):
    """Novel predictions: measurements bought after the rulebook entered and that it predicted.

    For IDENTIFY_LAW only a receipt that reports the goal observable confirms; a side
    observable alone never stands in for the goal. A prediction confirms only when it is
    decided within the declared tolerance: an undecided enclosure or an unadjudicated
    observable confirms nothing.
    """
    goal, count, tolerances = _goal_observable(state['plan']), 0, _tolerances(state['plan'])
    for ident, obs in state['observations'].items():
        if obs['status'] != 'MEASURED' or not _novel_for(state, obs, hyp):
            continue
        values, status = model.predict(hyp, env(state, candidate(state, ident)))
        keys = [goal] if goal is not None else list(values)
        if status == 'PREDICTED' and any(
                k in obs['outputs'] and k in tolerances and values.get(k) is not None
                and model.decide(values[k], model.exact(obs['outputs'][k]), tolerances[k]) is True for k in keys):
            count += 1
    return count


def basin(state):
    """Diagnose a deceptive basin: leaders keep failing and residuals stop shrinking."""
    escape = state['plan']['escape']
    streak, residuals = 0, []
    for decision in _since_jump(state):
        leader = state['hypotheses'].get(decision.get('leader'))
        if leader is None or decision['candidate'] not in state['observations']:
            continue
        if leader['status'] == 'REFUTED':
            streak += 1
            cells = [abs(model.exact(c['residual'])) for c in leader['replay']['counterexamples']
                     if c['residual'] is not None]
            residuals.append(min(cells) if cells else None)
        else:
            streak = 0
    hyps = list(state['hypotheses'].values())
    if not hyps:
        return {'trigger': 'NO_RIVALS', 'streak': 0}
    if not live(state):
        return {'trigger': 'ALL_REFUTED', 'streak': streak}
    if escape['refuted_streak'] and streak >= escape['refuted_streak']:
        return {'trigger': 'REFUTED_STREAK', 'streak': streak}
    window = escape['stagnation_rounds'] + 1
    tail = residuals[-window:]
    if escape['stagnation_rounds'] and len(tail) == window and None not in tail and all(
            b >= a for a, b in zip(tail, tail[1:])):
        return {'trigger': 'RESIDUAL_STAGNATION', 'streak': streak, 'residuals': [model.text(r) for r in tail]}
    return {'trigger': None, 'streak': streak}


def _min_points(state):
    return max(3, len(state['plan']['inputs']) + 2)


def _spread(state, count):
    """Probe points that cover the unobserved input space (max-min distance)."""
    seen = [env(state, candidate(state, i)) for i in state['observations']]
    pool, chosen = remaining(state), []
    names = list(state['plan']['inputs'])
    while pool and len(chosen) < count:
        def distance(item):
            point = {k: model.exact(v) for k, v in item['inputs'].items()}
            others = seen + chosen
            if not others:
                return (-sum(abs(point[n]) for n in names), item['id'])
            return (min(sum(abs(point[n] - o[n]) for n in names) for o in others), item['id'])
        best = max(pool, key=distance)
        pool.remove(best)
        chosen.append({k: model.exact(v) for k, v in best['inputs'].items()} | {'_id': best['id']})
    return [c.pop('_id') for c in chosen]


def _confirm_candidate(state, rows, pending, tolerances):
    """The confirmation point with the highest EIG, or the farthest spread point when none separates.

    A confirmation buys a prediction the leader did not see. Buying an input that no live rival
    can separate spends an experiment without ruling anything out, so the separating input wins;
    the spread point remains the fallback for coverage, where a sparse exception could sit.
    """
    if state['plan']['verification'].get('confirm_by_eig', True):
        adjudicable = [{**h, 'predict': {k: v for k, v in h['predict'].items() if k in tolerances}} for h in rows]
        seen = [env(state, candidate(state, i)) for i in state['observations']]
        ranking = model.rank_experiments(
            adjudicable, [{'id': c['id'], 'inputs': env(state, c), 'cost': c['cost']} for c in pending],
            tolerances, observed=seen, weights=_weights(rows), limit=1)
        if ranking['ranked']:
            return ranking['ranked'][0]['id']
    return _spread(state, 1)[0]


def _barren(state, jump):
    """A jump that added no rival at its evidence: ask again only after new evidence."""
    if any(tool['seq'] > jump['seq'] for tool in state['proposed_tools'].values()):
        return False  # A tool offered since this jump has not met the oracle yet.
    if jump['status'] == 'NO_RIVAL_WITHIN_BOUND':
        return True
    if jump['status'] != 'PROPOSALS_REQUESTED':
        return False
    disposition = state['dispositions'].get(jump['packet']['sha256'])
    return disposition is not None and not any(o.get('admitted') for o in disposition['outcomes'])


def decide(state):
    """Pure decision from the folded state; the caller records and executes it."""
    plan = state['plan']
    active = [rid for rid, status in state['runs'].items() if rid.startswith('compass-')
              and status in ('RESERVED', 'RUNNING')]
    if active:
        return {'action': 'RECOVER', 'runs': sorted(active)}
    spent = sum(1 for rid in state['runs'] if rid.startswith('compass-'))
    wall = state['budget'].get('wall_seconds', {}).get('remaining', 0)
    if plan['goal']['kind'] == 'IMPROVE':
        return _decide_improve(state, spent, wall)
    pending = remaining(state)
    can_buy = bool(pending) and spent < plan['budget']['experiments'] and wall >= plan['experiment']['wall_seconds']
    tolerances = _tolerances(plan)
    observable = plan['goal']['observable']
    if observable not in tolerances:
        return {'action': 'HANDOFF', 'reason': 'JUDGMENT_REQUIRED',
                'detail': 'Goal observable tolerance is UNKNOWN; refutation cannot be adjudicated'}
    diagnosis = basin(state)
    rows = live(state)
    last = state['jumps'][-1] if state['jumps'] else None
    if (diagnosis['trigger'] and last and _barren(state, last)
            and last.get('observations') == len(state['observations'])):
        # Same evidence, same bound: another synthesis pass cannot differ. Buy evidence or stop.
        if can_buy:
            return {'action': 'PROBE', 'candidate': _spread(state, 1)[0], 'basin': diagnosis,
                    'reason': 'NO_RIVAL_WITHIN_BOUND_AT_CURRENT_EVIDENCE'}
        return {'action': 'HANDOFF', 'reason': 'SEARCH_EXHAUSTED', 'basin': diagnosis,
                'detail': 'No counterexample-consistent rival within the synthesis/tool bound'}
    if diagnosis['trigger'] and not rows and not can_buy and len(state['observations']) < _min_points(state):
        return {'action': 'HANDOFF', 'reason': 'BUDGET_EXHAUSTED', 'basin': diagnosis}
    if diagnosis['trigger']:
        if len(state['jumps']) >= plan['escape']['max_jumps']:
            return {'action': 'HANDOFF', 'reason': 'SEARCH_EXHAUSTED', 'basin': diagnosis}
        measured_points = sum(1 for o in state['observations'].values() if o['status'] == 'MEASURED')
        probes_since = [d for d in state['decisions'] if d['action'] == 'PROBE'
                        and d['seq'] > max((j['seq'] for j in state['jumps']), default=0)]
        if measured_points < _min_points(state) and can_buy and len(probes_since) < plan['escape']['probe_points']:
            picks = _spread(state, 1)
            return {'action': 'PROBE', 'candidate': picks[0], 'basin': diagnosis}
        return {'action': 'ESCAPE', 'basin': diagnosis}
    if len(rows) >= 2 and can_buy:
        adjudicable = [{**h, 'predict': {k: v for k, v in h['predict'].items() if k in tolerances}} for h in rows]
        seen = [env(state, candidate(state, i)) for i in state['observations']]
        ranking = model.rank_experiments(adjudicable, [{'id': c['id'], 'inputs': env(state, c), 'cost': c['cost']}
                                                       for c in pending], tolerances, observed=seen,
                                         weights=_weights(rows), limit=3)
        if ranking['ranked']:
            top = ranking['ranked'][0]
            return {'action': 'EXPERIMENT', 'candidate': top['id'], 'leader': rows[0]['id'],
                    'eig_bits': top['eig_bits'], 'eig_per_cost': top['eig_per_cost'],
                    'partition_sha256': top['partition_sha256'],
                    'classes': top['classes'], 'alternatives': [r['id'] for r in ranking['ranked'][1:]],
                    'excluded': ranking['excluded'][:16], 'live': [h['id'] for h in rows]}
    observed_count = len(state['observations'])
    tried = any(d['action'] == 'RIVALS' and d['observations'] == observed_count for d in state['decisions'])
    if rows and can_buy and not tried and observable in tolerances:
        return {'action': 'RIVALS', 'live': [h['id'] for h in rows]}
    if rows and can_buy:
        leader = rows[0]
        done = confirmations(state, leader)
        # Plans compiled before spend_budget existed keep their recorded stopping rule.
        spend = plan['verification'].get('spend_budget', False)
        cap = plan['verification'].get('max_confirmations', 0)
        bought = sum(1 for d in state['decisions'] if d['action'] == 'CONFIRM')
        confirmed = done < plan['verification']['confirmations'] and (not cap or bought < cap)
        spending = spend and (not cap or bought < cap)
        if confirmed or spending:
            # Falsification test: a fitted law must survive predictions it did not see. While budget
            # remains, a leader is tested at the uncovered inputs before any claim is made.
            return {'action': 'CONFIRM', 'candidate': _confirm_candidate(state, rows, pending, tolerances),
                    'leader': leader['id'],
                    'confirmations': done, 'required': plan['verification']['confirmations'],
                    'reason': 'MINIMUM_CONFIRMATIONS' if done < plan['verification']['confirmations']
                    else 'SPEND_BUDGET_ON_REFUTATION'}
    holdout_pending = [cid for cid in plan.get('holdout') or [] if cid not in state['observations']]
    if rows and holdout_pending and can_buy:
        return {'action': 'HOLDOUT', 'candidate': holdout_pending[0], 'leader': rows[0]['id']}
    if rows:
        return {'action': 'VERIFY', 'live': [h['id'] for h in rows]}
    return {'action': 'HANDOFF', 'reason': 'JUDGMENT_REQUIRED', 'basin': diagnosis}


def next_step(state, stale):
    """``decide``, gated on a fresh ``stale_bindings`` recheck (None: none was made).

    Ingested evidence is re-bound before it steers a rival, a ranking, a purchase or a terminal
    handoff: changed or missing receipt bytes, or no recheck at all, lead to VERIFY, which records them.
    """
    step = decide(state)
    if step['action'] in ('RECOVER', 'VERIFY') or stale == []:
        return step
    return {'action': 'VERIFY', 'reason': 'RECEIPTS_UNCHECKED' if stale is None else 'STALE_EVIDENCE',
            'stale': (stale or [])[:8], 'decided': step['action']}


def _decide_improve(state, spent, wall):
    plan = state['plan']
    can_buy = spent < plan['budget']['experiments'] and wall >= plan['experiment']['wall_seconds']
    baseline = plan['profile']['baseline']
    base = state['observations'].get(baseline)
    if (base['status'] != 'MEASURED' or not base['samples']) if base else \
            next_attempt(state, candidate(state, baseline)) is None:
        # Every arm is judged against the baseline: once it settled without a usable measurement
        # (or has no slot left), a bought arm could never be evaluated, so nothing more is bought.
        return {'action': 'VERIFY', 'live': [], 'reason': 'BASELINE_UNMEASURED'}
    order = [baseline] + [c['id'] for c in sorted(plan['candidates'], key=lambda c: (model.exact(c['cost']), c['id']))
                          if c['id'] != baseline and 'replicates' not in c]
    for ident in order:
        if ident not in state['observations'] and next_attempt(state, candidate(state, ident)) is not None:
            if can_buy:
                return {'action': 'EXPERIMENT', 'candidate': ident, 'leader': None,
                        'reason': 'BASELINE' if ident == baseline else 'ARM_PROFILE'}
            return {'action': 'VERIFY', 'live': []}
    # Replication: re-run only arms whose every bought run passes the rule, best first. An arm that
    # failed a run cannot be accepted, so its replicates are never bought.
    improvement = _improve_verdicts(state)
    runs = _replicates(plan)
    pending = sorted((a for a in improvement['arms'] if a['verdict'] == 'UNREPLICATED'),
                     key=lambda a: (-model.exact(a['min_mean_gain']), a['arm']))
    for arm in pending:
        for ident in runs[arm['arm']]:
            if ident not in state['observations'] and next_attempt(state, candidate(state, ident)) is not None:
                if can_buy:
                    return {'action': 'EXPERIMENT', 'candidate': ident, 'leader': None, 'reason': 'REPLICATION',
                            'replicates': arm['arm']}
                return {'action': 'VERIFY', 'live': []}
    return {'action': 'VERIFY', 'live': []}


def record(store, state, step):
    """Bind a decision to the state head it was computed from."""
    value = {**deepcopy(step), 'head': state['head'], 'observations': len(state['observations'])}

    def guard(db, rows):
        require(digest(rows[-1]) == state['head'], 'Compass state advanced; recompute the decision')
    ident = 'd%04d' % (len(state['decisions']) + 1)
    return cstore.append(store, 'DECISION', ident, value, guard=guard)


def manifest(state, ident):
    item = candidate(state, ident)
    slot = next_attempt(state, item)
    require(slot is not None, 'Candidate has no unbought attempt slot: ' + ident)
    plan = state['plan']
    return {'schema': 1, 'id': slot['run_id'], 'arm': 'control', 'control_id': None, 'protocol': plan['protocol'],
            'argv': slot['argv'], 'outpaths': [slot['output']],
            'resource_estimates': {'wall_seconds': plan['experiment']['wall_seconds']},
            'timeout_seconds': plan['experiment']['timeout_seconds'],
            'description': 'Compass experiment ' + ident}


# --- Rival generation and abductive jumps ------------------------------------

def _points(state):
    return [env(state, c) for c in state['plan']['candidates']]


def _novel(state, hyp, points, tolerances, extra=()):
    """Classify a candidate rulebook against every retained hypothesis."""
    for other in list(state['hypotheses'].values()) + list(extra):
        verdict = model.equivalent(hyp, other, points, tolerances)
        if verdict:
            kind = 'REPEAT_OF_REFUTED' if other.get('status') == 'REFUTED' else 'DUPLICATE'
            return {'novel': False, 'reason': kind, 'equivalent_to': other['id'], 'relation': verdict}
    return {'novel': True}


EXTENDED_OPERATORS = ('add', 'sub', 'mul', 'div', 'sqrt', 'exp', 'log', 'sin', 'cos', 'pi')


def _grammar(state):
    plan = state['plan']
    names = variables(state)
    grammar = deepcopy(plan['grammar'] or {})
    base = grammar.get('variables', list(plan['inputs']))
    derived = [n for n in names if n not in plan['inputs']]
    grammar['variables'] = sorted(set(base) | set(derived))
    return grammar, names


def _fits(state, *, rows=None, probes=None, bound=None, escalate=False):
    """Counterexample-filtered synthesis in three modes; exhaustion is bound-relative."""
    plan = state['plan']
    tolerances = _tolerances(plan)
    observable = plan['goal']['observable']
    rows = measured(state) if rows is None else rows
    holdout = set(plan.get('holdout') or [])
    rows = [r for r in rows if observable in r['outputs'] and r['id'] not in holdout]
    if not rows or observable not in tolerances:
        return [], {'status': 'NO_MEASUREMENTS'}
    grammar, names = _grammar(state)
    probes = [env(state, c) for c in remaining(state)] if probes is None else probes
    bound = bound or plan['synthesis']
    found, notes = [], {}
    whole = model.synthesize(rows, names, observable, tolerances, grammar=grammar, max_nodes=bound['max_nodes'],
                             max_candidates=bound['max_candidates'], probes=probes, keep=4)
    escalated = None
    if escalate and not whole['fits'] and not (plan.get('grammar') or {}).get('operators'):
        # An open grammar may still be expressible in the wider dialect: escalate only after the
        # declared arithmetic bound is exhausted, so a cheap fit is never displaced.
        wider = {**grammar, 'operators': list(EXTENDED_OPERATORS)}
        escalated = model.synthesize(rows, names, observable, tolerances, grammar=wider,
                                     max_nodes=bound['max_nodes'],
                                     max_candidates=min(6000, bound['max_candidates']),
                                     probes=probes, keep=4)
        if escalated['fits']:
            whole = escalated
    notes['WHOLE_LAW'] = {k: whole[k] for k in ('status', 'attempted') if k in whole}
    if escalated is not None:
        notes['GRAMMAR_ESCALATION'] = {k: escalated[k] for k in ('status', 'attempted') if k in escalated}
    found += [(fit, 'WHOLE_LAW', None) for fit in whole['fits']]
    refuted = [h for h in state['hypotheses'].values() if h['status'] == 'REFUTED' and observable in h['predict']]
    if refuted:
        best = min(refuted, key=lambda h: (sum(abs(model.exact(c['residual'])) for c in h['replay']['counterexamples']
                                               if c['residual'] is not None), h['size'], h['id']))
        shifted = model.synthesize(rows, names, observable, tolerances, grammar=grammar,
                                   max_nodes=min(7, bound['max_nodes']),
                                   max_candidates=min(20000, bound['max_candidates']),
                                   probes=probes, base=best['predict'][observable], keep=2)
        notes['RESIDUAL_CORRECTION'] = {'status': shifted['status'], 'attempted': shifted['attempted'],
                                        'base': best['id']}
        found += [(fit, 'RESIDUAL_CORRECTION', best['id']) for fit in shifted['fits']]
    if not found and len(rows) >= 6:
        for name in plan['inputs']:
            split = model.regime_split(rows, names, observable, tolerances, variable=name, grammar=grammar,
                                       max_nodes=min(5, bound['max_nodes']), max_candidates=3000, probes=probes)
            notes['REGIME_SPLIT:' + name] = {'status': split['status'], 'attempted': split['attempted']}
            found += [(fit, 'REGIME_SPLIT', None) for fit in split['fits'][:2]]
    return found, notes


def _hypothesis_from_fit(state, fit, mode, parent, ident):
    plan = state['plan']
    observable = plan['goal']['observable']
    premises = []
    for tool in state['tools'].values():
        if tool['status'] == 'QUALIFIED' and set(tool['features']) & model.variables_of(fit['expression']):
            premises.append('tool:' + tool['sha256'])
    return model.compile_hypothesis({'id': ident, 'predict': {observable: fit['expression']},
                                     'premises': premises, 'origin': mode, 'parent': parent,
                                     'statement': f"{observable} = {fit['text']}"},
                                    variables(state), plan['observables'])


def generate_rivals(store, state):
    """When few rivals live, synthesize consistent alternatives that disagree on unobserved points."""
    tolerances = _tolerances(state['plan'])
    points = _points(state)
    found, notes = _fits(state)
    added = []
    for index, (fit, mode, parent) in enumerate(found):
        hyp = _hypothesis_from_fit(state, fit, mode, parent, 'r%02d-%d' % (len(state['decisions']) + 1, index))
        if _novel(state, hyp, points, tolerances, added)['novel']:
            added.append(hyp)
    for hyp in added:
        cstore.append(store, 'HYPOTHESIS', hyp['id'], {**hyp, 'origin': 'rival-synthesis:' + hyp['origin']})
        _note_tools(store, hyp)
    record(store, fold(store), {'action': 'RIVALS', 'live': [h['id'] for h in live(state)],
                          'found': [h['id'] for h in added], 'synthesis': notes,
                          'bound': state['plan']['synthesis']})
    return added


def _note_tools(store, hyp):
    for premise in hyp['premises']:
        if premise.startswith('tool:'):
            cstore.append(store, 'TOOL_USED', premise[5:12] + ':' + hyp['id'],
                          {'tool': premise[5:], 'hypothesis': hyp['id'], 'hypothesis_sha256': hyp['sha256']})


def _compact_hyp(hyp):
    return {'id': hyp['id'], 'statement': hyp['statement'][:256], 'sha256': hyp['sha256'], 'size': hyp['size'],
            'status': hyp.get('status'), 'origin': hyp.get('origin')}


def packet(state, items, trigger, notes, stale=None):
    rows = measured(state)
    observable = state['plan']['goal']['observable']
    body = {'schema': PACKET_SCHEMA, 'status': 'CURRENT', 'kind': 'compass-abductive-jump',
            'trigger': trigger, 'head': state['head'], 'goal': state['plan']['goal']['statement'][:512],
            'request': 'DISPOSITIONS' if items else 'PROPOSALS', 'items': items,
            'evidence': {'observations': [{'id': r['id'], 'inputs': {k: model.text(v) for k, v in r['inputs'].items()},
                                           observable: model.text(r['outputs'][observable]) if observable in r['outputs'] else None,
                                           'receipt_sha256': r['receipt_sha256']} for r in rows[-24:]],
                         'refuted': [{**_compact_hyp(h), 'witness': h['replay']['counterexamples'][:1]}
                                     for h in state['hypotheses'].values() if h['status'] == 'REFUTED'][-12:]},
            'synthesis': notes, 'omissions': {'items': 0, 'generation_results': False},
            'response': {'schema': 1, 'decisions': '[{id, disposition: adopt|adapt|reject|defer, reason, next_step, '
                                                    'hypothesis? (adapt: {predict: {obs: expr}})}]',
                         'proposals': 'optional [{id, predict, applies?, statement?}]',
                         'usage': 'optional {input_tokens, output_tokens, cached_input_tokens?}: this call',
                         'tools': 'optional [{source}] (at most 4): one-argument integer feature functions, tried at '
                                  'the next capability gap and qualified only by the receipted oracle triple'},
            'scientific_support': 'UNKNOWN'}
    if state['plan'].get('trend'):
        body['evidence']['fit_trend'] = fit_trend(state, compact=True, stale=stale)
    tokens = agent_tokens(state)
    if tokens['cap'] is not None:
        body['budget'] = {'agent_tokens': {'cap': tokens['cap'], 'spent': tokens['spent'],
                                           'remaining': tokens['cap'] - tokens['spent']}}
    while len(canonical(body).encode('utf-8')) > PACKET_BYTES and body['evidence']['observations']:
        body['evidence']['observations'] = body['evidence']['observations'][1:]
        body['omissions'] = {'items': 0, 'generation_results': False, 'evidence_rows_trimmed': True}
    return {**body, 'sha256': digest(body)}


def _streams(stdout, stderr):
    """Raw-byte identities of the agent's streams, complete or cut off by a timeout."""
    stdout, stderr = stdout or b'', stderr or b''
    return {'stdout_sha256': hashlib.sha256(stdout).hexdigest(), 'stdout_bytes': len(stdout),
            'stderr_sha256': hashlib.sha256(stderr).hexdigest(), 'stderr_bytes': len(stderr),
            'stderr_tail': stderr[-512:].decode('utf-8', 'replace')}


def _usage(stdout):
    """The agent's own token report on stdout, even from a failed call; None unless well formed."""
    try:
        usage = strict_json(stdout.decode('utf-8')).get('usage') if len(stdout) <= AGENT_BYTES else None
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None
    keys = {'input_tokens', 'output_tokens'}
    ok = isinstance(usage, dict) and keys <= set(usage) <= keys | {'cached_input_tokens'} and \
        all(type(v) is int and 0 <= v < 10 ** 12 for v in usage.values())
    return dict(usage) if ok else None


def fit_trend(state, compact=False, stale=None):
    """Train/test fit-trend conclusions recomputed from receipted measurements (``rds_fit_trend``).

    ``stale`` is a fresh ``stale_bindings`` result: without one the receipts are unchecked, and a
    trend resting on any receipt that no longer verifies concludes nothing.
    """
    plan = state['plan']
    spec, tolerances, higher = plan.get('trend'), _tolerances(plan), plan['higher_is_better']
    if spec is None:
        return None
    pair = {spec['train'], spec['test']}
    rows = [r for r in measured(state) if pair <= set(r['outputs'])]
    fixed = [k for k in plan['inputs'] if k not in (spec['along'], spec.get('replicate'))]
    varying = sorted(k for k in fixed if len({r['inputs'].get(k) for r in rows}) > 1)
    levels = len({r['inputs'].get(spec['along']) for r in rows})
    used = {row['candidate'] for row in stale or []} & {r['id'] for r in rows}
    if not pair <= set(tolerances) or not pair <= set(higher):
        out = {'status': 'UNKNOWN', 'reason': 'TOLERANCE_UNDECLARED' if not pair <= set(tolerances)
               else 'DIRECTION_UNDECLARED'}
    elif stale is None:
        out = {'status': 'UNKNOWN', 'reason': 'RECEIPTS_UNCHECKED'}
    elif used:
        out = {'status': 'STALE_EVIDENCE', 'stale': sorted(used)}
    elif varying:  # Another factor moved with x: no trend in x is concluded.
        out = {'status': 'CONFOUNDED', 'varying_inputs': varying}
    elif not 2 <= levels <= fit.MAX_LEVELS or len(rows) > fit.MAX_RUNS:
        out = {'status': 'INSUFFICIENT_LEVELS' if levels < 2 else 'TOO_MANY_RUNS', 'levels': levels}
    else:
        runs = [{'x': r['inputs'][spec['along']], 'train': r['outputs'][spec['train']],
                 'test': r['outputs'][spec['test']]} for r in rows]
        out = {'status': 'DESCRIBED', **fit.analyze(runs, {'train': tolerances[spec['train']],
                                                          'test': tolerances[spec['test']]}, not higher[spec['train']]),
               'receipts': [r['receipt_sha256'] for r in rows]}
    out = {**out, 'trend': spec, 'scientific_support': 'UNKNOWN'}
    if compact:
        out = {k: v for k, v in out.items() if k not in ('levels', 'receipts')}
        out.update({'steps': out['steps'][-12:]} if 'steps' in out else {})
    return out


def trend_stale(store, state):
    """Fresh receipt rechecks for a declared fit trend; None, and nothing re-read, without one."""
    return stale_bindings(store, state) if state['plan'].get('trend') else None


def agent_tokens(state):
    """Token spend over every recorded agent call; the counts are the agent's report (INPUT_REPORTED).

    With ``agent.max_tokens`` declared, a spent budget or any call without a usage report
    withholds further calls: an unaccounted spend is never assumed to be zero.
    """
    cap = (state['plan'].get('agent') or {}).get('max_tokens')
    calls = [d['agent'] for d in state['dispositions'].values() if d.get('agent')]
    reported = [c['usage'] for c in calls if c.get('usage') is not None]
    spent = sum(u['input_tokens'] + u['output_tokens'] for u in reported)
    status = 'CAP_UNKNOWN' if cap is None else 'USAGE_UNREPORTED' if len(reported) < len(calls) else \
        'EXHAUSTED' if spent >= cap else 'AVAILABLE'
    return {'cap': cap, 'spent': spent, 'cached_input_tokens': sum(u.get('cached_input_tokens', 0) for u in reported),
            'calls': len(calls), 'unreported_calls': len(calls) - len(reported), 'status': status,
            'provenance': 'INPUT_REPORTED'}


def _call_agent(store, agent, value):
    raw = canonical(value).encode('utf-8')
    sent = {'argv': agent['argv'], 'input_sha256': hashlib.sha256(raw).hexdigest()}
    try:
        done = subprocess.run(agent['argv'], input=raw, capture_output=True, timeout=agent['timeout_seconds'],
                              cwd=store.root, check=False)
    except subprocess.TimeoutExpired as exc:
        return None, {'status': 'AGENT_TIMEOUT', 'phase': 'RUNNING', 'timeout_seconds': agent['timeout_seconds'],
                      **sent, **_streams(exc.stdout, exc.stderr), 'usage': None}
    except OSError as exc:  # Never launched: nothing was spent.
        return None, {'status': 'AGENT_UNAVAILABLE', 'phase': 'LAUNCH', **sent,
                      'error': type(exc).__name__ + ': ' + str(exc)[:200], 'usage': {'input_tokens': 0, 'output_tokens': 0}}
    evidence = {'status': 'RETURNED', 'exit_code': done.returncode, **sent, **_streams(done.stdout, done.stderr),
                'usage': _usage(done.stdout)}
    if done.returncode != 0 or len(done.stdout) > AGENT_BYTES:
        return None, {**evidence, 'status': 'AGENT_FAILED'}
    try:
        return strict_json(done.stdout.decode('utf-8')), evidence
    except (ValueError, UnicodeDecodeError) as exc:
        return None, {**evidence, 'status': 'AGENT_OUTPUT_INVALID', 'error': str(exc)[:200]}


def _substantive(text):
    return isinstance(text, str) and 8 <= len(text.strip()) and len(text.encode('utf-8')) <= 2048 and \
        text.strip().casefold() not in {'ok', 'okay', 'ack', 'acknowledged', 'received', 'noted', '收到'}


def _goal_observable(plan):
    return plan['goal']['observable'] if plan['goal']['kind'] == 'IDENTIFY_LAW' else None


def validate_response(state, value, sealed, candidates):
    """Every delivered item exactly once; adapted/proposed rulebooks pass the record filter."""
    require(isinstance(value, dict) and value.get('schema') == 1 and value.get('packet_sha256') == sealed['sha256'],
            'Jump response packet/schema mismatch')
    require(set(value) <= {'schema', 'packet_sha256', 'decisions', 'proposals', 'usage', 'tools'},
            'Unknown jump response fields')
    from rds_compass_rsi import extract
    tools = value.get('tools', [])
    require(isinstance(tools, list) and len(tools) <= 4 and all(
        isinstance(t, dict) and set(t) == {'source'} and isinstance(t['source'], str) and len(t['source']) <= 16384
        for t in tools), 'Jump response tools must be at most 4 {source} objects')
    for tool in tools:
        try:
            extract(tool['source'])  # The same allowlist as every other tool, before anything is recorded.
        except SyntaxError as exc:
            raise ValueError('Jump response tool does not parse') from exc
    ids = [item['id'] for item in sealed['items']]
    decisions = value.get('decisions')
    require(isinstance(decisions, list) and len(decisions) == len(ids), 'Jump response must address every item once')
    plan, out, seen = state['plan'], [], set()
    for row in decisions:
        require(isinstance(row, dict) and {'id', 'disposition', 'reason', 'next_step'} <= set(row)
                <= {'id', 'disposition', 'reason', 'next_step', 'hypothesis'}, 'Invalid jump decision fields')
        require(row['id'] in ids and row['id'] not in seen, 'Invented or repeated jump item')
        require(row['disposition'] in DISPOSITIONS, 'Invalid jump disposition')
        require(_substantive(row['reason']) and _substantive(row['next_step']), 'Jump decision needs substantive text')
        require(('hypothesis' in row) == (row['disposition'] == 'adapt'), 'adapt (only) carries a hypothesis')
        seen.add(row['id'])
        clean = {k: row[k] for k in ('id', 'disposition', 'reason', 'next_step')}
        if row['disposition'] == 'adapt':
            spec = row['hypothesis']
            require(isinstance(spec, dict) and set(spec) <= {'predict', 'applies', 'statement'}, 'Invalid adaptation')
            base = candidates[row['id']]
            clean['hypothesis'] = _tool_premises(state, model.compile_hypothesis(
                {**spec, 'id': row['id'] + '-adapted', 'origin': 'agent-adapt', 'parent': row['id'],
                 'premises': base['premises']}, variables(state), plan['observables'], goal=_goal_observable(plan)))
        out.append(clean)
    proposals = []
    for spec in value.get('proposals', []):
        require(isinstance(spec, dict) and len(proposals) < 8, 'At most 8 agent proposals')
        proposals.append(_tool_premises(state, model.compile_hypothesis(
            {**spec, 'origin': 'agent-proposal'}, variables(state), plan['observables'], goal=_goal_observable(plan))))
    return out, proposals, [t['source'] for t in tools]


def _taken(state, hyp, extra):
    """A recorded id names one rulebook forever: reuse would overwrite or conflict in the ledger."""
    return hyp['id'] in state['hypotheses'] or any(h['id'] == hyp['id'] for h in extra)


def _admit(state, hyp, extra):
    """Record filter for externally supplied rulebooks: a fresh id, replay, then equivalence."""
    if _taken(state, hyp, extra):
        return {'admitted': False, 'reason': 'ID_TAKEN'}
    tolerances = _tolerances(state['plan'])
    result = model.replay(hyp, measured(state), {k: tolerances[k] for k in hyp['predict'] if k in tolerances})
    if result['status'] == 'REFUTED':
        return {'admitted': False, 'reason': 'REFUTED_BY_RECORD', 'witness': result['counterexamples'][:1]}
    novelty = _novel(state, hyp, _points(state), tolerances, extra)
    if not novelty['novel']:
        return {'admitted': False, **novelty}
    return {'admitted': True}


def _evidence_identity(store):
    return cstore.head(store), {rid: r['sha256'] for rid, r in cstore.receipts(store).items()}


def _tool_premises(state, hyp):
    """Bind a rulebook to every qualified tool whose feature it reads, whoever wrote it."""
    used = set()
    for expr in list(hyp['predict'].values()) + ([hyp['applies']] if hyp['applies'] is not None else []):
        used |= model.variables_of(expr)
    derived = {'tool:' + tool['sha256'] for tool in state['tools'].values()
               if tool['status'] == 'QUALIFIED' and set(tool['features']) & used}
    return {**hyp, 'premises': sorted(set(hyp['premises']) | derived)}


def escape(store, state, diagnosis):
    """Abductive jump: synthesize counterexample-filtered rivals, packetize, dispose."""
    from rds_compass_rsi import escalate, propose
    found, notes = _fits(state, escalate=True)
    rsi = None
    if not found and state['plan']['rsi']['enabled'] and state['plan']['inputs']:
        rsi = escalate(store, state, {'trigger': diagnosis['trigger'], 'synthesis': notes})
        if rsi['qualified']:
            state = fold(store)
            found, notes = _fits(state, escalate=True)
            notes['rsi'] = {'qualified': rsi['qualified']}
    tolerances = _tolerances(state['plan'])
    points = _points(state)
    jump_id = 'j%02d' % (len(state['jumps']) + 1)
    hyps, items, rejected = {}, [], []
    for index, (fit, mode, parent) in enumerate(found):
        hyp = _hypothesis_from_fit(state, fit, mode, parent, f'{jump_id}-{index}')
        novelty = _novel(state, hyp, points, tolerances, hyps.values())
        if not novelty['novel']:
            rejected.append({'id': hyp['id'], **novelty})
            continue
        hyps[hyp['id']] = hyp
        diverge = [c['id'] for c in remaining(state)
                   if any(model.predict(hyp, env(state, c))[0] != model.predict(o, env(state, c))[0]
                          for o in live(state))][:8]
        items.append({'id': hyp['id'], 'kind': mode, 'parent': parent, 'statement': hyp['statement'],
                      'size': hyp['size'], 'sha256': hyp['sha256'], 'premises': hyp['premises'],
                      'fits_measurements': len(measured(state)), 'diverges_from_live_at': diverge})
    agent = state['plan'].get('agent')
    budget = {'agent_tokens': agent_tokens(state)} if agent else {}
    if budget and budget['agent_tokens']['status'] in ('EXHAUSTED', 'USAGE_UNREPORTED'):
        agent = None  # Fail closed: a spent or unaccounted token budget buys no further agent call.
    if not items and not agent:
        cstore.append(store, 'JUMP', jump_id, {'trigger': diagnosis, 'packet': None, 'synthesis': notes,
                                               'rejected': rejected, 'rsi': rsi, 'status': 'NO_RIVAL_WITHIN_BOUND',
                                               'observations': len(state['observations']), **budget})
        return {'status': 'NO_RIVAL_WITHIN_BOUND', 'jump': jump_id, 'rsi': rsi, **budget}
    # With no machine rival left, a declared agent is asked for proposals: its rulebooks pass the
    # same record filter as any other, so the agent adds candidates, never evidence or verdicts.
    sealed = packet(state, items, diagnosis, notes, trend_stale(store, state))
    cstore.append(store, 'JUMP', jump_id, {'trigger': diagnosis, 'packet': sealed, 'synthesis': notes,
                                           'rejected': rejected, 'rsi': rsi,
                                           'status': 'PACKET_DELIVERED' if items else 'PROPOSALS_REQUESTED',
                                           'observations': len(state['observations']), **budget})
    disposer, evidence, decisions, proposals, errors, offers = 'COMPASS_AUTO', None, None, [], [], []
    if agent:
        delivered = _evidence_identity(store)
        response, evidence = _call_agent(store, agent, sealed)
        if response is not None and _evidence_identity(store) != delivered:
            # The packet's evidence moved while the agent worked: nothing filtered against it is adopted.
            cstore.append(store, 'DISPOSITION', jump_id, {
                'packet_sha256': sealed['sha256'], 'jump': jump_id, 'disposer': 'AGENT', 'agent': evidence,
                'errors': ['STALE_RESPONSE: ledger evidence changed during the response'], 'decisions': [],
                'outcomes': []})
            return {'status': 'STALE_RESPONSE', 'jump': jump_id, 'adopted': [], 'disposer': 'AGENT',
                    'errors': ['STALE_RESPONSE'], 'rsi': rsi, 'outcomes': []}
        if response is not None:
            try:
                decisions, proposals, offers = validate_response(state, response, sealed, hyps)
                disposer = 'AGENT'
            except (ValueError, KeyError, TypeError) as exc:
                errors.append('INVALID_RESPONSE: ' + str(exc)[:300])
        else:
            errors.append(evidence['status'])
    if decisions is None:
        # Unattended policy: the fits already passed the record filter, so adopt them.
        decisions = [{'id': i['id'], 'disposition': 'adopt', 'reason': 'Counterexample-filtered fit within bound',
                      'next_step': 'Discriminate against live rivals with the highest-EIG experiment'} for i in items]
    adopted, outcomes = [], []
    for row in decisions:
        if row['disposition'] in ('adopt', 'adapt'):
            hyp = hyps[row['id']] if row['disposition'] == 'adopt' else row['hypothesis']
            verdict = _admit(state, hyp, adopted) if row['disposition'] == 'adapt' else \
                {'admitted': False, 'reason': 'ID_TAKEN'} if _taken(state, hyp, adopted) else {'admitted': True}
            outcomes.append({'id': row['id'], 'disposition': row['disposition'], **verdict})
            if verdict['admitted']:
                adopted.append(hyp)
        else:
            outcomes.append({'id': row['id'], 'disposition': row['disposition'], 'admitted': False})
    for hyp in proposals:
        verdict = _admit(state, hyp, adopted)
        outcomes.append({'id': hyp['id'], 'disposition': 'proposal', **verdict})
        if verdict['admitted']:
            adopted.append(hyp)
    cstore.append(store, 'DISPOSITION', jump_id, {
        'packet_sha256': sealed['sha256'], 'jump': jump_id, 'disposer': disposer, 'agent': evidence,
        'errors': errors, 'decisions': [{k: v for k, v in d.items() if k != 'hypothesis'} for d in decisions],
        'outcomes': outcomes})
    for hyp in adopted:
        cstore.append(store, 'HYPOTHESIS', hyp['id'], {**hyp, 'origin': 'jump:' + jump_id + ':' + hyp['origin']})
        _note_tools(store, hyp)
    offered = [propose(store, source, 'AGENT_PROPOSED') for source in offers]
    return {'status': 'ADOPTED' if adopted else 'NONE_ADOPTED', 'jump': jump_id, 'adopted': [h['id'] for h in adopted],
            'disposer': disposer, 'errors': errors, 'rsi': rsi, 'outcomes': outcomes, **budget,
            **({'tools_offered': offered} if offered else {})}


# --- Verification, working set, report ---------------------------------------

def _lean(state, hyp, rows):
    from rds_lean_verify import verify as lean_verify
    tolerances = _tolerances(state['plan'])
    relations = []
    for row in rows[:128]:
        values, status = model.predict(hyp, row['inputs'])
        if status != 'PREDICTED':
            continue
        for name, observed in row['outputs'].items():
            if name not in values:
                continue
            absolute, relative = tolerances[name]
            # An irrational prediction is checked through its enclosure: |midpoint - observed| + radius bounds its gap.
            radius = getattr(values[name], 'radius', 0)
            if absolute == 0 and relative == 0 and not radius:
                relations.append({'relation': 'eq', 'left': model.text(values[name]), 'right': model.text(observed)})
            else:
                relations.append({'relation': 'le', 'left': model.text(abs(values[name] - observed) + radius),
                                  'right': model.text(absolute + relative * abs(observed))})
    if not relations:
        return {'status': 'NOT_APPLICABLE', 'reason': 'No predicted measured cells'}
    result = lean_verify({'schema': 1, 'kind': 'lean_vector_obligation', 'relations': relations[:128]})
    return {'status': result['status'], 'assurance': result['assurance'], 'backend': result['backend'],
            'relations': len(relations[:128]), 'scope': 'PREDICTED_VS_MEASURED_CLOSED_RATIONAL_RELATIONS',
            'reason': result.get('reason')}


def _solve_exact(matrix, rhs):
    """Gauss-Jordan over QQ; None unless the system has exactly one solution."""
    rows = [list(row) + [value] for row, value in zip(matrix, rhs)]
    columns = len(matrix[0])
    for column in range(columns):
        pivot = next((i for i in range(column, len(rows)) if rows[i][column] != 0), None)
        if pivot is None:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        rows[column] = [v / rows[column][column] for v in rows[column]]
        for i in range(len(rows)):
            if i != column and rows[i][column]:
                rows[i] = [a - rows[i][column] * b for a, b in zip(rows[i], rows[column])]
    if any(row[-1] != 0 for row in rows[columns:]):
        return None  # Inconsistent: no polynomial of this degree passes every measurement.
    return [rows[i][-1] for i in range(columns)]


def _polynomial_check(state, best):
    """#194: an independent certificate outside the synthesis bound for one-input laws.

    Fit the unique polynomial of degree <= m-2 through m receipted points (one
    spare equation), have ``rds_math_router.check_certificate`` replay it with
    its own exact checker, then compare it with the identified law on every
    declared input. A disagreement is a rival the synthesis bound never saw.
    """
    import rds_math_router as router
    plan = state['plan']
    observable = plan['goal']['observable']
    names = list(plan['inputs'])
    if len(names) != 1 or best['applies'] is not None or any(p.startswith('tool:') for p in best['premises']):
        return {'status': 'NOT_APPLICABLE', 'reason': 'One plain input and an unconditional law are required'}
    rows = [r for r in measured(state) if observable in r['outputs']][:router.MAX_ROWS]
    if len(rows) < 3:
        return {'status': 'NOT_APPLICABLE', 'reason': 'At least three receipted points are required'}
    degree = len(rows) - 2
    matrix = [[r['inputs'][names[0]] ** j for j in range(degree + 1)] for r in rows]
    rhs = [r['outputs'][observable] for r in rows]
    if any(router._bits(v) > router.MAX_COEFFICIENT_BITS for row in matrix for v in row + rhs):
        return {'status': 'NOT_APPLICABLE', 'reason': 'Coefficients exceed the router input bound'}
    spec = {'schema': router.REQUEST_SCHEMA, 'kind': 'rational_linear_system', 'domain': 'QQ',
            'matrix': [[router._fraction_json(v) for v in row] for row in matrix],
            'rhs': [router._fraction_json(v) for v in rhs], 'claim': 'unique_solution'}
    solution = _solve_exact(matrix, rhs)
    base = {'degree_bound': degree, 'points': [r['id'] for r in rows], 'spec_sha256': router._sha256(spec),
            'checker': 'rds_math_router.check_certificate', 'scientific_support': 'UNKNOWN'}
    if solution is None:
        return {**base, 'status': 'NO_POLYNOMIAL_WITHIN_DEGREE'}
    certificate = {'schema': router.CERTIFICATE_SCHEMA, 'kind': 'unique_linear_solution',
                   'spec_sha256': router._sha256(spec), 'solution': [router._fraction_json(v) for v in solution],
                   'rank_a': degree + 1}
    if not router.check_certificate(spec, certificate):
        return {**base, 'status': 'CERTIFICATE_REJECTED'}
    base.update(coefficients=certificate['solution'], assurance='CERTIFICATE_CHECKED')
    for item in plan['candidates']:
        point = env(state, item)
        values, status = model.predict(best, point)
        interpolant = sum(c * point[names[0]] ** j for j, c in enumerate(solution))
        if status != 'PREDICTED' or not model.agree(values[observable], interpolant, _tolerances(plan)[observable]):
            return {**base, 'status': 'POLYNOMIAL_RIVAL_DISAGREES',
                    'witness': {'candidate': item['id'], 'law': model.text(values[observable]) if status == 'PREDICTED'
                                else None, 'polynomial': model.text(interpolant)}}
    return {**base, 'status': 'AGREES_WITH_UNIQUE_POLYNOMIAL_ON_DECLARED_EXPERIMENTS'}


def _replicates(plan):
    """Declared runs per original arm: the arm first, then its replicates by (cost, id)."""
    runs = {c['id']: [c['id']] for c in plan['candidates'] if 'replicates' not in c}
    for c in sorted(plan['candidates'], key=lambda c: (model.exact(c['cost']), c['id'])):
        if 'replicates' in c:
            runs[c['replicates']].append(c['id'])
    return runs


def unknown_improve_terms(plan):
    """Terms of the declared IMPROVE rule that the declaration left unknown."""
    profile, metric = plan['profile'], plan['profile']['metric']
    return ([k for k in ('min_gain', 'max_degradation') if k not in profile] +
            (['higher_is_better'] if metric not in plan['higher_is_better'] else []) +
            (['tolerance'] if plan['tolerances'].get(metric) is None else []))


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


def verify(store, state):
    """Re-derive every measurement from receipts, replay exactly, and check closed relations."""
    plan = state['plan']
    stale = stale_bindings(store, state)
    if plan['goal']['kind'] == 'IMPROVE':
        value = {'goal': 'IMPROVE', 'receipts_rechecked': not stale, 'stale': stale,
                 'improvement': _improve_verdicts(state, stale), 'scientific_support': 'UNKNOWN'}
    else:
        rows = live(state)
        tolerances = _tolerances(plan)
        points = _points(state)
        classes = []
        for hyp in rows:
            for group in classes:
                # Tolerance equivalence is not transitive: a class holds only pairwise-equivalent
                # rulebooks, so one class means no live rival is separable from any other.
                if all(model.equivalent(hyp, member, points, tolerances) for member in group):
                    group.append(hyp)
                    break
            else:
                classes.append([hyp])
        best = min(classes[0], key=lambda h: (h['size'], h['id'])) if classes else None
        replay = model.replay(best, measured(state), {k: tolerances[k] for k in best['predict'] if k in tolerances}) if best else None
        value = {'goal': 'IDENTIFY_LAW', 'receipts_rechecked': not stale, 'stale': stale,
                 'hypothesis': _compact_hyp(best) if best else None,
                 # The claim states only what replay compared; an observable without a tolerance is
                 # named, never asserted.
                 'predict': {k: model.render(v) for k, v in best['predict'].items() if k in tolerances} if best else None,
                 'unadjudicated': sorted(k for k in best['predict'] if k not in tolerances) if best else [],
                 'equivalence_class': [h['id'] for h in classes[0]] if classes else [],
                 'distinct_live_classes': len(classes),
                 'replay': {k: replay[k] for k in ('status', 'tested', 'not_applicable', 'unknown_measurements')} if replay else None,
                 'lean': _lean(state, best, measured(state)) if best else None,
                 'polynomial': _polynomial_check(state, best) if best else None,
                 'novel_confirmations': confirmations(state, best) if best else 0,
                 'required_confirmations': plan['verification']['confirmations'],
                 'coverage': {'measured': len(measured(state)), 'declared': len(plan['candidates']),
                              'unmeasured_inputs_are': 'PREDICTED_NOT_MEASURED'},
                 'claim': ('NOT_ESTABLISHED' if not replay or replay['status'] != 'CONSISTENT' else
                           'CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS'
                           if confirmations(state, best) >= plan['verification']['confirmations'] else
                           'CONSISTENT_BUT_UNCONFIRMED'),
                 'uniqueness': ('UNIQUE_CLASS_WITHIN_SYNTHESIS_BOUND_ON_DECLARED_EXPERIMENTS' if len(classes) == 1
                                else 'MULTIPLE_LIVE_CLASSES'),
                 'bound': {'synthesis': plan['synthesis'], 'grammar': _grammar(state)[0],
                           'experiments': len(plan['candidates'])},
                 'tools': [{'name': t['name'], 'sha256': t['sha256'], 'status': t['status']}
                           for t in state['tools'].values()],
                 'scientific_support': 'UNKNOWN'}
    ident = 'v%02d' % (len(state['verifications']) + 1)
    cstore.append(store, 'VERIFICATION', ident, value)
    return value


def workset(state, reason=None, detail=None, stale=None):
    """Compact, hash-bound continuation state: enough for a fresh agent to resume."""
    plan = state['plan']
    rows = live(state)
    decision = next_step(state, stale) if reason is None else None
    body = {'schema': WORKSET_SCHEMA, 'head': state['head'], 'goal': plan['goal'],
            'unknown': plan['unknown'], 'live': [_compact_hyp(h) for h in rows[:8]],
            'live_total': len(rows),
            'refuted_total': sum(1 for h in state['hypotheses'].values() if h['status'] == 'REFUTED'),
            'recent_refutations': [{'hypothesis': k, 'witness': v['counterexamples'][:1]}
                                   for k, v in list(state['refutations'].items())[-4:]],
            'observations': len(state['observations']), 'experiments_remaining': len(remaining(state)),
            'budget': state['budget'], 'jumps': len(state['jumps']),
            'tools': [{'name': t['name'], 'sha256': t['sha256'], 'status': t['status']} for t in state['tools'].values()],
            'basin': basin(state) if plan['goal']['kind'] == 'IDENTIFY_LAW' else None,
            'next': {k: decision[k] for k in ('action', 'candidate', 'eig_bits', 'reason') if k in decision} if decision else None,
            'handoff': {'reason': reason, 'detail': detail} if reason else None,
            **({'agent_tokens': agent_tokens(state)} if plan.get('agent') else {}),
            **({'fit_trend': fit_trend(state, compact=True, stale=stale)} if plan.get('trend') else {}),
            'scientific_support': 'UNKNOWN'}
    require(reason is None or reason in HANDOFF_REASONS, 'Unknown handoff reason')
    return {**body, 'sha256': digest(body)}


def handoff(store, reason, detail=None):
    state = fold(store)
    value = workset(state, reason, detail, trend_stale(store, state))
    cstore.append(store, 'HANDOFF', 'h%03d' % (len(state['handoffs']) + 1), value)
    return value


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


def report(store):
    """Auditable campaign report recomputed from ledger records and receipts."""
    state = fold(store)
    plan, now = state['plan'], stale_bindings(store, state)
    body = {'schema': REPORT_SCHEMA, 'head': state['head'], 'goal': plan['goal'], 'unknown': plan['unknown'],
            'defaulted': plan['defaulted'], 'contract_sha256': plan['contract_sha256'],
            'declaration_sha256': plan['declaration_sha256'],
            'observations': [{'candidate': k, 'status': v['status'], 'inputs': v['inputs'], 'outputs': v['outputs'],
                              'provenance': v['provenance'], 'receipt_sha256': v['receipt_sha256']}
                             for k, v in sorted(state['observations'].items(), key=lambda kv: kv[1]['seq'])],
            'hypotheses': [{**_compact_hyp(h), 'premises': h['premises'],
                            'witness': (h['replay'] or {}).get('counterexamples', [])[:1]}
                           for h in sorted(state['hypotheses'].values(), key=lambda h: (h['seq'], h['id']))],
            'decisions': [{k: d.get(k) for k in ('action', 'candidate', 'leader', 'eig_bits', 'found', 'basin')}
                          for d in state['decisions']],
            'jumps': [{'id': 'j%02d' % (i + 1), 'status': j['status'], 'trigger': j['trigger'],
                       'packet_sha256': (j['packet'] or {}).get('sha256'), 'rejected': j['rejected'],
                       'rsi': j.get('rsi')} for i, j in enumerate(state['jumps'])],
            'dispositions': list(state['dispositions'].values()),
            'tools': [{k: t.get(k) for k in ('name', 'sha256', 'status', 'oracle', 'scope', 'origin', 'retraction')}
                      for t in state['tools'].values()],
            'tool_rejections': state['tool_rejections'],
            **({'tool_offers': [{k: t[k] for k in ('name', 'sha256', 'origin', 'status')}
                                for t in state['proposed_tools'].values()]} if state['proposed_tools'] else {}),
            'verification': _rechecked(state, now),
            'handoff': state['handoffs'][-1] if state['handoffs'] else None,
            'interruptions': state['interruptions'], 'budget': state['budget'],
            **({'agent_tokens': agent_tokens(state)} if plan.get('agent') else {}),
            **({'fit_trend': fit_trend(state, stale=now)} if plan.get('trend') else {}),
            'interrupted_attempts': [{'candidate': v['candidate'], 'run_id': k, 'receipt_sha256': v['receipt_sha256']}
                                     for k, v in sorted(state['interrupted'].items())],
            'experiments': sum(1 for rid in state['runs'] if rid.startswith('compass-')),
            'labels': {'measurement': 'INPUT_REPORTED', 'replay': 'CONSISTENT|REFUTED|UNTESTED',
                       'scientific_support': 'UNKNOWN'},
            'scientific_support': 'UNKNOWN'}
    return {**body, 'sha256': digest(body)}

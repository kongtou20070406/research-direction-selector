"""``compass`` CLI: one declaration in, one unattended auditable loop out."""

ACTIONS = ('init', 'drive', 'status', 'decide', 'report', 'tools')
DONE = {'GOAL_IDENTIFIED', 'IMPROVEMENT_EVALUATED', 'INITIALIZED', 'ALREADY_INITIALIZED'}


def add_parser(commands):
    compass = commands.add_parser('compass', help='Compass Advisor: EIG rival discrimination, basin escape, goal-bound RSI')
    actions = compass.add_subparsers(dest='action', required=True)
    init = actions.add_parser('init', help='Compile a Compass declaration into the kernel contract (unknowns stay UNKNOWN)')
    init.add_argument('--declaration', required=True)
    drive = actions.add_parser('drive', help='Run unattended: Advisor -> experiment -> jump/RSI -> verification -> report')
    drive_options(drive)
    actions.add_parser('status', help='Hash-bound compact working set and the next decision')
    actions.add_parser('decide', help='Print the next decision without executing it')
    actions.add_parser('report', help='Auditable report recomputed from ledger records and receipts')
    tools = actions.add_parser('tools', help='Qualified, rejected and retracted goal-bound tools, offers, catalogue')
    tools.add_argument('--propose', metavar='FILE', help='Offer a one-argument tool; it qualifies only via the oracle triple')
    tools.add_argument('--catalogue', action='store_true', help='List the base tools offered at a capability gap')
    tool = actions.add_parser('tool', help='Agent-callable primitives: synthesize, rank, replay (JSON request in/out)')
    primitives = tool.add_subparsers(dest='primitive', required=True)
    for name in ('synthesize', 'rank', 'replay', 'holdout'):
        primitives.add_parser(name, help='Read a JSON request from --request; holdout reads the ledger')\
            .add_argument('--request', required=True)
    return compass


def _exact_rows(rows):
    import rds_compass_model as model
    return [{'id': row.get('id', str(i)),
             'inputs': {k: model.exact(v) for k, v in row['inputs'].items()},
             'outputs': {k: model.exact(v) for k, v in row['outputs'].items()}}
            for i, row in enumerate(rows)]


def agent_tool(primitive, request):
    """The primitives an agent needs in its own loop, without a declaration or a ledger."""
    import rds_compass_model as model
    observable = request['observable']
    tolerances = {name: model.tolerance(spec) for name, spec in request['tolerances'].items()}
    if primitive == 'synthesize':
        return model.synthesize(request['rows'], request['inputs'], observable, tolerances,
                                grammar=request.get('grammar'),
                                max_nodes=request.get('max_nodes', 7),
                                max_candidates=request.get('max_candidates', 20000),
                                probes=request.get('probes', []), keep=request.get('keep', 4))
    if primitive == 'replay':
        hyp = model.compile_hypothesis({'id': 'candidate', 'predict': {observable: request['law']}},
                                       request['inputs'], [observable])
        return model.replay(hyp, _exact_rows(request['rows']), tolerances)
    if primitive == 'holdout':
        import rds_compass as compass
        import rds_compass_store as cstore
        store = cstore.store_for(request['root'])
        state = compass.fold(store)
        stale = compass.stale_bindings(store, state)
        if stale:
            return {'status': 'STALE_EVIDENCE', 'stale': stale[:8], 'scientific_support': 'UNKNOWN'}
        hyp = state['hypotheses'].get(request['hypothesis_id'])
        if hyp is None:
            return {'status': 'UNKNOWN_HYPOTHESIS', 'hypothesis': request['hypothesis_id']}
        rows = []
        for ident in request['measurement_ids']:
            obs = state['observations'].get(ident)
            if obs is None or obs['status'] != 'MEASURED':
                return {'status': 'UNKNOWN_MEASUREMENT', 'measurement': ident, 'scientific_support': 'UNKNOWN'}
            rows.append({'id': ident, 'inputs': compass.env(state, compass.candidate(state, ident)),
                         'outputs': {k: model.exact(v) for k, v in obs['outputs'].items()},
                         'receipt_sha256': obs['receipt_sha256']})
        declared = {name: model.tolerance(spec) for name, spec in state['plan']['tolerances'].items()
                    if spec is not None}
        return {**model.replay(hyp, rows, declared), 'ledger_head': state['head'], 'receipts_rechecked': True,
                'scientific_support': 'UNKNOWN'}
    hyp = [model.compile_hypothesis({'id': rival['id'], 'predict': {observable: rival['predict']}},
                                    request['inputs'], [observable]) for rival in request['rivals']]
    observed = [{k: model.exact(v) for k, v in row['inputs'].items()} for row in request.get('rows', [])]
    return model.rank_experiments(hyp, request['candidates'], tolerances, observed=observed,
                                  limit=request.get('limit', 8))


def drive_options(parser):
    parser.add_argument('--max-steps', type=int, default=200, help='Controller steps this pass (each step is durable)')
    parser.add_argument('--wall-seconds', type=float, help='Controller deadline; ends with partial evidence, resumable')
    parser.add_argument('--no-tms', action='store_true', help='Skip mirroring Compass records into the TMS map')


def command(args):
    import rds_compass as compass
    import rds_compass_store as cstore
    if args.action == 'tool':
        import json
        with open(args.request, encoding='utf-8') as stream:
            return agent_tool(args.primitive, json.load(stream))
    if args.action == 'init':
        from rds_compass_declaration import initialize
        result = initialize(args.root, args.declaration)
        plan = result.pop('plan')
        return {**result, 'unknown': plan['unknown'], 'defaulted': plan['defaulted'],
                'candidates': len(plan['candidates']), 'hypotheses': len(plan['hypotheses']),
                'goal': plan['goal'], 'next_command': 'compass drive'}
    if args.action == 'drive':
        from rds_compass_drive import drive
        return drive(args.root, max_steps=args.max_steps, wall_seconds=args.wall_seconds, publish=not args.no_tms)
    if args.action == 'tools' and args.catalogue:
        import rds_compass_rsi as rsi
        return {'catalogue': rsi.catalogue(), 'scientific_support': 'UNKNOWN'}
    store = cstore.store_for(args.root)
    if args.action == 'tools' and args.propose:
        import rds_compass_rsi as rsi
        with open(args.propose, encoding='utf-8') as stream:
            return {**rsi.propose(store, stream.read(), 'USER_PROPOSED'), 'scientific_support': 'UNKNOWN'}
    if args.action in ('status', 'decide'):
        state = compass.fold(store)
        stale = compass.stale_bindings(store, state)
        if args.action == 'status':
            return compass.workset(state, stale=stale)
        return {'head': state['head'], 'decision': compass.next_step(state, stale), 'execution_started': False}
    if args.action == 'report':
        return compass.report(store)
    state = compass.fold(store)
    return {'tools': [{k: t.get(k) for k in ('name', 'sha256', 'status', 'origin', 'library_provenance', 'oracle',
                                             'boundary', 'scope', 'path', 'retraction', 'uses')}
                      for t in state['tools'].values()],
            'rejected': state['tool_rejections'],
            'offers': [{k: t[k] for k in ('name', 'sha256', 'origin', 'status')} for t in state['proposed_tools'].values()],
            'scientific_support': 'UNKNOWN'}


def exit_code(result):
    status = result.get('status')
    return 0 if status in DONE or status is None else 2

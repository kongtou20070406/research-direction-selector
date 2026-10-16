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
    return compass


def drive_options(parser):
    parser.add_argument('--max-steps', type=int, default=200, help='Controller steps this pass (each step is durable)')
    parser.add_argument('--wall-seconds', type=float, help='Controller deadline; ends with partial evidence, resumable')
    parser.add_argument('--no-tms', action='store_true', help='Skip mirroring Compass records into the TMS map')


def command(args):
    import rds_compass as compass
    import rds_compass_store as cstore
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

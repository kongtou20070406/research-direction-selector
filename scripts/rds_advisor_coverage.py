"""One complete-analysis gate shared by Advisor, QUICK choice and owned admission.

Coverage concerns declared program semantics. It neither proves the research
claims nor converts descriptive relations or unknown evidence into true facts.
"""
from copy import deepcopy


def _operation_frontier(context):
    """Calculate the supplied frontier once, retaining its existing semantics."""
    if 'frontier' not in context:
        return None
    from rds_frontier import discover_frontier
    from rds_project import digest
    spec = context['frontier']
    report = discover_frontier(spec)
    excluded = {(row['record_type'], row['id']): row for row in report['excluded']}
    nodes = spec.get('nodes', [])
    edges = spec.get('edges', [])
    eligible_nodes = [row for row in nodes if ('nodes', row['id']) not in excluded]
    used_nodes = {row['id'] for row in eligible_nodes[:report['truncation']['limits']['max_nodes']]}
    eligible_edges = [i for i, row in enumerate(edges) if ('edges', 'edge:' + str(i)) not in excluded
                      and row['from'] in used_nodes and row['to'] in used_nodes]
    used_edges = set(eligible_edges[:report['truncation']['limits']['max_edges']])
    def disposition(kind, ident, used):
        if (kind, ident) in excluded:
            return {'disposition': 'EXCLUDED_AT_CUTOFF', 'reason': excluded[kind, ident]['reason']}
        return {'disposition': 'AVAILABLE_GRAPH_INPUT' if used else 'OMITTED_GRAPH_INPUT',
                'reason': 'Used by existing frontier analysis' if used else 'Node/edge bound or unavailable endpoint'}
    truncated = report['truncation']['truncated']
    report['coverage'] = {'status': 'INCOMPLETE' if truncated else 'FULL', 'full': not truncated,
                          'input_sha256': digest(spec), 'node_count': len(nodes), 'edge_count': len(edges),
                          'analysis': 'EXISTING_FRONTIER_REACHABILITY_AND_DECLARED_REQUESTS',
                          'analyzed_nodes': [{'id': row['id'], **disposition('nodes', row['id'], row['id'] in used_nodes)}
                                             for row in nodes],
                          'analyzed_edges': [{'id': 'edge:' + str(i), 'declared_status': row['status'],
                                              **disposition('edges', 'edge:' + str(i), i in used_edges)}
                                             for i, row in enumerate(edges)],
                          'statistics': deepcopy(report['statistics']), 'excluded': deepcopy(report['excluded']),
                          'reasons': ['Frontier: ' + reason for reason in report['truncation']['reasons']]}
    return report


def project_context(root, context):
    """Saved active dependencies cannot be omitted or replaced for one choice."""
    if not isinstance(context, dict):
        raise ValueError('Advisor context must be an object')
    from rds_tms_store import current, with_saved_dependencies
    saved = current(root)
    if saved is None:
        if context.get('dependency_snapshot_sha256') is not None:
            raise ValueError('Previously reviewed dependency snapshot is missing')
        return context
    if context.get('dependency_snapshot_sha256', saved['sha256']) != saved['sha256']:
        raise ValueError('Dependency snapshot changed after analysis; review the current complete graph')
    supplied = context.get('dependency_map')
    normalized = with_saved_dependencies(root, {k: v for k, v in context.items() if k != 'dependency_map'})
    if supplied is not None and supplied not in (saved['dependency_map'], normalized['dependency_map']):
        raise ValueError('Advisor must use the complete current dependency graph; update project dependencies before reviewing a changed graph')
    return {**normalized, 'dependency_snapshot_sha256': saved['sha256']}


def assess(search, dependency=None):
    graphs = []
    reasons = []
    graph = search.get('graph_coverage')
    if not isinstance(graph, dict):
        reasons.append('Direction graph coverage is absent')
    else:
        graphs.append({'kind': 'DIRECTION_GRAPH', **deepcopy(graph)})
        if graph.get('full') is not True:
            reasons.extend(graph.get('reasons') or ['Direction graph analysis is incomplete'])
    if search.get('truncation', {}).get('truncated'):
        reasons.extend('Direction search: ' + r for r in search['truncation'].get('reasons', ['truncated']))
    composition = search.get('experiment_composition') or {}
    truncation = composition.get('truncation') or {}
    if composition.get('truncated') or truncation.get('truncated'):
        reasons.append('Experiment composition is truncated')
    if dependency is not None:
        coverage = dependency.get('coverage') or {}
        graphs.append({'kind': 'DEPENDENCY_GRAPH', **deepcopy(coverage),
                       'node_count': coverage.get('counts', {}).get('nodes'),
                       'edge_count': coverage.get('counts', {}).get('hyperedges')})
        if dependency.get('status') != 'ANALYZED' or coverage.get('full') is not True:
            reasons.extend(coverage.get('reasons') or [dependency.get('reason') or 'Dependency graph analysis is incomplete'])
    if search.get('frontier_coverage') is not None:
        coverage = search['frontier_coverage']
        graphs.append({'kind': 'FRONTIER_GRAPH', **deepcopy(coverage)})
        if coverage.get('full') is not True:
            reasons.extend(coverage.get('reasons') or ['Frontier graph analysis is incomplete'])
    return {'scope': 'ALL_DECLARED_ACTIVE_GRAPHS', 'status': 'INCOMPLETE' if reasons else 'FULL',
            'full': not reasons, 'graphs': graphs, 'reasons': list(dict.fromkeys(reasons)),
            'authorization': 'UNCHANGED', 'assurance': 'DECLARED_PROGRAM_ANALYSIS_NOT_SCIENTIFIC_PROOF'}


def require_complete(searches):
    if not searches or any(s.get('analysis_coverage', {}).get('full') is not True for s in searches):
        raise ValueError('Complete graph analysis required before selection or dispatch; inspect analysis_coverage. '
                         'Resolve missing inputs or computation limits without removing project graph records')

"""One complete-analysis gate shared by Advisor, QUICK choice and owned admission.

Coverage concerns declared program semantics. It neither proves the research
claims nor converts descriptive relations or unknown evidence into true facts.
"""
from copy import deepcopy


def project_context(root, context):
    """Saved active dependencies cannot be omitted or replaced for one choice."""
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
    return {'scope': 'ALL_DECLARED_ACTIVE_GRAPHS', 'status': 'INCOMPLETE' if reasons else 'FULL',
            'full': not reasons, 'graphs': graphs, 'reasons': list(dict.fromkeys(reasons)),
            'authorization': 'UNCHANGED', 'assurance': 'DECLARED_PROGRAM_ANALYSIS_NOT_SCIENTIFIC_PROOF'}


def require_complete(searches):
    if not searches or any(s.get('analysis_coverage', {}).get('full') is not True for s in searches):
        raise ValueError('Complete graph analysis required before selection or dispatch; inspect analysis_coverage. '
                         'Resolve missing inputs or computation limits without removing project graph records')

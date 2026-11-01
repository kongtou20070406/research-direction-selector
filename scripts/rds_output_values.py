"""Pure, bounded output projections and platform-aware serialization."""
import json
import os

# Growth limit for a retained settlement report; L1 campaign and L3 owned
# advisor share it without importing each other's layer.
MAX_REPORT_BYTES = 32 * 1024 * 1024


def _brief_move(summary, move):
    """Retain a bounded explanation while keeping the historical next_move string."""
    if not isinstance(move, dict):
        summary['next_move'] = move
        summary.pop('next_move_detail', None)
        return
    summary['next_move'] = move.get('kind', move)
    detail = {}
    for key in ('reason', 'basis', 'source'):
        if isinstance(move.get(key), str):
            limit = 512 if key == 'reason' else 128
            detail[key] = move[key][:limit]
            if len(move[key]) > limit:
                detail[key + '_truncated'] = True
    if detail:
        summary['next_move_detail'] = detail
    else:
        summary.pop('next_move_detail', None)



def serialize_brief(summary, *, newline=False):
    """Share CLI JSON formatting and account for print's platform newline."""
    raw = json.dumps(summary, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return raw + os.linesep if newline else raw



def compact_brief_planning(summary):
    """Fit display-only planning around exact locators and deciding observations."""
    planning = summary.get('planning')
    if not isinstance(planning, dict):
        return
    # Match CLI serialization, including its newline and platform-dependent path.
    def fits():
        return len(serialize_brief(summary, newline=True).encode('utf-8')) < 1024

    for key, omitted in (('ready_obligations', 'omitted_ready_obligations'),
                         ('local_checks', 'local_checks_omitted'),
                         ('open_predicates', 'open_predicates_omitted')):
        rows = planning.get(key, [])
        while rows and not fits():
            rows.pop()
            planning[omitted] = planning.get(omitted, 0) + 1
        if not rows:
            planning.pop(key, None)
    if not fits():
        summary['planning'] = {'scope': planning.get('scope'), 'details_omitted': True}
        if planning.get('open_predicates_truncated'):
            summary['planning']['open_predicates_truncated'] = planning['open_predicates_truncated']
    if not fits():
        minimal = summary.pop('planning')
        # Keep the scope/alert only when required fields alone exceed the budget.
        if not fits():
            summary['planning'] = minimal

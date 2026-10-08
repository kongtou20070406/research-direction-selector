"""Finite result operations for existing RSI qualification and owned consumers.

These functions accept data, not paths to read or commands to execute. Source
metadata remains caller-supplied; the original consumer must bind actual source,
input and receipt bytes. Arithmetic and inventory do not establish scientific
support. Extract the selected function with the existing ``rsi extract`` entry.
"""
from json import loads
from math import isfinite

MAX_BYTES = 2097152
MAX_NODES = 20000
IDENTITY_FIELDS = ('run_id', 'source_path', 'source_sha256', 'data_sha256',
                   'data_split', 'evaluator_sha256', 'metric', 'metric_definition',
                   'reduction', 'unit', 'direction', 'code_sha256', 'config_sha256')
SOURCE_FIELDS = ('run_id', 'source_path', 'source_sha256')
MATCH_FIELDS = ('data_sha256', 'data_split', 'evaluator_sha256', 'metric',
                'metric_definition', 'reduction', 'unit', 'direction')


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _utf8_size(value):
    try:
        return len(value.encode('utf-8'))
    except UnicodeError:
        raise ValueError('Invalid Unicode scalar in JSON data')


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('Non-finite JSON constant')


def _tree(value, depth=0):
    _require(depth <= 32, 'JSON nesting exceeds 32')
    count = 1
    if isinstance(value, float):
        _require(isfinite(value), 'Non-finite JSON number')
    elif isinstance(value, str):
        _require(_utf8_size(value) <= MAX_BYTES, 'JSON string exceeds limit')
    elif isinstance(value, dict):
        _require(len(value) <= MAX_NODES, 'JSON object exceeds limit')
        for key, item in value.items():
            _require(isinstance(key, str), 'JSON object key must be text')
            count += _tree(key, depth + 1) + _tree(item, depth + 1)
            _require(count <= MAX_NODES, 'JSON node count exceeds limit')
    elif isinstance(value, list):
        _require(len(value) <= MAX_NODES, 'JSON array exceeds limit')
        for item in value:
            count += _tree(item, depth + 1)
            _require(count <= MAX_NODES, 'JSON node count exceeds limit')
    else:
        _require(value is None or isinstance(value, (bool, int)), 'Not JSON data')
    return count


def _document(raw_text):
    _require(isinstance(raw_text, str) and len(raw_text) <= MAX_BYTES,
             'Supply bounded original JSON text')
    _require(_utf8_size(raw_text) <= MAX_BYTES, 'Original JSON exceeds 2 MiB')
    try:
        value = loads(raw_text, object_pairs_hook=_pairs, parse_constant=_constant)
    except RecursionError:
        raise ValueError('JSON nesting exceeds parser limit')
    _tree(value)
    return value


def _identity(value):
    _require(isinstance(value, dict) and set(value) <= set(IDENTITY_FIELDS),
             'Unsupported result identity fields')
    for key, item in value.items():
        _require(item is None or isinstance(item, str) and _utf8_size(item) <= 1024,
                 'Identity must contain bounded text or null')
        if item is not None and key.endswith('_sha256') and item != 'UNKNOWN':
            _require(len(item) == 64 and all(c in '0123456789abcdef' for c in item),
                     'Identity hash must be lowercase SHA256')
    return value.copy()


def _missing(identity, fields):
    return [key for key in fields if not isinstance(identity.get(key), str)
            or not identity[key].strip() or identity[key].strip().upper() == 'UNKNOWN']


def _conflicts(document, identity):
    if not isinstance(document, dict):
        return []
    origins = [document] + [document[key] for key in ('binding', 'protocol', 'identity')
                            if isinstance(document.get(key), dict)]
    return sorted(set(key for origin in origins for key in IDENTITY_FIELDS
                      if key in origin and key in identity and origin[key] != identity[key]))


def _pointer(document, pointer):
    _require(isinstance(pointer, str) and _utf8_size(pointer) <= 4096
             and (pointer == '' or pointer.startswith('/')), 'Invalid JSON pointer')
    parts = pointer.split('/')[1:] if pointer else []
    _require(len(parts) <= 32, 'JSON pointer exceeds depth limit')
    _require(all(part[:1] in ('0', '1') for segment in parts for part in segment.split('~')[1:]),
             'Invalid JSON pointer escape')
    value = document
    for segment in parts:
        key = segment.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            _require(key.isascii() and key.isdigit() and (key == '0' or not key.startswith('0')),
                     'Invalid JSON array index')
            index = int(key)
            if index >= len(value):
                return False, None
            value = value[index]
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return False, None
    return True, value


def extract_json_result(raw_text, pointer, identity):
    """Read one scalar from strict original JSON; unresolved metadata stays unknown."""
    identity = _identity(identity)
    document = _document(raw_text)
    found, value = _pointer(document, pointer)
    missing = _missing(identity, SOURCE_FIELDS)
    conflicts = _conflicts(document, identity)
    result = {'operation': 'extract_json_result', 'status': 'UNKNOWN', 'value': None,
              'identity': identity, 'pointer': pointer, 'missing_fields': missing,
              'conflicting_fields': conflicts, 'reason': None,
              'identity_assurance': 'CALLER_METADATA', 'scientific_support': 'UNKNOWN'}
    if missing or conflicts:
        result['reason'] = 'INCOMPLETE_OR_CONFLICTING_IDENTITY'
    elif not found:
        result['reason'] = 'MISSING_VALUE'
    elif value is None:
        result['reason'] = 'NULL_VALUE'
    elif isinstance(value, (dict, list)):
        result['reason'] = 'NON_SCALAR_VALUE_EXPAND_ORIGINAL'
    elif isinstance(value, str) and _utf8_size(value) > 2048:
        result['reason'] = 'VALUE_EXCEEDS_DISPLAY_LIMIT_EXPAND_ORIGINAL'
    else:
        result.update(status='OBSERVED', value=value)
    return result


def _numeric(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and (not isinstance(value, float) or isfinite(value)))


def compare_metrics(candidate, baseline):
    """Compute a descriptive difference only for complete matching metric identities."""
    _require(isinstance(candidate, dict) and isinstance(baseline, dict), 'Metric points must be objects')
    ci, bi = _identity(candidate.get('identity')), _identity(baseline.get('identity'))
    cv, bv = candidate.get('value'), baseline.get('value')
    _tree(cv)
    _tree(bv)
    _require(not isinstance(cv, (list, dict)) and not isinstance(bv, (list, dict)),
             'Metric values must be scalars')
    missing = ['candidate.' + key for key in _missing(ci, SOURCE_FIELDS + MATCH_FIELDS)]
    missing += ['baseline.' + key for key in _missing(bi, SOURCE_FIELDS + MATCH_FIELDS)]
    conflicts = [key for key in MATCH_FIELDS if key in ci and key in bi and ci[key] != bi[key]]
    if ci.get('run_id') == bi.get('run_id') and ci.get('source_sha256') != bi.get('source_sha256'):
        conflicts.append('same_run_different_sources')
    cv_large = isinstance(cv, str) and _utf8_size(cv) > 2048
    bv_large = isinstance(bv, str) and _utf8_size(bv) > 2048
    result = {'operation': 'compare_metrics', 'status': 'UNKNOWN', 'delta': None, 'improvement': None,
              'candidate': {'value': None if cv_large else cv, 'value_omitted': cv_large, 'identity': ci},
              'baseline': {'value': None if bv_large else bv, 'value_omitted': bv_large, 'identity': bi},
              'matched_fields': [], 'missing_fields': missing, 'conflicting_fields': conflicts,
              'reason': None, 'identity_assurance': 'CALLER_METADATA', 'scientific_support': 'UNKNOWN'}
    if missing or conflicts:
        result['reason'] = 'INCOMPLETE_OR_CONFLICTING_IDENTITY'
    elif (candidate.get('status', 'OBSERVED') != 'OBSERVED'
          or baseline.get('status', 'OBSERVED') != 'OBSERVED'):
        result['reason'] = 'UNRESOLVED_INPUT'
    elif not _numeric(cv) or not _numeric(bv):
        result['reason'] = 'MISSING_OR_NON_NUMERIC_VALUE'
    elif ci['direction'] not in ('minimize', 'maximize'):
        result['reason'] = 'UNSUPPORTED_METRIC_DIRECTION'
    else:
        try:
            delta = cv - bv
            improvement = -delta if ci['direction'] == 'minimize' else delta
        except ArithmeticError:
            result['reason'] = 'UNREPRESENTABLE_DIFFERENCE'
            return result
        if not _numeric(delta) or not _numeric(improvement):
            result['reason'] = 'UNREPRESENTABLE_DIFFERENCE'
        else:
            result.update(status='COMPARABLE', delta=delta, improvement=improvement,
                          matched_fields=list(MATCH_FIELDS))
    return result


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def summarize_failures(raw_text, identity, limit=16):
    """Summarize the existing development result format without inventing causes."""
    _require(_count(limit) and limit <= 64, 'Inventory display limit must be 0..64')
    identity = _identity(identity)
    document = _document(raw_text)
    _require(isinstance(document, dict), 'Failure report must be an object')
    result = {'operation': 'summarize_failures', 'status': 'UNKNOWN', 'identity': identity,
              'case_count': None, 'failure_count': None, 'skipped_count': None,
              'unknown_count': None, 'observed_failure_records': None, 'observed_skipped_records': None,
              'entries': [], 'total_records': None, 'omitted_records': None, 'reasons': [],
              'identity_assurance': 'CALLER_METADATA', 'scientific_support': 'UNKNOWN'}
    if _missing(identity, SOURCE_FIELDS) or _conflicts(document, identity):
        result['reasons'].append('INCOMPLETE_OR_CONFLICTING_IDENTITY')
    if not all(_count(document.get(key)) for key in ('case_count', 'failure_count', 'skipped_count')):
        result['reasons'].append('MISSING_OR_INVALID_COUNTS')
    if not isinstance(document.get('failures'), list) or not isinstance(document.get('skipped'), list):
        result['reasons'].append('MISSING_OR_INVALID_INVENTORY')
        return result
    failures, skipped = document['failures'], document['skipped']
    result.update(observed_failure_records=len(failures), observed_skipped_records=len(skipped),
                  total_records=len(failures) + len(skipped), unknown_count=0)
    seen = set()
    for kind, rows, field in (('failures', failures, 'trace'), ('skipped', skipped, 'reason')):
        for index, row in enumerate(rows):
            valid = (isinstance(row, dict) and set(row) == {'case_id', field}
                     and isinstance(row.get('case_id'), str) and bool(row['case_id'].strip())
                     and _utf8_size(row['case_id']) <= 1024 and isinstance(row.get(field), str))
            duplicate = valid and row['case_id'] in seen
            if not valid or duplicate:
                result['unknown_count'] += 1
                result['reasons'].append('DUPLICATE_CASE_ID' if duplicate else 'INVALID_INVENTORY_RECORD')
            if valid:
                seen.add(row['case_id'])
            if len(result['entries']) < limit:
                size = _utf8_size(row[field]) if valid else None
                result['entries'].append({'kind': kind, 'case_id': row['case_id'] if valid else None,
                    'pointer': '/' + kind + '/' + str(index), 'field': field,
                    'text': row[field] if valid and size <= 2048 else None,
                    'text_bytes': size, 'text_omitted': valid and size > 2048,
                    'status': 'UNKNOWN' if not valid or duplicate else 'OBSERVED'})
    result['omitted_records'] = result['total_records'] - len(result['entries'])
    counts_valid = all(_count(document.get(key)) for key in ('case_count', 'failure_count', 'skipped_count'))
    if counts_valid:
        if (document['failure_count'] != len(failures) or document['skipped_count'] != len(skipped)
                or document['case_count'] < len(failures) + len(skipped)):
            result['reasons'].append('COUNT_INVENTORY_CONFLICT')
        else:
            result.update(case_count=document['case_count'], failure_count=document['failure_count'],
                          skipped_count=document['skipped_count'])
    result['reasons'] = sorted(set(result['reasons']))
    if not result['reasons']:
        result['status'] = 'OBSERVED'
    return result

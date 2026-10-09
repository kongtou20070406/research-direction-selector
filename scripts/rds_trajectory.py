"""Read-only accounting of explicitly supplied traces; never execution authority.

Source hashes bind bytes, not honesty or scientific validity. No host discovery,
provider calls, hidden reasoning, prices, tokenizer estimates or new ledger.
"""
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path

from rds_artifacts import _pointer, _read, strict_json
from rds_costs import finite, sha256, summarize_costs
from rds_verify_types import digest, require

SCHEMA = 'rds-trajectory-manifest-v1'
PHASES = {'generation', 'retrieval', 'planning', 'mechanical', 'execution',
          'verification', 'recovery', 'rework', 'UNKNOWN'}
FORMATS = {'openai-responses', 'openai-chat', 'anthropic-message'}
MAX_RECORDS = 1024
TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'cached_input_tokens',
                'cache_write_input_tokens', 'reasoning_tokens')


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 512 and bool(value.strip()) and value.upper() != 'UNKNOWN'


def _get(record, pointer):
    try:
        return _pointer(record, pointer)
    except (KeyError, IndexError, TypeError):
        return None


def _number(value, *, integer=False):
    return value if (type(value) in (int, float) and 0 <= value <= 2**63 - 1 and
                     finite(value) and (not integer or type(value) is int)) else None


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return instant.timestamp() if instant.utcoffset() is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _total(rows, field):
    values = [row.get(field) for row in rows]
    observed = sum(value for value in values if value is not None)
    complete = bool(rows) and all(value is not None for value in values) and finite(observed)
    return {'value': observed if complete else None,
            'observed_subtotal': observed if finite(observed) else None,
            'known_records': sum(value is not None for value in values),
            'unknown_records': sum(value is None for value in values),
            'status': 'SOURCE_RECORDED' if complete else 'UNKNOWN'}


def _provider(record, spec):
    fmt = spec['format']
    require(fmt in FORMATS, 'unsupported provider format')
    provider = 'anthropic' if fmt == 'anthropic-message' else 'openai'
    rid = record.get('id')
    require(_text(rid), 'provider response id missing; cannot deduplicate paid attempt')
    usage = record.get('usage')
    usage = usage if isinstance(usage, dict) else {}
    if fmt == 'openai-chat':
        paths = ('/prompt_tokens', '/completion_tokens', '/prompt_tokens_details/cached_tokens',
                 '/prompt_tokens_details/cache_write_tokens', '/completion_tokens_details/reasoning_tokens')
    elif fmt == 'openai-responses':
        paths = ('/input_tokens', '/output_tokens', '/input_tokens_details/cached_tokens',
                 '/input_tokens_details/cache_write_tokens', '/output_tokens_details/reasoning_tokens')
    else:
        paths = ('/input_tokens', '/output_tokens', '/cache_read_input_tokens',
                 '/cache_creation_input_tokens', '/reasoning_tokens')
    tokens = {key: _number(_get(usage, path), integer=True) for key, path in zip(TOKEN_FIELDS, paths)}
    problems = []
    for key, path in zip(TOKEN_FIELDS, paths):
        if _get(usage, path) is not None and tokens[key] is None:
            problems.append('invalid usage field: ' + path)
    if provider == 'anthropic':
        # Anthropic input_tokens excludes both cache categories. Unknown is not zero.
        parts = [tokens[key] for key in ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens')]
        tokens['uncached_input_tokens'] = tokens['input_tokens']
        tokens['input_tokens'] = sum(parts) if all(v is not None for v in parts) else None
        # The supported Messages usage schema has no separate reasoning counter.
        tokens['reasoning_tokens'] = None
    else:
        for key in ('cached_input_tokens', 'cache_write_input_tokens'):
            if tokens[key] is not None and tokens['input_tokens'] is not None and tokens[key] > tokens['input_tokens']:
                problems.append(key + ' exceeds input_tokens')
        if tokens['reasoning_tokens'] is not None and tokens['output_tokens'] is not None and tokens['reasoning_tokens'] > tokens['output_tokens']:
            problems.append('reasoning_tokens exceeds output_tokens')
    tokens['total_tokens'] = (tokens['input_tokens'] + tokens['output_tokens']
                              if tokens['input_tokens'] is not None and tokens['output_tokens'] is not None else None)
    if 'total_tokens' in usage:
        declared = _number(usage['total_tokens'], integer=True)
        if declared is None or tokens['total_tokens'] is not None and declared != tokens['total_tokens']:
            problems.append('inconsistent total_tokens')
    # Reject the entire conflicting usage, not merely its inconvenient breakdown.
    if problems:
        tokens = {key: None for key in tokens}
    fields = spec.get('fields', {})
    require(isinstance(fields, dict) and set(fields) <= {'host', 'effort', 'tokenizer', 'latency_seconds'},
            'unsupported provider metadata selector')
    metadata = {key: _get(record, value) for key, value in fields.items()}
    latency = _number(metadata.pop('latency_seconds', None))
    result = {'key': [provider, spec['namespace'], rid], 'provider': provider, 'attempt_id': rid,
              'model': record.get('model') if _text(record.get('model')) else None,
              'phase': spec.get('phase', 'UNKNOWN'), 'phase_assurance': 'DECLARED_ATTRIBUTION',
              'status': record.get('status', record.get('stop_reason')),
              'latency_seconds': latency, 'usage_problems': problems, **tokens,
              **{key: metadata.get(key) if _text(metadata.get(key)) else None for key in ('host', 'effort', 'tokenizer')},
              'usage_locators': {key: '/usage' + path for key, path in zip(TOKEN_FIELDS, paths)
                                 if provider != 'anthropic' or key != 'reasoning_tokens'}}
    return result


def _selected(record, spec, allowed):
    fields = spec.get('fields', {})
    require(isinstance(fields, dict) and set(fields) <= set(allowed), 'unsupported field selector')
    return {key: _get(record, fields[key]) if key in fields else None for key in allowed}


def _tool(record, spec):
    row = _selected(record, spec, ('call_id', 'round_trip_id', 'origin', 'status', 'schema_tokens',
                                  'returned_tokens', 'tokenizer', 'format_error', 'retry', 'regenerated_code'))
    require(_text(row['call_id']), 'tool call id missing')
    require(row['origin'] in {'llm', 'internal'}, 'tool origin must be source-recorded llm or internal')
    row['key'] = [spec['namespace'], row['call_id']]
    row['namespace'] = spec['namespace']
    row['phase'] = spec.get('phase', 'UNKNOWN')
    row['phase_assurance'] = 'DECLARED_ATTRIBUTION'
    for key in ('schema_tokens', 'returned_tokens'):
        row[key] = _number(row[key], integer=True) if _text(row['tokenizer']) else None
    for key in ('format_error', 'retry', 'regenerated_code'):
        row[key] = int(row[key]) if type(row[key]) is bool else None
    if not _text(row['round_trip_id']):
        row['round_trip_id'] = None
    return row


def _outcome(record, spec, goal):
    row = _selected(record, spec, ('goal_id', 'evaluator_sha256', 'protocol_sha256', 'artifact_sha256',
                                  'status', 'observed_at', 'score', 'delivered', 'incorrect_claims', 'human_rescues'))
    for key in ('evaluator_sha256', 'protocol_sha256', 'artifact_sha256'):
        require(sha256(row[key]), 'outcome needs valid ' + key)
        row[key] = row[key].lower()
    require(all(row[key] == goal[key] for key in ('goal_id', 'evaluator_sha256', 'protocol_sha256')),
            'outcome goal/evaluator/protocol differs from declared evaluation')
    require(row['status'] in {'PASS', 'FAIL', 'UNKNOWN'}, 'outcome status must be PASS, FAIL or UNKNOWN')
    row['key'] = [row[key] for key in ('goal_id', 'evaluator_sha256', 'protocol_sha256', 'artifact_sha256')]
    row['assurance'] = 'EVALUATOR_REPORTED_NOT_INDEPENDENTLY_VERIFIED'
    for key in ('incorrect_claims', 'human_rescues'):
        row[key] = _number(row[key], integer=True)
    row['delivered'] = row['delivered'] if type(row['delivered']) is bool else None
    return row


def _deduplicate(rows, conflicts, kind):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row['key'])].append(row)
    kept, duplicates = [], 0
    for key, items in groups.items():
        fingerprints = {digest({k: v for k, v in row.items() if k != 'source'}) for row in items}
        if len(fingerprints) != 1:
            conflicts.append({'kind': kind, 'key': list(key), 'reason': 'conflicting records for one identity',
                              'sources': [row['source'] for row in items]})
            continue
        row = dict(items[0])
        row['sources'] = [item['source'] for item in items]
        kept.append(row)
        duplicates += len(items) - 1
    return sorted(kept, key=lambda row: row['key']), duplicates


def _report(root, manifest_path):
    """Export a supplied-inventory report, without mutating any research state."""
    root = Path(root).resolve()
    raw = _read(str(manifest_path), root)
    manifest = strict_json(raw.decode('utf-8-sig'))
    require(isinstance(manifest, dict) and manifest.get('schema') == SCHEMA, 'unsupported trajectory manifest')
    require(set(manifest) <= {'schema', 'goal', 'sources', 'providers', 'tools', 'outcomes', 'receipts', 'coverage'},
            'unsupported trajectory manifest field')
    goal = manifest.get('goal')
    require(isinstance(goal, dict) and _text(goal.get('goal_id')) and
            all(sha256(goal.get(key)) for key in ('evaluator_sha256', 'protocol_sha256')), 'goal and evaluation identities required')
    require(set(goal) <= {'goal_id', 'evaluator_sha256', 'protocol_sha256', 'started_at'}, 'unsupported goal field')
    goal = {**goal, **{key: goal[key].lower() for key in ('evaluator_sha256', 'protocol_sha256')}}
    sources = manifest.get('sources', [])
    require(isinstance(sources, list) and len(sources) <= 64, 'source limit exceeded')
    inventory, documents, cache, errors, conflicts, receipt_sources = [], {}, {}, [], [], []
    source_ids = set()
    for source in sources:
        require(isinstance(source, dict) and set(source) == {'id', 'path', 'sha256'} and
                _text(source['id']) and isinstance(source['path'], str) and len(source['path']) <= 4096 and
                sha256(source['sha256']), 'invalid source reference')
        require(source['id'] not in source_ids, 'duplicate source id')
        source_ids.add(source['id'])
        item = {**source, 'status': 'UNKNOWN'}
        inventory.append(item)
        try:
            name = (root / source['path']).resolve()
            name.relative_to(root)
            if name not in cache:
                data = _read(str(name), root)
                cache[name] = (digest(data), strict_json(data.decode('utf-8-sig')))
            actual, document = cache[name]
            require(actual == source['sha256'].lower(), 'source hash mismatch')
            documents[source['id']] = document
            item['status'] = 'READ'
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
            item['reason'] = str(exc)
            errors.append({'source': source['id'], 'reason': str(exc)})
    groups = {kind: [] for kind in ('providers', 'tools', 'outcomes', 'receipts')}
    total = 0
    for kind in groups:
        specs = manifest.get(kind, [])
        require(isinstance(specs, list), kind + ' must be a list')
        total += len(specs)
        require(total <= MAX_RECORDS, 'record limit exceeded')
        for index, spec in enumerate(specs):
            require(isinstance(spec, dict) and _text(spec.get('source')) and
                    isinstance(spec.get('pointer'), str) and len(spec['pointer']) <= 4096,
                    'record needs bounded source and JSON pointer')
            require(spec['source'] in source_ids, 'unknown source reference')
            require(set(spec) <= {'source', 'pointer', 'namespace', 'phase', 'format', 'fields'}, 'unsupported record field')
            if kind in {'providers', 'tools'}:
                require(_text(spec.get('namespace')), 'provider/tool namespace required')
                require(spec.get('phase', 'UNKNOWN') in PHASES, 'unsupported phase')
            source = next(item for item in sources if item['id'] == spec['source'])
            locator = {**source, 'pointer': spec['pointer'], 'fields': spec.get('fields', {})}
            if spec['source'] not in documents:
                errors.append({'kind': kind, 'index': index, 'source': locator, 'reason': 'source unavailable'})
                continue
            try:
                record = _pointer(documents[spec['source']], spec['pointer'])
                require(isinstance(record, dict), 'selected record must be an object')
                if kind == 'providers':
                    row = _provider(record, spec)
                elif kind == 'tools':
                    row = _tool(record, spec)
                elif kind == 'outcomes':
                    row = _outcome(record, spec, goal)
                else:
                    groups[kind].append(record)
                    receipt_sources.append(locator)
                    continue
                row['source'] = locator
                require(len(json.dumps(row, ensure_ascii=False).encode('utf-8')) <= 32768,
                        'selected report record exceeds 32 KiB; use narrower original fields')
                groups[kind].append(row)
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                errors.append({'kind': kind, 'index': index, 'source': locator, 'reason': str(exc)})
    duplicates = {}
    for kind in ('providers', 'tools', 'outcomes'):
        groups[kind], duplicates[kind] = _deduplicate(groups[kind], conflicts, kind)
    providers, tools, outcomes = (groups[kind] for kind in ('providers', 'tools', 'outcomes'))
    tokens = {field: _total(providers, field) for field in TOKEN_FIELDS + ('total_tokens', 'latency_seconds')}
    # Invalid/unknown identity cannot disappear into a seemingly complete total.
    for field in tokens.values():
        if any(e.get('kind') == 'providers' for e in errors) or any(c['kind'] == 'providers' for c in conflicts):
            field.update(value=None, status='UNKNOWN')
    llm = [row for row in tools if row['origin'] == 'llm']
    round_trips = {(row['namespace'], row['round_trip_id']) for row in llm if row['round_trip_id'] is not None}
    tool_incomplete = any(row['round_trip_id'] is None for row in llm) or any(
        e.get('kind') == 'tools' for e in errors) or any(c['kind'] == 'tools' for c in conflicts)
    phases = {phase: {'provider_attempts': len(rows), 'tokens': {key: _total(rows, key) for key in TOKEN_FIELDS + ('total_tokens',)}}
              for phase in sorted({row['phase'] for row in providers})
              for rows in [[row for row in providers if row['phase'] == phase]]}
    if any(e.get('kind') == 'providers' for e in errors) or any(c['kind'] == 'providers' for c in conflicts):
        for phase in phases.values():
            for field in phase['tokens'].values():
                field.update(value=None, status='UNKNOWN')
    tool_totals = {key: _total(tools, key) for key in ('schema_tokens', 'returned_tokens', 'format_error', 'retry', 'regenerated_code')}
    if any(e.get('kind') == 'tools' for e in errors) or any(c['kind'] == 'tools' for c in conflicts):
        for field in tool_totals.values():
            field.update(value=None, status='UNKNOWN')
    accepted = [row for row in outcomes if row['status'] == 'PASS']
    outcome_incomplete = (any(e.get('kind') == 'outcomes' for e in errors)
                          or any(c['kind'] == 'outcomes' for c in conflicts))
    started = _timestamp(goal.get('started_at'))
    times = [_timestamp(row['observed_at']) for row in accepted]
    first = min(times) - started if started is not None and times and all(t is not None and t >= started for t in times) else None
    if outcome_incomplete:
        first = None
    tokens_per_pass = (tokens['total_tokens']['value'] / len(accepted)
                       if accepted and not outcome_incomplete and tokens['total_tokens']['value'] is not None else None)
    outcome_totals = {key: _total(outcomes, key) for key in ('incorrect_claims', 'human_rescues')}
    if outcome_incomplete:
        for field in outcome_totals.values():
            field.update(value=None, status='UNKNOWN')
    costs = summarize_costs(groups['receipts'])
    # Inventory errors affect only the kinds that actually select that source.
    # Unread receipt selectors remain missing cost, even beside valid receipts.
    receipt_errors = [error for error in errors if error.get('kind') == 'receipts']
    if receipt_errors:
        costs['missing'].extend(receipt_errors)
        if costs['status'] != 'CONFLICT':
            costs['status'] = 'PARTIAL'
    return {'schema': 'rds-trajectory-report-v1', 'manifest_sha256': digest(raw),
            'status': 'CONFLICT' if conflicts or costs['status'] == 'CONFLICT' else 'PARTIAL',
            'coverage': {'scope': 'SUPPLIED_RECORDS_ONLY', 'declared': manifest.get('coverage'),
                         'complete_research_cost': 'UNKNOWN', 'unlisted_records': 'UNKNOWN'},
            'goal': goal, 'source_inventory': inventory, 'errors': errors, 'conflicts': conflicts,
            'duplicate_records': duplicates, 'providers': providers, 'provider_usage': tokens,
            'provider_attempts_observed': len(providers), 'phases': phases, 'tools': tools,
            'tool_burden': {'llm_tool_calls_observed': len(llm),
                            'llm_round_trips': len(round_trips) if not tool_incomplete and llm else None,
                            'round_trips_observed_subtotal': len(round_trips),
                            'internal_steps_observed': len(tools) - len(llm),
                            **tool_totals},
            'outcomes': outcomes, 'outcome_summary': {'evaluator_reported_pass_artifacts': len(accepted),
                'evaluator_reported_fail_artifacts': sum(row['status'] == 'FAIL' for row in outcomes),
                'unknown_artifacts': sum(row['status'] == 'UNKNOWN' for row in outcomes),
                'seconds_to_first_reported_pass': first, 'independently_verified_results': None,
                'supplied_tokens_per_reported_pass': tokens_per_pass,
                **outcome_totals},
            'receipt_costs': costs, 'receipt_sources': receipt_sources,
            'limits': ['Source-recorded usage is not FLOPs or a provider bill.',
                       'Cache and reasoning breakdowns are not added to total_tokens.',
                       'Provider latency sums can overlap; they are not trajectory wall time.',
                       'Receipt resources and provider usage may overlap and are never added together.',
                       'Phases and coverage are declarations; omitted overhead remains unknown.',
                       'Evaluation records and hashes do not independently certify scientific outcomes.']}


def report(root, manifest_path):
    result = _report(root, manifest_path)
    require(len(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')) <= 16 * 1024 * 1024,
            'trajectory report exceeds 16 MiB; split the declared inventory and retain its scope')
    return result

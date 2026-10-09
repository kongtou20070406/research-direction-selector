#!/usr/bin/env python3
"""Export a read-only, hierarchical explorer for a complete research archive.

This display-only route deliberately does not run TMS analysis or write a ledger.
There is no fixed node/edge/input-size cutoff; available memory is the boundary.
"""
import argparse
import base64
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path

ASSETS = Path(__file__).with_name('archive_view')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def load_archive(path):
    raw = Path(path).read_bytes()
    if raw.startswith(b'\x1f\x8b'):
        raw = gzip.decompress(raw)
    def nonfinite(value):
        raise ValueError('Non-finite JSON number: ' + value)
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                      parse_constant=nonfinite)


def prepare_archive(value):
    """Validate identities and retain every original record and parallel edge."""
    if not isinstance(value, dict):
        raise ValueError('Archive must be an object')
    nodes, edges = value.get('nodes'), value.get('edges')
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError('nodes and edges must be lists')
    node_ids = set()
    edge_ids = set()
    for kind, rows, seen in [('node', nodes, node_ids), ('edge', edges, edge_ids)]:
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
                raise ValueError('Every ' + kind + ' needs a non-empty string id')
            if row['id'] in seen:
                raise ValueError('Duplicate ' + kind + ' id: ' + row['id'])
            seen.add(row['id'])
    for edge in edges:
        if edge.get('source') not in node_ids or edge.get('target') not in node_ids:
            raise ValueError('Missing edge endpoint: ' + edge['id'])
    groups = value.get('groups', [])
    if not isinstance(groups, list):
        raise ValueError('groups must be a list')
    group_ids = set()
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get('id'), str) or not group['id']:
            raise ValueError('Every group needs a non-empty string id')
        if group['id'] in group_ids:
            raise ValueError('Duplicate group id: ' + group['id'])
        group_ids.add(group['id'])
    fallback = 'display:ungrouped'
    while fallback in group_ids:
        fallback += ':other'
    groups = [dict(group) for group in groups]
    assignments = []
    for node in nodes:
        gid = node.get('group')
        if gid is not None and gid not in group_ids:
            raise ValueError('Unknown group on node: ' + node['id'])
        assignments.append(gid if gid is not None else fallback)
    if fallback in assignments:
        groups.append({'id': fallback, 'label': '未分组档案',
                       'summary': '没有声明研究分组的原始记录；归类不推断科学关系。'})
    counts = Counter(assignments)
    # Group membership is a display index, never an added source edge.
    result = dict(value)
    result['groups'] = [dict(g, node_count=counts[g['id']]) for g in groups]
    result['display_groups'] = assignments
    result['counts'] = {'nodes': len(nodes), 'edges': len(edges), 'groups': len(groups)}
    result['display_limits'] = {'nodes': None, 'edges': None, 'input_bytes': None}
    result['assurance'] = 'READ_ONLY_ARCHIVE; NO_INFERENCE; GROUPING_IS_NOT_SCIENTIFIC_SUPPORT'
    return result


def render_archive_html(value):
    prepared = prepare_archive(value)
    raw = json.dumps(prepared, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    packed = base64.b64encode(gzip.compress(raw, mtime=0)).decode('ascii')
    page = (ASSETS / 'index.html').read_text(encoding='utf-8')
    for marker, path in [('/*ARCHIVE_CSS*/', 'style.css'), ('/*ARCHIVE_JS*/', 'explorer.js')]:
        text = (ASSETS / path).read_text(encoding='utf-8').replace('</script', '<\\/script')
        page = page.replace(marker, text)
    return page.replace('ARCHIVE_PAYLOAD', packed), prepared['counts']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, help='JSON or gzip JSON with nodes, edges and optional groups')
    parser.add_argument('--output', required=True, help='Standalone HTML outside the .rds ledger')
    args = parser.parse_args(argv)
    source, target = Path(args.archive).resolve(), Path(args.output).resolve()
    try:
        if target.suffix.lower() != '.html' or target == source or any(p.casefold() == '.rds' for p in target.parts):
            raise ValueError('Output must be a separate .html file outside .rds')
        page, counts = render_archive_html(load_archive(source))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page, encoding='utf-8')
    except (OSError, ValueError, TypeError, MemoryError, EOFError) as error:
        parser.error(str(error) or type(error).__name__)
    print(json.dumps({'status': 'AVAILABLE', 'output': str(target), 'counts': counts,
                      'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                      'analysis': 'NOT_RUN_DISPLAY_ONLY', 'ledger_modified': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

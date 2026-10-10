"""Fixed-campaign stdio MCP entry. The host, not this process, owns isolation.

No caller-supplied root, command, environment, policy or ledger mutation API.
Native ProjectStore owns reservations, admission, attempts, costs and receipts.
"""
import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys
import threading

import rds_campaign as campaign
from rds_owned_advisor import review
from rds_project import ProjectStore, digest, require
from rds_source_documents import strict_json

PROTOCOL = '2025-11-25'
MAX_INPUT = 65536
MAX_OUTPUT = 4 * 1024 * 1024
NAMES = ('rds_status', 'rds_next', 'rds_execute_selected', 'rds_recover')
IDENTITY_FIELDS = {'expected_run_id', 'expected_manifest_sha256'}


def identity(arguments):
    require(isinstance(arguments, dict) and set(arguments) == IDENTITY_FIELDS,
            'Exactly expected_run_id and expected_manifest_sha256 are required')
    run_id, sha = arguments['expected_run_id'], arguments['expected_manifest_sha256']
    require(isinstance(run_id, str) and 1 <= len(run_id) <= 80
            and all(c.isalnum() or c in '-_' for c in run_id), 'Invalid expected run ID')
    require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{64}', sha), 'Invalid manifest SHA256')
    return run_id, sha


def advice_summary(report):
    # Full native reports remain in the original CAS and review events.
    keys = ('status', 'selected_run', 'selection_basis', 'fingerprint',
            'snapshot_sha256', 'analysis_coverage', 'next_move', 'warnings')
    return {key: report[key] for key in keys if key in report}


class Broker:
    def __init__(self, root, binding_path):
        require(Path(root).is_absolute() and Path(binding_path).is_absolute(),
                'Operator root and binding pointer must be absolute')
        self.root = Path(root).resolve()
        # Retain the operator's pointer path so later removal/retargeting is checked.
        self.binding_path = str(Path(binding_path).absolute())
        self.store = ProjectStore(self.root)
        self.lock = threading.RLock()
        self.pinned = self._binding()
        self._check()

    def _binding(self):
        # Reuse native verification of marker, genesis and nonce event. The
        # explicit pointer is mandatory even if ancestor discovery finds none.
        value = campaign._resolve(self.root, self.binding_path)
        inherited = campaign.binding(self.root)
        require(value is not None, 'Broker requires an existing campaign binding')
        pinned = {key: value[key] for key in campaign.FIELDS}
        require(inherited is None or pinned == {key: inherited[key] for key in campaign.FIELDS},
                'Inherited campaign pointer conflicts with operator binding')
        require(self.root == Path(value['project_root']).resolve(),
                'Broker root must be the original campaign project')
        return pinned

    def _check(self):
        require(self._binding() == self.pinned, 'Campaign identity changed; operator intervention required')
        state = self.store.snapshot()
        require('advisor_policy' in state['contract'], 'Broker requires a program-owned Advisor policy')
        return state

    def _guard(self, db, run):
        # Runs under the native admission transaction, after native selection and
        # input checks. Never opens another write connection to this database.
        require(self._binding() == self.pinned, 'Campaign identity changed before admission')

    def _result(self, result):
        value = {'result': result, 'execution_scope': 'FIXED_CAMPAIGN_BROKER_ONLY'}
        report = getattr(self.store, 'last_advisor_review', None)
        if report is not None:
            value['advisor'] = advice_summary(report)
        return value

    def call(self, name, arguments):
        require(name in NAMES, 'Unknown broker tool')
        if name in NAMES[:2]:
            require(isinstance(arguments, dict) and not arguments, 'This tool takes no arguments')
        else:
            run_id, sha = identity(arguments)
        with self.lock:
            state = self._check()
            if name == 'rds_status':
                return {'campaign': self.pinned, 'budget': state['budget'],
                        'runs': [{key: run[key] for key in ('id', 'status', 'attempt_id', 'manifest_sha256')}
                                 for run in state['runs']],
                        'execution_scope': 'FIXED_CAMPAIGN_BROKER_ONLY',
                        'direct_shell_containment': False}
            if name == 'rds_next':
                report = review(self.store)
                spec = report.get('selected_manifest')
                return {'advisor': advice_summary(report), 'selection':
                        {'expected_run_id': spec['id'], 'expected_manifest_sha256': digest(spec)} if spec else None}
            existing = next((run for run in state['runs'] if run['id'] == run_id), None)
            if existing is not None:
                require(existing['manifest_sha256'] == sha, 'Existing run manifest differs from request')
                if name == 'rds_recover' or existing['attempt_id'] is not None:
                    # Replay is observation of this exact attempt, never a new
                    # selection. The native recovery method never reruns it.
                    return self._result(self.store.recover(run_id))
            require(name != 'rds_recover', 'Unknown run; recovery cannot register or execute it')
            report = review(self.store)
            spec = report.get('selected_manifest')
            require(report['status'] == 'REVIEWED' and spec is not None
                    and spec['id'] == run_id and digest(spec) == sha,
                    'Requested identity is not the current program-selected manifest; inspect rds_next')
            self._check()
            if existing is None:
                self.store.register(spec)
            return self._result(self.store.execute(run_id, admission_guard=self._guard))


def tool_specs():
    descriptions = (
        'Read the fixed campaign budget and run identities; no containment claim.',
        'Collect native evidence and return the program-selected run/hash, or no selection.',
        'Execute only this exact current selected run; retry observes the same attempt and never advances another run.',
        'Recover this exact existing run without rerunning it; active or uncertain attempts remain unconfirmed.')
    specs = []
    for name, description in zip(NAMES, descriptions):
        properties = {} if name in NAMES[:2] else {
            'expected_run_id': {'type': 'string', 'minLength': 1, 'maxLength': 80},
            'expected_manifest_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}}
        specs.append({'name': name, 'description': description,
                      'inputSchema': {'type': 'object', 'properties': properties,
                                      'required': list(properties), 'additionalProperties': False}})
    return specs


def error(request_id, code, message):
    return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': message}}


class Session:
    def __init__(self, broker):
        self.broker = broker
        self.initialized = False
        self.ready = False

    def handle(self, message):
        if (not isinstance(message, dict) or message.get('jsonrpc') != '2.0'
                or not isinstance(message.get('method'), str)
                or set(message) - {'jsonrpc', 'id', 'method', 'params'}):
            return error(None, -32600, 'Invalid request')
        request_id, method, params = message.get('id'), message['method'], message.get('params', {})
        if not isinstance(params, dict):
            return error(request_id, -32602, 'Params must be an object') if 'id' in message else None
        if 'id' not in message:
            if method == 'notifications/initialized' and self.initialized:
                self.ready = True
            # A notification must NEVER execute a tool, even with its name.
            return None
        if type(request_id) not in (str, int) or (isinstance(request_id, str) and len(request_id) > 256):
            return error(None, -32600, 'Invalid request ID')
        if isinstance(request_id, str):
            try:
                request_id.encode('utf-8')
            except UnicodeError:
                return error(None, -32600, 'Request ID is not valid Unicode')
        result = None
        if method == 'initialize':
            if self.initialized:
                return error(request_id, -32600, 'Already initialized')
            if not (isinstance(params.get('protocolVersion'), str)
                    and isinstance(params.get('clientInfo'), dict)
                    and isinstance(params.get('capabilities'), dict)):
                return error(request_id, -32602, 'Invalid initialization')
            self.initialized = True
            result = {'protocolVersion': PROTOCOL, 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'rds-fixed-campaign-broker', 'version': '1'},
                      'instructions': 'Only native fixed-campaign operations. Retry the same run/hash after uncertainty. Host isolation is external.'}
        elif method == 'ping':
            result = {}
        elif not self.ready:
            return error(request_id, -32600, 'Initialize session before using tools')
        elif method == 'tools/list':
            if set(params) - {'_meta'}:
                return error(request_id, -32602, 'Unsupported tools/list fields')
            result = {'tools': tool_specs()}
        elif method == 'tools/call':
            if (set(params) - {'name', 'arguments', '_meta'} or params.get('name') not in NAMES
                    or not isinstance(params.get('arguments', {}), dict)):
                return error(request_id, -32602, 'Unknown tool or unsupported fields')
            try:
                value = self.broker.call(params['name'], params.get('arguments', {}))
                result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False, allow_nan=False)}],
                          'isError': False}
            except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
                result = {'content': [{'type': 'text', 'text': 'RDS refused or could not confirm operation: ' + str(exc)}],
                          'isError': True}
        else:
            return error(request_id, -32601, 'Method not found')
        return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


def serve(broker, reader, writer):
    session = Session(broker)
    while True:
        raw = reader.readline(MAX_INPUT + 1)
        if not raw:
            return
        oversized = len(raw) > MAX_INPUT
        if oversized:
            response = error(None, -32600, 'Request exceeds 64 KiB; connection closed without dispatch')
        else:
            try:
                response = session.handle(strict_json(raw.decode('utf-8')))
            except (ValueError, UnicodeError, RecursionError, TypeError):
                response = error(None, -32700, 'Invalid strict JSON')
        if response is not None:
            # Escape any lone surrogate in native diagnostic text as well as
            # user fields; a diagnostic must not crash the response transport.
            encoded = json.dumps(response, ensure_ascii=True, allow_nan=False).encode('utf-8') + b'\n'
            if len(encoded) > MAX_OUTPUT:
                encoded = (json.dumps(error(response.get('id'), -32603,
                           'Response too large; operation may have completed. Recover the SAME run/hash; do not advance.')) + '\n').encode('utf-8')
            writer.write(encoded)
            writer.flush()
        if oversized:
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='Operator-owned original project root')
    parser.add_argument('--binding', required=True, help='Operator-owned existing campaign marker pointer')
    args = parser.parse_args()
    try:
        broker = Broker(args.root, args.binding)
        serve(broker, sys.stdin.buffer, sys.stdout.buffer)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print('RDS broker unavailable: ' + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())

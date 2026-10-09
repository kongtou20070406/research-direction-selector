"""Real stdio caller and native campaign assertions, with tiny synthetic CPU jobs."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_rds_owned_advisor as fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_campaign as campaign
from rds_host_broker import Broker, NAMES, MAX_INPUT
from rds_project import ProjectStore, digest


class Client:
    def __init__(self, root, marker, env, cwd=None, before_worker_crash=False):
        command = [sys.executable, '-B', str(ROOT / 'scripts/rds_host_broker.py')]
        if before_worker_crash:
            # Fault injection after the real native attempt transaction commits,
            # before the worker claim. The production adapter is unchanged.
            code = ("import os,sys; sys.path.insert(0,sys.argv.pop(1)); "
                    "import rds_host_broker as b; "
                    "b.ProjectStore._execute_claim=lambda *a:os._exit(33); sys.exit(b.main())")
            command = [sys.executable, '-B', '-c', code, str(ROOT / 'scripts')]
        self.process = subprocess.Popen(
            command + ['--root', str(root), '--binding', str(marker)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', env=env, cwd=cwd or ROOT)
        self.lines = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.index = 0

    def _read(self):
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + '\n')
        self.process.stdin.flush()

    def raw(self, value):
        self.process.stdin.write(value + '\n')
        self.process.stdin.flush()
        return self.receive()

    def receive(self):
        line = self.lines.get(timeout=35)
        if line is None:
            raise AssertionError('Broker closed: ' + self.process.stderr.read())
        return json.loads(line)

    def request(self, method, params=None):
        self.index += 1
        self.send({'jsonrpc': '2.0', 'id': self.index, 'method': method, 'params': params or {}})
        result = self.receive()
        if result['id'] != self.index:
            raise AssertionError(result)
        return result

    def initialize(self):
        result = self.request('initialize', {'protocolVersion': '2025-11-25',
                              'capabilities': {}, 'clientInfo': {'name': 'fixture', 'version': '1'}})
        self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        return result

    def tool(self, name, arguments=None):
        return self.request('tools/call', {'name': name, 'arguments': arguments or {}})

    def value(self, name, arguments=None):
        response = self.tool(name, arguments)
        if 'error' in response or response['result']['isError']:
            raise AssertionError(response)
        return json.loads(response['result']['content'][0]['text'])

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if not stream.closed:
                stream.close()


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.OwnedAdvisorCLITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.env = self.fixture.env
        self.fixture.initialize()
        campaign.bind(ProjectStore(self.root), self.root)
        self.marker = self.root / campaign.MARKER

    def client(self, **options):
        client = Client(self.root, self.marker, self.env, **options)
        self.addCleanup(client.close)
        client.initialize()
        return client

    def unchanged(self, before):
        after = self.fixture.snapshot()
        for key in ('runs', 'receipts', 'budget', 'exposures'):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(self.fixture.starts(), [])

    def test_real_transport_selection_execution_and_lost_response_retry(self):
        client = self.client(cwd=self.root.parent)
        self.assertEqual([t['name'] for t in client.request('tools/list')['result']['tools']], list(NAMES))
        selected = client.value('rds_next')['selection']
        self.assertEqual(selected['expected_run_id'], 'baseline')
        first = client.value('rds_execute_selected', selected)['result']
        self.fixture.assert_owned_receipt(first, 'baseline', 'SUCCEEDED')
        self.assertEqual(client.value('rds_next')['selection']['expected_run_id'], 'repair')
        before = self.fixture.snapshot()
        # New connection models a lost response / restarted client. It sends the
        # original identity, even though native selection has advanced.
        replay = self.client().value('rds_execute_selected', selected)['result']
        self.assertEqual(replay, first)
        recovered = client.value('rds_recover', selected)['result']
        self.assertEqual(recovered, first)
        after = self.fixture.snapshot()
        for key in ('runs', 'receipts', 'budget', 'exposures'):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(self.fixture.starts(), ['baseline'])
        self.assertEqual(len(after['receipts']), 1)
        self.assertGreater(after['budget']['wall_seconds']['spent_measured'], 0)

    def test_model_cannot_supply_root_commands_policy_or_environment(self):
        client = self.client()
        before = self.fixture.snapshot()
        selected = client.value('rds_next')['selection']
        for key, value in [('root', str(self.root.parent)), ('argv', ['python', '-c', 'pass']),
                           ('env', {}), ('manifest', {}), ('budget', 100000), ('background', True)]:
            with self.subTest(key=key):
                response = client.tool('rds_execute_selected', {**selected, key: value})
                self.assertTrue(response['result']['isError'])
        self.assertIn('error', client.tool('project_init'))
        self.unchanged(before)

    def test_forged_or_nonselected_identity_never_reserves_or_launches(self):
        client = self.client()
        before = self.fixture.snapshot()
        for args in ({'expected_run_id': 'repair', 'expected_manifest_sha256': digest(self.fixture.manifests['repair'])},
                     {'expected_run_id': 'baseline', 'expected_manifest_sha256': '0' * 64}):
            self.assertTrue(client.tool('rds_execute_selected', args)['result']['isError'])
        self.unchanged(before)

    def test_missing_marker_after_start_never_falls_back_to_unbound(self):
        client = self.client()
        selected = client.value('rds_next')['selection']
        before = self.fixture.snapshot()
        self.marker.unlink()
        self.assertTrue(client.tool('rds_execute_selected', selected)['result']['isError'])
        self.unchanged(before)

    def test_identity_replacement_is_refused_even_if_native_verification_is_valid(self):
        broker = Broker(self.root, self.marker)
        replaced = {**broker.pinned, 'binding_id': 'f' * 32}
        # This narrow fault injection isolates the broker's pinned identity check.
        with patch.object(broker, '_binding', return_value=replaced):
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                broker.call('rds_status', {})

    def test_conflicting_required_environment_pointer_refuses_startup(self):
        with patch.dict(os.environ, {campaign.ENVIRONMENT: str(self.root / 'missing-marker')}):
            with self.assertRaises(ValueError):
                Broker(self.root, self.marker)

    def test_real_other_campaign_marker_replacement_refuses_live_and_new_clients(self):
        client = self.client()
        selected = client.value('rds_next')['selection']
        other, unused_client = self.other_fixture()
        original = self.marker.read_bytes()
        self.marker.write_bytes((other.root / campaign.MARKER).read_bytes())
        try:
            self.assertTrue(client.tool('rds_execute_selected', selected)['result']['isError'])
            with self.assertRaises(ValueError):
                Broker(self.root, self.marker)
        finally:
            self.marker.write_bytes(original)
        self.assertEqual(self.fixture.starts(), [])
        self.assertEqual(self.fixture.snapshot()['runs'], [])

    def test_unbound_and_noncanonical_roots_refused(self):
        with self.assertRaises(ValueError):
            Broker(self.root.parent, self.marker)
        with self.assertRaises(ValueError):
            Broker(self.root, self.root / 'missing-marker')

    def test_changed_frozen_code_blocks_real_dispatch(self):
        client = self.client()
        selected = client.value('rds_next')['selection']
        before = self.fixture.snapshot()
        with (self.root / 'code.py').open('a', encoding='utf-8') as handle:
            handle.write('\n# modified after selection\n')
        self.assertTrue(client.tool('rds_execute_selected', selected)['result']['isError'])
        self.unchanged(before)

    def test_admission_rechecks_marker_after_registration(self):
        broker = Broker(self.root, self.marker)
        selected = broker.call('rds_next', {})['selection']
        original = broker.store.register
        def register_then_remove(spec):
            result = original(spec)
            self.marker.unlink()
            return result
        with patch.object(broker.store, 'register', side_effect=register_then_remove):
            with self.assertRaises(ValueError):
                broker.call('rds_execute_selected', selected)
        state = self.fixture.snapshot()
        self.assertEqual(self.fixture.starts(), [])
        self.assertIsNone(state['runs'][0]['attempt_id'])
        self.assertEqual(state['receipts'], [])
        self.assertGreater(state['budget']['wall_seconds']['reserved'], 0)

    def test_recover_unknown_or_unstarted_never_launches(self):
        client = self.client()
        selected = client.value('rds_next')['selection']
        self.assertTrue(client.tool('rds_recover', selected)['result']['isError'])
        self.fixture.create()
        result = client.value('rds_recover', selected)['result']
        self.assertIsNone(result['attempt_id'])
        self.assertIn('Unstarted', result['recovery'])
        self.assertEqual(self.fixture.starts(), [])

    def test_two_real_clients_never_launch_twice(self):
        first, second = self.client(), self.client()
        selected = first.value('rds_next')['selection']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda client: client.tool('rds_execute_selected', selected), (first, second)))
        self.assertTrue(any(not r['result']['isError'] for r in results), results)
        self.assertEqual(self.fixture.starts(), ['baseline'])
        self.assertEqual(len(self.fixture.snapshot()['receipts']), 1)
        self.assertEqual(first.value('rds_recover', selected)['result']['run_id'], 'baseline')

    def test_strict_transport_rejects_duplicate_keys_nan_batch_and_notifications(self):
        client = self.client()
        before = self.fixture.snapshot()
        for raw in ('{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}',
                    '{"id":NaN}', '[]', '{broken'):
            self.assertIn('error', client.raw(raw))
        selected = client.value('rds_next')['selection']
        client.send({'jsonrpc': '2.0', 'method': 'tools/call',
                     'params': {'name': 'rds_execute_selected', 'arguments': selected}})
        self.assertEqual(client.request('ping')['result'], {})
        self.unchanged(before)

    def test_oversized_input_closes_without_dispatch(self):
        client = self.client()
        before = self.fixture.snapshot()
        self.assertIn('error', client.raw(' ' * MAX_INPUT))
        self.assertEqual(client.process.wait(timeout=5), 0)
        self.unchanged(before)

    def test_tool_cannot_execute_before_initialization(self):
        client = Client(self.root, self.marker, self.env)
        self.addCleanup(client.close)
        self.assertIn('error', client.tool('rds_next'))
        self.assertEqual(self.fixture.starts(), [])

    def test_invalid_unicode_id_is_rejected_and_connection_remains_usable(self):
        client = self.client()
        response = client.raw('{"jsonrpc":"2.0","id":"\\ud800","method":"ping"}')
        self.assertIn('error', response)
        self.assertEqual(client.request('ping')['result'], {})

    def other_fixture(self, script=None, **kwargs):
        other = fixture.OwnedAdvisorCLITests()
        other.setUp()
        self.addCleanup(other.doCleanups)
        with patch.object(fixture, 'SCRIPT', script or fixture.SCRIPT):
            other.initialize(**kwargs)
        campaign.bind(ProjectStore(other.root), other.root)
        client = Client(other.root, other.root / campaign.MARKER, other.env)
        self.addCleanup(client.close)
        client.initialize()
        return other, client

    def waiting_fixture(self):
        wait = ('deadline = time.monotonic() + 5\n'
                '    while not pathlib.Path("outputs/release").exists() and time.monotonic() < deadline:\n'
                '        time.sleep(.01)')
        return self.other_fixture(script=fixture.SCRIPT.replace('time.sleep(1)', wait),
                                  mode='slownegative', timeout=8)

    def send_execute(self, client, selected):
        client.index += 1
        client.send({'jsonrpc': '2.0', 'id': client.index, 'method': 'tools/call',
                     'params': {'name': 'rds_execute_selected', 'arguments': selected}})

    def wait_for_launch(self, other):
        deadline = time.monotonic() + 5
        while not other.starts() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(other.starts(), ['baseline'])

    def test_second_client_during_live_attempt_only_observes_it(self):
        other, first = self.waiting_fixture()
        second = Client(other.root, other.root / campaign.MARKER, other.env)
        self.addCleanup(second.close)
        second.initialize()
        selected = first.value('rds_next')['selection']
        self.send_execute(first, selected)
        self.wait_for_launch(other)
        live = second.value('rds_execute_selected', selected)['result']
        self.assertIn('recovery', live)
        self.assertIn('active', live['recovery'])
        self.assertEqual(other.starts(), ['baseline'])
        self.assertEqual(other.snapshot()['receipts'], [])
        (other.root / 'outputs/release').touch()
        response = first.receive()
        self.assertFalse(response['result']['isError'], response)
        receipt = json.loads(response['result']['content'][0]['text'])['result']
        self.assertEqual(receipt['attempt_id'], live['attempt_id'])
        self.assertEqual(len(other.snapshot()['receipts']), 1)

    def test_killed_broker_recovers_same_attempt_without_claiming_child_stopped(self):
        other, first = self.waiting_fixture()
        selected = first.value('rds_next')['selection']
        self.send_execute(first, selected)
        self.wait_for_launch(other)
        before = other.snapshot()
        attempt = before['runs'][0]['attempt_id']
        first.process.kill()
        first.process.wait(timeout=5)
        replacement = Client(other.root, other.root / campaign.MARKER, other.env)
        self.addCleanup(replacement.close)
        replacement.initialize()
        result = replacement.value('rds_recover', selected)['result']
        self.assertEqual(result['attempt_id'], attempt)
        if 'recovery' in result:
            self.assertIn('active', result['recovery'])
        else:
            self.assertEqual(result['run_status'], 'INTERRUPTED')
        self.assertEqual(other.starts(), ['baseline'])
        after = other.snapshot()['budget']['wall_seconds']
        self.assertGreater(after['reserved'] + after['spent_measured'] + after['charged_estimate'], 0)
        # A bounded fixture on platforms without parent-death kill can finish
        # its own wait. Releasing it is not a claim about native process death.
        (other.root / 'outputs/release').touch()
        from rds_project import _alive
        deadline = time.monotonic() + 6
        while _alive(before['runs'][0]['pid']) is True and time.monotonic() < deadline:
            time.sleep(.02)

    def test_crash_after_admission_before_worker_retains_attempt_and_cost(self):
        client = self.client(before_worker_crash=True)
        selected = client.value('rds_next')['selection']
        self.send_execute(client, selected)
        self.assertEqual(client.process.wait(timeout=10), 33)
        before = self.fixture.snapshot()
        self.assertIsNotNone(before['runs'][0]['attempt_id'])
        self.assertEqual(self.fixture.starts(), [])
        replacement = self.client()
        recovered = replacement.value('rds_recover', selected)['result']
        self.assertEqual(recovered['attempt_id'], before['runs'][0]['attempt_id'])
        self.assertEqual(recovered['run_status'], 'INTERRUPTED')
        self.assertEqual(replacement.value('rds_execute_selected', selected)['result'], recovered)
        self.assertEqual(self.fixture.starts(), [])
        self.assertGreater(self.fixture.snapshot()['budget']['wall_seconds']['charged_estimate'], 0)

    def test_failed_workload_receipt_and_charge_survive_replay(self):
        other, client = self.other_fixture(mode='nonzero')
        selected = client.value('rds_next')['selection']
        receipt = client.value('rds_execute_selected', selected)['result']
        other.assert_owned_receipt(receipt, 'baseline', 'FAILED')
        self.assertEqual(receipt['exit_code'], 7)
        before = other.snapshot()
        self.assertEqual(client.value('rds_execute_selected', selected)['result'], receipt)
        self.assertEqual(other.starts(), ['baseline'])
        self.assertEqual(other.snapshot()['budget'], before['budget'])
        self.assertGreater(before['budget']['wall_seconds']['spent_measured'], 0)

    def test_insufficient_original_budget_never_launches_or_creates_an_attempt(self):
        other, client = self.other_fixture(timeout=30)
        # The frozen route asks for 30.2 seconds against the real 30 second cap.
        before = other.snapshot()
        selected = {'expected_run_id': 'baseline',
                    'expected_manifest_sha256': digest(other.manifests['baseline'])}
        self.assertTrue(client.tool('rds_execute_selected', selected)['result']['isError'])
        after = other.snapshot()
        self.assertEqual(other.starts(), [])
        for key in ('runs', 'receipts', 'budget', 'exposures'):
            self.assertEqual(after[key], before[key], key)


if __name__ == '__main__':
    unittest.main()

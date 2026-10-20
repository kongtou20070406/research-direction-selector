"""Token spend as a first-class budget resource: design §2.1 and its hard equation §5.1."""
import json
from pathlib import Path
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compass_fixture as fx
import rds_compass as compass
import rds_compass_store as cstore
from rds_compass_declaration import TOKEN_LEDGER_ENV, initialize
from rds_compass_drive import drive
from rds_project import TOKEN_RESOURCE, ProjectStore, charge_tokens

QUAD = dict(law='x*x+1', xs=range(-4, 5), hypotheses=[('lin', 'x+1'), ('dbl', '2*x+1')])
USAGE = {'input_tokens': 600, 'output_tokens': 500, 'cached_input_tokens': 200}


class TokenBudget(unittest.TestCase):
    """The ledger row, the declaration switch and the per-call charge over real campaigns."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(os.environ.pop, TOKEN_LEDGER_ENV, None)

    def declaration(self, name, *, max_tokens=None, agent=True):
        root = self.base / name
        path = fx.law_campaign(root, QUAD['law'], QUAD['xs'], hypotheses=QUAD['hypotheses'],
                               agent='reject' if agent else None, escape={'max_jumps': 2})
        value = json.loads(path.read_text(encoding='utf-8'))
        if max_tokens is not None:
            value['agent']['max_tokens'] = max_tokens
        fx.write(path, value)
        return root, path

    def campaign(self, name, *, max_tokens, usage=USAGE):
        root, path = self.declaration(name, max_tokens=max_tokens)
        fx.write(root / 'agent-mode.json', {'mode': 'reject', **({'usage': usage} if usage else {})})
        self.assertEqual(initialize(root, path)['status'], 'INITIALIZED')
        drive(root)
        return ProjectStore(root)

    def rows(self, store):
        with store._db(True) as db:
            db.execute('BEGIN')
            return {r['resource']: {'cap': r['cap'], 'spent': r['spent'], 'charged': r['charged'],
                                    'reserved': r['reserved']}
                    for r in db.execute('SELECT resource,cap,spent,charged,reserved FROM budget')}

    def dispositions(self, store):
        return [e['value'] for e in cstore.events(store) if e['kind'] == cstore.PREFIX + 'DISPOSITION']

    def test_token_spend_equals_the_sum_over_dispositions(self):
        """§5.1: budget('agent_tokens').spent == Σ over DISPOSITION events of g(record['agent']['usage'])."""
        store = self.campaign('equation', max_tokens=10 ** 6)
        row = self.rows(store)[TOKEN_RESOURCE]
        usages = [(d.get('agent') or {}).get('usage') for d in self.dispositions(store)]
        reported = [u for u in usages if u is not None]
        spent = sum(u['input_tokens'] + u['output_tokens'] for u in reported)
        unreported = len(usages) - len(reported)
        print(f'\n§5.1 budget_spent={row["spent"]} events_sum={spent} charged={row["charged"]} '
              f'reported_calls={len(reported)} unreported={unreported}')
        self.assertEqual((row['spent'], row['charged']), (spent, spent))
        self.assertEqual(unreported, 0)
        # The event side is the evidence; the row must never disagree with it.
        self.assertEqual(compass.report(store)['agent_tokens']['spent'], spent)

    def test_an_unreported_usage_writes_no_zero_charge(self):
        """A failed report stays UNKNOWN: §5.1 degrades to reported-only and must not pass as zero."""
        store = self.campaign('silent', max_tokens=10 ** 6, usage=None)
        row = self.rows(store)[TOKEN_RESOURCE]
        usages = [(d.get('agent') or {}).get('usage') for d in self.dispositions(store)]
        unreported = sum(1 for u in usages if u is None)
        print(f'\nUSAGE_UNREPORTED budget_spent={row["spent"]} calls={len(usages)} unreported={unreported}')
        self.assertGreaterEqual(unreported, 1)
        self.assertEqual((row['spent'], row['charged'], row['reserved']), (0, 0, 0))
        self.assertNotEqual(compass.report(store)['agent_tokens']['status'], 'AVAILABLE')

    def test_a_single_call_may_overrun_the_cap(self):
        """Overrunning is a legal state: the check happens before the next buy, not at settlement."""
        store = self.campaign('overrun', max_tokens=1000)
        row = self.rows(store)[TOKEN_RESOURCE]
        print(f'\noverrun cap={row["cap"]} spent={row["spent"]} charged={row["charged"]}')
        self.assertEqual((row['spent'], row['cap']), (1100, 1000))
        # ``spent`` and ``charged`` carry the same tokens (§2.1), so snapshot's
        # cap - spent - charged - reserved subtracts them twice: -1200 for an overrun of 100.
        # That display is risk #13 in the report; run settlement never sees the row, so nothing is refused.
        view = store.snapshot()['budget'][TOKEN_RESOURCE]
        self.assertEqual(view['remaining'], view['cap'] - view['spent_measured'] - view['charged_estimate'])
        self.assertLess(view['remaining'], 0)

    def test_an_uncapped_agent_is_refused_once_the_ledger_is_enabled(self):
        root, path = self.declaration('uncapped')
        os.environ[TOKEN_LEDGER_ENV] = '1'
        with self.assertRaisesRegex(ValueError, 'agent.max_tokens is required'):
            initialize(root, path)
        self.assertFalse(ProjectStore(root).path.exists())

    def test_an_uncapped_agent_keeps_the_old_behaviour_with_the_ledger_off(self):
        """§5.3 reverse test: the switch off leaves the dimension-less campaign exactly as before."""
        root, path = self.declaration('compat')
        os.environ[TOKEN_LEDGER_ENV] = '0'
        fx.write(root / 'agent-mode.json', {'mode': 'reject', 'usage': {'input_tokens': 7, 'output_tokens': 3}})
        self.assertEqual(initialize(root, path)['status'], 'INITIALIZED')
        drive(root)
        store = ProjectStore(root)
        calls = len((root / 'agent-calls.jsonl').read_text(encoding='utf-8').splitlines())
        self.assertEqual(sorted(self.rows(store)), ['wall_seconds'])
        tokens = compass.report(store)['agent_tokens']
        self.assertEqual((tokens['spent'], tokens['calls'], tokens['status']),
                         (10 * calls, calls, 'CAP_UNKNOWN'))

    def test_a_contract_without_an_agent_declares_no_token_dimension(self):
        root, path = self.declaration('auto', agent=False)
        self.assertEqual(initialize(root, path)['status'], 'INITIALIZED')
        self.assertEqual(sorted(self.rows(ProjectStore(root))), ['wall_seconds'])

    def test_charge_tokens_needs_the_recording_transaction_and_refuses_negative_amounts(self):
        root, path = self.declaration('unit', max_tokens=100)
        self.assertEqual(initialize(root, path)['status'], 'INITIALIZED')
        store = ProjectStore(root)
        with self.assertRaisesRegex(ValueError, 'transaction that records the disposition'):
            with store._db(True) as db:
                charge_tokens(db, jump_id='j01', usage=USAGE)
        with store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'non-negative'):
                charge_tokens(db, jump_id='j01', usage=USAGE, charge=lambda usage: -1)
            self.assertEqual(charge_tokens(db, jump_id='j01', usage=USAGE), 1100)
        self.assertEqual(self.rows(store)[TOKEN_RESOURCE]['spent'], 1100)
        # A contract that never declared the row fails closed instead of inventing one.
        other, other_path = self.declaration('unledgered', agent=False)
        self.assertEqual(initialize(other, other_path)['status'], 'INITIALIZED')
        with ProjectStore(other)._db() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'never declared agent_tokens'):
                charge_tokens(db, jump_id='j01', usage=USAGE)

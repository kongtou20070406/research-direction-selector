"""Base tool catalogue and tool offers: every candidate tool still qualifies only through the oracle triple."""
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compass_fixture as fx
import rds_compass as compass
import rds_compass_rsi as rsi
import rds_compass_store as cstore
from rds_compass_cli import command
from rds_compass_declaration import initialize
from rds_compass_drive import drive

# Twice the sum of squared decimal digits, plus one: no base tool and no add/mul law reproduces it.
LAW = '2*((x % 10) ** 2 + (x // 10) ** 2)+1'
GRAMMAR = {'variables': ['x'], 'constants': [1, 2], 'operators': ['add', 'mul']}
# Popcount again under the catalogue's name, with different code and so a different hash.
TWIN = ('def digitsum2(n):\n    n = abs(int(n))\n    acc = 0\n    while n > 0:\n'
        '        acc = acc + n % 2\n        n = n // 2\n    return acc\n')
SQUARES = ('def digitsq10(n):\n    n = abs(int(n))\n    acc = 0\n    while n:\n'
           '        acc = acc + (n % 10) ** 2\n        n = n // 10\n    return acc\n')


def factors(n):
    out, p = [], 2
    while n > 1:
        while n % p == 0:
            out.append(p)
            n //= p
        p += 1
    return out


def collatz(n):
    n, steps = abs(n), 0
    while n > 1:
        n, steps = (3 * n + 1 if n % 2 else n // 2), steps + 1
    return steps


REFERENCE = {
    'totient': lambda n: sum(1 for k in range(1, abs(n) + 1) if math.gcd(k, abs(n)) == 1),
    'omega': lambda n: len(set(factors(abs(n)))),
    'bigomega': lambda n: len(factors(abs(n))),
    'mobius': lambda n: 0 if n == 0 or len(set(factors(abs(n)))) < len(factors(abs(n))) else (-1) ** len(factors(abs(n))),
    'lpf': lambda n: min(factors(abs(n)), default=abs(n)),
    'gpf': lambda n: max(factors(abs(n)), default=abs(n)),
    'rad': lambda n: math.prod(set(factors(abs(n)))) if abs(n) > 1 else abs(n),
    'digitrev10': lambda n: int(str(abs(n))[::-1]),
    'issquare': lambda n: int(n >= 0 and round(n ** 0.5) ** 2 == n),
    'primepi': lambda n: sum(1 for k in range(2, n + 1) if len(factors(k)) == 1),
    'collatz': collatz,
}


class Campaign(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()

    def campaign(self, name, **options):
        root = self.base / name
        path = fx.law_campaign(root, LAW, range(30), hypotheses=[('lin', 'x+1')], grammar=GRAMMAR, **options)
        self.assertEqual(initialize(root, path)['status'], 'INITIALIZED')
        return root

    def qualified(self, root):
        return [(t['name'], t['origin'], t['self_certified'], t['oracle']['discriminating_negative']['kind'])
                for t in compass.fold(cstore.store_for(root))['tools'].values()]


class CatalogueTests(Campaign):
    def test_base_tools_match_independent_references(self):
        tools = [dict(zip(('name', 'entry', 'source', 'sha256'), rsi.extract(s))) for s in rsi.families()]
        inputs = list(range(-3, 160))
        values, _ = rsi.evaluate_tools(tools, inputs)
        named = {t['name']: values[t['sha256']]['values'] for t in tools}
        for name, reference in REFERENCE.items():
            self.assertEqual(named[name], [str(reference(n)) for n in inputs], name)
        listed = command(SimpleNamespace(root=self.base, action='tools', catalogue=True, propose=None))['catalogue']
        self.assertEqual([t['name'] for t in listed][:3], ['digitsum2', 'digitcount2', 'nonzero2'])
        self.assertEqual(len({t['sha256'] for t in listed}), len(listed))
        self.assertTrue(all(t['status'] == 'OFFER_NOT_QUALIFIED' for t in listed))


class OfferTests(Campaign):
    def test_a_user_offer_is_recorded_unqualified_then_meets_the_oracle(self):
        root = self.campaign('user')
        fx.write(root / 'tool.py', SQUARES)
        args = SimpleNamespace(root=root, action='tools', catalogue=False, propose=str(root / 'tool.py'))
        offer = command(args)
        self.assertEqual((offer['status'], offer['origin'], offer['recorded']), ('OFFER_NOT_QUALIFIED', 'USER_PROPOSED', True))
        self.assertFalse(command(args)['recorded'])  # One record per source and origin.
        self.assertEqual(self.qualified(root), [])
        self.assertEqual(drive(root, publish=False)['status'], 'GOAL_IDENTIFIED')
        self.assertEqual(self.qualified(root), [('digitsq10', 'USER_PROPOSED', False, 'MUTANT_CONTRADICTED_BY_RECEIPT')])

    def test_offers_pass_the_same_allowlist(self):
        store = cstore.store_for(self.campaign('bad'))
        for source in ('def f(n):\n    import os\n    return 1\n', 'def broken(:\n', 'x = 1\n'):
            with self.assertRaises(ValueError):
                rsi.propose(store, source, 'USER_PROPOSED')
        with self.assertRaises(ValueError):
            rsi.propose(store, SQUARES, 'SELF_CERTIFIED')
        self.assertEqual(compass.fold(store)['proposed_tools'], {})
        # A ledger without a Compass declaration takes no offer: the first record must stay the plan.
        with patch.object(cstore, 'events', return_value=[]), self.assertRaisesRegex(ValueError, 'compass init'):
            rsi.propose(store, SQUARES, 'USER_PROPOSED')

    def test_a_second_tool_of_the_same_name_gets_its_own_feature(self):
        root = self.base / 'twin'
        self.assertEqual(initialize(root, fx.law_campaign(root, '2*popcount(x)+1', range(17)))['status'], 'INITIALIZED')
        store = cstore.store_for(root)
        for ident in ('x0', 'x1', 'x2', 'x3', 'x5', 'x6', 'x7', 'x9'):
            manifest = compass.manifest(compass.fold(store), ident)
            store.register(manifest)
            self.assertEqual(store.execute(manifest['id'])['run_status'], 'SUCCEEDED')
        state, _ = compass.ingest(store)
        self.assertEqual(rsi.escalate(store, state, {'trigger': 'TEST'})['qualified'], ['digitsum2'])
        rsi.propose(store, TWIN, 'USER_PROPOSED')
        self.assertEqual(rsi.escalate(store, compass.fold(store), {'trigger': 'TEST'})['qualified'], ['digitsum2'])
        tools = list(compass.fold(store)['tools'].values())
        features = [next(iter(t['features'])) for t in tools]
        self.assertEqual(features, ['digitsum2_x', 'digitsum2' + tools[1]['sha256'][:12] + '_x'])
        # Each name keeps its own tool's table, so env() overwrites neither.
        point = compass.env(compass.fold(store), compass.candidate(compass.fold(store), 'x7'))
        self.assertEqual((point[features[0]], point[features[1]]), (3, 3))

    def test_a_value_outside_the_exact_domain_rejects_the_tool_not_the_drive(self):
        root = self.base / 'huge'
        self.assertEqual(initialize(root, fx.law_campaign(root, '2*popcount(x)+1', range(17)))['status'], 'INITIALIZED')
        store = cstore.store_for(root)
        for ident in ('x0', 'x1', 'x2', 'x3', 'x5', 'x6', 'x7', 'x9'):
            manifest = compass.manifest(compass.fold(store), ident)
            store.register(manifest)
            self.assertEqual(store.execute(manifest['id'])['run_status'], 'SUCCEEDED')
        state, _ = compass.ingest(store)
        rsi.propose(store, 'def huge(n):\n    return 2 ** 1000 + n\n', 'USER_PROPOSED')
        report = rsi.escalate(store, compass.fold(store), {'trigger': 'TEST'})
        self.assertIn({'name': 'huge', 'reason': 'OUTSIDE_EXACT_DOMAIN'}, report['rejected'])
        self.assertEqual(report['qualified'], ['digitsum2'])  # The catalogue still closes the gap.
        rejected = [e['value'] for e in cstore.events(store) if e['kind'] == cstore.PREFIX + 'TOOL_REJECTED'
                    and e['value']['name'] == 'huge']
        self.assertEqual([r['witness'] for r in rejected], [{'candidate': 'x0'}])

    def test_an_agent_offer_is_tried_at_the_next_gap(self):
        root = self.campaign('agent', agent='adopt')
        fx.write(root / 'agent-mode.json', {'mode': 'adopt', 'tools': [SQUARES]})
        self.assertEqual(drive(root, publish=False)['status'], 'GOAL_IDENTIFIED')
        self.assertEqual(self.qualified(root), [('digitsq10', 'AGENT_PROPOSED', False, 'MUTANT_CONTRADICTED_BY_RECEIPT')])

    def test_an_invalid_agent_tool_voids_the_response(self):
        root = self.campaign('agent-bad', agent='adopt')
        fx.write(root / 'agent-mode.json', {'mode': 'adopt', 'tools': ['def f(n):\n    import os\n    return 1\n']})
        drive(root, publish=False)
        state = compass.fold(cstore.store_for(root))
        self.assertEqual(state['proposed_tools'], {})
        errors = [e for d in state['dispositions'].values() for e in d['errors']]
        self.assertTrue(errors and all(e.startswith('INVALID_RESPONSE') for e in errors), errors)


if __name__ == '__main__':
    unittest.main()

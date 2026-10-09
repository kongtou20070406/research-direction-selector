"""Independent exhaustive truth-table checks of bounded blocker enumeration."""
from copy import deepcopy
from itertools import combinations
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from rds_hypergraph import analyze_hypergraph


def graph(names, edges, goals, **limits):
    return {'schema': 1, 'nodes': [{'id': n, 'status': 'UNKNOWN', 'source': 'synthetic'} for n in names],
            'hyperedges': [{'id': str(i), 'premises': tails, 'conclusion': head,
                           'status': 'SUPPORTED', 'source': 'synthetic'}
                          for i, (tails, head) in enumerate(edges)], 'goals': goals, 'limits': limits}


def brute_force(spec, grounded=()):
    """Enumerate assumed atom subsets; only literal forward truth propagation.

    No production closure, antichain, worklist or set-product helper is used.
    """
    nodes = {n['id']: n for n in spec['nodes']}
    incoming = {e['conclusion'] for e in spec['hyperedges']}

    def blocked(row):
        ref = row.get('evidence', {}).get('receipt')
        return ref is not None and (ref['project_root'], ref['sha256']) not in grounded

    leaves = {n for n, row in nodes.items() if
              (row['status'] == 'UNKNOWN' or row['status'] == 'SUPPORTED' and blocked(row))
              and row.get('allow_direct_evidence', n not in incoming)}
    proposals = {e['id'] for e in spec['hyperedges'] if e['status'] == 'PROPOSED'
                 or e['status'] == 'SUPPORTED' and blocked(e)}
    atoms = sorted(['node:' + n for n in leaves] + ['rule:' + e for e in proposals])
    if len(atoms) > 9:
        raise ValueError('Oracle limited to nine atoms')
    answers = {goal: [] for goal in spec['goals']}
    for size in range(len(atoms) + 1):
        for items in combinations(atoms, size):
            assumed = frozenset(items)
            supported = {n for n, row in nodes.items() if row['status'] == 'SUPPORTED' and not blocked(row)}
            supported.update(n for n in leaves if 'node:' + n in assumed)
            while True:
                before = set(supported)
                for edge in spec['hyperedges']:
                    if edge['status'] == 'CONTRADICTED' or nodes[edge['conclusion']]['status'] == 'CONTRADICTED':
                        continue
                    if edge['id'] in proposals and 'rule:' + edge['id'] not in assumed:
                        continue
                    if all(tail in supported for tail in edge['premises']):
                        supported.add(edge['conclusion'])
                if before == supported:
                    break
            for goal in answers:
                if goal in supported and not any(old <= assumed for old in answers[goal]):
                    answers[goal].append(assumed)
    return {goal: [sorted(v) for v in sorted(values, key=lambda v: (len(v), sorted(v)))]
            for goal, values in answers.items()}


class BlockerEnumerationTests(unittest.TestCase):
    def assert_oracle(self, spec, grounded=(), audit=False):
        original = deepcopy(spec)

        def reader(root, digest):
            if (root, digest) not in grounded:
                return {'status': 'RECEIPT_NOT_FOUND'}
            return {'status': 'RECEIPT_FOUND', 'body': {'sha256': digest, 'run_status': 'SUCCEEDED', 'run_id': 'synthetic'}}

        result = analyze_hypergraph(spec, audit_receipts_enabled=audit, read_receipt=reader)
        self.assertFalse(result['truncated'])
        self.assertEqual({g: v['minimal_missing_evidence_sets'] for g, v in result['goals'].items()},
                         brute_force(spec, grounded if audit else ()))
        self.assertEqual(spec, original)
        return result

    def test_exhaustive_oracle_on_300_generated_cyclic_and_or_graphs(self):
        rng = random.Random(261)
        for case in range(300):
            names = [str(i) for i in range(rng.randrange(2, 6))]
            spec = graph(names, [(rng.sample(names, rng.randrange(min(len(names), 3) + 1)), rng.choice(names))
                                for _ in range(rng.randrange(7))], names,
                         max_blocker_sets=512, max_combinations=100000)
            for node in spec['nodes']:
                node['status'] = rng.choice(('UNKNOWN', 'UNKNOWN', 'SUPPORTED', 'CONTRADICTED'))
                if rng.randrange(3) == 0:
                    node['allow_direct_evidence'] = bool(rng.randrange(2))
            for edge in spec['hyperedges']:
                edge['status'] = rng.choice(('SUPPORTED', 'SUPPORTED', 'PROPOSED', 'CONTRADICTED'))
            with self.subTest(case=case):
                self.assert_oracle(spec)

    def test_reverse_unknown_chain_finishes_without_raising_work_cap(self):
        names = [str(i) for i in range(257)]
        spec = graph(names, [([names[i]], names[i + 1]) for i in range(256)], [names[-1]], max_nodes=257)
        for reverse in (False, True):
            if reverse:
                spec['hyperedges'].reverse()
            result = analyze_hypergraph(spec)
            self.assertFalse(result['truncated'])
            self.assertEqual(result['goals']['256']['minimal_missing_evidence_sets'], [['node:0']])
            self.assertLessEqual(result['combinations_examined'], 1024)
            self.assertEqual(result['limits']['max_combinations'], 50000)

    def test_late_smaller_family_replaces_same_size_and_reaches_downstream(self):
        spec = graph(['x', 'y', 'z', 'a', 'b', 'm', 'goal'], [
            (['x', 'y'], 'm'), (['m', 'z'], 'goal'), (['b'], 'm'), (['a'], 'b'), (['x'], 'a')], ['m', 'goal'])
        result = self.assert_oracle(spec)
        self.assertEqual(result['goals']['goal']['minimal_missing_evidence_sets'], [['node:x', 'node:z']])

    def test_equal_premise_families_are_idempotent_and_not_reenumerated(self):
        mids = ['m' + str(i) for i in range(8)]
        spec = graph(['a', 'b', 'goal'] + mids,
                     [([leaf], m) for m in mids for leaf in ('a', 'b')] + [(mids, 'goal')],
                     ['goal'], max_combinations=40)
        result = self.assert_oracle(spec)
        self.assertEqual(result['goals']['goal']['minimal_missing_evidence_sets'], [['node:a'], ['node:b']])

    def test_existing_head_prunes_only_supersets_of_its_evidence(self):
        leaves = ['b' + str(i) for i in range(8)]
        mids = ['m' + str(i) for i in range(8)]
        spec = graph(['a', 'goal'] + leaves + mids, [(['a'], 'goal')] +
                     [(tails, m) for b, m in zip(leaves, mids) for tails in (['a'], [b])] + [(mids, 'goal')],
                     ['goal'], max_blocker_sets=2)
        result = self.assert_oracle(spec)
        self.assertEqual(result['goals']['goal']['minimal_missing_evidence_sets'], [['node:a'], sorted('node:' + b for b in leaves)])

    def test_true_exponential_family_stays_explicitly_incomplete(self):
        leaves = ['a' + str(i) for i in range(8)] + ['b' + str(i) for i in range(8)]
        mids = ['m' + str(i) for i in range(8)]
        spec = graph(leaves + mids + ['goal', 'known'],
                     [([prefix + str(i)], mids[i]) for i in range(8) for prefix in ('a', 'b')] + [(mids, 'goal')],
                     ['goal', 'known'])
        spec['nodes'][-1]['status'] = 'SUPPORTED'
        result = analyze_hypergraph(spec)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['truncation_reason'], 'max_blocker_sets exceeded')
        self.assertFalse(result['goals']['goal']['blocker_sets_complete'])
        self.assertEqual(result['goals']['goal']['minimal_missing_evidence_sets'], [])
        self.assertEqual(result['goals']['known']['minimal_missing_evidence_sets'], [[]])
        self.assertTrue(result['goals']['known']['blocker_sets_complete'])
        spec['limits']['max_blocker_sets'] = 256
        complete = analyze_hypergraph(spec)
        expected = {frozenset('node:' + ('b' if mask & (1 << i) else 'a') + str(i) for i in range(8))
                    for mask in range(256)}
        self.assertFalse(complete['truncated'])
        self.assertEqual({frozenset(v) for v in complete['goals']['goal']['minimal_missing_evidence_sets']}, expected)

    def test_receipt_grounding_and_namespaced_atoms_match_oracle(self):
        spec = graph(['a', 'b', 'goal'], [(['a', 'b'], 'goal'), (['a'], 'b')], ['a', 'b', 'goal'])
        spec['nodes'][0].update(status='SUPPORTED', evidence={'receipt': {'project_root': 'one', 'sha256': 'a' * 64}})
        spec['hyperedges'][0].update(id='a', evidence={'receipt': {'project_root': 'two', 'sha256': 'a' * 64}})
        for grounded in ((), (('one', 'a' * 64),), (('two', 'a' * 64),),
                         (('one', 'a' * 64), ('two', 'a' * 64))):
            self.assert_oracle(spec, grounded, audit=True)
        self.assert_oracle(spec, (('one', 'a' * 64), ('two', 'a' * 64)), audit=False)


if __name__ == '__main__':
    unittest.main()

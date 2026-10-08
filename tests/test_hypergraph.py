"""AND/OR semantics, unproved-rule obligations and bounded fail-closed output."""
from copy import deepcopy
import hashlib
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rds_hypergraph import ASSURANCE, analyze_hypergraph, audit_sources
import rds_hypergraph as hypergraph


def graph(statuses, rules, goals, **limits):
    return {"schema": 1,
            "nodes": [{"id": name, "status": status, "source": "node source " + name}
                      for name, status in statuses.items()],
            "hyperedges": [{"id": name, "premises": tails, "conclusion": head,
                            "status": status, "source": "rule source " + name}
                           for name, tails, head, status in rules],
            "goals": goals, "limits": limits}


def ordered_reference(nodes, edges, grounded_receipts=frozenset()):
    """Independent pre-optimization traversal, including original witness order."""
    closure = {ident for ident, node in nodes.items() if node['status'] == 'SUPPORTED'
               and (node.get('evidence') is None
                    or (node['evidence']['receipt']['project_root'],
                        node['evidence']['receipt']['sha256']) in grounded_receipts)}
    derivations, conflicts = {}, set()
    changed = True
    while changed:
        changed = False
        for edge in edges:
            if edge['status'] != 'SUPPORTED' or not set(edge['premises']) <= closure:
                continue
            head = edge['conclusion']
            if nodes[head]['status'] == 'CONTRADICTED':
                conflicts.add(edge['id'])
            elif head not in closure:
                closure.add(head)
                derivations[head] = edge['id']
                changed = True
    receipt_block = {ident: 'receipt not grounded' for ident, node in nodes.items()
                     if node['status'] == 'SUPPORTED' and node.get('evidence') is not None
                     and (node['evidence']['receipt']['project_root'],
                          node['evidence']['receipt']['sha256']) not in grounded_receipts}
    return closure, derivations, conflicts, receipt_block


def relevance_reference(edges, goals):
    """Original repeated reverse scans; proposed and contradicted heads included."""
    relevant_nodes, relevant_edges = set(goals), set()
    changed = True
    while changed:
        changed = False
        for edge in edges:
            if edge['status'] == 'CONTRADICTED' or edge['conclusion'] not in relevant_nodes:
                continue
            relevant_edges.add(edge['id'])
            before = len(relevant_nodes)
            relevant_nodes.update(edge['premises'])
            changed |= len(relevant_nodes) != before
    return relevant_nodes, relevant_edges


class HypergraphTests(unittest.TestCase):
    def record_fixture(self):
        spec = graph({'opaque-a': 'SUPPORTED', 'opaque-b': 'SUPPORTED', 'opaque-c': 'SUPPORTED',
                      'opaque-d': 'UNKNOWN', 'opaque-e': 'UNKNOWN', 'opaque-f': 'SUPPORTED',
                      'opaque-g': 'SUPPORTED'}, [], ['opaque-d'])
        metadata = [
            {'record_kind': 'run', 'run_id': 'run.with.dots'},
            {'record_kind': 'receipt', 'run_id': 'run.with.dots', 'receipt_id': 'a' * 64},
            {'record_kind': 'artifact', 'run_id': 'run.with.dots', 'receipt_id': 'a' * 64,
             'artifact_path': 'out/result.json', 'source': {'locator': 'synthetic original',
                                                          'file': 'out/result.json', 'sha256': 'b' * 64}},
            {'record_kind': 'observation', 'run_id': 'run.with.dots', 'receipt_id': 'a' * 64,
             'output_path': 'out/result.json', 'source': {'locator': '/score', 'file': 'out/result.json',
                                                        'sha256': 'b' * 64, 'receipt_id': 'a' * 64}},
            {'record_kind': 'declared_output', 'run_id': 'run.with.dots', 'output_path': 'out/missing.json'},
            {'record_kind': 'lifecycle_fact', 'run_id': 'run.with.dots'},
            {'record_kind': 'contract'}]
        for node, fields in zip(spec['nodes'], metadata):
            node.update(fields)
        return spec

    def test_record_relations_are_exact_and_leave_every_inference_field_unchanged(self):
        spec = self.record_fixture()
        plain = deepcopy(spec)
        for node in plain['nodes']:
            for key in ('record_kind', 'run_id', 'receipt_id', 'output_path', 'artifact_path'):
                node.pop(key, None)
        expected, actual = analyze_hypergraph(plain), analyze_hypergraph(spec)
        for field in expected.keys() - {'record_relations', 'record_topology', 'reported_nodes'}:
            self.assertEqual(actual[field], expected[field], field)
        self.assertEqual(actual['record_topology']['relation_counts'], {
            'artifact_observation': 1, 'declared_output': 1, 'receipt_artifact': 1,
            'run_artifact': 1, 'run_lifecycle_fact': 1, 'run_observation': 1, 'run_receipt': 1})
        self.assertEqual(actual['record_topology']['project_context_node_ids'], ['opaque-g'])
        self.assertEqual(actual['record_topology']['record_unlinked_node_ids'], [])
        self.assertEqual(actual['record_topology']['dependency_unlinked_node_ids'], sorted(n['id'] for n in spec['nodes']))
        self.assertEqual(actual['record_topology']['issues'], [])
        self.assertNotIn('opaque-d', actual['declared_supported_closure'])
        self.assertNotIn('opaque-e', actual['declared_supported_closure'])
        self.assertTrue(all(r['scientific_support'] == 'UNKNOWN' for r in actual['record_relations']))
        self.assertEqual(spec, self.record_fixture())

    def test_record_relations_report_ambiguous_and_conflicting_bindings_without_guessing(self):
        spec = self.record_fixture()
        duplicate = deepcopy(spec['nodes'][0])
        duplicate['id'] = 'duplicate-run'
        spec['nodes'].append(duplicate)
        spec['nodes'][3]['source']['receipt_id'] = 'c' * 64
        result = analyze_hypergraph(spec)
        self.assertFalse(any(r['kind'].startswith('run_') or r['kind'] == 'declared_output'
                             for r in result['record_relations']))
        self.assertFalse(any(r['to'] == 'opaque-d' for r in result['record_relations']))
        self.assertTrue(any(i['reason'] == 'AMBIGUOUS_BINDING' for i in result['record_topology']['issues']))
        self.assertTrue(any(i['node_id'] == 'opaque-d' and i['reason'] == 'CONFLICTING_BINDINGS'
                            for i in result['record_topology']['issues']))

    def test_record_relations_do_not_match_equal_bytes_across_runs_or_disagreeing_receipts(self):
        spec = self.record_fixture()
        other = deepcopy(spec['nodes'][0])
        other.update(id='another-run', run_id='another')
        spec['nodes'].append(other)
        spec['nodes'][3]['run_id'] = 'another'
        result = analyze_hypergraph(spec)
        self.assertEqual([r['kind'] for r in result['record_relations'] if r['to'] == 'opaque-d'], ['run_observation'])
        self.assertTrue(any(i['field'] == 'receipt.run_id' for i in result['record_topology']['issues']))
        spec['nodes'][3].pop('receipt_id')
        spec['nodes'][3]['source'].pop('receipt_id')
        result = analyze_hypergraph(spec)
        self.assertFalse(any(r['kind'] == 'artifact_observation' for r in result['record_relations']))
        self.assertTrue(any(i['reason'] == 'UNMATCHED_BINDING' and i['field'] == 'artifact_identity'
                            for i in result['record_topology']['issues']))

    def test_record_conflicts_preserve_only_independent_exact_field_relations(self):
        spec = self.record_fixture()
        spec['nodes'][2]['receipt_id'] = 'c' * 64
        result = analyze_hypergraph(spec)
        self.assertTrue(any(r['kind'] == 'run_observation' and r['to'] == 'opaque-d'
                            for r in result['record_relations']))
        self.assertFalse(any(r['kind'] == 'artifact_observation' for r in result['record_relations']))
        self.assertTrue(any(i['node_id'] == 'opaque-d' and i['field'] == 'artifact.receipt_id'
                            for i in result['record_topology']['issues']))

    def test_classified_records_report_missing_required_identities(self):
        required = {'run': ('run_id',), 'receipt': ('run_id', 'receipt_id'),
                    'artifact': ('run_id', 'path', 'sha256'), 'declared_output': ('run_id', 'path'),
                    'lifecycle_fact': ('run_id',), 'observation': ('run_id',)}
        for kind, fields in required.items():
            with self.subTest(kind=kind):
                spec = graph({'opaque-record': 'UNKNOWN'}, [], ['opaque-record'])
                spec['nodes'][0]['record_kind'] = kind
                before = deepcopy(spec)
                result = analyze_hypergraph(spec)
                issues = result['record_topology']['issues']
                self.assertEqual({i['field'] for i in issues if i['reason'] == 'MISSING_BINDING'}, set(fields))
                self.assertEqual(result['record_relations'], [])
                self.assertEqual(result['goals']['opaque-record']['status'], 'UNKNOWN')
                self.assertEqual(spec, before)

    def test_receipt_ownership_conflict_preserves_independent_run_relations(self):
        for position, expected in ((2, 'run_artifact'), (3, 'run_observation'),
                                   (4, 'declared_output'), (5, 'run_lifecycle_fact')):
            with self.subTest(kind=expected):
                spec = self.record_fixture()
                other_run = deepcopy(spec['nodes'][0]); other_run.update(id='run-b', run_id='run-b')
                other_receipt = deepcopy(spec['nodes'][1]); other_receipt.update(id='receipt-b', run_id='run-b', receipt_id='c' * 64)
                spec['nodes'].extend([other_run, other_receipt])
                target = spec['nodes'][position]
                target['receipt_id'] = 'c' * 64
                if isinstance(target['source'], dict):
                    target['source']['receipt_id'] = 'c' * 64
                result = analyze_hypergraph(spec)
                relations = [r for r in result['record_relations'] if r['to'] == target['id']]
                self.assertEqual([r['kind'] for r in relations], [expected])
                self.assertEqual(relations[0]['from'], 'opaque-a')
                self.assertTrue(any(i['node_id'] == target['id'] and i['field'] == 'receipt.run_id'
                                    for i in result['record_topology']['issues']))

    def test_explicit_source_base_normalizes_aliases_without_changing_identity_metadata(self):
        spec = self.record_fixture()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            before = deepcopy(spec)
            expected = hypergraph.record_topology(spec)
            adapted = deepcopy(spec)
            adapted['record_source_base_dir'] = str(base)
            for row in adapted['nodes']:
                if isinstance(row['source'], dict) and 'file' in row['source']:
                    row['source']['path'] = row['source']['file']
                    row['source']['file'] = str(base / row['source']['file'])
            original = deepcopy(adapted)
            actual = hypergraph.record_topology(adapted)
            self.assertEqual(actual['record_relations'], expected['record_relations'])
            self.assertEqual(actual['record_topology']['issues'], [])
            self.assertEqual(adapted, original)
            self.assertEqual(spec, before)
            adapted['nodes'][3]['source']['path'] = 'out/different.json'
            result = hypergraph.record_topology(adapted)
            self.assertTrue(any(i['node_id'] == 'opaque-d' and i['field'] == 'path'
                                and i['reason'] == 'CONFLICTING_BINDINGS' for i in result['record_topology']['issues']))

    def test_unresolvable_path_alias_is_diagnostic_without_changing_supported_closure(self):
        spec = self.record_fixture()
        expected = analyze_hypergraph(spec)
        with tempfile.TemporaryDirectory() as directory:
            spec['record_source_base_dir'] = str(Path(directory).resolve())
            spec['nodes'][2]['artifact_path'] = 'out/invalid\0.json'
            spec['nodes'][2]['source']['file'] = 'out/invalid\0.json'
            result = analyze_hypergraph(spec)
            self.assertTrue(any(i['node_id'] == 'opaque-c' and i['field'] == 'path'
                                and i['reason'] == 'INVALID_BINDING' for i in result['record_topology']['issues']))
            for field in ('declared_supported_closure', 'goals', 'node_gaps', 'goal_relevance'):
                if field in expected:
                    self.assertEqual(result[field], expected[field])

    def test_invalid_reported_source_base_does_not_interrupt_actual_inference(self):
        spec = self.record_fixture()
        spec['hyperedges'] = [{'id': 'supported-route', 'premises': ['opaque-a'],
                              'conclusion': 'opaque-d', 'status': 'SUPPORTED', 'source': 'synthetic'}]
        expected = analyze_hypergraph(spec)
        self.assertEqual(expected['goals']['opaque-d']['status'], 'DECLARED_SUPPORTED')
        for base in ('relative/source', [], 'Z:\\invalid\0base'):
            with self.subTest(base=base):
                value = {**deepcopy(spec), 'record_source_base_dir': base}
                before = deepcopy(value)
                actual = analyze_hypergraph(value)
                for field in expected.keys() - {'record_topology'}:
                    self.assertEqual(actual[field], expected[field], field)
                self.assertTrue(any(i['scope'] == 'dependency_map' and i['node_id'] is None
                                    and i['field'] == 'record_source_base_dir' and i['reason'] == 'INVALID_BINDING'
                                    for i in actual['record_topology']['issues']))
                self.assertEqual(value, before)
        # API arguments are explicit configuration rather than reported metadata.
        with self.assertRaises(ValueError):
            hypergraph.record_topology(spec, source_base='relative/source')

    def test_structural_goal_paths_and_record_components_never_discharge_and_obligations(self):
        spec = self.record_fixture()
        spec['nodes'].append({'id': 'formal-goal', 'status': 'UNKNOWN', 'source': 'synthetic formal goal'})
        spec['hyperedges'] = [{'id': 'and-rule', 'premises': ['opaque-d', 'opaque-e'],
                              'conclusion': 'formal-goal', 'status': 'PROPOSED', 'source': 'synthetic rule'}]
        spec['goals'] = ['formal-goal']
        result = analyze_hypergraph(spec)
        topology = result['record_topology']
        self.assertIn('opaque-a', topology['dependency_no_goal_path_node_ids'])
        self.assertNotIn('opaque-a', topology['record_and_dependency_no_goal_connection_node_ids'])
        self.assertEqual(topology['record_and_dependency_no_goal_connection_node_ids'], ['opaque-g'])
        self.assertNotIn('formal-goal', topology['record_unlinked_node_ids'])
        self.assertIn('formal-goal', topology['unclassified_node_ids'])
        self.assertEqual(len(topology['dependency_components']), 6)
        self.assertEqual(len(topology['record_components']), 2)
        self.assertEqual(result['goals']['formal-goal']['status'], 'UNKNOWN')
        self.assertNotIn('formal-goal', result['declared_supported_closure'])

    def test_record_relations_leave_unregistered_routes_and_untyped_locators_unlinked(self):
        spec = graph({'owned:run:pretend': 'SUPPORTED', 'owned:fact:run.pretend.failed': 'SUPPORTED',
                      'pending': 'UNKNOWN'}, [], ['pending'])
        spec['nodes'][0]['source'] = 'owned run pretend'
        spec['nodes'][1].update(record_kind='lifecycle_fact', route_id='pretend')
        spec['nodes'][2].update(record_kind='observation', route_id='pretend', output_path='out/a.json')
        result = analyze_hypergraph(spec)
        self.assertEqual(result['record_relations'], [])
        self.assertEqual(result['record_topology']['unclassified_node_ids'], ['owned:run:pretend'])
        self.assertEqual([i['reason'] for i in result['record_topology']['issues']], ['RUN_NOT_REGISTERED'] * 2)

    def test_record_relations_diagnose_invalid_metadata_without_changing_supported_status(self):
        spec = self.record_fixture()
        spec['nodes'][0]['run_id'] = ['run.with.dots']
        spec['nodes'][1]['receipt_id'] = 'invalid'
        spec['nodes'][2]['artifact_path'] = 'different.json'
        spec['nodes'][3]['record_kind'] = {'untrusted': 'observation'}
        result = analyze_hypergraph(spec)
        self.assertEqual(result['record_relations'], [])
        self.assertIn('opaque-a', result['declared_supported_closure'])
        self.assertEqual({i['reason'] for i in result['record_topology']['issues']},
                         {'INVALID_BINDING', 'CONFLICTING_BINDINGS', 'INVALID_RECORD_KIND', 'UNMATCHED_BINDING'})

    def test_ordered_first_witness_survives_delayed_early_rule(self):
        spec = graph({'a': 'SUPPORTED', 'b': 'UNKNOWN', 'c': 'UNKNOWN'}, [
            ('early', ['b'], 'c', 'SUPPORTED'),
            ('b', ['a'], 'b', 'SUPPORTED'),
            ('late', ['a'], 'c', 'SUPPORTED')], ['c'])
        result = analyze_hypergraph(spec)
        self.assertEqual(result['declared_derivation_rules'], {'b': 'b', 'c': 'late'})
        self.assertNotIn('a', result['declared_derivation_rules'])

    def test_event_round_order_and_delayed_contradicted_head(self):
        spec = graph({'a': 'SUPPORTED', 'b': 'UNKNOWN', 'c': 'UNKNOWN',
                      'd': 'UNKNOWN', 'bad': 'CONTRADICTED', 'blocked': 'UNKNOWN'}, [
            ('early', ['d'], 'c', 'SUPPORTED'),
            ('d', ['b'], 'd', 'SUPPORTED'),
            ('late', ['b'], 'c', 'SUPPORTED'),
            ('b', ['a'], 'b', 'SUPPORTED'),
            ('conflict', ['c', 'd'], 'bad', 'SUPPORTED'),
            ('blocked', ['bad'], 'blocked', 'SUPPORTED')], ['c', 'bad', 'blocked'])
        result = analyze_hypergraph(spec)
        self.assertEqual(list(result['declared_derivation_rules'].items()), [('b', 'b'), ('d', 'd'), ('c', 'late')])
        self.assertEqual(result['active_contradicted_conclusion_rules'], ['conflict'])
        self.assertEqual(result['declared_supported_closure'], ['a', 'b', 'c', 'd'])

    def test_optimized_closure_and_whole_analyzer_match_1000_ordered_graphs(self):
        rng = random.Random(20261002)
        for case in range(1000):
            names = [str(i) for i in range(rng.randrange(1, 19))]
            statuses = {name: rng.choice(('SUPPORTED', 'UNKNOWN', 'CONTRADICTED')) for name in names}
            rules = [(str(i), rng.sample(names, rng.randrange(min(len(names), 4) + 1)), rng.choice(names),
                      rng.choice(('SUPPORTED', 'SUPPORTED', 'PROPOSED', 'CONTRADICTED')))
                     for i in range(rng.randrange(36))]
            spec = graph(statuses, rules, rng.sample(names, rng.randrange(1, min(len(names), 3) + 1)),
                         max_blocker_sets=16, max_combinations=250)
            for node in spec['nodes']:
                if rng.randrange(3) == 0:
                    node['allow_direct_evidence'] = bool(rng.randrange(2))
            nodes = {node['id']: node for node in spec['nodes']}
            with self.subTest(case=case):
                expected = ordered_reference(nodes, spec['hyperedges'])
                actual = hypergraph._supported_closure(nodes, spec['hyperedges'])
                self.assertEqual(actual, expected)
                self.assertEqual(list(actual[1].items()), list(expected[1].items()))
                self.assertEqual(hypergraph._goal_relevance(spec['hyperedges'], spec['goals']),
                                 relevance_reference(spec['hyperedges'], spec['goals']))
                result = analyze_hypergraph(spec)
                with patch.object(hypergraph, '_supported_closure', side_effect=ordered_reference), \
                        patch.object(hypergraph, '_goal_relevance', side_effect=relevance_reference):
                    original = analyze_hypergraph(spec)
                self.assertEqual(result, original)

    def test_relevance_keeps_proposed_rules_and_contradicted_heads(self):
        spec = graph({'a': 'UNKNOWN', 'b': 'UNKNOWN', 'bad': 'CONTRADICTED', 'outside': 'UNKNOWN', 'orphan': 'UNKNOWN'}, [
            ('proposal', ['a'], 'bad', 'PROPOSED'),
            ('supported', ['b'], 'a', 'SUPPORTED'),
            ('cycle', ['a'], 'b', 'PROPOSED'),
            ('rejected', ['outside'], 'a', 'CONTRADICTED'),
            ('only-rejected', ['outside'], 'orphan', 'CONTRADICTED')], ['bad', 'orphan'])
        self.assertEqual(hypergraph._goal_relevance(spec['hyperedges'], spec['goals']),
                         ({'a', 'b', 'bad', 'orphan'}, {'proposal', 'supported', 'cycle'}))
        with patch.object(hypergraph, '_goal_relevance', side_effect=relevance_reference):
            expected = analyze_hypergraph(spec)
        self.assertEqual(analyze_hypergraph(spec), expected)
        self.assertNotIn('a', analyze_hypergraph(spec)['direct_evidence_node_ids'])
        self.assertNotIn('orphan', analyze_hypergraph(spec)['direct_evidence_node_ids'])

    def test_empty_graph_preserves_empty_closure_and_relevance(self):
        result = analyze_hypergraph(graph({}, [], []))
        self.assertEqual(result['declared_supported_closure'], [])
        self.assertEqual(result['declared_derivation_rules'], {})
        self.assertEqual(result['active_contradicted_conclusion_rules'], [])
        self.assertEqual(result['goals'], {})

    def test_and_requires_every_premise(self):
        spec = graph({"A": "SUPPORTED", "B": "UNKNOWN", "C": "UNKNOWN"},
                     [("ab", ["A", "B"], "C", "SUPPORTED")], ["C"])
        spec["nodes"][0]["allow_direct_evidence"] = False
        result = analyze_hypergraph(spec)
        self.assertEqual(result["declared_supported_closure"], ["A"])
        self.assertEqual(result["goals"]["C"]["minimal_missing_evidence_sets"],
                         [["node:B"]])
        spec["nodes"][1]["status"] = "SUPPORTED"
        self.assertEqual(analyze_hypergraph(spec)["declared_supported_closure"], ["A", "B", "C"])

    def test_empty_unanchored_cycle_never_self_proves(self):
        spec = graph({"A": "UNKNOWN", "B": "UNKNOWN"},
                     [("a", ["A"], "B", "SUPPORTED"),
                      ("b", ["B"], "A", "SUPPORTED")], ["A", "B"])
        result = analyze_hypergraph(spec)
        self.assertEqual(result["declared_supported_closure"], [])
        for goal in result["goals"].values():
            self.assertEqual(goal["minimal_missing_evidence_sets"], [])
            self.assertEqual(goal["status"], "UNRESOLVED")
            self.assertNotIn([], goal["minimal_missing_evidence_sets"])

    def test_proposed_rule_is_an_obligation_not_supported(self):
        spec = graph({"A": "SUPPORTED", "C": "UNKNOWN"},
                     [("proposal", ["A"], "C", "PROPOSED")], ["C"])
        result = analyze_hypergraph(spec)
        self.assertEqual(result["declared_supported_closure"], ["A"])
        self.assertEqual(result["goals"]["C"]["minimal_missing_evidence_sets"],
                         [["rule:proposal"]])
        self.assertEqual(result["ready_obligations"][0]["token"], "rule:proposal")

    def test_proposed_rule_still_requires_all_unknown_tails(self):
        spec = graph({"A": "UNKNOWN", "B": "UNKNOWN", "C": "UNKNOWN"},
                     [("proposal", ["A", "B"], "C", "PROPOSED")], ["C"])
        result = analyze_hypergraph(spec)
        self.assertIn(["node:A", "node:B", "rule:proposal"],
                      result["goals"]["C"]["minimal_missing_evidence_sets"])
        self.assertFalse(any(row["token"] == "rule:proposal" for row in result["ready_obligations"]))

    def test_alternative_rules_are_or_and_dominated_sets_removed(self):
        spec = graph({"A": "UNKNOWN", "B": "UNKNOWN", "C": "UNKNOWN", "D": "UNKNOWN"},
                     [("a", ["A"], "D", "SUPPORTED"),
                      ("bc", ["B", "C"], "D", "SUPPORTED"),
                      ("abc", ["A", "B", "C"], "D", "SUPPORTED")], ["D"])
        self.assertEqual(analyze_hypergraph(spec)["goals"]["D"]["minimal_missing_evidence_sets"],
                         [["node:A"], ["node:B", "node:C"]])

    def test_empty_supported_rule_is_an_explicit_reported_anchor(self):
        spec = graph({"A": "UNKNOWN"}, [("axiom", [], "A", "SUPPORTED")], ["A"])
        result = analyze_hypergraph(spec)
        self.assertEqual(result["declared_supported_closure"], ["A"])
        self.assertEqual(result["goals"]["A"]["minimal_missing_evidence_sets"], [[]])

    def test_contradicted_rules_and_nodes_never_silently_activate(self):
        spec = graph({"A": "SUPPORTED", "B": "CONTRADICTED", "C": "UNKNOWN"},
                     [("conflict", ["A"], "B", "SUPPORTED"),
                      ("rejected", ["A"], "C", "CONTRADICTED"),
                      ("blocked", ["B"], "C", "SUPPORTED")], ["B", "C"])
        spec["nodes"][1]["allow_direct_evidence"] = True
        result = analyze_hypergraph(spec)
        self.assertEqual(result["declared_supported_closure"], ["A"])
        self.assertEqual(result["active_contradicted_conclusion_rules"], ["conflict"])
        self.assertEqual(result["goals"]["B"]["status"], "CONTRADICTED")
        self.assertEqual(result["goals"]["B"]["minimal_missing_evidence_sets"], [])
        self.assertEqual(result["goals"]["C"]["minimal_missing_evidence_sets"], [])
        self.assertEqual(result["goals"]["C"]["status"], "UNRESOLVED")

    def test_work_truncation_discards_partial_blockers(self):
        spec = graph({"A": "UNKNOWN", "B": "UNKNOWN", "C": "UNKNOWN"},
                     [("ab", ["A", "B"], "C", "SUPPORTED")], ["C"], max_combinations=1)
        result = analyze_hypergraph(spec)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["declared_supported_closure"], [])
        self.assertEqual(result["goals"]["C"]["minimal_missing_evidence_sets"], [])
        self.assertFalse(result["goals"]["C"]["blocker_sets_complete"])

    def test_family_truncation_is_fail_closed(self):
        spec = graph({"A": "UNKNOWN", "B": "UNKNOWN"},
                     [("ab", ["A"], "B", "SUPPORTED")], ["B"], max_blocker_sets=1)
        spec["nodes"][1]["allow_direct_evidence"] = True
        result = analyze_hypergraph(spec)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["goals"]["B"]["minimal_missing_evidence_sets"], [])

    def test_source_preservation_and_no_input_mutation(self):
        spec = graph({"A": "SUPPORTED", "B": "UNKNOWN"},
                     [("a", ["A"], "B", "PROPOSED")], ["B"])
        spec["nodes"][0]["source"] = {"locator": "paper, page 7", "extra": [1, 2]}
        original = deepcopy(spec)
        result = analyze_hypergraph(spec)
        self.assertEqual(spec, original)
        self.assertEqual(result["reported_nodes"], original["nodes"])
        self.assertEqual(result["reported_hyperedges"], original["hyperedges"])
        self.assertEqual(result["assurance"], ASSURANCE)
        result["reported_nodes"][0]["source"]["extra"].append(3)
        self.assertEqual(spec, original)

    def test_derived_goal_has_only_intermediate_gap_not_goal_atom(self):
        spec = graph({"anchor": "SUPPORTED", "gap": "UNKNOWN", "middle": "UNKNOWN", "goal": "UNKNOWN"},
                     [("m", ["anchor", "gap"], "middle", "SUPPORTED"),
                      ("g", ["middle"], "goal", "SUPPORTED")], ["goal"])
        result = analyze_hypergraph(spec)
        self.assertEqual(result["goals"]["goal"]["minimal_missing_evidence_sets"], [["node:gap"]])
        self.assertEqual(result["direct_evidence_node_ids"], ["gap"])
        self.assertFalse(any("node:goal" in row for row in result["goals"]["goal"]["minimal_missing_evidence_sets"]))

    def test_explicit_direct_proof_is_a_separate_serious_alternative(self):
        spec = graph({"A": "UNKNOWN", "goal": "UNKNOWN"},
                     [("r", ["A"], "goal", "SUPPORTED")], ["goal"])
        spec["nodes"][1]["allow_direct_evidence"] = True
        result = analyze_hypergraph(spec)
        self.assertEqual(result["goals"]["goal"]["minimal_missing_evidence_sets"],
                         [["node:A"], ["node:goal"]])
        self.assertIn("DIRECT_PROOF_ALTERNATIVE", [row["kind"] for row in result["ready_obligations"]])

    def test_explicit_false_leaf_stays_unresolved_without_fake_unlock(self):
        spec = graph({"leaf": "UNKNOWN"}, [], ["leaf"])
        spec["nodes"][0]["allow_direct_evidence"] = False
        result = analyze_hypergraph(spec)
        self.assertEqual(result["goals"]["leaf"]["status"], "UNRESOLVED")
        self.assertEqual(result["goals"]["leaf"]["minimal_missing_evidence_sets"], [])
        self.assertEqual(result["ready_obligations"], [])
        self.assertTrue(result["goals"]["leaf"]["blocker_sets_complete"])

    def test_direct_evidence_flag_must_be_boolean(self):
        spec = graph({"A": "UNKNOWN"}, [], ["A"])
        for invalid in (0, 1, "true", None):
            with self.subTest(invalid=invalid):
                spec["nodes"][0]["allow_direct_evidence"] = invalid
                with self.assertRaises(ValueError):
                    analyze_hypergraph(spec)

    def test_invalid_input_rejects_unknown_refs_duplicates_and_limits(self):
        base = graph({"A": "UNKNOWN"}, [], ["A"])
        cases = [dict(base, limits={"max_nodes": 0}),
                 dict(base, limits={"max_nodes": True}),
                 dict(base, goals=["missing"]),
                 dict(base, nodes=base["nodes"] * 2),
                 graph({"A": "UNKNOWN", "B": "UNKNOWN"}, [], ["A"], max_nodes=1),
                 graph({"A": "UNKNOWN"}, [("x", ["missing"], "A", "SUPPORTED")], ["A"])]
        for spec in cases:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                analyze_hypergraph(spec)

    def test_optional_hash_audit_checks_bytes_without_upgrading_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.txt"
            path.write_bytes(b"evidence bytes")
            spec = graph({"A": "UNKNOWN"}, [], ["A"])
            spec["nodes"][0]["source"] = {"locator": "local evidence", "file": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            self.assertTrue(audit_sources(spec, directory)["all_requested_files_match"])
            analyzed = analyze_hypergraph(spec)
            self.assertEqual(analyzed["declared_supported_closure"], [])
            self.assertEqual(analyzed["goals"]["A"]["minimal_missing_evidence_sets"], [["node:A"]])
            path.write_bytes(b"changed")
            self.assertFalse(audit_sources(spec, directory)["all_requested_files_match"])
            spec["limits"] = {"max_source_bytes": 1}
            self.assertEqual(audit_sources(spec, directory)["audits"][0]["status"], "BYTE_LIMIT_EXCEEDED")


if __name__ == "__main__":
    unittest.main()

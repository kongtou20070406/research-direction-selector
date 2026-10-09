"""Decision-changing graph search boundaries, without research execution."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rds_advisor_search import evaluate_condition, search_directions
from rds_advisor import RDSAdvisor
from rds_meta import parse_simple_yaml


def fact(value, **extra):
    return {"value": value, "source": "synthetic-fixture:line-1", **extra}


def node(rid, pre=None, satisfied=None, action_id=None):
    return {"id": rid, "sources": ["synthetic rule"], "executable": {
        "decisions": ["choose"], "preconditions": pre or [],
        "satisfied_when": satisfied if satisfied is not None else [{"fact": rid + "-done", "value": True}],
        "action": {"id": action_id or rid + "-test", "kind": "PAIRED_TEST", "description": "bounded paired test",
            "competing_explanations": ["explanation A", "explanation B"], "required_observables": ["paired endpoint"],
            "outcomes": [{"observation": "A", "next_decision": "select A"}, {"observation": "B", "next_decision": "select B"}]}}}


def edge(source, target, relation="prerequisite_for"):
    return {"from": source, "to": target, "relation": relation}


def comparable_probes():
    graph = {"nodes": [node("fast"), node("slow")], "edges": []}
    for rule in graph["nodes"]:
        rule["executable"]["action"]["discrimination"] = {
            "scope_id": "same-paired-test", "source": "synthetic paired-outcome fixture",
            "predictions": {"explanation A": ["A"], "explanation B": ["B"]}}
    return graph


class SearchTests(unittest.TestCase):
    def context(self, **extra):
        return {"decision": {"id": "choose", "target_rules": ["root"]}, "facts": {}, **extra}

    def test_source_and_reliability_control_three_valued_truth(self):
        condition = {"fact": "x", "value": True}
        for record in ({"value": True}, fact(True, reliable=False), fact(True, reliability="UNRELIABLE"), fact(None)):
            self.assertEqual(evaluate_condition(condition, {"x": record})["truth"], "UNKNOWN")
        self.assertEqual(evaluate_condition(condition, {"x": fact(False)})["truth"], "FALSE")
        report = evaluate_condition(condition, {"x": fact(True, evidence_status="VERIFIED")})
        self.assertEqual(report["truth"], "TRUE")
        self.assertEqual(report["evidence_status"], "INPUT_REPORTED")
        self.assertEqual(evaluate_condition(condition, {"x": fact(1)})["truth"], "FALSE")

    def test_unknown_generates_specific_query_and_false_blocks_experiment(self):
        graph = {"nodes": [node("root", [{"fact": "matched", "value": True, "query": "Read paired arm manifests"}])], "edges": []}
        result = search_directions(graph, self.context())
        self.assertEqual(result["candidates"][0]["status"], "NEEDS_EVIDENCE")
        self.assertEqual(result["queries"][0]["query"], "Read paired arm manifests")
        self.assertTrue(result["candidates"][0]["steps"][-1]["conditional"])
        result = search_directions(graph, self.context(facts={"matched": fact(False)}))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["blocked_candidates"][0]["status"], "BLOCKED_PREREQUISITE")

    def test_true_dependencies_compose_derivation_without_repeating_checks(self):
        graph = {"nodes": [node("check"), node("root")], "edges": [edge("check", "root")]}
        result = search_directions(graph, self.context(facts={"check-done": fact(True)}))
        candidate = result["candidates"][0]
        self.assertEqual(candidate["status"], "READY")
        self.assertEqual([s["id"] for s in candidate["steps"]], ["root-test"])
        self.assertTrue(any(d.get("relation") == "prerequisite_for" for d in candidate["derivation"]))
        self.assertTrue(any(d.get("fact") == "check-done" and d["truth"] == "TRUE" for d in candidate["derivation"]))
        changed = search_directions(graph, self.context(facts={"check-done": fact(False)}))
        self.assertEqual(changed["candidates"], [])

    def test_unknown_and_unconfigured_prerequisites_never_certify_readiness(self):
        graph = {"nodes": [{"id": "text", "trigger": "eval anything"}, node("root")], "edges": [edge("text", "root")]}
        result = search_directions(graph, self.context())
        self.assertEqual(result["candidates"][0]["status"], "NEEDS_EVIDENCE")
        self.assertIn("explicit bounded prerequisite", result["queries"][0]["query"])

    def test_cycles_depth_and_candidate_limits_report_bounds(self):
        graph = {"nodes": [node("check"), node("root")], "edges": [edge("check", "root"), edge("root", "check")]}
        result = search_directions(graph, self.context())
        self.assertTrue(result["cycles"])
        self.assertEqual(result["candidates"], [])
        graph["edges"] = [edge("check", "root")]
        result = search_directions(graph, self.context(), max_depth=1)
        self.assertTrue(result["truncation"]["truncated"])
        context = {"decision": "choose", "facts": {}}
        result = search_directions({"nodes": [node("one"), node("two")], "edges": []}, context, max_candidates=1)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertIn("candidate limit", result["truncation"]["reasons"])

    def test_sourced_costs_compare_supported_same_scope_coverage_and_budget(self):
        graph = comparable_probes()
        costs = {name + "-test": fact(value, unit="seconds", comparison_group="host-recipe") for name, value in (("fast", 2), ("slow", 5))}
        result = search_directions(graph, {"decision": "choose", "facts": {}, "costs": costs})
        self.assertEqual(result["ranking"]["dominance"][0]["better"], "fast:fast-test")
        changed = copy.deepcopy(costs)
        changed["slow-test"]["unit"] = "steps"
        self.assertEqual(search_directions(graph, {"decision": "choose", "costs": changed})["ranking"]["dominance"], [])
        budget = fact(3, unit="seconds", comparison_group="host-recipe")
        result = search_directions(graph, {"decision": "choose", "costs": costs, "budget": budget})
        self.assertEqual([c["id"] for c in result["candidates"]], ["fast:fast-test"])
        self.assertEqual(result["blocked_candidates"][0]["status"], "BLOCKED_BUDGET")
        unknown = search_directions(graph, {"decision": "choose"})
        self.assertEqual(len(unknown["ranking"]["cost_unknown"]), 2)
        self.assertEqual(unknown["ranking"]["dominance"], [])

    def test_priority_fallback_retains_bound_and_default_search_order(self):
        active = node('active')
        active['executable']['decisions'] = []
        trigger = node('trigger', [{'fact': 'ready', 'value': True,
                                   'on_false': copy.deepcopy(active['executable']['action'])}])
        graph = {'nodes': [node('first'), trigger, active], 'edges': []}
        context = {'decision': 'choose', 'facts': {'ready': fact(False)}}
        original = copy.deepcopy((graph, context))
        ordinary = search_directions(graph, context, max_candidates=1)
        empty = search_directions(graph, context, max_candidates=1, priority_action_ids=())
        self.assertEqual(ordinary, empty)
        self.assertEqual([row['action']['id'] for row in ordinary['candidates']], ['first-test'])
        prioritized = search_directions(graph, context, max_candidates=1, priority_action_ids=('active-test',))
        self.assertEqual([row['id'] for row in prioritized['candidates']], ['trigger:active-test'])
        # Retention priority cannot authorize a route from a partial comparison.
        self.assertEqual(prioritized['candidates'][0]['status'], 'NEEDS_COMPLETE_ANALYSIS')
        self.assertFalse(prioritized['analysis_coverage']['full'])
        self.assertEqual(prioritized['truncation']['limits']['max_candidates'], 1)
        self.assertIn('candidate limit', prioritized['truncation']['reasons'])
        self.assertTrue(prioritized['truncation']['truncated'])
        self.assertEqual((graph, context), original)

    def test_over_budget_preserves_method_and_evidence_gates(self):
        from test_rds_methods import method, policy
        for gate, status in (("conflict", "BLOCKED_METHOD"),
                             ("description", "NEEDS_METHOD_DESCRIPTION"),
                             ("clarification", "NEEDS_METHOD_CLARIFICATION"),
                             ("evidence", "NEEDS_EVIDENCE")):
            with self.subTest(gate=gate):
                root = node("root")
                root["executable"]["action"]["methods"] = method()
                context = self.context(**policy(),
                    costs={"root-test": fact(31, unit="seconds", comparison_group="host")},
                    budget=fact(30, unit="seconds", comparison_group="host"))
                if gate == "conflict":
                    root["executable"]["action"]["methods"] = method(device="gpu")
                elif gate == "description":
                    del root["executable"]["action"]["methods"]
                elif gate == "clarification":
                    context["method_constraints"].append({"id": "unclear", "quote": "Use exact methods",
                        "source": "user:fixture", "status": "UNRESOLVED", "question": "Which steps?"})
                else:
                    root["executable"]["preconditions"] = [{"fact": "matched", "value": True,
                                                            "query": "Read paired arm manifests"}]
                    context["costs"]["query:root:matched"] = fact(0, unit="seconds", comparison_group="host")
                graph = {"nodes": [root], "edges": []}
                original = copy.deepcopy((graph, context))
                result = search_directions(graph, context)
                candidates = result["blocked_candidates"] if gate == "conflict" else result["candidates"]
                candidate = candidates[0]
                self.assertEqual(candidate["status"], status)
                self.assertEqual(candidate["budget_status"], "OVER_REPORTED_BUDGET")
                self.assertEqual(candidate["incremental_cost"]["value"], 31)
                self.assertEqual(candidate["method_review"]["status"],
                                 "CONFLICT" if gate == "conflict" else "COMPATIBLE" if gate == "evidence" else "UNKNOWN")
                if gate == "clarification":
                    self.assertEqual(candidate["method_review"]["questions"], [{"constraint_id": "unclear",
                        "quote": "Use exact methods", "source": "user:fixture", "question": "Which steps?"}])
                if gate == "evidence":
                    self.assertEqual(len(result["queries"]), 1)
                    self.assertEqual(result["queries"][0]["query"], "Read paired arm manifests")
                self.assertEqual((graph, context), original)

    def test_method_blocked_over_budget_route_does_not_hide_compatible_route(self):
        from test_rds_methods import method, policy
        graph = {"nodes": [node("blocked"), node("safe")], "edges": []}
        for rule, device in zip(graph["nodes"], ("gpu", "cpu")):
            rule["executable"]["action"]["methods"] = method(device=device)
        context = {"decision": "choose", **policy(),
            "costs": {rid + "-test": fact(cost, unit="seconds", comparison_group="host")
                      for rid, cost in (("blocked", 31), ("safe", 1))},
            "budget": fact(30, unit="seconds", comparison_group="host")}
        result = search_directions(graph, context)
        self.assertEqual(result["blocked_candidates"][0]["status"], "BLOCKED_METHOD")
        self.assertEqual([(c["action"]["id"], c["status"]) for c in result["candidates"]], [("safe-test", "READY")])

    def test_blocked_priority_action_cannot_evict_ready_candidate(self):
        for blocker in ('budget', 'method', 'unknown-premise'):
            with self.subTest(blocker=blocker):
                graph = {'nodes': [node('first'), node('active')], 'edges': []}
                context = {'decision': 'choose', 'facts': {}}
                if blocker == 'budget':
                    context.update(costs={name + '-test': fact(cost, unit='seconds', comparison_group='same-host')
                                          for name, cost in (('first', 1), ('active', 2))},
                                   budget=fact(1, unit='seconds', comparison_group='same-host'))
                elif blocker == 'method':
                    for rule, family in zip(graph['nodes'], ('safe', 'blocked')):
                        rule['executable']['action']['methods'] = {'family': family}
                    context['method_constraints'] = [{'id': 'no-blocked-method', 'quote': 'exclude blocked methods',
                        'source': 'synthetic fixture', 'status': 'CONFIRMED', 'forbid': {'family': 'blocked'}}]
                else:
                    graph['nodes'][1]['executable']['preconditions'] = [{'fact': 'unread', 'value': True}]
                result = search_directions(graph, context, max_candidates=1, priority_action_ids=('active-test',))
                self.assertEqual([row['action']['id'] for row in result['candidates']], ['first-test'])
                self.assertEqual(result['candidates'][0]['status'],
                                 'NEEDS_COMPLETE_ANALYSIS' if blocker == 'unknown-premise' else 'READY')
                if blocker != 'unknown-premise':
                    self.assertEqual(result['blocked_candidates'][0]['status'],
                                     'BLOCKED_BUDGET' if blocker == 'budget' else 'BLOCKED_METHOD')
                # Budget/method refusals are fully evaluated and reported; an
                # omitted conditional candidate is still a truncated comparison.
                self.assertEqual(result['truncation']['truncated'], blocker == 'unknown-premise')

    def test_unrelated_actions_with_matching_decision_text_do_not_compete_on_cost(self):
        graph = {"nodes": [node("cheap"), node("important")], "edges": []}
        graph["nodes"][1]["executable"]["action"]["competing_explanations"] = ["mechanism C", "mechanism D"]
        costs = {name + "-test": fact(value, unit="seconds", comparison_group="same-host")
                 for name, value in (("cheap", 1), ("important", 100))}
        result = search_directions(graph, {"decision": "choose", "costs": costs})
        self.assertEqual(result["candidates"][0]["decision_coverage"], result["candidates"][1]["decision_coverage"])
        self.assertEqual(result["ranking"]["dominance"], [])
        self.assertEqual(set(result["ranking"]["pareto_front"]), {"cheap:cheap-test", "important:important-test"})

    def test_actions_with_no_decision_difference_are_discarded(self):
        root = node("root")
        for outcome in root["executable"]["action"]["outcomes"]:
            outcome["next_decision"] = "keep same choice"
        result = search_directions({"nodes": [root]}, self.context())
        self.assertEqual(result["candidates"], [])
        self.assertIn("no outcome", result["discarded_candidates"][0]["reason"])

    def test_discarded_obligation_preserves_exact_identity_and_original_input(self):
        root = node("root", action_id="check:claim")
        root["executable"]["action"].update(kind="OBLIGATION_CHECK", target="scoped target", claim="scoped claim",
            outcomes=[{"observation": label, "next_decision": "review " + label}
                      for label in ("verified", "unresolved")])
        graph, context = {"nodes": [root]}, self.context()
        original = copy.deepcopy((graph, context))
        result = search_directions(graph, context)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["discarded_candidates"], [{"rule_id": "root", "id": "root:check:claim",
            "action_id": "check:claim", "reason": "obligation outcomes must distinguish verified, counterexample and unresolved"}])
        self.assertEqual((graph, context), original)

    def test_malformed_and_fallback_actions_do_not_invent_discard_identity(self):
        for action in (None, {}, {"id": []}):
            with self.subTest(action=action):
                root = node("root")
                root["executable"]["action"] = action
                result = search_directions({"nodes": [root]}, self.context())
                self.assertEqual(result["discarded_candidates"], [{"rule_id": "root", "reason": "missing action identity"}])
        fallback = node("unused", action_id="repair")['executable']['action']
        fallback['required_observables'] = []
        root = node("root", [{"fact": "ready", "value": True, "on_false": fallback}])
        result = search_directions({"nodes": [root]}, self.context(facts={"ready": fact(False)}))
        self.assertEqual(result['discarded_candidates'], [{"rule_id": "root", "id": "root:repair",
            "action_id": "repair", "reason": "missing required observables"}])

    def test_off_decision_fallback_does_not_inherit_an_unrelated_current_choice(self):
        fallback = node('unused')['executable']['action']
        fallback.update(id='interpretation', kind='INTERPRETATION_UPDATE',
                        outcomes=[{'observation': 'different boundary', 'next_decision': 'keep'}])
        unrelated = node('other', [{'fact': 'ready', 'value': True, 'on_false': fallback}])
        unrelated['executable']['decisions'] = ['other-decision']
        graph = {'nodes': [node('root'), unrelated], 'edges': []}
        context = {'decision': {'id': 'choose', 'current_choice': 'keep'},
                   'facts': {'ready': fact(False)}}
        result = search_directions(graph, context)
        row = next(r for r in result['analysis_coverage']['analyzed_nodes'] if r['id'] == 'other')
        self.assertTrue(row['fallback_actions'][0]['action_validation']['valid'])
        self.assertFalse(any('Invalid active fallback for other' in reason
                             for reason in result['analysis_coverage']['reasons']))
        self.assertFalse(any(r.get('action', {}).get('id') == 'interpretation' for r in result['candidates']))
        context['decision']['id'] = 'other-decision'
        selected = search_directions(graph, context)
        self.assertTrue(any('Invalid active fallback for other' in reason
                            for reason in selected['analysis_coverage']['reasons']))

    def test_distinct_resources_never_dominate_or_share_budget(self):
        graph = {"nodes": [node("cpu"), node("gpu")], "edges": []}
        costs = {name + "-test": fact(value, resource=name, unit="seconds", comparison_group="same-trial")
                 for name, value in (("cpu", 1), ("gpu", 2))}
        context = {"decision": "choose", "costs": costs}
        result = search_directions(graph, context)
        self.assertEqual(result["ranking"]["dominance"], [])
        self.assertEqual({c["incremental_cost"]["resource"] for c in result["candidates"]}, {"cpu", "gpu"})
        for limit in (0.5, 3):
            with self.subTest(limit=limit):
                context["budget"] = fact(limit, resource="cpu", unit="seconds", comparison_group="same-trial")
                result = search_directions(graph, context)
                gpu = next(c for c in result["candidates"] if c["rule_id"] == "gpu")
                self.assertEqual(gpu["status"], "READY")
                self.assertEqual(gpu["budget_status"], "UNKNOWN")
                if limit < 1:
                    self.assertEqual([c["rule_id"] for c in result["blocked_candidates"]], ["cpu"])
                else:
                    cpu = next(c for c in result["candidates"] if c["rule_id"] == "cpu")
                    self.assertEqual(cpu["budget_status"], "WITHIN_REPORTED_BUDGET")

    def test_named_resource_comparison_keeps_legacy_records_separate(self):
        graph = comparable_probes()
        costs = {name + "-test": fact(value, resource="cpu", unit="seconds", comparison_group="same-trial")
                 for name, value in (("fast", 2), ("slow", 5))}
        context = {"decision": "choose", "costs": costs}
        self.assertEqual(search_directions(graph, context)["ranking"]["dominance"][0]["better"], "fast:fast-test")
        context["budget"] = fact(3, resource="cpu", unit="seconds", comparison_group="same-trial")
        self.assertEqual(search_directions(graph, context)["blocked_candidates"][0]["rule_id"], "slow")
        costs["slow-test"].pop("resource")
        result = search_directions(graph, context)
        self.assertEqual(result["ranking"]["dominance"], [])
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(next(c for c in result["candidates"] if c["rule_id"] == "slow")["budget_status"], "UNKNOWN")
        context["budget"].pop("resource")
        result = search_directions(graph, context)
        self.assertEqual(result["blocked_candidates"][0]["rule_id"], "slow")
        self.assertEqual(result["candidates"][0]["budget_status"], "UNKNOWN")

    def test_explicit_unknown_resource_stays_unknown(self):
        graph = {"nodes": [node("root")], "edges": []}
        for resource in (None, [], {}, "UNKNOWN", " unknown ", "", "  ", 0, True):
            with self.subTest(resource=resource):
                cost = fact(2, resource=resource, unit="seconds", comparison_group="same-trial")
                candidate = search_directions(graph, self.context(costs={"root-test": cost}))["candidates"][0]
                self.assertEqual(candidate["incremental_cost"]["status"], "UNKNOWN")
                known = {**cost, "resource": "cpu"}
                candidate = search_directions(graph, self.context(costs={"root-test": known}, budget=cost))["candidates"][0]
                self.assertEqual(candidate["budget_status"], "UNKNOWN")

    def test_mixed_step_resources_cannot_be_summed(self):
        graph = {"nodes": [node("root", [{"fact": "matched", "value": True}])], "edges": []}
        costs = {"query:root:matched": fact(1, resource="cpu", unit="seconds", comparison_group="same-trial"),
                 "root-test": fact(2, resource="gpu", unit="seconds", comparison_group="same-trial")}
        context = self.context(costs=costs)
        self.assertEqual(search_directions(graph, context)["candidates"][0]["incremental_cost"]["status"], "UNKNOWN")
        costs["root-test"]["resource"] = "cpu"
        cost = search_directions(graph, context)["candidates"][0]["incremental_cost"]
        self.assertEqual((cost["value"], cost["resource"]), (3, "cpu"))
        costs["query:root:matched"].pop("resource")
        self.assertEqual(search_directions(graph, context)["candidates"][0]["incremental_cost"]["status"], "UNKNOWN")
        costs["root-test"].pop("resource")
        cost = search_directions(graph, context)["candidates"][0]["incremental_cost"]
        self.assertEqual(cost["value"], 3)
        self.assertNotIn("resource", cost)

    def test_input_preservation_no_implicit_seed_or_non_dependency_edge(self):
        graph = {"nodes": [node("check"), node("root")], "edges": [edge("check", "root", "qualified_by")]}
        context = self.context(facts={"check-done": fact(False)})
        original = copy.deepcopy((graph, context))
        result = search_directions(graph, context)
        self.assertEqual(result["candidates"][0]["status"], "READY")
        self.assertEqual((graph, context), original)
        self.assertNotIn("multi-seed", json.dumps(result))
        json.dumps(result, allow_nan=False)

    def test_real_graph_boundary_example_blocks_rho_one_crossing(self):
        graph = parse_simple_yaml((ROOT / "references/judgment-graph.yaml").read_text(encoding="utf-8-sig"))
        context = json.loads((ROOT / "examples/advisor-search/boundary-context.json").read_text(encoding="utf-8"))
        result = search_directions(graph, context)
        self.assertTrue(result["blocked_candidates"])
        fallback = result["candidates"][0]
        self.assertEqual(fallback["action"]["id"], "narrow-boundary-interpretation")
        self.assertIn("rho=1", fallback["action"]["description"])
        self.assertFalse(any(c["action"]["id"] == "inspect-actual-boundary-crossings" for c in result["candidates"]))
        context["facts"]["strict_crossing_feasible"]["value"] = True
        changed = search_directions(graph, context)
        self.assertEqual(changed["candidates"][0]["action"]["id"], "inspect-actual-boundary-crossings")

    def test_advisor_entry_preserves_existing_interface(self):
        graph = {"nodes": [node("root")], "edges": []}
        advisor = RDSAdvisor(ROOT)
        results = advisor.recommend_next_directions({"advisor_context": self.context()}, graph)
        search = next(r for r in results if r["type"] == "EXECUTABLE_DIRECTION_SEARCH")
        self.assertEqual(search["search"]["candidates"][0]["rule_id"], "root")
        self.assertTrue(any(r["type"] == "JUDGMENT_GRAPH_REVIEW" for r in results))


if __name__ == "__main__":
    unittest.main(verbosity=2)

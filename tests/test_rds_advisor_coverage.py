"""Complete declared graph analysis gates selection across Advisor entry points."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rds_advisor_search import search_directions
from rds_advisor import RDSAdvisor
from rds_project import digest
from rds_quick import choice
from rds_tms_store import current, save
import test_rds_advisor_search as search_fixture
import test_rds_experiments as composition_fixture
import test_rds_hypergraph_coverage as hypergraph_fixture
import test_rds_owned_advisor as owned_fixture
import test_rds_frontier as frontier_fixture
from unittest.mock import patch


def context(**extra):
    return {"decision": {"id": "choose", "goal_revision": "coverage-v1",
                         "scope": {"domain": "synthetic-software-acceptance"}},
            "facts": {}, **extra}


def advice(search):
    return {"recommendations": [{"type": "EXECUTABLE_DIRECTION_SEARCH", "search": search}]}


def first_search(report):
    return next(row["search"] for row in report["recommendations"]
                if row.get("type") == "EXECUTABLE_DIRECTION_SEARCH")


def disconnected_dependency_map():
    return hypergraph_fixture.graph(
        [hypergraph_fixture.node("healthy", "SUPPORTED"), hypergraph_fixture.node("a"),
         hypergraph_fixture.node("b"), hypergraph_fixture.node("c")],
        [hypergraph_fixture.edge("abc", ["a", "b"], "c")], ["healthy"], max_combinations=1)


class AdvisorCoverageTests(unittest.TestCase):
    def test_active_frontier_truncation_blocks_raw_search_choice(self):
        frontier = frontier_fixture.fixture()
        frontier['limits'] = {'max_nodes': 1}
        ctx = context(frontier=frontier)
        result = search_directions({'nodes': [search_fixture.node('root')], 'edges': []}, ctx)
        self.assertFalse(result['analysis_coverage']['full'])
        coverage = next(row for row in result['analysis_coverage']['graphs'] if row['kind'] == 'FRONTIER_GRAPH')
        self.assertEqual(coverage['node_count'], 3)
        self.assertEqual(coverage['input_sha256'], digest(frontier))
        self.assertIn('Frontier: node limit', coverage['reasons'])
        self.assertEqual(len(coverage['analyzed_nodes']), 3)
        self.assertEqual(result['candidates'][0]['status'], 'NEEDS_COMPLETE_ANALYSIS')
        with self.assertRaisesRegex(ValueError, 'Complete graph analysis required'):
            choice(advice(result), ctx)

    def test_frontier_cutoff_exclusions_remain_explicit_and_complete(self):
        frontier = frontier_fixture.fixture()
        frontier['nodes'].append(frontier_fixture.node('future', available_on='2001-01-01'))
        ctx = context(frontier=frontier)
        result = search_directions({'nodes': [search_fixture.node('root')], 'edges': []}, ctx)
        self.assertTrue(result['analysis_coverage']['full'])
        coverage = result['frontier_coverage']
        future = next(row for row in coverage['analyzed_nodes'] if row['id'] == 'future')
        self.assertEqual(future['disposition'], 'EXCLUDED_AT_CUTOFF')
        self.assertEqual(future['reason'], 'FUTURE_RECORD')
        self.assertEqual(choice(advice(result), ctx)['candidate']['id'], 'root:root-test')

    def test_disconnected_completion_is_analyzed_without_changing_action_readiness(self):
        root, disconnected = search_fixture.node("root"), search_fixture.node("disconnected")
        disconnected["executable"]["decisions"] = []
        ctx = context(facts={"disconnected-done": search_fixture.fact(False)})
        graph = {"nodes": [root, disconnected], "edges": []}
        original = deepcopy((graph, ctx))
        result = search_directions(graph, ctx)
        reports = {row["id"]: row for row in result["graph_coverage"]["analyzed_nodes"]}
        self.assertEqual(set(reports), {"root", "disconnected"})
        self.assertEqual(reports["disconnected"]["action_readiness"], "TRUE")
        self.assertEqual(reports["disconnected"]["satisfaction"], "FALSE")
        self.assertEqual(reports["root"]["satisfaction"], "UNKNOWN")
        self.assertTrue(result["analysis_coverage"]["full"])
        self.assertEqual(result["analysis_coverage"]["status"], "FULL")
        self.assertEqual(result["context_sha256"], digest(ctx))
        self.assertEqual(choice(advice(result), ctx)["candidate"]["id"], "root:root-test")
        self.assertEqual((graph, ctx), original)

    def test_malformed_late_node_is_validated_before_node_cap(self):
        graph = {"nodes": [search_fixture.node("root"), {"id": 7}], "edges": []}
        with self.assertRaisesRegex(ValueError, "unique nonempty string id"):
            search_directions(graph, context(), max_nodes=1)

    def test_missing_edge_endpoint_makes_global_analysis_incomplete(self):
        graph = {"nodes": [search_fixture.node("root")],
                 "edges": [search_fixture.edge("missing", "root")]}
        ctx = context()
        result = search_directions(graph, ctx)
        self.assertFalse(result["analysis_coverage"]["full"])
        self.assertEqual(result["analysis_coverage"]["status"], "INCOMPLETE")
        report = result["graph_coverage"]["analyzed_edges"][0]
        self.assertEqual(report["disposition"], "MISSING_ENDPOINT")
        self.assertIn("missing", report["reason"])
        with self.assertRaisesRegex(ValueError, "Complete graph analysis required"):
            choice(advice(result), ctx, "root:root-test")

    def test_descriptive_relation_is_classified_without_becoming_a_prerequisite(self):
        source, target = search_fixture.node("source"), search_fixture.node("target")
        source["executable"]["decisions"] = []
        ctx = context(facts={"source-done": search_fixture.fact(False)})
        graph = {"nodes": [source, target], "edges": [search_fixture.edge("source", "target", "related_to")]}
        result = search_directions(graph, ctx)
        edge_report = result["graph_coverage"]["analyzed_edges"][0]
        self.assertEqual(edge_report["disposition"], "DESCRIPTIVE_ONLY")
        self.assertIn("no executable inference", edge_report["reason"])
        self.assertTrue(result["analysis_coverage"]["full"])
        self.assertEqual(result["candidates"][0]["status"], "READY")
        self.assertFalse(any(row.get("step") == "dependency" for row in result["candidates"][0]["derivation"]))
        self.assertEqual(choice(advice(result), ctx)["candidate"]["action"]["id"], "target-test")

    def test_no_decision_still_analyzes_every_node(self):
        graph = {"nodes": [search_fixture.node("a"), search_fixture.node("b")], "edges": []}
        result = search_directions(graph, {"facts": {"b-done": search_fixture.fact(False)}})
        reports = {row["id"]: row for row in result["graph_coverage"]["analyzed_nodes"]}
        self.assertEqual(set(reports), {"a", "b"})
        self.assertEqual(reports["b"]["satisfaction"], "FALSE")
        self.assertEqual(result["candidates"], [])
        self.assertTrue(result["analysis_coverage"]["full"])

    def test_candidate_cap_marks_local_readiness_and_blocks_explicit_choice(self):
        graph = {"nodes": [search_fixture.node("a"), search_fixture.node("b")], "edges": []}
        ctx = context()
        result = search_directions(graph, ctx, max_candidates=1)
        self.assertEqual(result["truncation"]["limits"]["max_candidates"], 1)
        self.assertIn("candidate limit", result["truncation"]["reasons"])
        self.assertFalse(result["analysis_coverage"]["full"])
        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["status"], "NEEDS_COMPLETE_ANALYSIS")
        self.assertEqual(candidate["local_status"], "READY")
        self.assertIn("COMPLETE_GRAPH_ANALYSIS_REQUIRED", {row["kind"] for row in result["selection_review"]["flags"]})
        for candidate_id in (None, candidate["id"]):
            with self.subTest(candidate_id=candidate_id), self.assertRaisesRegex(ValueError, "Complete graph analysis required"):
                choice(advice(result), ctx, candidate_id)

    def test_composition_cap_blocks_otherwise_ready_direction(self):
        graph, ctx, templates = composition_fixture.fixture()
        ctx["decision"] = {"id": "mechanism-attribution", "goal_revision": "composition-v1",
                           "scope": {"domain": "synthetic-composition"}}
        result = search_directions(graph, ctx, templates=templates, max_combinations=1)
        self.assertTrue(result["experiment_composition"]["truncation"]["truncated"])
        self.assertFalse(result["analysis_coverage"]["full"])
        self.assertIn("Experiment composition is truncated", result["analysis_coverage"]["reasons"])
        self.assertTrue(result["candidates"])
        self.assertTrue(all(row["status"] == "NEEDS_COMPLETE_ANALYSIS" for row in result["candidates"]))
        with self.assertRaisesRegex(ValueError, "Complete graph analysis required"):
            choice(advice(result), ctx, result["candidates"][0]["id"])

    def test_disconnected_tms_truncation_blocks_locally_healthy_direction(self):
        ctx = context(dependency_map=disconnected_dependency_map())
        result = search_directions({"nodes": [search_fixture.node("root")], "edges": []}, ctx)
        dependency = result["selection_review"]["dependency_review"]
        self.assertTrue(dependency["goals"]["healthy"]["blocker_sets_complete"])
        self.assertEqual(dependency["goals"]["healthy"]["status"], "DECLARED_SUPPORTED")
        self.assertTrue(dependency["truncated"])
        self.assertEqual(set(dependency["node_analysis"]), {"healthy", "a", "b", "c"})
        self.assertFalse(result["analysis_coverage"]["full"])
        self.assertEqual(result["candidates"][0]["status"], "NEEDS_COMPLETE_ANALYSIS")
        with self.assertRaisesRegex(ValueError, "Complete graph analysis required"):
            choice(advice(result), ctx, "root:root-test")

    def test_changed_facts_or_map_reject_old_complete_context(self):
        dependency = hypergraph_fixture.graph([hypergraph_fixture.node("healthy", "SUPPORTED")], goals=["healthy"])
        ctx = context(dependency_map=dependency)
        result = search_directions({"nodes": [search_fixture.node("root")], "edges": []}, ctx)
        self.assertTrue(result["analysis_coverage"]["full"])
        self.assertEqual(choice(advice(result), deepcopy(ctx))["candidate"]["id"], "root:root-test")
        for changed in ({**ctx, "facts": {"root-done": search_fixture.fact(False)}},
                        {**ctx, "dependency_map": {**dependency, "nodes": dependency["nodes"] + [hypergraph_fixture.node("new")]}}):
            with self.subTest(context=changed), self.assertRaisesRegex(ValueError, "context changed"):
                choice(advice(result), changed, "root:root-test")


class AdvisorCoverageCLITests(unittest.TestCase):
    def fixture(self):
        case = owned_fixture.OwnedAdvisorCLITests("runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_owned_public_api_rejects_direction_subsets_and_collects_native_state(self):
        case = self.fixture()
        case.initialize()
        before = case.snapshot()
        graph = deepcopy(before['contract']['advisor_policy']['graph'])
        api = RDSAdvisor(case.root)
        supplied = {**before, 'advisor_context': context(facts={'caller-invented': search_fixture.fact(True)})}
        for changed in ({'nodes': graph['nodes'][:1], 'edges': []},
                        {**graph, 'nodes': [{**graph['nodes'][0], 'description': 'caller replacement'}, *graph['nodes'][1:]]}):
            with self.subTest(graph=changed), self.assertRaisesRegex(ValueError, 'complete frozen direction graph'):
                api.recommend_next_directions(supplied, changed)
        report = {'recommendations': api.recommend_next_directions(supplied, graph)}
        search = first_search(report)
        self.assertEqual({row['id'] for row in search['graph_coverage']['analyzed_nodes']},
                         {node['id'] for node in graph['nodes']})
        self.assertNotEqual(search['context_sha256'], digest(supplied['advisor_context']))
        self.assertTrue(search['analysis_coverage']['full'])
        self.assertEqual(case.starts(), [])
        after = case.snapshot()
        for key in ('budget', 'runs', 'receipts', 'contract_sha256'):
            self.assertEqual(after[key], before[key], key)

    def test_public_advisor_reuses_frontier_calculation_and_blocks_selection(self):
        case = self.fixture()
        case.initialize(include_policy=False)
        frontier = frontier_fixture.fixture()
        frontier['limits'] = {'max_nodes': 1}
        ctx = context(frontier=frontier)
        graph = {'nodes': [search_fixture.node('root')], 'edges': []}
        with patch('rds_frontier.discover_frontier', wraps=frontier_fixture.discover_frontier) as calculate:
            report = {'recommendations': RDSAdvisor(case.root).recommend_next_directions({'advisor_context': ctx}, graph)}
        self.assertEqual(calculate.call_count, 1)
        search = first_search(report)
        self.assertFalse(search['analysis_coverage']['full'])
        self.assertEqual(search['candidates'][0]['status'], 'NEEDS_COMPLETE_ANALYSIS')
        with self.assertRaisesRegex(ValueError, 'Complete graph analysis required'):
            choice(report, ctx)

    def test_owned_candidate_cap_refuses_advance_without_launch_or_budget_reset(self):
        case = self.fixture()

        def both_ready(policy):
            policy["context"]["max_candidates"] = 1
            policy["graph"]["nodes"][1]["executable"]["preconditions"] = []

        case.initialize(mutate_policy=both_ready)
        before = case.snapshot()
        reviewed = case.output("project", "next", status_codes=(2,))
        self.assertEqual(reviewed["status"], "INCOMPLETE_ANALYSIS")
        self.assertFalse(reviewed["analysis_coverage"]["full"])
        self.assertIsNone(reviewed["selected_run"])
        self.assertIsNone(reviewed["selected_manifest"])
        search = first_search(reviewed)
        self.assertEqual(search["truncation"]["limits"]["max_candidates"], 1)
        self.assertEqual(search["candidates"][0]["status"], "NEEDS_COMPLETE_ANALYSIS")
        advanced = case.output("project", "advance", status_codes=(2,))
        self.assertEqual(advanced["status"], "INCOMPLETE_ANALYSIS")
        self.assertIsNone(advanced["selected_manifest"])
        self.assertEqual(case.starts(), [])
        after = case.snapshot()
        for key in ("budget", "runs", "receipts", "contract_sha256"):
            self.assertEqual(after[key], before[key], key)

    def test_cli_injects_saved_full_tms_and_refuses_a_dropped_component(self):
        case = self.fixture()
        case.initialize(include_policy=False)
        dependency = disconnected_dependency_map()
        pinned = save(case.root, dependency, expected=None)
        ctx = context()
        ctx_path = case.write_json("coverage-context.json", ctx)
        graph_path = case.write_json("coverage-directions.json", {"nodes": [search_fixture.node("root")], "edges": []})
        report = case.output("advise", "--research-context", ctx_path, "--graph", graph_path)
        search = first_search(report)
        self.assertFalse(search["analysis_coverage"]["full"])
        self.assertEqual(set(search["selection_review"]["dependency_review"]["node_analysis"]), {"healthy", "a", "b", "c"})
        self.assertEqual(search["candidates"][0]["status"], "NEEDS_COMPLETE_ANALYSIS")
        ctx["dependency_map"] = hypergraph_fixture.graph([hypergraph_fixture.node("healthy", "SUPPORTED")], goals=["healthy"])
        case.write_json("coverage-context.json", ctx)
        rejected = case.call("advise", "--research-context", ctx_path, "--graph", graph_path, ok=False)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("complete current dependency graph", rejected.stderr)
        self.assertEqual(current(case.root)["sha256"], pinned)
        self.assertEqual(case.starts(), [])
        self.assertEqual(case.snapshot()["runs"], [])

    def test_direct_advisor_api_injects_saved_tms_and_rejects_subset(self):
        case = self.fixture()
        case.initialize(include_policy=False)
        dependency = disconnected_dependency_map()
        pinned = save(case.root, dependency, expected=None)
        ctx = context()
        state = {**case.snapshot(), "advisor_context": ctx}
        graph = {"nodes": [search_fixture.node("root")], "edges": []}
        result = {"recommendations": RDSAdvisor(case.root).recommend_next_directions(state, graph)}
        search = first_search(result)
        self.assertFalse(search["analysis_coverage"]["full"])
        self.assertEqual(set(search["selection_review"]["dependency_review"]["node_analysis"]), {"healthy", "a", "b", "c"})
        self.assertEqual(ctx, context())
        subset = hypergraph_fixture.graph([hypergraph_fixture.node("healthy", "SUPPORTED")], goals=["healthy"])
        with self.assertRaisesRegex(ValueError, "complete current dependency graph"):
            RDSAdvisor(case.root).recommend_next_directions({**state, "advisor_context": {**ctx, "dependency_map": subset}}, graph)
        self.assertEqual(current(case.root)["sha256"], pinned)
        self.assertEqual(case.starts(), [])


if __name__ == "__main__":
    unittest.main()

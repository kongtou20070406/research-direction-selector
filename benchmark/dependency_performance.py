"""Matched synthetic dependency/Advisor timings against a trusted local commit.

python -B benchmark/dependency_performance.py --baseline <full-commit-sha>
No wall-time assertion, research job, scientific score or external model call.
"""
import argparse
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_advisor
import rds_advisor_search
import rds_experiments
import rds_hypergraph

NAMES = ("rds_hypergraph_blockers", "rds_hypergraph", "rds_advisor_search", "rds_experiments", "rds_advisor")


def load_baseline(ref):
    """Bind the selected sources, including the optional historical blocker helper."""
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise ValueError("baseline must be a full lowercase commit SHA from trusted local history")
    modules, hashes = {}, {}
    helper = subprocess.check_output(
        ["git", "ls-tree", "--name-only", ref, "scripts/rds_hypergraph_blockers.py"], cwd=ROOT)
    for name in NAMES:
        if name == "rds_hypergraph_blockers" and not helper.strip():
            # Old revisions have no helper; any unexpected import must fail,
            # never resolve to the candidate already loaded in this process.
            modules[name], hashes[name] = None, None
            continue
        raw = subprocess.check_output(["git", "show", f"{ref}:scripts/{name}.py"], cwd=ROOT)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("baseline module exceeds 2 MiB")
        module = types.ModuleType(name)
        module.__file__ = str(ROOT / "scripts" / (name + ".py"))
        with patch.dict(sys.modules, modules):
            exec(compile(raw, module.__file__, "exec"), module.__dict__)
        modules[name] = module
        hashes[name] = hashlib.sha256(raw).hexdigest()
    return modules, hashes


def paired(before, after, baseline_modules, repeats, inner):
    samples = [[], []]
    with patch.dict(sys.modules, baseline_modules):
        expected = before()
    if after() != expected:
        raise AssertionError("full output differs from baseline")
    for iteration in range(repeats):
        for side in (iteration % 2, 1 - iteration % 2):
            fn = (before, after)[side]
            scope = patch.dict(sys.modules, baseline_modules) if side == 0 else nullcontext()
            with scope:
                start = time.perf_counter()
                for _ in range(inner):
                    last = fn()
                samples[side].append((time.perf_counter() - start) * 1000 / inner)
            if last != expected:
                raise AssertionError("sample output differs from baseline")
    medians = [statistics.median(values) for values in samples]
    return {"full_output_parity": True, "calls_per_sample": inner,
            "before_ms": {"median": medians[0], "samples": samples[0]},
            "after_ms": {"median": medians[1], "samples": samples[1]},
            "speedup": medians[0] / medians[1]}


def chains(count, length, reverse=False):
    nodes, edges, goals = [], [], []
    for chain in range(count):
        for index in range(length + 1):
            name = f"c{chain}-{index}"
            nodes.append({"id": name, "status": "SUPPORTED" if index == 0 else "UNKNOWN",
                          "source": "synthetic timing fixture"})
            if index:
                edges.append({"id": "e" + name, "premises": [f"c{chain}-{index-1}"],
                              "conclusion": name, "status": "SUPPORTED", "source": "synthetic timing fixture"})
        goals.append(f"c{chain}-{length}")
    if reverse:
        edges.reverse()
    return {"schema": 1, "nodes": nodes, "hyperedges": edges, "goals": goals,
            "limits": {"max_nodes": 1024, "max_hyperedges": 1024}}


def advisor_fixture(dependency=None):
    if dependency is None:
        dependency = json.loads((ROOT / "examples/goal-linked-hypergraph.json").read_text(encoding="utf-8"))
    large = len(dependency["nodes"]) > 7
    action = {"kind": "OBLIGATION_CHECK", "target": "unrestricted_lower",
              "claim": "synthetic complete-domain lower bound", "required_observables": ["certificate"],
              "outcomes": [{"observation": outcome, "next_decision": outcome}
                           for outcome in ("verified", "counterexample", "unresolved")],
              "goal_contribution": {"target": "completion_standard",
                                    "path": ["unrestricted_lower", "completion_standard"],
                                    "source": "synthetic timing fixture"}}
    if large:
        action["target"] = "c0-24"
        action["goal_contribution"].update(target="c0-25", path=["c0-24", "c0-25"])
    graph = {"nodes": [{"id": f"route-{i}", "executable": {"decisions": ["next"],
                        "preconditions": [], "action": {**deepcopy(action), "id": f"action-{i}"}}}
                       for i in range(3)], "edges": []}
    context = {"research_mode": "theory", "decision": {"id": "next", "goal_revision": "1",
                "scope": {"domain": "synthetic"}}, "facts": {}, "dependency_map": dependency}
    return graph, {"advisor_context": context}


def analysis_calls(fn, module, scope):
    with scope, patch.object(module, "analyze_hypergraph", wraps=module.analyze_hypergraph) as analyzer:
        fn()
        return analyzer.call_count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="Trusted local before-revision; its Python sources execute")
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 30:
        parser.error("--repeats must be 1..30")
    baseline, source_hashes = load_baseline(args.baseline)
    result = {"environment": {"python": platform.python_version(), "platform": platform.platform()},
              "baseline_commit": args.baseline, "baseline_source_sha256": source_hashes,
              "current_source_sha256": {name: hashlib.sha256((ROOT / "scripts" / (name + ".py")).read_bytes()).hexdigest()
                                        for name in NAMES}, "repeats": args.repeats, "workloads": {}}
    workloads = {"public_7_nodes": json.loads((ROOT / "examples/goal-linked-hypergraph.json").read_text()),
                 "ordered_520_nodes": chains(20, 25), "reverse_520_nodes": chains(20, 25, True),
                 "ordered_513_node_chain": chains(1, 512), "reverse_513_node_chain": chains(1, 512, True)}
    for label, spec in workloads.items():
        result["workloads"][label] = {"nodes": len(spec["nodes"]), "edges": len(spec["hyperedges"]),
            **paired(lambda: baseline["rds_hypergraph"].analyze_hypergraph(spec),
                     lambda: rds_hypergraph.analyze_hypergraph(spec), baseline, args.repeats, 10)}
    with tempfile.TemporaryDirectory(prefix="rds-performance-") as directory:
        old = baseline["rds_advisor"].RDSAdvisor.__new__(baseline["rds_advisor"].RDSAdvisor)
        current = rds_advisor.RDSAdvisor.__new__(rds_advisor.RDSAdvisor)
        old.root_dir = current.root_dir = Path(directory)
        for label, dependency, templates in (
                ("advisor_3_candidates", None, None),
                ("advisor_with_empty_templates", None, {"schema": 1, "templates": []}),
                ("advisor_520_nodes", chains(20, 25), None),
                ("advisor_520_nodes_with_empty_templates", chains(20, 25), {"schema": 1, "templates": []})):
            graph, state = advisor_fixture(dependency)
            if templates is not None:
                state["advisor_templates"] = templates
            original = deepcopy((graph, state))
            before = lambda: json.dumps(old.recommend_next_directions(state, graph), sort_keys=True, separators=(",", ":"))
            after = lambda: json.dumps(current.recommend_next_directions(state, graph), sort_keys=True, separators=(",", ":"))
            row = paired(before, after, baseline, args.repeats, 4 if dependency else 25)
            row["includes_json_serialization"] = True
            row["full_output_bytes"] = len(after().encode())
            row["analysis_calls"] = {"before": analysis_calls(before, baseline["rds_hypergraph"], patch.dict(sys.modules, baseline)),
                                     "after": analysis_calls(after, rds_hypergraph, nullcontext())}
            result["workloads"][label] = row
            if (graph, state) != original:
                raise AssertionError("Advisor timing mutated its graph or context")
    result["limitations"] = ["Synthetic graphs, local warmed in-process calls; not an end-to-end model or scientific-policy speedup.",
                             "Source loading and result equality checks are outside timing; validation, hashing and traversal are inside.",
                             "The selected local baseline is trusted code, not imported untrusted research data."]
    raw = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(raw + "\n", encoding="utf-8")
    print(raw)


if __name__ == "__main__":
    main()

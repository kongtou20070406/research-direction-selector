"""Read Python syntax, never import the inspected code. JSON goes to stdout."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys


# Responsibility labels describe the current mixed modules, not compliance.
GROUPS = {
    "L0": "bounded_io mutation",
    "L1": "project project_hooks campaign workspace costs checkpoints math method_revision state cas "
          "project_read_port contract_read artifacts owned_history source_documents probe "
          "tool_applicability project_lifecycle "
          "steering tools tool_compare tool_workbench tool_application tool_calls owned_tools "
          "tms_store proof_cache rsi_confirmation postcommit_confirmation blind_runner "
          "verify verify_registry builtin_verifiers formal_kernel lean_verify scalar_verify "
          "matrix_verify dynamics_verify nn_verify tensor_verify scale_verify statistical_verify "
          "disk_cover_verify rational_voronoi_verify affine_toolchain theory_progression "
          "continuous_confirmation polynomial_confirmation domain_confirmation advancement guard",
    "L2": "hypergraph hypergraph_blockers hypergraph_input bus_adapter accelerator trajectory "
          "context_compact compress dynamics_probe discrimination",
    "L3": "advisor advisor_coverage advisor_search advisor_workset owned_advisor autonomy "
          "continuation experiments feasibility frontier frontier_proposals global_planning graph_ranker "
          "jump jump_search math_router meta methods operators resources result_tools rsi "
          "adversary search_allocation search_neighborhood structure theory_tools",
    "L4": "cli quick project_worker autonomy_worker project_assembly hypergraph_archive "
          "broker_hook host_broker host_hook conversation_hook dashboard dialogue "
          "hypergraph_readable hypergraph_view obelisk session capabilities model_export "
          "outputs research_note usage wheel",
    "SHARED": "core project_values reference_values admission_values project_records run_records verify_types "
              "unit_disk_voronoi_core",
}
LAYERS = {"rds_" + name: layer for layer, names in GROUPS.items() for name in names.split()}
LAYERS.update(build_plugins="L4", update_lean_toolchain="L4")
LOADERS = {"importlib.import_module", "__import__", "builtins.__import__"}
OPAQUE_LOADERS = {"importlib.util.spec_from_file_location", "importlib.util.module_from_spec",
                  "importlib.machinery.SourceFileLoader", "runpy.run_path", "runpy.run_module"}


def qualified(node, resolve):
    if isinstance(node, ast.Name):
        return resolve(node.id)
    if isinstance(node, ast.Attribute):
        return {name + "." + node.attr for name in qualified(node.value, resolve)}
    return {ast.unparse(node)}


def lexical_resolver(tree, parents):
    comprehensions = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    scopes = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda) + comprehensions
    bindings = {node: {} for node in ast.walk(tree) if isinstance(node, scopes)}

    def enclosing(node):
        child, parent = node, parents.get(node)
        while parent is not None:
            if isinstance(parent, scopes):
                if isinstance(parent, comprehensions):
                    # Only the first iterable is evaluated in the enclosing scope.
                    first_iter = parent.generators[0].iter
                    if node is not first_iter and first_iter not in ancestors(node, parent):
                        return parent
                    child, parent = parent, parents.get(parent)
                    continue
                body = getattr(parent, 'body', [])
                inside_body = child is body if isinstance(parent, ast.Lambda) else child in body
                # Defaults, annotations, decorators and class bases use the outer scope.
                if isinstance(parent, ast.Module) or inside_body:
                    return parent
            child, parent = parent, parents.get(parent)
        return None

    def ancestors(node, stop):
        while node in parents and node is not stop:
            node = parents[node]
            yield node

    def bind(scope, name, value):
        bindings[scope].setdefault(name, set()).add(value)

    for node in ast.walk(tree):
        scope = enclosing(node)
        if isinstance(node, ast.Import):
            for alias in node.names:
                bind(scope, alias.asname or alias.name.split('.')[0],
                     alias.name if alias.asname else alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bind(scope, alias.asname or alias.name,
                     (node.module or '') + '.' + alias.name if not node.level else '!relative')
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if isinstance(parents.get(node), ast.NamedExpr):
                while isinstance(scope, comprehensions):
                    scope = enclosing(scope)
            bind(scope, node.id, '!local')
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bind(scope, node.name, '!exception')
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            bind(scope, node.name, '!pattern')
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bind(scope, node.rest, '!pattern')
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Keep module class constructors distinct from shadowing local names.
            value = '!module_class.' + node.name if isinstance(node, ast.ClassDef) and scope is tree else '!local'
            bind(scope, node.name, value)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            for arg in args + [a for a in (node.args.vararg, node.args.kwarg) if a]:
                bind(node, arg.arg, '!parameter')

    def at(node, name):
        scope = enclosing(node)
        first, candidates = True, set()
        while scope is not None:
            # A method does not close over its class namespace.
            if first or not isinstance(scope, ast.ClassDef):
                wildcards = bindings[scope].get('*', set())
                if wildcards:
                    # Exports and competing star imports are not statically known.
                    candidates.update(value[:-1] + name if value.endswith('.*') else value
                                      for value in wildcards)
                    candidates.add('!wildcard')
                if name in bindings[scope]:
                    candidates.update(bindings[scope][name])
                    # Class assignments are ordered, not function-wide lexical locals.
                    # Keep outer alternatives instead of silently losing possible imports.
                    if not isinstance(scope, ast.ClassDef):
                        return candidates
            first = False
            scope = enclosing(scope)
        return candidates | {name}
    return at


def dict_targets(tree):
    tables = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            if all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        tables[target.id] = value
    return tables


def call_argument(node, position, keyword):
    if len(node.args) > position:
        return node.args[position]
    return next((item.value for item in node.keywords if item.arg == keyword), None)


def scan(name, tree, modules, package, hook_routes):
    tables = dict_targets(tree)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    resolve = lexical_resolver(tree, parents)
    edges, unresolved, references = [], [], []

    def site(node):
        scopes, current = [], node
        while current in parents:
            current = parents[current]
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scopes.append(current.name)
        return {"source": name, "line": node.lineno, "scope": ".".join(reversed(scopes)),
                "expression": ast.unparse(node)}

    def emit(node, target, kind):
        if target in modules:
            edges.append({**site(node), "target": target, "kind": kind})
        elif target.split(".")[0] in modules:
            unresolved.append({**site(node), "reason": "local dotted target not found", "target": target})

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                emit(node, alias.name, "static")
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                if node.level > len(parts):
                    unresolved.append({**site(node), "reason": "relative import outside known package"})
                    continue
                base = ".".join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
            emit(node, base, "static")
            for alias in node.names:
                member = base + "." + alias.name
                if member in modules:
                    emit(node, member, "static")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in modules:
            references.append({**site(node), "target": node.value})
        elif isinstance(node, ast.Call):
            callees = qualified(node.func, lambda symbol: resolve(node, symbol))
            proof_rules = {"rds_verify_registry.ProofRule", "!module_class.ProofRule"}
            if name == "rds_verify" and callees & proof_rules and len(callees) > 1:
                unresolved.append({**site(node), "reason": "ambiguous plugin binding"})
                continue
            if name == "rds_verify" and "ProofRule" in callees:
                unresolved.append({**site(node), "reason": "unresolved plugin binding"})
                continue
            known = LOADERS | OPAQUE_LOADERS | {"rds_project_hooks.provider"}
            if len(callees) > 1 and (callees & known or
                                    any(c == '!wildcard' or c.startswith('!wildcard.') for c in callees)):
                unresolved.append({**site(node), "reason": "ambiguous loader binding"})
                continue
            callee = next(iter(callees)) if len(callees) == 1 else ""
            if name == "rds_verify" and callee in proof_rules:
                module = call_argument(node, 2, "module")
                if isinstance(module, ast.Constant) and isinstance(module.value, str):
                    emit(node, module.value, "declared_plugin_candidate")
                else:
                    unresolved.append({**site(node), "reason": "runtime plugin declaration"})
            elif callee == "rds_project_hooks.provider":
                arg = call_argument(node, 0, "name")
                key = arg.value if isinstance(arg, ast.Constant) else None
                if isinstance(key, str) and key in hook_routes:
                    emit(node, hook_routes[key], "provider_callback")
                else:
                    unresolved.append({**site(node), "reason": "runtime provider selection"})
            elif callee in LOADERS:
                arg = call_argument(node, 0, "name")
                level = next((k.value for k in node.keywords if k.arg == "level"),
                             node.args[4] if len(node.args) > 4 else ast.Constant(value=0))
                if callee.endswith("__import__") and (any(k.arg is None for k in node.keywords) or
                        not (isinstance(level, ast.Constant) and type(level.value) is int and level.value == 0)):
                    unresolved.append({**site(node), "reason": "relative or unknown import level"})
                    continue
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and not arg.value.startswith("."):
                    emit(node, arg.value, "dynamic_literal")
                else:
                    table = tables.get(arg.value.id, {}) if isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name) else {}
                    for target in sorted(set(table.values())):
                        emit(node, target, "dynamic_registry_candidate")
                    unresolved.append({**site(node), "reason": "runtime import expression",
                                       "candidates": sorted(set(table.values()))})
            elif callee in OPAQUE_LOADERS or callee.endswith(".exec_module"):
                unresolved.append({**site(node), "reason": "opaque code loader"})
    return edges, unresolved, references


def cycles(nodes, pairs):
    adjacency = {name: set() for name in nodes}
    for source, target in pairs:
        adjacency[source].add(target)
    index, low, stack, active, groups = {}, {}, [], set(), []

    def visit(node):
        index[node] = low[node] = len(index)
        stack.append(node)
        active.add(node)
        for target in sorted(adjacency[node]):
            if target not in index:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], index[target])
        if low[node] == index[node]:
            group = []
            while True:
                target = stack.pop()
                active.remove(target)
                group.append(target)
                if target == node:
                    break
            if len(group) > 1 or node in adjacency[node]:
                groups.append(sorted(group))
    for node in sorted(nodes):
        if node not in index:
            visit(node)
    return sorted(groups)


def inventory(root):
    root = Path(root)
    trees, modules = {}, {}
    for path in sorted((root / "scripts").rglob("*.py")):
        relative = path.relative_to(root / "scripts")
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        name = ".".join(parts)
        raw = path.read_bytes()
        trees[name] = ast.parse(raw, filename=str(path))
        modules[name] = {"path": path.relative_to(root).as_posix(),
                         "sha256": hashlib.sha256(raw).hexdigest(), "layer": LAYERS.get(name, "UNKNOWN"),
                         "package": name if path.name == "__init__.py" else name.rpartition(".")[0]}
    if not modules:
        raise ValueError("no Python sources found under scripts/")
    hooks = dict_targets(trees["rds_project_hooks"]).get("_modules", {}) if "rds_project_hooks" in trees else {}
    edges, unresolved, references = [], [], []
    for name, tree in trees.items():
        e, u, r = scan(name, tree, modules, modules[name]["package"], hooks)
        edges.extend(e)
        unresolved.extend(u)
        references.extend(r)
    key = lambda item: (item["source"], item["line"], item.get("target", ""), item.get("kind", ""))
    edges.sort(key=key)
    pairs = {(e["source"], e["target"]) for e in edges}
    upward, unclassified = [], []
    for edge in edges:
        a, b = (modules[edge[k]]["layer"] for k in ("source", "target"))
        if "UNKNOWN" in (a, b):
            unclassified.append(edge)
        elif b != "SHARED" and (a == "SHARED" or a < b):
            upward.append(edge)
    digest_input = "".join(f"{n}\0{m['sha256']}\n" for n, m in sorted(modules.items()))
    return {"schema_version": 1, "source_sha256": hashlib.sha256(digest_input.encode()).hexdigest(),
            "modules": modules, "edges": edges, "upward": upward,
            "unclassified_edges": unclassified, "unresolved": sorted(unresolved, key=key),
            "module_string_references": sorted(references, key=key),
            "cycles": cycles(modules, pairs),
            "mutual_pairs": sorted([a, b] for a, b in pairs if a < b and (b, a) in pairs),
            "counts": {"modules": len(modules), "edges": len(pairs), "sites": len(edges),
                       "upward_edges": len({(e['source'], e['target']) for e in upward}),
                       "unresolved": len(unresolved)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = inventory(args.root)
    except (OSError, SyntaxError, ValueError) as error:
        parser.exit(2, f"Inventory failed: {error}\n")
    json.dump(result, sys.stdout, indent=2, sort_keys=True, ensure_ascii=True)
    print()


if __name__ == "__main__":
    main()

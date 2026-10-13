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
    "L1": "project project_hooks campaign workspace costs checkpoints math method_revision state cas quick_ledger result_record "
          "project_read_port project_read_services admission_read contract_read artifacts owned_history source_documents probe "
          "outbox tool_applicability project_lifecycle "
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
    "L4": "app cli quick project_worker autonomy_worker project_assembly hypergraph_archive hypergraph_render "
          "broker_hook host_broker host_hook conversation_hook dashboard dialogue "
          "hypergraph_readable hypergraph_view obelisk session capabilities model_export "
          "outputs research_note usage wheel",
    "SHARED": "core project_values reference_values output_values admission_values feasibility_values scalar_values project_records run_records verify_types "
              "plugin_registry unit_disk_voronoi_core",
}
LAYERS = {"rds_" + name: layer for layer, names in GROUPS.items() for name in names.split()}
LAYERS.update(build_plugins="L4", update_lean_toolchain="L4")
LAYERS.update(
    rds_research_import="L1",
    rds_decision_packet="L2",
    rds_packet_graph="L2",
    rds_research_advisor="L3",
    rds_transition="L2",
    rds_research_policy="L3",
    rds_offline_contract_values="SHARED",
    rds_retrieval_contract="L3",
    rds_predictor_contract="L3",
    rds_transition_dataset_contract="L2",
    rds_predictor_worker="L1",
    rds_code_diff="SHARED",
    rds_cpu_jepa="SHARED",
)
LOADERS = {"importlib.import_module", "__import__", "builtins.__import__", "runpy.run_module"}
OPAQUE_LOADERS = {"importlib.util.spec_from_file_location", "importlib.util.module_from_spec",
                  "importlib.machinery.SourceFileLoader", "runpy.run_path"}


def qualified(node, resolve):
    if isinstance(node, ast.Name):
        return resolve(node.id)
    if isinstance(node, ast.Attribute):
        return {name + "." + node.attr for name in qualified(node.value, resolve)}
    return {ast.unparse(node)}


def binding_key(node):
    return f'!store.{node.lineno}:{node.col_offset}'


def import_base(node, package):
    base = node.module or ''
    if not node.level:
        return base
    parts = package.split('.') if package else []
    if node.level > len(parts):
        return None
    return '.'.join(parts[:len(parts) - node.level + 1] + ([base] if base else []))


def local_name(name, modules):
    """The inventory is rooted below scripts/, unless scripts is a local package."""
    if name.startswith('scripts.') and 'scripts' not in modules:
        parts = name.removeprefix('scripts.').split('.')
        if any('.'.join(parts[:length]) in modules for length in range(1, len(parts) + 1)):
            return '.'.join(parts)
    return name


def lexical_resolver(tree, parents, package):
    comprehensions = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    scopes = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda) + comprehensions
    bindings = {node: {} for node in ast.walk(tree) if isinstance(node, scopes)}
    ordered_sites = {node: [] for node in bindings if isinstance(node, (ast.Module, ast.ClassDef))}
    literal_tables = dict_targets(tree, binding_keys=True)

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

    def bind(scope, name, value, origin):
        bindings[scope].setdefault(name, set()).add(value)
        if scope in ordered_sites:
            ordered_sites[scope].append((origin, name, value))

    for node in ast.walk(tree):
        scope = enclosing(node)
        if isinstance(node, ast.Import):
            for alias in node.names:
                bind(scope, alias.asname or alias.name.split('.')[0],
                     alias.name if alias.asname else alias.name.split('.')[0], node)
        elif isinstance(node, ast.ImportFrom):
            base = import_base(node, package)
            for alias in node.names:
                bind(scope, alias.asname or alias.name,
                     base + '.' + alias.name if base is not None else '!relative', node)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            declaration = parents.get(node)
            if (scope in ordered_sites and isinstance(declaration, ast.AnnAssign)
                    and declaration.target is node and declaration.value is None):
                continue  # Bare annotations do not assign module/class values.
            if isinstance(parents.get(node), ast.NamedExpr):
                while isinstance(scope, comprehensions):
                    scope = enclosing(scope)
            bind(scope, node.id, binding_key(node), node)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bind(scope, node.name, '!exception', node)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            bind(scope, node.name, '!pattern', node)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bind(scope, node.rest, '!pattern', node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Keep module class constructors distinct from shadowing local names.
            value = '!module_class.' + node.name if isinstance(node, ast.ClassDef) and scope is tree else '!local'
            bind(scope, node.name, value, node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            for arg in args + [a for a in (node.args.vararg, node.args.kwarg) if a]:
                bind(node, arg.arg, '!parameter', arg)

    def preceding_binding(scope, node, name):
        call_ancestors = set(ancestors(node, scope)) | {node}
        loops = {parent for parent in ancestors(node, scope)
                 if isinstance(parent, (ast.For, ast.AsyncFor, ast.While) + comprehensions)
                 and not (isinstance(parent, (ast.For, ast.AsyncFor)) and parent.iter in call_ancestors)
                 and not (isinstance(parent, comprehensions)
                          and parent.generators[0].iter in call_ancestors)}
        if any(bound in (name, '*') and loops.intersection(ancestors(origin, scope))
               for origin, bound, _ in ordered_sites[scope]):
            # Filters precede results; later stores can also run in an earlier iteration.
            return None
        value = ('__import__' if name == '__import__' else '!unbound.' + name) if scope is tree else None
        position = lambda item: (item.lineno, item.col_offset)
        for origin, bound, target in sorted(ordered_sites[scope], key=lambda item: position(item[0])):
            if bound not in (name, '*') or position(origin) >= position(node):
                continue
            if origin in call_ancestors and isinstance(origin, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # Defaults/bases finish before the definition replaces its name.
            if isinstance(origin, ast.ExceptHandler) and origin.type in call_ancestors:
                continue  # Matching the exception type precedes binding its alias.
            if isinstance(origin, ast.Name):
                if any(isinstance(parent, comprehensions)
                       and parent.generators[0].iter in call_ancestors
                       for parent in ancestors(origin, scope)):
                    continue  # The first iterable precedes comprehension-body stores.
                loop = next((parent for parent in ancestors(origin, scope)
                             if isinstance(parent, (ast.For, ast.AsyncFor))), None)
                if loop is not None and loop.iter in call_ancestors:
                    continue  # The iterable is evaluated before the first target store.
                assignment = next((parent for parent in ancestors(origin, scope) if isinstance(
                    parent, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr))), None)
                if assignment is not None and assignment.value in call_ancestors:
                    continue  # Only this target's own RHS delays its store.
            # Completed module classes also establish declared constructor identities.
            statement = parents[origin] if target in literal_tables else origin
            direct = parents[statement] is scope and (
                isinstance(statement, (ast.Import, ast.ImportFrom)) or target in literal_tables
                or (scope is tree and isinstance(statement, ast.ClassDef)))
            completed = (statement.end_lineno, statement.end_col_offset) <= position(node)
            value = target if direct and completed and bound == name else None
        return value

    def at(node, name):
        scope = enclosing(node)
        first, immediate, candidates = True, True, set()
        while scope is not None:
            # A method does not close over its class namespace.
            if first or not isinstance(scope, ast.ClassDef):
                # Deferred function bodies must not resolve globals at definition time.
                if isinstance(scope, ast.ClassDef) or (immediate and scope is tree):
                    imported = preceding_binding(scope, node, name)
                    if imported is not None:
                        return candidates | {imported}
                wildcards = bindings[scope].get('*', set())
                if wildcards:
                    # Exports and competing star imports are not statically known.
                    candidates.update(value[:-1] + name if value.endswith('.*') else value
                                      for value in wildcards)
                    candidates.add('!wildcard')
                if name in bindings[scope]:
                    candidates.update(bindings[scope][name])
                    if scope is tree and name == '__import__':
                        # Before assignment (or after deletion), globals may use
                        # the builtin; a deferred call has no known execution time.
                        candidates.add('__import__')
                    # Class assignments are ordered, not function-wide lexical locals.
                    # Keep outer alternatives instead of silently losing possible imports.
                    if not isinstance(scope, ast.ClassDef):
                        return candidates
            if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.GeneratorExp)):
                immediate = False
            first = False
            scope = enclosing(scope)
        return candidates | {name}
    return at


def dict_targets(tree, *, binding_keys=False):
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
                        tables[binding_key(target) if binding_keys else target.id] = value
    return tables


def call_argument(node, position, keyword):
    if len(node.args) > position:
        return node.args[position]
    return next((item.value for item in node.keywords if item.arg == keyword), None)


def named_exports(trees, modules):
    """Possible module-level import aliases, not verified runtime exports."""
    exports = {}
    for name, tree in trees.items():
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                parent = parents.get(parent)
            if parent is not None:
                continue
            base = import_base(node, modules[name]['package']) if isinstance(node, ast.ImportFrom) else ''
            if base is None:
                continue
            for alias in node.names:
                if alias.name == '*':
                    continue
                target = base + '.' + alias.name if isinstance(node, ast.ImportFrom) else (
                    alias.name if alias.asname else alias.name.split('.')[0])
                bound = alias.asname or (alias.name if isinstance(node, ast.ImportFrom) else alias.name.split('.')[0])
                exports.setdefault(name + '.' + bound, set()).add(local_name(target, modules))
    return exports


def possible_loader_reexport(name, exports):
    pending, steps = [(name, frozenset())], 0
    known = LOADERS | OPAQUE_LOADERS | {'rds_project_hooks.provider'}
    while pending and steps < 64:
        current, trail = pending.pop()
        steps += 1
        if current in known or current in trail:
            return True
        parts = current.split('.')
        for length in range(len(parts), 0, -1):
            prefix = '.'.join(parts[:length])
            if prefix in exports:
                suffix = current[len(prefix):]
                pending.extend((target + suffix, trail | {current}) for target in sorted(exports[prefix])
                               if target + suffix != current)
                break
    return bool(pending)  # Cycles or the bounded walk never certify a non-loader.


def non_executed_annotations(tree, parents):
    """Python 3.11-3.13 annotation syntax; reflective evaluation is not inferred."""
    postponed = any(isinstance(node, ast.ImportFrom) and node.module == '__future__'
                    and any(alias.name == 'annotations' for alias in node.names) for node in tree.body)
    ignored = set()
    for node in ast.walk(tree):
        annotation = None
        if postponed and isinstance(node, ast.arg):
            annotation = node.annotation
        elif postponed and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotation = node.returns
        elif isinstance(node, ast.AnnAssign):
            scope = parents.get(node)
            while scope is not None and not isinstance(scope, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                                               ast.AsyncFunctionDef, ast.Lambda)):
                scope = parents.get(scope)
            if postponed or isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                annotation = node.annotation
        if annotation is not None:
            ignored.update(ast.walk(annotation))
    return ignored


def scan(name, tree, modules, package, hook_routes, exports):
    tables = dict_targets(tree, binding_keys=True)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    annotations = non_executed_annotations(tree, parents)
    resolve = lexical_resolver(tree, parents, package)
    edges, unresolved, references = [], [], []
    emitted = {}

    def site(node):
        scopes, current = [], node
        while current in parents:
            current = parents[current]
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scopes.append(current.name)
        return {"source": name, "line": node.lineno, "scope": ".".join(reversed(scopes)),
                "expression": ast.unparse(node)}

    def record(node, target, kind):
        kinds = emitted.setdefault((node, target), set())
        if kind not in kinds:
            edges.append({**site(node), 'target': target, 'kind': kind})
            kinds.add(kind)

    def emit(node, target, kind):
        original = target
        target = local_name(target, modules)
        parts = target.split('.')
        for length in range(1, len(parts)):
            prefix = '.'.join(parts[:length])
            if (prefix != name and prefix in modules
                    and modules[prefix]['path'].endswith('/__init__.py')
                    and (node, prefix) not in emitted):
                record(node, prefix, kind)
        if target in modules:
            record(node, target, kind)
        elif target.split(".")[0] in modules or (original.startswith('scripts.') and 'scripts' not in modules):
            unresolved.append({**site(node), "reason": "local dotted target not found", "target": target})

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                emit(node, alias.name, "static")
        elif isinstance(node, ast.ImportFrom):
            base = import_base(node, package)
            if base is None:
                unresolved.append({**site(node), "reason": "relative import outside known package"})
                continue
            if base != name or node.module or not node.level:
                emit(node, base, "static")
            for alias in node.names:
                member = base + "." + alias.name
                if (local_name(member, modules) in modules
                        or (base == 'scripts' and 'scripts' not in modules and alias.name != '*')):
                    emit(node, member, "static")
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and local_name(node.value, modules) in modules):
            references.append({**site(node), "target": local_name(node.value, modules)})
        elif isinstance(node, ast.Call):
            if node in annotations:
                continue
            callees = {local_name(callee, modules) for callee in
                       qualified(node.func, lambda symbol: resolve(node, symbol))}
            proof_rules = {"rds_verify_registry.ProofRule", "!module_class.ProofRule"}
            if name == "rds_verify" and callees & proof_rules and len(callees) > 1:
                unresolved.append({**site(node), "reason": "ambiguous plugin binding"})
                continue
            if name == "rds_verify" and callees & {"ProofRule", "!unbound.ProofRule"}:
                unresolved.append({**site(node), "reason": "unresolved plugin binding"})
                continue
            known = LOADERS | OPAQUE_LOADERS | {"rds_project_hooks.provider"}
            if len(callees) > 1 and (callees & known or
                                    any(c == '!wildcard' or c.startswith('!wildcard.') for c in callees)):
                unresolved.append({**site(node), "reason": "ambiguous loader binding"})
                continue
            if any(callee not in known and possible_loader_reexport(callee, exports) for callee in callees):
                unresolved.append({**site(node), 'reason': 'local loader re-export'})
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
                arg = call_argument(node, 0, "mod_name" if callee == "runpy.run_module" else "name")
                level = next((k.value for k in node.keywords if k.arg == "level"),
                             node.args[4] if len(node.args) > 4 else ast.Constant(value=0))
                if callee.endswith("__import__") and (any(k.arg is None for k in node.keywords) or
                        any(isinstance(arg, ast.Starred) for arg in node.args) or
                        not (isinstance(level, ast.Constant) and type(level.value) is int and level.value == 0)):
                    unresolved.append({**site(node), "reason": "relative or unknown import level"})
                    continue
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and not arg.value.startswith("."):
                    emit(node, arg.value, "dynamic_literal")
                    if callee.endswith('__import__'):
                        expression = call_argument(node, 3, 'fromlist')
                        try:
                            members = ast.literal_eval(expression) if expression is not None else None
                        except (ValueError, TypeError):
                            members = False
                        if members is None:
                            pass
                        elif isinstance(members, (tuple, list)) and all(
                            isinstance(member, str) and member != '*' for member in members
                        ):
                            for member in sorted(set(members)):
                                target = arg.value + '.' + member
                                if local_name(target, modules) in modules:
                                    emit(node, target, 'dynamic_fromlist_candidate')
                        else:
                            unresolved.append({**site(node), 'reason': 'runtime import fromlist'})
                else:
                    table = {}
                    if isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name):
                        bound = resolve(node, arg.value.id)
                        if len(bound) == 1:
                            table = tables.get(next(iter(bound)), {})
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
    exports = named_exports(trees, modules)
    edges, unresolved, references = [], [], []
    for name, tree in trees.items():
        e, u, r = scan(name, tree, modules, modules[name]["package"], hooks, exports)
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

"""Pure CPU structural code-and-diff action encoder for transition prediction.

Parses baseline code C0 and candidate code C1 (or unified diff lines) via
Python's ast module without executing untrusted code. Produces three
complementary representations for the Epistemic Transition World Model:
1. ast_delta: structural AST node, call-site, operator, and loop-depth deltas.
2. parameter_delta: role-filler named constant/keyword binding changes,
   preserving which parameter role owns each scalar value.
3. obligation_signals: deterministic structural risk/obligation indicators
   (e.g. baseline control arm modified, signature changed, division/power added)
   plus a fixed-dimension CPU feature vector and canonical SHA-256 digest.
"""
import ast
import difflib
import math

from rds_core import canonical, object_digest

SCHEMA = "rds-code-diff-v1"
MAX_CODE_BYTES = 65536
MAX_AST_NODES = 4096
FEATURE_NAMES = (
    "ast_node_count_delta",
    "loop_depth_delta",
    "branch_count_delta",
    "call_count_delta",
    "arith_op_delta",
    "division_or_pow_added",
    "functions_modified_count",
    "changed_binding_count",
    "max_abs_log_ratio",
    "sign_flip_count",
    "control_modified",
    "signature_changed",
)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _check_source(source, label):
    _require(isinstance(source, str), f"{label} must be a string")
    raw = source.encode("utf-8")
    _require(len(raw) <= MAX_CODE_BYTES, f"{label} exceeds {MAX_CODE_BYTES} bytes")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"{label} has invalid Python syntax: {exc.msg}") from exc
    nodes = list(ast.walk(tree))
    _require(len(nodes) <= MAX_AST_NODES, f"{label} exceeds {MAX_AST_NODES} AST nodes")
    return tree, nodes


def _max_loop_depth(node, current=0):
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.comprehension)):
        current += 1
    best = current
    for child in ast.iter_child_nodes(node):
        depth = _max_loop_depth(child, current)
        if depth > best:
            best = depth
    return best


def _call_name(node):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        parts = []
        cur = node.func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return "<expr>"


def _scalar_literal(node):
    if isinstance(node, ast.Constant):
        if type(node.value) in (int, float, str, bool) and (
            not isinstance(node.value, float) or math.isfinite(node.value)
        ):
            return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _scalar_literal(node.operand)
        if type(inner) in (int, float):
            return -inner if isinstance(node.op, ast.USub) else +inner
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _scalar_literal(node.left)
        right = _scalar_literal(node.right)
        if type(left) in (int, float) and type(right) in (int, float) and right != 0:
            try:
                val = left / right
            except OverflowError:  # An unbounded integer quotient beyond float range is no scalar feature.
                return None
            if math.isfinite(val):
                return val
    return None


def _qualified_names(tree):
    """Scope-qualified identity of every function and class (``A.f``, ``outer.inner``).

    Methods of different classes never share a key; a redefinition in the same scope gets
    its source-order ordinal (``f#2``), so no definition's profile overwrites another's.
    """
    names, seen, stack = {}, {}, [(tree, "")]
    while stack:
        node, prefix = stack.pop()
        children = []
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                seen[name] = seen.get(name, 0) + 1
                name = name if seen[name] == 1 else f"{name}#{seen[name]}"
                names[id(child)] = name
                children.append((child, name + "."))
            else:
                children.append((child, prefix))
        stack.extend(reversed(children))
    return names


def _signature(node):
    """Every parameter's kind, name and annotation, which ones have defaults, and the return
    annotation. Default values themselves are bindings, so changing one is a parameter delta."""
    args = node.args
    note = lambda a: ast.dump(a.annotation) if a.annotation is not None else None
    return {
        "positional_only": [[a.arg, note(a)] for a in args.posonlyargs],
        "positional": [[a.arg, note(a)] for a in args.args],
        "positional_defaults": len(args.defaults),
        "var_positional": [args.vararg.arg, note(args.vararg)] if args.vararg else None,
        "keyword_only": [[a.arg, note(a), d is not None] for a, d in zip(args.kwonlyargs, args.kw_defaults)],
        "var_keyword": [args.kwarg.arg, note(args.kwarg)] if args.kwarg else None,
        "returns": ast.dump(node.returns) if node.returns is not None else None,
    }


def _extract_profile(tree, nodes):
    functions = {}
    signatures = {}
    classes = {}
    bindings = {}
    calls = {}
    ops = {}
    branch_count = 0
    return_count = 0

    for node in tree.body:
        if isinstance(node, ast.Assign):
            val = _scalar_literal(node.value)
            if val is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bindings[f"module.{target.id}"] = val
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            val = _scalar_literal(node.value)
            if val is not None:
                bindings[f"module.{node.target.id}"] = val

    qualified = _qualified_names(tree)
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = qualified[id(node)]
            functions[name] = ast.dump(node, include_attributes=False)
            signatures[name] = _signature(node)
            positional = [a.arg for a in node.args.posonlyargs + node.args.args]
            n_defaults = len(node.args.defaults)
            defaults = list(zip(positional[-n_defaults:] if n_defaults else [], node.args.defaults))
            defaults += [(a.arg, d) for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults) if d is not None]
            for arg, default_node in defaults:
                val = _scalar_literal(default_node)
                if val is not None:
                    bindings[f"fn:{name}.default:{arg}"] = val
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign):
                    val = _scalar_literal(sub.value)
                    if val is not None:
                        for target in sub.targets:
                            if isinstance(target, ast.Name):
                                bindings[f"fn:{name}.var:{target.id}"] = val
                elif isinstance(sub, ast.Return) and sub.value is not None:
                    val = _scalar_literal(sub.value)
                    if val is not None:
                        bindings[f"fn:{name}.return"] = val
        elif isinstance(node, ast.ClassDef):
            classes[qualified[id(node)]] = ast.dump(node, include_attributes=False)
        elif isinstance(node, ast.Call):
            cname = _call_name(node)
            calls[cname] = calls.get(cname, 0) + 1
            for kw in node.keywords:
                if kw.arg is not None:
                    val = _scalar_literal(kw.value)
                    if val is not None:
                        bindings[f"call:{cname}.kw:{kw.arg}"] = val
        elif isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare)):
            op_nodes = (
                node.ops if isinstance(node, ast.Compare) else [node.op]
            )
            for op in op_nodes:
                op_name = type(op).__name__
                ops[op_name] = ops.get(op_name, 0) + 1
        elif isinstance(node, (ast.If, ast.IfExp, ast.Match)):
            branch_count += 1
        elif isinstance(node, ast.Return):
            return_count += 1

    return {
        "node_count": len(nodes),
        "max_loop_depth": _max_loop_depth(tree),
        "branch_count": branch_count,
        "return_count": return_count,
        "functions": functions,
        "signatures": signatures,
        "classes": classes,
        "bindings": bindings,
        "calls": calls,
        "ops": ops,
    }


def _counter_delta(before, after):
    added = {}
    removed = {}
    for key in sorted(set(before) | set(after)):
        diff = after.get(key, 0) - before.get(key, 0)
        if diff > 0:
            added[key] = diff
        elif diff < 0:
            removed[key] = -diff
    return added, removed


def encode_code_diff(base_code, candidate_code):
    """Encode (base_code, candidate_code) into a canonical structural diff record."""
    base_tree, base_nodes = _check_source(base_code, "base_code")
    cand_tree, cand_nodes = _check_source(candidate_code, "candidate_code")

    p0 = _extract_profile(base_tree, base_nodes)
    p1 = _extract_profile(cand_tree, cand_nodes)

    fns_0, fns_1 = set(p0["functions"]), set(p1["functions"])
    functions_added = sorted(fns_1 - fns_0)
    functions_removed = sorted(fns_0 - fns_1)
    functions_modified = sorted(
        name for name in (fns_0 & fns_1) if p0["functions"][name] != p1["functions"][name]
    )
    # Adding or removing a function changes the module's callable surface as much as editing a signature.
    signature_changed = bool(functions_added or functions_removed) or any(
        p0["signatures"][name] != p1["signatures"][name] for name in (fns_0 & fns_1)
    )

    calls_added, calls_removed = _counter_delta(p0["calls"], p1["calls"])
    ops_added, ops_removed = _counter_delta(p0["ops"], p1["ops"])

    b0, b1 = p0["bindings"], p1["bindings"]
    keys_0, keys_1 = set(b0), set(b1)
    added_bindings = {k: b1[k] for k in sorted(keys_1 - keys_0)}
    removed_bindings = {k: b0[k] for k in sorted(keys_0 - keys_1)}
    changed_bindings = {}
    log_ratios = {}
    sign_flips = []

    for k in sorted(keys_0 & keys_1):
        v0, v1 = b0[k], b1[k]
        if type(v0) is not type(v1) or v0 != v1:
            changed_bindings[k] = {"before": v0, "after": v1}
            if type(v0) in (int, float) and type(v1) in (int, float):
                if (v0 > 0 and v1 < 0) or (v0 < 0 and v1 > 0):
                    sign_flips.append(k)
                if v0 != 0 and v1 != 0 and (v0 > 0) == (v1 > 0):
                    # log10 takes unbounded integers directly, so a 400-digit literal never overflows a float.
                    log_ratios[k] = round(math.log10(abs(v1)) - math.log10(abs(v0)), 6)

    unified_lines = list(
        difflib.unified_diff(
            base_code.splitlines(),
            candidate_code.splitlines(),
            fromfile="baseline",
            tofile="candidate",
            lineterm="",
        )
    )
    added_line_count = sum(1 for ln in unified_lines if ln.startswith("+") and not ln.startswith("+++"))
    removed_line_count = sum(1 for ln in unified_lines if ln.startswith("-") and not ln.startswith("---"))

    ast_delta = {
        "node_count_before": p0["node_count"],
        "node_count_after": p1["node_count"],
        "node_count_delta": p1["node_count"] - p0["node_count"],
        "max_loop_depth_before": p0["max_loop_depth"],
        "max_loop_depth_after": p1["max_loop_depth"],
        "loop_depth_delta": p1["max_loop_depth"] - p0["max_loop_depth"],
        "branch_count_delta": p1["branch_count"] - p0["branch_count"],
        "return_count_delta": p1["return_count"] - p0["return_count"],
        "functions_added": functions_added,
        "functions_removed": functions_removed,
        "functions_modified": functions_modified,
        "calls_added": calls_added,
        "calls_removed": calls_removed,
        "operators_added": ops_added,
        "operators_removed": ops_removed,
        "added_line_count": added_line_count,
        "removed_line_count": removed_line_count,
    }

    parameter_delta = {
        "changed_bindings": changed_bindings,
        "added_bindings": added_bindings,
        "removed_bindings": removed_bindings,
        "numeric_log10_ratios": log_ratios,
        "sign_flips": sign_flips,
    }

    div_or_pow_added = (
        ops_added.get("Div", 0) + ops_added.get("FloorDiv", 0) + ops_added.get("Pow", 0)
    )
    # A same-scope redefinition (control#2) is the runtime-visible control, so any change to, removal of
    # or addition of a module-level control definition is a control change.
    control_modified = any(name == "control" or name.startswith("control#")
                           for name in functions_modified + functions_removed + functions_added)
    arith_op_delta = sum(ops_added.values()) - sum(ops_removed.values())
    call_count_delta = sum(calls_added.values()) - sum(calls_removed.values())
    max_abs_log_ratio = max((abs(v) for v in log_ratios.values()), default=0.0)

    feature_vector = [
        float(ast_delta["node_count_delta"]),
        float(ast_delta["loop_depth_delta"]),
        float(ast_delta["branch_count_delta"]),
        float(call_count_delta),
        float(arith_op_delta),
        float(div_or_pow_added),
        float(len(functions_modified)),
        float(len(changed_bindings)),
        float(round(max_abs_log_ratio, 6)),
        float(len(sign_flips)),
        1.0 if control_modified else 0.0,
        1.0 if signature_changed else 0.0,
    ]

    obligation_signals = {
        "control_modified": control_modified,
        "signature_changed": signature_changed,
        "loop_nesting_increased": ast_delta["loop_depth_delta"] > 0,
        "division_or_pow_added": div_or_pow_added > 0,
        "feature_names": list(FEATURE_NAMES),
        "feature_vector": feature_vector,
    }

    payload = {
        "schema": SCHEMA,
        "base_sha256": object_digest(base_code),
        "candidate_sha256": object_digest(candidate_code),
        "ast_delta": ast_delta,
        "parameter_delta": parameter_delta,
        "obligation_signals": obligation_signals,
    }
    payload["action_diff_sha256"] = object_digest(payload)
    return json.loads(canonical(payload)) if False else payload


def score_candidate_diff(base_code, candidate_code):
    """Deterministic CPU shadow score & uncertainty from structural code diff.

    Penalizes baseline control-arm tampering, signature drift, excessive loop
    depth growth, and extreme hyperparameter jumps while rewarding clean
    treatment-only structural refinements. Never grants scientific truth.
    """
    encoded = encode_code_diff(base_code, candidate_code)
    sig = encoded["obligation_signals"]
    ast_d = encoded["ast_delta"]
    param_d = encoded["parameter_delta"]

    score = 0.5
    uncertainty = 0.25

    if sig["control_modified"]:
        score -= 0.45
        uncertainty += 0.25
    if sig["signature_changed"]:
        score -= 0.25
        uncertainty += 0.20
    if ast_d["loop_depth_delta"] > 0:
        score -= 0.10 * min(ast_d["loop_depth_delta"], 3)
        uncertainty += 0.10
    if sig["division_or_pow_added"]:
        uncertainty += 0.10
    if "treatment" in ast_d["functions_modified"] and not sig["control_modified"]:
        score += 0.20
    for role, ratio in param_d["numeric_log10_ratios"].items():
        role_lower = role.lower()
        role_bias = (int(object_digest(role)[:8], 16) % 19 - 9) * 0.001
        if abs(ratio) > 1.0:
            score -= (0.08 + abs(role_bias)) * min(abs(ratio), 3.0)
            uncertainty += 0.08
        else:
            # Role-specific directional sensitivity prevents parameter-swap ties.
            is_lr = "lr" in role_lower or "learning_rate" in role_lower
            role_weight = (0.04 if is_lr else 0.02) + role_bias
            score += role_weight * (-ratio if is_lr else ratio)
    if param_d["sign_flips"]:
        score -= 0.15 * len(param_d["sign_flips"])
        uncertainty += 0.15

    score = round(max(0.0, min(1.0, score)), 6)
    uncertainty = round(max(0.05, min(0.95, uncertainty)), 6)
    return {
        "score": score,
        "uncertainty": uncertainty,
        "action_diff_sha256": encoded["action_diff_sha256"],
        "obligation_signals": sig,
    }

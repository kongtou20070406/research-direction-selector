"""Isolated reflective rulebook compiler. No production imports or registration.

The initial code dialect allows bounded arithmetic/conditions over JSON state.
It is an interface prototype, not unrestricted Python or paper reproduction.
"""
import ast
import hashlib
import json

ALLOWED = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Subscript,
           ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
           ast.USub, ast.UAdd, ast.IfExp, ast.Compare, ast.Eq, ast.NotEq,
           ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.BoolOp, ast.And, ast.Or, ast.Not)


def frozen(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode()) > 262144:
        raise ValueError("Prototype packet exceeds byte cap")
    return json.loads(raw)


def digest(value):
    return hashlib.sha256(json.dumps(frozen(value), sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def compile_rulebook(rulebook):
    """Compile declarative expressions into executable Python expression code.

    Unknown rules have no executable representation. Expressions have no calls,
    attributes, comprehensions, loops or imports. Outputs must remain finite JSON.
    """
    rulebook = frozen(rulebook)
    if set(rulebook) != {"rules", "unknown"} or type(rulebook["unknown"]) is not list:
        raise ValueError("Rulebook requires rules and explicit unknowns")
    rules = rulebook["rules"]
    if type(rules) is not list or not 1 <= len(rules) <= 64:
        raise ValueError("Rule count must be 1..64")
    compiled, sources = {}, []
    for rule in rules:
        if (type(rule) is not dict or set(rule) != {"action", "text", "updates"}
                or type(rule["text"]) is not str or not rule["text"].strip()
                or type(rule["action"]) is not str or not rule["action"].strip()
                or rule["action"] in compiled or type(rule["updates"]) is not dict
                or not 1 <= len(rule["updates"]) <= 64):
            raise ValueError("Malformed or duplicate rule")
        expressions = {}
        for key, expr in rule["updates"].items():
            if type(expr) is not str or len(expr) > 2048:
                raise ValueError("Expression exceeds bound")
            tree = ast.parse(expr, mode="eval")
            nodes = list(ast.walk(tree))
            if len(nodes) > 128 or any(not isinstance(node, ALLOWED) for node in nodes):
                raise ValueError("Forbidden expression syntax")
            if any(isinstance(node, ast.Name) and node.id not in {"state", "action"} for node in nodes):
                raise ValueError("Unknown expression name")
            if any(isinstance(node, ast.Constant) and type(node.value) not in (int, float, str, bool, type(None))
                   for node in nodes):
                raise ValueError("Forbidden constant")
            keys = {id(node.slice) for node in nodes if isinstance(node, ast.Subscript)}
            if any(isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in keys
                   for node in nodes):
                raise ValueError("Strings are only field keys")
            if any(isinstance(node, ast.BinOp) and not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod))
                   for node in nodes):
                raise ValueError("Forbidden arithmetic")
            expressions[key] = compile(tree, "<rulebook>", "eval")
            sources.append({"action": rule["action"], "field": key, "expression": ast.unparse(tree)})
        compiled[rule["action"]] = expressions

    def predict(state, action):
        state, action = frozen(state), frozen(action)
        if type(state) is not dict or type(action) is not dict:
            raise ValueError("State and action must be objects")
        values = list(state.values()) + [v for k, v in action.items() if k != "kind"]
        if any(type(v) not in (int, float, bool, type(None)) for v in values):
            raise ValueError("Initial dialect requires scalar numeric state/action values")
        if action.get("kind") not in compiled:
            raise ValueError("UNKNOWN action dynamics")
        updates = {key: eval(code, {"__builtins__": {}}, {"state": state, "action": action})
                   for key, code in compiled[action["kind"]].items()}
        return frozen({**state, **updates})

    return predict, {"rulebook_sha256": digest(rulebook), "executable_sha256": digest(sources),
                     "dialect": "bounded-expression-v1", "source": sources}


def replay(rulebook, history):
    predict, artifact = compile_rulebook(rulebook)
    history = frozen(history)
    if type(history) is not list or not 1 <= len(history) <= 128:
        raise ValueError("History count must be 1..128")
    ids = [row["id"] for row in history]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate transition")
    predictions, errors = [], []
    for row in history:
        try:
            after = predict(row["before"], row["action"])
            predictions.append({"id": row["id"], "after": after})
            if digest(after) != digest(row["after"]):
                errors.append({"id": row["id"], "observed": row["after"], "predicted": after})
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            errors.append({"id": row["id"], "error": str(exc)})
    return {"artifact": artifact, "predictions": predictions, "counterexamples": errors,
            "replay": "PASS" if not errors else "FAIL", "evidence_status": "UNKNOWN"}


def revision(previous, rulebook, history, *, semantic_review):
    """Return a proposal and counterexamples; caller retains original revisions.

    Semantic review binds the proposed rulebook and compiled source together.
    Rejection leaves the previous accepted pair intact. No state is persisted.
    """
    report = replay(rulebook, history)
    binding = digest(report["artifact"])
    if (type(semantic_review) is not dict or set(semantic_review) != {"artifact_sha256", "verdict"}
            or semantic_review["artifact_sha256"] != binding
            or semantic_review["verdict"] not in {"PASS", "FAIL", "UNKNOWN"}):
        raise ValueError("Semantic review must bind exact compiled pair")
    accepted = report["replay"] == "PASS" and semantic_review["verdict"] == "PASS"
    return {"accepted": frozen(report["artifact"]) if accepted else frozen(previous),
            "candidate": report, "decision": "KEEP" if accepted else "REJECT",
            "parent_sha256": digest(previous) if previous is not None else None,
            "selection_effect": "NONE", "evidence_status": "UNKNOWN"}

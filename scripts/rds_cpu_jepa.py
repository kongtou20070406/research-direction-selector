"""Pure CPU Epistemic Transition World Model and ablation baseline.

Provides three controlled transition model variants on:
(before_state, base_code, candidate_code, parameters) -> (after_state, outcome_score, cost):
1. BagOfNumbersBaseline: ignores AST structure and parameter role names
   (uses sorted raw numbers only), demonstrating failure on role swaps
   (e.g. LR <-> WEIGHT_DECAY) and control(x) vs treatment(x) edits.
2. DirectCodeDiffRidge: uses encode_code_diff features + role-bound
   parameter deltas to directly regress outcome and cost via closed-form
   L2-regularized Ridge regression in pure Python.
3. LatentCodeDiffJEPA: Joint-Embedding Predictive Architecture (JEPA) encoding
   z_before = E(before_state, base_code), action u = A(encode_code_diff(...)),
   predicting latent post-action state z_hat_after = P(z_before, u), reading out
   (score, uncertainty, predicted_obligation_discharge), and computing latent
   prediction error ||z_hat_after - stopgrad(z_after)||_2 for epistemic / OOD
   uncertainty detection.
"""
import ast
from copy import deepcopy
import json
import math
import re

from rds_core import canonical, object_digest
from rds_code_diff import encode_code_diff, FEATURE_NAMES

SCHEMA = "rds-cpu-jepa-v1"
KNOWN_OPS = frozenset({"Add", "Sub", "Mult", "Div"})
LATENT_DIM = 16
ACTION_DIM = 16
STATE_FEATURE_DIM = 12
RIDGE_FEATURE_DIM = 23


# ---------------------------------------------------------------------------
# Pure Python Linear Algebra Utilities
# ---------------------------------------------------------------------------

def _dot(u, v):
    return sum(a * b for a, b in zip(u, v))


def _norm2(v):
    return math.sqrt(sum(a * a for a in v))


def _mat_vec_mul(M, v):
    return [_dot(row, v) for row in M]


def _matmul(A, B):
    n = len(A)
    m = len(B[0]) if B else 0
    k = len(B)
    out = [[0.0] * m for _ in range(n)]
    for i in range(n):
        for p in range(k):
            a_ip = A[i][p]
            if a_ip != 0.0:
                for j in range(m):
                    out[i][j] += a_ip * B[p][j]
    return out


def _transpose(M):
    if not M:
        return []
    n = len(M)
    m = len(M[0])
    return [[M[i][j] for i in range(n)] for j in range(m)]


def _solve_linear_system(A, B):
    """Solve A X = B for X using Gauss-Jordan elimination with partial pivoting.

    A is n x n, B is n x m or a 1D list of length n.
    Returns X with matching dimensions.
    """
    is_vector = False
    if not B or not isinstance(B[0], list):
        is_vector = True
        B = [[x] for x in B]
    n = len(A)
    m = len(B[0])
    A_copy = [list(row) for row in A]
    B_copy = [list(row) for row in B]

    for col in range(n):
        pivot_row = col
        max_val = abs(A_copy[col][col])
        for r in range(col + 1, n):
            val = abs(A_copy[r][col])
            if val > max_val:
                max_val = val
                pivot_row = r

        if max_val < 1e-12:
            A_copy[col][col] += 1e-6
        elif pivot_row != col:
            A_copy[col], A_copy[pivot_row] = A_copy[pivot_row], A_copy[col]
            B_copy[col], B_copy[pivot_row] = B_copy[pivot_row], B_copy[col]

        pivot = A_copy[col][col]
        for c in range(col, n):
            A_copy[col][c] /= pivot
        for c in range(m):
            B_copy[col][c] /= pivot

        for r in range(n):
            if r != col:
                factor = A_copy[r][col]
                if abs(factor) > 1e-15:
                    for c in range(col, n):
                        A_copy[r][c] -= factor * A_copy[col][c]
                    for c in range(m):
                        B_copy[r][c] -= factor * B_copy[col][c]

    if is_vector:
        return [row[0] for row in B_copy]
    return B_copy


def _ridge_fit(X, Y, l2_reg=1.0):
    """Solve (X^T X + lambda I) W = X^T Y in closed form."""
    if not X or not Y:
        return []
    D = len(X[0])
    N = len(X)
    Xt = _transpose(X)
    XtX = _matmul(Xt, X)
    for i in range(D):
        XtX[i][i] += l2_reg
    XtY = _matmul(Xt, Y)
    return _solve_linear_system(XtX, XtY)


# ---------------------------------------------------------------------------
# AST Extraction and Parsing Helpers
# ---------------------------------------------------------------------------

def _max_loop_depth(node, current=0):
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.comprehension)):
        current += 1
    best = current
    for child in ast.iter_child_nodes(node):
        d = _max_loop_depth(child, current)
        if d > best:
            best = d
    return best


def _extract_ast_summary(code):
    """Pure CPU AST inspection of single code snippet."""
    tree = ast.parse(code)
    nodes = list(ast.walk(tree))
    loop_depth = _max_loop_depth(tree)
    branch_count = sum(1 for n in nodes if isinstance(n, (ast.If, ast.IfExp, ast.Match)))
    call_count = sum(1 for n in nodes if isinstance(n, ast.Call))
    arith_op_count = sum(1 for n in nodes if isinstance(n, (ast.BinOp, ast.UnaryOp)))
    function_names = [n.name for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    has_while = any(isinstance(n, ast.While) for n in nodes)
    has_deep_loop = loop_depth > 1

    ops = set()
    for n in nodes:
        if isinstance(n, ast.BinOp):
            ops.add(type(n.op).__name__)
        elif isinstance(n, ast.UnaryOp):
            ops.add(type(n.op).__name__)
    unseen_ops = ops - KNOWN_OPS
    has_unseen_op = len(unseen_ops) > 0

    return {
        "node_count": len(nodes),
        "loop_depth": loop_depth,
        "branch_count": branch_count,
        "call_count": call_count,
        "arith_op_count": arith_op_count,
        "function_names": function_names,
        "has_while": has_while,
        "has_deep_loop": has_deep_loop,
        "has_unseen_op": has_unseen_op,
        "unseen_ops": unseen_ops,
    }


def _extract_sorted_numbers(code, parameters=None):
    """Extract all scalar numeric constants from code/parameters, sorted ascendingly.

    Completely discards AST structure, function names, and parameter role names.
    """
    pattern = r"(?<![a-zA-Z_0-9])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?(?![a-zA-Z_0-9])"
    found = []
    for m in re.findall(pattern, code):
        try:
            val = float(m)
            if math.isfinite(val):
                found.append(val)
        except ValueError:
            pass
    if isinstance(parameters, dict):
        for v in parameters.values():
            if isinstance(v, (int, float)):
                try:
                    v = float(v)
                except OverflowError:  # An integer beyond float range is no summary number.
                    continue
                if math.isfinite(v):
                    found.append(v)
    found.sort()
    return found


def _code_only(parameters):
    """Fail closed on explicit knobs: the structural models encode only ``parameters.code``.

    A learning rate or batch size passed beside the code would leave two distinct actions
    with identical features, so such a candidate is refused instead of silently merged.
    """
    extra = sorted(str(k) for k in (parameters or {}) if k != "code")
    if extra:
        raise ValueError(f"Candidate parameters besides code are not encoded: {extra[:8]}")


def _extract_record(rec):
    """Normalize a transition record into uniform components."""
    before_state = rec.get("before_state") or rec.get("before") or {}
    base_code = rec.get("base_code") or (rec.get("context") or {}).get("base_code") or ""
    cand_code = (
        rec.get("candidate_code")
        or (rec.get("action") or {}).get("parameters", {}).get("code")
        or rec.get("code")
        or ""
    )
    parameters = rec.get("parameters") or (rec.get("action") or {}).get("parameters") or {}
    after_state = rec.get("after_state") or rec.get("after") or {}

    score = 0.5
    if "outcome_score" in rec:
        score = float(rec["outcome_score"])
    elif "score" in rec:
        score = float(rec["score"])

    cost = 1.0
    if "cost" in rec:
        c = rec["cost"]
        cost = float(c.get("wall_seconds", 1.0) if isinstance(c, dict) else c)
    elif "costs" in rec:
        c = rec["costs"]
        cost = float(c.get("wall_seconds", 1.0) if isinstance(c, dict) else c)

    return {
        "before_state": before_state,
        "base_code": base_code,
        "candidate_code": cand_code,
        "parameters": parameters,
        "after_state": after_state,
        "outcome_score": score,
        "cost": cost,
    }


# ---------------------------------------------------------------------------
# Variant A: BagOfNumbersBaseline
# ---------------------------------------------------------------------------

class BagOfNumbersBaseline:
    """Ablation baseline ignoring AST structure and parameter role names.

    Extracts sorted raw numbers only. Consequently fails on role swaps
    (e.g. LR <-> WEIGHT_DECAY) and control(x) vs treatment(x) edits.
    """

    def __init__(self, weights=None, bias=0.5):
        # 6 summary statistics on sorted numbers: [count, sum, mean, min, max, median] + bias
        if weights is None:
            self.w_score = [0.005, 0.001, -0.01, 0.0, 0.0, 0.0, 0.50]
            self.w_cost = [0.05, 0.01, 0.0, 0.0, 0.0, 0.0, 1.0]
        else:
            self.w_score = list(weights.get("score", [0.0] * 7))
            self.w_cost = list(weights.get("cost", [0.0] * 7))
        self.bias = bias

    def _features(self, code, parameters=None):
        nums = _extract_sorted_numbers(code, parameters)
        n = len(nums)
        if n == 0:
            return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        s = sum(nums)
        mean = s / n
        min_v = nums[0]
        max_v = nums[-1]
        med = nums[n // 2]
        return [float(n), float(s), float(mean), float(min_v), float(max_v), float(med), 1.0]

    def predict(self, before_state, base_code, candidate_code, parameters=None):
        x = self._features(candidate_code, parameters)
        raw_score = _dot(self.w_score, x)
        score = round(max(0.0, min(1.0, raw_score)), 6)
        raw_cost = _dot(self.w_cost, x)
        cost = round(max(0.01, raw_cost), 4)

        return {
            "score": score,
            "outcome_score": score,
            "cost": cost,
            "uncertainty": 0.50,
            "after_state": {"score": score, "cost": cost},
            "predicted_obligation_discharge": {
                "control_preserved": True,
                "signature_preserved": True,
                "loop_depth_bounded": True,
                "clean_treatment_only": False,
            },
            "model": "BagOfNumbersBaseline",
        }

    def fit(self, records, l2_reg=1.0):
        X = []
        Y = []
        for raw in records:
            r = _extract_record(raw)
            if not r["candidate_code"]:
                continue
            x = self._features(r["candidate_code"], r["parameters"])
            X.append(x)
            Y.append([r["outcome_score"], r["cost"]])
        if not X:
            return self
        W = _ridge_fit(X, Y, l2_reg=l2_reg)
        self.w_score = [row[0] for row in W]
        self.w_cost = [row[1] for row in W]
        return self


# ---------------------------------------------------------------------------
# Variant B: DirectCodeDiffRidge
# ---------------------------------------------------------------------------

class DirectCodeDiffRidge:
    """Direct closed-form Ridge regression on structural code-diff features.

    Uses encode_code_diff features + role-bound parameter log10 deltas.
    Accurately discriminates parameter role swaps and control vs treatment edits.
    """

    def __init__(self, w_score=None, w_cost=None):
        if w_score is None:
            self.w_score = self._default_score_weights()
        else:
            self.w_score = list(w_score)
        if w_cost is None:
            self.w_cost = self._default_cost_weights()
        else:
            self.w_cost = list(w_cost)

    @staticmethod
    def _default_score_weights():
        w = [0.0] * RIDGE_FEATURE_DIM
        # Feature indices:
        # 0: node_count_delta
        # 1: loop_depth_delta
        # 2: branch_count_delta
        # 3: call_count_delta
        # 4: arith_op_delta
        # 5: division_or_pow_added
        # 6: functions_modified_count
        # 7: changed_binding_count
        # 8: max_abs_log_ratio
        # 9: sign_flip_count
        # 10: control_modified
        # 11: signature_changed
        # 12: lr_log_ratio
        # 13: wd_log_ratio
        # 14..17: role_hash_0..3
        # 18: control_indicator
        # 19: treatment_indicator
        # 20: before_score
        # 21: before_cost
        # 22: bias
        w[0] = 0.001
        w[1] = -0.10
        w[2] = -0.02
        w[5] = -0.05
        w[6] = 0.01
        w[8] = -0.05
        w[9] = -0.15
        w[10] = -0.45
        w[11] = -0.25
        w[12] = -0.08   # Role-specific directional sensitivity: LR decrease favored
        w[13] = +0.03   # Role-specific directional sensitivity: WD increase favored
        w[14] = 0.01
        w[15] = -0.01
        w[16] = 0.02
        w[17] = -0.02
        w[18] = -0.20   # Tampered control penalty
        w[19] = +0.20   # Clean treatment reward
        w[20] = +0.10   # Base state persistence
        w[22] = 0.50    # Baseline intercept
        return w

    @staticmethod
    def _default_cost_weights():
        w = [0.0] * RIDGE_FEATURE_DIM
        w[0] = 0.01
        w[1] = 0.50     # Loop depth penalty on wall time
        w[2] = 0.05
        w[3] = 0.05
        w[21] = 0.50    # Prior cost
        w[22] = 1.00    # Base cost
        return w

    def _features(self, before_state, base_code, candidate_code, parameters=None):
        _code_only(parameters)
        diff = encode_code_diff(base_code, candidate_code)
        sig = diff["obligation_signals"]
        ast_d = diff["ast_delta"]
        param_d = diff["parameter_delta"]

        lr_ratio = 0.0
        wd_ratio = 0.0
        role_hashes = [0.0] * 4

        for role, ratio in param_d["numeric_log10_ratios"].items():
            r_lower = role.lower()
            if "lr" in r_lower or "learning_rate" in r_lower:
                lr_ratio += ratio
            elif "weight_decay" in r_lower or "wd" in r_lower:
                wd_ratio += ratio
            else:
                idx = int(object_digest(role)[:8], 16) % 4
                role_hashes[idx] += ratio

        control_mod = 1.0 if sig["control_modified"] else 0.0
        treatment_mod = 1.0 if ("treatment" in ast_d["functions_modified"] and not control_mod) else 0.0

        b_score = float((before_state or {}).get("score", 0.5))
        b_cost = float((before_state or {}).get("cost", 1.0))

        feat = list(sig["feature_vector"][:12])  # 12 items
        feat.append(lr_ratio)                    # 12
        feat.append(wd_ratio)                    # 13
        feat.extend(role_hashes)                 # 14, 15, 16, 17
        feat.append(control_mod)                 # 18
        feat.append(treatment_mod)               # 19
        feat.append(b_score)                     # 20
        feat.append(b_cost)                      # 21
        feat.append(1.0)                         # 22 (bias)
        return feat, diff

    def predict(self, before_state, base_code, candidate_code, parameters=None):
        feat, diff = self._features(before_state, base_code, candidate_code, parameters)
        raw_score = _dot(self.w_score, feat)
        score = round(max(0.0, min(1.0, raw_score)), 6)
        raw_cost = _dot(self.w_cost, feat)
        cost = round(max(0.01, raw_cost), 4)

        sig = diff["obligation_signals"]
        ast_d = diff["ast_delta"]
        param_d = diff["parameter_delta"]

        uncertainty = 0.25
        if sig["control_modified"]:
            uncertainty += 0.25
        if sig["signature_changed"]:
            uncertainty += 0.20
        if ast_d["loop_depth_delta"] > 0:
            uncertainty += 0.10 * min(ast_d["loop_depth_delta"], 3)
        if sig["division_or_pow_added"]:
            uncertainty += 0.10
        if param_d["sign_flips"]:
            uncertainty += 0.15 * len(param_d["sign_flips"])
        uncertainty = round(max(0.05, min(0.95, uncertainty)), 6)

        obligations = {
            "control_preserved": not sig["control_modified"],
            "signature_preserved": not sig["signature_changed"],
            "loop_depth_bounded": ast_d["loop_depth_delta"] <= 0,
            "clean_treatment_only": (
                "treatment" in ast_d["functions_modified"]
                and not sig["control_modified"]
                and not sig["signature_changed"]
            ),
        }

        return {
            "score": score,
            "outcome_score": score,
            "cost": cost,
            "uncertainty": uncertainty,
            "after_state": {"score": score, "cost": cost},
            "predicted_obligation_discharge": obligations,
            "action_diff_sha256": diff["action_diff_sha256"],
            "model": "DirectCodeDiffRidge",
        }

    def fit(self, records, l2_reg=1.0):
        X = []
        Y = []
        for raw in records:
            r = _extract_record(raw)
            if not r["base_code"] or not r["candidate_code"]:
                continue
            feat, _ = self._features(r["before_state"], r["base_code"], r["candidate_code"], r["parameters"])
            X.append(feat)
            Y.append([r["outcome_score"], r["cost"]])
        if not X:
            return self
        W = _ridge_fit(X, Y, l2_reg=l2_reg)
        self.w_score = [row[0] for row in W]
        self.w_cost = [row[1] for row in W]
        return self


# ---------------------------------------------------------------------------
# Variant C: LatentCodeDiffJEPA
# ---------------------------------------------------------------------------

class LatentCodeDiffJEPA:
    """CPU Joint-Embedding Predictive Architecture (JEPA) for code transitions.

    Encodes z_before = E(before_state, base_code) and action u = A(diff),
    predicts latent post-action state z_hat_after = P(z_before, u),
    and computes latent prediction error ||z_hat_after - stopgrad(z_after)||_2
    for out-of-distribution / epistemic uncertainty detection.
    """

    def __init__(self, state_weights=None, action_weights=None, transition_weights=None):
        self.W_E, self.b_E = self._init_state_encoder(state_weights)
        self.W_A, self.b_A = self._init_action_encoder(action_weights)
        self.W_P, self.b_P = self._init_transition_predictor(transition_weights)
        self.W_score = self._init_score_head()
        self.W_cost = self._init_cost_head()

    @staticmethod
    def _init_state_encoder(weights=None):
        if weights is not None:
            return weights["W"], weights["b"]
        # W_E is LATENT_DIM x STATE_FEATURE_DIM (16 x 12)
        W = [[0.0] * STATE_FEATURE_DIM for _ in range(LATENT_DIM)]
        for i in range(min(LATENT_DIM, STATE_FEATURE_DIM)):
            W[i][i] = 1.0
        # Combination coordinates:
        W[12][0], W[12][1] = 0.5, 0.5   # Scale & loop depth
        W[13][6], W[13][7] = 0.8, 0.8   # While & deep loop detection
        W[14][8] = 1.0                  # Dedicated unseen operator detector
        W[15][9], W[15][10] = 0.5, -0.5 # Prior score & cost trade-off
        b = [0.0] * LATENT_DIM
        return W, b

    @staticmethod
    def _init_action_encoder(weights=None):
        if weights is not None:
            return weights["W"], weights["b"]
        # W_A is ACTION_DIM x ACTION_DIM (16 x 16)
        W = [[0.0] * ACTION_DIM for _ in range(ACTION_DIM)]
        for i in range(ACTION_DIM):
            W[i][i] = 1.0
        b = [0.0] * ACTION_DIM
        return W, b

    @staticmethod
    def _init_transition_predictor(weights=None):
        if weights is not None:
            return weights["W"], weights["b"]
        # W_P is LATENT_DIM x (LATENT_DIM + ACTION_DIM) (16 x 32)
        W = [[0.0] * (LATENT_DIM + ACTION_DIM) for _ in range(LATENT_DIM)]
        # Action updates mapped into latent transitions:
        # z_hat_after = z_before + tanh(W_P [z_before; u])
        W[0][16 + 0] = 0.8    # Node count delta -> latent scale
        W[1][16 + 1] = 0.8    # Loop depth delta -> latent loop depth
        W[2][16 + 2] = 0.5    # Branch delta
        W[3][16 + 3] = 0.5    # Call delta
        W[4][16 + 4] = 0.5    # Arith op delta

        # Latent score coordinate (index 9) influenced by treatment, control, and role deltas:
        W[9][16 + 10] = -0.50  # control_modified penalty
        W[9][16 + 11] = -0.30  # signature_changed penalty
        W[9][16 + 12] = +0.35  # clean treatment reward
        W[9][16 + 8] = -0.15   # LR decrease reward (negative ratio yields positive delta)
        W[9][16 + 9] = +0.06   # WD increase reward

        # Notice: Nominal transition predictor does NOT anticipate novel unseen operators (W[8], W[14] zeroed)
        # causing large latent prediction error on unseen operator injections.
        b = [0.0] * LATENT_DIM
        return W, b

    @staticmethod
    def _init_score_head():
        # Score readout from latent vector z_hat (16 dimensions)
        w = [0.0] * LATENT_DIM
        w[9] = 0.40   # Core outcome feature
        w[1] = -0.10  # Excess loop depth penalty
        w[7] = -0.10  # Deep loop penalty
        w[8] = -0.15  # Unseen operator penalty
        w[12] = 0.05  # Scale stability
        return w

    @staticmethod
    def _init_cost_head():
        # Cost readout from latent vector
        w = [0.0] * LATENT_DIM
        w[1] = 0.40   # Loop depth increases wall-clock cost
        w[10] = 0.30  # Prior cost persistence
        w[0] = 0.05   # AST size
        return w

    def encode_state(self, state, code):
        """E(state, code) -> z in R^16."""
        summary = _extract_ast_summary(code)
        s = [
            float(summary["node_count"]) / 40.0,
            float(summary["loop_depth"]),
            float(summary["branch_count"]) / 5.0,
            float(summary["call_count"]) / 5.0,
            float(summary["arith_op_count"]) / 5.0,
            float(len(summary["function_names"])) / 5.0,
            1.0 if summary["has_while"] else 0.0,
            1.0 if summary["has_deep_loop"] else 0.0,
            1.0 if summary["has_unseen_op"] else 0.0,
            float((state or {}).get("score", 0.5)),
            float((state or {}).get("cost", 1.0)) / 5.0,
            float((state or {}).get("step", 0.0)) / 100.0,
        ]
        raw_z = _mat_vec_mul(self.W_E, s)
        z = [math.tanh(raw_z[i] + self.b_E[i]) for i in range(LATENT_DIM)]
        return z, summary

    def encode_action(self, base_code, candidate_code):
        """A(diff) -> u in R^16."""
        diff = encode_code_diff(base_code, candidate_code)
        sig = diff["obligation_signals"]
        ast_d = diff["ast_delta"]
        param_d = diff["parameter_delta"]

        lr_ratio = 0.0
        wd_ratio = 0.0
        for role, ratio in param_d["numeric_log10_ratios"].items():
            r_lower = role.lower()
            if "lr" in r_lower or "learning_rate" in r_lower:
                lr_ratio += ratio
            elif "weight_decay" in r_lower or "wd" in r_lower:
                wd_ratio += ratio

        added_ops = set(ast_d.get("operators_added", {}).keys())
        unseen_ops_added = added_ops - KNOWN_OPS
        has_unseen_op_action = len(unseen_ops_added) > 0

        control_mod = 1.0 if sig["control_modified"] else 0.0
        treatment_mod = 1.0 if ("treatment" in ast_d["functions_modified"] and not control_mod) else 0.0

        a = [
            float(ast_d["node_count_delta"]) / 20.0,
            float(ast_d["loop_depth_delta"]),
            float(ast_d["branch_count_delta"]),
            float(sum(ast_d["calls_added"].values()) - sum(ast_d["calls_removed"].values())),
            float(sum(ast_d["operators_added"].values()) - sum(ast_d["operators_removed"].values())),
            float(sig["division_or_pow_added"]),
            float(len(ast_d["functions_modified"])),
            float(len(param_d["changed_bindings"])),
            lr_ratio,
            wd_ratio,
            control_mod,
            1.0 if sig["signature_changed"] else 0.0,
            treatment_mod,
            1.0 if has_unseen_op_action else 0.0,
            float(len(param_d["sign_flips"])),
            float(max((abs(v) for v in param_d["numeric_log10_ratios"].values()), default=0.0)),
        ]
        raw_u = _mat_vec_mul(self.W_A, a)
        u = [math.tanh(raw_u[i] + self.b_A[i]) for i in range(ACTION_DIM)]
        return u, diff

    def predict_latent(self, z_before, u):
        """P(z_before, u) -> z_hat_after in R^16."""
        zu = list(z_before) + list(u)
        delta_raw = _mat_vec_mul(self.W_P, zu)
        delta_z = [math.tanh(delta_raw[i] + self.b_P[i]) for i in range(LATENT_DIM)]
        z_hat_after = [z_before[i] + delta_z[i] for i in range(LATENT_DIM)]
        return z_hat_after

    def predict(self, before_state, base_code, candidate_code, parameters=None):
        _code_only(parameters)
        z_before, base_summary = self.encode_state(before_state, base_code)
        u, diff = self.encode_action(base_code, candidate_code)
        z_hat_after = self.predict_latent(z_before, u)

        # Target post-action state encoded directly from candidate_code
        # acts as stopgrad(z_after) for latent prediction error computation:
        z_after, cand_summary = self.encode_state(before_state, candidate_code)

        # Latent prediction error: ||z_hat_after - stopgrad(z_after)||_2
        err_vec = [z_hat_after[i] - z_after[i] for i in range(LATENT_DIM)]
        latent_error = round(_norm2(err_vec), 6)

        # Readout score & cost
        sig = diff["obligation_signals"]
        raw_score = 0.50 + _dot(self.W_score, z_hat_after)
        if sig["control_modified"]:
            raw_score -= 0.40
        if sig["signature_changed"]:
            raw_score -= 0.20
        score = round(max(0.0, min(1.0, raw_score)), 6)

        raw_cost = 1.0 + _dot(self.W_cost, z_hat_after)
        cost = round(max(0.01, raw_cost), 4)

        # Epistemic uncertainty detection: base + latent error + structural indicators
        base_uncertainty = 0.20
        epistemic_term = 0.35 * latent_error
        control_term = 0.25 if sig["control_modified"] else 0.0
        sig_term = 0.20 if sig["signature_changed"] else 0.0
        div_term = 0.10 if sig["division_or_pow_added"] else 0.0

        uncertainty = round(
            max(0.05, min(0.95, base_uncertainty + epistemic_term + control_term + sig_term + div_term)),
            6,
        )

        obligations = {
            "control_preserved": not sig["control_modified"],
            "signature_preserved": not sig["signature_changed"],
            "loop_depth_bounded": diff["ast_delta"]["loop_depth_delta"] <= 0,
            "clean_treatment_only": (
                "treatment" in diff["ast_delta"]["functions_modified"]
                and not sig["control_modified"]
                and not sig["signature_changed"]
            ),
        }

        after_state = {
            "score": score,
            "cost": cost,
            "latent_state": [round(val, 6) for val in z_hat_after],
            "obligations": obligations,
        }

        return {
            "score": score,
            "outcome_score": score,
            "cost": cost,
            "uncertainty": uncertainty,
            "after_state": after_state,
            "predicted_obligation_discharge": obligations,
            "latent_error": latent_error,
            "action_diff_sha256": diff["action_diff_sha256"],
            "model": "LatentCodeDiffJEPA",
        }

    def fit(self, records, l2_reg=1.0):
        """Fit transition operator and readout heads on empirical transition records."""
        ZU = []
        DeltaZ = []
        ZHat = []
        Targets = []

        for raw in records:
            r = _extract_record(raw)
            if not r["base_code"] or not r["candidate_code"]:
                continue
            _code_only(r["parameters"])
            z_b, _ = self.encode_state(r["before_state"], r["base_code"])
            u, _ = self.encode_action(r["base_code"], r["candidate_code"])
            z_a, _ = self.encode_state(r["after_state"] or r["before_state"], r["candidate_code"])

            zu = list(z_b) + list(u)
            delta = [z_a[i] - z_b[i] for i in range(LATENT_DIM)]
            ZU.append(zu)
            DeltaZ.append(delta)
            ZHat.append(z_a)
            Targets.append([r["outcome_score"], r["cost"]])

        if not ZU:
            return self

        # Fit W_P: DeltaZ ~ ZU
        # (len(ZU) x 32) -> W_P (16 x 32)
        W_P_sol = _ridge_fit(ZU, DeltaZ, l2_reg=l2_reg)
        self.W_P = _transpose(W_P_sol)

        # Fit readout heads: Targets ~ ZHat
        W_readout = _ridge_fit(ZHat, Targets, l2_reg=l2_reg)
        self.W_score = [row[0] for row in W_readout]
        self.W_cost = [row[1] for row in W_readout]
        return self


# ---------------------------------------------------------------------------
# Global Singleton & Public APIs
# ---------------------------------------------------------------------------

_DEFAULT_JEPA = LatentCodeDiffJEPA()
_DEFAULT_RIDGE = DirectCodeDiffRidge()
_DEFAULT_BAG = BagOfNumbersBaseline()


def predict_transition(before_state, base_code, candidate_code, parameters=None, model=None):
    """Zero-shot calibrated transition prediction.

    Evaluates (before_state, base_code, candidate_code) and returns
    (score, uncertainty, cost, after_state, predicted_obligation_discharge, latent_error).
    """
    if model is None or model in ("cpu_jepa", "latent_jepa", "default"):
        return _DEFAULT_JEPA.predict(before_state, base_code, candidate_code, parameters)
    if model in ("ridge", "direct_code_diff_ridge"):
        return _DEFAULT_RIDGE.predict(before_state, base_code, candidate_code, parameters)
    if model in ("bag", "bag_of_numbers", "baseline"):
        return _DEFAULT_BAG.predict(before_state, base_code, candidate_code, parameters)
    if hasattr(model, "predict"):
        return model.predict(before_state, base_code, candidate_code, parameters)
    raise ValueError(f"Unknown transition model: {model}")


def fit_transitions(records, model_type="latent_jepa", l2_reg=1.0):
    """Fit a new transition model instance on a list of transition dicts."""
    if model_type in ("latent_jepa", "cpu_jepa", "jepa"):
        model = LatentCodeDiffJEPA()
    elif model_type in ("ridge", "direct_code_diff_ridge"):
        model = DirectCodeDiffRidge()
    elif model_type in ("bag", "bag_of_numbers", "baseline"):
        model = BagOfNumbersBaseline()
    else:
        raise ValueError(f"Unsupported model_type: {model_type}")
    model.fit(records, l2_reg=l2_reg)
    return model

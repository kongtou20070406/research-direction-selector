"""PySR to RDS dialect expression adapter.

Translates candidate expressions produced by PySR/SymPy into the bounded
RDS expression dialect (add/sub/mul/div/pow/exp/log/sin/cos/sqrt/pi, exact
rationals and bounded constants).

Rejects non-compliant constructs fail-closed with explicit reasons (e.g.,
rational exponent with denominator > 8, custom/unsupported functions,
undeclared variables, or empty expressions), and forbids silent rewrites.
"""
from fractions import Fraction
import ast
from pathlib import Path
import sys

# Locate and import rds_compass_model from scripts/
ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(ROOT / 'scripts')
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
import rds_compass_model as model

MAX_NODES = model.MAX_RULEBOOK_NODES  # 64
MAX_POWER = model.MAX_POWER           # 8
MAX_ROOT = model.MAX_ROOT             # 8
MAX_BITS = model.MAX_BITS             # 512

CALLS = {
    'abs': 1, 'floor': 1, 'ceil': 1, 'sqrt': 1,
    'exp': 1, 'log': 1, 'sin': 1, 'cos': 1,
    'min': 2, 'max': 2, 'ite': 3
}


class AdapterRejection(Exception):
    """Raised when an expression construct violates the RDS bounded dialect."""


def _eval_const_expr(node):
    """Evaluate an AST expression node to a Fraction if it is pure constant."""
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return Fraction(repr(node.value)) if isinstance(node.value, float) else Fraction(node.value)
    if isinstance(node, ast.UnaryOp):
        inner = _eval_const_expr(node.operand)
        if inner is None:
            return None
        if isinstance(node.op, ast.UAdd):
            return inner
        if isinstance(node.op, ast.USub):
            return -inner
        return None
    if isinstance(node, ast.BinOp):
        left, right = _eval_const_expr(node.left), _eval_const_expr(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                return None
            return left / right
        if isinstance(node.op, ast.Pow):
            if right.denominator != 1:
                return None
            return left ** int(right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == 'Rational' and len(node.args) == 2:
            p, q = _eval_const_expr(node.args[0]), _eval_const_expr(node.args[1])
            if p is not None and q is not None and q != 0 and p.denominator == 1 and q.denominator == 1:
                return Fraction(int(p), int(q))
        if node.func.id in ('Integer', 'Float') and len(node.args) == 1:
            return _eval_const_expr(node.args[0])
    return None


class PowerRewriter(ast.NodeTransformer):
    """Rewrite unsupported fractional powers base**exp to exp(exp * log(base))."""
    def __init__(self):
        super().__init__()
        self.rewrote = False

    def visit_BinOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Pow):
            exp_val = _eval_const_expr(node.right)
            if exp_val is not None and (exp_val.denominator > MAX_ROOT or abs(exp_val) > MAX_POWER):
                self.rewrote = True
                return ast.Call(
                    func=ast.Name(id='exp', ctx=ast.Load()),
                    args=[
                        ast.BinOp(
                            left=node.right,
                            op=ast.Mult(),
                            right=ast.Call(
                                func=ast.Name(id='log', ctx=ast.Load()),
                                args=[node.left],
                                keywords=[]
                            )
                        )
                    ],
                    keywords=[]
                )
        return node


def _convert_to_rds_ast(tree, variables=None):
    """Compile Python AST to RDS JSON AST and collect domain constraints.

    Returns:
        (ast_dict, domain_constraints: set)
    Raises:
        AdapterRejection: on unsupported constructs
    """
    node_count = 0
    domain_notes = set()

    def visit(node):
        nonlocal node_count
        node_count += 1
        if node_count > MAX_NODES:
            raise AdapterRejection(
                f"表达式节点数超过上限 {MAX_NODES} (expression size exceeds {MAX_NODES} nodes)"
            )

        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, float):
                raise AdapterRejection(f"不支持的常数类型: {type(node.value).__name__}")
            frac = Fraction(repr(node.value)) if isinstance(node.value, float) else Fraction(node.value)
            if frac.numerator.bit_length() > MAX_BITS or frac.denominator.bit_length() > MAX_BITS:
                raise AdapterRejection("常数精度超过 512 位限制 (constant precision exceeds 512 bits)")
            return {'const': model.text(frac)}

        if isinstance(node, ast.Name):
            if node.id == 'pi' and (variables is None or 'pi' not in variables):
                return {'op': 'pi', 'args': []}
            if not node.id.isidentifier() or node.id.startswith('_') or len(node.id) > 64:
                raise AdapterRejection(f"非法变量名: {repr(node.id)}")
            if variables is not None and node.id not in variables:
                raise AdapterRejection(f"变量不在声明集合: {node.id} (variable '{node.id}' not in declared variables)")
            return {'var': node.id}

        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.UAdd):
                return visit(node.operand)
            if isinstance(node.op, ast.USub):
                return {'op': 'neg', 'args': [visit(node.operand)]}
            raise AdapterRejection(f"不支持的单目运算符: {type(node.op).__name__}")

        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Pow):
                left_ast = visit(node.left)
                exp_frac = _eval_const_expr(node.right)
                if exp_frac is None:
                    has_var = any(isinstance(n, ast.Name) for n in ast.walk(node.right))
                    if has_var:
                        raise AdapterRejection("pow 指数必须为常量，不支持变量指数 (pow exponent must be a constant, not variable)")
                    raise AdapterRejection("pow 指数无法求值为常量 (pow exponent could not be evaluated to constant)")

                if abs(exp_frac) > MAX_POWER:
                    raise AdapterRejection(
                        f"指数 {exp_frac} 绝对值超过 {MAX_POWER} (exponent {exp_frac} absolute value exceeds {MAX_POWER})"
                    )
                if exp_frac.denominator > MAX_ROOT:
                    raise AdapterRejection(
                        f"指数 {exp_frac} 分母 {exp_frac.denominator} 超过 {MAX_ROOT} (exponent {exp_frac} denominator {exp_frac.denominator} exceeds {MAX_ROOT})"
                    )
                if exp_frac < 0:
                    try:
                        base_text = ast.unparse(node.left)
                        domain_notes.add(f"{base_text} != 0")
                    except Exception:
                        pass
                return {'op': 'pow', 'args': [left_ast, {'const': model.text(exp_frac)}]}

            ops = {
                ast.Add: 'add',
                ast.Sub: 'sub',
                ast.Mult: 'mul',
                ast.Div: 'div',
                ast.Mod: 'mod',
            }
            if type(node.op) not in ops:
                raise AdapterRejection(f"不支持的二元运算符: {type(node.op).__name__}")
            op_name = ops[type(node.op)]
            left_ast = visit(node.left)
            right_ast = visit(node.right)
            if op_name in ('div', 'mod'):
                right_const = _eval_const_expr(node.right)
                if right_const == 0:
                    raise AdapterRejection("除数为零 (division by zero)")
                if right_const is None:
                    try:
                        denom_text = ast.unparse(node.right)
                        domain_notes.add(f"{denom_text} != 0")
                    except Exception:
                        pass
            return {'op': op_name, 'args': [left_ast, right_ast]}

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise AdapterRejection("不支持的方法调用语法 (unsupported call syntax)")
            fname = node.func.id
            if fname in ('Rational', 'Integer', 'Float'):
                frac = _eval_const_expr(node)
                if frac is not None:
                    return {'const': model.text(frac)}
            if fname not in CALLS:
                raise AdapterRejection(f"不支持的函数或自定义函数: {fname} (unsupported function: {fname})")
            if len(node.args) != CALLS[fname] or node.keywords:
                raise AdapterRejection(f"函数 {fname} 参数个数不匹配 (expected {CALLS[fname]} args)")

            args_ast = [visit(arg) for arg in node.args]
            if fname == 'log':
                try:
                    arg_text = ast.unparse(node.args[0])
                    domain_notes.add(f"{arg_text} > 0")
                except Exception:
                    pass
            elif fname == 'sqrt':
                try:
                    arg_text = ast.unparse(node.args[0])
                    domain_notes.add(f"{arg_text} >= 0")
                except Exception:
                    pass
            return {'op': fname, 'args': args_ast}

        if isinstance(node, ast.Compare):
            if len(node.ops) != 1:
                raise AdapterRejection("不支持链式比较 (chained comparisons unsupported)")
            cmps = {ast.Lt: 'lt', ast.LtE: 'le', ast.Eq: 'eq'}
            kind = type(node.ops[0])
            left_ast = visit(node.left)
            right_ast = visit(node.comparators[0])
            if kind in (ast.Gt, ast.GtE):
                return {'op': 'lt' if kind is ast.Gt else 'le', 'args': [right_ast, left_ast]}
            if kind in cmps:
                return {'op': cmps[kind], 'args': [left_ast, right_ast]}
            raise AdapterRejection(f"不支持的比较运算符: {kind.__name__}")

        if isinstance(node, ast.IfExp):
            return {'op': 'ite', 'args': [visit(node.test), visit(node.body), visit(node.orelse)]}

        raise AdapterRejection(f"不支持的语法结构: {type(node).__name__}")

    raw_ast = visit(tree.body)
    normalized = model.normalize(raw_ast, variables=variables, limit=MAX_NODES)
    return normalized, domain_notes


def adapt_pysr_expression(source, variables=None, *, suggest_equivalent=False):
    """Convert a PySR/SymPy/Python expression string to the RDS dialect.

    Args:
        source: Formula string (e.g. 'a*x**2 + b*cos(z) + c').
        variables: Optional collection of declared variable names.
        suggest_equivalent: If True and expression is unsupported due to
            incompatible powers (e.g. c**1.3), also return an equivalent
            rewrite proposal (e.g. exp(1.3*log(c))) with domain notes.

    Returns:
        List of dicts: [{'expression': str, 'supported': bool, 'reason': str|None, 'domain_notes': str|None}]
    """
    if variables is not None:
        variables = set(variables)

    # 1. 检查空表达式
    if not isinstance(source, str) or not source.strip():
        return [{
            'expression': source.strip() if isinstance(source, str) else '',
            'supported': False,
            'reason': '空表达式 (empty expression)',
            'domain_notes': None
        }]

    clean_source = source.strip()

    # 2. 解析 AST
    try:
        tree = ast.parse(clean_source, mode='eval')
    except SyntaxError as exc:
        return [{
            'expression': clean_source,
            'supported': False,
            'reason': f"语法错误 (syntax error): {exc.msg}",
            'domain_notes': None
        }]

    # 3. 转换并验证
    try:
        rds_ast, notes = _convert_to_rds_ast(tree, variables=variables)
        rds_text = model.render(rds_ast)
        notes_str = ', '.join(sorted(notes)) if notes else None
        return [{
            'expression': rds_text,
            'supported': True,
            'reason': None,
            'domain_notes': notes_str
        }]
    except (AdapterRejection, ValueError) as exc:
        reason_msg = str(exc)
        results = [{
            'expression': clean_source,
            'supported': False,
            'reason': reason_msg,
            'domain_notes': None
        }]

        # 4. 如果允许建议等价改写且原式不支持
        if suggest_equivalent:
            try:
                rewriter = PowerRewriter()
                new_tree = rewriter.visit(tree)
                if rewriter.rewrote:
                    equiv_ast, equiv_notes = _convert_to_rds_ast(new_tree, variables=variables)
                    equiv_text = model.render(equiv_ast)
                    results.append({
                        'expression': equiv_text,
                        'supported': True,
                        'reason': None,
                        'domain_notes': ', '.join(sorted(equiv_notes)) if equiv_notes else None
                    })
            except Exception:
                pass

        return results


# Aliases
adapt = adapt_pysr_expression
convert_pysr_to_rds = adapt_pysr_expression

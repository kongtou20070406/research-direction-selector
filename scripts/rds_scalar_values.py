"""Restricted scalar source and rational input values, using only stdlib."""
import ast
from fractions import Fraction
import re


def rational(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or len(value) > 80 or not re.fullmatch(
        r"-?\d+(?:/\d+|\.\d+)?", value
    ):
        raise ValueError("Use a finite integer, decimal string or rational string")
    result = Fraction(value)
    if max(result.numerator.bit_length(), result.denominator.bit_length()) > 256:
        raise ValueError("Rational input too large")
    return result


def parse_source(source):
    if len(source.encode("utf-8")) > 8192:
        raise ValueError("Source exceeds the reference runner limit")
    tree = ast.parse(source)
    if len(list(ast.walk(tree))) > 160:
        raise ValueError("AST exceeds the reference runner limit")
    functions = {}
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name not in {"control", "treatment"}:
            raise ValueError("Only control(x) and treatment(x) definitions are allowed")
        args = fn.args
        if (fn.name in functions or fn.decorator_list or fn.returns or fn.type_comment
                or getattr(fn, "type_params", []) or args.posonlyargs
                or [a.arg for a in args.args] != ["x"] or args.args[0].annotation
                or args.defaults or args.kw_defaults or args.kwonlyargs
                or args.vararg or args.kwarg or len(fn.body) != 1
                or not isinstance(fn.body[0], ast.Return)):
            raise ValueError("Each arm must be exactly def arm(x): return expression")
        expression = fn.body[0].value
        for node in ast.walk(expression):
            if isinstance(node, ast.Constant):
                if type(node.value) is not int or abs(node.value) > 1000000:
                    raise ValueError("AST literals must be small integers; use a/b for fractions")
            elif isinstance(node, ast.Name):
                if node.id != "x":
                    raise ValueError("Only input x is available")
            elif isinstance(node, ast.BinOp):
                if isinstance(node.op, ast.Pow) and (
                    not isinstance(node.right, ast.Constant)
                    or type(node.right.value) is not int or not 0 <= node.right.value <= 4
                ):
                    raise ValueError("Only literal powers 0..4 are allowed")
            elif not isinstance(node, (ast.UnaryOp, ast.Add, ast.Sub, ast.Mult,
                                       ast.Div, ast.Pow, ast.USub, ast.UAdd, ast.Load)):
                raise ValueError("Unsupported AST node: " + type(node).__name__)
        functions[fn.name] = expression
    if set(functions) != {"control", "treatment"}:
        raise ValueError("Both control and treatment are required")
    return functions

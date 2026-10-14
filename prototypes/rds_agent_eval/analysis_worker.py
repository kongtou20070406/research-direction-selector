"""CPU analysis tool shared byte-for-byte by Bare and RDS conditions.

Small Python dialect for CSV analysis; this is not an OS sandbox. Only declared
CSV inputs and one declared JSON output are exposed to candidate code.
"""
import ast
import csv
import json
import math
from pathlib import Path
import statistics
import sys

ATTRS = {"DictReader", "reader", "loads", "dumps", "mean", "median", "stdev", "pstdev",
         "correlation", "sqrt", "log", "log10", "exp", "replace", "strip", "split",
         "splitlines", "items", "values", "keys", "append", "extend", "sort", "get", "index"}
BUILTINS = {name: getattr(__import__("builtins"), name) for name in (
    "abs", "min", "max", "sum", "len", "float", "int", "str", "dict", "list", "tuple",
    "set", "enumerate", "range", "zip", "sorted", "print", "round", "bool", "any", "all",
    "isinstance", "type", "repr", "Exception", "ValueError", "KeyError")}


def validate(code):
    if len(code.encode()) > 16384:
        raise ValueError("Analysis code exceeds 16 KiB")
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef, ast.AsyncFunctionDef)):
            raise ValueError("Imports/classes/async are outside the analysis dialect")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise ValueError("Private names are forbidden")
        if isinstance(node, ast.Attribute) and node.attr not in ATTRS:
            raise ValueError("Attribute is outside the analysis dialect: " + node.attr)
    return tree


def run(code_path, output_path):
    code_path, output_path = Path(code_path).resolve(), Path(output_path).resolve()
    root = Path.cwd().resolve()
    if not code_path.is_relative_to(root) or not output_path.is_relative_to(root):
        raise ValueError("Tool paths escape workspace")
    code = code_path.read_text(encoding="utf-8")

    def read(name):
        path = (root / name).resolve()
        if path.parent != root or path.suffix != ".csv" or path.stat().st_size > 2097152:
            raise ValueError("Only declared small CSV inputs are readable")
        return path.read_text(encoding="utf-8-sig")

    outputs = []
    def emit(value):
        outputs.append(json.loads(json.dumps(value, allow_nan=False)))

    scope = {"__builtins__": BUILTINS, "csv": csv, "json": json,
             "math": math, "statistics": statistics, "read": read, "emit": emit}
    exec(compile(validate(code), str(code_path), "exec"), scope)
    if len(outputs) != 1 or type(outputs[0]) is not dict:
        raise ValueError("Emit exactly one JSON object with measured evidence")
    encoded = json.dumps(outputs[0], indent=2, allow_nan=False)
    if len(encoded.encode()) > 65536:
        raise ValueError("Analysis output exceeds 64 KiB")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(encoded, encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    run(*sys.argv[1:])

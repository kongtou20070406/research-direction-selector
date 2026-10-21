"""Prove that a moved function is the SAME function, not a rewrite.

A refactor that only relocates code has an exact test: parse both sides, rename
the self parameter, and compare the syntax trees. Anything a reviewer would
call a silent behaviour change -- a dropped branch, a flipped comparison, a
changed default, an extra guard -- changes the tree. So an identical tree after
normalisation is a much stronger claim than "the tests still pass", and it is
cheap enough to run on every moved symbol.

Two shapes are supported because the repository uses both:

  function  module-level function -> module-level function. The trees must be
            identical with no renaming at all.
  method    class method -> module-level function. The first parameter is
            renamed (``self`` becomes whatever the caller passes) and every
            route to it is normalised to one marker, so ``self.db`` and
            ``store.db`` compare equal.

The docstring is excluded by default: a moved helper usually gains an
explanation of why it lives in its new home, and that is not a behaviour
change. ``--keep-docstring`` restores the strict comparison.

Usage::

    python -B tools/verify_pure_move.py --mode method \
        --source scripts/rds_project.py --class ProjectStore \
        --target scripts/rds_project_strategies.py \
        --map D:/tmp/map.json

The map is ``{"OldName": "new_name"}``. Exit code 0 means every pair matched,
1 means at least one did not, 2 means the invocation itself was wrong.
"""

import argparse
import ast
import json
import sys

SELF = '\x00self'


class _Rename(ast.NodeTransformer):
    """Rewrite every mention of the bound receiver to one marker."""

    def __init__(self, names):
        self.names = set(names)

    def visit_Name(self, node):
        if node.id in self.names:
            return ast.copy_location(ast.Name(id=SELF, ctx=node.ctx), node)
        return node

    def visit_arg(self, node):
        if node.arg in self.names:
            return ast.copy_location(ast.arg(arg=SELF, annotation=node.annotation,
                                             type_comment=node.type_comment), node)
        return node


def _strip_docstring(body):
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def _signature_rest(args):
    """The parameter list minus the receiver, as comparable text."""
    out = []
    for a in args.posonlyargs + args.args:
        out.append((a.arg, ast.dump(a.annotation) if a.annotation else None))
    for a in args.kwonlyargs:
        out.append((a.arg, ast.dump(a.annotation) if a.annotation else None))
    defaults = [ast.dump(d) for d in args.defaults]
    kwdefaults = [ast.dump(d) if d else None for d in args.kw_defaults]
    return {
        'positional': out,
        'defaults': defaults,
        'kwonly': [a.arg for a in args.kwonlyargs],
        'kwdefaults': kwdefaults,
        'vararg': args.vararg.arg if args.vararg else None,
        'kwarg': args.kwarg.arg if args.kwarg else None,
    }


def _find_class(tree, cls):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return node
    raise SystemExit(f'class {cls!r} not found')


def _find_method(cls_node, name):
    for node in cls_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise SystemExit(f'method {name!r} not found')


def _find_function(tree, name):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise SystemExit(f'function {name!r} not found')


def _receiver_names(side, node):
    args = node.args
    positional = args.posonlyargs + args.args
    if not positional:
        return ()
    return (positional[0].arg,)


def compare(mode, source_tree, target_tree, cls, old, new, keep_docstring):
    """Return (ok, detail) for one moved symbol."""
    if mode == 'method':
        node = _find_method(_find_class(source_tree, cls), old)
        receiver = _receiver_names('source', node)
        source_body = node.body
        source_rest = _signature_rest(node.args)
        source_rest['positional'] = source_rest['positional'][1:]
    else:
        node = _find_function(source_tree, old)
        receiver = ()
        source_body = node.body
        source_rest = _signature_rest(node.args)

    target = _find_function(target_tree, new)
    target_receiver = _receiver_names('target', target) if mode == 'method' else ()
    target_body = target.body
    target_rest = _signature_rest(target.args)
    if mode == 'method':
        target_rest['positional'] = target_rest['positional'][1:]

    if not keep_docstring:
        source_body = _strip_docstring(source_body)
        target_body = _strip_docstring(target_body)

    renamed_source = _Rename(receiver).visit(ast.Module(body=list(source_body), type_ignores=[]))
    renamed_target = _Rename(target_receiver).visit(ast.Module(body=list(target_body), type_ignores=[]))

    left = ast.dump(renamed_source, annotate_fields=True, include_attributes=False)
    right = ast.dump(renamed_target, annotate_fields=True, include_attributes=False)

    problems = []
    if left != right:
        problems.append('body differs')
    if source_rest != target_rest:
        for key in source_rest:
            if source_rest[key] != target_rest.get(key):
                problems.append(f'parameter {key}: {source_rest[key]!r} != {target_rest.get(key)!r}')

    if problems:
        detail = '; '.join(problems)
        if left != right:
            detail += f'\n    source: {left[:600]}\n    target: {right[:600]}'
        return False, detail
    return True, 'identical tree'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode', choices=('function', 'method'), required=True)
    ap.add_argument('--source', required=True)
    ap.add_argument('--target', required=True)
    ap.add_argument('--class', dest='cls', default='')
    ap.add_argument('--map', required=True, help='JSON file: {"OldName": "new_name"}')
    ap.add_argument('--keep-docstring', action='store_true')
    args = ap.parse_args(argv)

    if args.mode == 'method' and not args.cls:
        ap.error('--class is required in method mode')

    with open(args.source, encoding='utf-8') as handle:
        source_tree = ast.parse(handle.read())
    with open(args.target, encoding='utf-8') as handle:
        target_tree = ast.parse(handle.read())
    with open(args.map, encoding='utf-8') as handle:
        mapping = json.load(handle)

    failed = 0
    for old, new in mapping.items():
        ok, detail = compare(args.mode, source_tree, target_tree, args.cls, old, new,
                             args.keep_docstring)
        print(f'{"PASS" if ok else "FAIL"}  {old} -> {new}  ({detail})')
        if not ok:
            failed += 1

    print(f'\n{len(mapping) - failed}/{len(mapping)} moved symbols are the same tree')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

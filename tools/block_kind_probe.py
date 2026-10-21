"""Reclassify every `_db`-borrowing block by who is borrowing and why.

The first pass counted every `with X._db(...)` block as external coupling. That
was wrong: a projection function spelled `def events(store, db=None)` borrows a
connection only when the caller did not supply a transaction, which is the
pattern the codebase wants. Counting those as debt inflates the number and
points the cleanup at code that is already correct.

Three classes:
  injection  -- the block sits in a function that accepts `db=None`; benign.
  in-class   -- the receiver is `self`; the class using its own connection.
  external   -- business code borrowing a connection. This is the debt.

Usage: python -B block_kind_probe.py <repo-root>
"""
import ast
import collections
import os
import sys

SKIP = {'.git', '.agents', '__pycache__', '.rds', 'node_modules', '.venv'}
VERBS = {'select', 'insert', 'update', 'delete', 'pragma', 'create', 'replace',
         'drop', 'alter', 'begin', 'commit', 'with'}
# Every way a caller can borrow a connection or run a read against one. `_db`
# is the raw door; `readonly` and `read_through` are the named public ones. A
# probe that only knows `_db` reports a smaller number after a rename without
# anything getting narrower, which is what happened once already.
MANAGERS = {'_db': 'borrow', 'readonly': 'readonly', 'read_through': 'projection'}


def sql_verbs(node):
    found = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            words = inner.value.strip().lstrip('(').split(None, 1)
            if words:
                word = words[0].lower().rstrip(';')
                if word in VERBS:
                    found.add(word)
    return found


def takes_db(node):
    """Does this function signature accept `db` with a default?"""
    args = node.args
    positional = args.posonlyargs + args.args
    defaults = args.defaults
    offset = len(positional) - len(defaults)
    for index, arg in enumerate(positional):
        if arg.arg == 'db' and index >= offset:
            return True
    return any(arg.arg == 'db' for arg in args.kwonlyargs)


def receiver(node):
    func = node.func
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name):
            return func.value.id
        return ast.unparse(func.value)
    return '?'


def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else '.'
    kinds = collections.Counter()
    detail = collections.defaultdict(list)
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for name in files:
            if not name.endswith('.py'):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, repo).replace(os.sep, '/')
            if rel.split('/')[0] not in ('scripts', 'benchmark', 'examples'):
                continue
            tree = ast.parse(open(path, 'rb').read())
            # Map every `with` node to the function that encloses it.
            owner = {}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for inner in ast.walk(node):
                        owner.setdefault(id(inner), node)
            for node in ast.walk(tree):
                if not isinstance(node, ast.With):
                    continue
                for item in node.items:
                    expr = item.context_expr
                    if not (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
                            and expr.func.attr in MANAGERS):
                        continue
                    who = receiver(expr)
                    manager = MANAGERS[expr.func.attr]
                    if manager == 'borrow':
                        # `_db(True)` already opens a read-only connection; only
                        # the bare form hands out a writer.
                        first = expr.args[0] if expr.args else None
                        if isinstance(first, ast.Constant) and first.value is True:
                            manager = 'readonly'
                    func = owner.get(id(node))
                    if func is not None and takes_db(func):
                        kind = 'injection'
                    elif who == 'self' and rel == 'scripts/rds_project.py':
                        kind = 'in-class'
                    else:
                        kind = 'external'
                    verbs = sql_verbs(node)
                    # `begin` opens a transaction, it does not change a row. A
                    # block whose only verbs are select/pragma/begin is a read.
                    mutating = verbs & {'insert', 'update', 'delete', 'replace',
                                        'create', 'drop', 'alter'}
                    if manager == 'projection':
                        what = 'projects'
                    elif mutating:
                        what = 'writes'
                    elif manager == 'readonly' or verbs:
                        what = 'reads'
                    else:
                        what = 'delegates'
                    kinds[(kind, what)] += 1
                    detail[kind].append((rel, node.lineno, who, sorted(verbs)))

    print('-- blocks by kind and what they do --')
    for (kind, what), count in sorted(kinds.items(), key=lambda kv: (kv[0][0], -kv[1])):
        print(f'{count:4d}  {kind:10s} {what}')
    total = collections.Counter()
    for (kind, _), count in kinds.items():
        total[kind] += count
    print('\n-- totals --')
    for kind, count in total.most_common():
        files = len({row[0] for row in detail[kind]})
        print(f'{count:4d}  {kind:10s} in {files} files')
    print('\n-- external blocks, file by file --')
    per_file = collections.Counter(row[0] for row in detail['external'])
    for rel, count in per_file.most_common():
        print(f'{count:3d}  {rel}')

    if '--detail' in sys.argv:
        print('\n-- every external block --')
        for rel, line, who, verbs in sorted(detail['external']):
            label = ' '.join(verbs) if verbs else '(no SQL: delegates)'
            print(f'{rel}:{line}  on {who}  {label}')


if __name__ == '__main__':
    main()

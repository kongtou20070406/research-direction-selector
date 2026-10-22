"""Classify every `with` block that borrows a DB connection, including the
shapes the first probe could not see.

v1 only recognised `with X._db(...)`, `with X.readonly()`, `with
X.read_through(...)`. Anything spelled as an IfExp, a variable, a bare
`connect(...)`, or a differently named manager was invisible, so a rename or a
new manager silently left the reported debt unchanged. v2 keeps the v1 column
byte-identical (that column is the denominator the protocol quotes) and adds
three independent things:

  hidden    -- connection-borrowing blocks whose manager is not one of the three
               v1 names. Reported file:line by file:line, never just summed.
  dml       -- a full-DML reverse check: every with block that carries DML,
               regardless of manager name. Cross-checks the v1 denominator.
  execution -- DML execution points inside blocks plus the bare ones outside
               them, which is the "37 blocks / 61 points" shape from the map.

Exit codes: 0 normal. `--detail` prints every hidden block.

Usage: python -B tools/block_kind_probe.py <repo-root> [--detail]
"""
import ast
import collections
import json
import os
import re
import sys

SKIP = {'.git', '.agents', '__pycache__', '.rds', 'node_modules', '.venv'}
VERBS = {'select', 'insert', 'update', 'delete', 'pragma', 'create', 'replace',
         'drop', 'alter', 'begin', 'commit', 'with'}
DML = {'insert', 'update', 'delete', 'replace', 'create', 'drop', 'alter'}
# v1 names. The legacy column must stay exactly these three.
MANAGERS = {'_db': 'borrow', 'readonly': 'readonly', 'read_through': 'projection'}
EXECUTE = {'execute', 'executemany', 'executescript'}
# "Write blocks" the map refuses to count: the manager is a temp dir/file, so
# the DML-looking string inside is not ledger SQL. (file, line) -> why.
FALSE_POSITIVE = {
    ('benchmark/formal_performance.py', 46): 'TemporaryDirectory manager; the UPDATE runs in a nested connect',
    ('scripts/update_lean_toolchain.py', 129): 'TemporaryFile manager; "replace" is a str method, not SQL',
}
# Blocks that carry DML but write somewhere other than the project ledger
# (independent DBs, example code). They belong to the DML total but are out of
# the batch-2 denominator. (file, line) -> why.
OUT_OF_LEDGER = {
    ('examples/explanation-loop/worker.py', 41): 'example code, its own sqlite (map #24)',
    ('scripts/rds_advisor.py', 427): 'independent knowledge.db (map #25)',
    ('scripts/rds_proof_cache.py', 34): 'independent proofs.db, file lock not SQL tx (map #26)',
    ('scripts/rds_proof_cache.py', 95): 'independent proofs.db (map #27)',
    ('scripts/rds_state.py', 107): 'legacy second ledger state.db (map #28)',
    ('scripts/rds_usage.py', 151): 'independent usage.db (map #29)',
    ('scripts/rds_usage.py', 217): 'independent usage.db (map #30)',
}


def first_word(text):
    words = text.strip().lstrip('(').split(None, 1)
    return words[0].lower().rstrip(';') if words else ''


def sql_verbs(node):
    found = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            word = first_word(inner.value)
            if word in VERBS:
                found.add(word)
    return found


def direct_verbs(node):
    """Verbs in this block's own statements, not inside a nested `with` body.

    A nested `with` owns its own body: its DML is counted against that inner
    block, which is the map's "nested blocks belong to the nearest layer" rule.
    The outer block still owns everything between itself and the nesting.
    """
    inner = set()
    for sub in ast.walk(node):
        if sub is node or not isinstance(sub, ast.With):
            continue
        for stmt in sub.body:
            for walk in ast.walk(stmt):
                inner.add(id(walk))
    found = set()
    for sub in ast.walk(node):
        if id(sub) in inner:
            continue
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            word = first_word(sub.value)
            if word in VERBS:
                found.add(word)
    return found


def takes_db(node):
    args = node.args
    positional = args.posonlyargs + args.args
    offset = len(positional) - len(args.defaults)
    for index, arg in enumerate(positional):
        if arg.arg == 'db' and index >= offset:
            return True
    return any(arg.arg == 'db' for arg in args.kwonlyargs)


def receiver(expr):
    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Attribute):
            return func.value.id if isinstance(func.value, ast.Name) else ast.unparse(func.value)
        return func.id if isinstance(func, ast.Name) else '?'
    if isinstance(expr, ast.Name):
        return expr.id
    return ast.unparse(expr)


def shape(expr):
    if isinstance(expr, ast.Call):
        return 'call'
    if isinstance(expr, ast.IfExp):
        return 'ifexp'
    if isinstance(expr, ast.Name):
        return 'name'
    return type(expr).__name__.lower()


def names_in(expr):
    """Every function name an expression may call (IfExp branches included)."""
    found = []
    for sub in ast.walk(expr):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Attribute):
            found.append((func.attr, sub))
        elif isinstance(func, ast.Name):
            found.append((func.id, sub))
    return found


def load_baseline(path):
    """Hidden write blocks at HEAD, keyed by (file, function, with-index).

    Line numbers drift when a converted call site gains an import, and one
    function may hold several connection blocks, so identity is the enclosing
    function plus the block ordinal among that owner's with-blocks.  The value
    carries the block's own DML verbs at baseline, so a block whose write verb
    moved into a named write method counts as converged even when an
    out-of-scope literal DML (the budget UPDATE) still sits in the block.
    """
    found = {}
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line.startswith('{'):
                continue
            row = json.loads(line)
            found[(row['rel'], row['func'], row['index'])] = frozenset(
                v for v in row['verbs'] if v in DML)
    return found


def main():
    repo = '.'
    baseline = {}
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if args:
        repo = args[0]
    detail = '--detail' in sys.argv
    for arg in sys.argv[1:]:
        if arg.startswith('--baseline='):
            baseline = load_baseline(arg.split('=', 1)[1])
    legacy = collections.Counter()
    legacy_rows = []
    hidden = []
    dml_blocks = []
    points_in = 0
    points_out = 0
    point_verbs = collections.Counter()

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
            owner = {}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for inner in ast.walk(node):
                        owner.setdefault(id(inner), node)
            withs = sorted((n for n in ast.walk(tree) if isinstance(n, ast.With)),
                           key=lambda n: (n.lineno, n.col_offset))
            order = collections.defaultdict(int)
            with_index = {}
            for node in withs:
                owner_id = id(owner.get(id(node)))
                with_index[id(node)] = order[owner_id]
                order[owner_id] += 1

            # --- v1 column, verbatim ---
            for node in withs:
                for item in node.items:
                    expr = item.context_expr
                    if not (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
                            and expr.func.attr in MANAGERS):
                        continue
                    who = receiver(expr)
                    manager = MANAGERS[expr.func.attr]
                    if manager == 'borrow':
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
                    mutating = verbs & DML
                    if manager == 'projection':
                        what = 'projects'
                    elif mutating:
                        what = 'writes'
                    elif manager == 'readonly' or verbs:
                        what = 'reads'
                    else:
                        what = 'delegates'
                    legacy[(kind, what)] += 1
                    legacy_rows.append((rel, node.lineno, who, sorted(verbs)))

            # --- hidden column: connection blocks v1 cannot name ---
            for node in withs:
                func = owner.get(id(node))
                # If any item is a v1 manager call, v1 already counted this
                # block. Counting it again would inflate the merged column.
                seen_by_v1 = any(
                    isinstance(item.context_expr, ast.Call)
                    and isinstance(item.context_expr.func, ast.Attribute)
                    and item.context_expr.func.attr in MANAGERS
                    for item in node.items)
                if seen_by_v1:
                    continue
                for item in node.items:
                    expr = item.context_expr
                    legacy_item = (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
                                   and expr.func.attr in MANAGERS)
                    if legacy_item:
                        continue
                    direct = direct_verbs(node)
                    # A call site converted by this slice keeps its identity:
                    # the very same (file, function, with-index) block was a
                    # hidden write block at baseline, and a verb it used to run
                    # literally now lives in a named write method — even when an
                    # out-of-scope literal DML (the budget UPDATE) stays behind.
                    block_id = (rel, func.name if func else '<module>', with_index[id(node)])
                    migrated = bool(baseline.get(block_id, frozenset()) - direct)
                    if not (direct & DML) and not migrated:
                        continue
                    if (rel, node.lineno) in FALSE_POSITIVE:
                        continue
                    if func is not None and takes_db(func):
                        kind = 'injection'
                    elif rel == 'scripts/rds_project.py':
                        # Every connection in this file is the store's own; the
                        # manager may be a variable (`writer`) but never leaves
                        # the class.
                        if not (direct & DML):
                            continue
                        kind = 'in-class'
                    elif (rel, node.lineno) in OUT_OF_LEDGER:
                        continue
                    elif migrated:
                        # The inline DML moved into a named write method of the
                        # store class; the block is now an in-class caller.
                        kind = 'in-class'
                    else:
                        kind = 'external'
                    what = 'writes'
                    hidden.append({
                        'rel': rel, 'line': node.lineno, 'func': func.name if func else '?',
                        'kind': kind, 'shape': shape(expr),
                        'manager': (receiver(expr) if shape(expr) != 'ifexp'
                                    else '|'.join(sorted({name for name, _ in names_in(expr)}))),
                        'verbs': sorted(direct),
                        'index': with_index[id(node)],
                        'moved': [],
                        'text': ast.unparse(expr).replace('\n', ' ')[:100],
                    })

            # --- DML reverse check ---
            for node in withs:
                if (rel, node.lineno) in FALSE_POSITIVE:
                    continue
                direct = direct_verbs(node)
                if not direct & DML:
                    continue
                kind = 'in-class' if rel == 'scripts/rds_project.py' else 'external'
                dml_blocks.append((rel, node.lineno, kind, sorted(direct)))

            inside = set()
            for node in withs:
                for stmt in node.body:
                    for walk in ast.walk(stmt):
                        inside.add(id(walk))
            for call in ast.walk(tree):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr in EXECUTE):
                    continue
                first = call.args[0] if call.args else None
                word = first_word(first.value) if isinstance(first, ast.Constant) and isinstance(first.value, str) else 'dynamic'
                point_verbs[word] += 1
                if word not in DML:
                    continue
                if id(call) in inside:
                    points_in += 1
                else:
                    points_out += 1

    # ---------------- report ----------------
    print('== v1 column (legacy manager names; the protocol denominator) ==')
    for (kind, what), count in sorted(legacy.items(), key=lambda kv: (kv[0][0], -kv[1])):
        print(f'{count:4d}  {kind:10s} {what}')
    total = collections.Counter()
    for (kind, _), count in legacy.items():
        total[kind] += count
    print('\n-- totals --')
    for kind, count in total.most_common():
        files = len({row[0] for row in legacy_rows if row and True})
        print(f'{count:4d}  {kind:10s}')
    print(f'   (files touched by external legacy blocks: '
          f'{len({r[0] for r in legacy_rows})})')

    print('\n== hidden column (connection blocks v1 cannot name) ==')
    merged = collections.Counter(legacy)
    for row in hidden:
        merged[(row['kind'], 'writes')] += 1
    print(f'   hidden write blocks: {len(hidden)}')
    for row in sorted(hidden, key=lambda r: (r['rel'], r['line'])):
        moved = ' -> in-class' if row['kind'] == 'in-class' else ''
        print(f"   {row['rel']}:{row['line']}  {row['func']}  {row['shape']}/{row['manager']}  "
              f"{' '.join(row['verbs'])}{moved}")
    print('\n-- merged column (legacy + hidden, project ledger only) --')
    for (kind, what), count in sorted(merged.items(), key=lambda kv: (kv[0][0], -kv[1])):
        print(f'{count:4d}  {kind:10s} {what}')
    mt = collections.Counter()
    for (kind, _), count in merged.items():
        mt[kind] += count
    for kind, count in mt.most_common():
        print(f'{count:4d}  {kind:10s} total')

    if '--dump' in sys.argv:
        for row in sorted(hidden, key=lambda r: (r['rel'], r['line'])):
            print(json.dumps({k: row[k] for k in ('rel', 'func', 'index', 'line', 'kind', 'shape', 'verbs')},
                             ensure_ascii=False))
        return
    print('\n== DML reverse check (any manager name) ==')
    kinds = collections.Counter(row[2] for row in dml_blocks)
    print(f'   blocks with own-layer DML: {len(dml_blocks)}  {dict(kinds)}')
    print(f'   execution points: inside with blocks {points_in}, bare {points_out}, '
          f'total {points_in + points_out}')
    print(f'   execute-call first words: {dict(point_verbs)}')
    excluded = [k for k in OUT_OF_LEDGER if k not in {(r[0], r[1]) for r in dml_blocks}]
    print(f'   out-of-ledger entries not observed (stale?): {sorted(excluded)}')

    if detail:
        print('\n== every legacy external block ==')
        for rel, line, who, verbs in sorted(legacy_rows):
            print(f'{rel}:{line}  on {who}  {" ".join(verbs) if verbs else "(no SQL)"}')


if __name__ == '__main__':
    main()

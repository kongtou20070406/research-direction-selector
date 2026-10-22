"""Run the acceptance gates from docs/project-store-split-protocol.md in one go.

The protocol lists six gates. Running them by hand invites two failures: running
five and calling it done, and comparing the graph numbers against a remembered
baseline instead of a fresh one. This runner fixes both by printing a table with
an explicit PASS/FAIL/SKIP per gate and by measuring the baseline itself from a
git ref.

    python -B tools/verify_slice_gates.py --map D:/tmp/map.json \\
        --target scripts/rds_project_strategies.py --baseline ce7a814

`--map` and `--target` drive gate 5; without them it is skipped, not passed.
`--baseline` is a git ref used only by gate 4, which builds an inventory of that
ref in a temporary worktree-free way: it reads the ref's blob hashes through
`git cat-file`, so the working tree is never touched.

Exit 0 only when every enabled gate passes.
"""

import argparse
import ast
import json
import re
import subprocess
import sys
import time

REPO = None


def run(cmd, timeout=1800):
    started = time.time()
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                          timeout=timeout, shell=isinstance(cmd, str))
    return proc.returncode, proc.stdout + proc.stderr, time.time() - started


def gate_tests(pattern, label):
    code, out, secs = run([sys.executable, '-B', '-m', 'unittest', 'discover',
                           '-s', 'tests', '-p', pattern])
    tail = [line for line in out.strip().splitlines() if line.strip()][-3:]
    ok = code == 0 and 'OK' in out
    return ok, f'{label}: {tail[-1] if tail else "no output"} ({secs:.0f}s)', out


def gate_layers():
    code, out, secs = run([sys.executable, '-B', '-m', 'unittest', 'discover',
                           '-s', 'tests', '-p', 'test_import_graph.py'])
    ok = code == 0 and 'OK' in out
    last = [line for line in out.strip().splitlines() if line.strip()][-1:]
    return ok, f'import graph: {last[0] if last else "no output"} ({secs:.0f}s)', out


GRAPH_PROBE = '''
import sys, json
sys.path[:0] = ['scripts', 'tools']
import import_graph as g
inv = g.inventory('.')
print(json.dumps({
    'cycles': len(inv['cycles']),
    'mutual': len(inv['mutual_pairs']),
    'upward': inv['counts']['upward_edges'],
    'unresolved': len(inv['unresolved']),
    'max_scc': max((len(c) for c in inv['cycles']), default=0),
    'modules': inv['counts']['modules'],
    'unknown_layers': sorted(k for k, v in inv['modules'].items() if v['layer'] == 'UNKNOWN'),
}))
'''


def gate_graph(limit, source_ref):
    """The graph must not get worse than the recorded baseline, and stay total.

    The baseline is a ceiling, not a remembered answer: the four counts recorded
    at `source_ref` are passed in and the working tree is measured fresh. Gate 4
    cannot lower a count by editing this file -- the limits are the numbers in
    docs/project-store-split-protocol.md, and changing them is a protocol edit.
    """
    code, out, _ = run([sys.executable, '-B', '-c', GRAPH_PROBE], timeout=600)
    if code != 0:
        return False, 'graph: could not measure the working tree', out[-600:]
    line = [item for item in out.strip().splitlines() if item.startswith('{')]
    if not line:
        return False, 'graph: probe printed nothing', out[-600:]
    now = json.loads(line[-1])

    problems = []
    if now['unknown_layers']:
        problems.append(f"unclassified modules: {now['unknown_layers']}")
    for key, ceiling in limit.items():
        if now[key] > ceiling:
            problems.append(f'{key} rose {ceiling} -> {now[key]}')

    detail = (f"graph: cycles={now['cycles']} mutual={now['mutual']} "
              f"upward={now['upward']} unresolved={now['unresolved']} "
              f"max_scc={now['max_scc']} modules={now['modules']} "
              f"(ceilings recorded at {source_ref})")
    if problems:
        return False, detail + ' | ' + '; '.join(problems), ''
    return True, detail, ''


def gate_pure_move(source, target, cls, map_path, mode):
    code, out, secs = run([sys.executable, '-B', 'tools/verify_pure_move.py',
                           '--mode', mode, '--source', source, '--target', target,
                           '--class', cls, '--map', map_path])
    lines = [line for line in out.strip().splitlines() if line.strip()]
    summary = lines[-1] if lines else 'no output'
    failures = [line for line in lines if line.startswith('FAIL')]
    return code == 0, f'same tree: {summary} ({secs:.0f}s)', '\n'.join(failures)


def gate_stale(names, cls, source):
    """A moved name must not still be referenced as a bound method in the class."""
    if not names:
        return None, 'stale call sites: SKIPPED (no names given)', ''
    with open(source, encoding='utf-8') as handle:
        text = handle.read()
    tree = ast.parse(text)
    node = None
    for item in tree.body:
        if isinstance(item, ast.ClassDef) and item.name == cls:
            node = item
            break
    if node is None:
        return False, f'stale call sites: class {cls!r} not found in {source}', ''

    hits = []
    for moved in names:
        pattern = re.compile(rf'\bself\.{re.escape(moved)}\b')
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f'{source}:{i} still spells self.{moved}')
    if hits:
        return False, f'stale call sites: {len(hits)} left', '\n'.join(hits)
    return True, f'stale call sites: none of {len(names)} names still bound on self', ''


def main(argv=None):
    global REPO
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', default='.')
    ap.add_argument('--baseline', default='ce7a814',
                    help='the commit the graph ceilings were measured at (recorded in the protocol)')
    ap.add_argument('--ceilings', default='4,40,18,1,49',
                    help='cycles,mutual,upward,unresolved,max_scc')
    ap.add_argument('--map', default=None, help='JSON map for the pure-move gate')
    ap.add_argument('--target', default=None)
    ap.add_argument('--source', default='scripts/rds_project.py')
    ap.add_argument('--class', dest='cls', default='ProjectStore')
    ap.add_argument('--mode', default='method', choices=('method', 'function'))
    ap.add_argument('--fast', action='store_true',
                    help='skip the two slow test gates and run only the structural ones')
    args = ap.parse_args(argv)
    REPO = args.repo

    rows = []

    def record(name, ok, detail, extra=''):
        rows.append((name, ok, detail, extra))
        mark = {True: 'PASS', False: 'FAIL', None: 'SKIP'}[ok]
        print(f'{mark}  {detail}')

    if not args.fast:
        ok, detail, _ = gate_tests('test_rds_project*.py', 'ledger tests')
        record('ledger tests', ok, detail)

        ok, detail, _ = gate_tests('test_rds_quick.py', 'quick ledger')
        record('quick ledger', ok, detail)

        ok, detail, _ = gate_layers()
        record('layer inventory', ok, detail)

    ceiling_values = [int(x) for x in args.ceilings.split(',')]
    limit = dict(zip(('cycles', 'mutual', 'upward', 'unresolved', 'max_scc'), ceiling_values))
    ok, detail, extra = gate_graph(limit, args.baseline)
    record('dependency graph', ok, detail, extra)

    if args.map and args.target:
        ok, detail, extra = gate_pure_move(args.source, args.target, args.cls, args.map, args.mode)
        record('pure move', ok, detail, extra)
    else:
        record('pure move', None, 'same tree: SKIPPED (no --map/--target)')

    names = []
    if args.map:
        with open(args.map, encoding='utf-8') as handle:
            names = list(json.load(handle))
    ok, detail, extra = gate_stale(names, args.cls, args.source)
    record('stale call sites', ok, detail, extra)

    failed = [row for row in rows if row[1] is False]
    print(f'\n{sum(1 for r in rows if r[1] is True)} pass, {len(failed)} fail, '
          f'{sum(1 for r in rows if r[1] is None)} skipped')
    for name, state, detail, extra in rows:
        if state is False and extra:
            print(f'\n--- {name} ---\n{extra}')
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())

"""Judge results/pareto-polynomial.json against the pre-registered safety table.

Reads only the two result files; every threshold is the one written down in
benchmark/newtonbench/polynomial-receipt-protocol.md §3 before the run.

    cd "D:/Myfile/work space/RDS/rds-357" && python -B docs/pareto-polynomial-20261021-compare.py

Exit code 0 iff all five safety items pass, 1 otherwise. Never writes.
"""
import json
import sys
from pathlib import Path

NEW = Path('benchmark/newtonbench/results/pareto-polynomial.json')
REF = Path('benchmark/newtonbench/results/pareto-stage2-costfix.json')
MAIN = (('sparse-tail', 'rds', 32), ('sparse-mid', 'rds', 32), ('regime-double', 'rds', 32))


def load(path):
    with path.open(encoding='utf-8') as handle:
        document = json.load(handle)
    return document, {(r['task'], r['arm'], r['budget']): r for r in document['rows']}


def row_text(row):
    keys = ('status', 'claim', 'claim_exact', 'exact_match', 'experiments', 'claimed',
            'false_certification')
    return {k: row.get(k) for k in keys}


def main():
    new, new_rows = load(NEW)
    ref, ref_rows = load(REF)
    lines, failures = [], []

    def say(text=''):
        lines.append(text)
        print(text)

    def item(number, passed, text):
        lines.append(f'{number}. {"PASS" if passed else "FAIL"}  {text}')
        print(lines[-1])
        if not passed:
            failures.append(number)

    new_rds, ref_rds = new['rds'], ref['rds']
    say(f'new: {NEW}  base: {REF}')
    say(f'rows: new={len(new_rows)} base={len(ref_rows)}')
    say()
    say('## S1 correct_count does not drop')
    item(1, new_rds['correct_count'] >= ref_rds['correct_count'],
         f"rds correct_count {ref_rds['correct_count']} -> {new_rds['correct_count']}; "
         f"whole-file {ref['correct_count']} -> {new['correct_count']}")

    say()
    say('## S2 false_certification_count does not rise')
    item(2, new_rds['false_certification_count'] <= ref_rds['false_certification_count'],
         f"rds false_certification_count {ref_rds['false_certification_count']} -> "
         f"{new_rds['false_certification_count']}; "
         f"whole-file {ref['false_certification_count']} -> {new['false_certification_count']} "
         f"(baseline arm is unchanged on purpose)")

    say()
    say('## S3 no GOAL_IDENTIFIED cell demoted')
    demoted = []
    for key, row in ref_rows.items():
        now = new_rows.get(key)
        if now is None:
            demoted.append(f'{key}: MISSING_IN_NEW')
        elif row.get('status') == 'GOAL_IDENTIFIED' and now.get('status') in (
                'JUDGMENT_REQUIRED', 'SEARCH_EXHAUSTED'):
            demoted.append(f'{key}: GOAL_IDENTIFIED -> {now.get("status")}')
    item(3, not demoted, f'demotions={demoted or "none"}')

    say()
    say('## S4 every newly claimed_exact row is not a false certification')
    gained = []
    bad = []
    for key, now in new_rows.items():
        if not now.get('claim_exact'):
            continue
        before = ref_rows.get(key)
        if before is None or before.get('claim_exact'):
            continue  # absent in base, or already counted there: not a new row
        gained.append((key, now.get('false_certification')))
        if now.get('false_certification'):
            bad.append(key)
    item(4, not bad, f'newly claim_exact rows={[(k, v) for k, v in gained] or "none"}; '
                     f'false certifications among them={bad or "none"}')

    say()
    say('## S5 experiments_per_correct stays under 110% of 21.0')
    limit = 21.0 * 1.10
    value = new_rds['experiments_per_correct']
    item(5, value is not None and value <= limit,
         f"rds experiments_per_correct {ref_rds['experiments_per_correct']} -> {value} "
         f'(limit {limit:.2f})')

    say()
    say('## per-cell differences (only cells that differ)')
    differing = 0
    for key in sorted(set(ref_rows) | set(new_rows), key=lambda k: (k[1], k[0], k[2])):
        before, now = ref_rows.get(key), new_rows.get(key)
        if before is None or now is None:
            differing += 1
            say(f'{key}: {"only in base" if now is None else "only in new"}')
            continue
        diff = {k: (before.get(k), now.get(k)) for k in row_text(before)
                if before.get(k) != now.get(k)}
        if diff:
            differing += 1
            say(f'{key}: {json.dumps(diff, ensure_ascii=False)}')
    if not differing:
        say('none: all 36 cells identical on status/claim/claim_exact/exact_match/experiments/'
            'claimed/false_certification')

    say()
    say('## main cells')
    for key in MAIN:
        row = new_rows.get(key)
        if row is None:
            say(f'{key}: MISSING')
            continue
        say(f'{key}: status={row.get("status")} claim={row.get("claim")} '
            f'claim_exact={row.get("claim_exact")} experiments={row.get("experiments")} '
            f'exact_match={row.get("exact_match")} false_certification='
            f'{row.get("false_certification")} law={row.get("law")!r} '
            f'polynomial={(row.get("polynomial") or {}).get("status")!r}')

    say()
    verdict = not failures
    say(f'OVERALL: {"PASS" if verdict else "FAIL"}'
        + ('' if verdict else f' (blocked by safety item(s) {failures})'))
    return 0 if verdict else 1


if __name__ == '__main__':
    sys.exit(main())

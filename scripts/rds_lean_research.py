"""Read-only Lean project assistance. This CLI never executes supplied Lean code."""
import argparse
import json
from pathlib import Path
import sys

from rds_app import create_app
from rds_lean_obligation import MAX_BYTES
from rds_lean_feedback import MAX_OUTPUT_BYTES


def _read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Lean request file exceeds byte limit')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('inspect')
    search = commands.add_parser('search')
    search.add_argument('query')
    search.add_argument('--limit', type=int, default=10)
    review = commands.add_parser('review')
    review.add_argument('--obligation', required=True, type=Path)
    review.add_argument('--audit', type=Path)
    feedback = commands.add_parser('feedback')
    feedback.add_argument('--source', required=True, type=Path)
    feedback.add_argument('--diagnostics', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        app = create_app(args.root)
        if args.command == 'inspect':
            result = app.inspect_lean_project()
        elif args.command == 'search':
            result = app.search_lean_declarations(args.query, limit=args.limit)
        elif args.command == 'feedback':
            with args.source.open('rb') as stream:
                source = stream.read(2 * 1024 * 1024 + 1)
            if len(source) > 2 * 1024 * 1024:
                raise ValueError('Lean source exceeds 2 MiB')
            with args.diagnostics.open('rb') as stream:
                diagnostics = stream.read(MAX_OUTPUT_BYTES + 1)
            result = app.lean_feedback(source, diagnostics).to_dict()
        else:
            result = app.review_lean_obligation(_read_json(args.obligation),
                                                _read_json(args.audit) if args.audit else None)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        if args.command == 'feedback':
            return 0 if result['parse_status'] == 'COMPLETE' else 2
        return 0 if result.get('status') in {'PASS', 'CONJECTURE'} else 2
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({'status': 'ERROR', 'assurance': 'NONE', 'reason': str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

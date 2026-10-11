"""Optional PreToolUse filter; never an OS or complete Codex enforcement boundary."""
import argparse
import json
import re
import sys

from rds_host_broker import NAMES, MAX_INPUT
from rds_source_documents import strict_json


def decision(event, allowed):
    valid = (isinstance(event, dict) and event.get('hook_event_name') == 'PreToolUse'
             and isinstance(event.get('tool_name'), str))
    if valid and event['tool_name'] in allowed:
        # Defer to the host's normal approvals; do NOT grant permission.
        return {}
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': 'This scoped host allows only the configured RDS broker tools. Use the broker or request operator intervention.'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-tool', action='append', required=True,
                        help='Exact host-observed MCP name, repeated for each broker tool')
    args = parser.parse_args()
    if any(not re.fullmatch(r'mcp__[A-Za-z0-9_-]+__(' + '|'.join(NAMES) + ')', name)
           for name in args.allow_tool):
        # Exit 2 is the supported blocking path, unlike hook crashes.
        print('Invalid operator broker tool allowlist', file=sys.stderr)
        return 2
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        event = strict_json(raw.decode('utf-8')) if len(raw) <= MAX_INPUT else None
    except (ValueError, UnicodeError, RecursionError, OSError):
        event = None
    print(json.dumps(decision(event, set(args.allow_tool))))
    return 0


if __name__ == '__main__':
    sys.exit(main())

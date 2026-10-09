import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BrokerHookTests(unittest.TestCase):
    def call(self, raw, name='mcp__rds_broker__rds_next'):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_broker_hook.py'),
                               '--allow-tool', name], input=raw, capture_output=True,
                              text=True, encoding='utf-8', timeout=5)

    def test_exact_broker_tool_defers_to_existing_host_permissions(self):
        result = self.call(json.dumps({'hook_event_name': 'PreToolUse', 'tool_name': 'mcp__rds_broker__rds_next'}))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {})

    def test_shell_patch_nested_mcp_agent_and_unknown_tools_are_denied(self):
        for name in ('Bash', 'PowerShell', 'exec_command', 'apply_patch', 'Agent',
                     'mcp__other__rds_next', 'functions.exec', 'write_stdin', 'unknown'):
            with self.subTest(name=name):
                result = self.call(json.dumps({'hook_event_name': 'PreToolUse', 'tool_name': name,
                                              'tool_input': {'command': 'echo allowed broker'}}))
                output = json.loads(result.stdout)['hookSpecificOutput']
                self.assertEqual(output['permissionDecision'], 'deny')
                self.assertEqual(output['hookEventName'], 'PreToolUse')
                self.assertEqual(result.returncode, 0)

    def test_malformed_oversized_duplicate_and_wrong_event_deny(self):
        for raw in ('{', '[]', '{}', ' ' * 65537,
                    '{"hook_event_name":"PreToolUse","tool_name":"x","tool_name":"mcp__rds_broker__rds_next"}',
                    '{"hook_event_name":"PermissionRequest","tool_name":"mcp__rds_broker__rds_next"}'):
            self.assertEqual(json.loads(self.call(raw).stdout)['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_invalid_operator_allowlist_uses_blocking_exit_two(self):
        result = self.call('{}', 'Bash')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()

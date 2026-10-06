"""Regression for the actual warning/tool classification failure; no model call."""
import importlib.util
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
directory = REPO / 'examples/explanation-loop'
sys.path.insert(0, str(REPO / 'scripts'))
spec = importlib.util.spec_from_file_location('explanation_worker', directory / 'worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
spec = importlib.util.spec_from_file_location('explanation_candidate', directory / 'candidate.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


class HarnessTests(unittest.TestCase):
    def test_configuration_warning_does_not_mean_tool_execution(self):
        trace = [{'type': 'item.completed', 'item': {'type': 'error', 'message': 'Malformed local role warning'}},
                 {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{}'}},
                 {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 2}}]
        tools, usage, completed = worker.review_trace(trace)
        self.assertEqual(tools, [])
        self.assertTrue(completed)
        self.assertEqual(usage['input_tokens'], 10)
        trace.insert(1, {'type': 'item.started', 'item': {'type': 'command_execution'}})
        self.assertEqual(worker.review_trace(trace)[0], ['command_execution'])
        self.assertFalse(worker.review_trace(trace[:-1])[2])

    def test_generated_ast_runs_as_bounded_data(self):
        law = {'op': 'mod', 'args': [{'x': True}, 2]}
        rival = {'op': 'mod', 'args': [{'op': 'mod', 'args': [{'x': True}, 5]}, 2]}
        self.assertEqual(candidate.calculate(law, 5), 1)
        self.assertEqual(candidate.calculate(rival, 5), 0)
        for expr in ('__import__("os")', {'x': 1}, {'op': 'exec', 'args': [0, 1]}, True):
            with self.assertRaises(ValueError):
                candidate.calculate(expr, 5)


if __name__ == '__main__':
    unittest.main()

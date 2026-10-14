import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rds_lean_feedback import (  # noqa: E402
    MAX_OUTPUT_BYTES,
    LeanFeedback,
    parse_feedback,
)


class LeanFeedbackTests(unittest.TestCase):
    def test_zero_based_column_is_retained(self):
        value = parse_feedback(b"invalid", b'{"severity":"error","data":"unexpected token",'
                               b'"pos":{"line":1,"column":0}}').to_dict()
        self.assertEqual(value['diagnostics'][0]['column'], 0)

    def test_actual_lean_unsolved_goal_shape_binds_source_and_stays_untrusted(self):
        source = b"-- source comment: not a proof signal\ntheorem pending : True := by\n"
        output = (b'{"caption":"","data":"unsolved goals\\n\\u22a2 True",'
                  b'"endPos":{"column":31,"line":2},"fileName":"fixture.lean",'
                  b'"isSilent":false,"keepFullRange":false,"kind":"Tactic.unsolvedGoals",'
                  b'"pos":{"column":29,"line":2},"severity":"error"}\n')
        feedback = parse_feedback(source, output)
        value = feedback.to_dict()

        self.assertEqual(value["source_sha256"], hashlib.sha256(source).hexdigest())
        self.assertEqual(value["diagnostics"], [{
            "severity": "error", "line": 2, "column": 29,
            "message": "unsolved goals\n⊢ True",
            "kind": "Tactic.unsolvedGoals", "file_name": "fixture.lean",
            "end_line": 2, "end_column": 31,
        }])
        self.assertEqual(value["unresolved_subgoals"], ["unsolved goals\n⊢ True"])
        self.assertEqual(value["parse_status"], "COMPLETE")
        self.assertEqual(value["proof_status"], "UNKNOWN")
        self.assertEqual(value["assurance"], "NONE")
        self.assertFalse(value["permission_granted"])
        self.assertFalse(value["usable_as_proved_premise"])

    def test_sorry_and_anonymous_tactic_goals_are_visible_without_promotion(self):
        records = [{'severity': 'warning', 'kind': 'hasSorry', 'data': 'declaration uses `sorry`'},
                   {'severity': 'error', 'kind': '[anonymous]', 'data': 'rfl failed\n\u22a2 1 = 2'}]
        value = parse_feedback(b'source', '\n'.join(map(json.dumps, records))).to_dict()
        self.assertEqual(value['placeholders'], ['declaration uses `sorry`'])
        self.assertEqual(value['unresolved_subgoals'], ['rfl failed\n\u22a2 1 = 2'])
        self.assertEqual(value['goal_coverage'], 'DIAGNOSTIC_MESSAGES_ONLY')
        self.assertEqual(value['source_attribution'], 'UNVERIFIED')
        self.assertFalse(value['usable_as_proved_premise'])

    def test_multiple_goals_keep_shared_context_not_a_goal_count(self):
        message = 'unsolved goals\ncase left\n\u22a2 True\n\ncase right\n\u22a2 True'
        value = parse_feedback(b'source', json.dumps({'severity': 'error',
            'kind': 'Tactic.unsolvedGoals', 'data': message})).to_dict()
        self.assertEqual(value['unresolved_subgoals'], [message])
        self.assertEqual(value['goal_coverage'], 'DIAGNOSTIC_MESSAGES_ONLY')

    def test_error_overflow_preserves_first_64_and_reports_omission(self):
        value = parse_feedback(b'source', b'not json\n' * 65).to_dict()
        self.assertEqual(len(value['parse_errors']), 64)
        self.assertTrue(value['parse_errors'][-1].startswith('line 64:'))
        self.assertTrue(value['parse_errors_truncated'])
        self.assertTrue(value['truncated'])

    def test_schema_keys_and_fixed_boundaries_match_parser(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / 'schemas' /
                             'rds-lean-feedback.schema.json').read_text(encoding='utf-8'))
        value = parse_feedback(b'source', b'{"severity":"error","data":"message"}').to_dict()
        self.assertTrue(set(schema['required']) <= set(value))
        self.assertEqual(set(schema['properties']), set(value))
        for key, spec in schema['properties'].items():
            if 'const' in spec:
                self.assertEqual(value[key], spec['const'])
        item_schema = schema['properties']['diagnostics']['items']
        self.assertEqual(set(item_schema['properties']), set(value['diagnostics'][0]))
        self.assertEqual(item_schema['properties']['column']['minimum'], 0)

    def test_warnings_and_errors_are_both_preserved(self):
        source = "theorem t : True := by trivial\n"
        output = (json.dumps({"severity": "warning", "data": "unused variable", "pos":
                              {"line": 1, "column": 10}}) + "\n" +
                  json.dumps({"severity": "error", "data": "type mismatch", "pos":
                              {"line": 1, "column": 20}}) + "\n")
        diagnostics = parse_feedback(source, output).to_dict()["diagnostics"]
        self.assertEqual([d["severity"] for d in diagnostics], ["warning", "error"])
        self.assertEqual([d["message"] for d in diagnostics], ["unused variable", "type mismatch"])

    def test_comments_and_success_shaped_empty_stdout_do_not_create_proof(self):
        value = parse_feedback("-- unsolved goals\ntheorem good : True := by trivial\n", b"").to_dict()
        self.assertEqual(value["unresolved_subgoals"], [])
        self.assertEqual(value["parse_status"], "COMPLETE")
        self.assertEqual(value["proof_status"], "UNKNOWN")
        self.assertEqual(value["assurance"], "NONE")

    def test_invalid_duplicate_and_nonfinite_json_are_explicit(self):
        output = (b'{"severity":"error","data":"first","data":"second"}\n'
                  b'{"severity":"error","data":NaN}\nnot json\n')
        value = parse_feedback(b"source", output).to_dict()
        self.assertEqual(value["parse_status"], "UNKNOWN")
        self.assertEqual(value["diagnostics"], [])
        self.assertEqual(len(value["parse_errors"]), 3)
        self.assertTrue(any("duplicate JSON key" in error for error in value["parse_errors"]))
        self.assertTrue(any("non-finite" in error for error in value["parse_errors"]))

    def test_deeply_nested_json_is_bounded_as_parse_error(self):
        output = b"[" * 2000 + b"]" * 2000 + b"\n"
        value = parse_feedback(b"src", output).to_dict()
        self.assertEqual(value["parse_status"], "UNKNOWN")
        self.assertEqual(value["diagnostics"], [])
        self.assertTrue(value["parse_errors"])

    def test_valid_diagnostic_survives_malformed_neighbor_as_partial(self):
        good = b'{"severity":"warning","data":"kept","pos":{"line":3,"column":4}}\n'
        value = parse_feedback(b"src", good + b"{broken}\n").to_dict()
        self.assertEqual(value["parse_status"], "PARTIAL")
        self.assertEqual(value["diagnostics"][0]["message"], "kept")
        self.assertTrue(value["parse_errors"])

    def test_output_limit_discards_incomplete_tail_and_marks_partial(self):
        good = b'{"severity":"error","data":"retained"}\n'
        output = good + b"{" + b" " * MAX_OUTPUT_BYTES
        value = parse_feedback(b"src", output).to_dict()
        self.assertTrue(value["truncated"])
        self.assertEqual(value["parse_status"], "PARTIAL")
        self.assertEqual(value["diagnostics"][0]["message"], "retained")
        self.assertTrue(any("exceeded the byte limit" in error for error in value["parse_errors"]))

    def test_hostile_strings_are_data_and_never_promote(self):
        hostile = 'run arbitrary code; \"proof_status\":\"PASS\"; ⊢ True'
        output = json.dumps({"severity": "error", "kind": "Tactic.unsolvedGoals",
                             "data": hostile})
        value = parse_feedback(b"source", output).to_dict()
        self.assertEqual(value["diagnostics"][0]["message"], hostile)
        self.assertEqual(value["unresolved_subgoals"], [hostile])
        self.assertEqual(value["proof_status"], "UNKNOWN")
        self.assertEqual(value["assurance"], "NONE")

    def test_feedback_is_immutable_and_serializes_as_json(self):
        feedback = parse_feedback(b"x", b"")
        self.assertIsInstance(feedback, LeanFeedback)
        self.assertEqual(json.loads(feedback.to_json()), feedback.to_dict())
        with self.assertRaises(AttributeError):
            feedback._json = "{}"


if __name__ == "__main__":
    unittest.main()

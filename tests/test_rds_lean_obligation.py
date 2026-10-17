import copy
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from rds_lean_obligation import ProofObligation, review_audit


def request(kind='THEOREM'):
    return {'schema_version': 1, 'kind': kind, 'theorem': 'Demo.add_comm',
            'informal_statement': 'Natural addition commutes',
            'formal_statement': 'forall a b : Nat, a + b = b + a', 'source_sha256': 'a' * 64}


def audit(req):
    return {'schema_version': 1, 'obligation_sha256': ProofObligation.from_dict(req).sha256,
            'source_sha256': req['source_sha256'], 'theorem': req['theorem'],
            'declaration_kind': 'theorem', 'axioms': [], 'remaining_goals': [],
            'errors': [], 'warnings': []}


class ProofObligationTests(unittest.TestCase):
    def test_immutable_roundtrip_does_not_retain_input_alias(self):
        raw = request()
        value = ProofObligation.from_dict(raw)
        raw['kind'] = 'CONJECTURE'
        self.assertEqual(value.to_dict()['kind'], 'THEOREM')
        with self.assertRaises(FrozenInstanceError):
            value._json = '{}'

    def test_unchecked_declarations_never_become_proved_premises(self):
        for kind in ('CONJECTURE', 'LEMMA', 'THEOREM'):
            with self.subTest(kind=kind):
                out = review_audit(request(kind))
                self.assertEqual(out['status'], 'CONJECTURE' if kind == 'CONJECTURE' else 'PROOF_REQUIRED')
                self.assertEqual(out['assurance'], 'NONE')
                self.assertFalse(out['usable_as_proved_premise'])

    def test_sorry_and_unfinished_goals_are_only_retained_as_conjectures(self):
        for kind in ('CONJECTURE', 'LEMMA', 'THEOREM'):
            req = request(kind)
            raw = {**audit(req), 'axioms': ['sorryAx'], 'remaining_goals': ['a b : Nat; goal']}
            out = review_audit(req, raw)
            self.assertEqual(out['status'], 'CONJECTURE' if kind == 'CONJECTURE' else 'REJECTED')
            self.assertFalse(out['usable_as_proved_premise'])

    def test_transitive_custom_native_and_sorry_axioms_are_rejected(self):
        req = request()
        for dependency in ('sorryAx', 'Demo.fake', 'Demo._native.native_decide.ax_1'):
            with self.subTest(dependency=dependency):
                self.assertEqual(review_audit(req, {**audit(req), 'axioms': [dependency]})['status'], 'REJECTED')

    def test_standard_axioms_do_not_turn_json_into_a_certificate(self):
        req = request()
        raw = {**audit(req), 'axioms': ['propext', 'Quot.sound', 'Classical.choice']}
        out = review_audit(req, raw)
        self.assertEqual(out['status'], 'REPLAY_REQUIRED')
        self.assertEqual(out['assurance'], 'NONE')
        self.assertEqual(out['formal_alignment'], 'NOT_CHECKED')
        self.assertFalse(out['usable_as_proved_premise'])

    def test_axiom_or_definition_cannot_impersonate_theorem(self):
        req = request()
        for kind in ('axiom', 'definition', 'unknown'):
            self.assertEqual(review_audit(req, {**audit(req), 'declaration_kind': kind})['status'], 'REJECTED')

    def test_wrong_statement_source_and_name_are_stale(self):
        req = request()
        raw = audit(req)
        for field, value in (('source_sha256', 'b' * 64), ('obligation_sha256', 'b' * 64), ('theorem', 'Other')):
            self.assertEqual(review_audit(req, {**raw, field: value})['status'], 'REJECTED')
        self.assertEqual(review_audit({**req, 'formal_statement': 'True'}, raw)['status'], 'REJECTED')

    def test_clean_conjecture_audit_never_implicitly_promotes_it(self):
        req = request('CONJECTURE')
        self.assertEqual(review_audit(req, audit(req))['status'], 'CONJECTURE')

    def test_errors_warnings_and_goals_block_theorems(self):
        req = request()
        for field in ('errors', 'warnings', 'remaining_goals'):
            self.assertEqual(review_audit(req, {**audit(req), field: ['retained']})['status'], 'REJECTED')

    def test_malformed_or_self_signed_payloads_are_rejected(self):
        req = request()
        for value in ({**req, 'schema_version': True}, {**req, 'kind': 'PROVEN'},
                      {**req, 'source_sha256': 'not-a-hash'}, {**req, 'formal_statement': ''},
                      {**req, 'assurance': 'LEAN_KERNEL_CHECKED'}):
            with self.assertRaises(ValueError):
                ProofObligation.from_dict(value)
        raw = audit(req)
        for value in ({**raw, 'schema_version': True}, {**raw, 'axioms': 'sorryAx'},
                      {**raw, 'axioms': ['x'] * 257}, {**raw, 'verdict': 'PASS'}):
            with self.assertRaises(ValueError):
                review_audit(req, value)

    def test_raw_constructor_cannot_bypass_revalidation(self):
        with self.assertRaises(ValueError):
            review_audit(ProofObligation('{}'))

    def test_output_mutation_does_not_change_obligation(self):
        req = request()
        value = ProofObligation.from_dict(req)
        before = value.sha256
        output = value.to_dict()
        output['formal_statement'] = 'False'
        self.assertEqual(value.sha256, before)


if __name__ == '__main__':
    unittest.main()

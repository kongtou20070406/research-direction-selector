"""Synthetic feedback and CAS fixtures; no production writer is provided."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rds_research_policy as policy


SCOPE = {'project_id': 'synthetic-project', 'decision_id': 'synthetic-decision'}
EMPTY = {'generation': 0, 'version_sha256': None}


def rule(kind='VETO', **changes):
    return {'id': 'feedback-1', 'kind': kind, 'enabled': True, 'scope': dict(SCOPE),
            'target': {'hypothesis_id': 'h1'}, 'reason': 'Synthetic scoped feedback',
            'source': 'synthetic:user-or-receipt', **changes}


def candidates():
    return [{'id': 'first', 'status': 'READY', 'hypothesis_id': 'h1',
             'cost': None, 'scientific_support': 'UNKNOWN', 'permissions': ['original']},
            {'id': 'second', 'status': 'READY', 'hypothesis_id': 'h2', 'cost': 3}]


class SyntheticLedger:
    """Test-only transaction consumer, never an authority provider or API."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript('''
            CREATE TABLE pointer (id INTEGER PRIMARY KEY, generation INTEGER, sha TEXT);
            INSERT INTO pointer VALUES (1, 0, NULL);
            CREATE TABLE versions (sha TEXT PRIMARY KEY, body BLOB);
            CREATE TABLE events (generation INTEGER, operation TEXT);
            CREATE TABLE jobs (id TEXT PRIMARY KEY, generation INTEGER, sha TEXT);
            CREATE TABLE budget (spent INTEGER, reserved INTEGER, allowance INTEGER);
            INSERT INTO budget VALUES (7, 3, 20);
        ''')

    def current(self):
        generation, sha = self.db.execute('SELECT generation,sha FROM pointer').fetchone()
        return {'generation': generation, 'version_sha256': sha}

    def blobs(self):
        return dict(self.db.execute('SELECT sha,body FROM versions'))

    def commit_proposal(self, proposal, interrupt=False):
        # Only this synthetic fixture consumes a proposal. Production requires
        # existing writer admission and reservation boundaries, not this helper.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            before, after = proposal['expected'], proposal['replacement']
            raw = proposal['policy_bytes']
            policy.load_policy(raw, after['version_sha256'])
            self.db.execute('INSERT OR IGNORE INTO versions VALUES (?,?)',
                            (after['version_sha256'], raw))
            count = self.db.execute('UPDATE pointer SET generation=?,sha=? '
                                    'WHERE id=1 AND generation=? AND sha IS ?',
                                    (after['generation'], after['version_sha256'],
                                     before['generation'], before['version_sha256'])).rowcount
            if count != 1:
                raise ValueError('Synthetic CAS conflict')
            if interrupt:
                raise RuntimeError('Synthetic interruption before commit')
            self.db.execute('INSERT INTO events VALUES (?,?)',
                            (after['generation'], proposal['operation']))


class FeedbackTests(unittest.TestCase):
    def test_veto_changes_next_recommendation_without_mutation(self):
        rows = candidates()
        before = deepcopy(rows)
        base = policy.draft_policy([])
        self.assertEqual(policy.recommend(base, rows, SCOPE)['selected_id'], 'first')
        raw = policy.draft_policy([rule()], base)
        result = policy.recommend(raw, rows, SCOPE)
        self.assertEqual(result['selected_id'], 'second')
        self.assertEqual(result['reviews'][0]['disposition'], 'VETOED')
        self.assertEqual(result['reviews'][0]['feedback'][0]['source'], rule()['source'])
        self.assertEqual(rows, before)
        self.assertEqual(result['candidates'], before)
        result['candidates'][0]['permissions'].append('not-shared')
        self.assertEqual(rows, before)
        self.assertFalse(result['execution_authorized'])
        self.assertEqual(result['scientific_support'], 'UNKNOWN')

    def test_failure_and_rejection_defer_but_do_not_refute_or_permanently_block(self):
        for kind in ('FAILURE', 'HYPOTHESIS_REJECTED'):
            with self.subTest(kind=kind):
                raw = policy.draft_policy([rule(kind)])
                result = policy.recommend(raw, candidates(), SCOPE)
                self.assertEqual(result['ordered_ids'], ['second', 'first'])
                self.assertEqual(result['reviews'][0]['disposition'], 'DEFERRED')
                self.assertEqual(policy.recommend(raw, candidates()[:1], SCOPE)['selected_id'], 'first')
                for changed in ({**SCOPE, 'project_id': 'other'}, {**SCOPE, 'decision_id': 'other'}):
                    self.assertEqual(policy.recommend(raw, candidates(), changed)['selected_id'], 'first')
                self.assertEqual(result['candidates'][0]['scientific_support'], 'UNKNOWN')

    def test_edit_disable_and_delete_restore_recommendation(self):
        raw = policy.draft_policy([rule()])
        for rules in ([rule(enabled=False)], [], [rule(target={'hypothesis_id': 'h2'})]):
            changed = policy.draft_policy(rules, raw)
            self.assertEqual(policy.recommend(changed, candidates(), SCOPE)['selected_id'], 'first')
            self.assertEqual(policy.load_policy(changed)['policy']['parent_sha256'],
                             policy.load_policy(raw)['sha256'])

    def test_method_scope_reuses_composed_step_review(self):
        raw = policy.draft_policy([rule(target={'methods': {'device': 'gpu'}})])
        rows = candidates()
        rows[0]['methods'] = [{'device': 'cpu'}, {'device': 'gpu'}]
        rows[1]['methods'] = {'device': 'cpu'}
        self.assertEqual(policy.recommend(raw, rows, SCOPE)['selected_id'], 'second')
        del rows[0]['methods']
        result = policy.recommend(raw, rows, SCOPE)
        self.assertEqual(result['reviews'][0]['disposition'], 'UNKNOWN')
        self.assertEqual(result['selected_id'], 'second')

    def test_unknown_scope_or_target_abstains_and_veto_wins(self):
        raw = policy.draft_policy([rule()])
        result = policy.recommend(raw, candidates(), {})
        self.assertEqual(result['selected_id'], 'second')  # h2 is definitely outside target.
        self.assertEqual(result['reviews'][0]['disposition'], 'UNKNOWN')
        row = candidates()[0]
        del row['hypothesis_id']
        self.assertEqual(policy.recommend(raw, [row], SCOPE)['status'], 'ABSTAIN')
        raw = policy.draft_policy([rule(), rule(id='unknown', target={'methods': {'device': 'gpu'}})])
        self.assertEqual(policy.recommend(raw, candidates(), SCOPE)['reviews'][0]['disposition'], 'VETOED')

    def test_existing_nonready_candidates_never_become_ready(self):
        rows = candidates()
        rows[0]['status'] = 'BLOCKED_BUDGET'
        rows[1]['status'] = 'UNKNOWN'
        result = policy.recommend(policy.draft_policy([]), rows, SCOPE)
        self.assertEqual(result['status'], 'ABSTAIN')
        self.assertEqual(result['candidates'], rows)

    def test_validation_rejects_global_or_forged_authority(self):
        invalid = [rule(scope={}), rule(scope={'project_id': 'p'}), rule(enabled=1),
                   rule(writer=True), rule(kind='REFUTED'), rule(target={}),
                   rule('HYPOTHESIS_REJECTED', target={'methods': {'device': 'cpu'}})]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy.draft_policy([value])
        for rules in ([rule(), rule()], [rule(id=str(i)) for i in range(33)]):
            with self.assertRaises(ValueError):
                policy.draft_policy(rules)
        value = policy.load_policy(policy.draft_policy([]))['policy']
        value['trusted_writer'] = True
        with self.assertRaises(ValueError):
            policy.validate_policy(value)

    def test_strict_bytes_and_bounded_candidates(self):
        raw = policy.draft_policy([])
        invalid = [b'{"schema":0,"schema":1}', b'[]', b'\xff', b' ' * (policy.MAX_BYTES + 1)]
        for value in invalid:
            with self.subTest(value=value[:25]), self.assertRaises(ValueError):
                policy.load_policy(value)
        with self.assertRaises(ValueError):
            policy.load_policy(raw, '0' * 64)
        with self.assertRaises(ValueError):
            policy.recommend(raw, candidates() * 65, SCOPE)
        with self.assertRaises(ValueError):
            policy.recommend(raw, [candidates()[0], candidates()[0]], SCOPE)
        with self.assertRaises(ValueError):
            policy.recommend(raw, [{**candidates()[0], 'cost': float('nan')}], SCOPE)


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='rds-policy-synthetic-')
        self.addCleanup(self.directory.cleanup)
        self.ledger = SyntheticLedger(Path(self.directory.name) / 'synthetic.sqlite3')
        self.addCleanup(self.ledger.db.close)
        # Noncanonical bytes prove rollback does not just rewrite a parent label.
        self.base = json.dumps(policy.load_policy(policy.draft_policy([]))['policy'], indent=4).encode() + b'\n'
        self.ledger.commit_proposal(policy.propose_adoption(EMPTY, EMPTY, self.base))

    def test_cas_race_preserves_single_winner_and_budget(self):
        current = self.ledger.current()
        before_budget = self.ledger.db.execute('SELECT * FROM budget').fetchall()
        first = policy.propose_adoption(current, current, policy.draft_policy([rule()], self.base))
        second = policy.propose_adoption(current, current, policy.draft_policy([rule('FAILURE')], self.base))
        self.ledger.commit_proposal(first)
        with self.assertRaisesRegex(ValueError, 'CAS conflict'):
            self.ledger.commit_proposal(second)
        self.assertEqual(self.ledger.current(), first['replacement'])
        self.assertNotIn(second['replacement']['version_sha256'], self.ledger.blobs())
        self.assertEqual(self.ledger.db.execute('SELECT * FROM budget').fetchall(), before_budget)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM events').fetchone()[0], 2)

    def test_running_job_retains_pin_across_adoption_and_rollback(self):
        pinned = policy.pin_version(self.ledger.current())
        with self.ledger.db:
            self.ledger.db.execute('INSERT INTO jobs VALUES (?,?,?)',
                                   ('running-synthetic', pinned['generation'], pinned['version_sha256']))
        current = self.ledger.current()
        raw = policy.draft_policy([rule()], self.base)
        self.ledger.commit_proposal(policy.propose_adoption(current, current, raw))
        child_pin = policy.pin_version(self.ledger.current())
        self.assertEqual(policy.recommend(raw, candidates(), SCOPE)['selected_id'], 'second')
        self.assertEqual(policy.load_pinned(pinned, self.ledger.blobs()), self.base)
        current = self.ledger.current()
        proposal = policy.propose_rollback(current, current, self.ledger.blobs())
        self.assertEqual(proposal['policy_bytes'], self.base)
        self.ledger.commit_proposal(proposal)
        active = policy.load_pinned(self.ledger.current(), self.ledger.blobs())
        self.assertEqual(active, self.base)
        self.assertEqual(policy.load_pinned(child_pin, self.ledger.blobs()), raw)
        self.assertEqual(policy.recommend(active, candidates(), SCOPE)['selected_id'], 'first')
        self.assertEqual(self.ledger.db.execute('SELECT generation,sha FROM jobs').fetchone(),
                         (pinned['generation'], pinned['version_sha256']))
        self.assertEqual(policy.load_pinned(pinned, self.ledger.blobs()), self.base)

    def test_rollback_prevents_aba_stale_adoption(self):
        original = self.ledger.current()
        raw = policy.draft_policy([rule()], self.base)
        stale = policy.propose_adoption(original, original, raw)
        self.ledger.commit_proposal(stale)
        current = self.ledger.current()
        self.ledger.commit_proposal(policy.propose_rollback(current, current, self.ledger.blobs()))
        self.assertEqual(self.ledger.current()['version_sha256'], original['version_sha256'])
        with self.assertRaisesRegex(ValueError, 'compare-and-swap'):
            policy.propose_adoption(self.ledger.current(), original, raw)
        with self.assertRaisesRegex(ValueError, 'CAS conflict'):
            self.ledger.commit_proposal(stale)

    def test_interruption_rolls_back_pointer_blob_and_event(self):
        before, blobs = self.ledger.current(), self.ledger.blobs()
        proposal = policy.propose_adoption(before, before, policy.draft_policy([rule()], self.base))
        with self.assertRaisesRegex(RuntimeError, 'interruption'):
            self.ledger.commit_proposal(proposal, interrupt=True)
        self.assertEqual(self.ledger.current(), before)
        self.assertEqual(self.ledger.blobs(), blobs)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM events').fetchone()[0], 1)

    def test_missing_corrupt_or_stale_versions_fail_closed(self):
        original = self.ledger.current()
        with self.assertRaisesRegex(ValueError, 'no parent'):
            policy.propose_rollback(original, original, self.ledger.blobs())
        raw = policy.draft_policy([rule()], self.base)
        self.ledger.commit_proposal(policy.propose_adoption(original, original, raw))
        current = self.ledger.current()
        for blobs in ({}, {current['version_sha256']: raw},
                      {**self.ledger.blobs(), original['version_sha256']: b'{}'}):
            with self.subTest(blobs=list(blobs)), self.assertRaises(ValueError):
                policy.propose_rollback(current, current, blobs)
        with self.assertRaisesRegex(ValueError, 'parent is stale'):
            policy.propose_adoption(current, current, raw)
        with self.assertRaisesRegex(ValueError, 'missing'):
            policy.load_pinned(original, {current['version_sha256']: raw})

    def test_proposals_never_claim_authorization_or_commit(self):
        current = self.ledger.current()
        proposal = policy.propose_adoption(current, current, policy.draft_policy([rule()], self.base))
        self.assertFalse(proposal['committed'])
        self.assertTrue(proposal['requires_trusted_writer'])
        self.assertEqual(proposal['authorization'], 'UNCHANGED')
        self.assertEqual(proposal['status'], 'PROPOSED')
        self.assertEqual(self.ledger.current(), current)
        forged = {**current, 'writer': True}
        with self.assertRaises(ValueError):
            policy.propose_adoption(forged, forged, self.base)

    def test_generation_exhaustion_is_explicit_and_pointer_schema_agrees(self):
        current = {**self.ledger.current(), 'generation': 2**63 - 2}
        raw = policy.draft_policy([rule()], self.base)
        proposal = policy.propose_adoption(current, current, raw)
        last = proposal['replacement']
        self.assertEqual(policy.pin_version(last)['generation'], 2**63 - 1)
        with self.assertRaisesRegex(ValueError, 'generation exhausted'):
            policy.propose_adoption(last, last, policy.draft_policy([], raw))
        for invalid in (-1, True, 1.0, 2**63):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                policy.pin_version({**last, 'generation': invalid})
        schema = json.loads((Path(__file__).resolve().parents[1] /
                             'schemas/rds-research-policy-pointer.schema.json').read_text())
        self.assertEqual(schema['properties']['generation']['maximum'], last['generation'])


if __name__ == '__main__':
    unittest.main()

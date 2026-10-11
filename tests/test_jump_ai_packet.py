"""Actual original jump outputs projected into AI input and linked responses."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import ProjectStore, digest, canonical, file_sha
import rds_jump as jump
import rds_structure as structure

spec = importlib.util.spec_from_file_location('jump_packet_example', REPO / 'examples/jump-generation/run.py')
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def build_multi_proposal_example(root):
    """Freeze original numeric oracle with explicit multi-proposal attribution.

    The example verifier deliberately accepts one proposal. These concurrent
    fixtures select an exact declared proposal/run, without changing its finite
    table comparison, discriminator or receipt/output authentication.
    """
    initialize = ProjectStore.initialize
    def frozen(store, contract):
        contract = deepcopy(contract)
        evaluator = store.root / 'evaluator.py'
        raw = evaluator.read_text(encoding='utf-8')
        raw = raw.replace("    if len(events) != 1:\n", "    if len(sys.argv) > 3:\n"
                          "        events = [e for e in events if e['id'] == sys.argv[3]]\n"
                          "    if len(events) != 1:\n")
        raw = raw.replace("    receipt = json.loads(db.execute(\"SELECT body FROM receipts WHERE run_id='jump-candidate'\").fetchone()[0])",
                          "    candidate_id = row['proposal']['experiment']['runs'][0]['id']\n"
                          "    receipt = json.loads(db.execute('SELECT body FROM receipts WHERE run_id=?', (candidate_id,)).fetchone()[0])")
        raw = raw.replace("'candidate_run_id': 'jump-candidate'", "'candidate_run_id': candidate_id")
        evaluator.write_text(raw, encoding='utf-8')
        next(b for b in contract['bindings'] if b['path'] == 'evaluator.py')['sha256'] = file_sha(evaluator)
        verifier = next(a for a in contract['allowed_commands'] if 'evaluator.py' in a)
        for ident in ('other-scoped-proposal', 'concurrent-refutation'):
            contract['allowed_commands'].append([*verifier, ident])
        (store.root / 'contract.json').write_text(canonical(contract), encoding='utf-8')
        return initialize(store, contract)
    with patch.object(ProjectStore, 'initialize', frozen):
        return example.build(root)


class JumpAiPacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project'

    def build(self, **kwargs):
        root, store = example.build(self.root, **kwargs)
        generated = jump.generate(root, 3)
        return root, store, generated

    def use(self, context):
        return {'schema': 1, 'packet_sha256': context['sha256'],
                'decisions': [{'id': item['id'], 'disposition': 'adapt',
                               'reason': 'The generated interaction accounts for the original measured residual.',
                               'next_step': 'Check the separating prediction through the unchanged independent verifier.'}
                              for item in context['items']]}

    def test_actual_explanations_predictions_sources_and_current_feedback(self):
        root, store, generated = self.build()
        context = generated['agent_context']
        self.assertEqual(context, jump.packet(store))
        self.assertEqual(context['status'], 'CURRENT')
        self.assertEqual(context['sha256'], digest({k: v for k, v in context.items() if k != 'sha256'}))
        item = context['items'][0]
        self.assertIn('mul', item['explanation'])
        self.assertTrue(item['assumptions'])
        self.assertTrue(item['rationale'])
        self.assertTrue(item['topology']['hyperedges'])
        self.assertNotEqual(item['discriminator']['proposal'], item['discriminator']['rival'])
        self.assertEqual(item['observation'], 'UNKNOWN')
        self.assertEqual(len(context['sources']), 3)
        self.assertEqual(context['generation_results']['synthesize']['search']['candidate']['op'], 'mul')
        self.assertEqual(context['generation_results']['probe']['new_observations'][0]['value'], 6)
        accepted = jump.validate_use(context, self.use(context))
        self.assertEqual(accepted['packet_sha256'], context['sha256'])
        structure.advance(root, item['id'])
        measured = jump.packet(store)
        self.assertEqual(measured['items'][0]['observation'], 'SUPPORT')
        self.assertEqual(measured['items'][0]['scientific_support'], 'UNKNOWN')
        self.assertNotEqual(measured['sha256'], context['sha256'])
        (root / 'out/verdict.json').write_text('{}', encoding='utf-8')
        unavailable = jump.packet(store)
        self.assertEqual(unavailable['status'], 'UNAVAILABLE')
        self.assertFalse(unavailable['items'])

    def test_corruption_original_output_or_retained_proposal_is_unavailable(self):
        root, store, generated = self.build()
        proposal = structure._find(store, 'PROPOSAL', generated['proposal_ids'][0])
        original_find = structure._find

        def changed(st, kind, ident):
            value = original_find(st, kind, ident)
            if kind == 'PROPOSAL' and ident == proposal['id']:
                value = deepcopy(value)
                value['proposal']['assumptions'].append('Unbound replacement assumption')
                value['proposal_sha256'] = digest(value['proposal'])
            return value

        with patch.object(structure, '_find', side_effect=changed):
            context = jump.packet(store)
        self.assertEqual(context['status'], 'UNAVAILABLE')
        self.assertIn('differs from original synthesis', context['diagnostic'])
        (root / 'out/probe.json').write_text('{}', encoding='utf-8')
        context = jump.packet(store)
        self.assertEqual(context['status'], 'UNAVAILABLE')
        with self.assertRaises(ValueError):
            jump.validate_use(context, self.use(context))

    def test_refuted_prediction_is_delivered_without_scientific_acceptance(self):
        original_write = example.write

        def rival_oracle(path, value):
            if path.name == 'oracle.json':
                value = [{'inputs': row['inputs'], 'value': row['inputs']['x'] + row['inputs']['y']}
                         for row in value]
            original_write(path, value)

        with patch.object(example, 'write', side_effect=rival_oracle):
            root, store, generated = self.build()
        structure.advance(root, generated['proposal_ids'][0])
        context = jump.packet(store)
        self.assertEqual(context['status'], 'CURRENT')
        self.assertEqual(context['items'][0]['observation'], 'REFUTE')
        self.assertEqual(context['items'][0]['feedback']['goal_status'], 'FAIL')
        self.assertEqual(context['items'][0]['scientific_support'], 'UNKNOWN')

    def test_every_delivered_item_once_exact_hash_and_substantive_use(self):
        _, _, generated = self.build()
        context = generated['agent_context']
        good = self.use(context)
        variants = []
        bad = deepcopy(good); bad['packet_sha256'] = '0' * 64; variants.append(bad)
        bad = deepcopy(good); bad['decisions'] = []; variants.append(bad)
        bad = deepcopy(good); bad['decisions'][0]['id'] = 'invented'; variants.append(bad)
        bad = deepcopy(good); bad['decisions'].append(deepcopy(bad['decisions'][0])); variants.append(bad)
        bad = deepcopy(good); bad['schema'] = True; variants.append(bad)
        bad = deepcopy(good); bad['decisions'][0]['disposition'] = True; variants.append(bad)
        bad = deepcopy(good); bad['decisions'][0]['reason'] = 'ACK ACK ACK'; variants.append(bad)
        bad = deepcopy(good); bad['decisions'][0]['next_step'] = 'ACK'; variants.append(bad)
        bad = deepcopy(good); bad['decisions'][0]['reason'] = 'x' * 2049; variants.append(bad)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                jump.validate_use(context, value)
        changed = deepcopy(context)
        changed['items'][0]['assumptions'].append('Changed after delivery')
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            jump.validate_use(changed, good)

    def test_same_scoped_hypothesis_refutation_retains_other_proposal_identity(self):
        write = example.write
        def rival_oracle(path, value):
            if path.name == 'oracle.json':
                value = [{'inputs': row['inputs'], 'value': row['inputs']['x'] + row['inputs']['y']}
                         for row in value]
            return write(path, value)
        with patch.object(example, 'write', rival_oracle):
            root, store = build_multi_proposal_example(self.root)
        generated = jump.generate(root, 3)
        ident = generated['proposal_ids'][0]
        original = structure._find(store, 'PROPOSAL', ident)
        alias = deepcopy(original['proposal'])
        alias['id'] = 'other-scoped-proposal'
        for run in alias['experiment']['runs']:
            run['id'] += '-other'
        alias['experiment']['runs'][1]['argv'].append(alias['id'])
        structure.propose(root, alias)
        feedback = structure.advance(root, alias['id'])
        self.assertEqual(feedback['observation'], 'REFUTE')
        self.assertIsNone(structure._find(store, 'FEEDBACK', ident))
        before = store.snapshot(), structure._events(store)
        context = jump.packet(store)
        item = context['items'][0]
        self.assertEqual(context['status'], 'CURRENT')
        self.assertEqual(item['id'], ident)
        self.assertEqual(item['proposal_sha256'], original['proposal_sha256'])
        self.assertEqual(item['observation'], 'REFUTE')
        self.assertEqual(item['feedback'], feedback)
        self.assertEqual(item['feedback']['id'], alias['id'])
        self.assertEqual(item['hypothesis_refutation'], structure._route_constraint(store, original))
        self.assertEqual(item['hypothesis_refutation']['feedback_sha256'], digest(feedback))
        self.assertEqual(item['scientific_support'], 'UNKNOWN')
        self.assertEqual((store.snapshot(), structure._events(store)), before)
        # A separately admitted unrelated hypothesis with authentic native
        # feedback cannot refute this generated item merely by matching prose.
        with patch.object(example, 'write', rival_oracle):
            other_root, other_store = build_multi_proposal_example(self.root.with_name('unrelated'))
        other_generated = jump.generate(other_root, 3)
        other_ident = other_generated['proposal_ids'][0]
        unrelated = deepcopy(structure._find(other_store, 'PROPOSAL', other_ident)['proposal'])
        unrelated['id'] = alias['id']
        unrelated['discriminator']['hypothesis_id'] = 'unrelated-hypothesis'
        for run in unrelated['experiment']['runs']:
            run['id'] += '-other'
        unrelated['experiment']['runs'][1]['argv'].append(unrelated['id'])
        structure.propose(other_root, unrelated)
        other_feedback = structure.advance(other_root, unrelated['id'])
        self.assertEqual(other_feedback['observation'], 'REFUTE')
        other_before = other_store.snapshot(), structure._events(other_store)
        unrelated_packet = jump.packet(other_store)
        self.assertEqual(unrelated_packet['status'], 'CURRENT')
        self.assertEqual(unrelated_packet['items'][0]['observation'], 'UNKNOWN')
        self.assertNotIn('hypothesis_refutation', unrelated_packet['items'][0])
        self.assertEqual((other_store.snapshot(), structure._events(other_store)), other_before)

    def test_no_candidate_is_exposed_to_ai_without_inventing_hypothesis(self):
        _, store, generated = self.build(corpus=[])
        context = generated['agent_context']
        self.assertEqual(context['status'], 'CURRENT')
        self.assertEqual(context['generation_status'], 'NO_CANDIDATE')
        self.assertEqual(context['items'][0]['kind'], 'no_candidate')
        self.assertEqual(context['items'][0]['observation'], 'UNKNOWN')
        self.assertTrue(context['generation_results']['probe']['diagnostic']['counterexamples'])
        self.assertIsNone(context['generation_results']['synthesize']['search']['candidate'])
        self.assertEqual(jump.validate_use(context, self.use(context))['decisions'][0]['id'], generated['id'])
        self.assertEqual(jump.packet(store), context)

    def test_repeat_and_cached_generation_do_not_record_or_charge_use(self):
        _, store, generated = self.build()
        original = store.snapshot()
        events = structure._events(store)
        with patch.object(structure, '_meter', side_effect=AssertionError('Read cannot reserve budget')):
            context = jump.packet(store)
            self.assertEqual(jump.generate(store.root, 3)['agent_context'], context)
            jump.validate_use(context, self.use(context))
        self.assertEqual(store.snapshot(), original)
        self.assertEqual(structure._events(store), events)
        self.assertEqual(context['use_assurance'], 'NOT_RECORDED')
        persisted = structure._find(store, 'JUMP_FINISHED', generated['id'])
        self.assertNotIn('agent_context', persisted)

    def test_packet_bound_preserves_full_original_and_forbids_omitted_use(self):
        corpus = [{'id': 'long-source', 'text': 'interaction ' + 'original source detail ' * 2000,
                   'operators': ['mul']}]
        _, store, generated = self.build(corpus=corpus)
        context = generated['agent_context']
        self.assertEqual(context['status'], 'NEEDS_ORIGINAL')
        self.assertLessEqual(len(canonical(context).encode('utf-8')), jump.PACKET_BYTES)
        self.assertEqual(context['items'], [])
        self.assertGreater(context['omissions']['items'], 0)
        full = structure._read_ref(store, context['original'])
        self.assertEqual(full['status'], 'CURRENT')
        self.assertIn('mul', full['items'][0]['explanation'])
        self.assertEqual(full['generation_results']['refresh']['sources'][0]['text'], corpus[0]['text'])
        with self.assertRaises(ValueError):
            jump.validate_use(context, self.use(full))

    def test_before_generation_and_changed_plan(self):
        root, store = example.build(self.root)
        self.assertIsNone(jump.packet(store))
        jump.generate(root, 1)
        context = jump.packet(store)
        self.assertEqual(context['status'], 'UNAVAILABLE')
        self.assertFalse(context['items'])
        (root / 'jump-generation.json').write_text('{}', encoding='utf-8')
        self.assertEqual(jump.packet(store)['status'], 'UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()

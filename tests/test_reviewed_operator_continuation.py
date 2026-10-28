"""Mocked integration tests; no research observations and no network calls."""
import asyncio
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_independent_mechanism_evidence as evidence_fixtures
from tests.test_independent_mechanism_evidence import Backend
from tests.test_offline_operator_loop import FakeBackend
from scripts.independent_mechanism_evidence import apply_review, generate, EvidenceStore
from scripts.offline_loop_protocol import atomic_json, read_json, file_hash
from scripts.reviewed_operator_continuation import prepare, ReviewedStore, ReviewedLoop
from scripts.operator_template_isolation import HIDDEN_SPECIALISTS, isolate_template


class EvaluationBackend(FakeBackend):
    async def generate(self, **kwargs):
        raise AssertionError('Continuation must not regenerate')

    async def execute(self, **kwargs):
        original = kwargs['operator']
        kwargs['operator'] = {'ReviewedAlpha': 'OfflineCountingAlpha', 'ReviewedBeta': 'OfflineCountingBeta'}.get(original, original)
        result = await super().execute(**kwargs)
        if result['selected_operator']: result['selected_operator'] = original
        return result


class HandoffTests(unittest.TestCase):
    def setUp(self):
        fixture = evidence_fixtures.EvidenceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.source, review = fixture.confirmed()
        apply_review(self.source, review)
        asyncio.run(generate(self.source, Backend()))
        self.root = fixture.path / 'continuation'
        self.paths = {}
        for role, nums in [('design', range(900, 905)), ('gate', range(910, 913)), ('regression', range(920, 923))]:
            rows = []
            for n in nums:
                if role != 'regression':
                    rows.append({'problem': f'How many ways to arrange {n} handoff objects?', 'solution': '\\boxed{1}'})
                if role != 'gate':
                    rows.append({'problem': f'Find the area of handoff triangle {n}.', 'solution': '\\boxed{1}'})
            path = fixture.path / (role + '.jsonl')
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            self.paths[role] = path

    def prepare(self, **kw):
        args = dict(root=self.root, evidence_run=self.source.root,
                    design_data=self.paths['design'], gate_data=self.paths['gate'], regression_data=self.paths['regression'])
        args.update(kw)
        prepare(**args)
        return ReviewedStore(self.root)

    def test_end_to_end_private_release_no_regeneration_and_resume(self):
        original = file_hash(self.source.root/'work/operator_library/operators.json')
        store = self.prepare()
        backend = EvaluationBackend()
        result = asyncio.run(ReviewedLoop(store, backend).run())
        self.assertTrue(result['promoted'], result)
        count = len(backend.executions)
        resumed = asyncio.run(ReviewedLoop(ReviewedStore(self.root), backend).run())
        self.assertEqual(result, resumed)
        self.assertEqual(count, len(backend.executions))
        self.assertEqual(original, file_hash(self.source.root/'work/operator_library/operators.json'))
        self.assertEqual([], backend.generated)
        frozen = read_json(self.root/'checkpoints/confirmation_freeze.json')['output']
        self.assertNotEqual(set(frozen['target_ids']), {r['problem_id'] for r in self.source.rows()})

    def test_gate_failure_archives_and_does_not_try_runner_up(self):
        store = self.prepare()
        backend = EvaluationBackend('bad_gate')
        result = asyncio.run(ReviewedLoop(store, backend).run())
        self.assertEqual('archived_hidden_failed', result['status'])
        self.assertFalse((self.root/'release').exists())
        self.assertFalse(any('gate/OfflineCountingBeta/' in k for k in backend.executions))

    def test_overlap_rejected_before_output(self):
        self.paths['gate'].write_text(json.dumps(self.source.rows()[0])+'\n')
        with self.assertRaisesRegex(ValueError, 'overlap'): self.prepare()
        self.assertFalse(self.root.exists())

    def test_scope_uses_reviewed_selector_not_feature_cluster(self):
        store = self.prepare()
        loop = ReviewedLoop(store, EvaluationBackend())
        yes = {'problem': 'How many possibilities?'}
        no = {'problem': 'Count possibilities.'}
        a, b = loop.split_scope([yes, no], {'selected': {'features': []}})
        self.assertEqual([yes], a)
        self.assertEqual([no], b)

    def test_candidate_and_source_snapshot_tampering_rejected(self):
        self.prepare()
        p = self.root/'work/operator_library/operators.json'
        payload = read_json(p)
        payload['operators']['ReviewedAlpha']['artifact']['class_code'] += '\n# changed'
        atomic_json(p, payload)
        with self.assertRaisesRegex(ValueError, 'candidate changed'): ReviewedStore(self.root)

    def test_snapshot_edit_rejected(self):
        self.prepare()
        p = self.root/'source_evidence/observations.json'
        atomic_json(p, [])
        with self.assertRaisesRegex(ValueError, 'Frozen input changed'): ReviewedStore(self.root)

    def test_unapproved_source_rejected(self):
        (self.source.root/'checkpoints/semantic_review.json').unlink()
        with self.assertRaisesRegex(ValueError, 'semantic review'): self.prepare()
        self.assertFalse(self.root.exists())

    def test_replay_confirmation_label_survives_release(self):
        store = self.prepare(evaluation_mode='historical_replay')
        result = asyncio.run(ReviewedLoop(store, EvaluationBackend()).run())
        self.assertTrue(result['promoted'])
        self.assertIn('Historical replay', result['scientific_scope'])
        library = read_json(Path(result['publication']['library_path']))
        record = library['operators'][result['winner']]
        self.assertIn('historical_replay_confirmation', json.dumps(record))
        self.assertNotIn('"source": "offline_independent_confirmation"', json.dumps(record))

    def test_template_has_no_hidden_specialist_code_or_description(self):
        for p in (self.source.root/'base_template').iterdir():
            text = p.read_text()
            for name in HIDDEN_SPECIALISTS: self.assertNotIn(name, text)
            self.assertNotIn('scripts.parameterized_tools', text)
            self.assertNotIn('scripts.restricted_prime_tool', text)
        self.assertEqual({}, read_json(self.source.root/'inputs/base_library.json')['operators'])

    def test_replay_evidence_cannot_be_labeled_independent(self):
        # A genuinely prepared replay, not a mutated manifest.
        fixture = self.fixture
        replay = fixture.prepare(root=fixture.path/'replay_source', evidence_mode='historical_replay')
        replay, review = fixture.confirmed(fixture.diagnosed(replay))
        apply_review(replay, review)
        asyncio.run(generate(replay, Backend()))
        with self.assertRaisesRegex(ValueError, 'Historical evidence'):
            self.prepare(evidence_run=replay.root)
        store = self.prepare(evidence_run=replay.root, evaluation_mode='historical_replay')
        self.assertEqual('historical_replay', store.manifest['evidence_mode'])

    def test_source_child_output_refused(self):
        with self.assertRaisesRegex(ValueError, 'outside the source'):
            prepare(root=self.source.root/'nested', evidence_run=self.source.root,
                    design_data=self.paths['design'], gate_data=self.paths['gate'], regression_data=self.paths['regression'])

    def test_evaluation_raw_request_tampering_rejected_on_resume(self):
        store = self.prepare()
        class RecordedFake(EvaluationBackend):
            async def execute(inner, **kwargs):
                # Synthetic raw response ledger entry, explicitly a fixture.
                atomic_json(store.root/'requests'/'fixture.json',
                            {'key': kwargs['key']+'/call_0', 'status': 'completed', 'response': 'fixture'})
                return await super().execute(**kwargs)
        loop = ReviewedLoop(store, RecordedFake())
        item = store.rows('design')[0]
        asyncio.run(loop.execute(item=item, stage='design', repeat=1))
        atomic_json(store.root/'requests'/'fixture.json', {'key': 'tampered', 'response': 'changed'})
        with self.assertRaisesRegex(ValueError, 'request evidence changed'):
            ReviewedStore(self.root)

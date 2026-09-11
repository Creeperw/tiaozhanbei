import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from APP.backend import database as m
from APP.backend.learning_continuity_service import build_learning_continuity
from APP.backend.multiscale_learning_service import build_multiscale_state
from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.contracts.multiscale_learning import MultiScaleLearningState
from competition_app.contracts.agent_context import _shared_user_portrait


class LearningContinuityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        m.Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add_all([
            m.UserModel(id=i, username=f'continuity{i}', email=f'{i}@test.local', hashed_password='x')
            for i in (1, 2)
        ])
        self.db.flush()
        self.db.add_all([
            m.KnowledgePoint(kp_id='OLD_FJ', name='四君子汤', source='legacy_catalog'),
            m.QuestionAttempt(user_id=1, question_id='Q1', kp_ids_json='["OLD_FJ", "OLD_FJ"]',
                              is_correct=True, score=100, created_at=datetime.utcnow() - timedelta(days=60)),
            m.QuestionAttempt(user_id=2, question_id='PRIVATE', kp_ids_json='["PRIVATE_KP"]'),
            m.LearnerKnowledgeMastery(user_id=1, kp_id='UNMAPPED', mastery=0.7),
            m.LearningPlanRecord(user_id=1, title='四君子汤学习计划', summary='方证对应',
                                 payload_json=json.dumps({'source': 'legacy_import'})),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_lifetime_history_keeps_provenance_without_inventing_mapping(self):
        result = build_learning_continuity(self.db, 1)
        self.assertEqual(result['attempt_count'], 1)
        self.assertTrue(result['has_history'])
        self.assertEqual(result['audit_status'], 'not_evaluated')
        self.assertEqual(result['topics'][0]['name'], '四君子汤')
        self.assertEqual(result['topics'][0]['attempt_count'], 1)
        self.assertEqual(result['topics'][1]['textbook_mapping_status'], 'not_verified')
        self.assertEqual(result['previous_plans'][0]['source'], 'legacy_import')
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('mastery', result['topics'][1])
        self.assertEqual(self.db.query(m.KnowledgeMasteryState).count(), 0)

    def test_contract_and_model_projection_preserve_continuation(self):
        state = build_multiscale_state(self.db, 1)
        normalized = MultiScaleLearningState.model_validate(state).model_dump(mode='json')
        projected = DiagnosisAgent._model_learning_state(normalized)
        history = projected['historical_learning']
        self.assertEqual(history['attempt_count'], 1)
        self.assertEqual(history['previous_plans'][0]['title'], '四君子汤学习计划')
        self.assertIn('不得因正式测评为空', history['continuation_policy'])
        portrait = _shared_user_portrait({
            'multi_scale_learning_state': state,
            'planner_multiscale_summary': {'has_long_term_plan': True},
        }, target_agent='audit_agent')
        self.assertEqual(portrait['historical_learning']['topics'][0]['name'], '四君子汤')
        self.assertEqual(portrait['historical_learning']['audit_status'], 'not_evaluated')
        self.assertEqual(self.db.query(m.LearningQuestionAttempt).count(), 0)
        self.assertEqual(state['state_digest'], build_multiscale_state(self.db, 1)['state_digest'])
        self.db.add(m.QuestionAttempt(user_id=1, question_id='Q2'))
        self.db.commit()
        self.assertNotEqual(state['state_digest'], build_multiscale_state(self.db, 1)['state_digest'])

    def test_empty_user_stays_empty_and_malformed_metadata_is_safe(self):
        result = build_learning_continuity(self.db, 999)
        self.assertFalse(result['has_history'])
        self.assertEqual(result['attempt_count'], 0)
        self.db.add(m.QuestionAttempt(user_id=1, question_id='BAD', kp_ids_json='{"bad":true}'))
        self.db.add(m.LearningPlanRecord(user_id=1, payload_json='[]'))
        self.db.commit()
        self.assertEqual(build_learning_continuity(self.db, 1)['attempt_count'], 2)
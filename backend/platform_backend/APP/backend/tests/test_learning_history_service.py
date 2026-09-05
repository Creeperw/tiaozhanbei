import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from APP.backend import database as m
from APP.backend.auth import get_current_user
from APP.backend.learning_history_service import history_page, session_messages, SECTIONS
from APP.backend.routers.learning_activity_routes import router


class LearningHistoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        m.Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add_all([m.UserModel(id=i, username=f'user{i}', email=f'u{i}@example.test', hashed_password='private') for i in (1, 2)])
        self.db.flush()
        for i in range(3):
            self.db.add(m.QuestionAttempt(user_id=1, question_id=f'Q{i}', answer='A', is_correct=True, score=100))
        self.db.add(m.QuestionAttempt(user_id=2, question_id='PRIVATE', answer='private', is_correct=False, score=0))
        self.db.add_all([m.DbSession(id='own', user_id=1, title='history'), m.DbSession(id='other', user_id=2, title='private')])
        self.db.flush()
        self.db.add_all([m.DbMessage(session_id='own', role='assistant', content='history text'), m.DbMessage(session_id='other', role='user', content='private')])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_history_is_paginated_and_owner_scoped(self):
        result = history_page(self.db, 1, 'attempts', 0, 2)
        self.assertEqual(result['total'], 3)
        self.assertTrue(result['has_more'])
        self.assertEqual(result['audit_status'], 'not_evaluated')
        self.assertEqual([r['question_id'] for r in result['items']], ['Q0', 'Q1'])
        self.assertEqual(len(history_page(self.db, 1, 'attempts', 2, 2)['items']), 1)

    def test_messages_reject_other_user_and_missing_session(self):
        self.assertIsNone(session_messages(self.db, 1, 'other'))
        self.assertIsNone(session_messages(self.db, 1, 'absent'))
        self.assertEqual(session_messages(self.db, 1, 'own')['items'][0]['content'], 'history text')

    def test_all_sections_have_real_whitelisted_fields(self):
        for section, (model, fields) in SECTIONS.items():
            for field in fields:
                self.assertTrue(hasattr(model, field), f'{section}.{field}')
            self.assertNotIn('hashed_password', fields)
            history_page(self.db, 1, section)
        with self.assertRaises(ValueError):
            history_page(self.db, 1, 'users')

    def test_route_auth_pagination_and_serialization(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[m.get_db] = lambda: self.db
        with TestClient(app) as client:
            self.assertEqual(client.get('/learning-activity/history').status_code, 401)
            app.dependency_overrides[get_current_user] = lambda: self.db.get(m.UserModel, 1)
            response = client.get('/learning-activity/history?section=attempts&limit=2')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['total'], 3)
            self.assertNotIn('hashed_password', response.text)
            for query in ('limit=101', 'offset=-1', 'limit=0'):
                self.assertEqual(client.get('/learning-activity/history?' + query).status_code, 422)
            self.assertEqual(client.get('/learning-activity/history?section=users').status_code, 400)
            self.assertEqual(client.get('/learning-activity/history/sessions/other/messages').status_code, 404)
            self.assertEqual(client.get('/learning-activity/history/sessions/own/messages').json()['total'], 1)


if __name__ == '__main__':
    unittest.main()
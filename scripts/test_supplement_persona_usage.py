import copy
from datetime import datetime, timedelta
import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import supplement_persona_usage as supplement


class PersonaSupplementTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 6, 10)
        self.catalog = json.loads(supplement.CATALOG.read_text())
        self.before = {name: [] for name in supplement.TABLES}
        self.profiles = {name: {'id': p['uid'], 'username': name, 'survey_json': json.dumps({
            'background': {'major': p['major']},
            'preferences': {'daily_available_minutes': p['minutes']}})}
            for name, p in supplement.PERSONAS.items()}

    def build(self):
        return supplement.build_plan(self.catalog,self.before,self.profiles,self.now)

    def test_different_personas_have_linked_answers_cards_and_reviews(self):
        before = copy.deepcopy(self.before)
        plan = self.build()
        self.assertEqual(self.before,before)
        self.assertEqual([s['added_questions'] for s in plan['summary']],[20,30,35])
        self.assertEqual([s['cards'] for s in plan['summary']],[3,4,5])
        self.assertTrue(all(s['review_attempts'] > 0 for s in plan['summary']))
        self.assertEqual(len({s['score_points']/s['added_questions'] for s in plan['summary']}),3)
        for name,rows in plan['rows'].items():
            self.assertTrue(all(row['user_id'] in (4,5,6) for row in rows))
        for row in plan['rows']['question_attempts']:
            feedback=json.loads(row['feedback'])
            number=int(row['question_id'].split('_')[-1])
            self.assertEqual(supplement.grade(number,feedback['answered_units']),(row['score'],row['is_correct']))
            self.assertLessEqual(row['created_at'],self.now)
            self.assertGreaterEqual(row['created_at'],datetime(2026,8,8)-timedelta(hours=8))
            self.assertFalse(feedback['audit_performed'])
        for row in plan['rows']['knowledge_card_records']:
            self.assertIn('演示数据',json.loads(row['resource_bundle_json'])['explanation']['content'])

    def test_mysql_float_storage_tolerance_is_narrow(self):
        self.assertTrue(supplement.stored_equal(33.33000183105469,33.33))
        self.assertTrue(supplement.stored_equal(0.33329999446868896,0.3333))
        self.assertFalse(supplement.stored_equal(33.34,33.33))
        self.assertFalse(supplement.stored_equal('changed','original'))

    def test_rerun_and_profile_changes_are_rejected(self):
        self.before['question_attempts'].append({'feedback':supplement.BATCH})
        with self.assertRaises(AssertionError):
            self.build()
        self.before['question_attempts'].clear()
        self.profiles['judge_nupt_ai']['survey_json']='{"background":{"major":"中医学"}}'
        with self.assertRaises(AssertionError):
            self.build()

    def test_schedule_respects_existing_focus_and_daily_budget(self):
        first=self.build()['rows']['learning_focus_sessions'][0]
        self.before['learning_focus_sessions'].append({**first,'active_seconds':35*60,'focus_session_id':'existing','resource_id':'existing_card'})
        plan=self.build()
        for row in plan['rows']['learning_focus_sessions']:
            if row['user_id']==first['user_id']:
                self.assertNotEqual(row['started_at'].date(),first['started_at'].date())

    def test_stub_statistics_history_and_resources_read_all_inserted_rows(self):
        from APP.backend import database
        from APP.backend.learning_statistics_service import build_learning_statistics,build_practice_history
        from APP.backend.learning_workshop_service import list_knowledge_cards,get_knowledge_card
        plan=self.build()
        engine=create_engine('sqlite://')
        database.Base.metadata.create_all(engine)
        with engine.begin() as conn:
            for uid in (3,4,5,6):
                conn.execute(database.UserModel.__table__.insert().values(id=uid,username=f'test_{uid}',hashed_password='x'))
            for name,rows in plan['rows'].items():
                for row in rows:
                    conn.execute(database.Base.metadata.tables[name].insert().values(**row))
        with Session(engine) as db:
            self.assertEqual(build_learning_statistics(db,3,now=self.now)['current_window']['questions_completed'],0)
            for uid,expected,cards in ((4,20,3),(5,30,4),(6,35,5)):
                stats=build_learning_statistics(db,uid,now=self.now)['current_window']
                history=build_practice_history(db,uid,now=self.now)
                self.assertEqual(stats['questions_completed'],expected)
                self.assertEqual(stats['audited_question_items_completed'],0)
                self.assertEqual(stats['available_points'],expected*100)
                self.assertEqual(history['total'],expected)
                self.assertTrue(all(r['title'] and '暂不可用' not in r['title'] for r in history['recent_activities']))
                self.assertEqual(len({r['activity_id'] for r in history['recent_activities']}),expected)
                library=list_knowledge_cards(db,user_id=uid,offset=0,limit=100)
                self.assertEqual(library['total'],cards)
                card=get_knowledge_card(db,user_id=uid,card_id=library['items'][0]['card_id'])
                self.assertTrue(card['resource_bundle']['explanation']['content'])
                self.assertIsNone(get_knowledge_card(db,user_id=3,card_id=library['items'][0]['card_id']))
        engine.dispose()


if __name__=='__main__':
    unittest.main()
"""Authorized synthetic demonstration supplement; no live app/ORM initialization.

Only the three explicitly approved accounts are writable. Existing rows, ssw,
mastery, profiles, credentials, audits and plans are never updated.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path

from sqlalchemy import MetaData, Table, select, text

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'backend/platform_backend/APP/backend/sample_data/shizhen_mvp_seed.json'
BATCH = 'persona_usage_20260906_v1'
TABLES = ('question_attempts', 'learning_activity_records', 'mistake_records',
          'learning_focus_sessions', 'knowledge_card_records')
PERSONAS = {
    'judge_nupt_ai': {
        'uid': 4, 'major': '人工智能', 'size': 2, 'sessions': 10, 'hour': 21,
        'minutes': 35, 'study_minutes': 10, 'answer_minutes': 3,
        'approach': '先用概念卡建立组成—功用—证候关系，再用短题检查理解；复习遗漏项。',
        'cards': ['KP_FJ_002', 'KP_FJ_003', 'KP_ZD_021'],
        'questions': [18,1,2,3,8,9,4,6,10,17,1,2,3,8,9,4,6,10,16,18],
        'partial': {1:1,4:1,7:0,8:0,12:0},
    },
    'judge_njucm_pharmacy': {
        'uid': 5, 'major': '药学', 'size': 5, 'sessions': 6, 'hour': 20,
        'minutes': 50, 'study_minutes': 7, 'answer_minutes': 3,
        'approach': '利用药学基础比较方中药物的配伍角色，重点补上功用与证候的对应关系。',
        'cards': ['KP_FJ_002', 'KP_FJ_004', 'KP_ZD_021', 'KP_FJ_018'],
        'questions': [1,4,5,6,7,20,15,3,9,12,5,6,7,20,15,10,11,12,13,14,1,4,5,6,7,20,15,9,12,19],
        'partial': {4:1,7:0,8:0,9:1,17:1,19:0,29:2},
    },
    'judge_tjutcm_tcm': {
        'uid': 6, 'major': '中医学', 'size': 5, 'sessions': 7, 'hour': 20,
        'minutes': 50, 'study_minutes': 7, 'answer_minutes': 4,
        'approach': '从病例证候提取病机，再推导治法与方剂；围绕气虚和虚寒做类方辨析。',
        'cards': ['KP_ZD_021', 'KP_FJ_003', 'KP_FJ_018', 'KP_CASE_001', 'KP_FJ_004'],
        'questions': [9,10,11,12,19,13,14,15,8,19,12,13,14,19,15,9,10,11,12,19,13,14,15,8,19,12,13,14,19,15,9,10,12,19,14],
        'partial': {3:1,4:2,7:1,14:1,19:2,27:0},
    },
}
# Exact rubric units from the archived catalog, not keyword grading of open text.
RUBRICS = {
    1:['人参','白术','茯苓','炙甘草'], 2:['益气健脾'], 3:['脾胃气虚证'],
    4:['人参'], 5:['健脾燥湿','助人参益气'], 6:['健脾渗湿'],
    7:['益气和中','调和诸药'], 8:['面色萎白','少气懒言','四肢乏力'],
    9:['舌淡苔白','脉虚弱'], 10:['益气健脾'], 11:['均可用于中焦脾胃病证'],
    12:['前者益气健脾','后者温中祛寒'], 13:['理中丸'], 14:['四君子汤'],
    15:['茯苓健脾渗湿','与益气药相配'], 16:['不能'], 17:['便于核验与追溯'],
    18:['四味'], 19:['脾胃气虚','益气健脾','四君子汤'], 20:['白术','茯苓'],
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def stored_equal(actual, expected):
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        return math.isclose(actual, expected, rel_tol=1e-7, abs_tol=1e-7)
    return actual == expected


def save_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())


def grade(number, selected_units):
    expected = RUBRICS[number]
    assert len(selected_units) == len(set(selected_units))
    assert set(selected_units).issubset(expected)
    return round(100 * len(selected_units) / len(expected), 2), selected_units == expected


def schedule(persona, before, now):
    uid = persona['uid']
    duration = persona['study_minutes'] + persona['size'] * persona['answer_minutes']
    beijing = timezone(timedelta(hours=8))
    today = now.replace(tzinfo=timezone.utc).astimezone(beijing).date()
    used = defaultdict(float)
    intervals = []
    for row in before['learning_focus_sessions']:
        if row['user_id'] != uid:
            continue
        start = row['started_at']
        end = row.get('ended_at') or now
        intervals.append((start, end))
        day = start.replace(tzinfo=timezone.utc).astimezone(beijing).date()
        used[day] += float(row.get('active_seconds') or 0) / 60
    for row in before['question_attempts']:
        if row['user_id'] == uid:
            intervals.append((row['created_at'] - timedelta(minutes=2), row['created_at'] + timedelta(minutes=2)))
    candidates = []
    for offset in range(29, 0, -1):
        day = today - timedelta(days=offset)
        if uid == 4 and day.weekday() >= 5:
            continue
        if used[day] + duration > persona['minutes']:
            continue
        for minute in (5, 31, 57):
            start = datetime.combine(day, datetime.min.time()).replace(hour=persona['hour'], minute=minute) - timedelta(hours=8)
            end = start + timedelta(minutes=duration)
            if end >= now or any(start < b and end > a for a, b in intervals):
                continue
            candidates.append((used[day], start))
            break
    # Prefer days without existing study, then spread across the whole window.
    count = persona['sessions']
    assert len(candidates) >= count, f'Insufficient non-overlapping study slots for {uid}'
    candidates.sort(key=lambda x: (x[0], x[1]))
    pool = sorted(start for used_minutes, start in candidates if used_minutes == candidates[0][0])
    if len(pool) < count:
        pool = sorted(start for _, start in candidates[:count])
    chosen = [pool[round(i * (len(pool) - 1) / (count - 1))] for i in range(count)]
    assert len(set(chosen)) == count
    return chosen


def build_plan(catalog, before, profiles, now):
    rows = {name: [] for name in TABLES}
    questions = {q['question_id']: q for q in catalog['question_bank']}
    kps = {k['kp_id']: k for k in catalog['knowledge_points']}
    summary = []
    for username, persona in PERSONAS.items():
        uid = persona['uid']
        profile = profiles[username]
        assert profile['id'] == uid
        survey = json.loads(profile['survey_json'])
        assert survey['background']['major'] == persona['major']
        assert int(survey['preferences']['daily_available_minutes']) == persona['minutes']
        assert not any(BATCH in encoded(r) for table in TABLES for r in before[table]), 'Batch already exists'
        slots = schedule(persona, before, now)
        card_ids = {}
        for kp in persona['cards']:
            assert not any(r['user_id'] == uid and r['kp_id'] == kp for r in before['knowledge_card_records']), 'Existing card must not be overwritten'
            card_id = f'{BATCH}_KC_{uid}_{kp}'
            card_ids[kp] = card_id
            relevant = [q for q in questions.values() if kp in q['kp_ids']]
            content = f"{persona['approach']}\n\n" + '\n\n'.join(f"{q['stem']}\n{q['answer']}。{q['analysis']}" for q in relevant)
            content += '\n\n来源：原有四君子汤教学示例目录。该学习记录为授权补充的演示数据，仅供学习，不替代诊疗。'
            bundle = {'schema_version':'1.0', 'knowledge_point':{'kp_id':kp,'name':kps[kp]['name']},
                      'explanation':{'content':content}, 'questions':[], 'videos':[], 'textbook_slices':[],
                      'estimated_minutes':persona['study_minutes'], 'source':BATCH,
                      'data_classification':'synthetic', 'reference_catalog_sha256':digest(catalog)}
            rows['knowledge_card_records'].append(dict(card_id=card_id,user_id=uid,kp_id=kp,title=kps[kp]['name'],
                learning_status='learned',resource_bundle_json=encoded(bundle),source_execution_id=BATCH,
                created_at=slots[0],updated_at=slots[0]+timedelta(minutes=persona['study_minutes'])))

        def activity(kind, resource, at, payload, status='completed', minutes=0, score=None, resource_type='knowledge_card'):
            rows['learning_activity_records'].append(dict(user_id=uid,activity_type=kind,resource_id=resource,
                resource_type=resource_type,duration_minutes=minutes,completion_status=status,score=score,
                payload_json=encoded({'source':BATCH,'data_classification':'synthetic','generated_at':now,**payload}),created_at=at))

        unresolved = {}
        attempts = []
        for session_index, start in enumerate(slots):
            duration = persona['study_minutes'] + persona['size'] * persona['answer_minutes']
            end = start + timedelta(minutes=duration)
            focus_id = f'{BATCH}_FOCUS_{uid}_{session_index}'
            kp = persona['cards'][session_index % len(persona['cards'])]
            card_id = card_ids[kp]
            view_id = f'{BATCH}_VIEW_{uid}_{session_index}'
            activity('dashboard_recommendations_view',view_id,start,{'recommendation_keys':[card_id]},'viewed',resource_type='dashboard_recommendations')
            activity('resource_click',card_id,start+timedelta(seconds=15),{'recommendation_view_id':view_id},'clicked')
            activity('resource_complete',card_id,start+timedelta(minutes=persona['study_minutes']),{'kp_ids':[kp]},minutes=persona['study_minutes'])
            rows['learning_focus_sessions'].append(dict(focus_session_id=focus_id,user_id=uid,task_id=None,
                resource_type='knowledge_card',resource_id=card_id,status='completed',is_visible=True,
                active_seconds=duration*60,started_at=start,ended_at=end,last_interaction_at=end,updated_at=end))
            for position in range(persona['size']):
                index = session_index * persona['size'] + position
                number = persona['questions'][index]
                qid = f'Q_SJZ_{number:03d}'
                question = questions[qid]
                units = RUBRICS[number]
                # Check that every rubric is an exact decomposition of the source answer.
                assert ''.join(units) == question['answer'].translate(str.maketrans('', '', '、，；'))
                answer_units = units[:persona['partial'].get(index, len(units))]
                score, correct = grade(number, answer_units)
                answer = '；'.join(answer_units) if answer_units else '未能作答'
                at = start + timedelta(minutes=persona['study_minutes']+(position+1)*persona['answer_minutes'])
                request = f'{BATCH}_ATT_{uid}_{index}'
                review = unresolved.get(qid)
                origin = 'question_training' if review else ('topic_training' if uid==4 else 'special_training' if uid==5 else 'question_training')
                feedback = encoded({'source':BATCH,'data_classification':'synthetic','rubric':units,
                    'answered_units':answer_units,'missing_units':[u for u in units if u not in answer_units],
                    'score_formula':'100 * answered_rubric_units / total_rubric_units','request_id':request,
                    'analysis':question['analysis'],'audit_performed':False})
                attempt = dict(user_id=uid,question_id=qid,answer=answer,is_correct=correct,score=score,
                    kp_ids_json=encoded(question['kp_ids']),feedback=feedback,created_at=at)
                rows['question_attempts'].append(attempt)
                attempts.append(attempt)
                activity('question_attempt',qid,at,{'request_id':request,'title':question['stem'],
                    'practice_origin':origin,'kp_ids':question['kp_ids'],'is_correct':correct,
                    'review_of_request_id':review,'catalog_source':'shizhen_mvp_seed.json'},
                    score=score/100,resource_type='question')
                if review:
                    activity('mistake_review',qid,at,{'request_id':request,'review_of_request_id':review,
                        'title':question['stem'],'is_correct':correct},resource_type='question')
                if not correct:
                    unresolved[qid] = request
                    rows['mistake_records'].append(dict(user_id=uid,question_id=qid,attempt_item_id=None,
                        question_version_id=None,kp_ids_json=encoded(question['kp_ids']),error_type='知识要点遗漏',
                        summary=f"[演示补充 {BATCH}] {question['stem']} 遗漏：{'、'.join(u for u in units if u not in answer_units)}；关联 {request}",
                        status='active',created_at=at,updated_at=at))
                elif review:
                    # Only resolve a mistake created by this batch, never an existing row.
                    for mistake in rows['mistake_records']:
                        if mistake['user_id']==uid and mistake['question_id']==qid and mistake['status']=='active':
                            mistake.update(status='resolved',updated_at=at)
                    unresolved.pop(qid, None)
        summary.append({'username':username,'user_id':uid,'added_questions':len(attempts),'study_sessions':len(slots),
            'added_minutes':sum(r['active_seconds']//60 for r in rows['learning_focus_sessions'] if r['user_id']==uid),
            'correct':sum(a['is_correct'] for a in attempts),'score_points':round(sum(a['score'] for a in attempts),2),
            'cards':len(card_ids),'review_attempts':sum(r['activity_type']=='mistake_review' and r['user_id']==uid for r in rows['learning_activity_records']),
            'dates_beijing':[(s+timedelta(hours=8)).isoformat() for s in slots]})
    assert [s['added_questions'] for s in summary] == [20,30,35]
    return {'batch':BATCH,'classification':'synthetic','generated_at':now,'catalog_sha256':digest(catalog),
            'before_sha256':digest(before),'profile_sha256':digest(profiles),'summary':summary,'rows':rows}


def run(args):
    from inspect_practice_dates import connect
    engine, settings = connect(args.pid)
    assert settings.mode == 'live'
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    with engine.begin() as conn:
        if not args.apply:
            conn.execute(text('SET TRANSACTION READ ONLY'))
        tables = {name:Table(name,MetaData(),autoload_with=conn) for name in TABLES}
        profiles = {r['username']:dict(r) for r in conn.execute(text(
            'SELECT u.id,u.username,p.survey_json FROM users u JOIN user_profiles p ON p.user_id=u.id WHERE u.id IN (4,5,6)')).mappings()}
        assert set(profiles)==set(PERSONAS)
        before = {}
        for name,table in tables.items():
            query = select(table).where(table.c.user_id.in_((4,5,6))).order_by(table.c.id)
            if args.apply:
                query = query.with_for_update()
            before[name] = [dict(r) for r in conn.execute(query).mappings()]
        if args.apply:
            plan = json.loads(Path(args.plan).read_text())
            assert digest(plan)==args.expected_sha256, 'Plan hash mismatch'
            assert plan['before_sha256']==digest(before), 'Source changed since dry-run'
            assert plan['profile_sha256']==digest(profiles), 'Profiles changed since dry-run'
            assert plan['catalog_sha256']==digest(catalog)
            # Rebuild from frozen time: the reviewed plan cannot contain arbitrary rows.
            rebuilt = build_plan(catalog,before,profiles,datetime.fromisoformat(plan['generated_at']))
            assert digest(rebuilt)==digest(plan), 'Plan does not match approved generator'
            directory = Path(args.backup_dir)
            directory.mkdir(parents=True,mode=0o700,exist_ok=True)
            save_private(directory/'before-and-plan.json',{'before':before,'plan':plan})
            inserted = defaultdict(list)
            for name, additions in rebuilt['rows'].items():
                for row in additions:
                    assert row['user_id'] in (4,5,6)
                    result=conn.execute(tables[name].insert().values(**row))
                    inserted[name].append(result.inserted_primary_key[0])
            for name,table in tables.items():
                after=[dict(r) for r in conn.execute(select(table).where(table.c.user_id.in_((4,5,6))).order_by(table.c.id)).mappings()]
                old=[r for r in after if r['id'] not in inserted[name]]
                assert digest(old)==digest(before[name]), f'Existing rows changed in {name}'
                assert len(after)==len(before[name])+len(rebuilt['rows'][name])
                by_id = {r['id']:r for r in after}
                for key, expected in zip(inserted[name], rebuilt['rows'][name]):
                    actual = by_id[key]
                    differences = [field for field,value in expected.items() if not stored_equal(actual[field],value)]
                    assert not differences, f'Inserted content mismatch: {name}/{key}/{differences}'
            save_private(directory/'inserted-ids-precommit.json',dict(inserted))
        else:
            plan=build_plan(catalog,before,profiles,datetime.utcnow().replace(microsecond=0))
            save_private(args.plan,plan)
        print(encoded({'apply':args.apply,'sha256':digest(plan),'summary':plan['summary'],
                       'tables':{name:len(rows) for name,rows in plan['rows'].items()}}))
    if args.apply:
        save_private(Path(args.backup_dir)/'committed.json',{'batch':BATCH,'plan_sha256':digest(plan),'inserted':dict(inserted)})
        print(encoded({'committed':True,'backup_dir':args.backup_dir}))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--pid',type=int,required=True)
    parser.add_argument('--plan',required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--expected-sha256',default='')
    parser.add_argument('--backup-dir',default='/srv/tiaozhanbei-backups/persona-usage-20260906')
    run(parser.parse_args())
"""Read-only archive of legacy learning records, not audited achievements."""
import json

from APP.backend import database as models


SECTIONS = {
    'activities': (models.LearningActivityRecord, ('id', 'activity_type', 'resource_id', 'duration_minutes', 'completion_status', 'score', 'created_at')),
    'attempts': (models.QuestionAttempt, ('id', 'question_id', 'kp_ids_json', 'answer', 'is_correct', 'score', 'feedback', 'created_at')),
    'mistakes': (models.MistakeRecord, ('id', 'question_id', 'kp_ids_json', 'error_type', 'summary', 'status', 'created_at')),
    'mastery': (models.LearnerKnowledgeMastery, ('id', 'kp_id', 'mastery', 'confidence', 'wrong_count', 'review_count', 'last_review_at', 'next_review_at')),
    'plans': (models.LearningPlanRecord, ('id', 'title', 'summary', 'status', 'payload_json', 'created_at')),
    'tasks': (models.LearningTask, ('id', 'task_id', 'task_type', 'task_content', 'kp_ids_json', 'resource_ids_json', 'status', 'estimated_minutes', 'due_at', 'completed_at', 'created_at')),
    'focus': (models.LearningFocusSession, ('id', 'focus_session_id', 'task_id', 'resource_type', 'resource_id', 'active_seconds', 'status', 'started_at', 'ended_at')),
    'daily_plans': (models.DailyTaskInstanceRecord, ('id', 'host_task_id', 'status', 'created_at')),
    'daily': (models.DailyTaskItemRecord, ('id', 'host_task_id', 'task_item_id', 'kp_id', 'item_kind', 'ordinal', 'required_question_count', 'status', 'completed_at', 'created_at')),
    'sessions': (models.DbSession, ('id', 'title', 'created_at')),
    'interventions': (models.LearningInterventionRecord, ('id', 'action', 'reason', 'feedback', 'effect_status', 'created_at')),
    'events': (models.AgentEvent, ('id', 'agent_name', 'event_type', 'input_summary', 'output_summary', 'created_at')),
}


def history_page(db, user_id, section, offset=0, limit=25):
    if section not in SECTIONS:
        raise ValueError('Unknown history section')
    model, fields = SECTIONS[section]
    query = db.query(model).filter(model.user_id == user_id)
    total = query.count()
    rows = query.order_by(model.id.asc()).offset(offset).limit(limit).all()
    items = []
    for row in rows:
        item = {field: getattr(row, field) for field in fields}
        if section == 'plans':
            try:
                payload = json.loads(item.pop('payload_json') or '{}')
            except (TypeError, ValueError):
                payload = {}
            item['source'] = payload.get('source', 'legacy') if isinstance(payload, dict) else 'legacy'
            if isinstance(payload, dict):
                for field in ('plan_id', 'target_kp_ids', 'daily_available_minutes'):
                    if field in payload:
                        item[field] = payload[field]
        items.append(item)
    return {'section': section, 'total': total, 'offset': offset, 'limit': limit,
            'has_more': offset + len(items) < total, 'items': items,
            'record_scope': 'legacy_archive', 'audit_status': 'not_evaluated'}


def session_messages(db, user_id, session_id, offset=0, limit=25):
    session = db.query(models.DbSession).filter_by(id=session_id, user_id=user_id).one_or_none()
    if session is None:
        return None
    query = db.query(models.DbMessage).filter_by(session_id=session_id)
    total = query.count()
    rows = query.order_by(models.DbMessage.id.asc()).offset(offset).limit(limit).all()
    return {'total': total, 'offset': offset, 'limit': limit,
            'has_more': offset + len(rows) < total,
            'items': [{'id': r.id, 'role': r.role, 'content': r.content, 'created_at': r.created_at} for r in rows]}
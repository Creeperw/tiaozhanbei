export function categoryForActivity(activity) {
  const activityType = String(activity?.activity_type || '').trim().toLowerCase();
  const resourceType = String(activity?.resource_type || '').trim().toLowerCase();
  const taskType = String(activity?.task_type || '').trim().toLowerCase();
  const mappings = {
    'training_workspace_task:special_training': 'special_training',
    'training_workspace_task:topic_training': 'topic_training',
    'training_workspace_task:question_training': 'question_training',
    'training_workspace_task:mistake_redo': 'question_training',
    'training_workspace_task:mistake_variation': 'question_training',
    'training_workspace_task:paper_generation': 'paper_workspace',
    'question_attempt:question': 'question_training',
    'question_attempt:user_question': 'question_training',
    'question_attempt:question_favorites': 'question_training',
    'paper_submission:paper': 'paper_workspace',
    'case_training:case_session': 'ai_patient_simulation',
    'case_training:simulated_patient_session': 'ai_patient_simulation',
  };
  return mappings[`${activityType}:${resourceType}`]
    || (activityType === 'training_workspace_task' ? mappings[`${activityType}:${taskType}`] : null)
    || null;
}

export function patientModeForActivity(activity) {
  const mode = String(activity?.payload?.practice_mode || activity?.practice_mode || '').trim().toLowerCase();
  const resourceType = String(activity?.resource_type || '').trim().toLowerCase();
  if (mode === 'acupuncture' || resourceType === 'acupuncture_session') return 'acupuncture';
  if (mode === 'topic' || mode === 'specialty' || resourceType === 'topic_session') return 'topic';
  return 'plus';
}

export function isVerifiedPracticeActivity(activity) {
  const status = String(activity?.completion_status || '').trim().toLowerCase();
  if (!['completed', 'complete', 'done', 'submitted', 'passed', 'needs_review'].includes(status)) return false;
  return categoryForActivity(activity) !== null;
}
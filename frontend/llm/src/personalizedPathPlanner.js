import {
  createAssistantSession,
  streamAssistantMessageOutcome,
} from './chatSessionClient';

const PLANNING_STAGES = [
  {
    key: 'long_term',
    label: '正在制定长期规划',
    request: '请为当前考试重新制定并替换长期学习规划。这是用户在学情调研后明确发起的长期规划重建，不要再询问要制定哪一层计划。',
  },
  {
    key: 'short_term',
    label: '正在生成短期计划',
    request: '请基于刚刚通过审核的长期规划，为当前考试制定并保存当前短期学习计划。',
  },
  {
    key: 'daily_task',
    label: '正在安排今日任务',
    request: '请基于刚刚通过审核的长期规划和短期计划，为当前考试制定并保存今日学习任务。',
  },
];

function targetDescription(target = {}) {
  const name = target.official_name || target.name || '当前考试';
  const examTrackId = target.exam_track_id || '';
  return `${name}${examTrackId ? `（考试标识：${examTrackId}）` : ''}`;
}

export async function buildPersonalizedLearningPath({ target, onStage, onUpdate, customRequirements = '' } = {}) {
  const targetText = targetDescription(target);
  const created = await createAssistantSession(`${targetText}个性化学习路径`);
  const sessionId = created?.conversation_id || created?.session_id || created?.id;
  if (!sessionId) throw new Error('无法创建学习规划会话');
  const requirementsText = String(customRequirements || '').trim();
  const requirementsBlock = requirementsText ? `\n【自定义需求】${requirementsText}` : '';

  for (const stage of PLANNING_STAGES) {
    onStage?.(stage);
    const outcome = await streamAssistantMessageOutcome(
      sessionId,
      `【当前考试】${targetText}\n【当前任务】${stage.request}\n【执行要求】优先使用刚完成的学情调研、用户画像和自定义需求；不得改为其他考试。如果仍缺少会导致计划无法可靠制定的必要信息，请明确追问，不要臆造。${requirementsBlock}`,
      { onUpdate },
    );
    if (outcome.status !== 'completed') {
      const error = new Error(outcome.visible || '学习路径规划需要补充信息');
      error.code = outcome.status || 'planning_incomplete';
      error.sessionId = sessionId;
      error.visible = outcome.visible;
      throw error;
    }
  }

  return { sessionId };
}

export { PLANNING_STAGES };

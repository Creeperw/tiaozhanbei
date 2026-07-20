function cleanSentencePart(value) {
  return String(value || '').trim().replace(/[。！？!?；;，,]+$/u, '');
}

export function buildAssistantGreeting({ username, goal, focus } = {}) {
  const learner = cleanSentencePart(username) || '同学';
  const todayGoal = cleanSentencePart(goal);
  const todayFocus = cleanSentencePart(focus);
  const plan = todayGoal
    ? `今天的学习目标是${todayGoal}${todayFocus ? `，${todayFocus}` : ''}。`
    : '今天可以继续完成你的学习计划。';
  return `你好，${learner}！${plan}有什么问题可以随时问我。`;
}

export function createNewAssistantState() {
  return {
    sessionId: null,
    messages: [],
    mode: 'new',
  };
}

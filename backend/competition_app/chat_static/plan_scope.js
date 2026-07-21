(function exposePlanScopeClassifier(root) {
  const longTermSections = [
    '【最终目标】',
    '【能力路径与阶段】',
    '【阶段里程碑】',
    '【资源预算】',
    '【重规划条件】',
    '【保温底线】',
  ];

  function hasAny(text, phrases) {
    return phrases.some(phrase => text.includes(phrase));
  }

  function extractInlineLongTermPlan(request) {
    const start = request.indexOf(longTermSections[0]);
    if (start < 0) return '';
    const candidate = request.slice(start);
    if (!longTermSections.every(section => candidate.includes(section))) return '';
    const supplementIndex = candidate.search(/[\r\n]+\s*(?:补充|说明|备注)\s*[:：]/);
    return candidate.slice(0, supplementIndex < 0 ? undefined : supplementIndex).trim();
  }

  function inferPlanScope(request) {
    const planningPhrases = ['制定', '规划', '计划', '安排', '调整', '修改', '更新', '重做', '重新'];
    const directDailyQuestion = /(?:当日|今日|今天|今晚)[^，。；！？?]{0,18}(?:学(?:习)?(?:些)?什么|学啥|要学|该学|看什么|看啥|做什么|做啥|复习什么|练什么)/.test(request);
    const directDailyTask = /(?:给我|再给我|来一个)[^，。；！？?]{0,18}(?:当日|今日|今天|今晚)(?:的)?(?:学习)?任务/.test(request);
    if (!directDailyQuestion && !directDailyTask && !hasAny(request, planningPhrases)) return null;

    // A pasted parent plan is supporting context, not another requested layer.
    // Resolve the leading user instruction before scanning fixed plan sections,
    // whose text may naturally contain words such as “长期” or “每日”.
    const supportingPlanIndex = request.search(
      /[\r\n]*\s*【(?:最终目标|能力路径与阶段|阶段里程碑|资源预算|重规划条件|保温底线)】/
    );
    if (supportingPlanIndex > 0) {
      const leadingScope = inferPlanScope(request.slice(0, supportingPlanIndex));
      if (leadingScope && leadingScope !== 'unspecified') return leadingScope;
    }

    const scopePhrases = {
      long_term: ['长期', '教材路线', '阶段路线', '最终目标'],
      short_term: ['短期', '本周', '这周', '下周', '近期', '未来一周', '未来两周'],
      daily_task: ['当日', '今日', '今天', '今晚', '每日'],
    };
    const scopes = new Set(
      Object.entries(scopePhrases)
        .filter(([, phrases]) => hasAny(request, phrases))
        .map(([scope]) => scope)
    );

    const longAndShortAreJointTargets = /长期[^，。；]{0,12}(?:和|与|及|、)[^，。；]{0,12}短期|短期[^，。；]{0,12}(?:和|与|及|、)[^，。；]{0,12}长期/.test(request);
    const longTermIsParent = /(?:根据|基于|按照|结合)[^，。；]{0,20}(?:长期(?:学习)?(?:规划|计划)|教材路线|阶段路线)/.test(request);
    const shortTermIsParent = /(?:根据|基于|按照|结合)[^，。；]{0,20}(?:短期(?:学习)?(?:规划|计划)|本周计划|这周计划)/.test(request);

    if (request.includes('任务') && (!scopes.has('short_term') || shortTermIsParent)) {
      scopes.add('daily_task');
    }

    if (scopes.has('daily_task')) {
      if (shortTermIsParent) scopes.delete('short_term');
      if (longTermIsParent) scopes.delete('long_term');
    } else if (scopes.has('short_term') && longTermIsParent && !longAndShortAreJointTargets) {
      scopes.delete('long_term');
    }

    return scopes.size === 1 ? [...scopes][0] : 'unspecified';
  }

  function inferPlanLayerAnswer(answer) {
    const scopes = [];
    if (hasAny(answer, ['长期', '教材路线', '阶段路线'])) scopes.push('long_term');
    if (hasAny(answer, ['短期', '本周', '这周', '近期'])) scopes.push('short_term');
    if (hasAny(answer, ['当日', '今日', '今天', '今晚', '每日', '任务'])) scopes.push('daily_task');
    return scopes.length === 1 ? scopes[0] : null;
  }

  root.inferPlanScope = inferPlanScope;
  root.inferPlanLayerAnswer = inferPlanLayerAnswer;
  root.extractInlineLongTermPlan = extractInlineLongTermPlan;
}(globalThis));

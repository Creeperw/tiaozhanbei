import { beforeEach, describe, expect, it, vi } from 'vitest';
import { buildPersonalizedLearningPath } from './personalizedPathPlanner';
import { createAssistantSession, streamAssistantMessageOutcome } from './chatSessionClient';
import { getWorkflowRun } from './workflowChatClient';

vi.mock('./chatSessionClient', () => ({
  createAssistantSession: vi.fn(),
  streamAssistantMessageOutcome: vi.fn(),
}));
vi.mock('./workflowChatClient', () => ({
  getWorkflowRun: vi.fn(),
}));

describe('personalizedPathPlanner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createAssistantSession.mockResolvedValue({ id: 'conversation-plan' });
    streamAssistantMessageOutcome.mockResolvedValue({ status: 'completed', visible: '已完成' });
    getWorkflowRun.mockResolvedValue(null);
  });

  it('recovers the disconnected run without resubmitting the long-term plan', async () => {
    streamAssistantMessageOutcome.mockRejectedValueOnce(Object.assign(new TypeError('network error'), { runId: 'THREAD_ORIGINAL' }));
    getWorkflowRun.mockResolvedValue({ status: 'completed', message: '长期规划已保存' });
    await buildPersonalizedLearningPath({});
    expect(getWorkflowRun).toHaveBeenCalledWith('THREAD_ORIGINAL');
    expect(streamAssistantMessageOutcome).toHaveBeenCalledTimes(3);
    expect(streamAssistantMessageOutcome.mock.calls[1][1]).toContain('短期学习计划');
  });

  it('stops at human review instead of treating it as missing information', async () => {
    streamAssistantMessageOutcome.mockRejectedValueOnce(Object.assign(new TypeError('network error'), { runId: 'THREAD_REVIEW' }));
    getWorkflowRun.mockResolvedValue({ status: 'waiting_human_review', result: { review_id: 'HR_1' } });
    await expect(buildPersonalizedLearningPath({})).rejects.toMatchObject({ code: 'waiting_human_review', stageIndex: 0, runId: 'THREAD_REVIEW', visible: expect.stringContaining('未通过自动审核') });
    expect(streamAssistantMessageOutcome).toHaveBeenCalledOnce();
  });

  it('restores an existing completed stage by GET only before starting its children', async () => {
    getWorkflowRun.mockResolvedValue({ status: 'completed' });
    await buildPersonalizedLearningPath({ continuation: { sessionId: 'saved', runId: 'THREAD_SAVED', stageIndex: 0, recover: true } });
    expect(createAssistantSession).not.toHaveBeenCalled();
    expect(streamAssistantMessageOutcome).toHaveBeenCalledTimes(2);
    expect(streamAssistantMessageOutcome.mock.calls[0][1]).toContain('短期学习计划');
  });

  it('retains recovery coordinates when status is unavailable', async () => {
    vi.useFakeTimers();
    try {
      getWorkflowRun.mockRejectedValue(new TypeError('network error'));
      const pending = buildPersonalizedLearningPath({ continuation: { sessionId: 'saved', runId: 'THREAD_SAVED', stageIndex: 1, recover: true } });
      const assertion = expect(pending).rejects.toMatchObject({ code: 'connection_lost', sessionId: 'saved', runId: 'THREAD_SAVED', stageIndex: 1 });
      await vi.runAllTimersAsync();
      await assertion;
      expect(streamAssistantMessageOutcome).not.toHaveBeenCalled();
    } finally { vi.useRealTimers(); }
  });

  it('uses one session to build long-term, short-term, and daily layers in order', async () => {
    const onStage = vi.fn();
    await expect(buildPersonalizedLearningPath({
      target: { official_name: '中医执业医师资格考试', exam_track_id: 'track-a' },
      onStage,
    })).resolves.toEqual({ sessionId: 'conversation-plan' });

    expect(streamAssistantMessageOutcome).toHaveBeenCalledTimes(3);
    expect(streamAssistantMessageOutcome.mock.calls.map(([, content]) => content)).toEqual([
      expect.stringContaining('长期学习规划'),
      expect.stringContaining('短期学习计划'),
      expect.stringContaining('今日学习任务'),
    ]);
    expect(streamAssistantMessageOutcome.mock.calls.every(([sessionId]) => sessionId === 'conversation-plan')).toBe(true);
    expect(onStage).toHaveBeenCalledTimes(3);
  });

  it('declares the planning session as a system surface so it never joins the assistant history', async () => {
    await buildPersonalizedLearningPath({ target: { official_name: '中医执业医师资格考试' } });

    // 会话在首个工作流落库之前就要先建出来；创建时不声明来源，规划向导会在
    // AI 助手历史里以内部指令原文当标题短暂出现，用户中途离开就永久留下。
    expect(createAssistantSession).toHaveBeenCalledWith(
      expect.stringContaining('个性化学习路径'),
      { source: 'system' },
    );
    // 每个阶段的请求同样要声明表面：这是系统自有枚举，用户自由文本不参与判定。
    expect(streamAssistantMessageOutcome).toHaveBeenCalledTimes(3);
    expect(
      streamAssistantMessageOutcome.mock.calls.every(
        ([, , options]) => options?.conversationSurface === 'system_task',
      ),
    ).toBe(true);
  });

  it('attaches custom requirements to every planning message when provided', async () => {
    await buildPersonalizedLearningPath({
      target: { official_name: '中医执业医师资格考试' },
      customRequirements: '希望侧重方剂背诵，每天只学 30 分钟',
    });

    const contents = streamAssistantMessageOutcome.mock.calls.map(([, content]) => content);
    expect(contents.length).toBe(3);
    expect(contents.every(
      (content) => content.includes('【自定义需求】希望侧重方剂背诵，每天只学 30 分钟'),
    )).toBe(true);
  });

  it('connects only safe progress to the dialog, not model or final-answer text', async () => {
    const onUpdate = vi.fn();
    streamAssistantMessageOutcome.mockImplementation(async (_session, _content, options) => {
      options.onUpdate?.('"clarific');
      options.onProgress?.('正在处理当前环节…');
      return { status: 'completed', visible: '完整的规划正文' };
    });
    await buildPersonalizedLearningPath({ onUpdate });
    expect(onUpdate.mock.calls).toEqual(Array(3).fill(['正在处理当前环节…']));
    for (const [, , options] of streamAssistantMessageOutcome.mock.calls) {
      expect(options.onUpdate).toBeUndefined();
      expect(options.onProgress).toBe(onUpdate);
    }
  });

  it('stops before child plans when an upstream layer needs clarification', async () => {
    streamAssistantMessageOutcome.mockResolvedValueOnce({
      status: 'interrupted',
      visible: '请补充每日可用时长',
      runId: 'THREAD_CLARIFY',
    });
    getWorkflowRun.mockResolvedValue({
      status: 'interrupted',
      interrupt: { reason: '信息不足', questions: ['每天可学习多长时间？'] },
    });

    await expect(buildPersonalizedLearningPath({
      target: { official_name: '中医执业医师资格考试' },
    })).rejects.toMatchObject({
      code: 'interrupted',
      sessionId: 'conversation-plan',
      runId: 'THREAD_CLARIFY',
      stageIndex: 0,
      visible: '每天可学习多长时间？',
    });
    expect(streamAssistantMessageOutcome).toHaveBeenCalledOnce();
  });

  it('resumes the interrupted stage in the same session and continues later stages', async () => {
    await buildPersonalizedLearningPath({
      target: { official_name: '中医执业医师资格考试' },
      continuation: {
        sessionId: 'conversation-existing',
        runId: 'THREAD_CLARIFY',
        stageIndex: 1,
      },
      clarificationAnswer: '计划持续六周，我已学完《中医学基础》。',
    });

    expect(createAssistantSession).not.toHaveBeenCalled();
    expect(streamAssistantMessageOutcome).toHaveBeenCalledTimes(2);
    expect(streamAssistantMessageOutcome.mock.calls[0].slice(0, 2)).toEqual([
      'conversation-existing',
      '计划持续六周，我已学完《中医学基础》。',
    ]);
    expect(streamAssistantMessageOutcome.mock.calls[1][1]).toContain('今日学习任务');
  });
});

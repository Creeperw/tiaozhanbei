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

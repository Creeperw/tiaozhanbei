import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  compactAssistantContent,
  resolveAssistantSessionId,
  streamAssistantMessage,
} from './chatSessionClient';

describe('chatSessionClient', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it('removes workflow protocol markers and hidden thinking from compact answers', () => {
    const raw = '<think>内部推理</think><<STATUS:searching:检索中>><<EV:{"type":"node_started"}>>四君子汤主治脾胃气虚证。';
    expect(compactAssistantContent(raw)).toBe('四君子汤主治脾胃气虚证。');
  });

  it('keeps only regenerated content after a rollback marker', () => {
    const raw = '旧回答<<ROLLBACK:审核未通过>><think>重写</think>新回答';
    expect(compactAssistantContent(raw)).toBe('新回答');
  });

  it('prefers an explicit session and otherwise restores the saved real session', () => {
    const sessions = [{ id: 'saved' }, { id: 'latest' }];
    expect(resolveAssistantSessionId(sessions, 'latest', 'saved')).toBe('latest');
    expect(resolveAssistantSessionId(sessions, null, 'saved')).toBe('saved');
    expect(resolveAssistantSessionId(sessions, null, 'missing')).toBe('saved');
  });

  it('does not start a second turn while the same conversation is still running', async () => {
    localStorage.setItem(
      'assistantPendingWorkflowRuns',
      JSON.stringify({ 'session-a': 'THREAD_RUNNING' }),
    );
    const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      thread_id: 'THREAD_RUNNING',
      status: 'running',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', request);

    await expect(streamAssistantMessage('session-a', '第二条消息')).rejects.toMatchObject({
      code: 'workflow_running',
    });
    expect(request).toHaveBeenCalledTimes(1);
    expect(request.mock.calls[0][0]).toContain('/review-cards/runs/THREAD_RUNNING');
  });
});

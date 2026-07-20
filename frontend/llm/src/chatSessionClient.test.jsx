import { describe, expect, it } from 'vitest';
import { compactAssistantContent, resolveAssistantSessionId } from './chatSessionClient';

describe('chatSessionClient', () => {
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
});

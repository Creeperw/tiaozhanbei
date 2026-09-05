import { beforeEach, describe, expect, it, vi } from 'vitest';

import { loadAtlasNodes } from './knowledge-atlas/knowledgeAtlasApi';
import { loadTextbookProgress } from './workshop-textbook/textbookChapterApi';
import { loadTextbookLearningSummary } from './learningPlanDashboard';

vi.mock('./knowledge-atlas/knowledgeAtlasApi', () => ({
  loadAtlasNodes: vi.fn(),
}));

vi.mock('./workshop-textbook/textbookChapterApi', () => ({
  loadTextbookProgress: vi.fn(),
}));

describe('lightweight textbook learning summary', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('uses chapter child counts without loading every chapter section page', async () => {
    loadAtlasNodes.mockResolvedValue({
      nodes: [
        { id: 'CH_1', name: '第一章', children_count: 2 },
        { id: 'CH_2', name: '第二章', children_count: 3 },
      ],
    });
    loadTextbookProgress.mockResolvedValue({
      completed_section_ids: ['S_1', 'S_2'],
      last_section_id: 'S_2',
      history: [{ chapter_name: '第一章', timestamp: '2026-08-15T00:00:00Z' }],
    });

    const summary = await loadTextbookLearningSummary('《中医学基础》');

    expect(loadAtlasNodes).toHaveBeenCalledOnce();
    expect(loadAtlasNodes).toHaveBeenCalledWith(expect.objectContaining({
      level: 2,
      lv1: '中医学基础',
    }));
    expect(summary).toMatchObject({
      completedSections: 2,
      totalSections: 5,
      progress: 0.4,
      lastSectionId: 'S_2',
      lastChapterName: '第一章',
    });
    expect(summary.nextSectionId).toBe('');
  });
});

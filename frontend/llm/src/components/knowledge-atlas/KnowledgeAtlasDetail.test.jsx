import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import KnowledgeAtlasDetail from './KnowledgeAtlasDetail';

vi.mock('./knowledgeAtlasApi', () => ({
  atlasImageUrl: (filename) => `/atlas/${filename}`,
  loadAtlasImage: vi.fn(),
}));

vi.mock('../../pageDataLoaders', () => ({
  recordDailyTaskVideoEvidence: vi.fn(),
  confirmDailyTaskIframeVideo: vi.fn(),
}));

vi.mock('../../utils/api', () => ({ fetchJsonWithAuthFallback: vi.fn() }));

describe('KnowledgeAtlasDetail video evidence', () => {
  it('requires effective focus before an iframe video can be confirmed', () => {
    render(<KnowledgeAtlasDetail
      node={{ id: 'KP_1', name: '四君子汤' }}
      detail={{
        kp: { id: 'KP_1', name: '四君子汤' }, chunks: [], questions: [],
        videos: [{ bvid: 'BV1test', page: 1, start_seconds: 0, end_seconds: 100, topic: '配伍讲解' }],
      }}
      loading={false}
      error=""
      taskItemId="ITEM_VIDEO"
      onClose={vi.fn()}
    />);
    fireEvent.click(screen.getByRole('button', { name: '00:00 配伍讲解' }));
    expect(screen.getByText(/第三方播放器无法读取真实进度/)).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: '有效专注进度' })).toHaveValue(0);
    expect(screen.getByRole('button', { name: '确认看完' })).toBeDisabled();
  });
});
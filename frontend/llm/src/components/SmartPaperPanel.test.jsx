import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SmartPaperPanel from './SmartPaperPanel';
import { loadPapers, loadReportsData } from '../pageDataLoaders';

vi.mock('../utils/api', () => ({
  fetchJsonWithAuthFallback: vi.fn(),
}));

vi.mock('../pageDataLoaders', () => ({
  generateWorkshopPaperWithAgents: vi.fn(),
  loadPaper: vi.fn(),
  loadPapers: vi.fn(),
  loadReportsData: vi.fn(),
}));

vi.mock('./PaperGenerationPanel', () => ({
  default: ({ onExit }) => (
    <div>
      paper workspace
      <button type="button" onClick={onExit}>退出试卷</button>
    </div>
  ),
}));

describe('SmartPaperPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    loadPapers.mockResolvedValue({
      papers: {
        items: [
          { paper_id: 'P1', title: '待办一', status: 'published', duration_minutes: 30 },
          { paper_id: 'P2', title: '历史一', status: 'completed', duration_minutes: 45 },
        ],
      },
    });
    loadReportsData.mockResolvedValue({
      report: {
        weak_points: [
          { kp_name: '四君子汤配伍' },
          { kp_name: '气血津液辨证' },
        ],
      },
    });
  });

  it('shows the paper list below the preview with completion states', async () => {
    render(<SmartPaperPanel />);

    expect(await screen.findByText('待办一')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '试卷列表' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'AI推荐主题' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '四君子汤配伍' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '四君子汤配伍' }));
    expect(screen.getByLabelText('专项练主题')).toHaveValue('四君子汤配伍');
    expect(screen.getByText('未完成')).toBeInTheDocument();
    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.getByText('历史一')).toBeInTheDocument();
    expect(screen.queryByText('历史存档')).not.toBeInTheDocument();
    expect(screen.getByLabelText('专项练主题')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /错题集重做/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText('单选题')).toHaveAttribute('type', 'text');
  });
  it('returns a task-bound paper to the current smart-paper archive', async () => {
    render(<SmartPaperPanel taskItemId="TASK_ITEM_1" />);

    expect(screen.getByText('paper workspace')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '退出试卷' }));

    expect(await screen.findByText('待办一')).toBeInTheDocument();
    expect(screen.queryByText('paper workspace')).not.toBeInTheDocument();
  });
});

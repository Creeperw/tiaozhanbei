import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SmartPaperPanel from './SmartPaperPanel';
import { generateWorkshopPaperWithAgents, loadPaper, loadPapers, loadReportsData } from '../pageDataLoaders';

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
    expect(screen.getByLabelText('专项练主题')).toBeInTheDocument();
    expect(screen.getByText('历史一')).toBeInTheDocument();
    expect(screen.getByText('未完成')).toBeInTheDocument();
    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /开始答题/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /查看试卷/ })).toBeInTheDocument();
    expect(screen.getByLabelText('单选题')).toHaveAttribute('type', 'text');
  });
  it('returns a task-bound paper to the current smart-paper archive', async () => {
    render(<SmartPaperPanel taskItemId="TASK_ITEM_1" />);

    expect(screen.getByText('paper workspace')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '退出试卷' }));

    expect(await screen.findByText('待办一')).toBeInTheDocument();
    expect(screen.queryByText('paper workspace')).not.toBeInTheDocument();
  });

  it('lets the user pick a difficulty and passes it through when generating', async () => {
    generateWorkshopPaperWithAgents.mockResolvedValue({
      paperId: 'P_GEN_1',
      result: { status: 'success', task_type: 'paper_generation', ui_actions: [] },
      error: '',
      source: null,
    });
    loadPaper.mockResolvedValue({
      paper: { paper_id: 'P_GEN_1', title: '难度试卷', status: 'published', duration_minutes: 30 },
      error: '',
    });
    render(<SmartPaperPanel />);

    expect(screen.getByRole('heading', { name: /难度要求/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '不限' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: '难度 3' }));
    expect(screen.getByRole('button', { name: '难度 3' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('难度 3 星')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('专项练主题'), { target: { value: '四君子汤组成' } });
    fireEvent.click(screen.getByRole('button', { name: /生成试卷/ }));

    expect(generateWorkshopPaperWithAgents).toHaveBeenCalledWith(
      expect.objectContaining({ difficulty: 3, topic: '四君子汤组成' }),
    );
  });
});

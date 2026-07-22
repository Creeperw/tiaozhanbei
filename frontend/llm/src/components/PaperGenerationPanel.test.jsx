import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import PaperGenerationPanel from './PaperGenerationPanel';
import { generateWorkshopPaperWithAgents, loadPaper, loadPapers, setPaperTimerPaused } from '../pageDataLoaders';

vi.mock('../pageDataLoaders', () => ({
  loadPaper: vi.fn(),
  loadPapers: vi.fn(),
  generateWorkshopPaperWithAgents: vi.fn(),
  savePaperAnswers: vi.fn(),
  setPaperTimerPaused: vi.fn(),
  submitPaper: vi.fn(),
}));

vi.mock('../utils/api', () => ({
  fetchJsonWithAuthFallback: vi.fn(),
}));

describe('PaperGenerationPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    loadPapers.mockResolvedValue({ papers: { items: [] }, error: '' });
  });

  it('supports the full set of paper question types', async () => {
    render(<PaperGenerationPanel enabled />);

    expect(await screen.findByRole('spinbutton', { name: '单选题' })).toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: '多选题' })).toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: '填空题' })).toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: '简答题' })).toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: '案例题' })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '难度' })).not.toBeInTheDocument();
  });

  it('generates a paper through the backend and opens the returned paper id', async () => {
    const paper = {
      paper_id: 'PAPER_GENERATED',
      title: '智能组卷',
      status: 'published',
      timing: { remaining_seconds: 600 },
      items: [{ paper_item_id: 'I1', position: 1, question_type: 'single_choice', stem: '题目', options: ['A', 'B'], answer: '' }],
    };
    generateWorkshopPaperWithAgents.mockResolvedValue({
      paperId: 'PAPER_GENERATED',
      error: '',
    });
    loadPaper.mockResolvedValue({ paper, error: '' });

    render(<PaperGenerationPanel enabled />);
    fireEvent.click(await screen.findByRole('button', { name: '生成试卷' }));

    expect(await screen.findByText('智能组卷')).toBeInTheDocument();
    expect(generateWorkshopPaperWithAgents).toHaveBeenCalledWith(expect.objectContaining({
      topic: '围绕四君子汤与脾胃气虚证完成训练',
      distribution: { single_choice: 1 },
    }));
    expect(sessionStorage.getItem('training-paper-id')).toBe('PAPER_GENERATED');
  });

  it('groups paper items by type and returns to the paper library with the timer stopped', async () => {
    const clearIntervalSpy = vi.spyOn(window, 'clearInterval');
    const paper = {
      paper_id: 'PAPER_1',
      title: '综合试卷',
      status: 'published',
      timing: { remaining_seconds: 120 },
      items: [
        { paper_item_id: 'I4', position: 4, question_type: 'short_answer', stem: '简答', options: [], answer: '' },
        { paper_item_id: 'I1', position: 1, question_type: 'single_choice', stem: '单选', options: ['A', 'B'], answer: '' },
        { paper_item_id: 'I3', position: 3, question_type: 'fill_blank', stem: '填空', options: [], answer: '' },
        { paper_item_id: 'I2', position: 2, question_type: 'multiple_choice', stem: '多选', options: ['A', 'B'], answer: '' },
      ],
    };
    loadPaper.mockResolvedValue({ paper, error: '' });
    loadPapers.mockResolvedValue({ papers: { items: [{ paper_id: 'PAPER_1', title: '综合试卷', status: 'published', duration_minutes: 30 }] }, error: '' });

    render(<PaperGenerationPanel enabled paperId="PAPER_1" />);

    expect(await screen.findByRole('heading', { name: '单选题' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '多选题' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '填空题' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '简答题' })).toBeInTheDocument();
    expect(screen.getByText('00:02:00')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '返回试卷列表' }));
    expect(await screen.findByRole('heading', { name: '待作答与历史试卷' })).toBeInTheDocument();
    await waitFor(() => expect(clearIntervalSpy).toHaveBeenCalled());
    expect(sessionStorage.getItem('training-paper-id')).toBeNull();
  });

  it('pauses and resumes the server-owned paper timer', async () => {
    const paper = {
      paper_id: 'PAPER_TIMER',
      title: '计时试卷',
      status: 'published',
      timing: { remaining_seconds: 120, paused: false },
      items: [{ paper_item_id: 'I1', position: 1, question_type: 'single_choice', stem: '单选', options: ['A', 'B'], answer: '' }],
    };
    loadPaper.mockResolvedValue({ paper, error: '' });
    setPaperTimerPaused
      .mockResolvedValueOnce({ paper: { ...paper, timing: { remaining_seconds: 119, paused: true } }, error: '' })
      .mockResolvedValueOnce({ paper: { ...paper, timing: { remaining_seconds: 119, paused: false } }, error: '' });

    render(<PaperGenerationPanel enabled paperId="PAPER_TIMER" />);

    fireEvent.click(await screen.findByRole('button', { name: '暂停计时' }));
    expect(await screen.findByRole('button', { name: '继续计时' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('计时已暂停');
    expect(setPaperTimerPaused).toHaveBeenLastCalledWith(expect.objectContaining({ paperId: 'PAPER_TIMER', paused: true }));

    fireEvent.click(screen.getByRole('button', { name: '继续计时' }));
    expect(await screen.findByRole('button', { name: '暂停计时' })).toBeInTheDocument();
    expect(setPaperTimerPaused).toHaveBeenLastCalledWith(expect.objectContaining({ paperId: 'PAPER_TIMER', paused: false }));
  });
});

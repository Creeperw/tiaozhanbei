import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import PaperGenerationPanel, { PaperQuestionContent } from './PaperGenerationPanel';
import { generateWorkshopPaperWithAgents, loadPaper, loadPapers, savePaperAnswers, setPaperTimerPaused } from '../pageDataLoaders';

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
    window.localStorage.clear();
    loadPapers.mockResolvedValue({ papers: { items: [] }, error: '' });
  });

  it('renders legacy question images without injecting arbitrary HTML', () => {
    render(<PaperQuestionContent content={'A. <img class="showpics" src="https://example.com/herb.png"><script>alert(1)</script>'} />);

    expect(screen.getByText('A.')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '题目配图' })).toHaveAttribute('src', 'https://example.com/herb.png');
    expect(document.querySelector('script')).toBeNull();
    expect(screen.getByText('alert(1)')).toBeInTheDocument();
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
    savePaperAnswers.mockResolvedValue({ paper, error: '' });

    render(<PaperGenerationPanel enabled paperId="PAPER_1" />);

    expect(await screen.findByText('单选')).toBeInTheDocument();
    expect(screen.getByText('单选题')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '展开答题卡' }));
    expect(screen.getByRole('heading', { name: '单选题' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '多选题' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '填空题' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '简答题' })).toBeInTheDocument();
    expect(screen.getByText('00:02:00')).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: '收起答题卡' }).at(-1));
    fireEvent.click(screen.getByRole('button', { name: '退出并保存' }));
    expect(await screen.findByRole('heading', { name: '待作答与历史试卷' })).toBeInTheDocument();
    await waitFor(() => expect(clearIntervalSpy).toHaveBeenCalled());
    expect(savePaperAnswers).toHaveBeenCalledWith(expect.objectContaining({ paperId: 'PAPER_1' }));
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

  it('opens a bound daily-task paper without exposing paper constraints', async () => {
    const paper = {
      paper_id: 'PAPER_BOUND', title: '今日冻结试卷', status: 'published',
      timing: { remaining_seconds: 600 },
      items: [{ paper_item_id: 'I1', position: 1, question_type: 'short_answer', stem: '冻结题目', options: [], answer: '' }],
    };
    generateWorkshopPaperWithAgents.mockResolvedValue({ paperId: 'PAPER_BOUND', error: '' });
    loadPaper.mockResolvedValue({ paper, error: '' });

    render(<PaperGenerationPanel enabled taskItemId="ITEM_PAPER" />);

    expect(await screen.findByText('今日冻结试卷')).toBeInTheDocument();
    expect(generateWorkshopPaperWithAgents).toHaveBeenCalledWith(expect.objectContaining({
      taskItemId: 'ITEM_PAPER',
      distribution: {},
    }));
    expect(screen.queryByLabelText('训练主题')).not.toBeInTheDocument();
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '生成试卷' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '退出并保存' })).not.toBeInTheDocument();
  });

  it('shows the paper notices once on entry instead of pushing the first question down', async () => {
    const paper = {
      paper_id: 'PAPER_NOTICE',
      title: '带说明的试卷',
      status: 'published',
      timing: { remaining_seconds: 600 },
      learner_notices: {
        题目来源说明: '本次可用的单元内题目不足：本卷有5道题来自其他知识点。',
        审核说明: '内容审核对本次试卷提出了以下问题。\n· 题干表述不清',
      },
      items: [{ paper_item_id: 'I1', position: 1, question_type: 'single_choice', stem: '单选', options: ['A', 'B'], answer: '' }],
    };
    loadPaper.mockResolvedValue({ paper, error: '' });

    render(<PaperGenerationPanel enabled paperId="PAPER_NOTICE" />);

    const dialog = await screen.findByRole('dialog', { name: '开始答题前请阅读' });
    expect(within(dialog).getByText('题目来源说明')).toBeInTheDocument();
    expect(within(dialog).getByText('本次可用的单元内题目不足：本卷有5道题来自其他知识点。')).toBeInTheDocument();
    expect(within(dialog).getByText('审核说明')).toBeInTheDocument();
    expect(within(dialog).getByText(/内容审核对本次试卷提出了以下问题。/)).toBeInTheDocument();
    // 说明不再占用答题区版面：题目前面没有说明区块，首屏就是第1题。
    expect(screen.queryByRole('region', { name: '试卷说明' })).not.toBeInTheDocument();
    expect(screen.getByText('单选')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: '我已了解，开始答题' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('reopens the paper notices from the header button after the first entry', async () => {
    const paper = {
      paper_id: 'PAPER_REOPEN',
      title: '可复看的试卷',
      status: 'published',
      timing: { remaining_seconds: 600 },
      learner_notices: { 审核说明: '内容审核对本次试卷提出了以下问题。\n· 题干表述不清' },
      items: [{ paper_item_id: 'I1', position: 1, question_type: 'single_choice', stem: '单选', options: ['A', 'B'], answer: '' }],
    };
    loadPaper.mockResolvedValue({ paper, error: '' });

    render(<PaperGenerationPanel enabled paperId="PAPER_REOPEN" />);

    const dialog = await screen.findByRole('dialog', { name: '开始答题前请阅读' });
    fireEvent.click(within(dialog).getByRole('button', { name: '关闭试卷说明' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '试卷说明' }));
    expect(await screen.findByRole('dialog', { name: '开始答题前请阅读' })).toBeInTheDocument();
  });

  it('keeps the header button but does not pop up again for an already read paper', async () => {
    window.localStorage.setItem('smart-paper-notice-seen:PAPER_READ', '1');
    const paper = {
      paper_id: 'PAPER_READ',
      title: '已读说明的试卷',
      status: 'published',
      timing: { remaining_seconds: 600 },
      learner_notices: { 审核说明: '内容审核对本次试卷提出了以下问题。\n· 题干表述不清' },
      items: [{ paper_item_id: 'I1', position: 1, question_type: 'single_choice', stem: '单选', options: ['A', 'B'], answer: '' }],
    };
    loadPaper.mockResolvedValue({ paper, error: '' });

    render(<PaperGenerationPanel enabled paperId="PAPER_READ" />);

    expect(await screen.findByText('已读说明的试卷')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: '试卷说明' })).toBeInTheDocument());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('omits the notices dialog and its header button when the paper carries none', async () => {
    const paper = {
      paper_id: 'PAPER_PLAIN',
      title: '无说明试卷',
      status: 'published',
      timing: { remaining_seconds: 600 },
      learner_notices: {},
      items: [{ paper_item_id: 'I1', position: 1, question_type: 'single_choice', stem: '单选', options: ['A', 'B'], answer: '' }],
    };
    loadPaper.mockResolvedValue({ paper, error: '' });

    render(<PaperGenerationPanel enabled paperId="PAPER_PLAIN" />);

    expect(await screen.findByText('无说明试卷')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '试卷说明' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '试卷说明' })).not.toBeInTheDocument();
  });
});

import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import KnowledgeCardLibrary from './KnowledgeCardLibrary';
import { loadKnowledgeCard, loadKnowledgeCards } from '../pageDataLoaders';

vi.mock('../pageDataLoaders', () => ({
  loadKnowledgeCard: vi.fn(),
  loadKnowledgeCards: vi.fn(),
  resolveKnowledgeCard: vi.fn(),
}));

vi.mock('../utils/api', () => ({
  fetchJsonWithAuthFallback: vi.fn(),
}));

describe('KnowledgeCardLibrary', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('explains how the library is populated when no learned cards exist', async () => {
    loadKnowledgeCards.mockResolvedValue({ cards: { items: [] }, error: '' });

    render(<KnowledgeCardLibrary />);

    expect(await screen.findByRole('heading', { name: '还没有知识卡' })).toBeInTheDocument();
    expect(screen.getByText(/完成知识讲解和配套题目后/)).toBeInTheDocument();
  });

  it('renders the complete resource bundle and filters learned cards', async () => {
    loadKnowledgeCards.mockResolvedValue({
      cards: { items: [
        { card_id: 'KC_1', title: '四君子汤' },
        { card_id: 'KC_2', title: '理中丸' },
      ] },
      error: '',
    });
    loadKnowledgeCard.mockResolvedValue({
      card: {
        card_id: 'KC_1',
        title: '四君子汤',
        kp_id: 'KP_FORMULA_1',
        resource_bundle: {
          knowledge_point: { title: '四君子汤', description: '益气健脾基础方。' },
          explanation: { content: JSON.stringify({ 知识讲解: '由人参、白术、茯苓、炙甘草组成。', 配套练习: [{ 题目: '组成是什么？' }] }) },
          textbook_slices: [{ chunk_uid: 'C1', retrieval_text: '主治脾胃气虚证。' }],
          videos: [{ source_id: 'V1', title: '配伍讲解', url: 'https://example.test/video' }],
          questions: [{ question_id: 'Q1', question_type: '填空题', stem: '四君子汤由哪些药物组成？' }],
          coverage: { fallback_used: ['video'] },
        },
      },
      error: '',
    });

    render(<KnowledgeCardLibrary />);
    fireEvent.click(await screen.findByRole('button', { name: '四君子汤' }));

    expect(await screen.findByRole('heading', { name: '四君子汤', level: 3 })).toBeInTheDocument();
    expect(screen.getByText('益气健脾基础方。')).toBeInTheDocument();
    expect(screen.getByText('由人参、白术、茯苓、炙甘草组成。')).toBeInTheDocument();
    expect(screen.queryByText(/"知识讲解"/)).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '教材切片 1' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '视频资源 1' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '配套题目 1' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '教材切片 1' }));
    expect(screen.getByRole('heading', { name: '教材切片 1' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '视频资源 1' }));
    expect(screen.getByRole('heading', { name: '视频资源 1' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '配套题目 1' }));
    expect(screen.getByRole('heading', { name: '配套题目 1' })).toBeInTheDocument();
    expect(screen.getByText('含网络补充资源')).toBeInTheDocument();

    fireEvent.change(screen.getByRole('searchbox', { name: '筛选知识卡' }), { target: { value: '理中丸' } });
    expect(screen.queryByRole('button', { name: '四君子汤' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '理中丸' })).toBeInTheDocument();
  });
});

import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TextbookChapterLearning from './TextbookChapterLearning';
import { loadAtlasNodes } from '../knowledge-atlas/knowledgeAtlasApi';
import { loadSectionLearningDetail } from './textbookChapterApi';

vi.mock('../knowledge-atlas/knowledgeAtlasApi', () => ({ loadAtlasNodes: vi.fn() }));
vi.mock('./textbookChapterApi', () => ({ loadSectionLearningDetail: vi.fn() }));

const chapter = { id: 'CH_1', name: '第一章 绪论', children_count: 2 };
const section = { id: 'SEC_1', name: '第一节 基础概念', count: 2 };
const baseVideo = { bvid: 'BV_BASE', page: 1, start_seconds: 0, video_title: '小节完整视频' };
const timestampVideo = { bvid: 'BV_KP', page: 1, start_seconds: 18, end_seconds: 48, video_title: '知识点视频' };

function prepare({ withSectionVideo = true } = {}) {
  loadAtlasNodes.mockImplementation(({ level }) => Promise.resolve({ nodes: level === 2 ? [chapter] : [section] }));
  loadSectionLearningDetail.mockResolvedValue({
    section: { name: section.name, book: '中医学基础', chapter: chapter.name },
    section_videos: withSectionVideo ? [baseVideo] : [],
    recommended_videos: withSectionVideo ? [] : [{ ...baseVideo, bvid: 'BV_RECOMMENDED', topic: '推荐内容' }],
    knowledge_points: [
      { kp_id: 'KP_1', name: '阴阳概念', timestamp_video: timestampVideo },
      { kp_id: 'KP_2', name: '五行关系', timestamp_video: null },
    ],
  });
}

describe('TextbookChapterLearning', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the textbook introduction and equal chapter/section catalogues', async () => {
    prepare();
    render(<TextbookChapterLearning navigationContext={{ route: 'textbook_14_5', lv1: '中医学基础' }} />);

    expect(screen.getByRole('img', { name: '《中医学基础》教材封面' })).toHaveAttribute('src', '/textbook-covers/%E4%B8%AD%E5%8C%BB%E5%AD%A6%E5%9F%BA%E7%A1%80.jpg');
    expect(screen.getByText(/系统讲解阴阳五行/)).toBeInTheDocument();
    const chapterButton = await screen.findByRole('button', { name: /第一章 绪论/ });
    expect(document.querySelector('.textbook-catalog-stage')).not.toHaveClass('has-chapter');
    fireEvent.click(chapterButton);
    expect(await screen.findByRole('button', { name: /第一节 基础概念/ })).toBeInTheDocument();
    expect(document.querySelector('.textbook-catalog-stage')).toHaveClass('has-chapter');
  });

  it('sorts chapters and sections by the numbers written in their titles', async () => {
    loadAtlasNodes.mockImplementation(({ level }) => Promise.resolve({
      nodes: level === 2
        ? [
          { id: 'CH_3', name: '第三章 三', children_count: 1 },
          { id: 'CH_1', name: '第一章 一', children_count: 4 },
          { id: 'CH_2', name: '第二章 二', children_count: 1 },
        ]
        : [
          { id: 'SEC_3', name: '第三节 三', count: 1 },
          { id: 'SEC_4', name: '第四节 四', count: 1 },
          { id: 'SEC_1', name: '第一节 一', count: 1 },
          { id: 'SEC_2', name: '第二节 二', count: 1 },
        ],
    }));
    render(<TextbookChapterLearning navigationContext={{ lv1: '中医文献学' }} />);

    await screen.findByRole('button', { name: /第一章 一/ });
    expect(
      [...document.querySelectorAll('.textbook-directory--chapters strong')]
        .map((node) => node.textContent),
    ).toEqual(['第一章 一', '第二章 二', '第三章 三']);

    fireEvent.click(screen.getByRole('button', { name: /第一章 一/ }));
    await screen.findByRole('button', { name: /第一节 一/ });
    expect(
      [...document.querySelectorAll('.textbook-directory--sections strong')]
        .map((node) => node.textContent),
    ).toEqual(['第一节 一', '第二节 二', '第三节 三', '第四节 四']);
  });

  it('replaces the section video with timestamp videos and supports video history back', async () => {
    prepare();
    render(<TextbookChapterLearning navigationContext={{ lv1: '中医学基础' }} />);

    fireEvent.click(await screen.findByRole('button', { name: /第一章 绪论/ }));
    fireEvent.click(await screen.findByRole('button', { name: /第一节 基础概念/ }));
    const knowledgePoint = await screen.findByRole('button', { name: /阴阳概念/ });
    expect(screen.queryByRole('heading', { name: '章节' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '小节' })).not.toBeInTheDocument();
    const back = await screen.findByRole('button', { name: /返回上一个视频/ });
    expect(back).toBeDisabled();

    fireEvent.click(knowledgePoint);
    await waitFor(() => expect(back).not.toBeDisabled());
    expect(screen.queryByTitle('小节完整视频：第一节 基础概念')).not.toBeInTheDocument();
    expect(screen.getByTitle('阴阳概念')).toBeInTheDocument();

    fireEvent.click(back);
    await waitFor(() => expect(back).toBeDisabled());
    expect(screen.queryByTitle('阴阳概念')).not.toBeInTheDocument();
    expect(screen.getByTitle('小节完整视频：第一节 基础概念')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /返回小节目录/ }));
    expect(screen.getByRole('heading', { name: '章节' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '小节' })).toBeInTheDocument();
  });

  it('replaces recommendation when a timestamp video is selected', async () => {
    prepare({ withSectionVideo: false });
    render(<TextbookChapterLearning navigationContext={{ lv1: '中医学基础' }} />);

    fireEvent.click(await screen.findByRole('button', { name: /第一章 绪论/ }));
    fireEvent.click(await screen.findByRole('button', { name: /第一节 基础概念/ }));
    expect(await screen.findByTitle('推荐内容')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /阴阳概念/ }));
    await waitFor(() => expect(screen.queryByTitle('推荐内容')).not.toBeInTheDocument());
    expect(screen.getByTitle('阴阳概念')).toBeInTheDocument();
  });
});

import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TextbookChapterLearning from './TextbookChapterLearning';
import { loadAtlasNodes } from '../knowledge-atlas/knowledgeAtlasApi';
import { completeTextbookSection, loadSectionLearningDetail, loadSectionQuestions, loadTextbookProgress } from './textbookChapterApi';
import { clearTextbookCache } from './textbookCache';

vi.mock('../knowledge-atlas/knowledgeAtlasApi', () => ({ loadAtlasNodes: vi.fn() }));
vi.mock('./TextbookPdfReader', () => ({
  default: ({ onClose, notesOpen }) => <div><span>电子教材阅读器</span>{notesOpen && <span>简约页笔记</span>}<button type="button" onClick={onClose}>返回教材目录</button></div>,
}));
vi.mock('./textbookChapterApi', () => ({
  completeTextbookSection: vi.fn().mockResolvedValue({ ok: true }),
  loadSectionLearningDetail: vi.fn(),
  loadSectionQuestions: vi.fn().mockResolvedValue({ items: [] }),
  loadTextbookProgress: vi.fn().mockResolvedValue({ completed_section_ids: [], last_section_id: '' }),
}));

const chapter = { id: 'CH_1', name: '第一章 绪论', children_count: 2 };
const section = { id: 'SEC_1', name: '第一节 基础概念', count: 2 };
const baseVideo = { bvid: 'BV_BASE', page: 1, start_seconds: 0, video_title: '小节完整视频' };
const timestampVideo = { bvid: 'BV_KP', page: 1, start_seconds: 18, end_seconds: 48, video_title: '知识点视频' };

function prepare({ withSectionVideo = true } = {}) {
  loadAtlasNodes.mockImplementation(({ level }) => Promise.resolve({
    nodes: level === 2 ? [chapter] : level === 3 ? [section] : [{ id: 'KP_1' }, { id: 'KP_2' }],
  }));
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

async function openCatalog() {
  fireEvent.click(await screen.findByRole('button', { name: '课程内容' }));
}

describe('TextbookChapterLearning', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearTextbookCache();
    loadSectionQuestions.mockResolvedValue({ items: [] });
    loadTextbookProgress.mockResolvedValue({ completed_section_ids: [], last_section_id: '' });
    completeTextbookSection.mockResolvedValue({ ok: true });
  });

  it('opens the e-textbook PDF reader by default with separate navigation buttons', async () => {
    prepare();
    render(<TextbookChapterLearning navigationContext={{ route: 'textbook_14_5', lv1: '中医学基础' }} />);

    expect(await screen.findByText('电子教材阅读器')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '电子教材' })).toHaveClass('is-active');
    expect(screen.getByRole('button', { name: '课程内容' })).not.toHaveClass('is-active');

    fireEvent.click(screen.getByRole('button', { name: '课程内容' }));
    expect(await screen.findByRole('button', { name: /第一章 绪论/ })).toBeInTheDocument();
    expect(screen.queryByText('电子教材阅读器')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '电子教材' }));
    expect(await screen.findByText('电子教材阅读器')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /第一章 绪论/ })).not.toBeInTheDocument();
  });

  it('returns to the textbook library when the PDF back button is clicked', async () => {
    prepare();
    const onNavigate = vi.fn();
    render(<TextbookChapterLearning navigationContext={{ lv1: '中医学基础' }} onNavigate={onNavigate} />);
    expect(await screen.findByText('电子教材阅读器')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '返回教材目录' }));

    expect(onNavigate).toHaveBeenCalledWith({ page: 'practice', params: {} });
  });

  it('switches to the section content when starting learning from the PDF', async () => {
    prepare();
    render(<TextbookChapterLearning navigationContext={{ lv1: '中医学基础' }} />);
    expect(await screen.findByText('电子教材阅读器')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /开始学习/ }));
    expect(await screen.findByRole('button', { name: /阴阳概念/ })).toBeInTheDocument();
    expect(screen.queryByText('电子教材阅读器')).not.toBeInTheDocument();
  });

  it('shows the textbook introduction and equal chapter/section catalogues', async () => {
    prepare();
    render(<TextbookChapterLearning navigationContext={{ route: 'textbook_14_5', lv1: '中医学基础' }} />);
    await openCatalog();

    expect(screen.getByRole('img', { name: '《中医学基础》教材封面' })).toHaveAttribute('src', '/textbook-covers/%E4%B8%AD%E5%8C%BB%E5%AD%A6%E5%9F%BA%E7%A1%80.jpg');
    expect(screen.getByText(/系统讲解阴阳五行/)).toBeInTheDocument();
    const chapterButton = await screen.findByRole('button', { name: /第一章 绪论/ });
    expect(await screen.findByRole('button', { name: /第一节 基础概念/ })).toBeInTheDocument();
    expect(document.querySelector('.textbook-catalog-stage')).toHaveClass('has-chapter');
    fireEvent.click(chapterButton);
    expect(document.querySelector('.textbook-catalog-stage')).not.toHaveClass('has-chapter');
    fireEvent.click(chapterButton);
    expect(await screen.findByRole('button', { name: /第一节 基础概念/ })).toBeInTheDocument();
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
    await openCatalog();

    await screen.findByRole('button', { name: /第一章 一/ });
    expect(
      [...document.querySelectorAll('.textbook-directory--chapters > .textbook-directory__items > button > span > strong')]
        .map((node) => node.textContent),
    ).toEqual(['第一章 一', '第二章 二', '第三章 三']);

    await screen.findByRole('button', { name: /第一节 一/ });
    expect(
      [...document.querySelectorAll('.textbook-directory--sections > .textbook-directory__items > button strong')]
        .map((node) => node.textContent),
    ).toEqual(['第一节 一', '第二节 二', '第三节 三', '第四节 四']);
  });

  it('replaces the section video with timestamp videos and supports video history back', async () => {
    prepare();
    render(<TextbookChapterLearning navigationContext={{ lv1: '中医学基础' }} />);
    await openCatalog();

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
    expect(screen.getByRole('button', { name: /第一节 基础概念/ })).toBeInTheDocument();
  });

  it('replaces recommendation when a timestamp video is selected', async () => {
    prepare({ withSectionVideo: false });
    render(<TextbookChapterLearning navigationContext={{ lv1: '中医学基础' }} />);
    await openCatalog();

    fireEvent.click(await screen.findByRole('button', { name: /第一节 基础概念/ }));
    expect(await screen.findByTitle('推荐内容')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /阴阳概念/ }));
    await waitFor(() => expect(screen.queryByTitle('推荐内容')).not.toBeInTheDocument());
    expect(screen.getByTitle('阴阳概念')).toBeInTheDocument();
  });

  it('shows question counts beside sections in the assignment catalogue', async () => {
    loadAtlasNodes.mockImplementation(({ level }) => Promise.resolve({
      nodes: level === 2
        ? [chapter]
        : level === 3
          ? [section]
          : [{ id: 'KP_1' }, { id: 'KP_2' }],
    }));
    loadSectionQuestions.mockResolvedValue({
      items: [
        { question_id: 'Q_1', kp_ids: ['KP_1'] },
        { question_id: 'Q_2', kp_ids: ['KP_2'] },
      ],
    });
    render(<TextbookChapterLearning navigationContext={{ lv1: '中医学基础' }} />);

    fireEvent.click(await screen.findByRole('button', { name: '作业与考试' }));

    expect(await screen.findByText('2 道题目')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /第一节 基础概念/ }));
    await waitFor(() => expect(loadSectionQuestions).toHaveBeenCalledWith(['KP_1', 'KP_2'], expect.any(Object)));
  });

  it('keeps the notebook and PDF reader inside the textbook page', async () => {
    const onNavigate = vi.fn();
    prepare();
    render(
      <TextbookChapterLearning
        navigationContext={{ route: 'textbook_14_5', lv1: '中医学基础', view: 'textbook-chapters' }}
        onNavigate={onNavigate}
      />,
    );
    expect(await screen.findByText('电子教材阅读器')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '作业与考试' }));
    expect(onNavigate).not.toHaveBeenCalled();
    expect(await screen.findByRole('button', { name: /第一节 基础概念/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /第一节 基础概念/ }));

    await waitFor(() => expect(loadSectionQuestions).toHaveBeenCalledWith(['KP_1', 'KP_2'], expect.any(Object)));
    expect(screen.getByText('该小节知识点暂未匹配到题目，题库补充后会在此展示。')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '课程内容' }));
    expect((await screen.findAllByText('小节完整视频')).length).toBeGreaterThan(0);
  });

  it('opens the embedded Tree-KG graph from chapter navigation', async () => {
    prepare();
    render(<TextbookChapterLearning navigationContext={{ lv1: '中医学基础' }} />);
    await screen.findByText('电子教材阅读器');

    fireEvent.click(screen.getByRole('button', { name: '知识图谱' }));

    expect(screen.getByTitle('中医学基础知识图谱')).toHaveAttribute(
      'src',
      '/treekg/?book=%E4%B8%AD%E5%8C%BB%E5%AD%A6%E5%9F%BA%E7%A1%80',
    );
    expect(screen.getByRole('region', { name: '中医知识图谱' })).toHaveClass('textbook-knowledge-graph');
  });

  it('filters partially completed chapters by section progress', async () => {
    const testSections = [
      { id: 'SEC_1', name: '第一节 一', count: 1 },
      { id: 'SEC_2', name: '第二节 二', count: 1 },
      { id: 'SEC_3', name: '第三节 三', count: 1 },
      { id: 'SEC_4', name: '第四节 四', count: 1 },
    ];
    loadAtlasNodes.mockImplementation(({ level }) => Promise.resolve({
      nodes: level === 2 ? [{ ...chapter, status: 'pending' }] : testSections,
    }));
    loadTextbookProgress.mockResolvedValue({ completed_section_ids: ['SEC_1', 'SEC_2'], last_section_id: 'SEC_2' });

    render(<TextbookChapterLearning navigationContext={{ lv1: '中医学基础' }} />);
    await openCatalog();
    await screen.findByRole('button', { name: /第一章 绪论/ });
    fireEvent.click(screen.getByRole('button', { name: '已完成' }));

    expect(screen.getByRole('button', { name: /第一章 绪论/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /第一节 一/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /第二节 二/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /第三节 三/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /第四节 四/ })).not.toBeInTheDocument();
  });
  it('filters the chapter catalogue by real learning status', async () => {
    loadAtlasNodes.mockImplementation(({ level }) => Promise.resolve({
      nodes: level === 2 ? [
        { id: 'CH_PENDING', name: '第一章 未完成', status: 'pending', children_count: 1 },
        { id: 'CH_CURRENT', name: '第二章 学习中', status: 'in_progress', children_count: 1 },
        { id: 'CH_DONE', name: '第三章 已完成', status: 'completed', children_count: 1 },
      ] : [section],
    }));
    render(<TextbookChapterLearning navigationContext={{ lv1: '中医学基础' }} />);
    await openCatalog();
    await screen.findByRole('button', { name: /第一章 未完成/ });

    fireEvent.click(screen.getByRole('button', { name: '已完成' }));
    expect(screen.getByRole('button', { name: /第三章 已完成/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /第一章 未完成/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '未完成' }));
    expect(screen.getByRole('button', { name: /第二章 学习中/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /第三章 已完成/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '全部' }));
    expect(screen.getAllByRole('button', { name: /第[一二三]章/ })).toHaveLength(3);
  });
});

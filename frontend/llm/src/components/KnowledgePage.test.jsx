import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import KnowledgePage from './KnowledgePage';
import { fetchWithAuth } from '../utils/api';

vi.mock('../utils/api', () => ({
  API_BASE: '',
  fetchWithAuth: vi.fn(),
  fetchJsonWithAuthFallback: vi.fn(async () => ({ data: null })),
}));

vi.mock('./QuestionWorkspacePage', () => ({
  default: () => <div>题目内容已并入知识库</div>,
}));

vi.mock('./CompactAssistant', () => ({
  default: () => <aside aria-label="知识资料智能助教">资料助教</aside>,
}));

vi.mock('./knowledge-atlas/KnowledgeAtlas', () => ({
  default: ({ initialContext, workspaceNavigation }) => (
    <section data-testid="knowledge-atlas">
      <header aria-label="知识星球顶栏">{workspaceNavigation}</header>
      知识星球：{initialContext.trackId || 'default'}
    </section>
  ),
  KnowledgeAtlasErrorBoundary: ({ children }) => children,
}));

describe('KnowledgePage workspace navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchWithAuth.mockImplementation(async (url) => ({
      ok: true,
      json: async () => url.endsWith('/knowledge/catalog')
        ? {
          documents: [{ id: 'doc-1', name: '病理学2.md', available: true }],
          datasets: [{ id: 'atlas_question_bank', name: 'Atlas 题库', count: 93111, available: true }],
          indexes: [{ id: 'question-v2', name: '题库-v2', count: 93111, available: true, loaded: true }],
          embedding: { state: 'ready', model_id: 'Qwen/Qwen3-Embedding-4B' },
        }
        : url.includes('/knowledge/status')
          ? { total_documents: 0, total_chunks: 0, status: '就绪', progress: 0, is_processing: false }
          : { files: [] },
    }));
  });

  it('opens the teammate Atlas by default and keeps it as the primary workspace', async () => {
    render(
      <KnowledgePage
        onBackHome={vi.fn()}
        currentUser={{ username: 'alice', role: 'user' }}
        navigationContext={{ trackId: 'track-a', membershipId: 'node-a' }}
      />,
    );

    expect(await screen.findByTestId('knowledge-atlas')).toHaveTextContent('track-a');
    expect(screen.getByRole('button', { name: '知识星球' })).toHaveClass('is-active');
    expect(screen.getByRole('button', { name: '知识资料与个性化数据' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '题目数据' })).toBeInTheDocument();
    expect(within(screen.getByRole('banner', { name: '知识星球顶栏' })).getByRole('navigation', { name: '知识库内容' })).toBeInTheDocument();
  });

  it('hosts source, personalized data, and question data in one knowledge workspace', async () => {
    render(
      <KnowledgePage
        onBackHome={vi.fn()}
        currentUser={{ username: 'alice', role: 'user' }}
        navigationContext={{ view: 'questions' }}
      />,
    );

    expect(screen.getByRole('navigation', { name: '知识库内容' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '知识资料与个性化数据' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '题目数据' })).toBeInTheDocument();
    expect(screen.getByText('题目内容已并入知识库')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '向量数据库状态' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '检索测试' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '知识资料与个性化数据' }));
    expect(screen.queryByText('题目内容已并入知识库')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '公共库' }));
    expect(await screen.findByRole('heading', { name: '文档、数据集与索引' })).toBeInTheDocument();
    expect(screen.getByText('Atlas 题库')).toBeInTheDocument();
    expect(screen.getByText('题库-v2')).toBeInTheDocument();
  });

  it('organizes sources as collection, search-and-reader, and contextual assistant columns', async () => {
    render(
      <KnowledgePage
        currentUser={{ username: 'alice', role: 'user' }}
        navigationContext={{ view: 'public' }}
      />,
    );

    const workbench = await screen.findByRole('region', { name: '知识资料工作台' });
    expect(within(workbench).getByRole('complementary', { name: '资料集合' })).toBeInTheDocument();
    expect(within(workbench).getByRole('main', { name: '资料检索与阅读' })).toBeInTheDocument();
    expect(within(workbench).getByRole('complementary', { name: '知识资料智能助教' })).toBeInTheDocument();
    expect(within(workbench).getByRole('heading', { name: '资料检索' })).toBeInTheDocument();
    expect(within(workbench).getByText('索引与导入状态')).toBeInTheDocument();
  });

  it('isolates status and file failures from the catalog and other workspaces', async () => {
    fetchWithAuth.mockImplementation(async (url) => {
      if (url.includes('/knowledge/status')) {
        return { ok: false, json: async () => ({ detail: '向量状态接口不可用' }) };
      }
      if (url.includes('/knowledge/files')) throw new Error('文件列表接口已断开');
      return {
        ok: true,
        json: async () => ({
          documents: [{ id: 'doc-1', name: '病理学2.md', available: true }],
          datasets: [{ id: 'atlas_question_bank', name: 'Atlas 题库', count: 93111, available: true }],
          indexes: [{ id: 'question-v2', name: '题库-v2', count: 93111, available: true, loaded: true }],
          embedding: { state: 'ready', model_id: 'Qwen/Qwen3-Embedding-4B' },
        }),
      };
    });

    render(
      <KnowledgePage
        currentUser={{ username: 'alice', role: 'user' }}
        navigationContext={{ view: 'public' }}
      />,
    );

    expect(await screen.findByText('向量状态接口不可用')).toBeInTheDocument();
    expect(await screen.findByText('文件列表接口已断开')).toBeInTheDocument();
    expect(await screen.findByText('Atlas 题库')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '题目数据' })).toBeInTheDocument();
  });

  it('renders processing progress with a composited scale transform', async () => {
    fetchWithAuth.mockImplementation(async (url) => ({
      ok: true,
      json: async () => url.includes('/knowledge/status')
        ? { total_documents: 2, total_chunks: 4, status: '构建中', progress: 50, is_processing: true }
        : url.endsWith('/knowledge/catalog')
          ? { documents: [], datasets: [], indexes: [], embedding: null }
          : { files: [] },
    }));

    render(
      <KnowledgePage
        currentUser={{ username: 'alice', role: 'user' }}
        navigationContext={{ view: 'personal' }}
      />,
    );

    const progressbar = await screen.findByRole('progressbar');
    expect(progressbar.firstElementChild).toHaveStyle({ transform: 'scaleX(0.5)' });
  });
});

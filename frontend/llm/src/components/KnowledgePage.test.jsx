import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import KnowledgePage from './KnowledgePage';
import { fetchWithAuth } from '../utils/api';

vi.mock('../utils/api', () => ({
  API_BASE: '',
  MAIN_API_BASE: '/api/v1',
  fetchWithAuth: vi.fn(),
  fetchJsonWithAuthFallback: vi.fn(async () => ({ data: null })),
}));

vi.mock('./QuestionWorkspacePage', () => ({
  default: () => <div>题目内容已并入知识库</div>,
}));

describe('KnowledgePage workspace navigation', () => {
  it('opens the shared upload entry with personal knowledge preselected', async () => {
    fetchWithAuth.mockResolvedValue({ ok: true, json: async () => ({ files: [], items: [], stats: {} }) });
    const onNavigate = vi.fn();
    render(<KnowledgePage currentUser={{ role: 'student' }} onNavigate={onNavigate} />);
    await screen.findByText('暂无个人知识文件');
    fireEvent.click(screen.getByRole('button', { name: '上传个人文档' }));
    expect(onNavigate).toHaveBeenCalledWith({ page: 'personalization', params: { view: 'resources', uploadType: 'knowledge' } });
    expect(fetchWithAuth.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true);
  });

  it('deletes committed personal documents through the authenticated new API', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    let deleted = false;
    fetchWithAuth.mockImplementation(async (url, options = {}) => {
      if (options.method === 'DELETE') deleted = true;
      return { ok: true, json: async () => url.endsWith('/content/library')
        ? { files: deleted ? [] : [{ id: 'doc-delete', name: '删除测试资料', scope: 'personal', can_delete: true }], stats: { total_documents: deleted ? 0 : 1 } }
        : { files: [], items: [], ok: true } };
    });
    render(<KnowledgePage currentUser={{ role: 'student' }} />);
    fireEvent.click(await screen.findByTitle('删除删除测试资料及向量数据'));
    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalledWith('/api/v1/knowledge/content/library/doc-delete', { method: 'DELETE' }));
    expect(await screen.findByText('个人有效资料及其索引已更新；历史记录和恢复备份保留。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '删除测试资料' })).not.toBeInTheDocument();
    confirm.mockRestore();
  });

  it('rebuilds only personal indexes and honors confirmation cancellation', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fetchWithAuth.mockImplementation(async () => ({ ok: true, json: async () => ({ files: [], items: [], stats: {} }) }));
    render(<KnowledgePage currentUser={{ role: 'student' }} />);
    await screen.findByRole('button', { name: '重建个人库' });
    fireEvent.click(screen.getByRole('button', { name: '重建个人库' }));
    expect(fetchWithAuth.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: '重建个人库' }));
    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalledWith('/api/v1/knowledge/content/library/rebuild', { method: 'POST' }));
    expect(await screen.findByText('个人向量索引已重建并校验。')).toBeInTheDocument();
    confirm.mockRestore();
  });
  it('shows committed personal documents and reads original chunks without legacy mutations', async () => {
    fetchWithAuth.mockImplementation(async url => ({
      ok: true,
      json: async () => url.endsWith('/content/library')
        ? { files: [{ id: 'doc1', name: '已入库阴阳资料', scope: 'personal', chunk_count: 4 }], stats: { total_documents: 1, total_chunks: 4, total_knowledge_points: 7, total_vectors: 7, status: '已建立个人索引' } }
        : url.endsWith('/content/library/doc1')
          ? { name: '已入库阴阳资料', chunks: [{ id: 'c1', title: '基本概念', text: '<script>阴阳原文</script>' }] }
          : { files: [], items: [] },
    }));
    render(<KnowledgePage currentUser={{ role: 'admin' }} />);
    fireEvent.click(await screen.findByRole('button', { name: '已入库阴阳资料' }));
    expect(await screen.findByText('<script>阴阳原文</script>')).toBeInTheDocument();
    expect(screen.getByText('7 个知识点 · 7 条向量')).toBeInTheDocument();
    expect(screen.getByText('已建立个人索引')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重建个人库' })).toBeInTheDocument();
    expect(fetchWithAuth.mock.calls.some(([url]) => url.includes('/knowledge/status?scope=personal'))).toBe(false);
  });
  it('reuses the textbook tool without calling the legacy search endpoint', async () => {
    fetchWithAuth.mockImplementation(async url => ({
      ok: true,
      json: async () => url.endsWith('/content/search')
        ? { evidence_items: [{ evidence_id: 'E_1', source_id: 'chunk-1', source_label: '中医学基础', content_summary: '阴阳教材原文', confidence: 0.9 }] }
        : { files: [], documents: [], datasets: [], indexes: [] },
    }));
    render(<KnowledgePage currentUser={{ role: 'admin' }} />);
    fireEvent.change(screen.getByPlaceholderText('输入知识主题，检索教材资料...'), { target: { value: '阴阳' } });
    fireEvent.click(screen.getByRole('button', { name: '测试' }));
    expect(await screen.findByText('阴阳教材原文')).toBeInTheDocument();
    expect(screen.getByText('90.0% 工具置信度')).toBeInTheDocument();
    expect(fetchWithAuth).toHaveBeenCalledWith('/api/v1/knowledge/content/search', expect.objectContaining({ body: JSON.stringify({ query: '阴阳', limit: 5 }) }));
    expect(fetchWithAuth.mock.calls.some(([url]) => url.includes('search_test'))).toBe(false);
  });

  it('switches explicitly to questions and exposes vector degradation', async () => {
    fetchWithAuth.mockImplementation(async url => ({
      ok: true,
      json: async () => url.endsWith('/questions/search')
        ? { vector_degraded: true, items: [{ question_id: 'Q_1', stem: '阴阳题干', reference_answer: '答案甲', retrieval: { channels: ['bridge', 'bm25'], channel_scores: {} } }] }
        : { files: [], documents: [], datasets: [], indexes: [] },
    }));
    render(<KnowledgePage currentUser={{ role: 'admin' }} />);
    fireEvent.change(screen.getByLabelText('检索类型'), { target: { value: 'questions' } });
    fireEvent.change(screen.getByPlaceholderText('输入题干或知识主题，检索题目...'), { target: { value: '阴阳' } });
    fireEvent.click(screen.getByRole('button', { name: '测试' }));
    expect(await screen.findByText(/不代表向量检索成功/)).toBeInTheDocument();
    expect(screen.getByText(/命中通道：知识点关联 · BM25/)).toBeInTheDocument();
    expect(screen.queryByText(/% 向量通道分/)).not.toBeInTheDocument();
    expect(fetchWithAuth).toHaveBeenCalledWith('/api/v1/knowledge/questions/search', expect.objectContaining({ body: JSON.stringify({ query: '阴阳', limit: 5, scope: 'all' }) }));
    fireEvent.change(screen.getByLabelText('检索类型'), { target: { value: 'content' } });
    expect(screen.queryByText(/不代表向量检索成功/)).not.toBeInTheDocument();
    expect(screen.queryByText(/阴阳题干/)).not.toBeInTheDocument();
  });

  it('renders structured search errors instead of an object string', async () => {
    fetchWithAuth.mockImplementation(async url => ({
      ok: !url.endsWith('/content/search'),
      json: async () => url.endsWith('/content/search') ? { detail: { message: '教材工具暂不可用' } } : { files: [], documents: [], datasets: [], indexes: [] },
    }));
    render(<KnowledgePage currentUser={{ role: 'admin' }} />);
    fireEvent.change(screen.getByPlaceholderText('输入知识主题，检索教材资料...'), { target: { value: '阴阳' } });
    fireEvent.click(screen.getByRole('button', { name: '测试' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('教材工具暂不可用'));
  });

  beforeEach(() => {
    vi.clearAllMocks();
    fetchWithAuth.mockImplementation(async (url) => ({
      ok: true,
      json: async () => url.includes('/knowledge/content/recognition-reports')
        ? { items: [] }
        : url.endsWith('/knowledge/catalog')
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

  it('opens the sources workspace by default and keeps it as the primary workspace', async () => {
    render(
      <KnowledgePage
        onBackHome={vi.fn()}
        currentUser={{ username: 'alice', role: 'user' }}
        navigationContext={{ trackId: 'track-a', membershipId: 'node-a' }}
      />,
    );

    expect(screen.getByRole('button', { name: '知识资料与个性化数据' })).toHaveClass('is-active');
    expect(screen.getByRole('button', { name: '题目数据' })).toBeInTheDocument();
    expect(within(screen.getByRole('navigation', { name: '知识库内容' }))).toBeTruthy();
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

  it('keeps source collections and reading in a focused workspace without an embedded assistant column', async () => {
    render(
      <KnowledgePage
        currentUser={{ username: 'alice', role: 'user' }}
        navigationContext={{ view: 'public' }}
      />,
    );

    const workbench = await screen.findByRole('region', { name: '知识资料工作台' });
    expect(within(workbench).getByRole('complementary', { name: '资料集合' })).toBeInTheDocument();
    expect(within(workbench).getByRole('main', { name: '资料检索与阅读' })).toBeInTheDocument();
    expect(within(workbench).queryByLabelText('知识资料智能助教')).not.toBeInTheDocument();
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
    expect(await screen.findByText('旧资料目录加载失败')).toBeInTheDocument();
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
        navigationContext={{ view: 'public' }}
      />,
    );

    const progressbar = await screen.findByRole('progressbar');
    expect(progressbar.firstElementChild).toHaveStyle({ transform: 'scaleX(0.5)' });
  });

  it('keeps imported files visible when the old directory fails', async () => {
    fetchWithAuth.mockImplementation(async url => {
      if (url.includes('/knowledge/files')) throw new Error('old service down');
      return { ok: true, json: async () => url.endsWith('/content/library')
        ? { files: [{ id: 'd1', name: '个人已入库资料', scope: 'personal', chunk_count: 1 }], stats: { total_documents: 1, total_chunks: 1 } }
        : { items: [] } };
    });
    render(<KnowledgePage currentUser={{ role: 'user' }} />);
    expect(await screen.findByRole('button', { name: '个人已入库资料' })).toBeInTheDocument();
    expect(await screen.findByText('旧资料目录加载失败')).toBeInTheDocument();
  });

  it('refreshes committed statistics and files after upload', async () => {
    let imported = false;
    fetchWithAuth.mockImplementation(async url => {
      if (url.includes('/content/import-file')) {
        imported = true;
        return { ok: true, json: async () => ({ chapter_hierarchy: { ok: true, chapter_nodes: 1 } }) };
      }
      return { ok: true, json: async () => url.endsWith('/content/library')
        ? { files: imported ? [{ id: 'd1', name: '新上传资料', scope: 'personal', chunk_count: 4 }] : [],
          stats: { total_documents: imported ? 1 : 0, total_chunks: imported ? 4 : 0, total_knowledge_points: imported ? 7 : 0, total_vectors: imported ? 7 : 0 } }
        : { files: [], items: [] } };
    });
    const { container } = render(<KnowledgePage currentUser={{ role: 'user' }} />);
    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalled());
    fireEvent.change(container.querySelector('input[type="file"]'), { target: { files: [new File(['test'], '新上传资料.pdf', { type: 'application/pdf' })] } });
    expect(await screen.findByRole('button', { name: '新上传资料' })).toBeInTheDocument();
    expect(await screen.findByText('7 个知识点 · 7 条向量')).toBeInTheDocument();
  });

  it('shows deterministic recognition confidence and opens a read-only review', async () => {
    fetchWithAuth.mockImplementation(async (url) => ({
      ok: true,
      json: async () => {
        if (url.includes('/recognition-reports/run-1')) {
          return {
            report_id: 'run-1', title: '用户中药学', structural_confidence: 0.91,
            source_chunk_count: 100, mapped_chunk_count: 98, needs_review_chunk_count: 2,
            confidence_level: 'good', issue_count_total: 1, read_only: true,
            metrics: [{ key: 'mapping_coverage', label: '切片映射覆盖率', ratio: 0.98, numerator: 98, denominator: 100, passed: false }],
            issues: [{ code: 'UNMAPPED_CHUNK', severity: 'error', message: '切片没有章节映射', chunk_uid: 'C_99' }],
          };
        }
        if (url.includes('/recognition-reports')) {
          return { items: [{ report_id: 'run-1', title: '用户中药学', structural_confidence: 0.91, confidence_level: 'good', book_count: 1, source_chunk_count: 100, mapped_chunk_count: 98, needs_review_chunk_count: 2 }] };
        }
        if (url.includes('/knowledge/status')) return { total_documents: 1, total_chunks: 100, status: '就绪', progress: 100, is_processing: false };
        if (url.endsWith('/knowledge/catalog')) return { documents: [], datasets: [], indexes: [], embedding: null };
        return { files: [] };
      },
    }));

    render(<KnowledgePage currentUser={{ username: 'alice', role: 'user' }} navigationContext={{ view: 'personal' }} />);

    expect(await screen.findByText('结构识别置信度 91%')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /用户中药学/ }));
    expect(await screen.findByRole('dialog', { name: /用户中药学 · 识别审查/ })).toBeInTheDocument();
    expect(screen.getByText('切片没有章节映射')).toBeInTheDocument();
    expect(screen.getByText(/只读报告/)).toBeInTheDocument();
  });
});

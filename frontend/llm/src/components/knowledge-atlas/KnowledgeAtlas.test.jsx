import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import KnowledgeAtlas from './KnowledgeAtlas';
import * as api from './knowledgeAtlasApi';
import { getAtlasNodeDrawKind, shouldContinueAtlasFrame } from './useKnowledgeAtlasCanvas';

vi.mock('./knowledgeAtlasApi', () => ({
  loadAtlasStatus: vi.fn(),
  loadAtlasRoutes: vi.fn(),
  loadAtlasNodes: vi.fn(),
  loadAtlasDetail: vi.fn(),
  resolveAtlasContext: vi.fn(),
  warmAtlas: vi.fn(),
  atlasImageUrl: vi.fn((name) => `/api/knowledge/atlas/images/${encodeURIComponent(name)}`),
  loadAtlasImage: vi.fn(async (name) => `/api/knowledge/atlas/images/${encodeURIComponent(name)}`),
}));

const route = { id: 'textbook_14_5', name: '十四五教材', description: '教材知识路线', book_count: 14 };
const lv1 = { id: 'book-1', name: '药理学', children_count: 1, count: 1 };
const lv2 = { id: 'topic-1', name: '第一节 心律失常的电生理学基础', children_count: 1, count: 1 };
const kp = { id: 'kp-1', name: '折返', question_count: 1, video_count: 1, count: 2 };
const questionOnly = { id: 'kp-2', name: '动作电位', question_count: 2, video_count: 0, count: 2 };
const videoOnly = { id: 'kp-3', name: '钠通道', question_count: 0, video_count: 1, count: 1 };
const plain = { id: 'kp-4', name: '复极', question_count: 0, video_count: 0, count: 0 };

function responseForLevel({ level }) {
  return { nodes: level === 1 ? [lv1] : level === 2 ? [lv2] : [kp, questionOnly, videoOnly, plain], stats: { lv1: 14, lv2: 4535, lv3: 73777 } };
}

describe('KnowledgeAtlas', () => {
  it('keeps L1/L2 nodes solid and enables four resource outlines only at L3', () => {
    expect(getAtlasNodeDrawKind(plain, false)).toBe('solid');
    expect(getAtlasNodeDrawKind(questionOnly, false)).toBe('solid');
    expect(getAtlasNodeDrawKind(plain, true)).toBe('plain');
    expect(getAtlasNodeDrawKind(questionOnly, true)).toBe('question');
  });

  it('stops idle drawing while assets are loading or a space transition waits for data', () => {
    const base = {
      reducedMotion: false,
      hidden: false,
      loading: true,
      spaceAnimating: false,
      paused: false,
      autoRotate: true,
      inertiaActive: false,
      pointerActive: false,
      transitionActive: false,
    };
    expect(shouldContinueAtlasFrame(base)).toBe(false);
    expect(shouldContinueAtlasFrame({ ...base, spaceAnimating: true })).toBe(true);
  });

  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => 7));
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), arc: vi.fn(), stroke: vi.fn(), fill: vi.fn(),
      moveTo: vi.fn(), lineTo: vi.fn(), save: vi.fn(), restore: vi.fn(), fillText: vi.fn(), measureText: vi.fn(() => ({ width: 30 })),
      createRadialGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
    });
    api.loadAtlasStatus.mockResolvedValue({ enabled: true, available: true, warmed: true });
    api.loadAtlasRoutes.mockResolvedValue([route, { id: 'tcm_assistant', name: '中医助理医师' }, { id: 'postgraduate', name: '西医考研' }]);
    api.loadAtlasNodes.mockImplementation(async (params) => responseForLevel(params));
    api.loadAtlasDetail.mockResolvedValue({
      kp,
      chunks: [{ id: 'chunk-1', content: '折返是心律失常的重要机制。公式 $I = g(V-E)$', images: ['figure.png'] }],
      questions: [{ question_id: 'q-1', stem: '折返形成的条件与 $I=g(V-E)$ 是？', options: ['A. 单向阻滞与 $I$', 'B. 双向阻滞'], answer: '$A$', explanation: '需有单向阻滞，且 $g > 0$。' }],
      question_count: 1,
      videos: [{ bvid: 'BV1test', page: 2, start_seconds: 72, end_seconds: 96, topic: '折返机制' }],
    });
    api.resolveAtlasContext.mockResolvedValue({ resolved: false, route: 'textbook_14_5' });
    api.warmAtlas.mockResolvedValue({ ok: true });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('keeps all three routes and shows only layouts valid for the current layer', async () => {
    render(<KnowledgeAtlas initialContext={{}} />);
    expect(await screen.findByRole('heading', { name: '知识星球' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '学习路线' })).toHaveDisplayValue('十四五教材');
    expect(screen.getByRole('option', { name: '中医助理医师' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '西医考研' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '球面布局' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '顺序列表' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '相关聚类' })).not.toBeInTheDocument();
    await screen.findByRole('button', { name: /进入药理学/ });
    const canvas = screen.getByLabelText('知识星球画布');
    expect(canvas).toHaveAttribute('data-zoom', '1.00');
    fireEvent.click(screen.getByRole('button', { name: '放大知识星球' }));
    await waitFor(() => expect(Number(canvas.getAttribute('data-zoom'))).toBeGreaterThan(1));
    fireEvent.click(screen.getByRole('button', { name: '复位知识星球' }));
    expect(canvas).toHaveAttribute('data-zoom', '1.00');
  });

  it('groups route context in one labelled topbar and keeps view controls in a dedicated toolbar', async () => {
    render(<KnowledgeAtlas initialContext={{ route: 'textbook_14_5', lv1: '药理学' }} />);

    const topbar = await screen.findByRole('banner', { name: '知识星球顶栏' });
    expect(within(topbar).getByRole('heading', { name: '知识星球' })).toBeInTheDocument();
    expect(within(topbar).getByRole('combobox', { name: '学习路线' })).toBeInTheDocument();
    expect(within(topbar).getByText('药理学')).toBeInTheDocument();
    expect(screen.getByRole('toolbar', { name: '知识星球视图工具' })).toBeInTheDocument();
  });

  it('forces compact L3 nodes to zoom 1.28 and enables cluster only for at least 12 L3 nodes', async () => {
    const { unmount } = render(<KnowledgeAtlas initialContext={{ route: 'textbook_14_5', lv1: '药理学', lv2: '第一节 心律失常的电生理学基础' }} />);
    const compactCanvas = await screen.findByLabelText('知识星球画布');
    await screen.findByRole('button', { name: /打开折返详情/ });
    await waitFor(() => expect(compactCanvas).toHaveAttribute('data-zoom', '1.28'));
    expect(screen.queryByRole('button', { name: '相关聚类' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /自动旋转/ })).toBeDisabled();
    unmount();

    api.loadAtlasNodes.mockImplementation(async (params) => (
      params.level === 3
        ? { nodes: Array.from({ length: 12 }, (_, index) => ({ ...kp, id: `kp-${index}`, name: `知识点 ${index}` })), stats: {} }
        : responseForLevel(params)
    ));
    render(<KnowledgeAtlas initialContext={{ route: 'textbook_14_5', lv1: '药理学', lv2: '第一节 心律失常的电生理学基础' }} />);
    const clusterButton = await screen.findByRole('button', { name: '相关聚类' });
    fireEvent.click(clusterButton);
    const clusterCanvas = screen.getByLabelText('知识星球画布');
    await waitFor(() => expect(clusterCanvas).toHaveAttribute('data-zoom', '1.14'));
    expect(screen.getByRole('button', { name: /自动旋转/ })).toBeDisabled();
  });

  it('drills through three levels, searches current nodes, and exposes four resource meanings', async () => {
    render(<KnowledgeAtlas initialContext={{}} />);
    fireEvent.click(await screen.findByRole('button', { name: /进入药理学/ }));
    fireEvent.click(await screen.findByRole('button', { name: /进入第一节 心律失常的电生理学基础/ }));
    expect(await screen.findByRole('button', { name: /打开折返详情/ })).toHaveAttribute('data-resource-kind', 'both');
    expect(screen.getByRole('button', { name: /打开动作电位详情/ })).toHaveAttribute('data-resource-kind', 'question');
    expect(screen.getByRole('button', { name: /打开钠通道详情/ })).toHaveAttribute('data-resource-kind', 'video');
    expect(screen.getByRole('button', { name: /打开复极详情/ })).toHaveAttribute('data-resource-kind', 'plain');
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索当前层' }), { target: { value: '不存在' } });
    expect(screen.getByText('当前层没有匹配节点')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '药理学' }));
    expect(await screen.findByText('第一节 心律失常的电生理学基础')).toBeInTheDocument();
  });

  it('keeps the 1050ms morph and exposes direction-aware dive and back space transitions', async () => {
    const { unmount } = render(<KnowledgeAtlas initialContext={{}} />);
    const stage = await screen.findByTestId('knowledge-atlas-stage');
    expect(stage).toHaveAttribute('data-morph-duration', '1050');
    fireEvent.click(await screen.findByRole('button', { name: /进入药理学/ }));
    expect(stage).toHaveAttribute('data-space-transition', 'dive-out');
    expect(stage).toHaveAttribute('data-space-transition-duration', '1080');
    unmount();

    render(<KnowledgeAtlas initialContext={{ route: 'textbook_14_5', lv1: '药理学' }} />);
    const nestedStage = await screen.findByTestId('knowledge-atlas-stage');
    fireEvent.click(await screen.findByRole('button', { name: '返回上一级' }));
    expect(nestedStage).toHaveAttribute('data-space-transition', 'back-out');
  });

  it('opens an accessible detail drawer with video timestamp, chunk image, formula text, answer and explanation', async () => {
    render(<KnowledgeAtlas initialContext={{ route: 'textbook_14_5', lv1: '药理学', lv2: '第一节 心律失常的电生理学基础', kpId: 'kp-1' }} />);
    const drawer = await screen.findByRole('dialog', { name: '折返' });
    const closeButton = screen.getByRole('button', { name: '关闭详情' });
    await waitFor(() => expect(closeButton).toHaveFocus());
    fireEvent.keyDown(closeButton, { key: 'Tab', shiftKey: true });
    expect(drawer).toContainElement(document.activeElement);
    expect(document.activeElement).not.toBe(closeButton);
    expect(screen.getByRole('button', { name: /01:12/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /01:12/ }));
    expect(screen.getByTitle('折返机制')).toHaveAttribute('src', expect.stringContaining('bvid=BV1test'));
    expect(screen.getByAltText('教材原图')).toHaveAttribute('src', '/api/knowledge/atlas/images/figure.png');
    fireEvent.click(screen.getByRole('tab', { name: /题目/ }));
    expect(screen.getByText(/折返形成的条件与/)).toBeInTheDocument();
    expect(drawer.querySelectorAll('.katex').length).toBeGreaterThanOrEqual(2);
    fireEvent.click(screen.getByRole('button', { name: '显示答案与解析' }));
    expect(screen.getAllByText(/单向阻滞/).length).toBeGreaterThanOrEqual(2);
    expect(drawer.querySelectorAll('.katex').length).toBeGreaterThanOrEqual(4);
    fireEvent.keyDown(drawer, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '折返' })).not.toBeInTheDocument());
  });

  it('isolates a failed Atlas request and aborts in-flight work on unmount', async () => {
    let capturedSignal;
    api.loadAtlasNodes.mockImplementation(({ signal }) => {
      capturedSignal = signal;
      return Promise.reject(new Error('Atlas assets missing'));
    });
    const { unmount } = render(<KnowledgeAtlas initialContext={{}} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Atlas assets missing');
    act(() => unmount());
    expect(capturedSignal.aborted).toBe(true);
    expect(cancelAnimationFrame).toHaveBeenCalled();
  });

  it('restores dashboard context through the resolver and honors reduced-motion opt-in', async () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    api.resolveAtlasContext.mockResolvedValue({
      resolved: true,
      route: 'postgraduate',
      lv1: '药理学',
      lv2: '第一节 心律失常的电生理学基础',
      kp_id: 'kp-1',
    });
    render(<KnowledgeAtlas initialContext={{ trackId: 'track-a', membershipId: 'node-a', source: 'dashboard' }} />);

    await waitFor(() => expect(api.resolveAtlasContext).toHaveBeenCalledWith(expect.objectContaining({ trackId: 'track-a', membershipId: 'node-a' })));
    expect(await screen.findByRole('dialog', { name: '折返' })).toBeInTheDocument();
    expect(screen.getByLabelText(/自动旋转/)).toBeDisabled();
    expect(screen.getByText('已按系统偏好减少空间运动')).toBeInTheDocument();
  });

  it('hands control back to the sources workspace when the backend feature switch is off', async () => {
    const onDisabled = vi.fn();
    api.loadAtlasStatus.mockResolvedValue({ enabled: false, available: false, errors: ['KNOWLEDGE_ATLAS_ENABLED=false'] });
    render(<KnowledgeAtlas initialContext={{}} onDisabled={onDisabled} />);
    await waitFor(() => expect(onDisabled).toHaveBeenCalledOnce());
  });
});

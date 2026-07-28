import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import LearningPathPage from './LearningPathPage';

function response(payload, ok = true, status = 200) {
  return { ok, status, text: async () => JSON.stringify(payload) };
}

const routePayload = {
  schema_version: '1.0',
  nodes: [{
    node_id: 'stage-1',
    membership_id: 'stage-1',
    node_type: 'stage',
    title: '中医基础与文化语言',
    description: '建立中医基础概念。',
    order: 1,
    status: 'in_progress',
    child_count: 4,
  }],
};

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function installLearningPathFetch(dashboardPayload = {}, options = {}) {
  const fetchMock = vi.fn((url) => {
    const path = String(url);
    if (path.includes('/qualification-targets')) {
      return Promise.resolve(response({
        items: [
          {
            target_id: 'target-tcm',
            exam_track_id: 'track-tcm',
            official_name: '中医类别执业医师资格考试',
            exam_date: '2026-11-29T23:59:59+08:00',
          },
          {
            target_id: 'target-integrated',
            exam_track_id: 'track-integrated',
            official_name: '中西医结合执业医师资格考试',
            exam_date: '2026-11-29T23:59:59+08:00',
          },
        ],
      }));
    }
    if (path.endsWith('/personalization/learning-target')) {
      return Promise.resolve(response({ target: { exam_track_id: 'track-tcm' } }));
    }
    if (path.includes('/learning-path?parent_id=') && options.stageFetch) {
      return options.stageFetch(path);
    }
    if (path.includes('/learning-path?parent_id=') && options.stagePayload) {
      return Promise.resolve(response(options.stagePayload));
    }
    if (path.includes('/learning-path')) return Promise.resolve(response(options.rootPayload || routePayload));
    if (path.includes('/learning-context')) {
      if (options.learningContextFetch) return options.learningContextFetch(path);
      return Promise.resolve(response({
        long_term_plan: { content: '【最终目标】通过中医执业医师资格考试。' },
        short_term_plan: { content: '【本周安排】完成中医基础理论复习。' },
      }));
    }
    if (path.includes('/dashboard/home')) return Promise.resolve(response(dashboardPayload));
    return Promise.resolve(response({}));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('LearningPathPage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('renders the learning path plan, short route, task rail, and route data', async () => {
    installLearningPathFetch({
      review_queue: {
        entries: [{
          is_due: true,
          memory_unit: {
            memory_unit_id: 'memory-1',
            kp_id: 'KP_1',
            prompt_abstract: '四君子汤配伍',
            mastery_score: 45,
          },
          task: { review_task_id: 'review-1' },
        }],
      },
    });

    render(<LearningPathPage currentUser={{ display_name: '林同学' }} onNavigate={vi.fn()} />);

    expect(await screen.findByRole('heading', { name: '学习路径规划' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '短期学习路径' })).toBeInTheDocument();
    expect(screen.getByText('中医基础与文化语言')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '复习任务' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: /四君子汤配伍/ })).toBeInTheDocument();
  });

  it('shows the persisted planning details and returns to the short route', async () => {
    installLearningPathFetch();
    render(<LearningPathPage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: '了解详情' }));

    expect(await screen.findByText('【最终目标】通过中医执业医师资格考试。')).toBeInTheDocument();
    expect(screen.getByText('【本周安排】完成中医基础理论复习。')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '长期规划和短期规划说明' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '返回短期学习路径' }));
    expect(await screen.findByRole('heading', { name: '短期学习路径' })).toBeInTheDocument();
  });

  it('preserves the core navigation intents from review and learning tasks', async () => {
    const onNavigate = vi.fn();
    installLearningPathFetch({
      current_learning_task: {
        task_id: 'TASK_1',
        title: '今日学习任务',
        items: [{
          task_item_id: 'ITEM_1',
          title: '完成四君子汤练习',
          kp_name: '四君子汤',
          estimated_minutes: 15,
          progress: { rate: 0.5 },
          action: {
            destination: 'workshop.practice',
            params: { taskItemId: 'ITEM_1', kpId: 'KP_1' },
          },
        }],
      },
      review_queue: {
        entries: [{
          is_due: true,
          memory_unit: {
            memory_unit_id: 'memory-1',
            kp_id: 'KP_1',
            prompt_abstract: '四君子汤配伍',
          },
          task: { review_task_id: 'review-1' },
        }],
      },
    });
    render(<LearningPathPage currentUser={{ username: 'alice' }} onNavigate={onNavigate} />);

    fireEvent.click(await screen.findByRole('button', { name: /四君子汤配伍/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'question_training',
        kpId: 'KP_1',
        reviewTaskId: 'review-1',
      },
    });

    fireEvent.click(screen.getByRole('tab', { name: '学习任务' }));
    fireEvent.click(screen.getByRole('button', { name: /完成四君子汤练习/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'question_training',
        taskItemId: 'ITEM_1',
        kpId: 'KP_1',
      },
    });
  });

  it('keeps a current learning task visible when the payload has no item list', async () => {
    installLearningPathFetch({
      current_learning_task: {
        task_id: 'TASK_SUMMARY',
        title: '完成今日章节学习',
        description: '继续学习中医基础理论',
        duration: '20 分钟',
        progress: 35,
      },
    });
    render(<LearningPathPage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole('tab', { name: '学习任务' }));

    expect(screen.getByRole('button', { name: /完成今日章节学习/ })).toBeInTheDocument();
  });

  it('does not duplicate the learning target selector inside the learning path page', () => {
    installLearningPathFetch();
    render(<LearningPathPage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    expect(screen.queryByRole('combobox', { name: '学习目标' })).not.toBeInTheDocument();
  });

  it('drills from a stage into its textbook and opens the textbook chapters', async () => {
    const onNavigate = vi.fn();
    const fetchMock = installLearningPathFetch({}, {
      stagePayload: {
        schema_version: '1.0',
        nodes: [{
          node_id: 'book-1',
          parent_id: 'stage-1',
          membership_id: 'book-1',
          node_type: 'book',
          title: '《中医基础理论》',
          description: '中医基础理论教材',
          order: 1,
          status: 'in_progress',
          child_count: 12,
          navigation: {
            route_id: 'textbook_tcm_foundation',
            book: '中医基础理论',
          },
        }],
      },
    });
    render(<LearningPathPage currentUser={{ username: 'alice' }} onNavigate={onNavigate} />);

    fireEvent.click(await screen.findByRole('button', { name: /进入中医基础与文化语言/ }));
    fireEvent.click(await screen.findByRole('button', { name: /进入《中医基础理论》/ }));

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/learning-path?parent_id=stage-1'),
      expect.any(Object),
    );
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: {
        view: 'textbook-chapters',
        route: 'textbook_tcm_foundation',
        lv1: '中医基础理论',
        source: 'learning-plan',
      },
    });
  });

  it('keeps the latest stage drill-down when an earlier request resolves last', async () => {
    const firstStage = deferred();
    const secondStage = deferred();
    installLearningPathFetch({}, {
      rootPayload: {
        schema_version: '1.0',
        nodes: [
          { ...routePayload.nodes[0], node_id: 'stage-1', membership_id: 'stage-1', title: '第一阶段' },
          { ...routePayload.nodes[0], node_id: 'stage-2', membership_id: 'stage-2', title: '第二阶段', order: 2 },
        ],
      },
      stageFetch: (path) => path.includes('stage-1') ? firstStage.promise : secondStage.promise,
    });
    render(<LearningPathPage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: /进入第一阶段/ }));
    fireEvent.click(screen.getByRole('button', { name: /进入第二阶段/ }));

    await act(async () => {
      secondStage.resolve(response({
        schema_version: '1.0',
        nodes: [{
          node_id: 'book-2',
          node_type: 'book',
          title: '《第二阶段教材》',
          order: 1,
          status: 'in_progress',
          navigation: { route_id: 'route-2', book: '第二阶段教材' },
        }],
      }));
      await secondStage.promise;
    });
    expect(await screen.findByRole('button', { name: /进入《第二阶段教材》/ })).toBeInTheDocument();

    await act(async () => {
      firstStage.resolve(response({
        schema_version: '1.0',
        nodes: [{
          node_id: 'book-1',
          node_type: 'book',
          title: '《第一阶段教材》',
          order: 1,
          status: 'in_progress',
          navigation: { route_id: 'route-1', book: '第一阶段教材' },
        }],
      }));
      await firstStage.promise;
    });
    expect(screen.getByRole('button', { name: /进入《第二阶段教材》/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /进入《第一阶段教材》/ })).not.toBeInTheDocument();
  });

  it('deduplicates repeated drill requests for the same stage', async () => {
    const stageRequest = deferred();
    const fetchMock = installLearningPathFetch({}, {
      stageFetch: () => stageRequest.promise,
    });
    render(<LearningPathPage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);
    const stageButton = await screen.findByRole('button', { name: /进入中医基础与文化语言/ });

    fireEvent.click(stageButton);
    fireEvent.click(stageButton);
    fireEvent.doubleClick(stageButton);

    const stageCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes('parent_id=stage-1'));
    expect(stageCalls).toHaveLength(1);

    await act(async () => {
      stageRequest.resolve(response({ schema_version: '1.0', nodes: [] }));
      await stageRequest.promise;
    });
  });

  it('does not report state updates when planning details resolve after unmount', async () => {
    const planningRequest = deferred();
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    installLearningPathFetch({}, {
      learningContextFetch: () => planningRequest.promise,
    });
    const { unmount } = render(
      <LearningPathPage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />,
    );

    fireEvent.click(await screen.findByRole('button', { name: '了解详情' }));
    unmount();
    await act(async () => {
      planningRequest.resolve(response({
        long_term_plan: { content: '长期规划' },
        short_term_plan: { content: '短期规划' },
      }));
      await planningRequest.promise;
    });

    expect(consoleError).not.toHaveBeenCalled();
  });

  it('exposes keyboard-operable ARIA task tabs', async () => {
    installLearningPathFetch();
    render(<LearningPathPage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    const learningTab = screen.getByRole('tab', { name: '学习任务' });
    const reviewTab = screen.getByRole('tab', { name: '复习任务' });
    expect(reviewTab).toHaveAttribute('tabindex', '0');
    expect(learningTab).toHaveAttribute('tabindex', '-1');
    expect(learningTab.id).not.toBe(reviewTab.id);
    expect(document.getElementById(reviewTab.getAttribute('aria-controls'))).toHaveAttribute('role', 'tabpanel');
    expect(document.getElementById(reviewTab.getAttribute('aria-controls'))).toHaveAttribute(
      'aria-labelledby',
      reviewTab.id,
    );

    reviewTab.focus();
    fireEvent.keyDown(reviewTab, { key: 'ArrowLeft' });
    expect(learningTab).toHaveFocus();
    expect(learningTab).toHaveAttribute('aria-selected', 'true');
    expect(learningTab).toHaveAttribute('tabindex', '0');

    fireEvent.keyDown(learningTab, { key: 'ArrowRight' });
    expect(reviewTab).toHaveFocus();
    expect(reviewTab).toHaveAttribute('aria-selected', 'true');
  });
});

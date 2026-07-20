import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ExamAtlas from './ExamAtlas';

function jsonResponse(payload, ok = true) {
  return Promise.resolve({
    ok,
    status: ok ? 200 : 500,
    text: async () => JSON.stringify(payload),
  });
}

const track = {
  track_id: 'track-a',
  title_normalized: '2025 中医执业医师资格考试',
};
const secondTrack = {
  track_id: 'track-b',
  title_normalized: '2025 中西医结合执业医师资格考试',
};

const rootNode = {
  membership_id: 'root-a',
  parent_membership_id: null,
  node_id: 'node-root',
  title: '中医学基础',
  child_count: 1,
  node: { node_type: 'domain' },
};

const leafNode = {
  membership_id: 'leaf-a',
  parent_membership_id: 'root-a',
  node_id: 'node-leaf',
  title: '阴阳学说',
  child_count: 0,
  node: { node_type: 'requirement', is_requirement: true },
};

function installFetch() {
  const requests = [];
  vi.stubGlobal('fetch', vi.fn((url) => {
    requests.push(url);
    if (url.endsWith('/personalization/learning-target')) {
      return jsonResponse({ target: { exam_track_id: 'track-a', exam_name: track.title_normalized } });
    }
    if (url.endsWith('/exam-learning/tracks')) {
      return jsonResponse({ items: [track], total: 1, version: '2.0.0' });
    }
    if (url.endsWith('/exam-learning/tracks/track-a/nodes')) {
      return jsonResponse({ track, parent_membership_id: null, items: [rootNode], total: 1 });
    }
    if (url.endsWith('/exam-learning/tracks/track-a/nodes?parent_membership_id=root-a')) {
      return jsonResponse({ track, parent_membership_id: 'root-a', items: [leafNode], total: 1 });
    }
    if (url.endsWith('/exam-learning/tracks/track-a/nodes/root-a')) {
      return jsonResponse({
        membership: rootNode,
        node: rootNode.node,
        breadcrumb: [{ membership_id: 'root-a', title: rootNode.title }],
        child_count: 1,
        track,
      });
    }
    if (url.endsWith('/exam-learning/tracks/track-a/nodes/leaf-a')) {
      return jsonResponse({
        membership: leafNode,
        node: leafNode.node,
        breadcrumb: [
          { membership_id: 'root-a', title: rootNode.title },
          { membership_id: 'leaf-a', title: leafNode.title },
        ],
        child_count: 0,
        track,
      });
    }
    if (url.endsWith('/exam-learning/knowledge-points/kp-yinyang/learner-state')) {
      return jsonResponse({
        kp_id: 'kp-yinyang',
        mastery_score: 82.5,
        mastery_status: 'mastered',
        attempt_count: 4,
        review_due: true,
        next_review_at: '2026-07-18T12:00:00',
        requires_remediation: false,
        active_mistake_count: 1,
      });
    }
    if (url.includes('/exam-learning/tracks/track-a/nodes/leaf-a/knowledge-points')) {
      return jsonResponse({
        track_id: 'track-a',
        membership_id: 'leaf-a',
        items: [
          { kp_id: 'kp-yinyang', name: '阴阳学说', path: ['中医学', '中医基础理论', '阴阳学说'], accepted_count: 1 },
        ],
        total: 1,
        offset: 0,
        limit: 50,
        has_more: false,
      });
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  return requests;
}

describe('ExamAtlas', () => {
  beforeEach(() => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('requires a formal learning target instead of silently choosing the first track', async () => {
    const requests = [];
    vi.stubGlobal('fetch', vi.fn((url) => {
      requests.push(url);
      if (url.endsWith('/personalization/learning-target')) return jsonResponse({ target: null });
      if (url.endsWith('/exam-learning/tracks')) return jsonResponse({ items: [track, secondTrack] });
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<ExamAtlas onNavigate={vi.fn()} />);

    expect(await screen.findByText('先选择正式考试目标')).toBeInTheDocument();
    expect(screen.getByLabelText('切换考试目标')).toHaveValue('');
    expect(requests.some((url) => url.includes('/nodes'))).toBe(false);
  });

  it('loads the active target root, drills by membership, and returns with breadcrumbs', async () => {
    const requests = installFetch();
    render(<ExamAtlas onNavigate={vi.fn()} />);

    expect(await screen.findByRole('heading', { name: '2025 中医执业医师资格考试' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /进入中医学基础/ }));

    expect(await screen.findByRole('button', { name: /查看阴阳学说知识点/ })).toBeInTheDocument();
    expect(requests).toContain('/api/exam-learning/tracks/track-a/nodes?parent_membership_id=root-a');
    fireEvent.click(screen.getByRole('button', { name: '2025 中医执业医师资格考试' }));
    expect(await screen.findByRole('button', { name: /进入中医学基础/ })).toBeInTheDocument();
  });

  it('searches the current surface and shows an equivalent semantic list', async () => {
    installFetch();
    render(<ExamAtlas onNavigate={vi.fn()} />);

    await screen.findByRole('button', { name: /进入中医学基础/ });
    const list = screen.getByRole('tree', { name: '当前考纲层级列表' });
    expect(within(list).getByText('中医学基础')).toBeInTheDocument();

    fireEvent.change(screen.getByRole('searchbox', { name: '搜索当前球面' }), {
      target: { value: '方剂' },
    });
    expect(screen.getByText('当前层没有匹配节点')).toBeInTheDocument();
  });

  it('opens accepted KP detail and sends practice, knowledge, and assistant intents', async () => {
    installFetch();
    const onNavigate = vi.fn();
    render(<ExamAtlas onNavigate={onNavigate} />);

    fireEvent.click(await screen.findByRole('button', { name: /进入中医学基础/ }));
    fireEvent.click(await screen.findByRole('button', { name: /查看阴阳学说知识点/ }));
    fireEvent.click(await screen.findByRole('button', { name: /打开知识点阴阳学说/ }));

    const dialog = await screen.findByRole('dialog', { name: '阴阳学说' });
    expect(within(dialog).getAllByText('中医学 / 中医基础理论 / 阴阳学说')).not.toHaveLength(0);
    expect(await within(dialog).findByText('82.5%')).toBeInTheDocument();
    expect(within(dialog).getByText('复习已到期')).toBeInTheDocument();
    expect(within(dialog).getByText('1 道待复盘错题')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '开始练习' }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: {
        trackId: 'track-a',
        membershipId: 'leaf-a',
        kpId: 'kp-yinyang',
        kpName: '阴阳学说',
      },
    });

    fireEvent.click(screen.getByRole('button', { name: '查看资料' }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'knowledge',
      params: {
        trackId: 'track-a',
        membershipId: 'leaf-a',
        kpId: 'kp-yinyang',
        query: '阴阳学说',
      },
    });

    fireEvent.click(screen.getByRole('button', { name: '询问助教' }));
    expect(onNavigate.mock.calls.at(-1)[0]).toMatchObject({
      page: 'assistant',
      params: {
        trackId: 'track-a',
        membershipId: 'leaf-a',
        kpId: 'kp-yinyang',
      },
    });
  });

  it('disables target changes while the selected target is being persisted', async () => {
    let resolveSave;
    const savePromise = new Promise((resolve) => { resolveSave = resolve; });
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      if (url.endsWith('/personalization/learning-target') && options.method === 'PUT') {
        return savePromise;
      }
      if (url.endsWith('/personalization/learning-target')) {
        return jsonResponse({ target: { exam_track_id: 'track-a' } });
      }
      if (url.endsWith('/exam-learning/tracks')) {
        return jsonResponse({ items: [track, secondTrack] });
      }
      if (url.endsWith('/exam-learning/tracks/track-a/nodes')) {
        return jsonResponse({ track, items: [rootNode], total: 1 });
      }
      if (url.endsWith('/exam-learning/tracks/track-b/nodes')) {
        return jsonResponse({ track: secondTrack, items: [], total: 0 });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<ExamAtlas onNavigate={vi.fn()} />);

    const selector = await screen.findByLabelText('切换考试目标');
    fireEvent.change(selector, { target: { value: 'track-b' } });
    expect(selector).toBeDisabled();

    resolveSave(await jsonResponse({ target: { exam_track_id: 'track-b' } }));
    await waitFor(() => expect(selector).not.toBeDisabled());
  });

  it('ignores a stale layer response after navigation selects a newer track', async () => {
    let resolveTrackA;
    const trackAResponse = new Promise((resolve) => { resolveTrackA = resolve; });
    const trackBNode = { ...rootNode, membership_id: 'root-b', title: '中西医结合基础' };
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.endsWith('/personalization/learning-target')) {
        return jsonResponse({ target: { exam_track_id: 'track-a' } });
      }
      if (url.endsWith('/exam-learning/tracks')) {
        return jsonResponse({ items: [track, secondTrack] });
      }
      if (url.endsWith('/exam-learning/tracks/track-a/nodes')) return trackAResponse;
      if (url.endsWith('/exam-learning/tracks/track-b/nodes')) {
        return jsonResponse({ track: secondTrack, items: [trackBNode], total: 1 });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));
    const { rerender } = render(
      <ExamAtlas navigationContext={{ trackId: 'track-a' }} onNavigate={vi.fn()} />,
    );

    rerender(<ExamAtlas navigationContext={{ trackId: 'track-b' }} onNavigate={vi.fn()} />);
    expect(await screen.findByRole('button', { name: /进入中西医结合基础/ })).toBeInTheDocument();
    resolveTrackA(await jsonResponse({ track, items: [rootNode], total: 1 }));

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /进入中医学基础/ })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /进入中西医结合基础/ })).toBeInTheDocument();
    });
  });

  it('ignores stale leaf knowledge points after the user switches tracks', async () => {
    let resolveLeaf;
    const leafResponse = new Promise((resolve) => { resolveLeaf = resolve; });
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      if (url.endsWith('/personalization/learning-target') && options.method === 'PUT') {
        return jsonResponse({ target: { exam_track_id: 'track-b' } });
      }
      if (url.endsWith('/personalization/learning-target')) {
        return jsonResponse({ target: { exam_track_id: 'track-a' } });
      }
      if (url.endsWith('/exam-learning/tracks')) return jsonResponse({ items: [track, secondTrack] });
      if (url.endsWith('/exam-learning/tracks/track-a/nodes')) {
        return jsonResponse({ track, items: [leafNode], total: 1 });
      }
      if (url.endsWith('/exam-learning/tracks/track-b/nodes')) {
        return jsonResponse({ track: secondTrack, items: [], total: 0 });
      }
      if (url.endsWith('/exam-learning/tracks/track-a/nodes/leaf-a')) return leafResponse;
      if (url.includes('/exam-learning/tracks/track-a/nodes/leaf-a/knowledge-points')) return leafResponse;
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<ExamAtlas onNavigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: /查看阴阳学说知识点/ }));
    fireEvent.change(screen.getByLabelText('切换考试目标'), { target: { value: 'track-b' } });
    await screen.findByText('当前层暂无节点');
    resolveLeaf(await jsonResponse({
      breadcrumb: [{ membership_id: 'leaf-a', title: '阴阳学说' }],
      items: [{ kp_id: 'kp-stale', name: '旧轨道知识点', accepted_count: 1 }],
      has_more: false,
      limit: 50,
    }));

    await waitFor(() => {
      expect(screen.queryByText('旧轨道知识点')).not.toBeInTheDocument();
      expect(screen.getByLabelText('切换考试目标')).toHaveValue('track-b');
    });
  });

  it('keeps an external navigation target authoritative over a stale save', async () => {
    let resolveTrackBSave;
    const trackBSave = new Promise((resolve) => { resolveTrackBSave = resolve; });
    const savedTrackIds = [];
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      if (url.endsWith('/personalization/learning-target') && options.method === 'PUT') {
        const requestedTrackId = JSON.parse(options.body).exam_track_id;
        savedTrackIds.push(requestedTrackId);
        if (requestedTrackId === 'track-b') return trackBSave;
        return jsonResponse({ target: { exam_track_id: requestedTrackId } });
      }
      if (url.endsWith('/personalization/learning-target')) {
        return jsonResponse({ target: { exam_track_id: 'track-a' } });
      }
      if (url.endsWith('/exam-learning/tracks')) {
        return jsonResponse({ items: [track, secondTrack] });
      }
      if (url.endsWith('/exam-learning/tracks/track-a/nodes')) {
        return jsonResponse({ track, items: [rootNode], total: 1 });
      }
      if (url.endsWith('/exam-learning/tracks/track-b/nodes')) {
        return jsonResponse({ track: secondTrack, items: [], total: 0 });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));
    const { rerender } = render(<ExamAtlas onNavigate={vi.fn()} />);

    await screen.findByRole('button', { name: /进入中医学基础/ });
    fireEvent.change(screen.getByLabelText('切换考试目标'), { target: { value: 'track-b' } });
    rerender(<ExamAtlas navigationContext={{ trackId: 'track-a' }} onNavigate={vi.fn()} />);
    resolveTrackBSave(await jsonResponse({ target: { exam_track_id: 'track-b' } }));

    await waitFor(() => expect(savedTrackIds).toEqual(['track-b', 'track-a']));
    expect(screen.getByLabelText('切换考试目标')).toHaveValue('track-a');
    expect(screen.getByRole('button', { name: /进入中医学基础/ })).toBeInTheDocument();
  });

  it('keeps the current track visible when target persistence fails', async () => {
    const requests = [];
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      requests.push({ url, options });
      if (url.endsWith('/personalization/learning-target') && options.method === 'PUT') {
        return jsonResponse({ detail: '目标保存失败' }, false);
      }
      if (url.endsWith('/personalization/learning-target')) {
        return jsonResponse({ target: { exam_track_id: 'track-a' } });
      }
      if (url.endsWith('/exam-learning/tracks')) {
        return jsonResponse({ items: [track, secondTrack] });
      }
      if (url.endsWith('/exam-learning/tracks/track-a/nodes')) {
        return jsonResponse({ track, items: [rootNode], total: 1 });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<ExamAtlas onNavigate={vi.fn()} />);

    await screen.findByRole('button', { name: /进入中医学基础/ });
    fireEvent.change(screen.getByLabelText('切换考试目标'), {
      target: { value: 'track-b' },
    });

    expect(await screen.findByRole('alert')).toHaveTextContent('目标保存失败');
    expect(screen.getByLabelText('切换考试目标')).toHaveValue('track-a');
    expect(requests.some(({ url }) => url.includes('/tracks/track-b/nodes'))).toBe(false);
  });

  it('shows a local retry state without replacing nodes with mock data', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.endsWith('/personalization/learning-target')) return jsonResponse({ target: { exam_track_id: 'track-a' } });
      if (url.endsWith('/exam-learning/tracks')) return jsonResponse({ items: [track] });
      return jsonResponse({ detail: '考纲暂时不可用' }, false);
    }));
    render(<ExamAtlas onNavigate={vi.fn()} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('考纲暂时不可用');
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(screen.queryByRole('treeitem')).not.toBeInTheDocument();
  });
});

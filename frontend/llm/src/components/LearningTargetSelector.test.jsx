import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import LearningTargetSelector from './LearningTargetSelector';

function response(payload, ok = true, status = 200) {
  return { ok, status, text: async () => JSON.stringify(payload) };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const qualificationTargets = [
  {
    target_id: 'target-a',
    exam_track_id: 'track-a',
    official_name: '中医执业医师资格考试',
  },
  {
    target_id: 'target-b',
    exam_track_id: 'track-b',
    official_name: '中西医结合执业医师资格考试',
  },
  {
    target_id: 'target-c',
    exam_track_id: 'track-c',
    official_name: '中医执业助理医师资格考试',
  },
];

function installTargetApi({
  currentTrackId = 'track-a',
  catalogFailures = 0,
  saveRequest,
} = {}) {
  let catalogAttempts = 0;
  const fetchMock = vi.fn((url, options = {}) => {
    const path = String(url);
    if (path.endsWith('/qualification-targets')) {
      catalogAttempts += 1;
      if (catalogAttempts <= catalogFailures) {
        return Promise.resolve(response({ detail: '资格考试目录加载失败' }, false, 503));
      }
      return Promise.resolve(response({ items: qualificationTargets }));
    }
    if (path.endsWith('/personalization/learning-target') && options.method === 'PUT') {
      if (saveRequest) return saveRequest(options);
      const body = JSON.parse(options.body);
      return Promise.resolve(response({ target: { exam_track_id: body.exam_track_id } }));
    }
    if (path.endsWith('/personalization/learning-target')) {
      return Promise.resolve(response({
        target: currentTrackId ? { exam_track_id: currentTrackId } : null,
      }));
    }
    return Promise.resolve(response({}));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('LearningTargetSelector', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('loads the qualification catalog and selects the persisted learning target', async () => {
    installTargetApi({ currentTrackId: 'track-b' });

    render(<LearningTargetSelector />);

    const select = await screen.findByRole('combobox', { name: '考试类别' });
    expect(select).toHaveValue('target-b');
    expect(screen.getByRole('option', { name: '中医执业医师资格考试' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '中西医结合执业医师资格考试' })).toBeInTheDocument();
  });

  it('shows a placeholder instead of the first catalog item when no target is persisted', async () => {
    installTargetApi({ currentTrackId: null });

    render(<LearningTargetSelector />);

    const select = await screen.findByRole('combobox', { name: '考试类别' });
    expect(select).toHaveValue('');
    expect(screen.getByRole('option', { name: '请选择考试类别' })).toBeDisabled();
  });

  it('does not check a menu item when no target is persisted', async () => {
    installTargetApi({ currentTrackId: null });

    render(<LearningTargetSelector variant="menu" />);

    const items = await screen.findAllByRole('menuitemradio');
    expect(items).toHaveLength(qualificationTargets.length);
    items.forEach((item) => expect(item).toHaveAttribute('aria-checked', 'false'));
  });

  it('persists the first catalog item before selecting it from an empty state', async () => {
    const onSelected = vi.fn();
    const fetchMock = installTargetApi({ currentTrackId: null });
    render(<LearningTargetSelector onSelected={onSelected} />);
    const select = await screen.findByRole('combobox', { name: '考试类别' });

    fireEvent.change(select, { target: { value: 'target-a' } });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/personalization/learning-target'),
      expect.objectContaining({
        method: 'PUT',
        body: expect.stringContaining('"exam_track_id":"track-a"'),
      }),
    ));
    expect(onSelected).toHaveBeenCalledWith(expect.objectContaining({
      target_id: 'target-a',
      exam_track_id: 'track-a',
    }));
  });

  it('persists the selected exam track and disables changes while saving', async () => {
    const save = deferred();
    const fetchMock = installTargetApi({ saveRequest: () => save.promise });
    render(<LearningTargetSelector />);
    const select = await screen.findByRole('combobox', { name: '考试类别' });

    fireEvent.change(select, { target: { value: 'target-b' } });

    expect(select).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/personalization/learning-target'),
      expect.objectContaining({
        method: 'PUT',
        body: expect.stringContaining('"exam_track_id":"track-b"'),
      }),
    );

    await act(async () => {
      save.resolve(response({ target: { exam_track_id: 'track-b' } }));
      await save.promise;
    });
    expect(select).not.toBeDisabled();
  });

  it('reports a successful save without navigating away', async () => {
    const onSaved = vi.fn();
    const originalLocation = window.location.href;
    installTargetApi();
    render(<LearningTargetSelector onSaved={onSaved} />);
    const select = await screen.findByRole('combobox', { name: '考试类别' });

    fireEvent.change(select, { target: { value: 'target-b' } });

    expect(await screen.findByRole('status')).toHaveTextContent('考试类别已更新');
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({
      target_id: 'target-b',
      exam_track_id: 'track-b',
      target: { exam_track_id: 'track-b' },
    }));
    expect(window.location.href).toBe(originalLocation);
  });

  it('opens the selected path after saving a new target', async () => {
    const onSelected = vi.fn();
    installTargetApi();
    render(<LearningTargetSelector variant="menu" onSelected={onSelected} />);

    fireEvent.click(await screen.findByRole('menuitemradio', {
      name: '中西医结合执业医师资格考试',
    }));

    await waitFor(() => expect(onSelected).toHaveBeenCalledWith(expect.objectContaining({
      target_id: 'target-b',
      exam_track_id: 'track-b',
      target: { exam_track_id: 'track-b' },
    })));
  });

  it('opens the current target path without saving it again', async () => {
    const onSelected = vi.fn();
    const fetchMock = installTargetApi();
    render(<LearningTargetSelector variant="menu" onSelected={onSelected} />);

    fireEvent.click(await screen.findByRole('menuitemradio', {
      name: '中医执业医师资格考试',
    }));

    expect(onSelected).toHaveBeenCalledWith(expect.objectContaining({
      target_id: 'target-a',
      exam_track_id: 'track-a',
    }));
    expect(fetchMock.mock.calls.filter(([, options = {}]) => options.method === 'PUT')).toHaveLength(0);
  });

  it('does not roll back a persisted selection when onSaved throws', async () => {
    const onSaved = vi.fn(() => {
      throw new Error('消费方回调失败');
    });
    installTargetApi();
    render(<LearningTargetSelector onSaved={onSaved} />);
    const select = await screen.findByRole('combobox', { name: '考试类别' });

    fireEvent.change(select, { target: { value: 'target-b' } });

    expect(await screen.findByRole('status')).toHaveTextContent('考试类别已更新');
    expect(select).toHaveValue('target-b');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('restores the previous selection and exposes the save error', async () => {
    installTargetApi({
      saveRequest: () => Promise.resolve(response({ detail: '目标保存失败' }, false, 500)),
    });
    render(<LearningTargetSelector />);
    const select = await screen.findByRole('combobox', { name: '考试类别' });

    fireEvent.change(select, { target: { value: 'target-b' } });

    expect(await screen.findByRole('alert')).toHaveTextContent('目标保存失败');
    expect(select).toHaveValue('target-a');
  });

  it('keeps load failures local and can retry them', async () => {
    installTargetApi({ catalogFailures: 1 });
    render(
      <section aria-label="父页面">
        <h1>学习路径</h1>
        <LearningTargetSelector />
      </section>,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('资格考试目录加载失败');
    expect(screen.getByRole('heading', { name: '学习路径' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重试加载考试类别' }));

    const select = await screen.findByRole('combobox', { name: '考试类别' });
    expect(select).toHaveValue('target-a');
  });

  it('ignores rapid repeat changes until the active save settles', async () => {
    const save = deferred();
    const fetchMock = installTargetApi({ saveRequest: () => save.promise });
    render(<LearningTargetSelector />);
    const select = await screen.findByRole('combobox', { name: '考试类别' });

    fireEvent.change(select, { target: { value: 'target-b' } });
    fireEvent.change(select, { target: { value: 'target-c' } });

    const saveCalls = fetchMock.mock.calls.filter(([, options]) => options?.method === 'PUT');
    expect(saveCalls).toHaveLength(1);
    expect(select).toHaveValue('target-b');

    await act(async () => {
      save.resolve(response({ target: { exam_track_id: 'track-b' } }));
      await save.promise;
    });
    await waitFor(() => expect(select).not.toBeDisabled());
  });
});

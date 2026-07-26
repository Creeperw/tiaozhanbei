import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import HomePage from './HomePage';

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
];

function response(payload, ok = true, status = 200) {
  return { ok, status, text: async () => JSON.stringify(payload) };
}

function installLearningTargetApi() {
  vi.stubGlobal('fetch', vi.fn((url) => {
    const path = String(url);
    if (path.endsWith('/qualification-targets')) {
      return Promise.resolve(response({ items: qualificationTargets }));
    }
    if (path.endsWith('/personalization/learning-target')) {
      return Promise.resolve(response({ target: { exam_track_id: 'track-a' } }));
    }
    throw new Error(`Unexpected request: ${path}`);
  }));
}

function installMotionPreference(reduced = false) {
  vi.stubGlobal('matchMedia', vi.fn(() => ({
    matches: reduced,
    media: '(prefers-reduced-motion: reduce)',
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
}

describe('HomePage', () => {
  beforeEach(() => {
    installLearningTargetApi();
    installMotionPreference();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('renders the platform headline and the real learning target selector', async () => {
    render(<HomePage onNavigate={vi.fn()} />);

    expect(screen.getByRole('heading', {
      name: '多智能体协同，让中医药学习更高效',
    })).toBeInTheDocument();
    const selector = await screen.findByRole('combobox', { name: '学习目标' });
    expect(selector).toHaveValue('target-a');
    expect(screen.getByRole('option', { name: '中西医结合执业医师资格考试' })).toBeInTheDocument();
  });

  it('renders the core video with autoplay-safe presentation attributes', () => {
    const { container } = render(<HomePage onNavigate={vi.fn()} />);
    const video = container.querySelector('video');

    expect(video).toBeInTheDocument();
    expect(video).toHaveAttribute('src', '/design-images/home/platform-agents.mp4');
    expect(video).toHaveAttribute('preload', 'metadata');
    expect(video).toHaveProperty('autoplay', true);
    expect(video).toHaveProperty('muted', true);
    expect(video).toHaveProperty('loop', true);
    expect(video).toHaveProperty('playsInline', true);
    expect(video).toHaveProperty('controls', false);
  });

  it('routes both hero calls to action to their intended modules', () => {
    const onNavigate = vi.fn();
    render(<HomePage onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole('button', { name: '开始学习路径' }));
    expect(onNavigate).toHaveBeenLastCalledWith({ page: 'learning-path', params: {} });

    fireEvent.click(screen.getByRole('button', { name: '了解多智能体如何协同' }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'assistant',
      params: { newConversation: true },
    });
  });

  it.each([
    ['多智能体协同', { page: 'assistant', params: {} }],
    ['个性化学习路径', { page: 'learning-path', params: {} }],
    ['知识图谱驱动', { page: 'knowledge', params: { view: 'atlas' } }],
    ['数据驱动成长', { page: 'personalization', params: {} }],
  ])('routes the %s capability card', (name, intent) => {
    const onNavigate = vi.fn();
    render(<HomePage onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${name}`) }));
    expect(onNavigate).toHaveBeenCalledWith(intent);
  });

  it('replaces a failed video with a silent visual fallback', () => {
    const { container } = render(<HomePage onNavigate={vi.fn()} />);

    fireEvent.error(container.querySelector('video'));

    expect(container.querySelector('video')).not.toBeInTheDocument();
    expect(screen.getByTestId('platform-video-fallback')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('does not autoplay or loop and pauses once loaded when reduced motion is preferred', async () => {
    installMotionPreference(true);
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    const { container } = render(<HomePage onNavigate={vi.fn()} />);
    const video = container.querySelector('video');

    expect(video).toHaveProperty('autoplay', false);
    expect(video).toHaveProperty('loop', false);
    fireEvent.loadedData(video);

    await waitFor(() => expect(pause).toHaveBeenCalled());
  });
});

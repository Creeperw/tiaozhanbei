import React from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
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

  it('renders the platform headline without duplicating the global learning target selector', () => {
    render(<HomePage onNavigate={vi.fn()} />);

    expect(screen.getByRole('heading', {
      name: '多智能体协同，让中医药学习更高效',
    })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '学习目标' })).not.toBeInTheDocument();
  });

  it('renders the core video with autoplay-safe presentation attributes', () => {
    const { container } = render(<HomePage onNavigate={vi.fn()} />);
    const video = container.querySelector('video');

    expect(video).toBeInTheDocument();
    expect(video).toHaveAttribute('src', '/platform-assets/home/platform-agents.mp4');
    expect(video).toHaveAttribute('preload', 'metadata');
    expect(video).toHaveProperty('autoplay', true);
    expect(video).toHaveProperty('muted', true);
    expect(video).toHaveProperty('loop', true);
    expect(video).toHaveProperty('playsInline', true);
    expect(video).toHaveProperty('controls', false);
  });

  it('routes the hero call to action to the learning path', () => {
    const onNavigate = vi.fn();
    render(<HomePage onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole('button', { name: '开始学习路径' }));
    expect(onNavigate).toHaveBeenLastCalledWith({ page: 'learning-path', params: {} });
  });

  it.each([
    ['多智能体协同', 'multi-agent'],
    ['个性化学习路径', 'learning-path'],
    ['知识图谱驱动', 'knowledge-graph'],
    ['数据驱动成长', 'data-growth'],
  ])('routes the %s capability card', (name, intent) => {
    const onNavigate = vi.fn();
    render(<HomePage onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${name}`) }));
    expect(onNavigate).toHaveBeenCalledWith({
      page: 'capability-detail',
      params: { capability: intent },
    });
  });

  it('replaces a failed video with a silent visual fallback', () => {
    const { container } = render(<HomePage onNavigate={vi.fn()} />);

    fireEvent.error(container.querySelector('video'));

    expect(container.querySelector('video')).not.toBeInTheDocument();
    expect(screen.getByTestId('platform-video-fallback')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('replaces a video blocked by the browser playback policy with the visual fallback', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockRejectedValueOnce(
      new Error('Autoplay is blocked'),
    );
    const { container } = render(<HomePage onNavigate={vi.fn()} />);

    fireEvent.loadedData(container.querySelector('video'));

    expect(await screen.findByTestId('platform-video-fallback')).toBeInTheDocument();
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

  it('uses the broadly supported H.264 video served by the backend', () => {
    const media = readFileSync(resolve(
      process.cwd(),
      '../../backend/competition_app/static/platform-assets/home/platform-agents.mp4',
    ));
    const containerMarkers = media.toString('latin1');

    expect(containerMarkers).toContain('avc1');
    expect(containerMarkers).not.toContain('hvc1');
  });

  it('uses accessible contrast for capability descriptions', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/components/PlatformHome.css'), 'utf8');

    expect(css).toMatch(
      /\.platform-home__capability-copy > span\s*\{[^}]*color:\s*#53635d;/,
    );
  });
});

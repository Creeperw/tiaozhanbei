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

  it('renders the homepage background video with autoplay-safe presentation attributes', () => {
    const { container } = render(<HomePage onNavigate={vi.fn()} />);
    const video = container.querySelector('video');

    expect(video).toBeInTheDocument();
    expect(video).toHaveAttribute('src', '/platform-assets/home/platform-agents.mp4?v=20260727');
    expect(video).toHaveAttribute('preload', 'metadata');
    expect(video).toHaveProperty('autoplay', true);
    expect(video).toHaveProperty('muted', true);
    expect(video).toHaveProperty('loop', true);
    expect(video).toHaveProperty('playsInline', true);
    expect(video).toHaveProperty('controls', false);
  });

  it('uses long progressive fades to hide every video edge', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/components/PlatformHome.css'), 'utf8');

    expect(css).toMatch(
      /linear-gradient\(90deg,\s*transparent 0%,\s*rgba\(0, 0, 0, 0\.16\) 9%,\s*#000 29%,\s*#000 62%,\s*rgba\(0, 0, 0, 0\.55\) 76%,\s*rgba\(0, 0, 0, 0\.12\) 90%,\s*transparent 100%\)/,
    );
    expect(css).toMatch(
      /linear-gradient\(180deg,\s*transparent 0%,\s*rgba\(0, 0, 0, 0\.16\) 6%,\s*#000 18%,\s*#000 58%,\s*rgba\(0, 0, 0, 0\.5\) 72%,\s*rgba\(0, 0, 0, 0\.1\) 88%,\s*transparent 100%\)/,
    );
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

  it('uses the requested H.264 homepage video served by the backend', () => {
    const media = readFileSync(resolve(
      process.cwd(),
      '../../backend/competition_app/static/platform-assets/home/platform-agents.mp4',
    ));
    const containerMarkers = media.toString('latin1');
    const metadataOffset = containerMarkers.indexOf('moov');
    const mediaDataOffset = containerMarkers.indexOf('mdat');

    expect(metadataOffset).toBeGreaterThan(0);
    expect(metadataOffset).toBeLessThan(mediaDataOffset);
    expect(containerMarkers).toContain('avc1');
    expect(containerMarkers).not.toContain('hvc1');
  });

  it('keeps the desktop video compact at a 16:9 ratio and aligned near the hero top', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/components/PlatformHome.css'), 'utf8');

    expect(css).toMatch(
      /\.platform-home__video,\s*\.platform-home__video-fallback\s*\{[^}]*height:\s*auto;[^}]*aspect-ratio:\s*16\s*\/\s*9;/,
    );
    expect(css).toMatch(
      /\.platform-home__visual\s*\{[^}]*align-items:\s*flex-start;/,
    );
    expect(css).toMatch(
      /\.platform-home__video\s*\{[^}]*object-position:\s*center top;/,
    );
  });

  it('moves the desktop hero content upward without shifting the mobile layout', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/components/PlatformHome.css'), 'utf8');

    expect(css).toMatch(
      /\.platform-home\s*\{[^}]*padding:\s*clamp\(8px, 1vw, 14px\) clamp\(20px, 4vw, 64px\) 42px;/,
    );
    expect(css).toMatch(
      /\.platform-home__hero\s*\{[^}]*min-height:\s*460px;/,
    );
    expect(css).toMatch(
      /\.platform-home__copy\s*\{[^}]*transform:\s*translateY\(-28px\);/,
    );
    expect(css).toMatch(
      /\.platform-home__capabilities\s*\{[^}]*margin-top:\s*8px;/,
    );
    expect(css).toMatch(
      /\.platform-home__capabilities\s*\{[^}]*background:[^}]*linear-gradient\(90deg, transparent,[^}]*top[^}]*linear-gradient\(90deg, transparent,[^}]*bottom/s,
    );
    expect(css).toMatch(
      /\.platform-home__capability\s*\{[^}]*border:\s*0;[^}]*border-radius:\s*0;[^}]*background:\s*transparent;[^}]*box-shadow:\s*none;/,
    );
    expect(css).toMatch(
      /\.platform-home__capability \+ \.platform-home__capability::before\s*\{[^}]*background:\s*linear-gradient\(180deg, transparent,[^}]*transparent\);/,
    );
    expect(css).toMatch(
      /@media \(max-width: 820px\)[\s\S]*?\.platform-home__copy\s*\{[^}]*transform:\s*none;/,
    );
  });

  it('adapts the fading capability dividers to two-column and single-column layouts', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/components/PlatformHome.css'), 'utf8');

    expect(css).toMatch(
      /@media \(max-width: 1120px\)[\s\S]*?\.platform-home__capability:nth-child\(odd\)::before\s*\{[^}]*content:\s*none;/,
    );
    expect(css).toMatch(
      /@media \(max-width: 1120px\)[\s\S]*?\.platform-home__capability:nth-child\(n \+ 3\)::after\s*\{[^}]*linear-gradient\(90deg, transparent,[^}]*transparent\);/,
    );
    expect(css).toMatch(
      /@media \(max-width: 560px\)[\s\S]*?\.platform-home__capability \+ \.platform-home__capability::before\s*\{[^}]*background:\s*linear-gradient\(90deg, transparent,[^}]*transparent\);/,
    );
    expect(css).toMatch(
      /@media \(max-width: 560px\)[\s\S]*?\.platform-home__capability::after\s*\{[^}]*content:\s*none;/,
    );
  });

  it('lets the headline wrap before it can overlap the video at medium desktop widths', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/components/PlatformHome.css'), 'utf8');

    expect(css).toMatch(
      /@media \(max-width: 1120px\)[\s\S]*?\.platform-home__headline h1\s*\{[^}]*width:\s*auto;/,
    );
  });

  it('uses accessible contrast for capability descriptions', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/components/PlatformHome.css'), 'utf8');

    expect(css).toMatch(
      /\.platform-home__capability-copy > span\s*\{[^}]*color:\s*#53635d;/,
    );
  });
});

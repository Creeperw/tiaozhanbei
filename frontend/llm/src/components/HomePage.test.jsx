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

function installLearningTargetApi({ savedTarget = { exam_track_id: 'track-a' }, saveOk = true } = {}) {
  const fetchMock = vi.fn((url, options = {}) => {
    const path = String(url);
    if (path.endsWith('/qualification-targets')) {
      return Promise.resolve(response({ items: qualificationTargets }));
    }
    if (path.endsWith('/personalization/learning-target')) {
      if (options.method === 'PUT') {
        const body = JSON.parse(options.body);
        return Promise.resolve(response(
          saveOk ? { target: { exam_track_id: body.exam_track_id } } : { detail: '保存失败' },
          saveOk,
          saveOk ? 200 : 500,
        ));
      }
      return Promise.resolve(response({ target: savedTarget }));
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
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
      name: '多智能体助力学习，让中医学习与考证更高效',
    })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '考试类别' })).not.toBeInTheDocument();
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

  it('opens the login page instead of loading a learning target for an unauthenticated visitor', () => {
    const onLoginRequested = vi.fn();
    render(<HomePage currentUser={null} onNavigate={vi.fn()} onLoginRequested={onLoginRequested} />);

    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));

    expect(onLoginRequested).toHaveBeenCalledOnce();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('always asks users to confirm the exam category before entering the learning path', async () => {
    const onNavigate = vi.fn();
    render(<HomePage onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));
    expect(await screen.findByRole('dialog', { name: '选择资格考试' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '中医执业医师资格考试' })).toHaveAttribute('aria-checked', 'true');
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it('asks first-time learners to choose and save a qualification target before entering', async () => {
    const fetchMock = installLearningTargetApi({ savedTarget: null });
    const onNavigate = vi.fn();
    const targetChanged = vi.fn();
    window.addEventListener('shizhen:learning-target-changed', targetChanged);
    render(<HomePage onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));

    expect(await screen.findByRole('dialog', { name: '选择资格考试' })).toBeInTheDocument();
    expect(onNavigate).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole('radio', { name: '中西医结合执业医师资格考试' }));
    fireEvent.click(screen.getByRole('button', { name: '确认并开始学习' }));

    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith({ page: 'learning-path', params: {} }));
    const saveRequest = fetchMock.mock.calls.find(([, options]) => options?.method === 'PUT');
    expect(JSON.parse(saveRequest[1].body)).toMatchObject({ exam_track_id: 'track-b' });
    expect(targetChanged).toHaveBeenCalledWith(expect.objectContaining({
      detail: expect.objectContaining({ exam_track_id: 'track-b' }),
    }));
    expect(screen.queryByRole('dialog', { name: '选择资格考试' })).not.toBeInTheDocument();
    window.removeEventListener('shizhen:learning-target-changed', targetChanged);
  });

  it('keeps the first-time target dialog open when saving fails', async () => {
    installLearningTargetApi({ savedTarget: null, saveOk: false });
    const onNavigate = vi.fn();
    render(<HomePage onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));
    await screen.findByRole('radio', { name: '中医执业医师资格考试' });
    fireEvent.click(screen.getByRole('button', { name: '确认并开始学习' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('保存失败');
    expect(screen.getByRole('dialog', { name: '选择资格考试' })).toBeInTheDocument();
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it('keeps the hero focused on learning without a duplicate assistant action', () => {
    const { container } = render(<HomePage onNavigate={vi.fn()} />);
    const learningAction = screen.getByRole('button', { name: '开始学习' });

    expect(learningAction).toBeInTheDocument();
    expect(learningAction.textContent).toContain('开始学习');
    expect(screen.queryByRole('button', { name: '多智能体助教' })).not.toBeInTheDocument();

    expect(container.querySelectorAll('feColorMatrix[values*="25 -9"]')).toHaveLength(0);
    expect(container.querySelectorAll('feComposite[operator="atop"]')).toHaveLength(0);
    expect(container.querySelector('.text-morph')).toBeNull();
  });

  it.each([
    ['多智能体协同', 'multi-agent'],
    ['个性化学习路径', 'learning-path'],
    ['专项训练', 'knowledge-graph'],
    ['人机协同', 'human-collaboration'],
  ])('routes the %s capability card', (name, intent) => {
    const onNavigate = vi.fn();
    render(<HomePage onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${name}`) }));
    expect(onNavigate).toHaveBeenCalledWith({
      page: 'capability-detail',
      params: { capability: intent },
    });
  });

  it('explains the four innovation-oriented homepage capabilities', () => {
    render(<HomePage onNavigate={vi.fn()} />);

    [
      '学情诊断、学习规划、专家等六大智能体协同处理学习任务，按需分工，每项建议都关联证据、约束与审核结果，贯穿学习全流程完成诊断、答疑与学习支持。',
      '结合考试目标、阶段计划与近期答题表现，在前置知识、复习到期和可用时间等约束下，生成可解释、可调整的学习路径。',
      '围绕薄弱点提供章节练习、错题变式、病例训练与试卷生成，把诊断结论转化为可完成的训练任务。',
      '学生可确认目标与时间、调整难度或更换资源，并查看推荐依据；任务反馈与完成效果共同进入下一轮路径和资源匹配。',
    ].forEach((description) => {
      expect(screen.getByText(description)).toBeInTheDocument();
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
      /@media \(max-width: 1120px\)[\s\S]*?\.platform-home__title-reveal\s*\{[^}]*width:\s*auto;/,
    );
  });

  it('uses accessible contrast for capability descriptions', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/components/PlatformHome.css'), 'utf8');

    expect(css).toMatch(
      /\.platform-home__capability-copy > span\s*\{[^}]*color:\s*#53635d;/,
    );
  });
});

import React from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { cwd } from 'node:process';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import CompactAssistant from './CompactAssistant';
import {
  createAssistantSession,
  listAssistantSessions,
  loadAssistantMessages,
  streamAssistantMessage,
} from '../chatSessionClient';

vi.mock('../chatSessionClient', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    createAssistantSession: vi.fn(),
    listAssistantSessions: vi.fn(),
    loadAssistantMessages: vi.fn(),
    streamAssistantMessage: vi.fn(),
  };
});

describe('CompactAssistant', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    listAssistantSessions.mockResolvedValue([]);
    loadAssistantMessages.mockResolvedValue([]);
    createAssistantSession.mockResolvedValue({ id: 'session-new', title: '新对话' });
    streamAssistantMessage.mockImplementation(async (_sessionId, _content, { onUpdate }) => {
      onUpdate('这是来自真实流式会话的回答。');
      return '这是来自真实流式会话的回答。';
    });
  });

  it('defaults to a new conversation with a contextual greeting and four window controls', async () => {
    listAssistantSessions.mockResolvedValue([{ id: 'session-saved', title: '方剂学复习' }]);
    const onOpenFull = vi.fn();
    render(
      <CompactAssistant
        currentUser="admin"
        dailyGoal="完成方剂学第3章"
        dailyFocus="重点掌握方剂证型"
        onOpenFull={onOpenFull}
      />,
    );

    expect(await screen.findByText('你好，admin！今天的学习目标是完成方剂学第3章，重点掌握方剂证型。有什么问题可以随时问我。')).toBeInTheDocument();
    expect(loadAssistantMessages).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '查看历史对话' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新建对话' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '折叠智能助教' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '打开完整智能助教' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '打开完整智能助教' }));
    expect(onOpenFull).toHaveBeenCalledWith(null);
  });

  it('renders the collapsed assistant as one framed control instead of nested frames', () => {
    render(<CompactAssistant initiallyCollapsed onOpenFull={vi.fn()} />);

    const shell = screen.getByLabelText('常驻智能助教');
    const restore = screen.getByRole('button', { name: '展开智能助教' });
    const stylesheet = readFileSync(resolve(cwd(), 'src/index.css'), 'utf8');
    const collapsedShellRule = stylesheet.match(/\.compact-assistant\.is-collapsed\[data-floating="true"\]\s*\{([^}]+)\}/)?.[1] || '';

    expect(shell).toContainElement(restore);
    expect(collapsedShellRule).toContain('border: 0;');
    expect(collapsedShellRule).toContain('background: transparent;');
    expect(collapsedShellRule).toContain('box-shadow: none;');
  });

  it('uses the Li Shizhen character as the collapsed restore control', () => {
    render(<CompactAssistant initiallyCollapsed onOpenFull={vi.fn()} />);

    const restore = screen.getByRole('button', { name: '展开智能助教' });
    const character = within(restore).getByTestId('lizhizhen-assistant-character');
    const centerImage = character.querySelector('img[data-pose="center"]');

    expect(character).toHaveAttribute('data-pose', 'center');
    expect(centerImage).toHaveAttribute('src', '/assistant-character/lizhizhen-center-cutout.png');
    expect(restore.querySelectorAll('svg')).toHaveLength(0);
  });

  it('loads tightly cropped alpha cutouts instead of the old loose-canvas images', () => {
    render(<CompactAssistant initiallyCollapsed onOpenFull={vi.fn()} />);

    const character = screen.getByTestId('lizhizhen-assistant-character');
    const images = [...character.querySelectorAll('img')];

    expect(images).toHaveLength(3);
    images.forEach((image) => {
      expect(image.getAttribute('src')).toMatch(/-cutout\.png$/);

      const asset = readFileSync(resolve(cwd(), 'public', image.getAttribute('src').slice(1)));
      expect(asset.readUInt32BE(16)).toBeLessThanOrEqual(240);
      expect(asset.readUInt32BE(20)).toBeLessThanOrEqual(470);
      expect(asset[25]).toBe(6);
    });
  });

  it('keeps the collapsed pointer target close to the visible character body', () => {
    const stylesheet = readFileSync(resolve(cwd(), 'src/index.css'), 'utf8');
    const restoreRule = stylesheet.match(/\.compact-assistant\.is-collapsed\[data-floating="true"\] \.compact-assistant__restore\s*\{([^}]+)\}/)?.[1] || '';

    expect(restoreRule).toContain('width: 58px;');
    expect(restoreRule).toContain('background: transparent;');
    expect(restoreRule).toContain('box-shadow: none;');
  });

  it('keeps a robot fallback when the character image cannot load', () => {
    render(<CompactAssistant initiallyCollapsed onOpenFull={vi.fn()} />);

    const restore = screen.getByRole('button', { name: '展开智能助教' });
    const centerImage = within(restore).getByTestId('lizhizhen-assistant-character')
      .querySelector('img[data-pose="center"]');
    fireEvent.error(centerImage);

    expect(within(restore).getByTestId('assistant-character-fallback')).toBeInTheDocument();
    expect(restore).toBeEnabled();
  });

  it('turns the collapsed character toward the drag direction and faces forward after release', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 700 });
    render(<CompactAssistant initiallyCollapsed onOpenFull={vi.fn()} />);

    const floatingAssistant = screen.getByLabelText('常驻智能助教');
    const restore = screen.getByRole('button', { name: '展开智能助教' });
    const character = within(restore).getByTestId('lizhizhen-assistant-character');
    vi.spyOn(floatingAssistant, 'getBoundingClientRect').mockReturnValue({
      left: 820,
      top: 520,
      right: 920,
      bottom: 650,
      width: 100,
      height: 130,
      x: 820,
      y: 520,
      toJSON: () => ({}),
    });

    fireEvent.pointerDown(restore, { clientX: 870, clientY: 580, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 790, clientY: 580, pointerId: 1 });
    expect(character).toHaveAttribute('data-pose', 'left');

    fireEvent.pointerMove(window, { clientX: 950, clientY: 580, pointerId: 1 });
    expect(character).toHaveAttribute('data-pose', 'right');

    fireEvent.pointerUp(window, { clientX: 950, clientY: 580, pointerId: 1 });
    expect(character).toHaveAttribute('data-pose', 'center');
  });

  it('defines idle, interactive, dragging, and reduced-motion character states', () => {
    const stylesheet = readFileSync(resolve(cwd(), 'src/index.css'), 'utf8');

    expect(stylesheet).toContain('@keyframes assistant-character-idle');
    expect(stylesheet).toContain('.compact-assistant__restore:hover .compact-assistant__character-figure');
    expect(stylesheet).toContain('.compact-assistant.is-collapsed.is-dragging[data-floating="true"] .compact-assistant__character-figure');
    expect(stylesheet).toMatch(/@media \(prefers-reduced-motion: reduce\)[\s\S]*\.compact-assistant__character-figure[\s\S]*animation: none/);
  });

  it('opens a history popover and switches to the selected real session', async () => {
    listAssistantSessions.mockResolvedValue([
      { id: 'session-saved', title: '方剂学复习', updated_at: '2026-07-18T08:00:00Z' },
    ]);
    loadAssistantMessages.mockResolvedValue([
      { id: 1, role: 'assistant', content: '<think>略</think>继续学习方剂学。' },
    ]);
    const onOpenFull = vi.fn();
    render(<CompactAssistant currentUser="admin" onOpenFull={onOpenFull} />);

    await screen.findByText(/今天可以继续完成你的学习计划/);
    fireEvent.click(screen.getByRole('button', { name: '查看历史对话' }));
    fireEvent.click(await screen.findByRole('button', { name: /方剂学复习/ }));

    expect(await screen.findByText('继续学习方剂学。')).toBeInTheDocument();
    expect(loadAssistantMessages).toHaveBeenCalledWith('session-saved');
    fireEvent.click(screen.getByRole('button', { name: '打开完整智能助教' }));
    expect(onOpenFull).toHaveBeenCalledWith('session-saved');
  });

  it('starts a fresh conversation without deleting history and keeps it through collapse restore', async () => {
    listAssistantSessions.mockResolvedValue([{ id: 'session-saved', title: '方剂学复习' }]);
    loadAssistantMessages.mockResolvedValue([{ id: 1, role: 'assistant', content: '历史回答' }]);
    render(<CompactAssistant currentUser="admin" preferredSessionId="session-saved" onOpenFull={vi.fn()} />);

    expect(await screen.findByText('历史回答')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '新建对话' }));
    expect(await screen.findByText(/今天可以继续完成你的学习计划/)).toBeInTheDocument();
    expect(listAssistantSessions).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole('button', { name: '折叠智能助教' }));
    expect(screen.getByLabelText('常驻智能助教')).toHaveAttribute('data-state', 'collapsed');
    fireEvent.click(screen.getByRole('button', { name: '展开智能助教' }));
    expect(screen.getByLabelText('常驻智能助教')).toHaveAttribute('data-state', 'workspace');
  });

  it('moves the collapsed assistant as a floating window without opening it after a drag', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 700 });
    const onCollapsedChange = vi.fn();
    render(
      <CompactAssistant
        currentUser="admin"
        initiallyCollapsed
        onCollapsedChange={onCollapsedChange}
        onOpenFull={vi.fn()}
      />,
    );

    const floatingAssistant = screen.getByLabelText('常驻智能助教');
    const restoreButton = screen.getByRole('button', { name: '展开智能助教' });
    vi.spyOn(floatingAssistant, 'getBoundingClientRect').mockReturnValue({
      left: 900,
      top: 600,
      right: 956,
      bottom: 656,
      width: 56,
      height: 56,
      x: 900,
      y: 600,
      toJSON: () => ({}),
    });

    expect(floatingAssistant).toHaveAttribute('data-floating', 'true');
    fireEvent.pointerDown(restoreButton, { clientX: 920, clientY: 620, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 500, clientY: 100, pointerId: 1 });
    fireEvent.pointerUp(window, { clientX: 500, clientY: 100, pointerId: 1 });

    expect(floatingAssistant).toHaveStyle({ left: '480px', top: '80px' });
    fireEvent.click(restoreButton);
    expect(floatingAssistant).toHaveAttribute('data-state', 'collapsed');
    expect(onCollapsedChange).not.toHaveBeenCalled();

    fireEvent.click(restoreButton);
    const expandedAssistant = screen.getByLabelText('常驻智能助教');
    expect(expandedAssistant).toHaveAttribute('data-state', 'workspace');
    expect(expandedAssistant).toHaveAttribute('data-floating', 'true');
    expect(expandedAssistant).toHaveStyle({ left: '480px', top: '80px' });
    expect(onCollapsedChange).toHaveBeenCalledWith(false);
  });

  it('drags the expanded assistant from its header', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 700 });
    const onFloatingDockChange = vi.fn();
    const { container } = render(
      <CompactAssistant
        currentUser="admin"
        onFloatingDockChange={onFloatingDockChange}
        onOpenFull={vi.fn()}
      />,
    );

    const floatingAssistant = screen.getByLabelText('常驻智能助教');
    const dragHandle = container.querySelector('.compact-assistant__header');
    vi.spyOn(floatingAssistant, 'getBoundingClientRect').mockReturnValue({
      left: 650,
      top: 150,
      right: 970,
      bottom: 650,
      width: 320,
      height: 500,
      x: 650,
      y: 150,
      toJSON: () => ({}),
    });

    expect(floatingAssistant).toHaveAttribute('data-floating', 'true');
    fireEvent.pointerDown(dragHandle, { clientX: 670, clientY: 170, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 500, clientY: 100, pointerId: 1 });
    fireEvent.pointerUp(window, { clientX: 500, clientY: 100, pointerId: 1 });

    expect(floatingAssistant).toHaveStyle({ left: '480px', top: '80px' });
    expect(onFloatingDockChange).toHaveBeenLastCalledWith(false);
  });

  it('can stay contained in a workspace without covering adjacent controls', () => {
    render(<CompactAssistant currentUser="admin" floating={false} onOpenFull={vi.fn()} />);

    expect(screen.getByLabelText('常驻智能助教')).toHaveAttribute('data-floating', 'false');
  });

  it('keeps the draggable assistant inside the visible viewport', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 700 });
    const onFloatingDockChange = vi.fn();
    render(
      <CompactAssistant
        initiallyCollapsed
        onFloatingDockChange={onFloatingDockChange}
        onOpenFull={vi.fn()}
      />,
    );

    const floatingAssistant = screen.getByLabelText('常驻智能助教');
    const restoreButton = screen.getByRole('button', { name: '展开智能助教' });
    vi.spyOn(floatingAssistant, 'getBoundingClientRect').mockReturnValue({
      left: 900,
      top: 600,
      right: 956,
      bottom: 656,
      width: 56,
      height: 56,
      x: 900,
      y: 600,
      toJSON: () => ({}),
    });

    fireEvent.pointerDown(restoreButton, { clientX: 920, clientY: 620, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 1400, clientY: 1000, pointerId: 1 });
    fireEvent.pointerUp(window, { clientX: 1400, clientY: 1000, pointerId: 1 });

    expect(floatingAssistant).toHaveStyle({ left: '936px', top: '636px' });
    expect(onFloatingDockChange).toHaveBeenLastCalledWith(true);
    fireEvent.click(restoreButton);
    fireEvent.click(restoreButton);
    expect(screen.getByLabelText('常驻智能助教')).toHaveStyle({ left: '672px', top: '132px' });
    fireEvent.click(screen.getByRole('button', { name: '折叠智能助教' }));
    expect(screen.getByLabelText('常驻智能助教')).toHaveStyle({ left: '936px', top: '636px' });
  });

  it('uses one contextual workbench with reusable quick actions', async () => {
    render(
      <CompactAssistant
        currentUser="admin"
        contextLabel="感冒 · 辨证论治"
        onOpenFull={vi.fn()}
      />,
    );

    await screen.findByText(/今天可以继续完成你的学习计划/);
    expect(screen.getByLabelText('常驻智能助教')).toHaveAttribute('data-state', 'workspace');
    expect(screen.getByText('感冒 · 辨证论治')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '解释当前内容' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成练习' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '对比资料' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '总结重点' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '解释当前内容' }));
    expect(screen.getByRole('textbox', { name: '向智能助教提问' })).toHaveValue('请解释当前内容，并结合学习目标说明关键概念。');
  });

  it('creates a real session on first send and streams the answer in place', async () => {
    const onOpenFull = vi.fn();
    render(<CompactAssistant currentUser="admin" onOpenFull={onOpenFull} />);

    const input = await screen.findByRole('textbox', { name: '向智能助教提问' });
    fireEvent.change(input, { target: { value: '四君子汤主治什么证型？' } });
    fireEvent.click(screen.getByRole('button', { name: '发送问题' }));

    expect(await screen.findByText('这是来自真实流式会话的回答。')).toBeInTheDocument();
    expect(createAssistantSession).toHaveBeenCalledOnce();
    expect(streamAssistantMessage).toHaveBeenCalledWith(
      'session-new',
      '四君子汤主治什么证型？',
      expect.objectContaining({ onUpdate: expect.any(Function) }),
    );
    await waitFor(() => expect(localStorage.getItem('lastSessionId')).toBe('session-new'));
    fireEvent.click(screen.getByRole('button', { name: '打开完整智能助教' }));
    expect(onOpenFull).toHaveBeenCalledWith('session-new');
  });

  it('clears the pending placeholder when streaming fails', async () => {
    streamAssistantMessage.mockRejectedValueOnce(new Error('网络中断'));
    render(<CompactAssistant currentUser="admin" onOpenFull={vi.fn()} />);

    const input = await screen.findByRole('textbox', { name: '向智能助教提问' });
    fireEvent.change(input, { target: { value: '请解释阴阳' } });
    fireEvent.click(screen.getByRole('button', { name: '发送问题' }));

    expect(await screen.findByText('网络中断')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('正在思考…')).not.toBeInTheDocument());
  });

  it('shows safe summaries for communication and local repair events', async () => {
    render(
      <CompactAssistant
        onOpenFull={vi.fn()}
        executionEvents={[
          { event: 'handoff_prepared', step_id: 'diagnosis', status: 'prepared', raw_content: '不要展示的通信原文' },
          { event: 'handoff_blocked', step_id: 'planner', status: 'blocked', content: '不要展示的阻断原文' },
          { event: 'repair_planned', repair_id: 'REPAIR_1', rerun_step_ids: ['diagnosis'], status: 'planned' },
          { event: 'repair_step_started', step_id: 'diagnosis', status: 'running' },
          { event: 'repair_completed', trigger_step_id: 'audit', status: 'pass' },
          { event: 'repair_stopped', trigger_step_id: 'audit', status: 'needs_human_review' },
        ]}
      />,
    );

    expect(await screen.findByText('按需通信')).toBeInTheDocument();
    expect(screen.getByText('通信信息不足')).toBeInTheDocument();
    expect(screen.getByText('已生成局部修复链')).toBeInTheDocument();
    expect(screen.getByText('局部修复执行中')).toBeInTheDocument();
    expect(screen.getByText('局部修复完成')).toBeInTheDocument();
    expect(screen.getByText('局部修复已停止')).toBeInTheDocument();
    expect(screen.queryByText(/不要展示/)).not.toBeInTheDocument();
  });
});

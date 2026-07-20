import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import KnowledgePlanetScene from './KnowledgePlanetScene';

const nodes = [
  { membership_id: 'root', title: '方剂学' },
  { membership_id: 'next', title: '方剂证型辨析' },
];
const positions = {
  root: { x: 0, y: 0, z: 0, material: 'current', side: 'current' },
  next: { x: 1.2, y: 0.2, z: 0.7, material: 'next', side: 'future' },
};
const edges = [{ from: 'root', to: 'next', kind: 'hierarchy' }];

function fakeRenderer() {
  return {
    domElement: document.createElement('canvas'),
    setPixelRatio: vi.fn(),
    setSize: vi.fn(),
    render: vi.fn(),
    dispose: vi.fn(),
  };
}

describe('KnowledgePlanetScene', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('mounts a WebGL renderer, exposes node actions, and disposes on unmount', () => {
    const renderer = fakeRenderer();
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 17);
    const cancel = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    const onNodeClick = vi.fn();
    const onNodeDoubleClick = vi.fn();
    const { unmount } = render(
      <KnowledgePlanetScene
        nodes={nodes}
        positions={positions}
        edges={edges}
        rendererFactory={() => renderer}
        onNodeClick={onNodeClick}
        onNodeDoubleClick={onNodeDoubleClick}
      />,
    );

    expect(screen.getByLabelText('三维知识星球')).toHaveAttribute('data-renderer', 'webgl');
    expect(screen.getByLabelText('三维知识星球')).toHaveAttribute('data-tone', 'light-mint-atlas');
    const nodeButton = screen.getByRole('button', { name: /打开方剂证型辨析知识卡片/ });
    expect(nodeButton).toHaveAttribute('data-visual', 'glow-point');
    expect(nodeButton).toHaveAttribute('data-anchor', 'point-origin');
    expect(nodeButton.querySelector('small')).not.toBeInTheDocument();
    fireEvent.click(nodeButton);
    fireEvent.doubleClick(nodeButton);
    expect(onNodeClick).toHaveBeenCalledWith(nodes[1]);
    expect(onNodeDoubleClick).toHaveBeenCalledWith(nodes[1]);

    fireEvent.keyDown(nodeButton, { key: 'ArrowRight' });
    expect(onNodeDoubleClick).toHaveBeenCalledTimes(2);

    unmount();
    expect(renderer.dispose).toHaveBeenCalledOnce();
    expect(cancel).toHaveBeenCalledWith(17);
  });

  it('pauses rotation and restores the standard time view', () => {
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 7);
    const onResetView = vi.fn();
    render(
      <KnowledgePlanetScene
        nodes={nodes}
        positions={positions}
        edges={edges}
        rendererFactory={fakeRenderer}
        onResetView={onResetView}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '暂停星球旋转' }));
    expect(screen.getByLabelText('三维知识星球')).toHaveAttribute('data-paused', 'true');
    fireEvent.click(screen.getByRole('button', { name: '回到时间视角' }));
    expect(onResetView).toHaveBeenCalledOnce();
  });

  it('reports a local fallback when WebGL initialization fails', async () => {
    const onFallback = vi.fn();
    render(
      <KnowledgePlanetScene
        nodes={nodes}
        positions={positions}
        edges={edges}
        rendererFactory={() => { throw new Error('WebGL unavailable'); }}
        onFallback={onFallback}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('三维渲染不可用，已切换二维知识路径');
      expect(screen.getByLabelText('三维知识星球')).toHaveAttribute('data-renderer', 'fallback');
    });
    expect(onFallback).toHaveBeenCalledOnce();
  });

  it('falls back when the active WebGL render loop fails', async () => {
    let frameCallback;
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frameCallback = callback;
      return 23;
    });
    const renderer = fakeRenderer();
    renderer.render.mockImplementation(() => { throw new Error('context lost while rendering'); });
    const onFallback = vi.fn();
    render(
      <KnowledgePlanetScene
        nodes={nodes}
        positions={positions}
        edges={edges}
        rendererFactory={() => renderer}
        onFallback={onFallback}
      />,
    );

    act(() => frameCallback(16));
    await waitFor(() => expect(screen.getByLabelText('三维知识星球')).toHaveAttribute('data-renderer', 'fallback'));
    expect(onFallback).toHaveBeenCalledOnce();
    expect(renderer.dispose).toHaveBeenCalledOnce();
  });

  it('falls back when the browser reports a lost WebGL context', async () => {
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 29);
    const renderer = fakeRenderer();
    const onFallback = vi.fn();
    render(
      <KnowledgePlanetScene
        nodes={nodes}
        positions={positions}
        edges={edges}
        rendererFactory={() => renderer}
        onFallback={onFallback}
      />,
    );

    renderer.domElement.dispatchEvent(new Event('webglcontextlost', { cancelable: true }));
    await waitFor(() => expect(screen.getByLabelText('三维知识星球')).toHaveAttribute('data-renderer', 'fallback'));
    expect(onFallback).toHaveBeenCalledOnce();
  });

  it('respects reduced-motion by keeping automatic rotation still', () => {
    let frameCallback;
    vi.stubGlobal('matchMedia', vi.fn(() => ({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })));
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frameCallback = callback;
      return 31;
    });
    const renderer = fakeRenderer();
    const { unmount } = render(
      <KnowledgePlanetScene
        nodes={nodes}
        positions={positions}
        edges={edges}
        rendererFactory={() => renderer}
      />,
    );

    act(() => frameCallback(16));
    const renderedScene = renderer.render.mock.calls[0][0];
    expect(renderedScene.children[0].rotation.y).toBeCloseTo(-0.16);
    unmount();
  });
});

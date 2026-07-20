import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import LearningPathOverview from './LearningPathOverview';
import LearningPlanRail from './LearningPlanRail';

const nodes = [
  { membership_id: 'basics', title: '中医基础理论', child_count: 8, status: 'completed' },
  { membership_id: 'formula', title: '方剂学', child_count: 8, status: 'in_progress' },
  { membership_id: 'clinic', title: '中医内科学', child_count: 6, status: 'locked' },
];
const edges = [
  { from: 'basics', to: 'formula' },
  { from: 'formula', to: 'clinic' },
];

afterEach(() => window.localStorage.clear());

describe('LearningPathOverview', () => {
  it('selects on a single click and drills once on double click', () => {
    const onSelect = vi.fn();
    const onDrill = vi.fn();
    render(
      <LearningPathOverview
        nodes={nodes}
        edges={edges}
        selectedId={null}
        onSelect={onSelect}
        onDrill={onDrill}
      />,
    );

    const formula = screen.getByRole('button', { name: /选择方剂学/ });
    fireEvent.click(formula);
    expect(onSelect).toHaveBeenCalledWith(nodes[1]);

    fireEvent.doubleClick(formula);
    expect(onDrill).toHaveBeenCalledTimes(1);
    expect(onDrill).toHaveBeenCalledWith(nodes[1]);
  });

  it('renders semantic progress and dependency edges without a globe canvas', () => {
    render(
      <LearningPathOverview
        nodes={nodes}
        edges={edges}
        selectedId="formula"
        onSelect={vi.fn()}
        onDrill={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('一级知识学习路径')).toBeInTheDocument();
    expect(screen.getAllByTestId('learning-tree-edge')).toHaveLength(2);
    expect(screen.getByRole('button', { name: /选择方剂学/ })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByText('星球')).not.toBeInTheDocument();
  });

  it('removes the canvas control toolbar while preserving the current animated path', () => {
    render(
      <LearningPathOverview
        nodes={nodes}
        edges={edges}
        selectedId="formula"
        onSelect={vi.fn()}
        onDrill={vi.fn()}
      />,
    );

    const canvas = screen.getByLabelText('一级知识学习路径');
    expect(canvas).toHaveAttribute('data-scale', '1');
    expect(screen.queryByLabelText('学习路径画布控制')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '放大学习路径' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '缩小学习路径' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /选择方剂学/ })).toHaveAttribute('data-current', 'true');
    expect(screen.getAllByTestId('learning-tree-edge')[0]).toHaveClass('is-active');
    expect(document.querySelectorAll('.learning-path-overview__ribbon-line')).toHaveLength(3);
    expect(screen.getAllByTestId('learning-path-stem')).toHaveLength(3);
    expect(screen.getByRole('button', { name: /选择中医基础理论/ })).toHaveAttribute('data-stage', 'past');
    expect(screen.getByRole('button', { name: /选择方剂学/ })).toHaveAttribute('data-stage', 'current');
    expect(screen.getByRole('button', { name: /选择中医内科学/ })).toHaveAttribute('data-stage', 'future');
  });

  it('clears the selected plan when the canvas background is clicked', () => {
    const onClearSelection = vi.fn();
    render(
      <LearningPathOverview
        nodes={nodes}
        edges={edges}
        selectedId="formula"
        onSelect={vi.fn()}
        onDrill={vi.fn()}
        onClearSelection={onClearSelection}
      />,
    );

    fireEvent.click(document.querySelector('.learning-path-overview__stage'));
    expect(onClearSelection).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole('button', { name: /选择方剂学/ }));
    expect(onClearSelection).toHaveBeenCalledOnce();
  });

  it('continues panning from the current manual position on every drag', () => {
    render(
      <LearningPathOverview
        nodes={nodes}
        edges={edges}
        selectedId="formula"
        onSelect={vi.fn()}
        onDrill={vi.fn()}
      />,
    );

    const stage = document.querySelector('.learning-path-overview__stage');
    const xOffset = () => Number(document.querySelector('.learning-path-overview__viewport')
      .style.transform.match(/translate3d\((-?[\d.]+)px/)[1]);

    fireEvent.pointerDown(stage, { button: 0, clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(stage, { clientX: 124, clientY: 100, pointerId: 1 });
    fireEvent.pointerUp(stage, { pointerId: 1 });
    const afterFirstDrag = xOffset();

    fireEvent.pointerDown(stage, { button: 0, clientX: 150, clientY: 100, pointerId: 2 });
    fireEvent.pointerMove(stage, { clientX: 162, clientY: 100, pointerId: 2 });

    expect(xOffset()).toBe(afterFirstDrag + 12);
  });

  it('keeps the learning stage fixed when another node is only selected for inspection', () => {
    render(
      <LearningPathOverview
        nodes={nodes}
        edges={edges}
        selectedId="clinic"
        onSelect={vi.fn()}
        onDrill={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: /选择方剂学/ })).toHaveAttribute('data-current', 'true');
    expect(screen.getByRole('button', { name: /选择中医内科学/ })).toHaveAttribute('data-current', 'false');
    expect(screen.getByRole('button', { name: /选择中医内科学/ })).toHaveAttribute('aria-pressed', 'true');
  });

  it('uses the first unfinished official node when no explicit current status exists', () => {
    const unassessedNodes = nodes.map((node) => ({ ...node, status: 'unassessed' }));
    render(
      <LearningPathOverview
        nodes={unassessedNodes}
        edges={edges}
        selectedId="clinic"
        onSelect={vi.fn()}
        onDrill={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: /选择中医基础理论/ })).toHaveAttribute('data-current', 'true');
    expect(screen.getByRole('button', { name: /选择中医内科学/ })).toHaveAttribute('data-current', 'false');
  });

  it('keeps long first-level paths readable at the initial zoom', () => {
    const longNodes = Array.from({ length: 10 }, (_, index) => ({
      membership_id: `long-${index}`,
      title: `阶段 ${index + 1}`,
      status: index === 4 ? 'in_progress' : 'unassessed',
    }));
    const longEdges = longNodes.slice(1).map((node, index) => ({
      from: longNodes[index].membership_id,
      to: node.membership_id,
      kind: 'spine',
    }));

    render(
      <LearningPathOverview
        nodes={longNodes}
        edges={longEdges}
        selectedId={null}
        onSelect={vi.fn()}
        onDrill={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('一级知识学习路径')).toHaveAttribute('data-scale', '0.68');
  });

  it('places the first node close to the left edge on initial entry', () => {
    render(
      <LearningPathOverview
        nodes={nodes}
        edges={edges}
        selectedId={null}
        onSelect={vi.fn()}
        onDrill={vi.fn()}
      />,
    );

    const viewport = document.querySelector('.learning-path-overview__viewport');
    const firstNode = screen.getByRole('button', { name: /选择中医基础理论/ });
    const viewportX = Number(viewport.style.transform.match(/translate3d\((-?[\d.]+)px/)[1]);
    const renderedLeft = Number.parseFloat(firstNode.style.left) + viewportX;

    expect(screen.getByLabelText('一级知识学习路径')).toHaveAttribute('data-view', 'start');
    expect(renderedLeft).toBeGreaterThanOrEqual(16);
    expect(renderedLeft).toBeLessThanOrEqual(28);
  });
});

describe('LearningPlanRail', () => {
  it('shows real completion data and a local retry state', () => {
    const { rerender } = render(
      <LearningPlanRail
        node={nodes[1]}
        summary={{
          total_count: 10,
          completed_count: 6,
          incomplete_count: 4,
          average_mastery: 62,
          review_due_count: 2,
          status: 'in_progress',
        }}
        onClose={vi.fn()}
        onStartLearning={vi.fn()}
      />,
    );

    expect(screen.getByText('第 6 / 10 个知识点')).toBeInTheDocument();
    expect(screen.getByText('62%')).toBeInTheDocument();
    expect(screen.getByText('4 个待完成')).toBeInTheDocument();

    const onRetry = vi.fn();
    rerender(
      <LearningPlanRail
        node={nodes[1]}
        summary={null}
        error="学习摘要暂时不可用"
        onRetry={onRetry}
        onClose={vi.fn()}
        onStartLearning={vi.fn()}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('学习摘要暂时不可用');
    fireEvent.click(screen.getByRole('button', { name: '重试学习摘要' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

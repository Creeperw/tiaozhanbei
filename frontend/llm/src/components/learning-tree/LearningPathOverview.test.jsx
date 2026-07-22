import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import LearningPathOverview from './LearningPathOverview';
import LearningPlanRail from './LearningPlanRail';

const nodes = [
  { membership_id: 'basics', title: '中医基础理论', child_count: 8, status: 'completed', order: 1 },
  { membership_id: 'formula', title: '方剂学', child_count: 8, status: 'in_progress', order: 2, average_mastery: 62 },
  { membership_id: 'clinic', title: '中医内科学', child_count: 6, status: 'locked', order: 3 },
];
const edges = [
  { from: 'basics', to: 'formula' },
  { from: 'formula', to: 'clinic' },
];

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

    const formula = screen.getByRole('button', { name: /选择方剂学，第 2 阶段/ });
    fireEvent.click(formula);
    expect(onSelect).toHaveBeenCalledWith(nodes[1]);

    fireEvent.doubleClick(formula);
    expect(onDrill).toHaveBeenCalledTimes(1);
    expect(onDrill).toHaveBeenCalledWith(nodes[1]);
  });

  it('renders a closed ordered orbit, progress center, and sequential path segments', () => {
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
    expect(screen.getByText('中医药知识体系')).toBeInTheDocument();
    expect(screen.getByLabelText('总体学习进度 48%')).toBeInTheDocument();
    expect(screen.getAllByTestId('learning-path-orbit-segment')).toHaveLength(nodes.length);
    expect(screen.getByRole('button', { name: /选择方剂学，第 2 阶段/ })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByText('星球')).not.toBeInTheDocument();
  });

  it('marks the active stage and preserves visual phase ordering', () => {
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
    expect(canvas).toHaveAttribute('data-layout', 'orbit');
    expect(screen.getByRole('button', { name: /选择方剂学，第 2 阶段/ })).toHaveAttribute('data-current', 'true');
    expect(screen.getByRole('button', { name: /选择中医基础理论，第 1 阶段/ })).toHaveAttribute('data-stage', 'past');
    expect(screen.getByRole('button', { name: /选择方剂学，第 2 阶段/ })).toHaveAttribute('data-stage', 'current');
    expect(screen.getByRole('button', { name: /选择中医内科学，第 3 阶段/ })).toHaveAttribute('data-stage', 'future');
    expect(screen.getByRole('button', { name: /选择方剂学，第 2 阶段/ })).toHaveAttribute('data-order', '2');
  });

  it('clears the selected plan when the orbit background is clicked', () => {
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

    fireEvent.click(document.querySelector('.learning-path-orbit__stage'));
    expect(onClearSelection).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole('button', { name: /选择方剂学，第 2 阶段/ }));
    expect(onClearSelection).toHaveBeenCalledOnce();
  });

  it('orders nodes by explicit route order even when source order differs', () => {
    const reordered = [nodes[2], nodes[0], nodes[1]];
    render(
      <LearningPathOverview
        nodes={reordered}
        edges={edges}
        selectedId={null}
        onSelect={vi.fn()}
        onDrill={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: /选择中医基础理论，第 1 阶段/ })).toHaveAttribute('data-order', '1');
    expect(screen.getByRole('button', { name: /选择方剂学，第 2 阶段/ })).toHaveAttribute('data-order', '2');
    expect(screen.getByRole('button', { name: /选择中医内科学，第 3 阶段/ })).toHaveAttribute('data-order', '3');
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

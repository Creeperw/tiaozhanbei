import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import LearningStageLanding from './LearningStageLanding';
import {
  DEFAULT_LEARNING_STAGES,
  getStageLayout,
  plannedStagesFromPath,
} from './learningStageModel';
import { loadPlannedLearningPath } from '../learning-tree/learningPathApi';

vi.mock('../learning-tree/learningPathApi', () => ({
  loadPlannedLearningPath: vi.fn(),
}));

describe('LearningStageLanding', () => {
  beforeEach(() => {
    loadPlannedLearningPath.mockReset();
  });

  it.each([4, 5, 6])('renders %s backend-computed stages with an adaptive layout', (count) => {
    render(<LearningStageLanding stages={DEFAULT_LEARNING_STAGES.slice(0, count)} />);

    expect(screen.getAllByRole('button', { name: /进入.+阶段/ })).toHaveLength(count);
    expect(screen.getByTestId('learning-stage-grid')).toHaveAttribute('data-stage-count', String(count));
    expect(getStageLayout(count)).toHaveLength(count);
    expect(getStageLayout(count).at(-1).progress).toBeGreaterThan(getStageLayout(count)[0].progress);
  });

  it('emits the selected stage and its source rectangle for the page transition', () => {
    const onStageSelect = vi.fn();
    render(<LearningStageLanding stages={DEFAULT_LEARNING_STAGES} onStageSelect={onStageSelect} />);

    fireEvent.click(screen.getByRole('button', { name: '进入基础筑基阶段' }));

    expect(onStageSelect).toHaveBeenCalledWith(expect.objectContaining({
      stage: expect.objectContaining({ id: 'foundation', title: '基础筑基' }),
      index: 0,
      sourceRect: expect.objectContaining({ width: expect.any(Number), height: expect.any(Number) }),
    }));
  });

  it('uses compact resources only for cards in the shorter half of the staircase', () => {
    render(<LearningStageLanding stages={DEFAULT_LEARNING_STAGES} />);

    const cards = screen.getAllByRole('button', { name: /进入.+阶段/ });
    expect(cards.slice(0, 3).every((card) => card.dataset.resourceDensity === 'compact')).toBe(true);
    expect(cards.slice(3).every((card) => card.dataset.resourceDensity === 'full')).toBe(true);
  });

  it('does not reserve card height for redundant action copy', () => {
    render(<LearningStageLanding stages={DEFAULT_LEARNING_STAGES} />);

    expect(screen.queryByText('查看学习路径')).not.toBeInTheDocument();
  });

  it('renders each supplied stage drawing as a decorative watermark', () => {
    const { container } = render(<LearningStageLanding stages={DEFAULT_LEARNING_STAGES} />);

    const drawings = Array.from(container.querySelectorAll('.learning-stage-card__illustration'));
    expect(drawings).toHaveLength(6);
    expect(drawings.every((drawing) => drawing.getAttribute('alt') === '')).toBe(true);
    expect(drawings.every((drawing) => drawing.getAttribute('aria-hidden') === 'true')).toBe(true);
    expect(drawings.map((drawing) => drawing.getAttribute('src'))).toEqual([
      '/learning-stage/foundation.png',
      '/learning-stage/classics.png',
      '/learning-stage/formulas.png',
      '/learning-stage/clinical.png',
      '/learning-stage/specialty.png',
      '/learning-stage/mastery.png',
    ]);
  });

  it('uses the approved green-to-ink stage palette in order', () => {
    render(<LearningStageLanding stages={DEFAULT_LEARNING_STAGES} />);

    const cards = screen.getAllByRole('button', { name: /进入.+阶段/ });
    expect(cards.map((card) => card.style.getPropertyValue('--stage-start'))).toEqual([
      '#3F8F68',
      '#347D70',
      '#33777B',
      '#3B6876',
      '#3B586A',
      '#293D4C',
    ]);
  });

  it('renders the current user persisted long-term plan instead of the visual defaults', async () => {
    loadPlannedLearningPath.mockResolvedValue({
      schema_version: '1.0',
      plan_ref: { plan_id: 'LP_1', plan_version: 3 },
      nodes: [{
        node_id: 'plan:LP_1:stage:stage-1',
        node_type: 'stage',
        title: '中医基础与文化语言',
        order: 1,
        status: 'in_progress',
        child_count: 4,
        description: '建立中医基础概念、文化史脉络和医古文阅读基础。',
      }],
    });
    const onStageSelect = vi.fn();

    render(<LearningStageLanding onStageSelect={onStageSelect} />);

    const stage = await screen.findByRole('button', { name: '进入中医基础与文化语言阶段' });
    expect(stage).toHaveTextContent('4 本教材');
    expect(stage).toHaveTextContent('建立中医基础概念、文化史脉络和医古文阅读基础。');
    expect(screen.queryByText('基础筑基')).not.toBeInTheDocument();
    fireEvent.click(stage);
    expect(onStageSelect).toHaveBeenCalledWith(expect.objectContaining({
      stage: expect.objectContaining({ nodeId: 'plan:LP_1:stage:stage-1' }),
    }));
  });

  it('shows a planning action when the backend reports no long-term plan', async () => {
    loadPlannedLearningPath.mockResolvedValue({
      schema_version: '1.0',
      nodes: [],
      availability: 'requires_long_term_plan',
      message: '请先完成长期学习规划，再生成阶段、教材和知识点路径。',
    });
    const onCreatePlan = vi.fn();

    render(<LearningStageLanding onCreatePlan={onCreatePlan} />);

    expect(await screen.findByText('请先完成长期学习规划，再生成阶段、教材和知识点路径。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '去制定长期规划' }));
    expect(onCreatePlan).toHaveBeenCalledOnce();
  });

  it('adapts only backend stage nodes in their persisted order', () => {
    const stages = plannedStagesFromPath({ nodes: [
      { node_id: 'book-1', node_type: 'book', title: '教材', order: 1 },
      { node_id: 'stage-2', node_type: 'stage', title: '第二阶段', order: 2, child_count: 3, status: 'locked' },
      { node_id: 'stage-1', node_type: 'stage', title: '第一阶段', order: 1, child_count: 2, status: 'completed' },
    ] });

    expect(stages.map((stage) => stage.title)).toEqual(['第一阶段', '第二阶段']);
    expect(stages.map((stage) => stage.duration)).toEqual(['2 本教材', '3 本教材']);
    expect(stages.map((stage) => stage.level)).toEqual(['已完成', '待学习']);
  });
});

import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import KnowledgeTreeDrilldown from './KnowledgeTreeDrilldown';
import {
  loadAllNodeKnowledgePoints,
  loadExamNodes,
  loadNodeLearnerStates,
} from '../exam-atlas/examAtlasApi';

vi.mock('../exam-atlas/examAtlasApi', () => ({
  loadExamNodes: vi.fn(),
  loadAllNodeKnowledgePoints: vi.fn(),
  loadNodeLearnerStates: vi.fn(),
}));

vi.mock('./KnowledgePlanetScene', () => ({
  default: ({ nodes, positions, edges, onNodeClick, onNodeDoubleClick }) => (
    <section aria-label="三维知识星球" data-renderer="webgl">
      {edges.map((edge, index) => (
        <i key={`${edge.kind}-${edge.from}-${edge.to}-${index}`} data-testid="drilldown-tree-edge" />
      ))}
      {nodes.map((node) => (
        <button
          key={node.membership_id}
          type="button"
          aria-label={`打开${node.title}知识卡片`}
          data-depth={positions[node.membership_id]?.side === 'current' ? 0 : 1}
          data-material={positions[node.membership_id]?.material}
          onClick={() => onNodeClick(node)}
          onDoubleClick={(event) => onNodeDoubleClick(node, event)}
        >{node.title}</button>
      ))}
    </section>
  ),
}));

const root = {
  membership_id: 'root',
  parent_membership_id: null,
  title: '方剂学',
  child_count: 2,
};
const childA = {
  membership_id: 'child-a',
  parent_membership_id: 'root',
  title: '解表剂',
  child_count: 1,
};
const childB = {
  membership_id: 'child-b',
  parent_membership_id: 'root',
  title: '温里剂',
  child_count: 0,
};
const leaf = {
  membership_id: 'leaf',
  parent_membership_id: 'child-a',
  title: '麻黄汤',
  child_count: 0,
};

describe('KnowledgeTreeDrilldown', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    loadExamNodes.mockImplementation(async (_trackId, parentId) => {
      if (parentId === 'root') return { items: [childA, childB] };
      if (parentId === 'child-a') return { items: [leaf] };
      return { items: [] };
    });
    loadAllNodeKnowledgePoints.mockResolvedValue({ items: [] });
    loadNodeLearnerStates.mockResolvedValue({
      items: [
        { membership_id: 'root', status: 'in_progress', display_order: 0 },
        { membership_id: 'child-a', status: 'completed', last_assessed_at: '2026-07-17T08:00:00Z', display_order: 1 },
        { membership_id: 'child-b', status: 'unassessed', display_order: 2 },
      ],
    });
  });

  it('loads one connected level at a time and keeps the first-level node at the center', async () => {
    render(<KnowledgeTreeDrilldown trackId="track-a" rootNode={root} onBack={vi.fn()} onNavigate={vi.fn()} />);

    const childButton = await screen.findByRole('button', { name: /打开解表剂知识卡片/ });
    expect(screen.queryByRole('button', { name: /打开麻黄汤知识卡片/ })).not.toBeInTheDocument();
    expect(screen.getAllByTestId('drilldown-tree-edge')).toHaveLength(4);
    expect(screen.getByRole('button', { name: /打开方剂学知识卡片/ })).toHaveAttribute('data-depth', '0');
    expect(loadExamNodes).toHaveBeenCalledWith('track-a', 'root');
    await waitFor(() => expect(loadNodeLearnerStates).toHaveBeenCalledWith(
      'track-a',
      expect.arrayContaining(['root', 'child-a', 'child-b']),
    ));
    expect(screen.getByRole('button', { name: /打开解表剂知识卡片/ })).toHaveAttribute('data-material', 'mastered');
    expect(screen.getByRole('button', { name: /打开温里剂知识卡片/ })).toHaveAttribute('data-material', 'next');

    fireEvent.click(childButton, { detail: 1 });
    fireEvent.click(childButton, { detail: 2 });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.doubleClick(childButton);
    expect(await screen.findByRole('button', { name: /打开麻黄汤知识卡片/ })).toBeInTheDocument();
    expect(screen.getAllByTestId('drilldown-tree-edge')).toHaveLength(6);
    expect(loadExamNodes).toHaveBeenCalledWith('track-a', 'child-a');
  });

  it('opens a knowledge card for any visible hierarchy node', async () => {
    loadAllNodeKnowledgePoints.mockResolvedValue({
      items: [{ kp_id: 'kp-mahuang', name: '麻黄汤证', path: ['方剂学', '解表剂', '麻黄汤证'] }],
    });
    render(<KnowledgeTreeDrilldown trackId="track-a" rootNode={root} onBack={vi.fn()} onNavigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: /打开温里剂知识卡片/ }));
    const dialog = await screen.findByRole('dialog', { name: '温里剂' });
    expect(dialog).toHaveTextContent('方剂学 / 解表剂 / 麻黄汤证');
    expect(dialog).toHaveTextContent('麻黄汤证');
  });

  it('ignores stale knowledge-card responses after another node is selected', async () => {
    let resolveFirst;
    loadAllNodeKnowledgePoints
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({ items: [{ kp_id: 'kp-new', name: '新节点内容', path: [] }] });
    render(<KnowledgeTreeDrilldown trackId="track-a" rootNode={root} onBack={vi.fn()} onNavigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: /打开解表剂知识卡片/ }));
    await screen.findByRole('dialog', { name: '解表剂' });
    fireEvent.click(screen.getByRole('button', { name: /打开温里剂知识卡片/ }));
    expect(await screen.findByText('新节点内容')).toBeInTheDocument();

    resolveFirst({ items: [{ kp_id: 'kp-old', name: '过期内容', path: [] }] });
    await waitFor(() => expect(screen.queryByText('过期内容')).not.toBeInTheDocument());
  });

  it('shows knowledge-card load failures separately from an empty node and can retry', async () => {
    loadAllNodeKnowledgePoints
      .mockRejectedValueOnce(new Error('知识点服务暂时不可用'))
      .mockResolvedValueOnce({ items: [] });
    render(<KnowledgeTreeDrilldown trackId="track-a" rootNode={root} onBack={vi.fn()} onNavigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: /打开温里剂知识卡片/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('知识点服务暂时不可用');
    fireEvent.click(screen.getByRole('button', { name: '重试加载知识点' }));
    await waitFor(() => expect(loadAllNodeKnowledgePoints).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('当前节点暂无已确认公共知识点，可继续查看下级结构。')).toBeInTheDocument();
  });

  it('keeps loaded branches visible when one descendant branch fails', async () => {
    loadExamNodes.mockImplementation(async (_trackId, parentId) => {
      if (parentId === 'root') return { items: [childA, childB] };
      if (parentId === 'child-a') throw new Error('解表剂分支暂时不可用');
      return { items: [] };
    });
    render(<KnowledgeTreeDrilldown trackId="track-a" rootNode={root} onBack={vi.fn()} onNavigate={vi.fn()} />);

    const childButton = await screen.findByRole('button', { name: /打开解表剂知识卡片/ });
    expect(screen.getByRole('button', { name: /打开温里剂知识卡片/ })).toBeInTheDocument();
    fireEvent.doubleClick(childButton);
    expect(await screen.findByRole('alert')).toHaveTextContent('解表剂分支暂时不可用');
    fireEvent.click(screen.getByRole('button', { name: '重试解表剂分支' }));
    await waitFor(() => expect(loadExamNodes).toHaveBeenCalledWith('track-a', 'child-a'));
  });
});

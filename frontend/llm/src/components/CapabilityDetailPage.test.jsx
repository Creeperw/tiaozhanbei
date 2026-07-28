import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import CapabilityDetailPage from './CapabilityDetailPage';

describe('CapabilityDetailPage', () => {
  it.each([
    ['multi-agent', '多智能体协同', '进入智能助教', 6],
    ['learning-path', '个性化学习路径', '查看我的学习路径', 3],
    ['knowledge-graph', '知识图谱驱动', '探索知识图谱', 3],
    ['data-growth', '数据驱动成长', '查看学情报告', 3],
  ])('renders the %s capability content', (key, title, actionLabel, featureCount) => {
    render(<CapabilityDetailPage capabilityKey={key} onNavigate={vi.fn()} />);

    expect(screen.getByRole('heading', { level: 1, name: title })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: actionLabel })).toHaveLength(2);
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(featureCount);
  });

  it('opens the real module from the capability action', () => {
    const onNavigate = vi.fn();
    render(<CapabilityDetailPage capabilityKey="data-growth" onNavigate={onNavigate} />);

    fireEvent.click(screen.getAllByRole('button', { name: '查看学情报告' })[0]);

    expect(onNavigate).toHaveBeenCalledWith({
      page: 'personalization',
      params: { view: 'reports' },
    });
  });

  it('presents the formal six-agent collaboration roles', () => {
    render(<CapabilityDetailPage capabilityKey="multi-agent" onNavigate={vi.fn()} />);

    ['任务规划', '记忆管理', '学情诊断', '知识库管理', '专家', '审核裁判'].forEach((role) => {
      expect(screen.getAllByText(role)).toHaveLength(2);
    });
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(6);
  });

  it('returns to the platform homepage', () => {
    const onNavigate = vi.fn();
    render(<CapabilityDetailPage capabilityKey="knowledge-graph" onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole('button', { name: '返回平台首页' }));

    expect(onNavigate).toHaveBeenCalledWith({ page: 'dashboard', params: {} });
  });
});

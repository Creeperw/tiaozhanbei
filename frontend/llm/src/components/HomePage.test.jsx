import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import HomePage from './HomePage';

describe('HomePage', () => {
  it('presents the six-agent platform and routes executable entries', () => {
    const onNavigate = vi.fn();
    render(<HomePage currentUser={{ display_name: '林同学' }} onNavigate={onNavigate} />);

    expect(screen.getByRole('heading', { name: '承时珍医脉启智慧学习' })).toBeInTheDocument();
    expect(screen.getByLabelText('六智能体协作示意')).toHaveTextContent('规划调度');
    expect(screen.getByLabelText('六智能体协作示意')).toHaveTextContent('质量审核');

    fireEvent.click(screen.getByRole('button', { name: /询问智能助教/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'assistant',
      params: { newConversation: true },
    });

    fireEvent.click(screen.getByRole('button', { name: /进入训练工坊/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({ page: 'training-workshop', params: {} });

    fireEvent.click(screen.getByRole('button', { name: /查看今日学习/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({ page: 'qualification-route', params: {} });

    fireEvent.click(screen.getByRole('button', { name: '查看资格考试经典路线' }));
    expect(onNavigate).toHaveBeenLastCalledWith({ page: 'qualification-route', params: {} });
  });
});

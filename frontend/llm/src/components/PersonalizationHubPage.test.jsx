import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import PersonalizationHubPage from './PersonalizationHubPage';

vi.mock('./PersonalizationPage', () => ({
  default: ({ view }) => <div data-testid="personalization-task">{view}</div>,
}));
vi.mock('./ReportsPage', () => ({ default: () => <div>reports-task</div> }));
vi.mock('./ProfileConflictList', () => ({ default: () => <div>conflicts-task</div> }));
vi.mock('./ReviewDashboardPanel', () => ({ default: () => <div>review-task</div> }));

describe('PersonalizationHubPage task routing', () => {
  it('opens the user profile collection form as an independent task', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(<PersonalizationHubPage navigationContext={{}} onNavigate={onNavigate} />);

    await user.click(screen.getByRole('button', { name: '用户画像' }));

    expect(screen.getByTestId('personalization-task')).toHaveTextContent('user-profile');
    expect(onNavigate).toHaveBeenCalledWith({ page: 'personalization', params: { view: 'user-profile' } });
  });

  it('opens the user profile workspace as the default task view', () => {
    render(<PersonalizationHubPage navigationContext={{}} onNavigate={vi.fn()} />);

    expect(screen.getByTestId('personalization-task')).toHaveTextContent('user-profile');
    expect(screen.getByRole('button', { name: '用户画像' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('navigation', { name: '个性数据二级菜单' })).toBeInTheDocument();
    expect(screen.queryByText('画像 · 记忆 · 规划 · 报告')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '学习记忆' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '干预与通知' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '冲突清单' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '学情规划' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '学情调查' })).not.toBeInTheDocument();
  });

  it('returns retired routes to the user profile workspace', () => {
    const { rerender } = render(<PersonalizationHubPage navigationContext={{ view: 'planning' }} onNavigate={vi.fn()} />);

    expect(screen.getByTestId('personalization-task')).toHaveTextContent('user-profile');

    rerender(<PersonalizationHubPage navigationContext={{ view: 'survey' }} onNavigate={vi.fn()} />);
    expect(screen.getByTestId('personalization-task')).toHaveTextContent('user-profile');
  });

  it('opens the review and mastery workspace as an independent task', async () => {
    const user = userEvent.setup();
    render(<PersonalizationHubPage navigationContext={{}} onNavigate={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: '复习与掌握' }));

    expect(screen.getByText('review-task')).toBeInTheDocument();
  });
});

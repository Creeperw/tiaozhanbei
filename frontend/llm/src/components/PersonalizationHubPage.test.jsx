import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import PersonalizationHubPage from './PersonalizationHubPage';

vi.mock('./PersonalizationPage', () => ({
  default: ({ view }) => <div data-testid="personalization-task">{view}</div>,
}));
vi.mock('./LearningInsightsReportPage', () => ({ default: () => <div>reports-task</div> }));
vi.mock('./ProfileConflictList', () => ({ default: () => <div>conflicts-task</div> }));
vi.mock('./ReviewDashboardPanel', () => ({ default: () => <div>review-task</div> }));

describe('PersonalizationHubPage task routing', () => {
  it('opens the user profile and keeps the persistent text navigation visible', () => {
    render(<PersonalizationHubPage navigationContext={{ view: 'user-profile' }} onNavigate={vi.fn()} />);
    expect(screen.getByTestId('personalization-task')).toHaveTextContent('user-profile');
    expect(screen.getByRole('navigation', { name: '个人数据页面切换' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '学习画像' })).toHaveAttribute('aria-current', 'page');
  });

  it('opens the learning report as the default task view', () => {
    render(<PersonalizationHubPage navigationContext={{}} onNavigate={vi.fn()} currentUser={{ username: 'alice' }} />);

    expect(screen.getByText('reports-task')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'alice的学情报告' })).toHaveAttribute('aria-current', 'page');
  });

  it('routes all persistent text buttons without wrapping them in cards', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(<PersonalizationHubPage navigationContext={{}} onNavigate={onNavigate} currentUser={{ username: 'alice' }} />);

    await user.click(screen.getByRole('button', { name: '复习与掌握' }));
    expect(onNavigate).toHaveBeenCalledWith({ page: 'personalization', params: { view: 'review' } });
  });

  it('returns retired routes to the learning report', () => {
    const { rerender } = render(<PersonalizationHubPage navigationContext={{ view: 'planning' }} onNavigate={vi.fn()} />);

    expect(screen.getByText('reports-task')).toBeInTheDocument();

    rerender(<PersonalizationHubPage navigationContext={{ view: 'survey' }} onNavigate={vi.fn()} />);
    expect(screen.getByText('reports-task')).toBeInTheDocument();
  });

  it('opens the review and mastery workspace from navigation context', () => {
    render(<PersonalizationHubPage navigationContext={{ view: 'review' }} onNavigate={vi.fn()} />);

    expect(screen.getByText('review-task')).toBeInTheDocument();
  });

  it('opens learning memory inside personal data', () => {
    render(<PersonalizationHubPage navigationContext={{ view: 'memory' }} onNavigate={vi.fn()} />);

    expect(screen.getByTestId('personalization-task')).toHaveTextContent('memory');
    expect(screen.getByRole('button', { name: '学习记忆' })).toHaveAttribute('aria-current', 'page');
  });
});

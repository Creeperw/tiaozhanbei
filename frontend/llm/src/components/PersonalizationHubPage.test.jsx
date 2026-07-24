import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import PersonalizationHubPage from './PersonalizationHubPage';

vi.mock('./PersonalizationPage', () => ({
  default: ({ view }) => <div data-testid="personalization-task">{view}</div>,
}));
vi.mock('./PlanningPage', () => ({ default: () => <div>planning-task</div> }));
vi.mock('./ReportsPage', () => ({ default: () => <div>reports-task</div> }));
vi.mock('./OnboardingSurveyPanel', () => ({ default: () => <div>survey-task</div> }));
vi.mock('./ProfileConflictList', () => ({ default: () => <div>conflicts-task</div> }));
vi.mock('./ReviewDashboardPanel', () => ({ default: () => <div>review-task</div> }));

describe('PersonalizationHubPage task routing', () => {
  it('opens profile and memory as one default task view', () => {
    render(<PersonalizationHubPage navigationContext={{}} onNavigate={vi.fn()} />);

    expect(screen.getByTestId('personalization-task')).toHaveTextContent('unified');
    expect(screen.getByRole('button', { name: '学习画像与记忆' })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByText('survey-task')).not.toBeInTheDocument();
  });

  it('redirects a legacy memory route into the unified view', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(<PersonalizationHubPage navigationContext={{ view: 'memory' }} onNavigate={onNavigate} />);

    expect(screen.getByTestId('personalization-task')).toHaveTextContent('unified');
    expect(screen.getByRole('button', { name: '学习画像与记忆' })).toHaveAttribute('aria-current', 'page');
    await user.click(screen.getByRole('button', { name: '学习画像与记忆' }));
    expect(onNavigate).toHaveBeenCalledWith({ page: 'personalization', params: { view: 'profile' } });
  });

  it('opens the review and mastery workspace as an independent task', async () => {
    const user = userEvent.setup();
    render(<PersonalizationHubPage navigationContext={{}} onNavigate={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: '复习与掌握' }));

    expect(screen.getByText('review-task')).toBeInTheDocument();
  });
});

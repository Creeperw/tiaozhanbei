import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import SettingsHubPage from './SettingsHubPage';

vi.mock('../utils/api', () => ({
  API_BASE: 'http://api.test',
  fetchWithAuth: vi.fn(() => Promise.resolve({ ok: true, payload: [] })),
  readJsonResponse: vi.fn((response, fallback) => Promise.resolve(response?.payload ?? fallback)),
}));
vi.mock('./PersonalizationPage', () => ({
  default: ({ view }) => <div data-testid="settings-memory-task">{view}</div>,
}));
vi.mock('./LearningGovernancePanel', () => ({ default: () => <div>governance-task</div> }));
vi.mock('./ProfileConflictList', () => ({ default: () => <div>conflicts-task</div> }));

describe('SettingsHubPage task routing', () => {
  it('opens the learning memory workspace by default', () => {
    render(<SettingsHubPage navigationContext={{}} onNavigate={vi.fn()} />);

    expect(screen.getByRole('navigation', { name: '用户设置二级菜单' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '学习记忆' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByTestId('settings-memory-task')).toHaveTextContent('unified');
  });

  it('opens moved notification and conflict sections under user settings', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(<SettingsHubPage navigationContext={{}} onNavigate={onNavigate} />);

    await user.click(screen.getByRole('button', { name: '干预与通知' }));
    expect(screen.getByText('governance-task')).toBeInTheDocument();
    expect(onNavigate).toHaveBeenCalledWith({ page: 'settings', params: { view: 'governance' } });

    await user.click(screen.getByRole('button', { name: '冲突清单' }));
    expect(screen.getByText('conflicts-task')).toBeInTheDocument();
    expect(onNavigate).toHaveBeenCalledWith({ page: 'settings', params: { view: 'conflicts' } });
  });

  it('keeps legacy profile links on the learning memory section', () => {
    render(<SettingsHubPage navigationContext={{ view: 'profile' }} onNavigate={vi.fn()} />);

    expect(screen.getByRole('button', { name: '学习记忆' })).toHaveAttribute('aria-current', 'page');
  });
});
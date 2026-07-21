import React from 'react';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import PaperGenerationPanel from './PaperGenerationPanel';
import { loadPapers } from '../pageDataLoaders';

vi.mock('../pageDataLoaders', () => ({
  loadPaper: vi.fn(),
  loadPapers: vi.fn(),
  savePaperAnswers: vi.fn(),
  submitPaper: vi.fn(),
  submitTrainingWorkspaceTask: vi.fn(),
}));

vi.mock('../utils/api', () => ({
  fetchJsonWithAuthFallback: vi.fn(),
}));

describe('PaperGenerationPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    loadPapers.mockResolvedValue({ papers: { items: [] }, error: '' });
  });

  it('supports the full set of paper question types', async () => {
    render(<PaperGenerationPanel enabled />);

    expect(await screen.findByRole('spinbutton', { name: '单选题' })).toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: '多选题' })).toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: '填空题' })).toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: '简答题' })).toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: '案例题' })).toBeInTheDocument();
  });
});

import React from 'react';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SmartPaperPanel from './SmartPaperPanel';
import { loadPapers } from '../pageDataLoaders';

vi.mock('../utils/api', () => ({
  fetchJsonWithAuthFallback: vi.fn(),
}));

vi.mock('../pageDataLoaders', () => ({
  generateWorkshopPaperWithAgents: vi.fn(),
  loadPaper: vi.fn(),
  loadPapers: vi.fn(),
}));

vi.mock('./PaperGenerationPanel', () => ({
  default: () => <div>paper workspace</div>,
}));

describe('SmartPaperPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    loadPapers.mockResolvedValue({
      papers: {
        items: [
          { paper_id: 'P1', title: '待办一', status: 'published', duration_minutes: 30 },
          { paper_id: 'P2', title: '历史一', status: 'completed', duration_minutes: 45 },
        ],
      },
    });
  });

  it('shows pending, history, and smart composition together in the compact workspace', async () => {
    render(<SmartPaperPanel />);

    expect(await screen.findByText('待办一')).toBeInTheDocument();
    expect(screen.getByText('历史一')).toBeInTheDocument();
    expect(screen.getByLabelText('专项练主题')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '试卷存档' })).toHaveClass('smart-paper__archive-grid');
  });
});

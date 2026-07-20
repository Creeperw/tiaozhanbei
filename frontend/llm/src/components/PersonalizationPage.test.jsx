import React from 'react';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import PersonalizationPage from './PersonalizationPage';
import { fetchWithAuth } from '../utils/api';

vi.mock('../utils/api', () => ({
  API_BASE: 'http://api.test',
  fetchWithAuth: vi.fn(),
}));
vi.mock('./LearningTrendChart', () => ({ default: () => <div>trend-chart</div> }));

function responseFor(url) {
  if (url.includes('/personalization/overview')) {
    return { profile: {}, stats: { by_category: {}, by_source: {} } };
  }
  if (url.includes('/personalization/learner-profile')) {
    return { locked_fields: [], survey: {}, lock_reason: {} };
  }
  if (url.includes('/personalization/learning-trends')) return { series: [] };
  return [];
}

describe('PersonalizationPage single-task views', () => {
  beforeEach(() => {
    fetchWithAuth.mockReset();
    fetchWithAuth.mockImplementation(async (url) => ({
      ok: true,
      json: async () => responseFor(url),
    }));
  });

  it('renders profile controls without the memory workspace', () => {
    render(<PersonalizationPage embedded view="profile" />);

    expect(screen.getByRole('heading', { name: '学习者画像' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '学习记忆数据库' })).not.toBeInTheDocument();
  });

  it('renders the memory workspace without profile controls', () => {
    render(<PersonalizationPage embedded view="memory" />);

    expect(screen.getByRole('heading', { name: '学习记忆数据库' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '学习者画像' })).not.toBeInTheDocument();
  });
});

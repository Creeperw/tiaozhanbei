import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TrainingHistoryPanel from './TrainingHistoryPanel';
import QuestionTrainingPanel from './QuestionTrainingPanel';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { categoryForActivity } from './trainingHistoryActivity';

vi.mock('../utils/api', () => ({ fetchJsonWithAuthFallback: vi.fn() }));
vi.mock('./exam-atlas/AtlasPracticePanel', () => ({ default: (props) => <div data-testid="practice-origin">{props.practiceOrigin}</div> }));
vi.mock('./SimulatedPatientChat', () => ({ default: () => null }));
vi.mock('./MistakeVariationPanel', () => ({ default: () => null }));

describe('practice history origin', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows completed special attempts in the special card instead of paper sets', async () => {
    fetchJsonWithAuthFallback.mockResolvedValue({ data: { recent_activities: [
      { activity_id: 594, activity_type: 'question_attempt', resource_type: 'question', completion_status: 'completed', practice_origin: 'special_training', title: '专项案例作答' },
      { activity_id: 595, activity_type: 'question_attempt', resource_type: 'question', completion_status: 'completed', practice_origin: 'topic_training', title: '知识点作答' },
    ] } });
    render(<TrainingHistoryPanel />);
    await screen.findByText('专项案例作答');
    const special = screen.getByRole('heading', { name: '专项特训' }).closest('article');
    const topic = screen.getByRole('heading', { name: '知识点特训' }).closest('article');
    const paper = screen.getByRole('heading', { name: '综合套题' }).closest('article');
    expect(within(special).getByText('专项案例作答')).toBeInTheDocument();
    expect(within(topic).getByText('知识点作答')).toBeInTheDocument();
    expect(within(paper).queryByText('专项案例作答')).not.toBeInTheDocument();
  });

  it.each(['special_training', 'topic_training'])('passes the explicit %s entry to submission panel', (origin) => {
    render(<QuestionTrainingPanel enabled practiceOrigin={origin} />);
    expect(screen.getByTestId('practice-origin')).toHaveTextContent(origin);
  });

  it('does not reclassify unknown or daily sources as special training', () => {
    for (const origin of [undefined, 'daily_task', 'unknown']) {
      expect(categoryForActivity({ activity_type: 'question_attempt', resource_type: 'question', practice_origin: origin })).toBe('question_training');
    }
  });

  it('loads remaining history pages instead of truncating at 100', async () => {
    const record = (id) => ({ activity_id: id, activity_type: 'question_attempt',
      resource_type: 'question', completion_status: 'completed', title: `作答${id}` });
    fetchJsonWithAuthFallback.mockResolvedValueOnce({ data: {
      recent_activities: Array.from({ length: 100 }, (_, i) => record(i)), total: 101, next_offset: 100,
    } }).mockResolvedValueOnce({ data: { recent_activities: [record(100)], total: 101, next_offset: null } });
    render(<TrainingHistoryPanel />);
    await screen.findAllByText('101');
    expect(fetchJsonWithAuthFallback).toHaveBeenCalledTimes(2);
    expect(fetchJsonWithAuthFallback.mock.calls[1][0].paths[0]).toContain('offset=100');
  });
});
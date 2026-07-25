import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import CaseTrainingPanel from './CaseTrainingPanel';

describe('CaseTrainingPanel', () => {
  beforeEach(() => {
    sessionStorage.clear();
    global.fetch = vi.fn().mockResolvedValue({ ok: true, text: async () => JSON.stringify({
      success: true, session_id: 'sp-1', turn_count: 1, help_available: false,
      data: { patient_reply: '医生，我腹痛。', patient_info: { gender: '男', age_range: '中年' } },
    }) });
  });

  it('starts a simulated-patient session through the unified API', async () => {
    render(<CaseTrainingPanel enabled />);
    fireEvent.click(screen.getByRole('button', { name: '开始问诊' }));

    expect(await screen.findByText('医生，我腹痛。')).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith('/api/v1/simulated-patient', expect.objectContaining({ method: 'POST' }));
  });
});

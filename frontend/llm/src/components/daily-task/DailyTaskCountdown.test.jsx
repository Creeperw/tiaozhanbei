import React from 'react';
import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import DailyTaskCountdown from './DailyTaskCountdown';

describe('DailyTaskCountdown', () => {
  afterEach(() => vi.useRealTimers());

  it('counts down from the server deadline and refreshes once at zero', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-23T00:00:00Z'));
    const onExpire = vi.fn();
    render(<DailyTaskCountdown timer={{
      available: true,
      server_time: '2026-07-23T00:00:00Z',
      refresh_due_at: '2026-07-23T00:00:02Z',
    }} onExpire={onExpire} />);

    expect(screen.getByText('00:00:02')).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(2000));
    expect(screen.getByText('00:00:00')).toBeInTheDocument();
    expect(onExpire).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(2000));
    expect(onExpire).toHaveBeenCalledTimes(1);
  });
});

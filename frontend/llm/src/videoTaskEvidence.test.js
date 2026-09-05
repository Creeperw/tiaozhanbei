import { describe, expect, it, vi } from 'vitest';

import {
  createHtml5VideoEvidenceTracker,
  createIframeFocusEvidenceTracker,
  isVideoThresholdMet,
  mergeWatchedIntervals,
  watchedCoverage,
} from './videoTaskEvidence';

function playableVideo() {
  const video = document.createElement('video');
  Object.defineProperties(video, {
    paused: { configurable: true, value: false },
    ended: { configurable: true, value: false },
    seeking: { configurable: true, value: false },
    duration: { configurable: true, value: 100 },
    playbackRate: { configurable: true, value: 1, writable: true },
    currentTime: { configurable: true, value: 0, writable: true },
  });
  return video;
}

describe('video task evidence', () => {
  it('merges replayed ranges without counting overlap twice', () => {
    expect(mergeWatchedIntervals([[0, 60], [20, 60], [60, 90]], 0, 100)).toEqual([[0, 90]]);
    expect(watchedCoverage([[0, 60], [20, 60], [60, 90]], 0, 100)).toBe(0.9);
  });

  it('does not fill a seek jump or emit evidence while the page is hidden', async () => {
    let clock = 0;
    const report = vi.fn();
    const video = playableVideo();
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
    const tracker = createHtml5VideoEvidenceTracker({ video, segmentEnd: 100, report, now: () => clock });
    video.dispatchEvent(new Event('timeupdate'));
    clock = 1000;
    video.currentTime = 1;
    video.dispatchEvent(new Event('timeupdate'));
    clock = 2000;
    video.currentTime = 90;
    video.dispatchEvent(new Event('timeupdate'));
    expect(tracker.getEvidence().watchedIntervals).toEqual([[0, 1]]);

    visibility.mockReturnValue('hidden');
    document.dispatchEvent(new Event('visibilitychange'));
    clock = 3000;
    video.currentTime = 91;
    video.dispatchEvent(new Event('timeupdate'));
    await tracker.stop();
    expect(tracker.getEvidence().watchedIntervals).toEqual([[0, 1]]);
    expect(report).toHaveBeenCalledTimes(1);
    visibility.mockRestore();
  });

  it('keeps 89% pending and accepts exactly 90%', () => {
    expect(isVideoThresholdMet(89, 0, 100)).toBe(false);
    expect(isVideoThresholdMet(90, 0, 100)).toBe(true);
  });

  it('counts iframe time only while the visible page and frame are focused', () => {
    let clock = 0;
    const iframe = document.createElement('iframe');
    document.body.appendChild(iframe);
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
    const hasFocus = vi.spyOn(document, 'hasFocus').mockReturnValue(true);
    iframe.focus();
    const tracker = createIframeFocusEvidenceTracker({
      iframe,
      segmentEnd: 10,
      now: () => clock,
      setIntervalFn: () => 1,
      clearIntervalFn: () => {},
    });
    clock = 1000;
    tracker.tick();
    expect(tracker.getEvidence().activeSeconds).toBe(1);
    visibility.mockReturnValue('hidden');
    clock = 2000;
    tracker.tick();
    expect(tracker.getEvidence().activeSeconds).toBe(1);
    tracker.stop();
    iframe.remove();
    visibility.mockRestore();
    hasFocus.mockRestore();
  });
});
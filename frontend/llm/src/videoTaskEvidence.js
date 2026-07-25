const finiteNumber = (value, fallback = 0) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
};

export function videoSegment(video, mediaDuration = 0) {
  const start = Math.max(0, finiteNumber(video?.segment_start_seconds ?? video?.start_seconds, 0));
  const explicitEnd = finiteNumber(video?.segment_end_seconds ?? video?.end_seconds, 0);
  const duration = finiteNumber(video?.duration_seconds ?? video?.duration, 0);
  const mediaEnd = finiteNumber(mediaDuration, 0);
  const end = explicitEnd > start
    ? explicitEnd
    : duration > 0 ? start + duration : mediaEnd > start ? mediaEnd : start;
  return { start, end, duration: Math.max(0, end - start) };
}

export function mergeWatchedIntervals(intervals, segmentStart = 0, segmentEnd = Number.POSITIVE_INFINITY) {
  const lower = Math.max(0, finiteNumber(segmentStart, 0));
  const upperValue = Number(segmentEnd);
  const upper = Number.isFinite(upperValue) ? Math.max(lower, upperValue) : Number.POSITIVE_INFINITY;
  const normalized = (Array.isArray(intervals) ? intervals : [])
    .map((interval) => [
      Math.max(lower, finiteNumber(interval?.[0], lower)),
      Math.min(upper, finiteNumber(interval?.[1], lower)),
    ])
    .filter(([start, end]) => end > start)
    .sort((left, right) => left[0] - right[0] || left[1] - right[1]);

  return normalized.reduce((merged, [start, end]) => {
    const previous = merged.at(-1);
    if (!previous || start > previous[1] + 0.05) {
      merged.push([start, end]);
    } else {
      previous[1] = Math.max(previous[1], end);
    }
    return merged;
  }, []);
}

export function watchedSeconds(intervals, segmentStart = 0, segmentEnd = Number.POSITIVE_INFINITY) {
  return mergeWatchedIntervals(intervals, segmentStart, segmentEnd)
    .reduce((total, [start, end]) => total + end - start, 0);
}

export function watchedCoverage(intervals, segmentStart, segmentEnd) {
  const duration = Math.max(0, finiteNumber(segmentEnd) - finiteNumber(segmentStart));
  return duration > 0 ? Math.min(1, watchedSeconds(intervals, segmentStart, segmentEnd) / duration) : 0;
}

export function isVideoThresholdMet(value, segmentStart, segmentEnd, threshold = 0.9) {
  const duration = Math.max(0, finiteNumber(segmentEnd) - finiteNumber(segmentStart));
  return duration > 0 && finiteNumber(value) + 1e-6 >= duration * threshold;
}

const pageIsVisible = (documentRef) => documentRef?.visibilityState !== 'hidden';

export function createHtml5VideoEvidenceTracker({
  video,
  segmentStart = 0,
  segmentEnd = 0,
  report,
  onProgress = () => {},
  documentRef = globalThis.document,
  now = () => globalThis.performance?.now?.() ?? Date.now(),
  maximumPlaybackRate = 2,
} = {}) {
  if (!video?.addEventListener) throw new TypeError('video element is required');
  let intervals = [];
  let lastMediaTime = null;
  let lastWallTime = null;
  let stopped = false;
  let reportChain = Promise.resolve();
  let lastReportKey = '';

  const bounds = () => {
    const start = Math.max(0, finiteNumber(segmentStart, 0));
    const explicitEnd = finiteNumber(segmentEnd, 0);
    const durationEnd = finiteNumber(video.duration, 0);
    return { start, end: explicitEnd > start ? explicitEnd : durationEnd > start ? durationEnd : start };
  };
  const resetSample = () => {
    lastMediaTime = null;
    lastWallTime = null;
  };
  const eligible = () => (
    !stopped
    && pageIsVisible(documentRef)
    && !video.paused
    && !video.ended
    && !video.seeking
    && finiteNumber(video.playbackRate, 1) > 0
    && finiteNumber(video.playbackRate, 1) <= maximumPlaybackRate
  );
  const snapshot = () => {
    const { start, end } = bounds();
    const merged = mergeWatchedIntervals(intervals, start, end);
    return {
      mode: 'html5',
      segmentStartSeconds: start,
      segmentEndSeconds: end,
      watchedIntervals: merged.map(([from, to]) => [Number(from.toFixed(3)), Number(to.toFixed(3))]),
      activeSeconds: watchedSeconds(merged, start, end),
      coverage: watchedCoverage(merged, start, end),
    };
  };
  const flush = () => {
    const evidence = snapshot();
    onProgress(evidence);
    if (!report || evidence.segmentEndSeconds <= evidence.segmentStartSeconds) return reportChain;
    const reportKey = JSON.stringify(evidence.watchedIntervals);
    if (reportKey === lastReportKey) return reportChain;
    lastReportKey = reportKey;
    reportChain = reportChain.then(() => report(evidence)).catch(() => undefined);
    return reportChain;
  };
  const sample = () => {
    if (!eligible()) {
      resetSample();
      return;
    }
    const mediaTime = finiteNumber(video.currentTime, 0);
    const wallTime = now();
    if (lastMediaTime === null || lastWallTime === null) {
      lastMediaTime = mediaTime;
      lastWallTime = wallTime;
      return;
    }
    const mediaDelta = mediaTime - lastMediaTime;
    const wallDelta = Math.max(0, (wallTime - lastWallTime) / 1000);
    const playbackRate = finiteNumber(video.playbackRate, 1);
    // A normal timeupdate may be sparse, but a seek-sized media jump must not
    // manufacture all of the skipped interval as watched evidence.
    const maximumContinuousDelta = Math.max(1, wallDelta * playbackRate * 2.5 + 0.25);
    if (mediaDelta > 0 && mediaDelta <= maximumContinuousDelta) {
      const { start, end } = bounds();
      intervals = mergeWatchedIntervals([...intervals, [lastMediaTime, mediaTime]], start, end);
      void flush();
    }
    lastMediaTime = mediaTime;
    lastWallTime = wallTime;
  };
  const onBoundary = () => {
    resetSample();
    if (video.paused || video.ended || video.seeking || !pageIsVisible(documentRef)) void flush();
  };
  const onVisibilityChange = () => {
    resetSample();
    if (!pageIsVisible(documentRef)) void flush();
  };
  const listeners = [
    ['play', onBoundary], ['pause', onBoundary], ['timeupdate', sample],
    ['seeking', onBoundary], ['seeked', onBoundary], ['ratechange', onBoundary],
    ['ended', onBoundary], ['loadedmetadata', onBoundary],
  ];
  listeners.forEach(([event, handler]) => video.addEventListener(event, handler));
  documentRef?.addEventListener?.('visibilitychange', onVisibilityChange);

  return {
    flush,
    getEvidence: snapshot,
    stop() {
      if (stopped) return reportChain;
      stopped = true;
      listeners.forEach(([event, handler]) => video.removeEventListener(event, handler));
      documentRef?.removeEventListener?.('visibilitychange', onVisibilityChange);
      resetSample();
      return flush();
    },
  };
}

export function createIframeFocusEvidenceTracker({
  iframe,
  segmentStart = 0,
  segmentEnd = 0,
  report,
  onProgress = () => {},
  documentRef = globalThis.document,
  now = () => globalThis.performance?.now?.() ?? Date.now(),
  intervalMs = 1000,
  reportEverySeconds = 5,
  idleLimitSeconds = 300,
  setIntervalFn = globalThis.setInterval,
  clearIntervalFn = globalThis.clearInterval,
} = {}) {
  if (!iframe) throw new TypeError('iframe element is required');
  let activeSeconds = 0;
  let lastTick = now();
  let lastReported = 0;
  let stopped = false;
  let reportChain = Promise.resolve();
  const duration = Math.max(0, finiteNumber(segmentEnd) - finiteNumber(segmentStart));
  const snapshot = () => ({
    mode: 'iframe',
    segmentStartSeconds: Math.max(0, finiteNumber(segmentStart)),
    segmentEndSeconds: Math.max(0, finiteNumber(segmentEnd)),
    watchedIntervals: [],
    activeSeconds,
    coverage: duration > 0 ? Math.min(1, activeSeconds / duration) : 0,
  });
  const flush = () => {
    const evidence = snapshot();
    onProgress(evidence);
    if (!report || duration <= 0 || activeSeconds <= lastReported) return reportChain;
    lastReported = activeSeconds;
    reportChain = reportChain.then(() => report(evidence)).catch(() => undefined);
    return reportChain;
  };
  const focused = () => (
    pageIsVisible(documentRef)
    && (typeof documentRef?.hasFocus !== 'function' || documentRef.hasFocus())
    && documentRef?.activeElement === iframe
  );
  const tick = () => {
    const current = now();
    const elapsed = Math.max(0, (current - lastTick) / 1000);
    lastTick = current;
    if (!stopped && focused() && elapsed > 0 && elapsed <= idleLimitSeconds) {
      // Timer suspension and background throttling cannot add a large block.
      activeSeconds += Math.min(elapsed, intervalMs / 1000 * 2.5);
      onProgress(snapshot());
      if (activeSeconds - lastReported >= reportEverySeconds) void flush();
    }
  };
  const resetClock = () => { lastTick = now(); };
  documentRef?.addEventListener?.('visibilitychange', resetClock);
  globalThis.addEventListener?.('blur', resetClock);
  globalThis.addEventListener?.('focus', resetClock);
  const timer = setIntervalFn(tick, intervalMs);

  return {
    tick,
    flush,
    getEvidence: snapshot,
    stop() {
      if (stopped) return reportChain;
      stopped = true;
      clearIntervalFn(timer);
      documentRef?.removeEventListener?.('visibilitychange', resetClock);
      globalThis.removeEventListener?.('blur', resetClock);
      globalThis.removeEventListener?.('focus', resetClock);
      return flush();
    },
  };
}
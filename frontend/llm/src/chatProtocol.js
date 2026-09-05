const EVENT_PREFIX = '<<EV:';

const scanTraceEventFrames = (content = '') => {
  const text = String(content || '');
  const frames = [];
  let searchFrom = 0;

  while (searchFrom < text.length) {
    const start = text.indexOf(EVENT_PREFIX, searchFrom);
    if (start === -1) break;

    const payloadStart = start + EVENT_PREFIX.length;
    let parsed = null;
    let frameEnd = -1;

    for (let cursor = payloadStart; cursor < text.length; cursor += 1) {
      const char = text[cursor];
      if ((char !== '}' && char !== ']') || text.slice(cursor + 1, cursor + 3) !== '>>') continue;
      try {
        parsed = JSON.parse(text.slice(payloadStart, cursor + 1));
        frameEnd = cursor + 3;
        break;
      } catch {
        // Formal output may contain nested `<<REFS:...>>` markers or arbitrary
        // `>>` text. Only a candidate that parses as the whole JSON payload is
        // the actual end of the outer EV frame.
      }
    }

    if (frameEnd !== -1) {
      frames.push({ start, end: frameEnd, raw: text.slice(start, frameEnd), event: parsed });
      searchFrom = frameEnd;
    } else {
      searchFrom = payloadStart;
    }
  }

  return { text, frames };
};

export const removeTraceEventsFromContent = (content = '') => {
  const { text, frames } = scanTraceEventFrames(content);
  if (frames.length === 0) return text;

  let result = '';
  let cursor = 0;
  for (const frame of frames) {
    result += text.slice(cursor, frame.start);
    cursor = frame.end;
  }
  return result + text.slice(cursor);
};

export const stripAssistantVisibleContent = (content = '') => {
  const text = String(content || '');
  const thinkEnd = '</think>';
  const idx = text.indexOf(thinkEnd);
  if (idx === -1) return '';
  const prefix = text.slice(0, idx + thinkEnd.length);
  const suffix = text.slice(idx + thinkEnd.length);
  const events = scanTraceEventFrames(suffix).frames.map(frame => frame.raw).join('');
  return `${prefix}\n${events}`;
};

export const extractTraceEventsFromContent = (content = '') => {
  return scanTraceEventFrames(content).frames.map(frame => frame.event);
};

export const hasExecutionDoneEvent = (content = '') => (
  extractTraceEventsFromContent(content).some(event => event?.type === 'execution_done')
);

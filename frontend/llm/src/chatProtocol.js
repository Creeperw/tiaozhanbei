export const stripAssistantVisibleContent = (content = '') => {
  const text = String(content || '');
  const thinkEnd = '</think>';
  const idx = text.indexOf(thinkEnd);
  if (idx === -1) return '';
  const prefix = text.slice(0, idx + thinkEnd.length);
  const suffix = text.slice(idx + thinkEnd.length);
  const events = [...suffix.matchAll(/<<EV:(.*?)>>/gs)].map(match => match[0]).join('');
  return `${prefix}\n${events}`;
};

export const extractTraceEventsFromContent = (content = '') => {
  const text = String(content || '');
  return [...text.matchAll(/<<EV:(.*?)>>/gs)]
    .map(match => {
      try {
        return JSON.parse(match[1]);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
};

export const hasExecutionDoneEvent = (content = '') => (
  extractTraceEventsFromContent(content).some(event => event?.type === 'execution_done')
);

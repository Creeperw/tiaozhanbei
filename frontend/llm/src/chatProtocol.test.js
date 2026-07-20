import test from 'node:test';
import assert from 'node:assert/strict';

import {
  extractTraceEventsFromContent,
  hasExecutionDoneEvent,
  stripAssistantVisibleContent,
} from './chatProtocol.js';

test('extracts valid event payloads while ignoring malformed stream events', () => {
  const content = '思考中<<EV:{"type":"planning_start"}>><<EV:not-json>><<EV:{"type":"execution_done"}>>';

  assert.deepEqual(extractTraceEventsFromContent(content), [
    { type: 'planning_start' },
    { type: 'execution_done' },
  ]);
  assert.equal(hasExecutionDoneEvent(content), true);
});

test('keeps only completed thinking and event history on rollback', () => {
  const content = '<think>分析证据</think>暂定回答<<STATUS:reviewing:审核中>><<REFS:[{"title":"证据"}]>><<VIDEOS:[{"title":"视频"}]>><<EV:{"type":"feedback_start"}>>';

  assert.equal(
    stripAssistantVisibleContent(content),
    '<think>分析证据</think>\n<<EV:{"type":"feedback_start"}>>',
  );
});

test('does not expose incomplete thinking content after rollback', () => {
  assert.equal(stripAssistantVisibleContent('<think>尚未完成'), '');
  assert.equal(hasExecutionDoneEvent('<<EV:{"type":"tool_done"}>>'), false);
});

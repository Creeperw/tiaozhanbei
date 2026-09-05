import test from 'node:test';
import assert from 'node:assert/strict';

import {
  extractTraceEventsFromContent,
  hasExecutionDoneEvent,
  removeTraceEventsFromContent,
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

test('keeps EV payloads intact when formal output contains nested markers and bare terminators', () => {
  const formalOutput = '教材原文 <<REFS:[{"title":"证据"}]>>，并保留普通 >> 文本';
  const event = {
    type: 'agent_output_stream',
    kind: 'delta',
    text: formalOutput,
    agent: 'expert_agent',
    stepId: 'expert',
    phase: 'formal',
    seq: 42,
  };
  const tag = `<<EV:${JSON.stringify(event)}>>`;
  const content = `<think>分析证据</think>${tag}\n最终回答正文。`;

  assert.deepEqual(extractTraceEventsFromContent(content), [event]);
  assert.equal(removeTraceEventsFromContent(content), '<think>分析证据</think>\n最终回答正文。');
  assert.equal(stripAssistantVisibleContent(content), `<think>分析证据</think>\n${tag}`);
});

test('removes several valid EV frames without consuming visible text around malformed candidates', () => {
  const first = '<<EV:{"type":"planning_start","text":"A >> B"}>>';
  const second = '<<EV:{"type":"execution_done"}>>';
  const content = `前文${first}中间<<EV:not-json>>正文${second}结尾`;

  assert.deepEqual(extractTraceEventsFromContent(content), [
    { type: 'planning_start', text: 'A >> B' },
    { type: 'execution_done' },
  ]);
  assert.equal(removeTraceEventsFromContent(content), '前文中间<<EV:not-json>>正文结尾');
});

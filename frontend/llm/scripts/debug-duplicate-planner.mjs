// 独立调试脚本：追踪双 planner 节点分裂点
// 运行：node scripts/debug-duplicate-planner.mjs
import { build } from 'esbuild';

const entry = `
import { reduceLangGraphEvent } from '/mnt/d/code/AI/deeplearning/tiaozhanbei/frontend/llm/src/stores/useLangGraphStore.js';

const ts = 1787494483137;
const events = [
  { type: 'planning_start', text: '正在理解你的需求', ts: ts + 0 },
  { type: 'planning_start', text: 'planner_agent开始处理', agent: 'planner_agent', stepId: 'planner', ts: ts + 100 },
  { type: 'model_call', kind: 'input', text: '任务规划智能体正在读取任务信息', agent: 'planner_agent', stepId: 'planner_agent', callId: 'MODEL_CALL_1', ts: ts + 200 },
  { type: 'reasoning_stream', kind: 'started', agent: 'planner_agent', stepId: 'planner_agent', ts: ts + 300 },
  { type: 'reasoning_stream', kind: 'delta', text: '好的，我需要分析', agent: 'planner_agent', stepId: 'planner_agent', ts: ts + 400 },
  { type: 'reasoning_stream', kind: 'committed', agent: 'planner_agent', stepId: 'planner_agent', ts: ts + 500 },
  { type: 'agent_output_stream', kind: 'started', agent: 'planner_agent', stepId: 'planner_agent', phase: 'working', ts: ts + 600 },
  { type: 'agent_output_stream', kind: 'delta', text: '正在分析', agent: 'planner_agent', stepId: 'planner_agent', phase: 'working', ts: ts + 700 },
  { type: 'agent_progress', kind: 'artifact_ready', text: '任务规划智能体已整理阶段产出', agent: 'planner_agent', stepId: 'planner', ts: ts + 800 },
  { type: 'agent_output_stream', kind: 'started', agent: 'planner_agent', stepId: 'planner', phase: 'formal', ts: ts + 900 },
  { type: 'agent_output_stream', kind: 'delta', text: '已识别为**组卷练习**', agent: 'planner_agent', stepId: 'planner', phase: 'formal', ts: ts + 1000 },
  { type: 'agent_output_stream', kind: 'committed', agent: 'planner_agent', stepId: 'planner', phase: 'formal', ts: ts + 1100 },
  { type: 'planning_done', text: 'planner_agent处理完成', agent: 'planner_agent', stepId: 'planner', ts: ts + 1200 },
  { type: 'planning_done', text: '执行路径已确定', plannedNodes: [
    { step_id: 'planner', agent: 'planner_agent' },
    { step_id: 'memory', agent: 'memory_agent' },
    { step_id: 'diagnosis', agent: 'diagnosis_agent' },
    { step_id: 'paper_blueprint', agent: 'paper_blueprint_agent' },
    { step_id: 'question_pool', agent: 'knowledge_base_agent' },
    { step_id: 'paper_assembly', agent: 'paper_assembly_agent' },
    { step_id: 'audit', agent: 'audit_agent' },
  ], ts: ts + 1300 },
];

let state = { nodes: [], currentActiveNodeId: null, isRollingBack: false, finalAnswerId: null, finalAnswerContent: '', references: [], showPreliminaryAnswer: true };
for (const ev of events) {
  state = reduceLangGraphEvent(state, ev);
  const plannerIds = state.nodes.filter((n) => n.agent === 'planner_agent').map((n) => n.id);
  const allIds = state.nodes.map((n) => n.id);
  console.log(\`after \${ev.type}(\${ev.kind || ''}, stepId=\${ev.stepId || '-'}): planner=[\${plannerIds.join(', ')}] all=[\${allIds.join(', ')}]\`);
}
console.log('FINAL planner nodes:', state.nodes.filter((n) => n.agent === 'planner_agent').map((n) => ({ id: n.id, status: n.status, logs: n.logs, formalOutput: n.formalOutput.slice(0, 30) })));
`;

const result = await build({
  stdin: { contents: entry, resolveDir: process.cwd(), sourcefile: 'debug-entry.js' },
  bundle: true,
  format: 'esm',
  platform: 'node',
  outfile: '/tmp/debug-duplicate-planner.mjs',
  logLevel: 'silent',
});
console.log('bundled');
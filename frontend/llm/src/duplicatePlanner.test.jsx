import { describe, it, expect } from 'vitest';
import { buildTraceFromEvents } from '../src/stores/useLangGraphStore';
import { buildAgentPresentation } from '../src/agentPresentationModel';

describe('duplicate planner node investigation', () => {
  it('reproduces the two-planner-node scenario', () => {
    const ts = 1787494483137;
    const events = [
      // run_started → planning_start (no stepId/agent)
      { type: 'planning_start', text: '正在理解你的需求', ts: ts + 0 },
      // step_started (planner)
      { type: 'planning_start', text: 'planner_agent开始处理', agent: 'planner_agent', stepId: 'planner', ts: ts + 100 },
      // model_input (planner) — stepId is workflow_step_id = 'planner_agent'
      { type: 'model_call', kind: 'input', text: '任务规划智能体正在读取任务信息', agent: 'planner_agent', stepId: 'planner_agent', callId: 'MODEL_CALL_1', ts: ts + 200 },
      // reasoning_stream (planner) — stepId = 'planner_agent'
      { type: 'reasoning_stream', kind: 'started', agent: 'planner_agent', stepId: 'planner_agent', ts: ts + 300 },
      { type: 'reasoning_stream', kind: 'delta', text: '好的，我需要分析', agent: 'planner_agent', stepId: 'planner_agent', ts: ts + 400 },
      { type: 'reasoning_stream', kind: 'committed', agent: 'planner_agent', stepId: 'planner_agent', ts: ts + 500 },
      // business_text_started → agent_output_stream (working) — stepId = 'planner_agent'
      { type: 'agent_output_stream', kind: 'started', agent: 'planner_agent', stepId: 'planner_agent', phase: 'working', ts: ts + 600 },
      { type: 'agent_output_stream', kind: 'delta', text: '正在分析', agent: 'planner_agent', stepId: 'planner_agent', phase: 'working', ts: ts + 700 },
      // system_output → agent_progress
      { type: 'agent_progress', kind: 'artifact_ready', text: '任务规划智能体已整理阶段产出', agent: 'planner_agent', stepId: 'planner', ts: ts + 800 },
      // agent_output_started (formal) — stepId = 'planner'
      { type: 'agent_output_stream', kind: 'started', agent: 'planner_agent', stepId: 'planner', phase: 'formal', ts: ts + 900 },
      { type: 'agent_output_stream', kind: 'delta', text: '已识别为**组卷练习**', agent: 'planner_agent', stepId: 'planner', phase: 'formal', ts: ts + 1000 },
      { type: 'agent_output_stream', kind: 'committed', agent: 'planner_agent', stepId: 'planner', phase: 'formal', ts: ts + 1100 },
      // step_completed (planner) → planning_done
      { type: 'planning_done', text: 'planner_agent处理完成', agent: 'planner_agent', stepId: 'planner', ts: ts + 1200 },
      // graph_compiled → planning_done (no stepId/agent)
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
    const nodes = buildTraceFromEvents(events, { historical: false });
    const plannerNodes = nodes.filter((n) => n.agent === 'planner_agent');
    const roles = buildAgentPresentation(nodes);
    expect(plannerNodes.length).toBe(1);
    expect(roles.filter((role) => role.label === '任务规划')).toHaveLength(1);
  });
});
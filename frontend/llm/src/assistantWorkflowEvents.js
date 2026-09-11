export const ASSISTANT_WORKFLOW_COMPLETED_EVENT = 'shizhen:assistant-workflow-completed';

export function notifyAssistantWorkflowCompleted(sessionId, runId, status) {
  if (status !== 'completed' || typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(ASSISTANT_WORKFLOW_COMPLETED_EVENT, {
    detail: { sessionId, runId },
  }));
}
import { loadAtlasStatus } from './knowledgeAtlasApi';

let runtimeEnabled = null;

export function isKnowledgeAtlasEnabled() {
  const buildFlag = String(import.meta.env.VITE_KNOWLEDGE_ATLAS_ENABLED ?? 'true').toLowerCase();
  const runtimeFlag = globalThis.__APP_CONFIG__?.KNOWLEDGE_ATLAS_ENABLED;
  return buildFlag !== 'false'
    && runtimeFlag !== false
    && String(runtimeFlag ?? 'true').toLowerCase() !== 'false'
    && runtimeEnabled !== false;
}

export function rememberKnowledgeAtlasRuntime(enabled) {
  if (typeof enabled === 'boolean') runtimeEnabled = enabled;
}

export async function resolveKnowledgeAtlasEnabled() {
  if (!isKnowledgeAtlasEnabled()) return false;
  if (runtimeEnabled != null) return runtimeEnabled;
  try {
    const status = await loadAtlasStatus();
    rememberKnowledgeAtlasRuntime(status?.enabled !== false);
  } catch {
    // Availability failures stay local to Atlas; a transient status failure must not disable navigation.
    runtimeEnabled = true;
  }
  return runtimeEnabled;
}

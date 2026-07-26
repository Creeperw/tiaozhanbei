export function practiceContextFromIntent(context = {}) {
  const { trackId, membershipId, kpId, kpName } = context;
  if (!kpId || !kpName) return null;
  return { trackId, membershipId, kpId, kpName };
}

export function knowledgeQueryFromContext(context = {}) {
  return String(context.query || context.kpName || '').trim();
}

export function assistantDraftFromContext(context = {}) {
  return String(context.context || '').trim();
}

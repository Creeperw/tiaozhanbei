export function buildProfileConflictSections({ memories = [], candidates = [] } = {}) {
  const safeMemories = Array.isArray(memories) ? memories : [];
  const safeCandidates = Array.isArray(candidates) ? candidates : [];
  const duplicateGroups = safeMemories
    .filter((item) => item?.is_active)
    .reduce((groups, item) => {
      const key = item.conflict_key || `${item.category || ''}:${(item.title || item.content || '').trim()}`;
      if (!key) return groups;
      return { ...groups, [key]: [...(groups[key] || []), item] };
    }, {});
  const conflicts = Object.entries(duplicateGroups)
    .filter(([, items]) => items.length > 1)
    .map(([key, items]) => ({ key, items }));
  const pendingCandidates = safeCandidates.filter((item) => item?.status === 'pending');

  return {
    conflicts,
    pendingCandidates,
    hasActionableItems: conflicts.length > 0 || pendingCandidates.length > 0,
  };
}

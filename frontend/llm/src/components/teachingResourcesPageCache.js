const TEACHING_RESOURCES_CACHE_TTL = 5 * 60 * 1000;
const teachingResourcesPageCache = new Map();

export function readTeachingResourcesPageCache(key) {
  const entry = teachingResourcesPageCache.get(key);
  if (!entry || Date.now() - entry.updatedAt > TEACHING_RESOURCES_CACHE_TTL) return null;
  return entry.value;
}

export function updateTeachingResourcesPageCache(key, patch) {
  const current = readTeachingResourcesPageCache(key) || {};
  teachingResourcesPageCache.set(key, {
    updatedAt: Date.now(),
    value: { ...current, ...patch },
  });
}

export function clearTeachingResourcesPageCache() {
  teachingResourcesPageCache.clear();
}

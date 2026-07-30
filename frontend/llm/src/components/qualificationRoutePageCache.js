const PAGE_CACHE_TTL = 5 * 60 * 1000;
const pageCache = new Map();
const routeCache = new Map();

function readFresh(cache, key) {
  const entry = cache.get(key);
  if (!entry || Date.now() - entry.updatedAt > PAGE_CACHE_TTL) return null;
  return entry.value;
}

export function readQualificationPageCache(key) {
  return readFresh(pageCache, key);
}

export function updateQualificationPageCache(key, patch) {
  const current = readQualificationPageCache(key) || {};
  pageCache.set(key, {
    updatedAt: Date.now(),
    value: { ...current, ...patch },
  });
}

export function readQualificationRouteCache(key) {
  return readFresh(routeCache, key);
}

export function updateQualificationRouteCache(key, value) {
  routeCache.set(key, { updatedAt: Date.now(), value });
}

export function clearQualificationRoutePageCache() {
  pageCache.clear();
  routeCache.clear();
}

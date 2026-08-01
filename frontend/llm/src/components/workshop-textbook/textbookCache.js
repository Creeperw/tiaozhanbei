// 教材静态目录的内存缓存：离开教学资源再进入时避免重复请求。
// 只缓存静态目录数据（教材列表/章节结构）；学习进度等实时数据不走缓存。
const cache = new Map();
const DEFAULT_TTL = 5 * 60 * 1000; // 5 分钟

/**
 * 带 TTL 的内存 Promise 缓存。
 * - 命中：直接返回缓存的 Promise（并发安全，同一 key 只发一次请求）
 * - 未命中/过期：调用 loader 发起请求，成功后缓存；失败不缓存，下次可重试
 * - loader 失败时传入的 signal 用于卸载中断，命中时忽略
 */
export function cachedPromise(key, loader, { ttl = DEFAULT_TTL } = {}) {
  const hit = cache.get(key);
  if (hit && hit.expiresAt > Date.now()) return hit.promise;
  const promise = loader().catch((error) => {
    cache.delete(key);
    throw error;
  });
  cache.set(key, { promise, expiresAt: Date.now() + ttl });
  return promise;
}

/** 仅测试用：清空缓存，避免跨用例数据污染 */
export function clearTextbookCache() {
  cache.clear();
}

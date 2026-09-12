/**
 * Request id helpers.
 *
 * `crypto.randomUUID` is only exposed in a secure context (HTTPS, or
 * localhost). The app is also served over plain HTTP on LAN / public
 * addresses, where the property is undefined and calling it throws
 * `TypeError: crypto.randomUUID is not a function`. That exception used to
 * abort the caller mid-flight (for example the smart-paper restore path),
 * leaving the request id empty and making submission fail.
 *
 * Always go through these helpers so id generation never throws.
 */

/** Cross-context-safe random token: dash-free, no `crypto` requirement. */
export function randomToken() {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return uuid.replaceAll('-', '');
  return `${Date.now()}${Math.random().toString(16).slice(2)}`;
}

/** Build a prefixed request id, e.g. `createRequestId('paper')`. */
export function createRequestId(prefix = '') {
  const token = randomToken();
  return prefix ? `${prefix}-${token}` : token;
}

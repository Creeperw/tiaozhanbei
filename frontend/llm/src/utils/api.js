export const API_BASE = '/api';
export const MAIN_API_BASE = '/api/v1';
export const AUTH_API_BASE = `${MAIN_API_BASE}/auth`;

// 认证统一由主后端的 HttpOnly Cookie 承载，前端不再接触会话令牌。
export const fetchWithAuth = async (url, options = {}) => {
  const headers = {
    ...options.headers,
  };

  // FormData 由浏览器生成 boundary；无 body 的 GET 也不需要 Content-Type。
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  const res = await fetch(url, { ...options, headers, credentials: 'include' });

  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent('competition:unauthorized'));
  }

  return res;
};

export const readJsonResponse = async (res, fallback = {}) => {
  const text = await res.text();
  if (!text || !text.trim()) {
    return fallback;
  }

  try {
    return JSON.parse(text);
  } catch {
    return fallback;
  }
};

export const fetchJsonWithAuthFallback = async ({
  paths,
  fallback = {},
  options = {},
  validator,
  isValid,
}) => {
  let lastError = null;
  const payloadIsValid = validator || isValid || ((data) => data !== null && data !== undefined);

  for (const path of paths) {
    try {
      const res = await fetchWithAuth(`${API_BASE}${path}`, options);
      const data = await readJsonResponse(res, fallback);
      if (res.ok) {
        if (payloadIsValid(data)) {
          return { data, source: path };
        }
        lastError = new Error(`Invalid payload for ${path}`);
        continue;
      }
      const detail = typeof data.detail === 'string' && data.detail.trim()
        ? data.detail.trim()
        : `Request failed for ${path}`;
      lastError = new Error(`${res.status}: ${detail}`);
    } catch (error) {
      lastError = error;
    }
  }

  throw lastError || new Error('Request failed');
};

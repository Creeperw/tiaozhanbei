export const API_BASE = '/api';

// 封装带 Token 的 fetch 请求
export const fetchWithAuth = async (url, options = {}) => {
  const token = localStorage.getItem('token');
  
  const headers = {
    ...options.headers,
    'Authorization': `Bearer ${token}`
  };

  // 如果是 FormData (文件上传)，不要手动设置 Content-Type，浏览器会自动设置
  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  
  const res = await fetch(url, { ...options, headers });
  
  if (res.status === 401) {
    // Token 过期或无效
    localStorage.removeItem('token');
    window.location.reload(); // 简单处理：强制刷新回登录页
    throw new Error("Unauthorized");
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
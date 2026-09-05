const DEFAULT_MAX_TEXT_CHARS = 8_000;
const MAX_ITEMS = 40;

const EXCLUDED_SELECTOR = [
  'script',
  'style',
  'noscript',
  'template',
  '[hidden]',
  '[aria-hidden="true"]',
  '[inert]',
  '[data-assistant-exclude]',
  '.compact-assistant',
  '.global-assistant-dock',
  '.home-guide',
  '.app-shell__drawer',
  '.app-shell__notification-panel',
].join(',');

const providers = new Map();

const stripControlCharacters = (value) => [...String(value || '')]
  .filter((character) => {
    const code = character.charCodeAt(0);
    return code === 9 || code === 10 || code === 13 || (code >= 32 && code !== 127);
  })
  .join('');

const collapseWhitespace = (value) => stripControlCharacters(value)
  .replace(/[ \t\f\v]+/g, ' ')
  .replace(/\s*\n\s*/g, '\n')
  .trim();

function redactText(value, redactions) {
  let text = collapseWhitespace(value);
  const replacements = [
    [/\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi, '[已脱敏凭据]', 'credential'],
    [/\b(access[_-]?token|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]{6,}/gi, '$1=[已脱敏凭据]', 'credential'],
    [/[A-Z]:\\(?:[^\\\s]+\\)+[^\\\s]*/gi, '[已脱敏路径]', 'file_path'],
    [/\/(?:home|Users)\/[\w.-]+(?:\/[^\s]*)?/g, '[已脱敏路径]', 'file_path'],
    [/(?<!\d)1[3-9]\d{9}(?!\d)/g, '[已脱敏手机号]', 'phone'],
    [/(?<!\d)\d{17}[\dXx](?!\d)/g, '[已脱敏证件号]', 'identity_number'],
    [/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, '[已脱敏邮箱]', 'email'],
  ];
  replacements.forEach(([pattern, replacement, category]) => {
    if (pattern.test(text)) {
      redactions.add(category);
      text = text.replace(pattern, replacement);
    }
    pattern.lastIndex = 0;
  });
  return text;
}

function isVisible(element) {
  if (!(element instanceof Element)) return false;
  if (element.matches(EXCLUDED_SELECTOR) || element.closest(EXCLUDED_SELECTOR)) return false;
  const style = globalThis.getComputedStyle?.(element);
  return !style || (style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0');
}

function uniqueStrings(values, redactions, limit = MAX_ITEMS) {
  const seen = new Set();
  const result = [];
  for (const value of values) {
    const text = redactText(value, redactions);
    if (!text || seen.has(text)) continue;
    seen.add(text);
    result.push(text);
    if (result.length >= limit) break;
  }
  return result;
}

function labelForControl(control, root) {
  const ariaLabel = control.getAttribute('aria-label');
  if (ariaLabel) return ariaLabel;
  const enclosingLabel = control.closest('label');
  if (enclosingLabel) return enclosingLabel.textContent;
  if (control.id) {
    const matchingLabel = [...root.querySelectorAll('label')]
      .find((label) => label.htmlFor === control.id);
    if (matchingLabel) return matchingLabel.textContent;
  }
  return control.getAttribute('name') || control.getAttribute('placeholder') || control.tagName.toLowerCase();
}

function collectFormState(root, redactions) {
  const values = [];
  for (const control of root.querySelectorAll('input, textarea, select')) {
    if (!isVisible(control)) continue;
    const type = String(control.getAttribute('type') || control.tagName).toLowerCase();
    if (type === 'password' || /password|token|secret|authorization|cookie/i.test(control.name || control.id || '')) {
      redactions.add('sensitive_form_field');
      continue;
    }
    const item = {
      label: redactText(labelForControl(control, root), redactions).slice(0, 160),
      type,
      disabled: Boolean(control.disabled),
    };
    if (type === 'checkbox' || type === 'radio') item.checked = Boolean(control.checked);
    else if (control.tagName === 'SELECT') {
      item.value = redactText(
        [...control.selectedOptions].map((option) => option.textContent || option.value).join('、'),
        redactions,
      ).slice(0, 500);
    } else {
      item.value = redactText(control.value, redactions).slice(0, 500);
    }
    values.push(item);
    if (values.length >= MAX_ITEMS) break;
  }
  return values;
}

function collectRegions(root, redactions) {
  const regions = [];
  const candidates = root.querySelectorAll('section, article, [role="region"], [role="tabpanel"]');
  for (const region of candidates) {
    if (!isVisible(region)) continue;
    const heading = region.querySelector('h1, h2, h3, [role="heading"]');
    const label = redactText(
      region.getAttribute('aria-label')
      || region.getAttribute('title')
      || heading?.textContent
      || '',
      redactions,
    ).slice(0, 200);
    if (!label) continue;
    const regionText = visiblePageText(region, redactions, 2_000).text;
    const actions = uniqueStrings(
      [...region.querySelectorAll('button, a[href], [role="button"]')]
        .filter(isVisible)
        .map((element) => element.getAttribute('aria-label') || element.textContent),
      redactions,
      20,
    );
    regions.push({ label, text: regionText, actions });
    if (regions.length >= 20) break;
  }
  return regions;
}

function visiblePageText(root, redactions, maxChars) {
  const lines = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node) {
    const parent = node.parentElement;
    if (parent && !parent.matches('input, textarea, select') && isVisible(parent)) {
      const text = redactText(node.nodeValue, redactions);
      if (text) lines.push(text);
    }
    node = walker.nextNode();
  }
  // Animated headings often render one character per text node. Merge only
  // consecutive tiny CJK/punctuation fragments, while preserving repeated
  // words elsewhere on the page (global de-duplication corrupts prose).
  const mergedLines = [];
  const isTinyFragment = (value) => value.length <= 2 && /^[\p{Script=Han}\p{P}\p{S}A-Za-z0-9]+$/u.test(value);
  let fragmentBuffer = '';
  const flushFragments = () => {
    if (fragmentBuffer && fragmentBuffer !== mergedLines.at(-1)) mergedLines.push(fragmentBuffer);
    fragmentBuffer = '';
  };
  for (const line of lines) {
    if (isTinyFragment(line)) {
      fragmentBuffer += line;
      continue;
    }
    flushFragments();
    if (line !== mergedLines.at(-1)) mergedLines.push(line);
  }
  flushFragments();
  const text = mergedLines.join('\n');
  return {
    text: text.slice(0, maxChars),
    truncated: text.length > maxChars,
  };
}

function sanitizeSemanticValue(value, redactions, depth = 0) {
  if (depth > 4 || value == null) return null;
  if (typeof value === 'string') return redactText(value, redactions).slice(0, 2_000);
  if (typeof value === 'number' || typeof value === 'boolean') return value;
  if (Array.isArray(value)) {
    return value.slice(0, MAX_ITEMS).map((item) => sanitizeSemanticValue(item, redactions, depth + 1));
  }
  if (typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([key]) => !/password|token|secret|authorization|cookie/i.test(key))
        .slice(0, MAX_ITEMS)
        .map(([key, item]) => [key, sanitizeSemanticValue(item, redactions, depth + 1)]),
    );
  }
  return null;
}

export function registerPageContextProvider(pageType, provider) {
  if (!pageType || typeof provider !== 'function') throw new TypeError('page context provider is invalid');
  providers.set(pageType, provider);
  return () => {
    if (providers.get(pageType) === provider) providers.delete(pageType);
  };
}

export function readCurrentPage({
  pageType = 'unknown',
  pageTitle = '',
  root = globalThis.document?.querySelector('.app-shell__main'),
  maxTextChars = DEFAULT_MAX_TEXT_CHARS,
} = {}) {
  const redactions = new Set();
  const capturedAt = new Date().toISOString();
  if (!root) {
    return {
      schema_version: '1.0',
      tool_name: 'read_current_page',
      source: 'current_browser_page',
      trust_level: 'untrusted_page_content',
      page_type: pageType,
      page_title: redactText(pageTitle, redactions),
      captured_at: capturedAt,
      available: false,
      reason: 'page_root_unavailable',
      redactions: [],
    };
  }

  const pageText = visiblePageText(root, redactions, Math.max(1_000, maxTextChars));
  const headings = uniqueStrings(
    [...root.querySelectorAll('h1, h2, h3, [role="heading"]')]
      .filter(isVisible)
      .map((element) => element.textContent),
    redactions,
  );
  const availableActions = uniqueStrings(
    [...root.querySelectorAll('button, a[href], [role="button"]')]
      .filter(isVisible)
      .filter((element) => !element.closest('.compact-assistant'))
      .map((element) => element.getAttribute('aria-label') || element.textContent),
    redactions,
  );
  const selectedItems = uniqueStrings(
    [...root.querySelectorAll('[aria-current="page"], [aria-selected="true"], [data-state="active"]')]
      .filter(isVisible)
      .map((element) => element.getAttribute('aria-label') || element.textContent),
    redactions,
  );
  const alerts = uniqueStrings(
    [...root.querySelectorAll('[role="alert"], [role="status"]')]
      .filter(isVisible)
      .map((element) => element.textContent),
    redactions,
  );
  const provider = providers.get(pageType);
  let semanticContext = null;
  if (provider) {
    try {
      semanticContext = sanitizeSemanticValue(provider({ root, pageType }), redactions);
    } catch {
      semanticContext = { available: false, reason: 'page_provider_failed' };
    }
  }

  return {
    schema_version: '1.0',
    tool_name: 'read_current_page',
    source: 'current_browser_page',
    trust_level: 'untrusted_page_content',
    page_type: String(pageType || 'unknown').slice(0, 80),
    page_title: redactText(pageTitle || headings[0] || '', redactions).slice(0, 200),
    url_path: String(globalThis.location?.pathname || '/').slice(0, 300),
    captured_at: capturedAt,
    available: true,
    visible_text: pageText.text,
    headings,
    selected_items: selectedItems,
    form_state: collectFormState(root, redactions),
    regions: collectRegions(root, redactions),
    available_actions: availableActions,
    alerts,
    semantic_context: semanticContext,
    redactions: [...redactions].sort(),
    truncated: pageText.truncated,
  };
}

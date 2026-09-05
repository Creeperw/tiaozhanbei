// @vitest-environment node

import { describe, expect, it } from 'vitest';

import viteConfig from '../vite.config.js';

describe('Vite API proxy', () => {
  it('routes every API surface through the integrated backend', () => {
    expect(Object.keys(viteConfig.server.proxy)).toEqual(['/api/v1', '/api', '/health', '/platform-assets', '/knowledge-graph']);
    expect(viteConfig.server.proxy['/api/v1'].target).toBe('http://127.0.0.1:7860');
    expect(viteConfig.server.proxy['/api'].target).toBe('http://127.0.0.1:7860');
    expect(viteConfig.server.proxy['/api'].rewrite('/api/dashboard/home')).toBe('/dashboard/home');
    expect(viteConfig.server.proxy['/api/v1'].rewrite).toBeUndefined();
    expect(viteConfig.server.proxy['/health'].target).toBe('http://127.0.0.1:7860');
    expect(viteConfig.server.proxy['/health'].rewrite).toBeUndefined();
    expect(viteConfig.server.proxy['/platform-assets'].target).toBe('http://127.0.0.1:7860');
    expect(viteConfig.server.proxy['/knowledge-graph'].target).toBe('http://127.0.0.1:7860');
  });
});

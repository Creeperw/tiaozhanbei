// @vitest-environment node

import { describe, expect, it } from 'vitest';

import viteConfig from '../vite.config.js';

describe('Vite API proxy', () => {
  it('targets the FastAPI development port by default', () => {
    expect(viteConfig.server.proxy['/api'].target).toBe('http://127.0.0.1:7860');
    expect(viteConfig.server.proxy['/api/v1'].target).toBe('http://127.0.0.1:7860');
    expect(viteConfig.server.proxy['/api/v1'].rewrite).toBeUndefined();
    expect(viteConfig.server.proxy['/health'].target).toBe('http://127.0.0.1:7860');
    expect(viteConfig.server.proxy['/health'].rewrite).toBeUndefined();
  });
});

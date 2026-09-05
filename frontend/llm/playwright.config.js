import { defineConfig, devices } from '@playwright/test';
import { rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import process from 'node:process';

const frontendPort = Number(process.env.PLAYWRIGHT_FRONTEND_PORT || 14173);
const backendPort = Number(process.env.PLAYWRIGHT_BACKEND_PORT || 17860);
const baseURL = `http://127.0.0.1:${frontendPort}`;
const defaultDatabasePath = fileURLToPath(new URL('./test-results/e2e.db', import.meta.url));
const databasePath = process.env.PLAYWRIGHT_SQLITE_PATH || defaultDatabasePath;
const databaseUrl = `sqlite:///${databasePath.replaceAll('\\', '/')}`;
if (!process.env.PLAYWRIGHT_SQLITE_PATH) rmSync(defaultDatabasePath, { force: true });

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results/artifacts',
  globalTeardown: './e2e/global-teardown.js',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [['list']],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: [
    {
      command: `python -m uvicorn APP.backend.main:app --host 127.0.0.1 --port ${backendPort}`,
      url: `http://127.0.0.1:${backendPort}/openapi.json`,
      cwd: '../..',
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: {
        USE_SQLITE: 'true',
        DATABASE_URL: databaseUrl,
        SQLITE_PATH: databasePath,
        EMBEDDING_MODE: 'disabled',
        VOICE_MODE: 'disabled',
        ADMIN_DEFAULT_PASSWORD: process.env.PLAYWRIGHT_ADMIN_PASSWORD || 'Admin@123456',
      },
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${frontendPort}`,
      url: baseURL,
      cwd: '.',
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: { VITE_API_TARGET: `http://127.0.0.1:${backendPort}` },
    },
  ],
});

import { rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import process from 'node:process';

export default function globalTeardown() {
  if (process.env.PLAYWRIGHT_SQLITE_PATH) return;
  rmSync(fileURLToPath(new URL('../test-results/e2e.db', import.meta.url)), { force: true });
}

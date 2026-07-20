import { expect, test } from '@playwright/test';
import process from 'node:process';

const username = process.env.PLAYWRIGHT_ADMIN_USERNAME || 'admin';
const password = process.env.PLAYWRIGHT_ADMIN_PASSWORD || 'Admin@123456';

async function login(page) {
  await page.goto('/');
  await page.getByLabel('账号').fill(username);
  await page.getByLabel('密码').fill(password);
  await page.getByRole('button', { name: '进入时珍智训' }).click();
}

test('renders the training workshop in the compact workspace shell', async ({ page }) => {
  await login(page);
  await page.getByRole('link', { name: '训练工坊' }).first().click();

  await expect(page.getByRole('main')).toHaveAttribute('data-mode', 'workspace');
  await expect(page.locator('.app-shell__sidebar')).toHaveAttribute('data-collapsed', 'true');
  await expect(page.getByRole('heading', { name: '训练工坊' })).toHaveCount(0);
});

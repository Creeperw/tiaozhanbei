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

async function assertNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: document.documentElement.clientWidth,
  }));
  expect(overflow.documentWidth).toBeLessThanOrEqual(overflow.viewportWidth + 1);
}

test('renders the training overview and opens an existing workflow', async ({ page }) => {
  await login(page);
  await page.getByRole('link', { name: '训练工坊' }).first().click();

  await expect(page.getByRole('main')).toHaveAttribute('data-mode', 'workspace');
  await expect(page.locator('.app-shell__sidebar')).toHaveAttribute('data-collapsed', 'true');
  await expect(page.getByRole('heading', { name: '训练工坊，实战精进' })).toBeVisible();
  await expect(page.getByRole('button', { name: /综合套题/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /智能组卷/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /模拟病患/ })).toBeVisible();
  await assertNoHorizontalOverflow(page);

  await page.getByRole('button', { name: /历史记录/ }).click();
  await expect(page.getByRole('button', { name: '返回训练工坊' })).toBeVisible();
  await page.getByRole('button', { name: '返回训练工坊' }).click();
  await expect(page.getByRole('heading', { name: '训练工坊，实战精进' })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('heading', { name: '训练工坊，实战精进' })).toBeVisible();
  await assertNoHorizontalOverflow(page);
});

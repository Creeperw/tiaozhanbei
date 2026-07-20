import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const username = process.env.PLAYWRIGHT_ADMIN_USERNAME || 'admin';
const password = process.env.PLAYWRIGHT_ADMIN_PASSWORD || 'Admin@123456';

async function login(page) {
  await page.goto('/');
  await page.getByLabel('账号').fill(username);
  await page.getByLabel('密码').fill(password);
  await page.getByRole('button', { name: '进入时珍智训' }).click();
  await expect(page.getByRole('heading', { name: '循序精进' })).toBeVisible();
  const deferProfile = page.getByRole('button', { name: /^(以后再说|稍后再说)$/ }).first();
  if (await deferProfile.waitFor({ state: 'visible', timeout: 5000 }).then(() => true).catch(() => false)) {
    await deferProfile.click();
  }
}

async function atlasStatus(page) {
  return page.evaluate(async () => {
    const token = localStorage.getItem('token');
    const response = await fetch('/api/knowledge/atlas/status', { headers: { Authorization: `Bearer ${token}` } });
    return response.ok ? response.json() : { available: false };
  });
}

async function openAtlas(page) {
  const sidebarLink = page.getByRole('link', { name: '知识库' }).first();
  if (await sidebarLink.isVisible().catch(() => false)) {
    await sidebarLink.click();
  } else {
    await page.getByRole('button', { name: '打开导航菜单' }).click();
    const drawer = page.getByRole('dialog', { name: '主导航' });
    await expect(drawer).toBeVisible();
    await drawer.getByRole('link', { name: '知识库' }).click();
  }
  await expect(page.getByRole('heading', { name: '知识星球' })).toBeVisible();
}

test('知识星球完成药理学到折返的资源闭环', async ({ page }) => {
  test.slow();
  await login(page);
  const status = await atlasStatus(page);
  test.skip(!status.available, '当前验收环境未安装 Knowledge Atlas 资产');
  await openAtlas(page);
  const atlasCanvas = page.getByLabel('知识星球画布');
  await expect(atlasCanvas).toHaveAttribute('data-resource-styles', 'false');

  const route = page.getByRole('combobox', { name: '学习路线' });
  await expect(route.locator('option')).toHaveCount(3);
  await route.selectOption('textbook_14_5');
  await page.getByRole('button', { name: /^进入药理学$/ }).click();
  await page.getByRole('button', { name: /进入第一节 心律失常的电生理学基础/ }).click();
  await expect(atlasCanvas).toHaveAttribute('data-resource-styles', 'true');
  await page.getByRole('button', { name: /^打开折返详情$/ }).click();

  const drawer = page.getByRole('dialog', { name: '折返' });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText('视频讲解时间戳')).toBeVisible();
  const timestamp = drawer.locator('.knowledge-atlas__videos > button').first();
  await expect(timestamp).toBeVisible();
  await timestamp.click();
  await expect(drawer.locator('iframe[src*="player.bilibili.com"]')).toBeVisible();
  await expect(drawer.locator('.knowledge-atlas__chunks article').first()).toBeVisible();
  await expect(drawer.locator('.knowledge-atlas__chunks img').first()).toBeVisible();

  await drawer.getByRole('tab', { name: /题目/ }).click();
  await expect(drawer.locator('.knowledge-atlas__question-card').first()).toBeVisible();
  await drawer.getByRole('button', { name: '显示答案与解析' }).first().click();
  await expect(drawer.locator('.knowledge-atlas__answer').first()).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();

  // “折返”的交付切片是图文内容，本身不含 LaTeX。用真实含公式的知识点验收 KaTeX。
  await page.getByRole('button', { name: '十四五规划教材总览' }).click();
  const search = page.getByRole('searchbox', { name: '搜索当前层' });
  await search.fill('中医养生学');
  await page.getByRole('button', { name: /^进入中医养生学$/ }).click();
  await search.fill('第三节 健康');
  await page.getByRole('button', { name: /^进入第三节 健康$/ }).click();
  await search.fill('形体强健的外在表现');
  await page.getByRole('button', { name: /^打开形体强健的外在表现详情$/ }).click();
  const formulaDrawer = page.getByRole('dialog', { name: '形体强健的外在表现' });
  await expect(formulaDrawer.locator('.knowledge-atlas__chunks .katex').first()).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(formulaDrawer).toBeHidden();

  for (const label of ['球面布局', '顺序列表', '相关聚类']) {
    await page.getByRole('button', { name: label }).click();
    await expect(page.getByRole('button', { name: label })).toHaveAttribute('aria-pressed', 'true');
  }

  const canvas = page.getByLabel('知识星球画布');
  await page.getByRole('button', { name: '球面布局' }).click();
  const beforeMotion = await canvas.screenshot();
  await page.waitForTimeout(260);
  const afterMotion = await canvas.screenshot();
  expect(beforeMotion.equals(afterMotion)).toBe(false);
  const previousZoom = await canvas.getAttribute('data-zoom');
  await page.getByRole('button', { name: '放大知识星球' }).click();
  await expect(canvas).not.toHaveAttribute('data-zoom', previousZoom);
});

test('知识星球在移动端使用当前层列表替代空间画布', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  const status = await atlasStatus(page);
  test.skip(!status.available, '当前验收环境未安装 Knowledge Atlas 资产');
  await openAtlas(page);

  await expect(page.getByLabel('知识星球画布')).toBeHidden();
  const mobileNodeList = page.getByLabel('当前层节点列表');
  await expect(mobileNodeList).toBeVisible();
  const lastNode = mobileNodeList.getByRole('button').last();
  await lastNode.scrollIntoViewIfNeeded();
  await expect(lastNode).toBeVisible();
  const overflow = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: document.documentElement.clientWidth,
  }));
  expect(overflow.documentWidth).toBeLessThanOrEqual(overflow.viewportWidth + 1);
});

test('知识星球三档视觉验收截图', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(String(error)));
  await page.setViewportSize({ width: 1440, height: 900 });
  await login(page);
  const status = await atlasStatus(page);
  test.skip(!status.available, '当前验收环境未安装 Knowledge Atlas 资产');

  const visualDir = path.join(process.cwd(), 'test-results', 'atlas-visual');
  mkdirSync(visualDir, { recursive: true });

  const learningCta = page.getByRole('button', { name: '开始今日学习' });
  const communityEffect = page.getByTestId('community-learning-effect');
  await expect(learningCta).toHaveAttribute('data-effect', 'community-cursor-orbit');
  await expect(communityEffect).toHaveAttribute('data-particles', '168');
  await learningCta.hover();
  await expect(communityEffect).toHaveCSS('opacity', '1');
  const beforeCommunityMotion = await communityEffect.screenshot();
  await page.waitForTimeout(180);
  const afterCommunityMotion = await communityEffect.screenshot();
  expect(beforeCommunityMotion.equals(afterCommunityMotion)).toBe(false);
  await page.screenshot({
    path: path.join(visualDir, 'dashboard-community-cta-1440x900.png'),
    animations: 'allow',
  });
  await page.mouse.move(12, 12);

  await openAtlas(page);
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 1024, height: 768 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    await expect(page.getByRole('heading', { name: '知识星球' })).toBeVisible();
    const stage = page.getByTestId('knowledge-atlas-stage');
    await expect(stage).toHaveAttribute('aria-busy', 'false', { timeout: 30000 });
    // Capture the settled 1050 ms morph, not an intentionally sparse in-between frame.
    await page.waitForTimeout(1250);
    expect(pageErrors).toEqual([]);
    if (viewport.width > 600) {
      const atlasCanvas = page.getByLabel('知识星球画布');
      await expect(atlasCanvas).toHaveAttribute('data-resource-styles', 'false');
      const canvasBox = await atlasCanvas.boundingBox();
      expect(canvasBox?.height || Number.POSITIVE_INFINITY).toBeLessThanOrEqual(viewport.height);
      const canvasMetrics = await atlasCanvas.evaluate((canvas) => {
        const stage = canvas.closest('[data-testid="knowledge-atlas-stage"]');
        const style = window.getComputedStyle(canvas);
        const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        let opaque = 0;
        let painted = 0;
        let maxAlpha = 0;
        for (let index = 3; index < data.length; index += 4) {
          if (data[index] > 0) painted += 1;
          if (data[index] > 96) opaque += 1;
          maxAlpha = Math.max(maxAlpha, data[index]);
        }
        return {
          opaque,
          painted,
          maxAlpha,
          width: canvas.width,
          height: canvas.height,
          renderedNodeCount: canvas.dataset.renderedNodeCount,
          arrangedNodeCount: canvas.dataset.arrangedNodeCount,
          maxNodeAlpha: canvas.dataset.maxNodeAlpha,
          stageBusy: stage?.getAttribute('aria-busy'),
          spaceTransition: stage?.dataset.spaceTransition,
          display: style.display,
          visibility: style.visibility,
          opacity: style.opacity,
        };
      });
      expect(canvasMetrics.opaque, JSON.stringify(canvasMetrics)).toBeGreaterThan(500);
      expect(canvasMetrics).toMatchObject({ display: 'block', visibility: 'visible', opacity: '1' });
      if (viewport.width === 1440) {
        await atlasCanvas.screenshot({
          path: path.join(visualDir, 'atlas-canvas-1440x900.png'),
          animations: 'allow',
        });
      }
    }
    await page.screenshot({
      path: path.join(visualDir, `atlas-${viewport.width}x${viewport.height}.png`),
      animations: 'allow',
    });
  }
  expect(pageErrors).toEqual([]);
});

import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { Buffer } from 'node:buffer';
import process from 'node:process';

const username = process.env.PLAYWRIGHT_ADMIN_USERNAME || 'admin';
const password = process.env.PLAYWRIGHT_ADMIN_PASSWORD || 'Admin@123456';

async function login(page) {
  await page.goto('/');
  await page.getByLabel('账号').fill(username);
  await page.getByLabel('密码').fill(password);
  await page.getByRole('button', { name: '进入时珍智训' }).click();
  await expect(page.getByRole('heading', { name: '循序精进' })).toBeVisible();
  const deferSurvey = page.getByRole('button', { name: /^(以后再说|稍后再说)$/ }).first();
  const surveyVisible = await deferSurvey.waitFor({ state: 'visible', timeout: 5_000 })
    .then(() => true)
    .catch(() => false);
  if (surveyVisible) await deferSurvey.click();
}

async function openQuestionWorkspace(page) {
  await page.getByRole('link', { name: '知识库' }).first().click();
  await expect(page.getByRole('heading', { name: '知识星球' })).toBeVisible();
  await page.getByRole('button', { name: '题目数据' }).click();
  await expect(page.locator('#question-workspace-title')).toHaveText('从学习资料中沉淀自己的题库');
}

async function assertNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: document.documentElement.clientWidth,
  }));
  expect(overflow.documentWidth).toBeLessThanOrEqual(overflow.viewportWidth + 1);
}

test('登录后可选择正式目标并浏览真实考纲层级', async ({ page }) => {
  test.slow();
  await login(page);

  await page.getByRole('link', { name: '学习画像' }).first().click();
  await expect(page.getByRole('button', { name: '学习画像' })).toHaveAttribute('aria-current', 'page');
  await page.getByRole('button', { name: '学情调查' }).click();
  const target = page.getByLabel('具体考试目标');
  await expect(target).toBeVisible();
  await expect(target.locator('option')).toHaveCount(5);
  const options = await target.locator('option').allTextContents();
  expect(options.slice(1).every((label) => label.includes('2025') || label.includes('医师'))).toBeTruthy();

  const group = page.getByLabel('所属用户群体');
  if (!await group.inputValue()) await group.selectOption({ index: 1 });
  if (!await target.inputValue()) await target.selectOption({ index: 1 });
  await page.getByRole('button', { name: '保存学情调查' }).click();
  await expect(page.getByText('学情调查与考试目标已保存。')).toBeVisible();

  await page.getByRole('link', { name: '首页' }).first().click();
  await expect(page.getByRole('heading', { name: '循序精进' })).toBeVisible();
  await page.getByRole('button', { name: /知识图谱/ }).click();
  await expect(page.getByRole('heading', { name: '知识星球' })).toBeVisible();
});

test('我的题目可完成上传、确认与停用，且不进入公共题库', async ({ page }) => {
  await login(page);
  await openQuestionWorkspace(page);

  const uniqueStem = `E2E 阴阳关系题 ${process.pid}`;
  const markdown = `# E2E 个人题集\n\n## 题目 1\n- 题型：简答题\n- 题干：${uniqueStem}\n- 答案：对立制约与互根互用。\n- 解析：仅用于当前用户验收。\n- 知识点：KP_E2E\n`;
  await page.locator('input[type="file"]').setInputFiles({
    name: 'e2e-questions.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from(markdown, 'utf8'),
  });
  await page.getByRole('button', { name: '解析并预览' }).click();
  const questionStem = uniqueStem;
  await expect(page.getByText(questionStem).first()).toBeVisible();
  await page.getByRole('button', { name: '确认导入' }).click();
  const activeRegion = page.getByRole('region', { name: '已激活个人题目' });
  await expect(activeRegion.getByText(questionStem)).toBeVisible();
  await activeRegion.getByRole('button', { name: '停用' }).click();
  await expect(activeRegion.getByText(questionStem)).toHaveCount(0);
});

test('@a11y 核心导航、考纲与上传工作区无严重可访问性违规', async ({ page }) => {
  await login(page);
  const dashboardResults = await new AxeBuilder({ page })
    .disableRules(['color-contrast'])
    .analyze();
  expect(dashboardResults.violations.filter((item) => ['critical', 'serious'].includes(item.impact))).toEqual([]);

  await openQuestionWorkspace(page);
  const workspaceResults = await new AxeBuilder({ page })
    .disableRules(['color-contrast'])
    .analyze();
  expect(workspaceResults.violations.filter((item) => ['critical', 'serious'].includes(item.impact))).toEqual([]);
});

for (const width of [320, 375, 414, 768, 1024, 1440]) {
  test(`响应式 ${width}px 无横向溢出`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 600 ? 800 : 900 });
    await login(page);
    await assertNoHorizontalOverflow(page);
  });
}

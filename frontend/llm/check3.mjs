import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.setViewportSize({ width: 1440, height: 900 });

await page.goto('http://localhost:5174/', { waitUntil: 'networkidle', timeout: 30000 });
await page.waitForTimeout(500);
await page.locator('input[name="username"], input#auth-username, input[autocomplete="username"]').first().fill('admin');
await page.locator('input[name="password"], input#auth-password, input[type="password"]').first().fill('Admin@123456');
await page.locator('button[type="submit"]').first().click();
await page.waitForTimeout(2000);

try {
  const b = page.getByRole('button').filter({ hasText: /later|skip|以后|稍后/i }).first();
  await b.click({ timeout: 5000 });
} catch {}

// Click 学习路径
await page.getByRole('link', { name: '学习路径' }).click();
await page.waitForTimeout(3000);

// Wait for route to appear
try {
  await page.waitForSelector('.learning-path-page__route', { timeout: 5000 });
} catch {
  console.log('route never appeared');
}

const result = await page.evaluate(() => {
  const legend = document.querySelector('.learning-path-orbit__legend');
  const route = document.querySelector('.learning-path-page__route');
  const orbit = document.querySelector('.learning-path-orbit');
  const mainPage = document.querySelector('.app-shell__main');
  
  function s(el) {
    if (!el) return null;
    const cs = window.getComputedStyle(el);
    return { fontSize: cs.fontSize, overflow: cs.overflow, minHeight: cs.minHeight, width: cs.width };
  }
  
  return {
    dataPage: mainPage ? mainPage.getAttribute('data-page') : null,
    legendFound: !!legend,
    legendStyle: legend ? s(legend) : null,
    routeFound: !!route,
    routeStyle: route ? s(route) : null,
    orbitStyle: orbit ? s(orbit) : null,
  };
});

console.log(JSON.stringify(result, null, 2));

// Take a screenshot cropped to just the orbit area
const box = await page.locator('.learning-path-page__route').boundingBox().catch(() => null);
if (box) {
  await page.screenshot({ path: './lp-route-screenshot.png', fullPage: false, clip: { x: box.x, y: box.y, width: box.width, height: box.height } });
  console.log('Route screenshot saved (full route card)');
} else {
  await page.screenshot({ path: './lp-page-screenshot.png', fullPage: false });
  console.log('Full page screenshot saved');
}

await browser.close();

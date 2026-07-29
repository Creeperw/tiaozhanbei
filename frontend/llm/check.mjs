import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.setViewportSize({ width: 1440, height: 900 });

await page.goto('http://localhost:5174/', { waitUntil: 'networkidle', timeout: 30000 });
await page.waitForTimeout(500);
await page.locator('input[name="username"], input#auth-username, input[autocomplete="username"]').first().fill('admin');
await page.locator('input[name="password"], input#auth-password, input[type="password"]').first().fill('Admin@123456');
await page.locator('button[type="submit"]').first().click();
await page.waitForTimeout(1500);
const deferBtn = page.getByRole('button').filter({ hasText: /later|skip|以后|稍后/i }).first();
try { await deferBtn.click({ timeout: 2000 }); } catch {}

await page.locator('nav a, header a, .app-shell__nav a').filter({ hasText: /路径|学习|learn/i }).first().click();
await page.waitForTimeout(3000);

const result = await page.evaluate(() => {
  function getStyles(el) {
    if (!el) return 'NOT FOUND';
    const s = window.getComputedStyle(el);
    return {
      fontSize: s.fontSize,
      display: s.display,
      overflow: s.overflow,
      minHeight: s.minHeight,
      position: s.position,
    };
  }
  const legend = document.querySelector('.learning-path-orbit__legend');
  const legendItems = legend ? Array.from(legend.querySelectorAll('span')) : [];
  return {
    legend: getStyles(legend),
    legendItems: legendItems.map(el => window.getComputedStyle(el).fontSize),
    legendInnerText: legend ? legend.innerText : 'NOT FOUND',
    orbit: document.querySelector('.learning-path-orbit') ? {
      minHeight: window.getComputedStyle(document.querySelector('.learning-path-orbit')).minHeight
    } : 'NOT FOUND',
    route: document.querySelector('.learning-path-page__route') ? {
      overflow: window.getComputedStyle(document.querySelector('.learning-path-page__route')).overflow
    } : 'NOT FOUND',
  };
});

console.log(JSON.stringify(result, null, 2));
await page.screenshot({ path: './check-lp.png', fullPage: false });
console.log('Screenshot saved');
await browser.close();

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

// Try to dismiss survey with longer timeout
try {
  const b = page.getByRole('button').filter({ hasText: /later|skip|以后|稍后/i }).first();
  await b.click({ timeout: 5000 });
} catch {}

// Click 学习路径
await page.getByRole('link', { name: '学习路径' }).click();
await page.waitForTimeout(3000);

const result = await page.evaluate(() => {
  const main = document.querySelector('.app-shell__main');
  const dataPage = main ? main.getAttribute('data-page') : 'NO main found';
  const allLegend = Array.from(document.querySelectorAll('.learning-path-orbit__legend'));
  const allOrbit = Array.from(document.querySelectorAll('.learning-path-orbit'));
  const route = document.querySelector('.learning-path-page__route');
  const routeLayout = document.querySelector('.learning-path-page__route-orbit-layout');
  
  function style(el) {
    if (!el) return null;
    const s = window.getComputedStyle(el);
    return { fontSize: s.fontSize, overflow: s.overflow, minHeight: s.minHeight, width: s.width, height: s.height };
  }
  
  return {
    dataPage,
    legendCount: allLegend.length,
    orbitCount: allOrbit.length,
    routeFound: !!route,
    routeStyle: route ? style(route) : null,
    legends: allLegend.map(el => ({ fontSize: window.getComputedStyle(el).fontSize })),
    orbits: allOrbit.map(el => ({ minHeight: window.getComputedStyle(el).minHeight })),
  };
});

console.log(JSON.stringify(result, null, 2));
await page.screenshot({ path: './check2-lp.png', fullPage: false });
console.log('Done');
await browser.close();

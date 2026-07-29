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
const b = page.getByRole('button').filter({ hasText: /later|skip|以后|稍后/i }).first();
try { b.click({ timeout: 2000 }); } catch {}

// Find ALL links/buttons in nav area
const links = await page.evaluate(() => {
  const allEls = document.querySelectorAll('a, button, [role="link"], [role="button"]');
  return Array.from(allEls)
    .filter(el => el.textContent.trim().length > 0)
    .map(el => ({
      tag: el.tagName,
      text: el.textContent.trim().substring(0, 80),
      cls: String(el.className).substring(0, 100),
    }))
    .slice(0, 50);
});

console.log(JSON.stringify(links, null, 2));
await browser.close();

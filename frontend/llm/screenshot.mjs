import { chromium } from 'playwright';

const username = 'admin';
const password = 'Admin@123456';

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1000 } });
const page = await context.newPage();

await page.goto('http://localhost:5174/', { waitUntil: 'networkidle', timeout: 30000 });
await page.waitForTimeout(1000);

await page.locator('input[name="username"], input#auth-username, input[autocomplete="username"]').first().fill(username);
await page.locator('input[name="password"], input#auth-password, input[type="password"]').first().fill(password);
await page.locator('button[type="submit"]').first().click();
await page.waitForTimeout(2000);

const deferBtn = page.getByRole('button').filter({ hasText: /later|skip|以后|稍后/i }).first();
try { await deferBtn.click({ timeout: 3000 }); } catch {}

// Navigate to learning path page
const navLink = page.locator('nav a, header a, .app-shell__nav a').filter({ hasText: /路径|学习|learn/i }).first();
try { await navLink.click({ timeout: 5000 }); } catch {}

await page.waitForTimeout(2000);
await page.screenshot({ path: './test-screenshot-1920.png', fullPage: false, type: 'png' });
console.log('1920 screenshot done');
await browser.close();

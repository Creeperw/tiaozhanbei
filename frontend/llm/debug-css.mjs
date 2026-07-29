import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.setViewportSize({ width: 1920, height: 1000 });

await page.goto('http://localhost:5174/', { waitUntil: 'networkidle', timeout: 30000 });
await page.waitForTimeout(1000);
await page.locator('input[name="username"], input#auth-username, input[autocomplete="username"]').first().fill('admin');
await page.locator('input[name="password"], input#auth-password, input[type="password"]').first().fill('Admin@123456');
await page.locator('button[type="submit"]').first().click();
await page.waitForTimeout(2000);
const deferBtn = page.getByRole('button').filter({ hasText: /later|skip|以后|稍后/i }).first();
try { await deferBtn.click({ timeout: 3000 }); } catch {}
const navLink = page.locator('nav a, header a, .app-shell__nav a').filter({ hasText: /路径|学习|learn/i }).first();
try { await navLink.click({ timeout: 5000 }); } catch {}
await page.waitForTimeout(3000);

// Check CSS computed styles
const legendStyle = await page.evaluate(() => {
  const legend = document.querySelector('.learning-path-orbit__legend');
  if (!legend) return 'NOT FOUND';
  const style = window.getComputedStyle(legend);
  return {
    fontSize: style.fontSize,
    position: style.position,
    justifyContent: style.justifyContent,
    margin: style.margin,
    padding: style.padding,
    background: style.backgroundColor,
  };
});

const nodeStyle = await page.evaluate(() => {
  const node = document.querySelector('.learning-path-orbit__node');
  if (!node) return 'NOT FOUND';
  const style = window.getComputedStyle(node);
  const title = node.querySelector('.learning-path-orbit__title');
  const titleStyle = title ? window.getComputedStyle(title) : null;
  return {
    nodeFontSize: style.fontSize,
    titleFontSize: titleStyle ? titleStyle.fontSize : 'N/A',
  };
});

const emCount = await page.evaluate(() => document.querySelectorAll('.learning-path-orbit em').length);
const hintText = await page.evaluate(() => {
  const em = document.querySelector('.learning-path-orbit em');
  return em ? em.textContent : 'NO EM ELEMENT FOUND';
});

console.log('Legend styles:', JSON.stringify(legendStyle, null, 2));
console.log('Node styles:', JSON.stringify(nodeStyle, null, 2));
console.log('EM elements count:', emCount);
console.log('EM hint text:', hintText);

await browser.close();

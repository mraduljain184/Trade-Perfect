import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.goto('http://localhost:5173/login', { waitUntil: 'networkidle' });
await page.fill('input[placeholder="priya.sharma"]', 'priya.sharma');
await page.fill('input[type="password"]', 'Tp-QXSKz2Mtr2');
await page.click('button:has-text("Sign in")');
await page.waitForURL(/\/(trades|my-trades|repair-queue|dashboard)/, { timeout: 15000 });
await page.waitForTimeout(1500);
const menuText = await page.locator('.tp-sider-menu-wrap, .ant-menu').first().innerText().catch(() => '');
const headerLinks = await page.locator('.tp-app-header').innerText().catch(() => '');
const url = page.url();
console.log('URL:', url);
console.log('SIDEBAR:\n', menuText || '(empty)');
console.log('HEADER:\n', headerLinks);
await browser.close();

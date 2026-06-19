const puppeteer = require('puppeteer');
const path = require('path');

const DESKTOP = path.join(process.env.USERPROFILE, 'Desktop');
const BASE = 'https://127.0.0.1:9443';

async function clickByText(page, selector, text) {
  await page.evaluate((sel, txt) => {
    const els = document.querySelectorAll(sel);
    for (const el of els) {
      if (el.textContent.trim() === txt) { el.click(); return; }
    }
  }, selector, text);
}

(async () => {
  const browser = await puppeteer.launch({
    headless: true,
    ignoreHTTPSErrors: true,
    args: ['--allow-insecure-localhost', '--no-sandbox'],
    defaultViewport: { width: 1920, height: 1080 },
  });

  const page = await browser.newPage();

  // 1: Dashboard — workspace I with 3 rooms, 3-column layout
  await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 15000 });
  await page.waitForSelector('.room-card', { timeout: 10000 });
  await new Promise(r => setTimeout(r, 4000));
  await page.screenshot({
    path: path.join(DESKTOP, 'vigil_dashboard.png'),
    fullPage: false,
  });
  console.log('1/4 Dashboard with 3 rooms');

  // 2: Settings screen
  await page.evaluate(() => {
    const btns = document.querySelectorAll('button, [role="tab"]');
    for (const b of btns) {
      if (b.textContent.toLowerCase().includes('settings')) { b.click(); return; }
    }
    if (typeof showSettings === 'function') showSettings();
  });
  await new Promise(r => setTimeout(r, 3000));
  await page.screenshot({
    path: path.join(DESKTOP, 'vigil_settings.png'),
    fullPage: false,
  });
  console.log('2/4 Settings screen');

  // 3: Workspace II
  await page.evaluate(() => {
    const btns = document.querySelectorAll('button, [role="tab"]');
    for (const b of btns) {
      if (b.textContent.trim() === 'II') { b.click(); return; }
    }
  });
  await new Promise(r => setTimeout(r, 4000));
  await page.screenshot({
    path: path.join(DESKTOP, 'vigil_workspace2.png'),
    fullPage: false,
  });
  console.log('3/4 Workspace II');

  // 4: Back to workspace I, single column
  await page.evaluate(() => {
    const tabs = document.querySelectorAll('.ws-tab, [data-ws]');
    for (const t of tabs) {
      if (t.textContent.trim() === 'I') { t.click(); return; }
    }
    const btns = document.querySelectorAll('button');
    for (const b of btns) {
      if (b.textContent.trim() === 'I' && !b.classList.contains('active')) {
        b.click(); return;
      }
    }
  });
  await new Promise(r => setTimeout(r, 3000));

  // Click 1-column layout button
  await page.evaluate(() => {
    const btns = document.querySelectorAll('.col-btn, [data-cols]');
    for (const b of btns) {
      if (b.dataset.cols === '1' || b.textContent.trim() === 'I') {
        b.click(); return;
      }
    }
  });
  await new Promise(r => setTimeout(r, 2000));
  await page.screenshot({
    path: path.join(DESKTOP, 'vigil_detail.png'),
    fullPage: false,
  });
  console.log('4/4 Detail / single column');

  await browser.close();
  console.log('Done! 4 screenshots saved to Desktop.');
})();

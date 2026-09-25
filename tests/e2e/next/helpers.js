// Shared bits for the new panel's specs.
const { expect } = require('@playwright/test');
const { AxeBuilder } = require('@axe-core/playwright');

const PASSWORD = 'local-pass-1234';   // scripts/seed_local.py and e2e_up.sh, local only

/** Sign in through the API; the cookie lands in the page's own context. */
async function signIn(page, username) {
  const res = await page.request.post('/api/session/login', { data: { username, password: PASSWORD } });
  expect(res.ok(), await res.text()).toBeTruthy();
  return res.json();
}

/** Every CSP violation and page error the page reports while `fn` runs. */
function watchProblems(page) {
  const problems = [];
  page.on('console', (m) => {
    // E2E_DEV=1: specs pointed at `next dev`, whose own injected styles the
    // strict CSP refuses; the production build the suite normally runs has none.
    if (process.env.E2E_DEV && /Refused to apply inline style|Download the React DevTools|\[HMR\]|\[Fast Refresh\]/.test(m.text())) return;
    if (m.type() === 'error' || /Content Security Policy/i.test(m.text())) problems.push(m.text());
  });
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  return problems;
}

async function noHorizontalScroll(page) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(over, 'the page scrolls sideways').toBeLessThanOrEqual(0);
}

/** Scroll the whole page so every reveal-on-view section has rendered. */
async function scrollThrough(page) {
  const h = await page.evaluate(() => document.documentElement.scrollHeight);
  for (let y = 0; y <= h; y += 500) {
    await page.evaluate((top) => window.scrollTo(0, top), y);
    await page.waitForTimeout(60);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
}

/** Polls (not page.waitForFunction — an async predicate there is truthy as
 * soon as it returns a Promise, not once that promise resolves, so it never
 * actually waits) until public/sw.js has written `url` into `cacheName`. */
async function waitForServiceWorkerCache(page, cacheName, url, timeoutMs = 15_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const hit = await page.evaluate(
      async ([name, u]) => {
        const c = await caches.open(name);
        return (await c.match(u)) !== undefined;
      },
      [cacheName, url],
    );
    if (hit) return;
    await page.waitForTimeout(150);
  }
  throw new Error(`${url} never appeared in cache "${cacheName}" within ${timeoutMs}ms`);
}

/** Serious and critical WCAG 2 A/AA violations; the new panel has no baseline. */
async function a11y(page) {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  return r.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.help} @ ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
}

module.exports = {
  PASSWORD, signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y, waitForServiceWorkerCache,
};

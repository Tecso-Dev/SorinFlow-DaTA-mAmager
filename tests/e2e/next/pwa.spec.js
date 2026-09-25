// The panel/portal PWA: public/sw.js, the per-scope manifests, the icons it
// precaches, and the offline fallback pages. See docs/phase4/reports/
// p4-r5-pwa-email.md for what is live (the panel) and what is scaffolding
// only (the portal — still frontend/portal.html, not this Next app).
const { test, expect } = require('@playwright/test');
const { watchProblems, noHorizontalScroll, a11y, waitForServiceWorkerCache } = require('./helpers');

test.describe('manifests', () => {
  for (const scope of ['panel', 'portal']) {
    test(`/${scope}/manifest.webmanifest is valid and served as a manifest`, async ({ page }) => {
      const res = await page.request.get(`/${scope}/manifest.webmanifest`);
      expect(res.ok()).toBeTruthy();
      expect(res.headers()['content-type']).toContain('application/manifest+json');
      const m = await res.json();
      expect(m.name).toBeTruthy();
      expect(m.start_url).toBe(`/${scope}`);
      expect(m.scope).toBe(`/${scope}/`);
      expect(m.display).toBe('standalone');
      expect(m.dir).toBe('rtl');
      expect(m.lang).toBe('fa');
      // Chrome's installability check: an "any"-purpose icon at 192 and at
      // least 512, plus a maskable one it can safely crop.
      const anyIcons = m.icons.filter((i) => (i.purpose ?? 'any') === 'any');
      expect(anyIcons.some((i) => i.sizes === '192x192')).toBeTruthy();
      expect(anyIcons.some((i) => i.sizes.split('x').map(Number)[0] >= 512)).toBeTruthy();
      expect(m.icons.some((i) => i.purpose === 'maskable')).toBeTruthy();
      // every icon it lists actually exists and is a real PNG
      for (const icon of m.icons) {
        const iconRes = await page.request.get(icon.src);
        expect(iconRes.ok(), icon.src).toBeTruthy();
        expect(iconRes.headers()['content-type']).toBe('image/png');
      }
    });
  }

  test('the panel page links its own manifest, not the portal\'s', async ({ page }) => {
    await page.goto('/panel/login');
    const href = await page.locator('link[rel="manifest"]').getAttribute('href');
    expect(href).toBe('/panel/manifest.webmanifest');
  });

  test('apple-touch-icon and the browser-tab favicon are wired on the panel', async ({ page }) => {
    await page.goto('/panel/login');
    const apple = await page.locator('link[rel="apple-touch-icon"]').getAttribute('href');
    expect(apple).toBe('/icons/apple-touch-icon.png');
    const icon = await page.locator('link[rel="icon"][href="/icons/favicon-32.png"]').count();
    expect(icon).toBeGreaterThan(0);
    // Next 16 emits the current standard tag for appleWebApp.capable, not
    // the deprecated Apple-prefixed one (still present for the title/status
    // bar, which have no non-prefixed equivalent).
    const meta = await page.locator('meta[name="mobile-web-app-capable"]').getAttribute('content');
    expect(meta).toBe('yes');
    const title = await page.locator('meta[name="apple-mobile-web-app-title"]').getAttribute('content');
    expect(title).toBeTruthy();
  });
});

test.describe('service worker', () => {
  test('public/sw.js is served as JavaScript from the site root', async ({ page }) => {
    const res = await page.request.get('/sw.js');
    expect(res.ok()).toBeTruthy();
    expect(res.headers()['content-type']).toMatch(/javascript/);
    const body = await res.text();
    expect(body).toContain('self.addEventListener');
    expect(body).toContain('/panel/offline');
    expect(body).toContain('/portal/offline');
  });

  test('never claims /api/, /images/, /downloads/ or /dashboard/', async () => {
    const fs = require('node:fs');
    const path = require('node:path');
    const sw = fs.readFileSync(path.resolve(__dirname, '../../../frontend-next/public/sw.js'), 'utf8');
    expect(sw).toContain('"/api/"');
    expect(sw).toContain('"/images/"');
    expect(sw).toContain('"/downloads/"');
    expect(sw).toContain('"/dashboard/"');
    expect(sw).toContain('skipWaiting');
    expect(sw).toContain('clients.claim');
  });

  test('registers for /panel/ and installs, on a production build', async ({ page, browserName }) => {
    test.skip(browserName !== 'chromium', 'installability is a Chromium/Chrome concept');
    const problems = watchProblems(page);
    await page.goto('/panel/login');
    await waitForServiceWorkerCache(page, 'sf-pwa-v1-offline', '/panel/offline');
    const cached = await page.evaluate(async () => {
      const names = await caches.keys();
      const urls = [];
      for (const n of names) {
        const c = await caches.open(n);
        urls.push(...(await c.keys()).map((k) => new URL(k.url).pathname));
      }
      return urls;
    });
    expect(cached).toContain('/panel/offline');
    expect(cached.some((u) => u.startsWith('/icons/'))).toBeTruthy();
    expect(problems).toEqual([]);
  });
});

test.describe('offline pages', () => {
  for (const scope of ['panel', 'portal']) {
    test(`/${scope}/offline renders standalone, in Persian, with no horizontal scroll`, async ({ page }) => {
      const problems = watchProblems(page);
      await page.goto(`/${scope}/offline`);
      await expect(page.getByRole('heading', { name: 'به اینترنت وصل نیستید' })).toBeVisible();
      await expect(page.getByRole('button', { name: /تلاش دوباره/ })).toBeVisible();
      expect(await page.locator('html').getAttribute('dir')).toBe('rtl');
      await noHorizontalScroll(page);
      expect(await a11y(page)).toEqual([]);
      expect(problems).toEqual([]);
    });
  }

  test('/panel/offline is reachable with no session cookie at all', async ({ page }) => {
    // src/proxy.ts must exempt it from the login redirect — the whole point
    // of an offline fallback is that it never depends on anything live.
    await page.context().clearCookies();
    await page.goto('/panel/offline');
    await expect(page).toHaveURL(/\/panel\/offline$/);
  });

  test('the offline cache actually holds a real copy of the offline page', async ({ page }) => {
    await page.goto('/panel/login');
    await waitForServiceWorkerCache(page, 'sf-pwa-v1-offline', '/panel/offline');
    const cachedOfflinePage = await page.evaluate(async () => {
      const names = await caches.keys();
      for (const n of names) {
        const c = await caches.open(n);
        const hit = await c.match('/panel/offline');
        if (hit) return { status: hit.status, text: await hit.text() };
      }
      return null;
    });
    expect(cachedOfflinePage).not.toBeNull();
    expect(cachedOfflinePage.status).toBe(200);
    expect(cachedOfflinePage.text).toContain('اینترنت وصل نیستید');
  });
});

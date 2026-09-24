// Smoke: the panel actually comes up, for both an unrestricted account and a
// permission-limited one, and the main sections show the seeded data
// (scripts/seed_local.py) rather than an empty or broken screen.
const { test, expect } = require('@playwright/test');
const { loginAs } = require('../fixtures/auth');

// Console/page-error noise this box does not control. Each entry needs a
// reason — an empty match here is a real bug, not something to allowlist.
const ALLOWED_CONSOLE_ERRORS = [
  // frontend/landing.html loads Kavenegar's page-view widget from
  // cdn.kavenegar.com; a sandboxed/offline runner can't reach it, and that
  // has nothing to do with the panel under test.
  { pattern: /cdn\.kavenegar\.com/, reason: 'external SMS-panel widget on the landing page' },
];

function trackErrors(page) {
  const errors = [];
  page.on('pageerror', err => errors.push(`pageerror: ${err.message}`));
  page.on('console', msg => {
    if (msg.type() !== 'error') return;
    const text = msg.text();
    if (ALLOWED_CONSOLE_ERRORS.some(({ pattern }) => pattern.test(text))) return;
    errors.push(`console.error: ${text}`);
  });
  return errors;
}

test.describe('smoke', () => {
  test('root: dashboard loads and every main section renders seeded data', async ({ page, request }) => {
    const errors = trackErrors(page);
    await loginAs(page, request, 'root');
    await page.goto('/dashboard/');

    const html = page.locator('html');
    await expect(html).toHaveAttribute('dir', 'rtl');
    await expect(html).toHaveAttribute('lang', 'fa');
    await expect(page.locator('#main-app')).toBeVisible();
    await expect(page.locator('#login-page')).toBeHidden();

    // Properties (listings) — seed_local.py creates 20, tagged LOCAL-0001..0020.
    await page.locator('#nav-link-properties').click();
    await expect(page.locator('#properties-table .pt-title').first()).toBeVisible();

    // CRM -> "لیدها" (leads) tab: the sub-tab most directly tied to seeded rows.
    await page.locator('#nav-link-crm').click();
    await page.locator('button[data-bs-target="#crm-tab-leads"]').click();
    await expect(page.locator('#crm-leads-table tr').first()).toBeVisible();
    await expect(page.locator('#crm-leads-table')).not.toContainText('هیچ لیدی یافت نشد');

    // Scraper page — root has every permission (app/auth/permissions.py).
    await page.locator('#nav-link-scraper').click();
    await expect(page.locator('#section-scraper')).toBeVisible();

    // Monitoring — permission-gated; checked for root only (see the agent
    // test below, where it and scraper stay hidden on purpose).
    await page.locator('#nav-link-monitoring').click();
    await expect(page.locator('#section-monitoring')).toBeVisible();

    expect(errors).toEqual([]);
  });

  test('agent: dashboard loads within its own permissions, no errors', async ({ page, request }) => {
    const errors = trackErrors(page);
    await loginAs(page, request, 'agent');
    await page.goto('/dashboard/');

    await expect(page.locator('#main-app')).toBeVisible();

    await page.locator('#nav-link-properties').click();
    await expect(page.locator('#properties-table .pt-title').first()).toBeVisible();

    await page.locator('#nav-link-crm').click();
    await page.locator('button[data-bs-target="#crm-tab-leads"]').click();
    await expect(page.locator('#crm-leads-table tr').first()).toBeVisible();

    // agent1 has DEFAULT_ADMIN_PERMISSIONS (properties, crm, stats) only —
    // scraper/monitoring/divar-auth stay hidden rather than 403ing
    // (frontend/js/app.js's NAV_PERMISSION + applyRoleUI).
    await expect(page.locator('#nav-link-scraper')).toBeHidden();
    await expect(page.locator('#nav-link-monitoring')).toBeHidden();
    await expect(page.locator('#nav-link-auth')).toBeHidden();

    expect(errors).toEqual([]);
  });

  test('portal loads (redirects to the dashboard while public sign-up is off)', async ({ page }) => {
    const errors = trackErrors(page);
    await page.goto('/portal');
    // app/main.py's portal_page(): PUBLIC_AUTH_ENABLED=false (this run's
    // config, matching .env.local.example) meta-refreshes straight to /dashboard.
    // A regex, not a "**/dashboard**" glob: Playwright's glob "**" only spans
    // whole path segments, so pasted next to literal text like that it never
    // matches and the wait times out even though the redirect already happened.
    await page.waitForURL(/\/dashboard/);
    await expect(page.locator('body')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('landing page loads', async ({ page }) => {
    const errors = trackErrors(page);
    await page.goto('/');
    const html = page.locator('html');
    await expect(html).toHaveAttribute('dir', 'rtl');
    await expect(html).toHaveAttribute('lang', 'fa');
    await expect(page).toHaveTitle(/.+/);
    expect(errors).toEqual([]);
  });
});

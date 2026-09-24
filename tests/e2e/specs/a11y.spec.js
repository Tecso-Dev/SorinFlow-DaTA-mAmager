// Accessibility gate: axe-core against WCAG 2A + 2AA on the pages a real
// visitor or staff member actually lands on. See fixtures/a11y.js for what
// "new" means — this fails on critical/serious violations not already in
// the committed baseline, not on every pre-existing one.
const { test, expect } = require('@playwright/test');
const { loginAs } = require('../fixtures/auth');
const { checkA11y } = require('../fixtures/a11y');

test.describe('accessibility (axe, wcag2a + wcag2aa)', () => {
  test('landing page (/)', async ({ page }) => {
    await page.goto('/');
    expect(await checkA11y(page, '/')).toEqual([]);
  });

  test('portal (follows the redirect to the dashboard)', async ({ page }) => {
    await page.goto('/portal');
    // PUBLIC_AUTH_ENABLED=false in this run -> meta-refresh to /dashboard.
    // Regex, not a glob — see smoke.spec.js's identical wait for why.
    await page.waitForURL(/\/dashboard/);
    expect(await checkA11y(page, '/portal -> /dashboard/')).toEqual([]);
  });

  test('dashboard — root, after the dashboard section renders', async ({ page, request }) => {
    await loginAs(page, request, 'root');
    await page.goto('/dashboard/');
    await expect(page.locator('#main-app')).toBeVisible();
    expect(await checkA11y(page, '/dashboard/ (root)')).toEqual([]);
  });

  test('dashboard — agent, after the dashboard section renders', async ({ page, request }) => {
    await loginAs(page, request, 'agent');
    await page.goto('/dashboard/');
    await expect(page.locator('#main-app')).toBeVisible();
    expect(await checkA11y(page, '/dashboard/ (agent)')).toEqual([]);
  });

  test('properties section', async ({ page, request }) => {
    await loginAs(page, request, 'root');
    await page.goto('/dashboard/');
    await page.locator('#nav-link-properties').click();
    await expect(page.locator('#properties-table .pt-title').first()).toBeVisible();
    expect(await checkA11y(page, '/dashboard/ properties section')).toEqual([]);
  });

  test('crm leads section', async ({ page, request }) => {
    await loginAs(page, request, 'root');
    await page.goto('/dashboard/');
    await page.locator('#nav-link-crm').click();
    await page.locator('button[data-bs-target="#crm-tab-leads"]').click();
    await expect(page.locator('#crm-leads-table tr').first()).toBeVisible();
    expect(await checkA11y(page, '/dashboard/ crm leads section')).toEqual([]);
  });
});

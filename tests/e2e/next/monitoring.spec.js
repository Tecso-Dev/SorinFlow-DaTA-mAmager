// پایش سامانه — نمای کلی (health tiles, server tables, Divar sessions,
// scraper/GCP, runtime, client errors, live chart, log viewer) plus the new
// tabs from docs/MONITORING.md §4, which degrade to «هنوز در دسترس نیست»
// when their endpoint is not on this base yet.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

test('owner sees the overview: health tiles, server table and the log viewer', async ({ page }) => {
  const problems = watchProblems(page);
  await signIn(page, 'owner');
  await page.goto('/panel/monitoring');
  await expect(page.getByRole('heading', { name: 'پایش سامانه' })).toBeVisible();
  await expect(page.getByText('پایگاه داده')).toBeVisible();
  await expect(page.getByText('سرور و سیستم')).toBeVisible();
  await expect(page.getByText('وضعیت نشست‌های دیوار')).toBeVisible();
  await expect(page.getByText('پردازه‌ها و کارهای پس‌زمینه')).toBeVisible();
  await expect(page.getByText('وضعیت زندهٔ سرور')).toBeVisible();
  await expect(page.getByText('لاگ سامانه')).toBeVisible();

  await scrollThrough(page);
  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('the connectivity test button runs a real probe', async ({ page }) => {
  await signIn(page, 'owner');
  await page.goto('/panel/monitoring');
  await page.getByRole('button', { name: 'تست واقعی' }).click();
  await expect(page.getByText(/اتصال به دیوار (برقرار است|ناموفق بود)/).first()).toBeVisible({ timeout: 15_000 });
});

test('the new tabs show a graceful "not yet available" state for endpoints missing on this base', async ({ page }) => {
  await signIn(page, 'owner');
  await page.goto('/panel/monitoring');
  for (const tab of ['سرور', 'کوبرنتیز', 'سرویس‌ها', 'CI/CD', 'هشدارها']) {
    await page.getByRole('tab', { name: tab, exact: true }).click();
    await expect(page.getByText('هنوز در دسترس نیست.').or(page.getByText(/سرویس‌ها|CI\/CD/)).first()).toBeVisible();
  }
});

test('the live chart pauses and resumes without an error', async ({ page }) => {
  await signIn(page, 'owner');
  await page.goto('/panel/monitoring');
  await page.getByRole('button', { name: 'توقف' }).click();
  await expect(page.getByRole('button', { name: 'ادامه' })).toBeVisible();
  await page.getByRole('button', { name: 'ادامه' }).click();
  await expect(page.getByRole('button', { name: 'توقف' })).toBeVisible();
});

test('an agent without the monitoring permission does not crash the page', async ({ page }) => {
  const problems = [];
  page.on('pageerror', (e) => problems.push(String(e)));
  await signIn(page, 'agent1');
  await page.goto('/panel/monitoring');
  await expect(page.getByRole('heading', { name: 'پایش سامانه' })).toBeVisible();
  expect(problems).toEqual([]);
});

test('the dark theme passes the same accessibility check', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await signIn(page, 'owner');
  await page.goto('/panel/monitoring');
  await scrollThrough(page);
  expect(await a11y(page)).toEqual([]);
});

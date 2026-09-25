// /panel/audit (root/super_admin only): actor/action/date filters, table,
// pagination.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

test('an admin without root/super_admin is refused', async ({ page }) => {
  await signIn(page, 'agent1');
  await page.goto('/panel/audit');
  await expect(page.getByText('این بخش فقط برای root و مدیر ارشد است.')).toBeVisible();
});

test('owner sees the audit trail with the login events this suite just made', async ({ page }) => {
  const problems = watchProblems(page);
  await signIn(page, 'owner');
  await page.goto('/panel/audit');
  await expect(page.getByRole('heading', { name: 'رویدادها', level: 1 })).toBeVisible();
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.getByRole('table').getByText('ورود موفق').first()).toBeVisible();
  await scrollThrough(page);
  await page.waitForTimeout(700); // let every Reveal section finish fading in before axe reads it
  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('filtering by actor narrows the table', async ({ page }) => {
  await signIn(page, 'root');
  await page.goto('/panel/audit');
  await page.getByLabel('کاربر').fill('agent1');
  await page.waitForTimeout(500);
  const rows = page.getByRole('row');
  await expect(rows.filter({ hasText: 'root' })).toHaveCount(0);
});

test('the action filter narrows the table to login events only', async ({ page }) => {
  await signIn(page, 'root');
  await page.goto('/panel/audit');
  await page.getByLabel('عملکرد').selectOption({ label: 'ورود موفق' });
  await page.waitForTimeout(500);
  const bodyRows = page.getByRole('row').filter({ hasNotText: 'زمان' });
  const count = await bodyRows.count();
  expect(count).toBeGreaterThan(0);
  for (let i = 0; i < count; i++) {
    await expect(bodyRows.nth(i)).toContainText('ورود موفق');
  }
});

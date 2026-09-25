// /panel/users (root/super_admin only): access tickets, maintenance mode,
// backup+DR cards, and the team table (search/filters, permission editor
// with the root-demotion guard, new user).
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

test('an admin without root/super_admin is refused, not shown the page', async ({ page }) => {
  await signIn(page, 'agent1');
  await page.goto('/panel/users');
  await expect(page.getByText('این بخش فقط برای root و مدیر ارشد است.')).toBeVisible();
  await expect(page.getByText('اعضای تیم')).toHaveCount(0);
});

test('owner sees every card and the seeded team', async ({ page }) => {
  const problems = watchProblems(page);
  await signIn(page, 'owner');
  await page.goto('/panel/users');
  await expect(page.getByRole('heading', { name: 'کاربران و بکاپ' })).toBeVisible();
  await expect(page.getByText('درخواست‌های دسترسی به پنل')).toBeVisible();
  await expect(page.getByText('حالت تعمیر سایت')).toBeVisible();
  await expect(page.getByText('بکاپ و نسخهٔ خارج از سرور')).toBeVisible();
  await expect(page.getByText('بکاپ کامل (DR)')).toBeVisible();
  const table = page.getByRole('table');
  await expect(table.getByText('علی رضایی')).toBeVisible();
  await expect(table.getByText('سارا محمدی')).toBeVisible();
  await scrollThrough(page);
  await page.waitForTimeout(700); // let every Reveal section finish fading in before axe reads it
  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('search narrows the team table', async ({ page }) => {
  await signIn(page, 'owner');
  await page.goto('/panel/users');
  await page.locator('#team-search').fill('زهرا');
  await expect(page.getByRole('table').getByText('زهرا کریمی')).toBeVisible();
  await expect(page.getByRole('table').getByText('علی رضایی')).toHaveCount(0);
});

test('super_admin does not see root in the team table at all', async ({ page }) => {
  // GET /users filters root rows out for anyone but root (users.py:
  // "if _.role != ROLE_ROOT: users = [u for u in users if u.role != ROLE_ROOT]")
  // — super_admin cannot even see the account it can't touch.
  await signIn(page, 'owner');
  await page.goto('/panel/users');
  await expect(page.getByRole('table').getByText('Root', { exact: true })).toHaveCount(0);
});

test('the permission editor preselects a root account\'s actual role (regression guard)', async ({ page }) => {
  // The old panel's bug: the role select fell back to "admin" for any role
  // it didn't recognise, and root matched nothing in that list — opening
  // this editor on a root account silently demoted it on save. root itself
  // is the only account that may open the editor on a root row at all.
  await signIn(page, 'root');
  await page.goto('/panel/users');
  const row = page.locator('tr', { hasText: 'Root' }).first();
  await row.getByRole('button', { name: 'عملیات کاربر' }).click();
  await page.getByRole('menuitem', { name: 'نقش و دسترسی‌ها' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByLabel('نقش')).toHaveValue('root');
  await page.keyboard.press('Escape');
});

test('a new admin can be created and then deleted', async ({ page }) => {
  const username = `e2e-r3a-${Date.now()}`;
  await signIn(page, 'owner');
  await page.goto('/panel/users');
  await page.getByRole('button', { name: 'کاربر تازه' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.locator('#nu-username').fill(username);
  await dialog.locator('#nu-fullname').fill('کاربر آزمایشی');
  await dialog.locator('#nu-password').fill('local-pass-1234');
  await dialog.getByRole('button', { name: 'ساخت کاربر' }).click();
  await expect(page.getByText('کاربر ساخته شد').first()).toBeVisible();
  await page.locator('#team-search').fill(username);
  const row = page.locator('tr', { hasText: username });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: 'عملیات کاربر' }).click();
  await page.getByRole('menuitem', { name: 'حذف' }).click();
  const confirmDialog = page.getByRole('dialog').filter({ hasText: 'حذف کاربر' });
  await confirmDialog.getByRole('button', { name: 'حذف' }).click();
  await expect(page.getByText('حذف شد').first()).toBeVisible();
  await expect(page.locator('tr', { hasText: username })).toHaveCount(0);
});

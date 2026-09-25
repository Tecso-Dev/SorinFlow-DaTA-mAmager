// /panel/settings (root only): every GET/PUT /api/settings/site field, with
// a live preview mirroring the login page's brand corner.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

test('super_admin (not root) is refused', async ({ page }) => {
  await signIn(page, 'owner');
  await page.goto('/panel/settings');
  await expect(page.getByText('این بخش فقط برای root است.')).toBeVisible();
});

test('root sees the form and a live preview', async ({ page }) => {
  const problems = watchProblems(page);
  await signIn(page, 'root');
  await page.goto('/panel/settings');
  await expect(page.getByRole('heading', { name: 'برند و سایت' })).toBeVisible();
  await expect(page.getByText('پیش‌نمایش زندهٔ صفحهٔ ورود')).toBeVisible();
  await scrollThrough(page);
  await page.waitForTimeout(700); // let every Reveal section finish fading in before axe reads it
  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('editing the office name updates the preview live and persists on save', async ({ page }) => {
  const stamp = `دفتر-${Date.now()}`;
  await signIn(page, 'root');
  await page.goto('/panel/settings');
  await page.locator('#site-agencyName').fill(stamp);
  // live: no save yet, the preview already reflects the typed value
  await expect(page.getByText(`${stamp} در یک جا`)).toBeVisible();
  await page.getByRole('button', { name: 'ذخیرهٔ تنظیمات' }).click();
  await expect(page.getByText('ذخیره شد').first()).toBeVisible();
  await page.reload();
  await expect(page.locator('#site-agencyName')).toHaveValue(stamp);
  // clean up: back to the empty default so the shared local backend
  // doesn't carry this test's name into another stream's screenshots
  await page.locator('#site-agencyName').fill('');
  await page.getByRole('button', { name: 'ذخیرهٔ تنظیمات' }).click();
  await expect(page.getByText('ذخیره شد').first()).toBeVisible();
});

test('saving with nothing changed is refused instead of calling the API', async ({ page }) => {
  const calls = [];
  await signIn(page, 'root');
  await page.goto('/panel/settings');
  page.on('request', (r) => { if (r.url().includes('/settings/site') && r.method() === 'PUT') calls.push(r.url()); });
  await page.getByRole('button', { name: 'ذخیرهٔ تنظیمات' }).click();
  await expect(page.getByText('چیزی تغییر نکرده است').first()).toBeVisible();
  expect(calls).toEqual([]);
});

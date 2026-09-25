// The admin-facing «درخواست‌های مشتریان» table at /panel/portal
// (docs/phase4/inventory-system.md §8): status filter, inline status
// PATCH-on-change, and the «ملک‌های مناسب» shortcut for a request already
// bridged into a CRM customer. A visitor request is created through the
// public portal first, so the table has something real to show.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, a11y } = require('./helpers');

function freshPhone() {
  return '09' + String(Date.now()).slice(-9);
}

async function createVisitorRequest(page, city) {
  const phone = freshPhone();
  const name = `e2e-admin-portal-${Date.now()}`;
  await page.goto('/portal');
  await page.getByRole('tab', { name: 'ثبت‌نام' }).click();
  await page.locator('#rg-name').fill(name);
  await page.locator('#rg-phone').fill(phone);
  await page.locator('#rg-email').fill(`${phone}@example.com`);
  await page.locator('#rg-pass').fill('Str0ngPassw0rd!');
  await page.getByRole('button', { name: 'ثبت‌نام و دریافت کد' }).click();
  const code = (await page.getByText(/کد تست: \d+/).textContent()).replace(/\D/g, '');
  await page.getByLabel('کد تأیید').fill(code);
  await page.getByRole('button', { name: 'تأیید و ورود' }).click();
  await page.waitForURL('**/portal/me');
  await page.getByLabel('شهر').fill(city);
  await page.getByRole('button', { name: 'ثبت درخواست' }).click();
  await expect(page.getByText('درخواست شما ثبت شد', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'خروج' }).click();
  await page.waitForURL('**/portal');
  return { name, phone };
}

test('admin table: loads with data, status filter, inline PATCH, matches shortcut', async ({ page }) => {
  const problems = watchProblems(page);
  const city = `e2e-city-${Date.now()}`;
  const { name } = await createVisitorRequest(page, city);

  await signIn(page, 'owner');
  await page.goto('/panel/portal');
  await expect(page.getByRole('heading', { name: 'درخواست‌های مشتریان' })).toBeVisible();
  await expect(page.getByRole('table')).toBeVisible();

  const row = page.getByRole('row', { name: new RegExp(name) });
  await expect(row).toBeVisible();
  await expect(row.getByText('در موتور تطبیق')).toBeVisible();
  await expect(row.getByRole('button', { name: 'ملک‌های مناسب' })).toBeVisible();

  // status filter narrows the list, then clears back to everything
  await page.getByLabel('فیلتر وضعیت').selectOption('in_review');
  await page.waitForResponse((r) => r.url().includes('/api/portal/admin/requests') && r.url().includes('status=in_review') && r.ok());
  await expect(row).toHaveCount(0);
  await page.getByLabel('فیلتر وضعیت').selectOption('');
  await expect(row).toBeVisible();

  // inline status select PATCHes on change
  await row.getByLabel('تغییر وضعیت درخواست').selectOption('in_review');
  await page.waitForResponse((r) => r.url().includes('/api/portal/admin/requests/') && r.request().method() === 'PATCH' && r.ok());
  await page.reload();
  const rowAfter = page.getByRole('row', { name: new RegExp(name) });
  await expect(rowAfter.getByLabel('تغییر وضعیت درخواست')).toHaveValue('in_review');

  // the matching-engine shortcut opens the shared customer-matches sheet
  await rowAfter.getByRole('button', { name: 'ملک‌های مناسب' }).click();
  await expect(page.getByRole('heading', { name: 'ملک‌های پیشنهادی' })).toBeVisible();
  await page.keyboard.press('Escape');

  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

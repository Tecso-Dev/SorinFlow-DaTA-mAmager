// The public visitor portal at /portal: sign-up → verify → signed-in
// dashboard, a property request created/listed/deleted (delete through the
// panel's own confirm dialog, never window.confirm), and the panel-access
// ticket. Runs against PUBLIC_AUTH_ENABLED=true — see docs/phase4 for how
// this stream's own server was started for these specs.
const { test, expect } = require('@playwright/test');
const { watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

function freshPhone() {
  return '09' + String(Date.now()).slice(-9);
}

test('landing page: tabs, phone/email/password validation, hero', async ({ page }) => {
  const problems = watchProblems(page);
  await page.goto('/portal');
  await expect(page.getByRole('heading', { name: 'پورتال مشتریان' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'ورود' })).toBeVisible();

  await page.getByRole('tab', { name: 'ثبت‌نام' }).click();
  await expect(page.locator('#rg-name')).toBeVisible();

  // blur validation: an invalid phone shows an inline error, not a native popup
  await page.locator('#rg-phone').fill('0912');
  await page.locator('#rg-email').click();
  await expect(page.getByText('شماره باید با فرمت ۰۹۱۲۳۴۵۶۷۸۹ باشد')).toBeVisible();

  // password strength meter reacts to what is typed
  await page.locator('#rg-pass').fill('weak');
  await expect(page.getByText('خیلی ضعیف')).toBeVisible();
  await page.locator('#rg-pass').fill('Str0ngPassw0rd!');
  await expect(page.getByText('قوی')).toBeVisible();

  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('sign-up, verify with the debug code, land on the signed-in dashboard', async ({ page }) => {
  const problems = watchProblems(page);
  const phone = freshPhone();
  const name = `e2e-portal-${Date.now()}`;

  await page.goto('/portal');
  await page.getByRole('tab', { name: 'ثبت‌نام' }).click();
  await page.locator('#rg-name').fill(name);
  await page.locator('#rg-phone').fill(phone);
  await page.locator('#rg-email').fill(`${phone}@example.com`);
  await page.locator('#rg-pass').fill('Str0ngPassw0rd!');
  await page.getByRole('button', { name: 'ثبت‌نام و دریافت کد' }).click();

  // debug_code is only ever present off-production, and only that value
  // proves the channel-aware hint text actually did its job first
  const codeBox = page.getByText(/کد تست: \d+/);
  await expect(codeBox).toBeVisible({ timeout: 10_000 });
  const code = (await codeBox.textContent()).replace(/\D/g, '');
  await page.getByLabel('کد تأیید').fill(code);
  await page.getByRole('button', { name: 'تأیید و ورود' }).click();

  await page.waitForURL('**/portal/me');
  await expect(page.getByRole('heading', { name: 'دنبال چه ملکی هستید؟' })).toBeVisible();
  await expect(page.getByText(name)).toBeVisible();

  await noHorizontalScroll(page);
  await scrollThrough(page);
  await page.waitForTimeout(700); // let every Reveal's opacity transition settle before axe reads it
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('request create/list/delete, buy/rent fields swap, ticket submit', async ({ page }) => {
  const problems = watchProblems(page);
  const phone = freshPhone();
  const name = `e2e-portal-req-${Date.now()}`;

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

  // buy fields show by default, rent fields swap in
  await expect(page.getByLabel('حداقل بودجه (تومان)')).toBeVisible();
  await page.getByLabel('نوع معامله').selectOption('rent');
  await expect(page.getByLabel('حداکثر ودیعه (تومان)')).toBeVisible();
  await expect(page.getByLabel('حداقل بودجه (تومان)')).toHaveCount(0);

  await page.getByLabel('شهر').fill('ارومیه');
  await page.getByLabel('توضیحات بیشتر').fill('یک واحد دو خوابه نزدیک مرکز شهر');
  await page.getByRole('button', { name: 'ثبت درخواست' }).click();
  await expect(page.getByText('درخواست شما ثبت شد', { exact: true })).toBeVisible();

  const item = page.locator('li', { hasText: 'اجاره · ارومیه' }).first();
  await expect(item).toBeVisible();
  await expect(item.getByText('یک واحد دو خوابه نزدیک مرکز شهر')).toBeVisible();

  // delete — the panel's own confirm dialog, never window.confirm
  await item.getByRole('button', { name: 'حذف' }).click();
  const confirmDialog = page.getByRole('dialog').filter({ hasText: 'این درخواست حذف شود؟' });
  await expect(confirmDialog).toBeVisible();
  await confirmDialog.getByRole('button', { name: 'تأیید' }).click();
  await expect(page.getByText('هنوز درخواستی ثبت نکرده‌اید.')).toBeVisible();

  // the access ticket: no ticket yet → form → pending state
  await page.getByLabel('توضیح کوتاه (اختیاری)').fill('مشاور املاک هستم و به پنل نیاز دارم');
  await page.getByRole('button', { name: 'ارسال درخواست دسترسی' }).click();
  await expect(page.getByText('در حال بررسی')).toBeVisible();
  await expect(page.getByText('درخواست شما برای مدیر ارسال شده و در انتظار بررسی است.')).toBeVisible();

  await noHorizontalScroll(page);
  await scrollThrough(page);
  await page.waitForTimeout(700); // let every Reveal's opacity transition settle before axe reads it
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('signed out on logout, and a stale session bounces back to the landing page', async ({ page }) => {
  const phone = freshPhone();
  const name = `e2e-portal-logout-${Date.now()}`;
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

  await page.getByRole('button', { name: 'خروج' }).click();
  await page.waitForURL('**/portal');
  await expect(page.getByRole('tab', { name: 'ورود' })).toBeVisible();

  // visiting the signed-in page with no session bounces back here too
  await page.goto('/portal/me');
  await page.waitForURL('**/portal');
});

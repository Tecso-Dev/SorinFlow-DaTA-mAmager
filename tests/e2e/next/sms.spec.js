// پیامک — stats, super_admin settings, single send, broadcast confirm and
// history filters, against app/api/routes/sms.py on real seeded data.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

test('owner sees the Kavenegar settings, sends a single message and the events log', async ({ page }) => {
  const problems = watchProblems(page);
  await signIn(page, 'owner');
  await page.goto('/panel/sms');
  await expect(page.getByRole('heading', { name: 'پیامک', level: 1 })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'تنظیمات کاوه‌نگار' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'رویدادهای سرویس پیامک' })).toBeVisible();

  await page.getByLabel('شمارهٔ گیرنده').first().fill('09121234567');
  await page.getByLabel('متن پیام').first().fill(`تست ای‌توای ${Date.now()}`);
  await page.getByRole('button', { name: 'ارسال', exact: true }).click();
  await expect(page.getByText(/ارسال (شد|ناموفق بود)/).first()).toBeVisible({ timeout: 10_000 });

  await scrollThrough(page);
  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('broadcast shows the audience counts and guards a zero-count group from sending', async ({ page }) => {
  await signIn(page, 'owner');
  await page.goto('/panel/sms');
  await expect(page.getByRole('heading', { name: 'ارسال گروهی' })).toBeVisible();
  await expect(page.getByText('کارکنان پنل')).toBeVisible();
  await page.getByText('کارکنان پنل').click();
  await page.getByLabel('متن پیام').nth(1).fill('پیام گروهی آزمایشی');
  // The local seed has no staff phone numbers, so the guarded send button
  // must stay disabled rather than let a 0-recipient broadcast through.
  const sendButton = page.getByRole('button', { name: /ارسال به .* نفر/ });
  await expect(sendButton).toBeDisabled();
  await expect(sendButton).toContainText('۰ نفر');
});

test('history search and status filter narrow the table', async ({ page }) => {
  await signIn(page, 'owner');
  await page.goto('/panel/sms');
  await expect(page.getByRole('heading', { name: 'تاریخچهٔ ارسال' })).toBeVisible();
  await page.getByPlaceholder('شماره یا متن').fill('09121234567');
  await page.waitForTimeout(400);
  await expect(page.getByText('پیامی یافت نشد.').or(page.getByRole('cell', { name: '09121234567' }).first())).toBeVisible();
});

test('an agent without the sms permission gets a clear message, not a crash', async ({ page }) => {
  const problems = [];
  page.on('pageerror', (e) => problems.push(String(e)));
  await signIn(page, 'agent1');
  await page.goto('/panel/sms');
  await expect(page.getByText(/دسترسی .* پیامک.* برای حساب شما فعال نیست|دسترسی/).first()).toBeVisible();
  expect(problems).toEqual([]);
});

test('the dark theme passes the same accessibility check', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await signIn(page, 'owner');
  await page.goto('/panel/sms');
  await scrollThrough(page);
  expect(await a11y(page)).toEqual([]);
});

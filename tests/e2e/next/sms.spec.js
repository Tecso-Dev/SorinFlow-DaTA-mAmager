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
  // scoped to the toast region (toaster.tsx): a page-wide text search also
  // matches the history filter's own always-present <option value="sent">
  // ارسال شد</option> below, which is never "visible" (a closed select's
  // option), so a plain .first() could hang the full 10s on that instead.
  const notifications = page.getByRole('region', { name: /Notifications/ });
  await expect(notifications.getByText(/ارسال (شد|ناموفق بود)/).first()).toBeVisible({ timeout: 10_000 });

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
  await page.getByLabel('متن پیام').nth(1).fill('پیام گروهی آزمایشی');

  // The rule, not one group's seeded size: whichever audience is chosen, the
  // button carries that group's own count and is disabled exactly when the
  // count is zero. Naming a group that happened to be empty made this test a
  // hostage of scripts/seed_local.py — giving the seeded staff a verified
  // phone (a fix on an unrelated branch) turned «کارکنان پنل» non-empty and
  // failed it on all three browsers, with nothing wrong in the panel.
  const sendButton = page.getByRole('button', { name: /ارسال به .* نفر/ });
  const groups = page.locator('input[name="sms-audience"]');
  const total = await groups.count();
  expect(total, 'no audience groups came back from /sms/audiences').toBeGreaterThan(0);

  let sawEmpty = false;
  for (let i = 0; i < total; i++) {
    const row = groups.nth(i).locator('xpath=ancestor::label[1]');
    await row.click();
    const count = (await row.innerText()).match(/([۰-۹]+) نفر/)?.[1];
    expect(count, `group ${i} shows no count`).toBeTruthy();
    await expect(sendButton).toContainText(`${count} نفر`);
    if (count === '۰') {
      sawEmpty = true;
      await expect(sendButton).toBeDisabled();
    } else {
      await expect(sendButton).toBeEnabled();
    }
  }
  // and at least one empty group has to exist for the guard to have been
  // exercised at all — otherwise this test silently stops testing it
  expect(sawEmpty, 'no audience was empty, so the zero-count guard went unchecked').toBeTruthy();
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

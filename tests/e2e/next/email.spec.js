// ایمیل — SMTP settings, template preview iframe, campaign live preview and
// history, against app/api/routes/email.py.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

test('owner sees SMTP settings and the sample template preview renders in its iframe', async ({ page }) => {
  const problems = watchProblems(page);
  await signIn(page, 'owner');
  await page.goto('/panel/email');
  await expect(page.getByRole('heading', { name: 'ایمیل', level: 1 })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'تنظیمات ایمیل (SMTP)' })).toBeVisible();

  const frame = page.frameLocator('iframe[title="پیش‌نمایش قالب ایمیل"]');
  await expect(frame.locator('body')).toContainText('سورین');

  await scrollThrough(page);
  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('the campaign card debounces a live preview and confirms before broadcast', async ({ page }) => {
  await signIn(page, 'owner');
  await page.goto('/panel/email');
  await expect(page.getByRole('heading', { name: 'کمپین ایمیلی' })).toBeVisible();
  // The local seed has exactly one staff address, so this group is sendable.
  await page.getByText('کارکنان پنل').click();
  await page.locator('#em-camp-subject').fill(`موضوع آزمایشی ${Date.now()}`);
  await page.locator('#em-camp-body').fill('متن آزمایشی کمپین');

  const preview = page.frameLocator('iframe[title="پیش‌نمایش کمپین"]');
  await expect(preview.locator('body')).toContainText('موضوع آزمایشی', { timeout: 3000 });

  const sendButton = page.getByRole('button', { name: /ارسال به .* نفر/ });
  await expect(sendButton).toBeEnabled();
  await sendButton.click();
  await expect(page.getByRole('dialog').getByText('ارسال کمپین ایمیلی')).toBeVisible();
  await page.getByRole('button', { name: 'انصراف' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('history filters by template', async ({ page }) => {
  await signIn(page, 'owner');
  await page.goto('/panel/email');
  await expect(page.getByRole('heading', { name: 'تاریخچهٔ ارسال' })).toBeVisible();
  await page.locator('#em-hist-tpl').selectOption('login_code');
  await page.waitForTimeout(400);
  await expect(page.getByText('ایمیلی یافت نشد.').or(page.getByRole('cell').first())).toBeVisible();
});

test('a manager without super_admin does not see SMTP settings or the campaign card', async ({ page }) => {
  await signIn(page, 'manager1');
  await page.goto('/panel/email');
  await expect(page.getByRole('heading', { name: 'ایمیل', level: 1 })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'تنظیمات ایمیل (SMTP)' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'کمپین ایمیلی' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'پیش‌نمایش قالب‌ها' })).toBeVisible();
});

test('the dark theme passes the same accessibility check', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await signIn(page, 'owner');
  await page.goto('/panel/email');
  await expect(page.locator('html')).toHaveClass(/dark/);
  await scrollThrough(page);
  expect(await a11y(page)).toEqual([]);
});

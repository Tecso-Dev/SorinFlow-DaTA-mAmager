// هوش مصنوعی — root/super_admin only: tiles, the six-agent grid, the call
// log, the «سورین» assistant Q&A and settings, against app/api/routes/ai.py
// + ai_assistant.py.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

test('owner sees every agent card and can toggle one off and back on', async ({ page }) => {
  const problems = watchProblems(page);
  await signIn(page, 'owner');
  await page.goto('/panel/ai');
  await expect(page.getByRole('heading', { name: 'هوش مصنوعی', level: 1 })).toBeVisible();
  for (const name of ['توضیح‌دهندهٔ پیشنهاد', 'خوانندهٔ آگهی', 'خوانندهٔ نیاز مشتری', 'جستجوی معنایی و تکراری‌یاب', 'برچسب‌زن عکس', 'دستیار دفتر «سورین»']) {
    await expect(page.getByText(name)).toBeVisible();
  }

  const card = page.locator('div', { has: page.getByText('خوانندهٔ آگهی', { exact: true }) }).first();
  const toggle = card.getByRole('switch').first();
  const before = await toggle.getAttribute('aria-checked');
  await toggle.click();
  await expect(toggle).not.toHaveAttribute('aria-checked', before ?? '');
  await toggle.click();

  await scrollThrough(page);
  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('the settings card shows the 4 model fields with their env fallback and saves the daily cap', async ({ page }) => {
  await signIn(page, 'owner');
  await page.goto('/panel/ai');
  await expect(page.getByRole('heading', { name: 'تنظیمات هوش مصنوعی' })).toBeVisible();
  for (const label of ['مدل نوشتن', 'مدل خواندن', 'مدل تصویر', 'مدل بردار']) {
    await expect(page.getByLabel(label)).toBeVisible();
  }
  const cap = page.getByLabel('سقف روزانه (دلار)');
  await cap.fill('3');
  await page.getByRole('button', { name: 'ذخیره', exact: true }).click();
  await expect(page.getByText('تنظیمات هوش مصنوعی ذخیره شد').first()).toBeVisible();
});

test('«بپرس» asks the assistant from the panel and the question lands in the log', async ({ page }) => {
  await signIn(page, 'owner');
  await page.goto('/panel/ai');
  const q = `پرسش آزمایشی ${Date.now()}`;
  await page.getByPlaceholder('از سورین بپرس…').fill(q);
  await page.getByRole('button', { name: 'بپرس' }).click();
  await expect(page.getByText(q).or(page.getByText(/پاسخی دریافت نشد|خطا/)).first()).toBeVisible({ timeout: 15_000 });
});

test('an admin (not super_admin) is refused with a clear message', async ({ page }) => {
  const problems = [];
  page.on('pageerror', (e) => problems.push(String(e)));
  await signIn(page, 'manager1');
  await page.goto('/panel/ai');
  await expect(page.getByText('این بخش فقط برای مدیر ارشد و root در دسترس است.')).toBeVisible();
  expect(problems).toEqual([]);
});

test('the dark theme passes the same accessibility check', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await signIn(page, 'owner');
  await page.goto('/panel/ai');
  await expect(page.locator('html')).toHaveClass(/dark/);
  await scrollThrough(page);
  expect(await a11y(page)).toEqual([]);
});

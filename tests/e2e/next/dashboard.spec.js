// The dashboard on real data: the office sees the office, an agent sees
// themselves, the monthly target is set by a super admin, and every panel
// renders without a CSP violation or sideways scroll.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

test('owner sees the whole office', async ({ page }) => {
  const problems = watchProblems(page);
  await signIn(page, 'owner');
  await page.goto('/panel');
  for (const title of ['آگهی‌های تازهٔ امروز', 'لیدهای باز', 'تماس‌های امروز', 'نرخ تبدیل لید']) {
    await expect(page.getByText(title, { exact: true })).toBeVisible();
  }
  await scrollThrough(page);
  for (const heading of ['روند آگهی و لید', 'بازار محله‌ها', 'قیف فروش', 'ساعت‌های تماس', 'عملکرد تیم',
    'قراردادها به تفکیک نوع', 'وضعیت سامانه', 'صف تماس امروز', 'قرارهای پیشِ رو']) {
    await expect(page.getByRole('heading', { name: heading })).toBeVisible();
  }
  const team = page.getByRole('table');
  await expect(team.getByText('علی رضایی')).toBeVisible();
  await expect(team.getByText('سارا محمدی')).toBeVisible();
  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('an agent sees only their own calls and row', async ({ page }) => {
  await signIn(page, 'agent1');
  await page.goto('/panel');
  await scrollThrough(page);
  await expect(page.getByRole('heading', { name: 'عملکرد شما' })).toBeVisible();
  const rows = page.getByRole('table').getByRole('row');
  await expect(rows).toHaveCount(2);   // header + agent1
  await expect(page.getByRole('button', { name: /تعیین هدف/ })).toHaveCount(0);
});

test('the trend switches between 7, 30 and 90 days', async ({ page }) => {
  await signIn(page, 'manager1');
  await page.goto('/panel');
  const hint = page.getByText(/آگهی و .* قرارداد در .* روز گذشته/);
  await expect(hint).toContainText('۳۰ روز');
  await page.getByRole('button', { name: '۷ روز' }).click();
  await expect(hint).toContainText('۷ روز');
});

test('a super admin sets the monthly target', async ({ page }) => {
  await signIn(page, 'owner');
  await page.goto('/panel');
  await page.getByRole('button', { name: /تعیین هدف/ }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('تعداد قرارداد').fill('15');
  await dialog.getByLabel(/کمیسیون/).fill('2500');
  await dialog.getByRole('button', { name: 'ذخیره' }).click();
  await expect(page.getByText('هدف ماه ذخیره شد').first()).toBeVisible();
  await expect(page.getByText(/از ۱۵/)).toBeVisible();
});

test('the dark theme passes the same accessibility check', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await signIn(page, 'owner');
  await page.goto('/panel');
  await expect(page.locator('html')).toHaveClass(/dark/);
  await scrollThrough(page);
  expect(await a11y(page)).toEqual([]);
});

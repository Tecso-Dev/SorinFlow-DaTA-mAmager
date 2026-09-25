// /panel/profile: hero (avatar/presence/IP), details form, Telegram link
// card, contact card (email/phone change flow UI, read-only Divar list),
// security card (password change, TOTP setup dialog with the QR).
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

test('profile loads with the signed-in user\'s data', async ({ page }) => {
  const problems = watchProblems(page);
  await signIn(page, 'agent1');
  await page.goto('/panel/profile');
  await expect(page.getByRole('heading', { name: 'پروفایل من' })).toBeVisible();
  await expect(page.getByText('@agent1')).toBeVisible();
  await expect(page.getByText('IP شما از نگاه سرور')).toBeVisible();
  await scrollThrough(page);
  await page.waitForTimeout(700); // let every Reveal section finish fading in before axe reads it
  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('editing headline and saving updates the hero card', async ({ page }) => {
  const stamp = `e2e-r3a-${Date.now()}`;
  await signIn(page, 'agent2');
  await page.goto('/panel/profile');
  await page.locator('#pf-headline').fill(stamp);
  await page.getByRole('button', { name: 'ذخیرهٔ مشخصات' }).click();
  await expect(page.getByText('ذخیره شد').first()).toBeVisible();
  await expect(page.locator('#pf-headline')).toHaveValue(stamp);
  // survives a reload — proves it round-tripped through PATCH /users/me
  await page.reload();
  await expect(page.locator('#pf-headline')).toHaveValue(stamp);
  // clean up: agent2 is a shared seeded account other streams' screenshots use
  await page.locator('#pf-headline').fill('');
  await page.getByRole('button', { name: 'ذخیرهٔ مشخصات' }).click();
  await expect(page.getByText('ذخیره شد').first()).toBeVisible();
});

test('presence switches without a page reload', async ({ page }) => {
  await signIn(page, 'agent1');
  await page.goto('/panel/profile');
  const select = page.getByLabel('وضعیت حضور');
  await select.selectOption('busy');
  await expect(select).toHaveValue('busy');
  await page.waitForTimeout(400);
  await page.reload();
  await expect(page.getByLabel('وضعیت حضور')).toHaveValue('busy');
  await page.getByLabel('وضعیت حضور').selectOption('available');
});

test('TOTP setup shows a QR code and a manual secret', async ({ page }) => {
  await signIn(page, 'agent2');
  await page.goto('/panel/profile');
  await page.getByRole('button', { name: 'راه‌اندازی' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByAltText(/QR|کد QR/)).toBeVisible();
  await expect(dialog.locator('#totp-secret')).not.toHaveValue('');
  await page.keyboard.press('Escape');
});

test('the Telegram card offers a link code', async ({ page }) => {
  await signIn(page, 'agent1');
  await page.goto('/panel/profile');
  await expect(page.getByText('اتصال به تلگرام')).toBeVisible();
  await page.getByRole('button', { name: 'دریافت کد اتصال' }).click();
  await expect(page.getByLabel('کد اتصال')).not.toHaveValue('');
  await expect(page.getByText(/^\/start /)).toBeVisible();
});

test('a staff account without divar_auth sees the permission note, not a failed call', async ({ page }) => {
  const fails = [];
  page.on('response', (r) => { if (r.url().includes('/auth/cookies') && r.status() >= 400) fails.push(r.status()); });
  await signIn(page, 'agent2');
  await page.goto('/panel/profile');
  await expect(page.getByText('شماره‌های دیوار من')).toBeVisible();
  await expect(page.getByText(/دسترسی «حساب‌های دیوار»/)).toBeVisible();
  expect(fails).toEqual([]);
});

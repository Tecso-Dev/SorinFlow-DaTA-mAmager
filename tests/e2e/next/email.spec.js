// ایمیل — SMTP settings, template preview iframe, campaign live preview and
// history, against app/api/routes/email.py.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

// Known, environment-only noise on this page alone, never a real failure.
// Chromium and WebKit word the same three things differently, so these are
// patterns, not exact strings.
//  - the hero image / connection failure: _site_url() in
//    app/services/email_templates.py always builds the hero image's <img>
//    src as an absolute https://<domain>/... URL, correct and required for
//    a real, sent email (a real domain always has a certificate there); the
//    local/e2e DOMAIN is "localhost" with no HTTPS listener on this box, so
//    the preview iframe's own image fails to load here. Production is
//    unaffected — sorinflow.com does serve https. Chromium names the image
//    and "ERR_CONNECTION_REFUSED"; WebKit just says it could not connect.
//  - about:srcdoc script blocked: the campaign card's own preview iframe
//    (srcDoc, sandboxed, no allow-scripts) correctly refusing to run
//    anything — the intended behavior, not a bug.
//  - style-src: axe-core's legacy mode (see the a11y() calls below) injects
//    its own <style> element for internal use, which the app's strict CSP
//    blocks for want of a nonce exactly like it would one of ours. Nothing
//    of ours creates a <style> element (style *attributes* are unrestricted,
//    see docs/FRONTEND.md), so any style-src hit here is axe's.
const KNOWN_NOISE = [
  /email-assets\/hero-/,
  /Failed to load resource:.*(ERR_CONNECTION_REFUSED|[Cc]ould not connect)/,
  /Blocked script execution in 'about:srcdoc'/,
  /style-src/,
];
function realProblems(problems) {
  return problems.filter((p) => !KNOWN_NOISE.some((n) => n.test(p)));
}

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
  // sandbox="allow-same-origin" with no allow-scripts (deliberate: this can
  // carry admin-authored template HTML) — axe cannot run inside it either.
  expect(await a11y(page, { exclude: ['iframe[title="پیش‌نمایش قالب ایمیل"]'], legacy: true })).toEqual([]);
  expect(realProblems(problems)).toEqual([]);
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
  expect(await a11y(page, { exclude: ['iframe[title="پیش‌نمایش قالب ایمیل"]'], legacy: true })).toEqual([]);
});

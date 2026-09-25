// «فرستندهٔ پیامک»: the phones that forward Divar's SMS codes. Covers the
// device list, the one-proper-form add flow (replacing the old panel's three
// window.prompt() calls) and its one-time secret, the six-step install
// guide with the QR shield's 30-second auto-hide, rotate, editing a SIM
// (with its re-scan warning), delete, the codes log's filter chips, and the
// `forwarder`/`sms` permission split. Every device this suite creates is
// deleted again, and SIM numbers are randomised so parallel runs never
// collide over the "one SIM, one active device" rule the backend enforces.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, a11y } = require('./helpers');

const tag = () => `e2e-fw-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
const randSim = (prefix) => prefix + String(Math.floor(1e6 + Math.random() * 8e6));
// The device row renders twice in the DOM — a desktop table row and a phone
// card, one hidden by CSS depending on viewport — so its own actions button
// needs the same visible-only filter every CRM spec uses for that shape.
const visible = (loc) => loc.filter({ visible: true }).first();

async function api(page, session, method, path, data) {
  const res = await page.request.fetch(`/api${path}`, {
    method, data, headers: { 'X-CSRF-Token': session.csrf_token },
  });
  expect(res.ok(), `${method} ${path}: ${await res.text()}`).toBeTruthy();
  return res.status() === 204 ? null : res.json();
}

test('devices load, add shows the one-time secret, the six-step guide, QR reveal/auto-hide, rotate, edit SIM, delete, codes log filters', async ({ page }) => {
  const problems = watchProblems(page);
  await signIn(page, 'owner');
  // Installed before navigating (Playwright's own guidance): the virtual
  // clock still ticks with real time until fastForward jumps it, so page
  // load and every animation run exactly as they would without it.
  await page.clock.install();
  await page.goto('/panel/forwarder');
  await expect(page.getByRole('heading', { name: 'فرستندهٔ پیامک', level: 1 })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'گوشی‌های من' })).toBeVisible();

  const label = tag();
  const sim1 = randSim('0912');
  const sim2 = randSim('0919');

  /* ───────────── add device: one form, then the one-time secret ───────────── */
  await page.getByRole('button', { name: 'افزودن گوشی' }).first().click();
  const addDlg = page.getByRole('dialog');
  await addDlg.getByLabel('نام گوشی').fill(label);
  await addDlg.getByLabel('سیم اول *').fill(sim1);
  await addDlg.getByLabel('سیم دوم').fill(sim2);
  await addDlg.getByRole('button', { name: 'ثبت گوشی' }).click();
  await expect(page.getByText('گوشی ثبت شد').first()).toBeVisible();

  const secretDlg = page.getByRole('dialog', { name: 'رمز این دستگاه' });
  await expect(secretDlg).toBeVisible();
  const secret = (await secretDlg.locator('div[dir="ltr"].font-mono').textContent() || '').trim();
  expect(secret).toMatch(/^[0-9a-f]{64}$/);

  /* ───────────── install guide: all six steps ───────────── */
  await secretDlg.getByRole('button', { name: 'باز کردن راهنمای نصب' }).click();
  const guide = page.getByRole('dialog', { name: new RegExp(`راهنمای نصب.*${label}`) });
  await expect(guide).toBeVisible();
  for (const step of [
    'دانلود برنامه', 'مجوز خواندن پیامک', 'غیرفعال‌کردن محدودیت باتری و Autostart',
    'کد QR و فیلدهای دستی', 'آزمایش اتصال', 'قانون‌های تشخیص کد',
  ]) {
    await expect(guide.getByRole('heading', { name: step })).toBeVisible();
  }
  // dual-SIM device: the SIM-2 rules are there too
  await expect(guide.getByText('گوشی دو سیم‌کارته')).toBeVisible();
  await expect(guide.getByText('سیم ۲').first()).toBeVisible();

  /* ───────────── QR shield: hidden by default, reveals, auto-hides at 30s ───────────── */
  const shield = guide.getByRole('button', { name: /نمایش کد QR/ });
  await expect(shield).toBeVisible();
  await expect(guide.getByAltText(/کد QR/)).toHaveCount(0);
  await shield.click();
  const qr = guide.getByAltText(/کد QR/);
  await expect(qr).toBeVisible();
  await expect(guide.getByText(/ثانیه تا پنهان‌شدن خودکار/)).toBeVisible();
  await page.clock.runFor(31_000); // > FW_QR_REVEAL_SECONDS (30s)
  await expect(qr).toHaveCount(0);
  await expect(shield).toBeVisible();

  // check connection: labelled as "last heard from the phone", not a live ping
  await expect(guide.getByText(/این یک ping زنده به گوشی نیست/)).toBeVisible();
  await guide.getByRole('button', { name: 'بررسی اتصال' }).click();
  await expect(page.getByText(/گوشی وصل است|هنوز خبری از گوشی نیست/).first()).toBeVisible();
  // the × button, not Escape: Radix's dismissable layer wires up its Escape
  // listener on a deferred timer, and the fake clock this block installed
  // stops auto-advancing once it has been fast-forwarded once, so a global
  // key listener registered after that point never arrives — a test-only
  // quirk of the clock, not a product bug, and the × is a direct click handler
  await guide.getByRole('button', { name: 'Close' }).click();
  await expect(guide).toHaveCount(0);

  /* ───────────── rotate: confirm first, guide reopens with the fresh secret ───────────── */
  const rowMenu = () => visible(page.getByRole('button', { name: new RegExp(`کارهای گوشی ${label}`) }));
  await rowMenu().click();
  await page.getByRole('menuitem', { name: 'کلید تازه' }).click();
  await expect(page.getByRole('dialog')).toContainText('گوشی تا وارد کردن کلید تازه');
  await page.getByRole('dialog').getByRole('button', { name: 'بله، کلید تازه' }).click();
  await expect(page.getByText('کلید تازه ساخته شد').first()).toBeVisible();
  const reopenedGuide = page.getByRole('dialog', { name: new RegExp(`راهنمای نصب.*${label}`) });
  await expect(reopenedGuide).toBeVisible();
  await reopenedGuide.getByRole('button', { name: 'Close' }).click();
  await expect(reopenedGuide).toHaveCount(0);

  /* ───────────── edit SIM: the re-scan warning lives in the dialog, not a toast ───────────── */
  await rowMenu().click();
  await page.getByRole('menuitem', { name: 'ویرایش سیم‌کارت' }).click();
  const editDlg = page.getByRole('dialog', { name: 'ویرایش سیم‌کارت' });
  await expect(editDlg.getByText(/کد QR و راهنمای نصب باید دوباره/)).toBeVisible();
  await editDlg.getByLabel('سیم اول').fill(randSim('0912'));
  await editDlg.getByRole('button', { name: 'ذخیره' }).click();
  await expect(page.getByText('شمارهٔ سیم به‌روز شد').first()).toBeVisible();

  /* ───────────── codes log filter chips (owner carries the `sms` permission too) ───────────── */
  await expect(page.getByRole('heading', { name: 'کدهای رسیده از گوشی' })).toBeVisible();
  const filters = page.getByRole('group', { name: 'فیلتر کدهای رسیده' });
  for (const name of ['همه', 'به اسکرپر داده شد', 'زودتر رسید — نگه داشته شد', 'مشکل‌دار']) {
    await filters.getByRole('button', { name }).click();
    await expect(filters.getByRole('button', { name, pressed: true })).toBeVisible();
  }

  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);

  /* ───────────── cleanup: delete the device this run created ───────────── */
  await rowMenu().click();
  await page.getByRole('menuitem', { name: 'حذف دستگاه' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'حذف' }).click();
  await expect(page.getByText('دستگاه حذف شد').first()).toBeVisible();
  await expect(page.getByRole('button', { name: new RegExp(`کارهای گوشی ${label}`) })).toHaveCount(0);
});

test('a user with «forwarder» but not «sms» never sees the codes log', async ({ page, browser }) => {
  const owner = await signIn(page, 'owner');
  const agentCtx = await browser.newContext();
  const agentPage = await agentCtx.newPage();
  const agentSession = await signIn(agentPage, 'agent1');
  const original = agentSession.user.permissions;
  try {
    // agent1 (DEFAULT_ADMIN_PERMISSIONS) has neither by default — give it
    // exactly `forwarder`, so the page itself is reachable but `sms` is not.
    await api(page, owner, 'PATCH', `/users/${agentSession.user.id}`, { permissions: ['forwarder'] });
    await agentPage.goto('/panel/forwarder');
    await expect(agentPage.getByRole('heading', { name: 'فرستندهٔ پیامک', level: 1 })).toBeVisible();
    await expect(agentPage.getByRole('heading', { name: 'گوشی‌های من' })).toBeVisible();
    // the working card (filters, the log itself) never renders — only the
    // plain explanation of why does
    await expect(agentPage.getByText('این بخش نیاز به دسترسی «پیامک» دارد که حساب شما ندارد.')).toBeVisible();
    await expect(agentPage.getByRole('group', { name: 'فیلتر کدهای رسیده' })).toHaveCount(0);
    await expect(agentPage.getByText('آخرین ۱۰۰ پیامک رسیده از گوشی‌ها')).toHaveCount(0);
  } finally {
    await api(page, owner, 'PATCH', `/users/${agentSession.user.id}`, { permissions: original });
    await agentCtx.close();
  }
});

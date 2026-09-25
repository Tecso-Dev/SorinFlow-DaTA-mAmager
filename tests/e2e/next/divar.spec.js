// «احراز هویت دیوار»: the login-a-number flow's phone gate, the saved
// sessions list, cookie-import validation, and the root-only numbers
// registry. The sandbox cannot reach real divar.ir, so these cover what the
// UI does with the backend's own validation and ownership-filtering
// responses rather than a real Divar round trip.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

test.describe('divar auth page', () => {
  test('loads clean for an owner with no saved number yet', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    await page.goto('/panel/divar');
    await expect(page.getByRole('heading', { name: 'احراز هویت دیوار', level: 1 })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'ورود به حساب دیوار' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'افزودن نشست دستی' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'نشست‌های ذخیره‌شده' })).toBeVisible();
    await expect(page.getByText('هنوز شمارهٔ دیواری وارد این حساب نشده است.')).toBeVisible();
    // not root: the registry card must not even be in the DOM
    await expect(page.getByRole('heading', { name: 'همهٔ شماره‌های دیوار و صاحبشان' })).toHaveCount(0);
    await scrollThrough(page);
    await noHorizontalScroll(page);
    expect(await a11y(page)).toEqual([]);
    expect(problems).toEqual([]);
  });

  test('a saved session shows its badges and row controls', async ({ page }) => {
    // manager1 owns a seeded, valid Divar session (scripts/seed_local.py)
    await signIn(page, 'manager1');
    await page.goto('/panel/divar');
    const row = page.locator('li').filter({ hasText: '09120000001' });
    await expect(row.getByText('معتبر')).toBeVisible();
    await expect(row.getByLabel(/روشن یا خاموش کردن شمارهٔ/)).toBeVisible();
    await expect(row.getByLabel(/بازنشانی نشست/)).toBeVisible();
    await expect(row.getByLabel(/خروج از/)).toBeVisible();
    await expect(row.getByLabel(/حذف نشست/)).toBeVisible();
  });

  test('sending a login code is a real round trip: either the code step opens or the server\'s own refusal is shown', async ({ page }) => {
    // POST /auth/login needs the caller's own verified phone first (api()
    // would open the shell's phone-gate dialog for a 403 phone_unverified);
    // with no SMS provider configured locally the gate fails open (by
    // design — phone_gate_reason), so this reaches the real endpoint, which
    // then tries to reach Divar and fails in this sandbox. Either way the
    // dialog must react, not sit frozen on a disabled button.
    await signIn(page, 'owner');
    await page.goto('/panel/divar');
    await page.getByRole('button', { name: 'ورود با شمارهٔ تازه' }).click();
    const dlg = page.getByRole('dialog', { name: 'شمارهٔ دیوار' });
    await dlg.getByLabel('شمارهٔ موبایل دیوار').fill('09123456789');
    await dlg.getByRole('button', { name: 'ارسال کد تأیید' }).click();
    await expect(dlg.getByLabel('کد تأیید دیوار').or(dlg.locator('p.text-destructive'))).toBeVisible({ timeout: 20_000 });
  });

  test('an invalid phone number is refused before any request is sent', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/divar');
    await page.getByRole('button', { name: 'ورود با شمارهٔ تازه' }).click();
    const dlg = page.getByRole('dialog', { name: 'شمارهٔ دیوار' });
    await dlg.getByLabel('شمارهٔ موبایل دیوار').fill('12345');
    await dlg.getByRole('button', { name: 'ارسال کد تأیید' }).click();
    await expect(dlg.getByText('شمارهٔ موبایل معتبر وارد کنید')).toBeVisible();
  });

  test('cookie import: bad JSON and an empty jar are both refused, with the server\'s own reason shown', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/divar');

    await page.getByLabel('شمارهٔ موبایل دیوار').fill('09123456789');
    await page.getByLabel('کوکی‌ها (JSON)').fill('{not json');
    await page.getByRole('button', { name: 'وارد کردن نشست' }).click();
    await expect(page.getByText(/فرمت JSON نادرست است/)).toBeVisible();

    // valid JSON, but an empty array: the server's own 400, reached without
    // any Divar network call (checked before the session is probed)
    await page.getByLabel('کوکی‌ها (JSON)').fill('[]');
    await page.getByRole('button', { name: 'وارد کردن نشست' }).click();
    await expect(page.getByText('هیچ کوکی‌ای ارسال نشد')).toBeVisible();

    // a jar with no session cookie: also refused before any network call
    await page.getByLabel('کوکی‌ها (JSON)').fill('[{"name":"unrelated","value":"x","domain":".divar.ir"}]');
    await page.getByRole('button', { name: 'وارد کردن نشست' }).click();
    await expect(page.getByText(/در آنچه وارد کردید وجود ندارد/)).toBeVisible();
  });

  test('the numbers registry is root-only: present for root, absent for owner and agent1', async ({ page }) => {
    await signIn(page, 'root');
    await page.goto('/panel/divar');
    await expect(page.getByRole('heading', { name: 'همهٔ شماره‌های دیوار و صاحبشان' })).toBeVisible();
    await expect(page.getByText('09120000001')).toBeVisible();
    await expect(page.getByText('09120000002')).toBeVisible();
    await scrollThrough(page);
    await noHorizontalScroll(page);
    expect(await a11y(page)).toEqual([]);
  });

  test('root: changing a number\'s owner asks for confirmation naming both sides, cancel changes nothing', async ({ page }) => {
    await signIn(page, 'root');
    await page.goto('/panel/divar');
    const row = page.getByRole('row', { name: /09120000002/ });
    const select = row.getByRole('combobox');
    const before = await select.inputValue();
    // pick the first option that is not the current owner and not «بی‌صاحب»
    const options = await select.locator('option').all();
    let target = null;
    for (const o of options) {
      const v = await o.getAttribute('value');
      if (v && v !== before) { target = v; break; }
    }
    test.skip(!target, 'no alternate owner available to test with');
    await select.selectOption(target);
    await row.getByRole('button', { name: 'ذخیره' }).click();
    const dlg = page.getByRole('dialog', { name: 'تغییر مالکیت شماره' });
    await expect(dlg).toContainText('از این پس فقط در اختیار');
    await expect(dlg).toContainText('به شمارهٔ دیگری از خودش منتقل می‌شود');
    await dlg.getByRole('button', { name: 'انصراف' }).click();
    await expect(dlg).toHaveCount(0);
    // nothing was sent: the select still shows the original owner after a reload
    await page.reload();
    await expect(page.getByRole('row', { name: /09120000002/ }).getByRole('combobox')).toHaveValue(before);
  });

  test('owner has no registry card', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/divar');
    await expect(page.getByRole('heading', { name: 'همهٔ شماره‌های دیوار و صاحبشان' })).toHaveCount(0);
  });

  test('agent1 has no registry card', async ({ page }) => {
    await signIn(page, 'agent1');
    await page.goto('/panel/divar');
    await expect(page.getByRole('heading', { name: 'همهٔ شماره‌های دیوار و صاحبشان' })).toHaveCount(0);
  });

  test('no horizontal scroll or a11y violations on a phone, dark theme', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await signIn(page, 'manager1');
    await page.goto('/panel/divar');
    await scrollThrough(page);
    await noHorizontalScroll(page);
    expect(await a11y(page)).toEqual([]);
  });
});

// «اسکرپر»: the new-scrape form (link parse, filters, estimate), the
// account picker, saved schedules (create/edit/delete — never "run now",
// which would actually launch a browser against Divar), and the jobs table
// with its log/skipped dialogs. Nothing here reaches divar.ir: starting a
// real run needs a logged-in Divar number this sandbox does not have, so
// the "start" test only goes as far as the no-session warning dialog.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

const tag = () => `e2e-scraper-${Date.now()}-${Math.floor(Math.random() * 1000)}`;

async function api(page, session, method, path, data) {
  const res = await page.request.fetch(`/api${path}`, { method, data, headers: { 'X-CSRF-Token': session.csrf_token } });
  expect(res.ok(), `${method} ${path}: ${await res.text()}`).toBeTruthy();
  return res.status() === 204 ? null : res.json();
}

test.describe('scraper', () => {
  test('the section loads with the jobs table, no horizontal scroll, clean a11y', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    await page.goto('/panel/scraper');
    await expect(page.getByRole('heading', { name: 'اسکرپر', level: 1 })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'اسکرپینگ جدید' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'اسکرپ تکی' })).toBeVisible();
    await expect(page.getByText('تسک‌های اسکرپینگ')).toBeVisible();
    // the sample jobs seeded for this account
    await expect(page.locator('table tbody tr').first()).toBeVisible();
    await scrollThrough(page);
    await noHorizontalScroll(page);
    expect(await a11y(page)).toEqual([]);
    expect(problems).toEqual([]);
  });

  test('an agent without the scraper permission has no link to this section', async ({ page }) => {
    await signIn(page, 'agent1');
    await page.goto('/panel');
    await expect(page.getByRole('link', { name: 'اسکرپر' })).toHaveCount(0);
  });

  test('city + category drive the estimate, and «فیلترهای بیشتر» opens itself once a filter is set', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/scraper');
    const more = page.getByRole('button', { name: /فیلترهای بیشتر/ });
    // closed with nothing set yet
    await expect(page.getByText('متراژ (متر)')).toBeHidden();

    await page.getByLabel('شهر', { exact: true }).selectOption('tehran');
    await page.getByLabel('دسته‌بندی', { exact: true }).selectOption('buy-apartment');
    // the estimate box reacts to city+category (debounced ~900ms)
    await expect(page.getByText('چند آگهی با این فیلترها هست؟').locator('..')).not.toContainText('اول شهر را انتخاب کنید', { timeout: 5000 });

    // opening it by hand shows the buy-only price block, not the rent one
    await more.click();
    await expect(page.getByText('قیمت کل (تومان)')).toBeVisible();
    await expect(page.getByText('ودیعه (تومان)')).toHaveCount(0);

    // setting a filter opens the panel on its own and counts it in the summary
    await page.getByLabel('قیمت از').fill('500000000');
    await expect(more).toContainText('۱ فعال');
  });

  test('starting a scrape with no logged-in Divar number stops at the warning, never reaches Divar', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/scraper');
    await page.getByLabel('شهر', { exact: true }).selectOption('tehran');
    await page.getByLabel('دسته‌بندی', { exact: true }).selectOption('rent-apartment');
    await page.getByRole('button', { name: 'شروع اسکرپینگ' }).click();
    const warn = page.getByRole('dialog', { name: 'هشدار: بدون ورود به دیوار' });
    await expect(warn).toBeVisible();
    await expect(warn).toContainText('شما هنوز وارد حساب دیوار نشده‌اید');
    await expect(warn.getByRole('link', { name: 'ورود به حساب' })).toHaveAttribute('href', '/panel/divar');
    // closing without continuing — no job, no request to Divar
    await page.keyboard.press('Escape');
    await expect(warn).toBeHidden();
  });

  test('single-scrape rejects a non-Divar URL client-side, without calling the API', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/scraper');
    let called = false;
    await page.route('**/api/scraper/scrape-single', (route) => {
      called = true;
      route.abort();
    });
    await page.getByLabel('آدرس ملک در دیوار').fill('https://example.com/not-divar');
    await page.getByRole('button', { name: 'اسکرپ این ملک' }).click();
    await expect(page.getByText('آدرس درست نیست').first()).toBeVisible();
    expect(called).toBe(false);
  });

  test('a schedule is created, shown, its hour edited, then deleted', async ({ page }) => {
    const s = await signIn(page, 'owner');
    const name = tag();
    await page.goto('/panel/scraper');
    await page.getByLabel('شهر', { exact: true }).selectOption('tehran');
    await page.getByLabel('دسته‌بندی', { exact: true }).selectOption('buy-apartment');
    await page.getByRole('button', { name: 'هر روز خودکار اجرا شود' }).click();
    const dlg = page.getByRole('dialog', { name: 'اجرای روزانه' });
    await expect(dlg).toBeVisible();
    await dlg.getByLabel('اسم زمان‌بندی').fill(name);
    await dlg.getByLabel('ساعت اجرا (به وقت تهران)').fill('09:15');
    await dlg.getByRole('button', { name: 'ذخیره' }).click();
    await expect(page.getByText('ذخیره شد').first()).toBeVisible();

    const card = page.locator('section').filter({ hasText: 'اسکرپ‌های زمان‌بندی‌شده' });
    await expect(card.getByText(name)).toBeVisible();
    await expect(card).toContainText('09:15');

    try {
      // edit the hour
      const row = card.getByText(name).locator('../..');
      await row.getByRole('button', { name: 'تغییر ساعت' }).click();
      const editDlg = page.getByRole('dialog', { name: 'ساعت اجرا' });
      await editDlg.getByLabel('ساعت').fill('10:30');
      await editDlg.getByRole('button', { name: 'ذخیره' }).click();
      await expect(page.getByText('ساعت تغییر کرد').first()).toBeVisible();
      await expect(card).toContainText('10:30');

      // toggle off and on
      await row.getByRole('switch', { name: name }).click();
      await expect(page.getByText('خاموش شد').first()).toBeVisible();
    } finally {
      const rows = await api(page, s, 'GET', '/scraper/schedules');
      const mine = rows.schedules.find((r) => r.name === name);
      if (mine) await api(page, s, 'DELETE', `/scraper/schedules/${mine.id}`);
    }
  });

  test('a completed job only offers report / skipped / delete — no cancel or switch-account', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/scraper');
    await page.getByRole('button', { name: 'کارهای بیشتر' }).first().click();
    const menu = page.getByRole('menu');
    await expect(menu.getByRole('menuitem', { name: 'گزارش این اسکرپ' })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: 'آگهی‌های ردشده' })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: 'حذف' })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: 'لغو' })).toHaveCount(0);
    await expect(menu.getByRole('menuitem', { name: 'تعویض شماره' })).toHaveCount(0);
  });

  test('the job log dialog opens with search presets, and the skipped-ads dialog opens with a reason filter', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/scraper');
    await page.getByRole('button', { name: 'کارهای بیشتر' }).first().click();
    await page.getByRole('menuitem', { name: 'گزارش این اسکرپ' }).click();
    const logDlg = page.getByRole('dialog', { name: 'گزارش اسکرپ' });
    await expect(logDlg).toBeVisible();
    await expect(logDlg.getByRole('button', { name: 'Skipping' })).toBeVisible();
    await expect(logDlg.getByRole('button', { name: 'ERROR' })).toBeVisible();
    await page.keyboard.press('Escape');

    await page.getByRole('button', { name: 'کارهای بیشتر' }).first().click();
    await page.getByRole('menuitem', { name: 'آگهی‌های ردشده' }).click();
    const skipDlg = page.getByRole('dialog', { name: 'آگهی‌های ردشده' });
    await expect(skipDlg).toBeVisible();
    await expect(skipDlg.getByLabel('فیلتر دلیل')).toBeVisible();
  });

  test('the scraper-log button is available to a full-access user (gated on the `stats` permission)', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/scraper');
    await page.getByRole('button', { name: 'لاگ اسکرپر' }).click();
    const dlg = page.getByRole('dialog', { name: 'لاگ اسکرپر' });
    await expect(dlg).toBeVisible();
    await expect(dlg.getByRole('button', { name: 'rotate' })).toBeVisible();
  });

  test('filtering the jobs table by category narrows the rows', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/scraper');
    // wait past the loading skeleton before counting anything
    await expect(page.locator('table tbody tr').first()).toBeVisible();
    const before = await page.locator('table tbody tr').count();
    expect(before).toBeGreaterThan(0);
    await page.getByLabel('فیلتر دسته‌بندی').selectOption({ label: 'اجاره کوتاه مدت' });
    await expect(page.getByText('هیچ تسکی وجود ندارد.')).toBeVisible();
    await page.getByLabel('فیلتر دسته‌بندی').selectOption({ label: 'همهٔ دسته‌بندی‌ها' });
    await expect(page.locator('table tbody tr').first()).toBeVisible();
    await expect(page.locator('table tbody tr')).toHaveCount(before);
  });

  test('dark theme passes axe too', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await signIn(page, 'owner');
    await page.goto('/panel/scraper');
    await expect(page.locator('html')).toHaveClass(/dark/);
    await expect(page.getByRole('heading', { name: 'اسکرپر', level: 1 })).toBeVisible();
    expect(await a11y(page)).toEqual([]);
  });
});

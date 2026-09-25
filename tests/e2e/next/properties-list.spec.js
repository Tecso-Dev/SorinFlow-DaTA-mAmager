// «لیست املاک» — the properties table, its filters and exports, the detail
// sheet (photos, inline جهت/نبش selects, AI blocks), the «ملک‌های مشابه»
// modal and delete. A throwaway property is made through POST /crm/leads
// (the only route that creates a Property row outside the scraper) and
// cleaned up again, so the suite runs next to the seeded data and the other
// streams' own throwaway rows.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

/** See the matching comment in insights-a.spec.js: a short bounded retry for
 *  axe's occasional transitional colour-contrast misread on this machine's
 *  `next dev`/Turbopack, never masking a real or repeatable finding. */
async function a11yStable(page, tries = 4) {
  let last = [];
  for (let i = 0; i < tries; i++) {
    last = await a11y(page);
    if (last.length === 0) return last;
    if (!last.every((v) => v.startsWith('color-contrast:'))) return last;
    await page.waitForTimeout(250);
  }
  return last;
}

const tag = () => `e2e-props-${Date.now()}-${Math.floor(Math.random() * 1000)}`;

async function api(page, session, method, path, data) {
  const res = await page.request.fetch(`/api${path}`, { method, data, headers: { 'X-CSRF-Token': session.csrf_token } });
  expect(res.ok(), `${method} ${path}: ${await res.text()}`).toBeTruthy();
  return res.status() === 204 ? null : res.json();
}

/** A throwaway lead (and the Property row it creates) to view/delete safely. */
async function makeListing(page, session, title, extra = {}) {
  const lead = await api(page, session, 'POST', '/crm/leads', {
    property_title: title, phone_number: '0912' + String(Math.floor(1e6 + Math.random() * 8e6)),
    city_name: 'تهران', listing_type: 'buy', price: 3_200_000_000, area: 88, ...extra,
  });
  return lead;
}

async function dropLead(page, session, id) {
  await page.request.fetch(`/api/crm/leads/${id}`, { method: 'DELETE', headers: { 'X-CSRF-Token': session.csrf_token } }).catch(() => null);
}

test.describe('properties list', () => {
  test('loads with data, city/category filters, no horizontal scroll, a11y clean', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    // settle Reveal's fade-in before the contrast check reads real colours
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/panel/properties');
    await expect(page.getByRole('heading', { name: 'لیست املاک', level: 1 })).toBeVisible();
    await expect(page.getByRole('table')).toBeVisible();
    const rowCount = await page.getByRole('row').count();
    expect(rowCount).toBeGreaterThan(1); // header + at least one property
    expect(rowCount).toBeLessThanOrEqual(21); // page size is 20

    // the searchable city picker (not a plain <select>)
    await page.getByRole('combobox').first().click();
    await page.getByRole('button', { name: /^تهران/ }).first().click();

    // a rent category shows the deposit/rent band, a buy one hides it again
    const category = page.locator('select').first();
    const rentLabel = (await category.locator('option').allTextContents()).find((o) => o === 'اجاره آپارتمان');
    await category.selectOption({ label: rentLabel });
    await expect(page.getByLabel(/حداقل ودیعه/)).toBeVisible();
    const buyLabel = (await category.locator('option').allTextContents()).find((o) => o === 'خرید آپارتمان');
    await category.selectOption({ label: buyLabel });
    await expect(page.getByLabel(/حداقل ودیعه/)).toHaveCount(0);

    await scrollThrough(page);
    await noHorizontalScroll(page);
    // axe's contrast check samples rendered pixels; a webfont still swapping in
    // (estedad) can catch a transitional frame and misreport a fine colour pair
    await page.evaluate(() => document.fonts.ready);
    expect(await a11yStable(page)).toEqual([]);
    expect(problems).toEqual([]);
  });

  test('free-text search narrows the table, pagination moves the page', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/properties');
    await expect(page.getByRole('table')).toBeVisible();
    await expect(page.getByRole('row').nth(1)).toBeVisible(); // header + at least one data row loaded
    const unfiltered = await page.getByRole('row').count();
    await page.getByRole('textbox', { name: 'جستجو' }).fill('155 متری');
    await page.locator('#main').getByRole('button', { name: /^جستجو$/ }).click();
    await expect(page.getByText('155 متری').first()).toBeVisible();
    const narrowed = await page.getByRole('row').count();
    expect(narrowed).toBeLessThan(unfiltered); // fewer than the unfiltered page

    await page.getByRole('textbox', { name: 'جستجو' }).fill('');
    await page.locator('#main').getByRole('button', { name: /^جستجو$/ }).click();
    await expect(page.getByRole('navigation', { name: 'صفحه‌ها' })).toBeVisible();
    await page.getByRole('navigation', { name: 'صفحه‌ها' }).getByRole('button', { name: '۲', exact: true }).click();
    await expect(page.getByRole('row').first()).toBeVisible();
  });

  test('the detail sheet, its inline selects and the similar-properties modal', async ({ page }) => {
    const s = await signIn(page, 'owner');
    const title = tag();
    const lead = await makeListing(page, s, title);
    try {
      await page.goto('/panel/properties');
      await page.getByRole('textbox', { name: 'جستجو' }).fill(title);
      await page.locator('#main').getByRole('button', { name: /^جستجو$/ }).click();
      const row = page.getByRole('row', { name: new RegExp(title) });
      await expect(row).toBeVisible();
      await row.getByLabel('مشاهدهٔ جزئیات').click();

      const sheet = page.getByRole('dialog');
      await expect(sheet.getByText(title)).toBeVisible();
      await expect(sheet.getByText('اطلاعات پایه')).toBeVisible();
      await expect(sheet.getByText('برداشت هوش مصنوعی')).toBeVisible();

      // جهت ساختمان — a select that PATCHes on change
      await sheet.getByLabel(/جهت ساختمان/).selectOption('شمالی');
      await expect(page.getByText('ذخیره شد').first()).toBeVisible();

      // «ملک‌های مشابه»
      await sheet.getByRole('button', { name: 'ملک‌های مشابه' }).click();
      const match = page.getByRole('dialog', { name: 'ملک‌های مشابه' });
      await expect(match).toBeVisible();
      await expect(match.getByText(/مبنای تطابق|ملک مشابهی پیدا نشد/).first()).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(match).toHaveCount(0);
      await expect(sheet).toBeVisible();
    } finally {
      await dropLead(page, s, lead.id);
    }
  });

  test('delete asks with the styled confirm dialog, never window.confirm, then removes the row', async ({ page }) => {
    const s = await signIn(page, 'owner');
    const title = tag();
    const lead = await makeListing(page, s, title);
    let cleaned = false;
    try {
      await page.goto('/panel/properties');
      await page.getByRole('textbox', { name: 'جستجو' }).fill(title);
      await page.locator('#main').getByRole('button', { name: /^جستجو$/ }).click();
      const row = page.getByRole('row', { name: new RegExp(title) });
      await expect(row).toBeVisible();

      await row.getByLabel('حذف').click();
      const dlg = page.getByRole('dialog', { name: 'حذف ملک' });
      await expect(dlg).toBeVisible();
      await expect(dlg).toContainText('برگشت‌پذیر نیست');
      await dlg.getByRole('button', { name: 'حذف' }).click();
      await expect(page.getByText('حذف شد').first()).toBeVisible();
      await expect(page.getByText('هیچ ملکی یافت نشد')).toBeVisible();
      cleaned = true;
    } finally {
      await dropLead(page, s, lead.id);
      void cleaned;
    }
  });

  test('both exports trigger without error', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/properties');
    const excelHref = await page.getByRole('link', { name: /خروجی اکسل/ }).getAttribute('href');
    expect(excelHref).toMatch(/\/api\/properties\/export\/excel/);
    const excelRes = await page.request.get(excelHref);
    expect(excelRes.ok()).toBeTruthy();

    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: /خروجی JSON/ }).click(),
    ]);
    expect(download.suggestedFilename()).toBe('properties-export.json');
  });

  test('a non-super_admin sees no AI photo tags block in the detail sheet', async ({ page }) => {
    await signIn(page, 'agent1');
    await page.goto('/panel/properties');
    await page.getByLabel('مشاهدهٔ جزئیات').first().click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toBeVisible();
    await expect(sheet.getByText('برچسب‌های هوش تصویری')).toHaveCount(0);
    // the AI facts block (reader) stays visible to every role — only its reread button is gated
    await expect(sheet.getByText('برداشت هوش مصنوعی')).toBeVisible();
    await expect(sheet.getByRole('button', { name: 'بازخوانی' })).toHaveCount(0);
  });
});

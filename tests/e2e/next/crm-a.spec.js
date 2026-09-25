// CRM stream A: «تماس‌های امروز» and «لیدها» in the new panel. Every outcome
// of the call queue, the matches and price-drop cards, and the leads list with
// its URL filters, bulk actions, add-lead dialog (photo and per-kind fields)
// and the lead drawer with its lightbox, follow-up form and timeline.
// Leads are created with unique names and deleted again, so the suite runs
// next to the other streams' data and can be run twice.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

const tag = () => `e2e-a-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
const visible = (loc) => loc.filter({ visible: true }).first();

// a real 16x16 PNG; the server re-encodes it as JPEG
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAGUlEQVR42mNkYPjPQApgYhhVMKpgVAEEAAC5mQH/pX7xVwAAAABJRU5ErkJggg==',
  'base64',
);

async function api(page, session, method, path, data) {
  const res = await page.request.fetch(`/api${path}`, {
    method, data, headers: { 'X-CSRF-Token': session.csrf_token },
  });
  expect(res.ok(), `${method} ${path}: ${await res.text()}`).toBeTruthy();
  return res.status() === 204 ? null : res.json();
}

async function makeLead(page, session, title, extra = {}) {
  return api(page, session, 'POST', '/crm/leads', {
    property_title: title, phone_number: '0912' + String(Math.floor(1e6 + Math.random() * 8e6)),
    city_name: 'تهران', listing_type: 'buy', price: 2_500_000_000, area: 90, ...extra,
  });
}

async function dropLead(page, session, id) {
  await page.request.fetch(`/api/crm/leads/${id}`, { method: 'DELETE', headers: { 'X-CSRF-Token': session.csrf_token }, timeout: 10_000 }).catch(() => null);
}

/** A queue card's outcome: a button on a wide screen, the «بیشتر» menu on a phone. */
async function outcome(page, card, name) {
  await expect(card).toBeVisible();
  const btn = card.getByRole('button', { name, exact: true });
  if (await btn.isVisible()) return btn.click();
  await card.getByRole('button', { name: 'نتیجه‌های دیگر' }).click();
  await page.getByRole('menuitem', { name }).click();
}

/* ───────────────────────── تماس‌های امروز ───────────────────────── */

test.describe('calls', () => {
  test('the call queue, matches, drops and summary load clean', async ({ page }) => {
    const problems = watchProblems(page);
    const s = await signIn(page, 'owner');
    const title = tag();
    const lead = await makeLead(page, s, title);
    try {
      await page.goto('/panel/crm/calls');
      await expect(page.getByRole('heading', { name: 'تماس‌های امروز', level: 1 })).toBeVisible();
      await expect(page.getByRole('list', { name: 'صف تماس' }).getByText(title)).toBeVisible();
      await expect(page.getByRole('heading', { name: /مشتری‌های هم‌خوان/ })).toBeVisible();
      await expect(page.getByRole('heading', { name: /ارزان شدند/ })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'امروز، به تفکیک مشاور' })).toBeVisible();
      await expect(page.getByRole('button', { name: 'بررسی الان' }).first()).toBeVisible();
      await scrollThrough(page);
      await noHorizontalScroll(page);
      expect(await a11y(page)).toEqual([]);
      expect(problems).toEqual([]);
    } finally {
      await dropLead(page, s, lead.id);
    }
  });

  test('an agent has no run-now and no per-agent summary', async ({ page }) => {
    await signIn(page, 'agent1');
    await page.goto('/panel/crm/calls');
    await expect(page.getByRole('heading', { name: /تماس‌های امروز من/ })).toBeVisible();
    await expect(page.getByRole('button', { name: 'بررسی الان' })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'امروز، به تفکیک مشاور' })).toHaveCount(0);
  });

  test('every outcome is logged and the card leaves the queue', async ({ page }) => {
    const s = await signIn(page, 'owner');
    const base = tag();
    const leads = [];
    for (const k of ['answered', 'callback', 'visit', 'wrong', 'none']) leads.push(await makeLead(page, s, `${base}-${k}`));
    try {
      await page.goto('/panel/crm/calls');
      const queue = page.getByRole('list', { name: 'صف تماس' });
      const card = (k) => queue.getByRole('listitem').filter({ hasText: `${base}-${k}` });

      // پاسخ داد: a note that stays on the lead
      await card('answered').getByRole('button', { name: 'پاسخ داد' }).click();
      const dlg = page.getByRole('dialog');
      await dlg.getByLabel('یادداشت (اختیاری)').fill('قیمت قطعی است');
      await dlg.getByRole('button', { name: 'ثبت' }).click();
      await expect(card('answered')).toHaveCount(0);
      // the card leaves once the POST answers; the read that follows may
      // still race the response, so it is polled rather than read once
      await expect.poll(async () => (await api(page, s, 'GET', `/crm/leads/${leads[0].id}`)).last_call_outcome).toBe('answered');
      const answered = await api(page, s, 'GET', `/crm/leads/${leads[0].id}`);
      expect(answered.notes).toContain('قیمت قطعی است');

      // دوباره زنگ بزن: tomorrow 10:00
      await outcome(page, card('callback'), 'دوباره زنگ بزن');
      await page.getByRole('radio', { name: 'فردا ۱۰ صبح' }).click();
      await page.getByRole('dialog').getByRole('button', { name: 'ثبت' }).click();
      await expect(card('callback')).toHaveCount(0);
      await expect.poll(async () => (await api(page, s, 'GET', `/crm/leads/${leads[1].id}`)).last_call_outcome).toBe('callback');
      const cb = await api(page, s, 'GET', `/crm/leads/${leads[1].id}`);
      expect(new Date(cb.next_call_at).getTime()).toBeGreaterThan(Date.now());

      // بازدید: a Jalali date and time goes to the calendar
      await outcome(page, card('visit'), 'بازدید');
      const when = page.getByRole('dialog').getByLabel('تاریخ و ساعت بازدید (اختیاری)');
      await when.fill('1405/08/10 16:30');
      await when.press('Tab');
      await page.getByRole('dialog').getByRole('button', { name: 'ثبت' }).click();
      await expect(page.getByText('در تقویم ثبت شد').first()).toBeVisible();
      await expect(card('visit')).toHaveCount(0);

      // شماره اشتباه: asks first, then closes the lead
      await card('wrong').getByRole('button', { name: 'نتیجه‌های دیگر' }).click();
      await page.getByRole('menuitem', { name: 'شماره اشتباه' }).click();
      await expect(page.getByRole('dialog')).toContainText('این لید بسته می‌شود');
      await page.getByRole('dialog').getByRole('button', { name: 'ثبت' }).click();
      await expect(card('wrong')).toHaveCount(0);
      await expect.poll(async () => (await api(page, s, 'GET', `/crm/leads/${leads[3].id}`)).status).toBe('rejected');

      // پاسخ نداد: one tap
      await card('none').getByRole('button', { name: 'پاسخ نداد' }).click();
      await expect(card('none')).toHaveCount(0);
    } finally {
      for (const l of leads) await dropLead(page, s, l.id);
    }
  });

  test('a match shows its SMS preview and the similar-listing drawer', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/crm/calls');
    const matches = page.getByRole('list', { name: 'تطبیق‌های تازه' });
    await expect(matches).toBeVisible();
    const first = matches.getByRole('listitem').first();
    await first.getByRole('button', { name: 'پیامک به مشتری' }).click();
    const dlg = page.getByRole('dialog');
    await expect(dlg.getByLabel(/متن پیامک/)).not.toHaveValue('');
    await expect(dlg.getByText(/بخش/).first()).toBeVisible();
    await dlg.getByRole('button', { name: 'انصراف' }).click();
    await first.getByRole('button', { name: 'کارهای بیشتر' }).click();
    await page.getByRole('menuitem', { name: 'جزئیات ملک' }).click();
    await expect(page.getByRole('dialog').getByText('مشخصات ملک')).toBeVisible();
  });
});

/* ───────────────────────── لیدها ───────────────────────── */

test.describe('leads', () => {
  test('the list loads clean, 25 to a page', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    await page.goto('/panel/crm/leads');
    await expect(page.getByRole('heading', { name: 'لیدها', level: 1 })).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'صفحه‌ها' })).toBeVisible();
    const statuses = page.getByRole('combobox', { name: 'تغییر وضعیت لید' }).filter({ visible: true });
    await expect(statuses).toHaveCount(25);
    await page.getByRole('navigation', { name: 'صفحه‌ها' }).getByRole('button', { name: '۲', exact: true }).click();
    await expect(page).toHaveURL(/page=2/);
    await scrollThrough(page);
    await noHorizontalScroll(page);
    expect(await a11y(page)).toEqual([]);
    expect(problems).toEqual([]);
  });

  test('filters live in the URL, the back button undoes them, the export follows', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/crm/leads');
    await page.getByRole('group', { name: 'فیلتر سریع وضعیت' }).getByRole('button', { name: /جدید/ }).click();
    await expect(page).toHaveURL(/status=new/);
    await expect(page.getByRole('link', { name: 'خروجی اکسل' })).toHaveAttribute('href', /export\/excel\?.*status=new/);
    const more = page.getByRole('button', { name: /فیلترهای بیشتر/ });
    if (await more.isVisible()) await more.click();
    await page.getByRole('button', { name: 'امروز', exact: true }).click();
    await expect(page).toHaveURL(/from=\d{4}-\d{2}-\d{2}.*to=/);
    await page.getByLabel('قیمت از').fill('1000000000');
    await expect(page).toHaveURL(/pmin=1000000000/);
    await expect(page.getByRole('link', { name: 'خروجی اکسل' })).toHaveAttribute('href', /price_min=1000000000/);
    await page.goBack();
    await expect(page).not.toHaveURL(/from=/);
    await expect(page).toHaveURL(/status=new/);
    await page.getByRole('button', { name: 'پاک کردن همه' }).click();
    await expect(page).not.toHaveURL(/status=/);
  });

  test('add a lead with a photo, open it, zoom, follow up, delete', async ({ page }) => {
    const s = await signIn(page, 'owner');
    const title = tag();
    await page.goto('/panel/crm/leads');
    await page.getByRole('button', { name: 'لید جدید' }).click();
    const dlg = page.getByRole('dialog');
    await dlg.getByLabel('عنوان ملک *').fill(title);
    await dlg.getByLabel('نوع ملک').selectOption('apartment');
    await dlg.getByLabel('طبقه', { exact: true }).fill('3');
    await dlg.getByLabel('آسانسور').selectOption('true');
    await dlg.getByLabel('قیمت (تومان)').fill('4500000000');
    await dlg.getByLabel('شماره تماس').fill('09125559911');
    await dlg.getByLabel('انتخاب تصویر').setInputFiles({ name: 'a.png', mimeType: 'image/png', buffer: PNG });
    await expect(dlg.getByRole('button', { name: 'حذف تصویر ۱' })).toBeVisible();
    await dlg.getByRole('button', { name: 'ثبت لید' }).click();
    await expect(page.getByText('لید جدید ثبت شد').first()).toBeVisible();

    await page.getByRole('searchbox', { name: 'جستجو در لیدها' }).fill(title);
    await expect(page).toHaveURL(new RegExp(`q=${title}`));
    await visible(page.getByRole('button', { name: title, exact: true })).click();
    await expect(page).toHaveURL(/lead=\d+/);
    const id = Number(new URL(page.url()).searchParams.get('lead'));
    try {
      const sheet = page.getByRole('dialog', { name: title });
      await expect(sheet.getByText('آسانسور')).toBeVisible();
      // the lightbox zooms and closes on its own, leaving the drawer open
      await sheet.getByRole('button', { name: 'بزرگ کردن تصویر ۱' }).first().click();
      const lb = page.getByRole('dialog', { name: /تصویر ۱ از ۱/ });
      await lb.getByRole('button', { name: 'بزرگ‌تر' }).click();
      await expect(lb.getByText('۱۳۰٪')).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(lb).toHaveCount(0);
      await expect(sheet).toBeVisible();

      await sheet.getByRole('tab', { name: 'پیگیری' }).click();
      await sheet.getByLabel('وضعیت CRM').selectOption('qualified');
      await sheet.getByLabel('یادداشت').fill('پیگیری از آزمون');
      await sheet.getByRole('button', { name: 'ذخیره' }).click();
      await expect(page.getByText('لید به‌روز شد').first()).toBeVisible();
      await sheet.getByRole('tab', { name: 'تاریخچه' }).click();
      await expect(sheet.getByText(/وضعیت به «qualified»/)).toBeVisible();
      expect(await a11y(page)).toEqual([]);

      await sheet.getByRole('button', { name: 'حذف' }).click();
      await page.getByRole('dialog', { name: 'حذف لید' }).getByRole('button', { name: 'حذف' }).click();
      await expect(page.getByText('لید حذف شد').first()).toBeVisible();
      await expect(page).not.toHaveURL(/lead=/);
    } finally {
      await dropLead(page, s, id);
    }
  });

  test('inline status, bulk status and bulk delete', async ({ page }) => {
    const s = await signIn(page, 'owner');
    const base = tag();
    const a = await makeLead(page, s, `${base}-1`);
    const b = await makeLead(page, s, `${base}-2`);
    try {
      await page.goto(`/panel/crm/leads?q=${base}`);
      await expect(page.getByRole('combobox', { name: 'تغییر وضعیت لید' }).filter({ visible: true })).toHaveCount(2);
      await page.getByRole('combobox', { name: 'تغییر وضعیت لید' }).filter({ visible: true }).first().selectOption('contacted');
      await expect(page.getByText('وضعیت لید تغییر کرد').first()).toBeVisible();

      for (const t of [`${base}-1`, `${base}-2`]) await visible(page.getByRole('checkbox', { name: `انتخاب لید ${t}` })).click();
      const bar = page.getByRole('region', { name: 'کارهای گروهی' });
      await expect(bar).toContainText('۲ لید انتخاب شده');
      await bar.getByLabel('تغییر وضعیت گروهی').selectOption('visit');
      await expect(page.getByText('۲ لید به‌روز شد').first()).toBeVisible();
      expect((await api(page, s, 'GET', `/crm/leads/${a.id}`)).status).toBe('visit');

      for (const t of [`${base}-1`, `${base}-2`]) await visible(page.getByRole('checkbox', { name: `انتخاب لید ${t}` })).click();
      await page.getByRole('region', { name: 'کارهای گروهی' }).getByRole('button', { name: 'حذف گروهی' }).click();
      await page.getByRole('dialog', { name: 'حذف گروهی' }).getByRole('button', { name: 'حذف' }).click();
      await expect(page.getByText('۲ لید حذف شد').first()).toBeVisible();
      await expect(page.getByText('هیچ لیدی با این فیلترها پیدا نشد.')).toBeVisible();
    } finally {
      await dropLead(page, s, a.id);
      await dropLead(page, s, b.id);
    }
  });

  test('similar listings open from a row, and the dark theme passes axe', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await signIn(page, 'owner');
    await page.goto('/panel/crm/leads');
    await visible(page.getByRole('button', { name: 'کارهای بیشتر' })).click();
    await page.getByRole('menuitem', { name: 'ملک‌های مشابه' }).click();
    const dlg = page.getByRole('dialog', { name: 'ملک‌های مشابه' });
    await expect(dlg).toBeVisible();
    await expect(dlg.getByText(/مبنای تطابق|ملک مشابهی پیدا نشد/).first()).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('html')).toHaveClass(/dark/);
    expect(await a11y(page)).toEqual([]);
  });
});

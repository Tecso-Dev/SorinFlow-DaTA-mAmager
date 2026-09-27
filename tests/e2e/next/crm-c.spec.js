// تقویم و کمد و زونکن — Jalali calendar (month/week/day) and the filing
// cabinet/binder explorer, both over real sample data. Runs across desktop,
// android and iphone projects (playwright.next.config.js), so every flow
// opens the tree through its "کمدها" Sheet trigger when the sidebar itself
// is hidden below the lg breakpoint, rather than assuming a desktop layout.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

// The cabinet/binder tree (tree.tsx, aria-label «کمد و زونکن‌ها») is mounted
// twice at once on a phone once the Sheet is open: the desktop copy stays in
// the DOM (display:none, so role/label queries already skip it), plus the
// Sheet's own copy. A plain text query is not filtered by that — scoping to
// this landmark keeps every cabinet/binder lookup pointed at the one visible
// copy instead of also matching the same name inside the hidden one.
const tree = (page) => page.getByRole('navigation', { name: 'کمد و زونکن‌ها' });

async function openTreeIfSheeted(page) {
  // a plain string is a substring match, unlike an anchored regex — the
  // button's accessible name is "کمدها" plus whatever the Menu icon
  // contributes, so an exact ^$ match silently never found it on a phone
  const trigger = page.getByRole('button', { name: 'کمدها' });
  // isVisible() is a one-shot, non-retrying read of *right now* — called
  // straight after goto()/onScope(), before this client component has
  // hydrated, it reported false (never visible) on a phone just as often
  // as it correctly reports false on desktop (where the button never
  // exists), silently skipping the open. waitFor() actually gives a
  // just-navigated page a moment to hydrate; desktop still resolves
  // quickly, just via the timeout instead of an instant read.
  const trulyDesktop = await trigger.waitFor({ state: 'visible', timeout: 2000 }).then(() => false).catch(() => true);
  if (trulyDesktop) return;
  const opened = page.getByRole('button', { name: 'کمد جدید' });
  // this scope's own unfiled list is ~300 real seeded properties (60 a
  // page), each card its own Tilt/motion component — a real click always
  // opens the Sheet, but mounting and animating it in can take a WebKit
  // main thread that busy well past a short wait. A blind fixed-attempt
  // retry loop here previously re-clicked the trigger on that same slow
  // mount, toggling the still-opening Sheet closed again — fixed: only
  // click when it is confirmed *not* open, and give each attempt long
  // enough that "still not open" means the click truly did not land (e.g.
  // one right after goto(), before hydration attaches the handler), not
  // that WebKit is merely still animating it in.
  for (let attempt = 0; attempt < 2; attempt++) {
    if (await opened.isVisible().catch(() => false)) return;
    await trigger.click();
    if (await opened.waitFor({ state: 'visible', timeout: 15000 }).then(() => true).catch(() => false)) return;
  }
}

test.describe('تقویم', () => {
  test('loads with data, filters by type, and switches views', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    await page.goto('/panel/crm/calendar');
    await expect(page.getByRole('heading', { name: 'تقویم' })).toBeVisible();
    // the month grid: Saturday first — the header shows «شنبه» on a wide
    // viewport and the short «ش» on a narrow one, never both (both spans are
    // always in the DOM once mounted, so .or().first() would deterministically
    // grab the hidden one — check each one's own visibility instead). The grid
    // itself only mounts once the events query resolves (calendar-page.tsx
    // shows a Skeleton in its place until then), so this polls instead of
    // reading isVisible() once — a one-shot read right after the heading
    // (which is not data-gated) could still catch the Skeleton.
    await expect.poll(async () => {
      const satFull = await page.getByText('شنبه', { exact: true }).first().isVisible().catch(() => false);
      const satShort = await page.getByText('ش', { exact: true }).first().isVisible().catch(() => false);
      return satFull || satShort;
    }, { timeout: 10_000 }).toBeTruthy();
    await expect(page.getByText(/^مهر|^آبان|^آذر|^دی|^بهمن|^اسفند|^فروردین|^اردیبهشت|^خرداد|^تیر|^مرداد|^شهریور/).first()).toBeVisible();

    // exact: the upcoming strip's own cards also start with «امروز»
    await page.getByRole('button', { name: 'امروز', exact: true }).click();
    await page.waitForTimeout(300);

    // event-type filter narrows without erroring
    await page.getByLabel('فیلتر نوع رویداد').selectOption('visit');
    await page.waitForTimeout(400);
    await page.getByLabel('فیلتر نوع رویداد').selectOption('');

    // week and day views render a different caption without erroring (a
    // plain button group, not ARIA tabs — see calendar-page.tsx)
    const viewGroup = page.getByRole('group', { name: 'نمای تقویم' });
    await viewGroup.getByRole('button', { name: 'هفته' }).click();
    await page.waitForTimeout(300);
    await viewGroup.getByRole('button', { name: 'روز' }).click();
    await page.waitForTimeout(300);
    await viewGroup.getByRole('button', { name: 'ماه' }).click();
    await page.waitForTimeout(300);

    await noHorizontalScroll(page);
    expect(problems).toEqual([]);
  });

  test('creates, edits, sends an SMS for and deletes an appointment', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    await page.goto('/panel/crm/calendar');

    const title = `e2e-cal-${Date.now()}`;
    await page.getByRole('button', { name: 'قرار جدید' }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('عنوان قرار').fill(title);
    await dialog.getByLabel('نام مالک').fill('مالک e2e');
    await dialog.getByLabel('شمارهٔ مالک').fill('09120000001');
    await dialog.getByRole('button', { name: 'ثبت قرار' }).click();
    await expect(page.getByText('قرار ثبت شد').first()).toBeVisible();

    // the month grid caps a busy day at three chips («+N بیشتر») — other
    // streams' own sample data on today is out of this test's control, so
    // the day view (uncapped) is what actually proves the create worked
    await page.getByRole('group', { name: 'نمای تقویم' }).getByRole('button', { name: 'روز' }).click();
    const chip = page.getByRole('button', { name: new RegExp(title) }).first();
    await expect(chip).toBeVisible();
    await chip.click();

    const edit = page.getByRole('dialog');
    await expect(edit.getByRole('heading', { name: 'ویرایش قرار' })).toBeVisible();
    await expect(edit.getByLabel('وضعیت')).toBeVisible(); // status only shows when editing

    await edit.getByRole('button', { name: /ارسال پیامک الان/ }).click();
    // no phone typed for the customer/agent side, only the owner's — the
    // confirm dialog still opens and can be cancelled without sending
    const confirmDialog = page.getByRole('dialog').filter({ hasText: 'ارسال پیامک این قرار؟' });
    if (await confirmDialog.isVisible().catch(() => false)) {
      await confirmDialog.getByRole('button', { name: 'انصراف' }).click();
    }

    await edit.getByRole('button', { name: 'حذف' }).click();
    const confirmDelete = page.getByRole('dialog').filter({ hasText: 'این قرار حذف شود؟' });
    await confirmDelete.getByRole('button', { name: 'حذف' }).click();
    await expect(page.getByText('قرار حذف شد').first()).toBeVisible();
    await expect(page.getByRole('button', { name: new RegExp(title) })).toHaveCount(0);

    expect(problems).toEqual([]);
  });

  test('accessibility: month view has no serious/critical violations', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/crm/calendar');
    await scrollThrough(page);
    expect(await a11y(page)).toEqual([]);
  });
});

test.describe('کمد و زونکن', () => {
  test('loads the tree, overview counts and the card grid', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    await page.goto('/panel/crm/filing');
    await expect(page.getByRole('heading', { name: 'کمد و زونکن' })).toBeVisible();
    await expect(page.getByText('بدون زونکن').first()).toBeVisible();
    // the grid's own heading, checked before the tree Sheet ever opens: a
    // Sheet is a modal (Radix Dialog underneath) and correctly aria-hides
    // the rest of the page while open, so this query would find nothing
    // once openTreeIfSheeted below has run
    await expect(page.getByRole('heading', { name: 'فایل‌های بدون زونکن' })).toBeVisible();
    await openTreeIfSheeted(page);
    // inside the (now open, on a phone) tree: the same "unfiled" entry
    await expect(page.getByRole('button', { name: 'فایل‌های بدون زونکن' })).toBeVisible();
    await noHorizontalScroll(page);
    // a scraped listing's own thumbnail host going stale is not this page's bug
    expect(problems.filter((p) => !/Failed to load resource/.test(p))).toEqual([]);
  });

  test('search narrows the grid to an empty state', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/crm/filing');
    const nonsense = `no-such-file-${Date.now()}`;
    await page.getByLabel('جستجوی فایل').fill(nonsense);
    await page.keyboard.press('Enter');
    await expect(page.getByText('چیزی با این فیلتر پیدا نشد')).toBeVisible();
  });

  test('cabinet and binder: create, select, bulk-pin a file, edit it, share it, then delete both', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    await page.goto('/panel/crm/filing');
    await openTreeIfSheeted(page);

    const cabName = `e2e-cab-${Date.now()}`;
    await page.getByRole('button', { name: 'کمد جدید' }).click();
    let dialog = page.getByRole('dialog');
    await dialog.getByLabel('نام کمد').fill(cabName);
    await dialog.getByRole('button', { name: 'ساخت کمد' }).click();
    await expect(page.getByText('کمد ساخته شد').first()).toBeVisible();

    const cabRow = tree(page).getByText(cabName, { exact: true }).locator('..');
    await cabRow.getByRole('button', { name: 'عملیات کمد' }).click();
    await page.getByRole('menuitem', { name: 'زونکن جدید' }).click();
    const binName = `e2e-bin-${Date.now()}`;
    dialog = page.getByRole('dialog');
    await dialog.getByLabel('نام', { exact: true }).fill(binName);
    await dialog.getByRole('button', { name: 'ساخت' }).click();
    await expect(page.getByText('زونکن ساخته شد').first()).toBeVisible();

    // an empty binder shows the empty state
    await tree(page).getByRole('button', { name: new RegExp(binName) }).click();
    await expect(page.getByText('فایلی اینجا نیست')).toBeVisible();

    // back to «بدون زونکن» — selecting a binder above closed the tree Sheet
    // on a phone (its own scope change), so it may need reopening
    await openTreeIfSheeted(page);
    await tree(page).getByRole('button', { name: 'فایل‌های بدون زونکن' }).click();
    await page.waitForTimeout(600);
    const checkboxes = page.locator('button[role="checkbox"]');
    await checkboxes.nth(0).click();
    await checkboxes.nth(1).click();
    const bulkBar = page.getByText('۲ انتخاب‌شده').locator('..');
    await expect(bulkBar).toBeVisible();
    await bulkBar.getByRole('button', { name: 'سنجاق', exact: true }).click();
    await expect(page.getByText(/روی ۲ فایل انجام شد/).first()).toBeVisible();
    // clean the pin back off so the shared sample data is not left marked
    await checkboxes.nth(0).click();
    await checkboxes.nth(1).click();
    const bulkBar2 = page.getByText('۲ انتخاب‌شده').locator('..');
    await bulkBar2.getByRole('button', { name: 'برداشتن سنجاق' }).click();
    await expect(page.getByText(/روی ۲ فایل انجام شد/).first()).toBeVisible();

    // edit the first file (a plain role+name query, not a class selector —
    // the sidebar's own nav links share the card's "group relative" classes)
    const main = page.getByRole('main');
    await main.getByRole('button', { name: 'ویرایش فایل' }).first().click({ force: true });
    dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('heading', { name: 'ویرایش فایل' })).toBeVisible();
    await expect(dialog.getByLabel('عنوان')).not.toHaveValue('');
    await page.keyboard.press('Escape');

    // share the same file
    await main.getByRole('button', { name: 'اشتراک‌گذاری' }).first().click({ force: true });
    dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('link', { name: 'واتس‌اپ' })).toHaveAttribute('href', /wa\.me/);
    await expect(dialog.getByRole('link', { name: 'تلگرام' })).toHaveAttribute('href', /t\.me/);
    await page.keyboard.press('Escape');

    // cleanup: delete the test binder, then the test cabinet
    await openTreeIfSheeted(page);
    const binRow = tree(page).getByText(binName, { exact: true }).locator('../..');
    await binRow.getByRole('button', { name: 'عملیات زونکن' }).click();
    await page.getByRole('menuitem', { name: 'حذف', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'حذف', exact: true }).click();
    await expect(page.getByText('حذف شد').first()).toBeVisible();

    const cabRow2 = tree(page).getByText(cabName, { exact: true }).locator('..');
    await cabRow2.getByRole('button', { name: 'عملیات کمد' }).click();
    await page.getByRole('menuitem', { name: 'حذف کمد' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'حذف', exact: true }).click();
    await expect(page.getByText('کمد حذف شد').first()).toBeVisible();

    // the scraped photos' own 404s are not this page's bug
    expect(problems.filter((p) => !/Failed to load resource/.test(p))).toEqual([]);
  });

  test('accessibility: the grid has no serious/critical violations', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/crm/filing');
    await scrollThrough(page);
    expect(await a11y(page)).toEqual([]);
  });
});

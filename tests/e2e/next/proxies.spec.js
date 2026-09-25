// «پراکسی‌ها»: the simplest of the four scraper/divar sections — no OTP, no
// polling, no per-user ownership. Proxies are global (one shared table, no
// other stream's nav section touches it), so every row this suite creates
// uses a unique high port and is deleted again in a `finally`, and no test
// ever calls the real "test"/"test-all"/bulk-import-with-test flow against
// rows it did not just create itself: those trigger a REAL ~25s-per-proxy
// network probe (app/services/proxy_pool.py TIMEOUT=25.0) against every
// currently active proxy in the table, not just the one clicked, so running
// one against a table this suite does not fully own would be slow and would
// mutate other people's rows. The bulk-import test only ever imports rows
// that are refused or duplicates (imported=0), which the backend never
// probes (`if request.test and imported: …`), so it stays fast and inert.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

let seq = 0;
const port = () => 24000 + (Date.now() % 5000) + seq++;
// The desktop table and the phone card list both sit in the DOM at once
// (CSS hides whichever the viewport does not use), so any text or row that
// exists in the list resolves twice — once actionable, once display:none.
// Pick the one actually on screen, exactly like crm-a.spec.js's own helper.
const visible = (loc) => loc.filter({ visible: true }).first();
// The panel shows every number in Persian digits (faNum) — matching a port
// by its plain JS string would never find it on screen.
const faDigits = (n) => String(n).replace(/[0-9]/g, (d) => '۰۱۲۳۴۵۶۷۸۹'[d]);

async function api(page, session, method, path, data) {
  const res = await page.request.fetch(`/api${path}`, { method, data, headers: { 'X-CSRF-Token': session.csrf_token } });
  expect(res.ok(), `${method} ${path}: ${await res.text()}`).toBeTruthy();
  return res.status() === 204 ? null : res.json();
}

async function addProxy(page, session, address, p) {
  return api(page, session, 'POST', '/proxies', { address, port: p, protocol: 'http' });
}

async function dropProxy(page, session, id) {
  await page.request.fetch(`/api/proxies/${id}`, { method: 'DELETE', headers: { 'X-CSRF-Token': session.csrf_token } }).catch(() => null);
}

test('the page loads with data, no console/CSP errors, no sideways scroll, clean a11y', async ({ page }) => {
  const problems = watchProblems(page);
  const s = await signIn(page, 'owner');
  const p1 = await addProxy(page, s, '1.1.1.1', port());
  try {
    await page.goto('/panel/proxies');
    await expect(page.getByRole('heading', { name: 'پراکسی‌ها', level: 1 })).toBeVisible();
    await expect(visible(page.getByText('1.1.1.1'))).toBeVisible();
    // untested yet: the exit column must say so, never blank or crash
    await expect(visible(page.getByText('تست نشده'))).toBeVisible();
    await scrollThrough(page);
    await noHorizontalScroll(page);
    expect(await a11y(page)).toEqual([]);
    expect(problems).toEqual([]);
  } finally {
    await dropProxy(page, s, p1.id);
  }
});

test('add-proxy validation: a private address is refused with the server\'s Persian message', async ({ page }) => {
  await signIn(page, 'owner');
  await page.goto('/panel/proxies');
  await page.getByRole('button', { name: 'افزودن پراکسی' }).first().click();
  const dlg = page.getByRole('dialog');
  await dlg.getByLabel('آدرس IP یا نام').fill('10.0.0.1');
  await dlg.getByLabel('پورت').fill(String(port()));
  await dlg.getByRole('button', { name: 'افزودن پراکسی' }).last().click();
  await expect(page.getByText('نشانی‌های داخلی و خصوصی پذیرفته نمی‌شوند').first()).toBeVisible();
  // refused server-side: never landed in the table
  await expect(page.getByText('10.0.0.1')).toHaveCount(0);
});

test('bulk import: three line formats parsed, refused/duplicate counts reported, nothing crashes', async ({ page }) => {
  const s = await signIn(page, 'owner');
  const dupPort = port();
  const dup = await addProxy(page, s, '1.1.1.9', dupPort);
  try {
    await page.goto('/panel/proxies');
    await page.getByRole('button', { name: 'وارد کردن دسته‌ای' }).first().click();
    const dlg = page.getByRole('dialog');
    // format 1 (ip:port) and format 2 (ip:port:user:pass): both private, refused
    // format 3 (scheme://host:port): the already-existing row above, so it is
    // parsed correctly (proving the scheme/host/port split works) and reported
    // as a duplicate rather than imported again.
    await dlg.getByLabel('لیست پراکسی‌ها').fill(
      `10.0.0.5:${port()}\n10.0.0.6:${port()}:user:pass\nhttp://1.1.1.9:${dupPort}`,
    );
    await dlg.getByRole('button', { name: 'وارد کردن', exact: true }).click();
    await expect(page.getByText('وارد کردن انجام شد').first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/۰ افزوده شد، ۱ تکراری، ۲ آدرس داخلی رد شد/).first()).toBeVisible();
  } finally {
    await dropProxy(page, s, dup.id);
  }
});

test('test, toggle and delete a proxy row', async ({ page }) => {
  test.setTimeout(120_000); // a real ~25s network probe against Divar runs here
  const s = await signIn(page, 'owner');
  const myPort = port();
  await page.goto('/panel/proxies');
  await page.getByRole('button', { name: 'افزودن پراکسی' }).first().click();
  const addDlg = page.getByRole('dialog');
  await addDlg.getByLabel('آدرس IP یا نام').fill('1.1.1.1');
  await addDlg.getByLabel('پورت').fill(String(myPort));
  await addDlg.getByRole('button', { name: 'افزودن پراکسی' }).last().click();
  await expect(page.getByText('پراکسی اضافه شد').first()).toBeVisible();

  const list = await api(page, s, 'GET', '/proxies?active_only=false');
  const mine = list.items.find((x) => x.port === myPort);
  expect(mine).toBeTruthy();
  expect(mine.is_active).toBe(true); // model default; the switch below starts on

  try {
    // desktop table row and phone card both exist in the DOM at once, one of
    // them display:none — .first() alone would grab whichever is FIRST in
    // markup (the table row) regardless of viewport, and clicking inside a
    // hidden row just hangs until the action times out. visible() picks the
    // one actually on screen for whichever project is running. Matched by
    // its own port, not just the address, in case another row with the same
    // 1.1.1.1 address is present.
    const anyRow = visible(page.locator('tr, li').filter({ hasText: faDigits(myPort) }));

    // test — a real probe against an address that is not actually a proxy:
    // it always fails here, which is exactly the case the exit column must
    // survive gracefully (no exit info at all, never a blank/broken cell)
    await anyRow.getByRole('button', { name: 'تست پراکسی' }).click();
    await Promise.race([
      page.getByText('تست موفق').first().waitFor({ timeout: 60_000 }),
      page.getByText('پراکسی کار نمی‌کند').first().waitFor({ timeout: 60_000 }),
    ]);
    await expect(anyRow.getByText('تست نشده')).toBeVisible();
    await noHorizontalScroll(page);

    // toggle
    const sw = anyRow.getByRole('switch');
    await expect(sw).toBeChecked();
    await sw.click();
    await expect(sw).not.toBeChecked();

    // delete
    await anyRow.getByRole('button', { name: 'حذف پراکسی' }).click();
    await page.getByRole('dialog', { name: 'حذف پراکسی' }).getByRole('button', { name: 'حذف', exact: true }).click();
    await expect(page.getByText('پراکسی حذف شد').first()).toBeVisible();
    await expect(page.getByText('1.1.1.1')).toHaveCount(0);
  } finally {
    // in case the in-page delete above failed for any reason
    const still = (await api(page, s, 'GET', '/proxies?active_only=false')).items.find((x) => x.port === myPort);
    if (still) await dropProxy(page, s, still.id);
  }
});

// «حذف همه» uses the confirm_count pattern (DELETE /proxies?confirm_count=N,
// N = the exact total shown on screen at the moment of the click). The
// endpoint has no per-owner scope — it deletes every row in the table, not
// just this suite's own — so exercising it for real here would risk wiping
// rows another run or another person added in the meantime. No test below
// calls it; it was checked by hand instead (see the phase report).

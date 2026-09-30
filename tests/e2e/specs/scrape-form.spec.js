// The «اسکرپینگ جدید» form, end to end (#34): the help line under the date
// field, the Divar estimate, starting a run and cancelling it, and axe.
//
// Nothing here reaches Divar. /api/scraper/estimate — the one call on this
// page that asks Divar — is answered by page.route, and scripts/e2e_up.sh
// runs the app with SCRAPE_WORKER_ENABLED=false, so the run «شروع» queues
// stays pending until the test cancels it: no Chromium, no search.
//
// The fields that belong to one category and not another are not tested
// here: that behaviour waits on #27.
const { test, expect } = require('@playwright/test');
const { loginAs } = require('../fixtures/auth');
const { checkA11y } = require('../fixtures/a11y');

const ESTIMATE = {
  count: 342, error: null,
  applied_by_divar: ['business-type', 'category'],
  applied_after_scrape: ['حداقل اتاق'],
};

async function openTheForm(page, request) {
  await loginAs(page, request, 'root');
  const asked = [];
  await page.route('**/api/scraper/estimate?**', route => {
    asked.push(new URL(route.request().url()).searchParams);
    return route.fulfill({ json: ESTIMATE });
  });
  await page.goto('/dashboard/');
  await expect(page.locator('#main-app')).toBeVisible();
  await page.locator('#nav-link-scraper').click();
  await expect(page.locator('#section-scraper')).toBeVisible();
  // the pickers are filled from /scraper/cities and /scraper/categories
  await expect(page.locator('#scraper-category option[value="rent-apartment"]')).toHaveCount(1);
  await page.waitForFunction(() => typeof document.getElementById('scraper-city-picker')?._setCityValue === 'function');
  return asked;
}

async function pickCityAndCategory(page) {
  await page.evaluate(() => document.getElementById('scraper-city-picker')._setCityValue('urmia'));
  await expect(page.locator('#scraper-city')).toHaveValue('urmia');
  await page.locator('#scraper-category').selectOption('rent-apartment');
}

test.describe('scrape form', () => {
  test('the date field says the day is walked to its end and «تعداد» only caps what is saved', async ({ page, request }) => {
    await openTheForm(page, request);
    await page.locator('#scraper-more > summary').click();
    const help = page.locator('#scraper-date-help');
    await expect(help).toBeVisible();
    await expect(help).toContainText('تا آخر همان روز');
    await expect(help).toContainText('«تعداد» فقط سقف');
    await expect(page.locator('#scraper-posted-date')).toHaveAttribute('aria-describedby', 'scraper-date-help');
  });

  test('the estimate asks with the form\'s filters and shows Divar\'s number', async ({ page, request }) => {
    const asked = await openTheForm(page, request);
    await pickCityAndCategory(page);
    await page.locator('#scraper-more > summary').click();
    await page.locator('#scraper-min-rooms').fill('2');
    await page.locator('#scraper-estimate-btn').click();

    const box = page.locator('#scraper-estimate');
    await expect(box).toBeVisible();
    await expect(box).toContainText('۳۴۲');
    await expect(box).toContainText('حداقل اتاق');
    const last = asked[asked.length - 1];
    expect(last.get('city')).toBe('urmia');
    expect(last.get('category')).toBe('rent-apartment');
    expect(last.get('min_rooms')).toBe('2');
  });

  test('a run can be started from the form and cancelled from the list', async ({ page, request }) => {
    await openTheForm(page, request);
    await pickCityAndCategory(page);
    await page.locator('#scraper-pages').fill('3');
    await page.locator('#scraper-images').uncheck();

    const started = page.waitForResponse(r => r.url().includes('/api/scraper/start')
                                             && r.request().method() === 'POST');
    await page.locator('#scraper-form button[type="submit"]').click();
    // No live Divar session is seeded for root: the panel warns first, and
    // «ادامه» goes on without one — the run is only queued here.
    const goOn = page.locator('#continue-scraping-btn');
    if (await goOn.waitFor({ state: 'visible', timeout: 3000 }).then(() => true, () => false)) {
      await goOn.click();
      // Bootstrap drops a hide() asked for mid-animation; a dialog left open
      // here sits over the jobs table and swallows the «لغو» click below.
      await expect(page.locator('#cookieWarningModal')).toBeHidden();
    }
    const resp = await started;
    expect(resp.status()).toBe(200);
    const job = await resp.json();
    expect(job.status).toBe('pending');
    const sent = resp.request().postDataJSON();
    expect(sent).toMatchObject({ city: 'urmia', category: 'rent-apartment', max_items: 3, download_images: false });

    const cancel = page.locator(`button[onclick="cancelJob('${job.job_id}')"]`);
    await expect(cancel).toBeVisible();
    await cancel.click();
    // the site's own dialog, never the browser's confirm()
    await expect(page.locator('#ask-ok')).toBeVisible();
    const cancelled = page.waitForResponse(r => r.url().includes(`/api/scraper/jobs/${job.job_id}/cancel`));
    await page.locator('#ask-ok').click();
    expect((await cancelled).status()).toBe(200);
    await expect(cancel).toHaveCount(0);

    const row = await request.get(`/api/scraper/jobs/${job.job_id}`, {
      headers: { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem('sf_token'))}` },
    });
    expect((await row.json()).status).toBe('cancelled');
  });

  test('accessibility: the open form has no new serious or critical findings', async ({ page, request }) => {
    await openTheForm(page, request);
    await pickCityAndCategory(page);
    await page.locator('#scraper-more > summary').click();
    await page.locator('#scraper-estimate-btn').click();
    await expect(page.locator('#scraper-estimate')).toContainText('۳۴۲');
    // The form's own card: the jobs table beside it has findings of its own
    // (unnamed progress bars, its category filter) that depend on which runs
    // exist, and are that table's business, not this form's.
    expect(await checkA11y(page, '/dashboard/ scraper form',
                           { include: '.card:has(#scraper-form)' })).toEqual([]);
  });
});

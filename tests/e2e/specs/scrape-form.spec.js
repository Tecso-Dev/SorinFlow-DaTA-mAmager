// The «اسکرپینگ جدید» form, end to end (#34): the help line under the date
// field, the Divar estimate, starting a run and cancelling it, and axe.
//
// Nothing here reaches Divar. /api/scraper/estimate — the one call on this
// page that asks Divar — is answered by page.route, and scripts/e2e_up.sh
// runs the app with SCRAPE_WORKER_ENABLED=false, so the run «شروع» queues
// stays pending until the test cancels it: no Chromium, no search.
//
// The form is built from each category's own Divar filters (#27): the last
// test switches categories — a sale, a rental, a short-term let, a service —
// and checks that only that category's fields show and that a field which
// stops applying is emptied, not just hidden.
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

  test('switching the category shows only its Divar filters and empties the rest (#27, #34)', async ({ page, request }) => {
    const asked = await openTheForm(page, request);
    await pickCityAndCategory(page);                         // rent-apartment
    await page.locator('#scraper-more > summary').click();
    const shown = key => page.locator(`#scraper-form [data-filter-key="${key}"], #scraper-extra-filters [data-extra-key="${key}"]`);

    // a rental: deposit and monthly rent, and Divar's own apartment filters
    await expect(shown('credit')).toBeVisible();
    await expect(shown('price')).toBeHidden();
    await expect(shown('building-age')).toBeVisible();
    await expect(shown('deed_type')).toHaveCount(0);          // a sale's filter
    await expect(shown('cooling_system')).toHaveCount(0);     // options not known yet: not offered
    await page.locator('#scraper-min-deposit').fill('100000000');
    await page.locator('#scraper-f-building-age-max').fill('5');
    await page.locator('#scraper-has-elevator').check();

    // a sale: the deposit is hidden AND emptied, the building age carries over
    await page.locator('#scraper-category').selectOption('buy-apartment');
    await expect(shown('credit')).toBeHidden();
    await expect(page.locator('#scraper-min-deposit')).toHaveValue('');
    await expect(shown('price')).toBeVisible();
    await expect(shown('deed_type')).toBeVisible();
    await expect(page.locator('#scraper-f-building-age-max')).toHaveValue('5');
    await expect(page.locator('#scraper-has-elevator')).toBeChecked();

    await page.locator('#scraper-estimate-btn').click();
    await expect(page.locator('#scraper-estimate')).toContainText('۳۴۲');
    const last = asked[asked.length - 1];
    expect(last.get('category')).toBe('buy-apartment');
    expect(last.get('min_deposit')).toBeNull();
    expect(JSON.parse(last.get('divar_filters'))).toEqual({ 'building-age': { max: 5 } });

    // a shop for rent: no lift, no building age at Divar — gone and empty
    await page.locator('#scraper-category').selectOption('rent-store');
    await expect(shown('elevator')).toBeHidden();
    await expect(page.locator('#scraper-has-elevator')).not.toBeChecked();
    await expect(shown('building-age')).toHaveCount(0);
    await page.locator('#scraper-category').selectOption('rent-apartment');
    await expect(page.locator('#scraper-f-building-age-max')).toHaveValue('');

    // short-term: daily rent and capacity instead of deposit and rent
    await page.locator('#scraper-category').selectOption('rent-temporary');
    await expect(shown('daily_rent')).toBeVisible();
    await expect(shown('person_capacity')).toBeVisible();
    await expect(shown('credit')).toBeHidden();
    await expect(shown('rent')).toBeHidden();

    // services: only what every category has
    await page.locator('#scraper-category').selectOption('real-estate-services');
    for (const key of ['price', 'credit', 'size', 'rooms', 'parking']) await expect(shown(key)).toBeHidden();
    await expect(shown('business-type')).toBeVisible();
    await expect(shown('recent_ads')).toBeVisible();
  });

  test('a Divar link fills the category\'s own filters and names what it lacks (#27)', async ({ page, request }) => {
    await openTheForm(page, request);
    await page.locator('#scraper-link').fill(
      'https://divar.ir/s/urmia/buy-apartment?deed_type=single_page%2C&elevator=true&credit=100-500&building-age=-10');
    await page.locator('#scraper-link-btn').click();
    await expect(page.locator('#scraper-link-note')).toContainText('فرم پر شد');
    await expect(page.locator('#scraper-category')).toHaveValue('buy-apartment');
    await expect(page.locator('#scraper-has-elevator')).toBeChecked();
    await expect(page.locator('#scraper-extra-filters [data-extra-key="deed_type"] input[value="single_page"]')).toBeChecked();
    await expect(page.locator('#scraper-f-building-age-max')).toHaveValue('10');
    await expect(page.locator('#scraper-link-note')).toContainText('ودیعه');
    await expect(page.locator('#scraper-min-deposit')).toHaveValue('');
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

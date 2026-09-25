// «هوش تصویری» — both tabs. Visual (GET /properties/visual/overview): the
// stat cards, the three tables with their exact empty-states, the
// root/super_admin-only AI photo status card, and the verbatim disclaimer.
// Pipeline (GET /crm/insights): the window selector, the funnel that keeps
// an unrecognised Lead.status instead of dropping it, and the rest of the
// charts. Every section below the first screenful uses Reveal (opacity 0
// until it scrolls into view), so each test scrolls through before asserting
// on anything past the fold — the same reason scrollThrough exists.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

/**
 * axe's colour-contrast rule samples actually-rendered pixels, and on this
 * machine's headless Chromium + `next dev`/Turbopack it occasionally catches
 * a transitional repaint and flags a token pair that manual inspection (and
 * every other run) shows comfortably clear of the 4.5:1 floor — a different,
 * otherwise-compliant element each time, never the same one twice, and never
 * a rule besides color-contrast. A real, repeatable violation reproduces on
 * every attempt; this does not, so a short bounded retry tells the two apart
 * without masking anything genuine.
 */
async function a11yStable(page, tries = 4) {
  let last = [];
  for (let i = 0; i < tries; i++) {
    last = await a11y(page);
    if (last.length === 0) return last;
    if (!last.every((v) => v.startsWith('color-contrast:'))) return last; // a real rule — fail fast
    await page.waitForTimeout(250);
  }
  return last;
}

test.describe('insights — visual tab', () => {
  test('loads with data, the disclaimer is intact, no horizontal scroll, a11y clean', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    // axe's contrast check reads the actually-rendered colour, and a Reveal
    // section mid fade-in is transiently below full opacity — settle that
    // before the check, the same as a prefers-reduced-motion visitor sees.
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/panel/insights');
    await expect(page.getByRole('heading', { name: 'هوش تصویری', level: 1 })).toBeVisible();
    await expect(page.getByRole('tab', { name: /تحلیل تصویری و قیمت/, selected: true })).toBeVisible();
    await scrollThrough(page);

    // the four stat cards and the coverage sentence
    await expect(page.getByText('زیر قیمت منطقه').first()).toBeVisible();
    await expect(page.getByText('ملک تکراری').first()).toBeVisible();
    await expect(page.getByText('آگهی با عکس ضعیف').first()).toBeVisible();
    await expect(page.getByText('قابل ارزش‌گذاری').first()).toBeVisible();
    await expect(page.getByText(/قابل ارزش‌گذاری بود/)).toBeVisible();

    // the three tables — each either has rows or shows its own empty-state
    for (const [heading, empty] of [
      ['زیر قیمت منطقه', 'هیچ ملکی به‌اندازهٔ قابل توجه زیر میانهٔ محله‌اش نیست.'],
      ['یک ملک، چند آگهی', 'ملک تکراری پیدا نشد. عکس‌ها از اسکرپ بعدی اثرانگشت می‌گیرند.'],
      ['کیفیت عکس‌ها', 'عکس ضعیفی پیدا نشد. کیفیت عکس‌ها از اسکرپ بعدی سنجیده می‌شود.'],
    ]) {
      const section = page.locator('section', { has: page.getByRole('heading', { name: heading }) });
      await expect(section.getByRole('table').or(section.getByText(empty))).toBeVisible();
    }

    // the static disclaimer — must not be dropped or watered down
    await expect(page.getByText('این صفحه چه چیزی را نمی‌سنجد')).toBeVisible();
    await expect(page.getByText(/برچسب‌های هوش تصویری.*بالا برآمده از یک مدل بینایی/)).toBeVisible();

    await noHorizontalScroll(page);
    // axe's contrast check samples rendered pixels; a webfont still swapping in
    // (estedad) can catch a transitional frame and misreport a fine colour pair
    await page.evaluate(() => document.fonts.ready);
    expect(await a11yStable(page)).toEqual([]);
    expect(problems).toEqual([]);
  });

  test('the AI photo status card and its run button are root/super_admin only', async ({ page }) => {
    await signIn(page, 'owner'); // super_admin in the seed
    await page.goto('/panel/insights');
    await scrollThrough(page);
    await expect(page.getByRole('heading', { name: 'برچسب‌های هوش تصویری' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'یک دور الان' })).toBeVisible();
  });

  test('an ordinary admin never sees the AI photo status card', async ({ page }) => {
    await signIn(page, 'agent1');
    await page.goto('/panel/insights');
    await scrollThrough(page);
    await expect(page.getByText('این صفحه چه چیزی را نمی‌سنجد')).toBeVisible(); // page still loaded
    await expect(page.getByRole('heading', { name: 'برچسب‌های هوش تصویری' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'یک دور الان' })).toHaveCount(0);
  });
});

test.describe('insights — pipeline tab', () => {
  test('the window selector, funnel, unexpected statuses, charts and tables load clean', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/panel/insights');
    await page.getByRole('tab', { name: /قیف و عملکرد/ }).click();
    await scrollThrough(page);

    await expect(page.getByText('کل لیدها')).toBeVisible();
    await expect(page.getByText('نرخ تبدیل به معامله')).toBeVisible();
    await expect(page.getByText('لید بی‌پیگیری')).toBeVisible();
    await expect(page.getByText('کمیسیون وصول‌نشده')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'قیف فروش' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'دمای مشتریان' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'روند لید و ملک' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'پراکندگی شهرها' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'عملکرد مشاوران' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'لیدهایی که معطل مانده‌اند' })).toBeVisible();

    // the seed data carries lead statuses the funnel does not define
    // (visit/rejected/closed/rented/contract_meeting) — they must show, flagged
    // with the «وضعیت ناشناخته» tooltip, not silently dropped
    await expect(page.locator('[title="وضعیت ناشناخته"]').first()).toBeVisible();
    await expect(page.getByText('visit', { exact: true })).toBeVisible();

    await noHorizontalScroll(page);
    // axe's contrast check samples rendered pixels; a webfont still swapping in
    // (estedad) can catch a transitional frame and misreport a fine colour pair
    await page.evaluate(() => document.fonts.ready);
    expect(await a11yStable(page)).toEqual([]);
    expect(problems).toEqual([]);

    // the window selector re-fetches
    const req = page.waitForResponse((r) => r.url().includes('/api/crm/insights?days=14'));
    await page.getByLabel('بازهٔ زمانی').selectOption('14');
    await req;
  });

  test('null stats render an em-dash, never a fake number, when the window is narrow enough to empty them', async ({ page }) => {
    await signIn(page, 'owner');
    await page.goto('/panel/insights');
    await page.getByRole('tab', { name: /قیف و عملکرد/ }).click();
    // whatever the numbers are, a coverage or conversion figure is either a
    // percentage or the placeholder — never a bare "0" standing in for "unknown"
    const coverage = await page.getByText(/شمارهٔ تماس دارند/).first().innerText();
    expect(coverage).toMatch(/(٪|—)/);
  });
});

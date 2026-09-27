// The landing page at /, its SEO files, the logo gallery and the logo in the
// panel. The brand is read from /api/public/site, never assumed.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

async function site(page) {
  const res = await page.request.get('/api/public/site');
  expect(res.ok()).toBeTruthy();
  return res.json();
}

test('the landing page renders every section on the server, with the brand from settings', async ({ page }) => {
  const problems = watchProblems(page);
  const s = await site(page);
  // Server-rendered: the words are in the HTML before any script runs.
  const html = await (await page.request.get('/')).text();
  const ids = ['top', 'overview', 'features', 'path', 'tour', 'ai', 'ownership', 'security', 'tech', 'contact'];
  for (const id of ids) expect(html).toContain(`id="${id}"`);
  expect(html).toContain(s.brandName);

  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('املاک');
  await expect(page.getByRole('link', { name: 'ورود', exact: true })).toHaveAttribute('href', '/panel/login');
  await expect(page.getByRole('link', { name: s.brandName }).first()).toBeVisible();
  await scrollThrough(page);
  for (const name of ['کار دفتر املاک', 'یک پلتفرم،', 'از آگهی', 'همان چیزی که', 'دستیاری که',
    'این محصول', 'دادهٔ دفتر،', 'معماری', 'بیایید']) {
    await expect(page.getByRole('heading', { level: 2, name: new RegExp(name) })).toBeVisible();
  }
  await expect(page.getByRole('contentinfo')).toContainText(s.brandName);
  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('the features list opens the item under focus and the CTAs lead to the panel', async ({ page }) => {
  await page.goto('/');
  const items = page.locator('#features button[aria-expanded]');
  await expect(items).toHaveCount(4);
  await items.nth(2).click();
  await expect(items.nth(2)).toHaveAttribute('aria-expanded', 'true');
  await expect(items.nth(0)).toHaveAttribute('aria-expanded', 'false');

  await page.locator('#top').getByRole('link', { name: /ورود به پنل/ }).click();
  await expect(page).toHaveURL(/\/panel\/login/);
});

test('the portal CTA shows only while public sign-up is on', async ({ page }) => {
  const status = await (await page.request.get('/api/public/auth/status')).json();
  await page.goto('/');
  const portal = page.locator('#top').getByRole('link', { name: /مشتریان/ });
  await expect(portal).toHaveCount(status.enabled ? 1 : 0);
});

test('the particle scene starts on a WebGL browser, and a reduced-motion visitor gets the still gradient', async ({ page, browser }) => {
  await page.goto('/');
  const canvas = page.getByTestId('nebula');
  await expect(canvas).toHaveCount(1);
  const webgl = await page.evaluate(() => !!document.createElement('canvas').getContext('webgl2'));
  // WebKit without a GPU in CI may have no WebGL: then the gradient is the page.
  if (webgl) await expect(canvas).toHaveAttribute('data-live', '1', { timeout: 15_000 });

  const ctx = await browser.newContext({ reducedMotion: 'reduce', baseURL: test.info().project.use.baseURL });
  const still = await ctx.newPage();
  const problems = watchProblems(still);
  await still.goto('/');
  await still.waitForTimeout(3000);
  await expect(still.getByTestId('nebula')).toHaveAttribute('data-live', '0');
  // the server cannot know the preference: the first render must still hydrate
  expect(problems).toEqual([]);
  await ctx.close();
});

test('metadata: title, canonical, OpenGraph image and JSON-LD come from settings', async ({ page }) => {
  const s = await site(page);
  await page.goto('/');
  await expect(page).toHaveTitle(new RegExp(s.brandName));
  expect(await page.locator('link[rel="canonical"]').getAttribute('href')).toContain(s.domain);
  const og = await page.locator('meta[property="og:image"]').getAttribute('content');
  expect(og).toMatch(/\/og\.png$/);
  expect(await page.locator('meta[property="og:site_name"]').getAttribute('content')).toBe(s.brandName);
  const img = await page.request.get(new URL(og).pathname);
  expect(img.ok()).toBeTruthy();
  expect(img.headers()['content-type']).toContain('image/png');

  const ld = JSON.parse(await page.locator('script[type="application/ld+json"]').textContent());
  const types = ld['@graph'].map((n) => n['@type']);
  expect(types).toEqual(expect.arrayContaining(['SoftwareApplication', 'Organization', 'WebSite', 'FAQPage']));
  expect(ld['@graph'].find((n) => n['@type'] === 'Organization').name).toBe(s.brandName);

  for (const icon of ['link[rel="icon"][type="image/png"]', 'link[rel="apple-touch-icon"]']) {
    const href = await page.locator(icon).first().getAttribute('href');
    // served from /_next/static, which the ingress already sends to the web pod
    expect(href).toMatch(/^\/_next\/static\//);
    expect((await page.request.get(href)).ok()).toBeTruthy();
  }
});

test('robots.txt and sitemap.xml point search engines at the landing page only', async ({ page }) => {
  const s = await site(page);
  const robots = await (await page.request.get('/robots.txt')).text();
  expect(robots).toMatch(/Disallow: \/panel/);
  expect(robots).toMatch(/Disallow: \/brand-preview/);
  expect(robots).toContain(`Sitemap: https://${s.domain}/sitemap.xml`);
  const sitemap = await (await page.request.get('/sitemap.xml')).text();
  expect(sitemap).toContain(`<loc>https://${s.domain}/</loc>`);
  expect(sitemap).not.toContain('/panel');
});

test('the logo gallery shows the three concepts and is not indexed', async ({ page }) => {
  const problems = watchProblems(page);
  const s = await site(page);
  await page.goto('/brand-preview');
  expect(await page.locator('meta[name="robots"]').getAttribute('content')).toMatch(/noindex/);
  await scrollThrough(page);
  for (const letter of ['الف', 'ب', 'ج']) {
    await expect(page.getByRole('heading', { level: 2, name: new RegExp(`^${letter} —`) })).toBeVisible();
  }
  await expect(page.getByText('در حال استفاده')).toHaveCount(1);
  await expect(page.getByText(s.brandName).first()).toBeVisible();
  // every mark's gradients are its own: no two <linearGradient> share an id
  const dup = await page.evaluate(() => {
    const ids = [...document.querySelectorAll('svg linearGradient, svg radialGradient')].map((g) => g.id);
    return ids.length - new Set(ids).size;
  });
  expect(dup).toBe(0);
  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('the panel shell and the login page wear the logo, not a letter', async ({ page }) => {
  const s = await site(page);
  await page.goto('/panel/login');
  await expect(page.locator('main svg[viewBox="0 0 64 64"]').first()).toBeVisible();
  await expect(page.getByText(s.brandName).first()).toBeVisible();
  await signIn(page, 'agent1');
  await page.goto('/panel');
  await expect(page.locator('a[href="/panel"] svg[viewBox="0 0 64 64"]').locator('visible=true').first()).toBeVisible();
});

test('the scroll badge is made of the brand name, follows the page and takes you back up', async ({ page }) => {
  const s = await site(page);
  await page.goto('/');
  const badge = page.getByRole('button', { name: /درصد صفحه پیمایش شده/ });
  await expect(badge).toBeVisible();
  // the ring is the brand's Latin name, never a hard-coded one
  await expect(badge.locator('textPath')).toContainText(s.brandNameLatin.toUpperCase());
  // at the top it reads zero and does nothing
  await expect(badge).toHaveAccessibleName(/^۰ درصد/);
  await expect(badge).toBeDisabled();

  const read = async () => (await badge.getAttribute('aria-label')).match(/^(\S+) درصد/)[1];
  const half = await page.evaluate(() => document.documentElement.scrollHeight / 2);
  await page.evaluate((y) => window.scrollTo(0, y), half);
  await expect.poll(read).not.toBe('۰');
  const middle = await read();

  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await expect.poll(read).toBe('۱۰۰');
  expect(middle).not.toBe('۱۰۰');

  await badge.click();
  await expect.poll(() => page.evaluate(() => window.scrollY), { timeout: 10_000 }).toBeLessThan(10);
});

test('each rail pins itself and slides sideways, and drops the pin for a reduced-motion visitor', async ({ page, browser }) => {
  await page.goto('/');
  for (const id of ['path', 'tour']) {
    const rail = page.locator(`#${id}`);
    const track = rail.locator('.w-max');
    // tall enough to scroll through, and wider than the screen
    const { tall, wide } = await rail.evaluate((el) => ({
      tall: el.offsetHeight > window.innerHeight * 2,
      wide: el.querySelector('.w-max').scrollWidth > window.innerWidth,
    }));
    expect(tall, `#${id} is not tall enough to pin`).toBeTruthy();
    expect(wide, `#${id} has nothing to slide`).toBeTruthy();

    const at = async () => track.evaluate((t) => new DOMMatrixReadOnly(getComputedStyle(t).transform).m41);
    // absolute positions, not scrollIntoView + scrollBy: a section several
    // screens tall counts as "in view" the moment its top edge appears, so a
    // relative scroll lands somewhere different for each rail
    const box = await rail.evaluate((el) => ({ top: el.offsetTop, h: el.offsetHeight }));
    await page.evaluate((y) => window.scrollTo(0, y), box.top + 40);
    await page.waitForTimeout(400);
    const start = await at();
    await page.evaluate((y) => window.scrollTo(0, y), box.top + box.h * 0.5);
    await page.waitForTimeout(400);
    // RTL: the first panel sits at the right, so the row travels to the right
    expect(await at(), `#${id} did not slide the right way`).toBeGreaterThan(start + 50);
    // and the strip stays put while it does
    const top = await rail.locator('.sticky').evaluate((el) => el.getBoundingClientRect().top);
    expect(Math.abs(top)).toBeLessThan(5);
  }

  const ctx = await browser.newContext({ reducedMotion: 'reduce', baseURL: test.info().project.use.baseURL });
  const still = await ctx.newPage();
  await still.goto('/');
  for (const id of ['path', 'tour']) {
    const rail = still.locator(`#${id}`);
    await expect(rail.locator('.sticky')).toHaveCount(0);
    await expect(rail.locator('article')).not.toHaveCount(0);
  }
  await ctx.close();
});

test('the repository link is the one in the settings, and nothing else reaches the page', async ({ page }) => {
  const s = await site(page);
  await page.goto('/');
  const links = page.locator('#tech a[target="_blank"], footer a[target="_blank"]');
  if (!s.github) {
    await expect(links).toHaveCount(0);
    return;
  }
  expect(s.github, 'the backend let through a link that is not https').toMatch(/^https:\/\//);
  for (const a of await links.all()) {
    expect(await a.getAttribute('href')).toBe(s.github);
    // an outbound link must not hand the destination a handle on this window
    expect(await a.getAttribute('rel')).toContain('noopener');
  }
});

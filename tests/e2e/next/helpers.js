// Shared bits for the new panel's specs.
const { expect } = require('@playwright/test');
const { AxeBuilder } = require('@axe-core/playwright');

const PASSWORD = 'local-pass-1234';   // scripts/seed_local.py and e2e_up.sh, local only

/** Sign in through the API; the cookie lands in the page's own context. */
async function signIn(page, username) {
  const res = await page.request.post('/api/session/login', { data: { username, password: PASSWORD } });
  expect(res.ok(), await res.text()).toBeTruthy();
  return res.json();
}

/** Every CSP violation and page error the page reports while `fn` runs. */
function watchProblems(page) {
  const problems = [];
  page.on('console', (m) => {
    // E2E_DEV=1: specs pointed at `next dev`, whose own injected styles the
    // strict CSP refuses; the production build the suite normally runs has none.
    if (process.env.E2E_DEV && /Refused to apply inline style|Download the React DevTools|\[HMR\]|\[Fast Refresh\]/.test(m.text())) return;
    if (m.type() === 'error' || /Content Security Policy/i.test(m.text())) problems.push(m.text());
  });
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  return problems;
}

async function noHorizontalScroll(page) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(over, 'the page scrolls sideways').toBeLessThanOrEqual(0);
}

/** Scroll the whole page so every reveal-on-view section has rendered. */
async function scrollThrough(page) {
  const h = await page.evaluate(() => document.documentElement.scrollHeight);
  for (let y = 0; y <= h; y += 500) {
    await page.evaluate((top) => window.scrollTo(0, top), y);
    await page.waitForTimeout(60);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
}

/** Polls (not page.waitForFunction — an async predicate there is truthy as
 * soon as it returns a Promise, not once that promise resolves, so it never
 * actually waits) until public/sw.js has written `url` into `cacheName`. */
async function waitForServiceWorkerCache(page, cacheName, url, timeoutMs = 15_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const hit = await page.evaluate(
      async ([name, u]) => {
        const c = await caches.open(name);
        return (await c.match(u)) !== undefined;
      },
      [cacheName, url],
    );
    if (hit) return;
    await page.waitForTimeout(150);
  }
  throw new Error(`${url} never appeared in cache "${cacheName}" within ${timeoutMs}ms`);
}

/**
 * Close every toast on screen. toaster.tsx's viewport gives up pointer
 * events over its own empty margin, but the toast card itself keeps them
 * (so its own close button still works) — a row that a create/edit toast
 * happens to sit over (a short phone viewport, a list already carrying
 * other rows from earlier in the same run) is still genuinely covered for
 * the toast's whole 5s duration. Closing it outright, right after the
 * toast's own text was asserted, is deterministic; waiting for it to time
 * itself out is not worth the risk.
 */
async function closeToasts(page) {
  const closeButtons = page.getByRole('button', { name: 'بستن' });
  const n = await closeButtons.count();
  for (let i = 0; i < n; i++) {
    await closeButtons.first().click().catch(() => {});
  }
}

/**
 * Wait out every finite, running entrance animation before axe measures the
 * page. Every panel section fades/lifts in with Reveal/AnimatedRow
 * (motion/react, viz.tsx) the first time it's on screen; axe sampling that
 * instant reads a still-transparent element as low-contrast — the same race
 * the old panel's suite hit on the landing page (tests/e2e/fixtures/a11y.js,
 * "axe measures the page after its entrance animations"). Only animations
 * that end are awaited; a looping one would never resolve.
 *
 * A group of cards each stagger their own Reveal by a growing `delay`
 * (report-tab.tsx's stat cards: 0, 0.04s, ..., 0.2s); a later card's
 * animation is not always a document.getAnimations() entry yet on the very
 * first read — one settle pass can finish before it is even constructed.
 * Keep settling until two reads in a row find nothing left running.
 */
async function settleAnimations(page) {
  await page.evaluate(async () => {
    const finite = () => document.getAnimations()
      .filter((a) => a.effect && a.effect.getComputedTiming().endTime !== Infinity);
    for (let clean = 0, round = 0; clean < 2 && round < 20; round++) {
      const anims = finite();
      await Promise.all(anims.map((a) => a.finished.catch(() => null)));
      clean = anims.length ? 0 : clean + 1;
      await new Promise((resolve) => setTimeout(resolve, 120));
    }

    // …and then the springs, which the loop above cannot see. `motion`'s
    // useSpring drives its value from requestAnimationFrame and writes the
    // transform directly, so it never appears in getAnimations(). Tilt uses
    // one on every card. axe reading a card mid-spring cannot work out what
    // colour the text is over — a transformed ancestor defeats its background
    // walk — and it reports that as a colour-contrast violation, on text that
    // is nowhere near failing. That is where this suite's "dark theme passes
    // axe" flakes came from: same CSS, same colours, a pass or a fail
    // depending on the frame axe happened to read.
    const tilted = () => [...document.querySelectorAll('.group\\/tilt, [style*="transform"]')];
    const snapshot = () => tilted().map((el) => getComputedStyle(el).transform).join("|");
    for (let same = 0, round = 0; same < 3 && round < 40; round++) {
      const before = snapshot();
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      same = snapshot() === before ? same + 1 : 0;
    }
  });
}

/** Serious and critical WCAG 2 A/AA violations; the new panel has no baseline. */
// `exclude`: CSS selectors axe must not descend into. `legacy`: forces
// axe-core-playwright's pre-runPartial mode. Both exist for the
// sandbox="allow-same-origin" iframe with no allow-scripts on /panel/email
// (email-view.tsx's template preview — deliberately script-less, since it
// can carry admin-authored template HTML): the default mode establishes an
// isolated world per frame via CDP to inject axe into it, and that fails on
// this one hard enough to take the whole run down (Target.createTarget /
// "already closed", https://github.com/dequelabs/axe-core-npm/blob/develop/packages/playwright/error-handling.md)
// — exclude() alone still throws the same way, since axe still has to reach
// the frame first to know it's excluded. Legacy mode runs axe only in the
// top frame (via window.postMessage to any same-origin frame instead of a
// CDP isolated world per frame), which sidesteps that path entirely.
async function a11y(page, { exclude = [], legacy = false } = {}) {
  await settleAnimations(page);
  const builder = new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']);
  for (const selector of exclude) builder.exclude(selector);
  if (legacy) builder.setLegacyMode(true);
  const r = await builder.analyze();
  return r.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.help} @ ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
}

module.exports = {
  PASSWORD, signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y,
  waitForServiceWorkerCache, closeToasts, settleAnimations,
};

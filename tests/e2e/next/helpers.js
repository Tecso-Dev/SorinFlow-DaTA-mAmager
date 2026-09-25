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
  });
}

/** Serious and critical WCAG 2 A/AA violations; the new panel has no baseline. */
async function a11y(page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  return r.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.help} @ ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
}

module.exports = { PASSWORD, signIn, watchProblems, noHorizontalScroll, scrollThrough, closeToasts, settleAnimations, a11y };

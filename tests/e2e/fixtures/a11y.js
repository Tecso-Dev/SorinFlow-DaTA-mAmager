// Runs axe-core (WCAG 2A + 2AA) against the current page and turns the
// result into a list of *new* critical/serious violations — ones not already
// recorded in the committed baseline (../a11y-baseline.json). The gate this
// backs is "no new serious/critical issues", not "zero issues": panel pages
// built over several phases carry some pre-existing ones, and baselining
// them here beats disabling the check.
const fs = require('fs');
const path = require('path');
const { AxeBuilder } = require('@axe-core/playwright');

const BASELINE_PATH = path.join(__dirname, '..', 'a11y-baseline.json');
const baseline = JSON.parse(fs.readFileSync(BASELINE_PATH, 'utf8'));

function isBaselined(pageKey, ruleId, target) {
  return baseline.some(b => b.page === pageKey && b.id === ruleId && b.target === target);
}

/**
 * @param {import('@playwright/test').Page} page
 * @param {string} pageKey - human-readable label matching a baseline "page" entry
 * @returns {Promise<string[]>} one line per new critical/serious violation; empty means clean
 */
async function checkA11y(page, pageKey) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze();

  const fresh = [];
  for (const violation of results.violations) {
    if (violation.impact !== 'critical' && violation.impact !== 'serious') continue;
    for (const node of violation.nodes) {
      const target = node.target.join(' ');
      if (isBaselined(pageKey, violation.id, target)) continue;
      fresh.push(`[${violation.impact}] ${violation.id} on "${pageKey}" at ${target} — ${violation.help}`);
    }
  }
  return fresh;
}

module.exports = { checkA11y };

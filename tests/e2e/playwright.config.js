// Playwright config for the SorinFlow panel's e2e + a11y suite.
//
// Chromium only: the panel is only ever opened from Chromium-family browsers
// in practice, and the Python side (app/scraper/stealth.py) is pinned to
// Chromium too — see package.json's @playwright/test version comment.
const path = require('path');
const { defineConfig, devices } = require('@playwright/test');

// A server already running (started by hand, or by a previous webServer)
// answers here instead of us spawning our own — set E2E_BASE_URL to point
// at one, e.g. while iterating on a single spec.
const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:8111';

module.exports = defineConfig({
  testDir: './specs',
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  // Skipped entirely when E2E_BASE_URL is set: something else is already
  // serving, and starting a second app on the same port would just fail.
  webServer: process.env.E2E_BASE_URL ? undefined : {
    command: 'bash scripts/e2e_up.sh',
    cwd: path.resolve(__dirname, '..', '..'),
    url: `${baseURL}/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});

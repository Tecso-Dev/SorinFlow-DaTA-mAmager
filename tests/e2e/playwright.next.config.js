// Playwright config for the new panel (frontend-next): end-to-end and axe
// checks on desktop, an Android phone and an iPhone.
//
// Two servers: the backend exactly as the old panel's suite runs it
// (scripts/e2e_up.sh, :8111), and a production build of frontend-next that
// forwards /api to it (scripts/e2e_next_up.sh, :3111). A production build,
// not the dev server, because the strict CSP is only strict there.
//
// The iPhone project runs on WebKit when E2E_WEBKIT=1 (CI installs it), on
// Chromium with the iPhone's screen, touch and user agent otherwise.
// PW_CHROMIUM points at a Chromium already on the machine instead of the one
// Playwright would download.
const path = require('path');
const { defineConfig, devices } = require('@playwright/test');

const root = path.resolve(__dirname, '..', '..');
// The same ports the two start scripts use, so a second checkout on this Mac
// can run the suite beside the first (E2E_PORT=8121 E2E_NEXT_PORT=3121 ...).
const api = process.env.E2E_API_URL || `http://127.0.0.1:${process.env.E2E_PORT || '8111'}`;
const baseURL = process.env.E2E_NEXT_URL || `http://127.0.0.1:${process.env.E2E_NEXT_PORT || '3111'}`;
const launchOptions = process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {};
const iphone = devices['iPhone 13'];

module.exports = defineConfig({
  testDir: './next',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report-next' }]],
  use: { baseURL, trace: 'on-first-retry', locale: 'fa-IR' },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, launchOptions } },
    { name: 'android', use: { ...devices['Pixel 7'], launchOptions } },
    process.env.E2E_WEBKIT
      ? { name: 'iphone', use: { ...iphone } }
      : { name: 'iphone', use: { ...iphone, browserName: 'chromium', defaultBrowserType: 'chromium', launchOptions } },
  ],
  webServer: process.env.E2E_NEXT_URL ? undefined : [
    {
      command: 'bash scripts/e2e_up.sh',
      cwd: root,
      url: `${api}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
    },
    {
      command: 'bash scripts/e2e_next_up.sh',
      cwd: root,
      url: `${baseURL}/panel/login`,
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
    },
  ],
});

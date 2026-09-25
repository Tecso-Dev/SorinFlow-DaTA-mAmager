// The new panel's login: the session is an httpOnly cookie, nothing lands
// in localStorage, a wrong password says so, and logout ends the session.
const { test, expect } = require('@playwright/test');
const { PASSWORD, watchProblems, noHorizontalScroll, a11y } = require('./helpers');

test('a page of the panel without a session goes to the login page and comes back after it', async ({ page }) => {
  const problems = watchProblems(page);
  await page.goto('/panel/crm/leads');
  await expect(page).toHaveURL(/\/panel\/login\?next=%2Fpanel%2Fcrm%2Fleads/);
  await expect(page.getByRole('heading', { name: 'ورود به پنل' })).toBeVisible();
  await noHorizontalScroll(page);
  expect(await a11y(page)).toEqual([]);

  await page.getByLabel(/نام کاربری/).fill('agent1');
  await page.getByLabel(/رمز/).first().fill('wrong-password');
  await page.getByRole('button', { name: /^ورود$/ }).click();
  await expect(page.getByText('نام کاربری یا رمز عبور اشتباه است')).toBeVisible();

  await page.getByLabel(/رمز/).first().fill(PASSWORD);
  await page.getByRole('button', { name: /^ورود$/ }).click();
  await expect(page).toHaveURL(/\/panel\/crm\/leads$/);

  const cookies = await page.context().cookies();
  const session = cookies.find((c) => c.name === 'sf_session');
  expect(session && session.httpOnly).toBeTruthy();
  expect(cookies.find((c) => c.name === 'sf_csrf')?.httpOnly).toBe(false);
  expect(await page.evaluate(() => JSON.stringify(window.localStorage))).not.toMatch(/token|eyJ/);
  // the wrong password's 401 is the browser logging a refused request, not a fault
  expect(problems.filter((p) => !/status of 401/.test(p))).toEqual([]);
});

test('logout ends the session', async ({ page }) => {
  await page.goto('/panel/login');
  await page.getByLabel(/نام کاربری/).fill('agent2');
  await page.getByLabel(/رمز/).first().fill(PASSWORD);
  await page.getByRole('button', { name: /^ورود$/ }).click();
  await expect(page).toHaveURL(/\/panel$/);
  await page.getByRole('button', { name: 'منوی حساب' }).click();
  await page.getByRole('menuitem', { name: /خروج/ }).click();
  await expect(page).toHaveURL(/\/panel\/login/);
  expect((await page.request.get('/api/session')).status()).toBe(401);
});

test('a state-changing call without the CSRF header is refused', async ({ page }) => {
  await page.request.post('/api/session/login', { data: { username: 'agent1', password: PASSWORD } });
  const forged = await page.request.patch('/api/users/me', { data: { presence: 'busy' } });
  expect(forged.status()).toBe(403);
});

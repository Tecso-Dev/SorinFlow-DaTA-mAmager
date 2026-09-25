// Logs a page in without ever typing a password into the UI.
//
// frontend/js/app.js's getToken() reads localStorage.sf_token (and
// sessionStorage) directly — see setToken()/getToken() near the top of that
// file. So the whole login form can be skipped: get a token from
// POST /api/users/token (the OAuth2 password form app/api/routes/users.py's
// login() expects) and seed it before the page's first script runs.
const SEEDED_PASSWORD = 'local-pass-1234';

// role -> seeded username. manager1/agent1/agent2 come from
// scripts/seed_local.py; root/owner are app/database.py:init_db's own
// boot-time seed. None of the five has TOTP or email-2FA enabled, so /token
// returns a full access_token straight away — no second factor to solve.
const USERS = {
  root: 'root',
  owner: 'owner',
  manager: 'manager1',
  agent: 'agent1',
  agent2: 'agent2',
};

/**
 * Log `page` in as a seeded role before its first navigation.
 * `request` is Playwright's built-in fixture — already pointed at baseURL.
 */
async function loginAs(page, request, role) {
  const username = USERS[role] || role;
  const res = await request.post('/api/users/token', {
    form: { username, password: SEEDED_PASSWORD },
  });
  if (!res.ok()) {
    throw new Error(`login failed for ${username}: ${res.status()} ${await res.text()}`);
  }
  const body = await res.json();
  if (!body.access_token) {
    throw new Error(`login for ${username} did not return a token: ${JSON.stringify(body)}`);
  }
  await page.addInitScript(token => window.localStorage.setItem('sf_token', token), body.access_token);
  return username;
}

module.exports = { loginAs, USERS, SEEDED_PASSWORD };

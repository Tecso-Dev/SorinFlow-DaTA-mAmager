/**
 * SorinFlow Divar Scraper - Dashboard JavaScript
 */

const API_BASE = '/api';

// The chart library takes a font name as a string, not a CSS value, so it
// cannot use var(--font-fa) directly. Reading the computed variable keeps it
// on the same face as the rest of the panel — including after a font swap.
const FA_FONT = (() => {
    try {
        const v = getComputedStyle(document.documentElement)
            .getPropertyValue('--font-fa').trim();
        // Chart.js wants a bare family name; take the first entry, unquoted.
        return (v.split(',')[0] || '').replace(/['"]/g, '').trim() || 'Vazirmatn';
    } catch (_) { return 'Vazirmatn'; }
})();

// One default rather than a `family` on every chart: the live chart was the
// one of five that forgot, and forgetting is silent — canvas text cannot
// inherit from CSS, so it just paints in Helvetica. Guarded because Chart
// comes from a CDN and an unguarded reference would take the panel down with
// it if that request fails.
if (window.Chart) Chart.defaults.font.family = FA_FONT;

let currentPage = 1;
let cityChart = null;
let trendChart = null;
let loginPhoneNumber = '';
let cookieStatus = { is_valid: false, has_cookies: false };
let pendingScrapingAction = null;
let _leadsDateFrom = '';   // Gregorian "YYYY-MM-DD"
let _leadsDateTo   = '';   // Gregorian "YYYY-MM-DD"

// ═══ Auth state ═══════════════════════════════════════════════
let _authToken = null;
let _currentUser = null; // { username, role, full_name }
let _totpSession = null; // temporary session token for TOTP step 2

// Where the session lives is the user's choice, not ours.
//
// "مرا به خاطر بسپار" ticked -> localStorage, and the session survives closing
// the browser. Unticked -> sessionStorage, and it dies with the tab. That
// distinction matters on a shared or public machine, and offering it is what
// lets someone safely say no.
function _tokenStore() {
    try {
        return localStorage.getItem('sf_remember') === '0' ? sessionStorage : localStorage;
    } catch (_) { return localStorage; }
}
function getToken() {
    try { return sessionStorage.getItem('sf_token') || localStorage.getItem('sf_token'); }
    catch (_) { return localStorage.getItem('sf_token'); }
}
function setToken(t) { _tokenStore().setItem('sf_token', t); _authToken = t; }
function clearToken() {
    try { sessionStorage.removeItem('sf_token'); } catch (_) {}
    localStorage.removeItem('sf_token');
    _authToken = null; _currentUser = null; _totpSession = null;
}

// ═══ PWA — the panel as an app ══════════════════════════════════
// The service worker caches the shell (never /api/); the manifest makes the
// panel installable. On Android/Chrome the browser hands us an install
// prompt; iOS has none, so the hint says where the button is.
let _pwaPrompt = null;

function _pwaStandalone() {
    return (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) || window.navigator.standalone === true;
}

function _pwaMaybeHint() {
    const box = document.getElementById('pwa-hint');
    if (!box || _pwaStandalone()) return;
    let dismissed = false;
    try { dismissed = localStorage.getItem('sf_pwa_dismissed') === '1'; } catch (_) {}
    if (dismissed) return;
    const ua = navigator.userAgent || '';
    const ios = /iPhone|iPad|iPod/.test(ua) && !window.MSStream;
    const phone = ios || /Android/.test(ua);
    if (!phone) return;
    box.classList.remove('d-none');
    if (ios) {
        document.getElementById('pwa-install-btn').classList.add('d-none');
        document.getElementById('pwa-ios').classList.remove('d-none');
    } else if (!_pwaPrompt) {
        // no prompt yet (or never — some Android browsers): hide the button, keep the card
        document.getElementById('pwa-install-btn').classList.add('d-none');
    }
}

async function pwaInstall() {
    if (!_pwaPrompt) return;
    _pwaPrompt.prompt();
    try {
        const { outcome } = await _pwaPrompt.userChoice;
        if (outcome === 'accepted') { showToast('نصب شد', 'برنامه روی صفحهٔ اصلی گوشی است', 'success'); pwaDismiss(); }
    } catch (_) {}
    _pwaPrompt = null;
}

function pwaDismiss() {
    try { localStorage.setItem('sf_pwa_dismissed', '1'); } catch (_) {}
    document.getElementById('pwa-hint')?.classList.add('d-none');
}

window.addEventListener('beforeinstallprompt', e => {
    e.preventDefault();
    _pwaPrompt = e;
    const btn = document.getElementById('pwa-install-btn');
    if (btn) btn.classList.remove('d-none');
    _pwaMaybeHint();
});
window.addEventListener('appinstalled', () => pwaDismiss());

if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        // relative, so the scope is /dashboard/ wherever the panel is mounted
        navigator.serviceWorker.register('sw.js').catch(() => { /* the panel works without it */ });
    });
}

// ═══ Theme (dark / light) ═════════════════════════════════════
// data-theme is set on <html> before first paint by an inline
// script in index.html; persisted in localStorage "sf-theme".
function currentTheme() { return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'; }

function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    // the three declarations that have to agree, or some device paints half
    // the page in the other theme: ours, Bootstrap's, and the browser's
    document.documentElement.setAttribute('data-bs-theme', theme);
    document.getElementById('meta-color-scheme')?.setAttribute('content', theme === 'light' ? 'only light' : 'dark');
    localStorage.setItem('sf-theme', theme);
    // sun shown in dark mode (click → light), moon in light mode
    document.querySelectorAll('.theme-icon').forEach(el => {
        el.className = 'bi theme-icon ' + (theme === 'light' ? 'bi-moon-stars' : 'bi-sun');
    });
    refreshChartTheme();
}

function toggleTheme() { applyTheme(currentTheme() === 'light' ? 'dark' : 'light'); }

// Chart.js colors depend on the active theme
function chartColors() {
    const light = currentTheme() === 'light';
    return {
        text:    light ? '#475569' : '#94a3b8',
        tick:    light ? '#64748b' : '#64748b',
        grid:    light ? 'rgba(15,23,42,.08)'  : 'rgba(255,255,255,0.04)',
        surface: light ? '#ffffff' : '#0a0a0c',
    };
}

// Re-color already-rendered charts after a theme switch
function refreshChartTheme() {
    const c = chartColors();
    if (cityChart) {
        cityChart.options.plugins.legend.labels.color = c.text;
        cityChart.data.datasets[0].borderColor = c.surface;
        cityChart.update();
    }
    if (trendChart) {
        trendChart.options.scales.x.grid.color = 'transparent';
        trendChart.options.scales.y.grid.color = c.grid;
        trendChart.options.scales.x.ticks.color = c.tick;
        trendChart.options.scales.y.ticks.color = c.tick;
        trendChart.data.datasets[0].pointBorderColor = c.surface;
        trendChart.update();
    }
    if (window._crmDealsChart) {
        window._crmDealsChart.options.plugins.legend.labels.color = c.text;
        window._crmDealsChart.update();
    }
    if (window._crmContactsChart) {
        window._crmContactsChart.options.scales.x.ticks.color = c.text;
        window._crmContactsChart.options.scales.y.ticks.color = c.text;
        window._crmContactsChart.update();
    }
}

document.addEventListener('DOMContentLoaded', () => applyTheme(currentTheme()));

// ═══ Stacked modals ═══════════════════════════════════════════════════
//
// Bootstrap gives every modal the same z-index, so a modal opened from
// inside another one — «ملک‌های مشابه» from the lead's details — painted
// BEHIND it whenever it came earlier in the document. Each newly shown modal
// goes above the ones already open, its backdrop just under it; and closing
// the inner one keeps the page locked while the outer one is still up.
document.addEventListener('show.bs.modal', e => {
    const open = document.querySelectorAll('.modal.show').length;
    if (!open) { e.target.style.zIndex = ''; return; }
    e.target.style.zIndex = String(1055 + 10 * open);
});
document.addEventListener('shown.bs.modal', e => {
    const z = parseInt(e.target.style.zIndex, 10);
    if (!z) return;
    const backdrops = document.querySelectorAll('.modal-backdrop');
    const last = backdrops[backdrops.length - 1];
    if (last) last.style.zIndex = String(z - 5);
});
document.addEventListener('hidden.bs.modal', e => {
    e.target.style.zIndex = '';
    if (document.querySelector('.modal.show')) document.body.classList.add('modal-open');
});

// ═══ Login / Logout ═══════════════════════════════════════════
async function doLogin() {
    const uEl = document.getElementById('login-username');
    const pEl = document.getElementById('login-password');
    const username = uEl.value.trim();
    const password = pEl.value;
    const btn = document.getElementById('login-btn');

    clearAuthErrors();

    // Validate at the field, and stop at the first empty one so focus lands
    // somewhere useful rather than on a summary the user must then decode.
    if (!username) {
        setFieldError('username', 'نام کاربری را وارد کنید'); uEl.focus(); return;
    }
    if (!password) {
        setFieldError('password', 'رمز عبور را وارد کنید'); pEl.focus(); return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> در حال ورود…';

    try {
        const form = new URLSearchParams();
        form.append('username', username);
        form.append('password', password);

        const resp = await fetch(`${API_BASE}/users/token`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: form.toString()
        });

        let data = {};
        try { data = await resp.json(); } catch (_) { data = {}; }

        if (!resp.ok) {
            _explainLoginFailure(resp.status, data.detail, pEl);
            return;
        }

        if (data.requires_email_code) {
            _emailSession = data.email_session;
            document.getElementById('login-email-hint').textContent = data.email_hint || '';
            document.getElementById('login-step-1').classList.add('d-none');
            document.getElementById('login-step-email').classList.remove('d-none');
            document.getElementById('login-email-code').focus();
            return;
        }

        if (data.requires_totp) {
            _totpSession = data.totp_session;
            document.getElementById('login-step-1').classList.add('d-none');
            document.getElementById('login-step-2').classList.remove('d-none');
            document.getElementById('login-totp-code').focus();
            return;
        }

        await _finishLogin(data);

    } catch (err) {
        // A network failure is not the user's fault and must not read like it.
        showAuthAlert('ارتباط با سرور برقرار نشد.',
                      'اتصال اینترنت خود را بررسی کنید و دوباره تلاش کنید. اطلاعات واردشده حفظ شده است.');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-box-arrow-in-right"></i> ورود به داشبورد';
    }
}

/** Map a failed sign-in onto a cause and a next step.
 *
 * The guide's error→recovery mapping: a bare "خطا در ورود" tells someone
 * nothing about what to try. Note the deliberate refusal to distinguish a
 * wrong username from a wrong password — saying which was wrong tells an
 * anonymous caller that the account exists.
 *
 * Inputs are never cleared. Retyping a whole form because one field was wrong
 * is the most irritating thing a login can do.
 */
function _explainLoginFailure(status, detail, passwordEl) {
    _loginFailures = (_loginFailures || 0) + 1;

    if (status === 429) {
        showAuthAlert(detail || 'تلاش‌های ناموفق بیش از حد مجاز بوده است.',
                      'برای امنیت حساب، ورود موقتاً محدود شده است. کمی صبر کنید و دوباره تلاش کنید.',
                      'warn');
        return;
    }
    if (status === 403) {
        showAuthAlert(detail || 'این حساب غیرفعال است.',
                      'برای فعال‌سازی با مدیر سیستم تماس بگیرید.', 'warn');
        return;
    }
    if (status >= 500) {
        showAuthAlert('سرور در حال حاضر پاسخ نمی‌دهد.',
                      'چند لحظه دیگر دوباره تلاش کنید. اطلاعات واردشده حفظ شده است.');
        return;
    }

    setFieldError('password', 'نام کاربری یا رمز عبور نادرست است');
    passwordEl.focus();
    passwordEl.select();

    // After a second failure the likeliest problem is no longer a typo, so
    // surface the way out instead of repeating the same red text.
    if (_loginFailures >= 2) {
        showAuthAlert('چند بار ورود ناموفق بوده است.',
                      'اگر رمز خود را به یاد ندارید، مدیر سیستم می‌تواند آن را بازنشانی کند. کلید Caps Lock را هم بررسی کنید.',
                      'warn');
    }
}

let _loginFailures = 0;

async function verifyTotpLogin() {
    const el = document.getElementById('login-totp-code');
    const code = el.value.trim();
    const btn = document.getElementById('login-totp-btn');

    clearAuthErrors();
    if (code.length !== 6) {
        setFieldError('totp', 'کد ۶ رقمی را کامل وارد کنید');
        el.focus();
        return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> در حال تأیید…';

    try {
        const resp = await fetch(`${API_BASE}/users/token/verify-totp`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ totp_session: _totpSession, code })
        });
        let data = {}; try { data = await resp.json(); } catch (_) {}

        if (!resp.ok) {
            // A TOTP code is time-based, so "wrong" and "expired" look the same
            // from here — say both, and name the clock, which is the usual cause.
            setFieldError('totp', 'این کد نادرست است یا منقضی شده');
            showAuthAlert('کد پذیرفته نشد.',
                          'کد هر ۳۰ ثانیه عوض می‌شود — کد تازه را وارد کنید. اگر باز هم نشد، ساعت گوشی شما باید با زمان واقعی هماهنگ باشد.',
                          'warn');
            el.value = ''; el.focus();
            return;
        }
        await _finishLogin(data);
    } catch (err) {
        showAuthAlert('ارتباط با سرور برقرار نشد.', 'چند لحظه دیگر دوباره تلاش کنید.');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-check-circle"></i> تأیید و ورود';
    }
}

function backToLogin() {
    _totpSession = null;
    _emailSession = null;
    // Every step that is not step 1, or «بازگشت» from the newer ones would
    // leave two forms on screen at once.
    ['login-step-2', 'login-step-email', 'login-step-reset']
        .forEach(id => document.getElementById(id)?.classList.add('d-none'));
    document.getElementById('login-step-1').classList.remove('d-none');
    ['login-totp-code', 'login-email-code', 'reset-code', 'reset-password']
        .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    document.getElementById('reset-code-wrap')?.classList.add('d-none');
    // The reset form's mode is read back off reset-code-wrap, so hiding it
    // returns the form to step 1 — but the labels submitPasswordReset changed
    // stay on step 2. That combination asks for a code, shows no field to type
    // one into, and re-sends a code when pressed.
    const rh = document.getElementById('reset-help');
    if (rh) rh.textContent = 'نام کاربری یا ایمیل خود را بنویسید تا کد بازنشانی برایتان ایمیل شود';
    const rb = document.getElementById('reset-btn-label');
    if (rb) rb.textContent = 'ارسال کد بازنشانی';
    document.getElementById('login-error').classList.add('d-none');
}

// ── the emailed second factor ───────────────────────────────────────────────
//
// TOTP asks for an app, a scan, a phone whose clock is right, and an entry
// nobody deletes by accident — and when any of that fails there is no way back
// in but the database. This costs a round trip and recovers itself.
let _emailSession = null;

async function verifyEmailLogin() {
    const el = document.getElementById('login-email-code');
    const code = (el.value || '').trim();
    clearAuthErrors();
    if (!/^\d{4,8}$/.test(code)) {
        setFieldError('emailcode', 'کد ۶ رقمی را وارد کنید');
        return;
    }
    const btn = document.getElementById('login-email-btn');
    btn.disabled = true;
    try {
        const resp = await fetch(`${API_BASE}/users/token/verify-email`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email_session: _emailSession, code }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            setFieldError('emailcode', _detailText(data.detail) || 'کد نادرست است');
            el.select();
            return;
        }
        await _finishLogin(data);
    } catch (err) {
        showAuthAlert('ارتباط با سرور برقرار نشد.', 'اتصال اینترنت را بررسی کنید.', 'warn');
    } finally {
        btn.disabled = false;
    }
}

// ── forgotten password ──────────────────────────────────────────────────────

function showResetForm() {
    ['login-step-1', 'login-step-2', 'login-step-email']
        .forEach(id => document.getElementById(id)?.classList.add('d-none'));
    document.getElementById('login-step-reset').classList.remove('d-none');
    document.getElementById('login-error').classList.add('d-none');
    const u = document.getElementById('login-username');
    const r = document.getElementById('reset-identifier');
    if (u && r && u.value) r.value = u.value;   // carry over what they typed
    r?.focus();
}

async function submitPasswordReset() {
    const idEl = document.getElementById('reset-identifier');
    const codeWrap = document.getElementById('reset-code-wrap');
    const btn = document.getElementById('reset-btn');
    const asking = codeWrap.classList.contains('d-none');
    clearAuthErrors();

    const identifier = (idEl.value || '').trim();
    if (!identifier) { setFieldError('reset-id', 'نام کاربری یا ایمیل را بنویسید'); return; }

    btn.disabled = true;
    try {
        if (asking) {
            const resp = await fetch(`${API_BASE}/users/password-reset/request`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ identifier }),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) { setFieldError('reset-id', _detailText(data.detail) || 'خطا'); return; }
            // The answer is deliberately the same whether or not the account
            // exists — see the endpoint. So the UI says the same thing too.
            document.getElementById('reset-help').textContent =
                'اگر این حساب وجود داشته باشد، کد بازنشانی ایمیل شد. کد و رمز تازه را وارد کنید';
            codeWrap.classList.remove('d-none');
            document.getElementById('reset-btn-label').textContent = 'تغییر رمز عبور';
            document.getElementById('reset-code').focus();
            return;
        }

        const code = (document.getElementById('reset-code').value || '').trim();
        const pw = document.getElementById('reset-password').value || '';
        if (!/^\d{4,8}$/.test(code)) { setFieldError('reset-code', 'کد ۶ رقمی را وارد کنید'); return; }
        if (pw.length < 8) { setFieldError('reset-pass', 'حداقل ۸ نویسه'); return; }

        const resp = await fetch(`${API_BASE}/users/password-reset/confirm`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ identifier, code, new_password: pw }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) { setFieldError('reset-code', _detailText(data.detail) || 'کد نادرست است'); return; }
        backToLogin();
        showAuthAlert('رمز عبور تغییر کرد.', 'حالا با رمز تازه وارد شوید.', 'ok');
    } catch (err) {
        showAuthAlert('ارتباط با سرور برقرار نشد.', 'اتصال اینترنت را بررسی کنید.', 'warn');
    } finally {
        btn.disabled = false;
    }
}

function showRegisterForm() {
    document.getElementById('login-step-1').classList.add('d-none');
    document.getElementById('login-step-2').classList.add('d-none');
    document.getElementById('login-step-register').classList.remove('d-none');
    document.getElementById('login-toggle-link').style.display = 'none';
    document.getElementById('register-toggle-link').style.display = 'block';
    document.getElementById('login-error').classList.add('d-none');
}

function showLoginForm() {
    document.getElementById('login-step-register').classList.add('d-none');
    document.getElementById('login-step-1').classList.remove('d-none');
    document.getElementById('register-toggle-link').style.display = 'none';
    document.getElementById('login-toggle-link').style.display = 'block';
    document.getElementById('login-error').classList.add('d-none');
}

async function doRegister() {
    const f = id => document.getElementById(id);
    const username    = f('reg-username').value.trim();
    const full_name   = f('reg-fullname').value.trim();
    const divar_phone = f('reg-divar-phone').value.trim() || null;
    const password    = f('reg-password').value;
    const password2   = f('reg-password2').value;
    const btn         = f('reg-btn');

    clearAuthErrors();

    // Each failure lands on its own field and focuses it, so the fix is where
    // the eye already is rather than in a banner at the top of the form.
    const fail = (field, msg) => {
        setFieldError(field, msg);
        const el = document.querySelector(`#f-${field} input`);
        if (el) el.focus();
        return false;
    };

    if (username.length < 3) return fail('reg-username', 'نام کاربری باید حداقل ۳ کاراکتر باشد');
    if (!/^[A-Za-z0-9._-]+$/.test(username))
        return fail('reg-username', 'فقط حروف انگلیسی، عدد، نقطه، خط تیره و زیرخط مجاز است');
    if (full_name.length < 2) return fail('reg-fullname', 'نام و نام خانوادگی را وارد کنید');
    if (divar_phone && !/^09\d{9}$/.test(divar_phone))
        return fail('reg-divar-phone', 'شماره باید با فرمت ۰۹۱۲۳۴۵۶۷۸۹ باشد');

    const rules = _pwScore(password);
    if (!rules.len) return fail('reg-password', 'رمز عبور باید حداقل ۸ کاراکتر باشد');
    if (Object.values(rules).filter(Boolean).length < 3)
        return fail('reg-password', 'رمز عبور ضعیف است — شرط‌های زیر را کامل کنید');
    if (password !== password2)
        return fail('reg-password2', 'رمزهای عبور یکسان نیستند');

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> در حال ساخت حساب…';

    try {
        const resp = await fetch(`${API_BASE}/users/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, full_name: full_name || null, divar_phone, password })
        });
        let data = {}; try { data = await resp.json(); } catch (_) {}

        if (!resp.ok) {
            const detail = _detailText(data.detail);
            // "Account exists" is not a dead end — it is a sign-in, prefilled.
            if (resp.status === 400 && /تکرار|قبلا|قبلاً|exists/i.test(detail)) {
                setFieldError('reg-username', detail || 'این نام کاربری قبلاً ثبت شده است');
                showAuthAlert('به نظر می‌رسد این حساب از قبل وجود دارد.',
                              'اگر حساب شماست، وارد شوید. نام کاربری برایتان پر شده است.', 'warn');
                const goto = document.getElementById('login-username');
                if (goto) goto.value = username;
                return;
            }
            showAuthAlert(detail || 'ساخت حساب انجام نشد.',
                          'اطلاعات واردشده حفظ شده است. دوباره تلاش کنید یا با مدیر سیستم تماس بگیرید.');
            return;
        }

        // Straight in — asking someone to log in with credentials they typed
        // ten seconds ago is friction with no purpose.
        const form = new URLSearchParams();
        form.append('username', username);
        form.append('password', password);
        const loginResp = await fetch(`${API_BASE}/users/token`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: form.toString()
        });
        let loginData = {}; try { loginData = await loginResp.json(); } catch (_) {}
        if (!loginResp.ok) {
            showAuthAlert('حساب ساخته شد.', 'اکنون با همان مشخصات وارد شوید.', 'ok');
            showLoginForm();
            const u = document.getElementById('login-username');
            if (u) { u.value = username; document.getElementById('login-password').focus(); }
            return;
        }
        _rememberUser(username);
        await _finishLogin(loginData);

    } catch (err) {
        showAuthAlert('ارتباط با سرور برقرار نشد.',
                      'اتصال خود را بررسی کنید. اطلاعات واردشده حفظ شده است.');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-person-plus"></i> ایجاد حساب کاربری';
    }
}

/** FastAPI sends `detail` as a string OR a list of {msg} on a 422. Rendering
 *  the list straight into the DOM produces "[object Object]". */
function _detailText(detail) {
    if (!detail) return '';
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map(d => d.msg || JSON.stringify(d)).join(' — ');
    return JSON.stringify(detail);
}

async function _finishLogin(data) {
    // Record the choice BEFORE the token is stored — setToken reads it to
    // decide between localStorage and sessionStorage.
    _rememberUser(data.username);
    setToken(data.access_token);
    // A visitor has no dashboard at all — send them to their own page rather
    // than rendering a shell with every nav item hidden.
    if (data.role === 'visitor') { window.location.href = '/portal'; return; }
    _currentUser = { username: data.username, role: data.role, full_name: data.full_name, permissions: [] };
    // The token says who you are; /me says what you may open. Nav is built from
    // the second, so it always matches what the routers will allow.
    try {
        const me = await apiCall('/users/me');
        if (me) _currentUser = { ...me, permissions: me.permissions || [] };
    } catch (_) { /* fall back to an empty permission set */ }
    showMainApp();
}

function doLogout() {
    clearToken();
    showLoginPage();
}

// Enter key bindings for login
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('login-password')?.addEventListener('keydown', e => {
        if (e.key === 'Enter') doLogin();
    });
    document.getElementById('login-totp-code')?.addEventListener('keydown', e => {
        if (e.key === 'Enter') verifyTotpLogin();
    });
});

// Show the «customers sign up here» link on the panel login only when public
// sign-up is actually on. The portal redirects to /dashboard while the feature
// is off, so an unconditional link would loop a visitor back to a form they
// cannot use.
document.addEventListener('DOMContentLoaded', () => {
    fetch(`${API_BASE}/public/auth/status`)
        .then(r => r.json())
        .then(s => {
            if (s && s.enabled) {
                document.getElementById('login-portal-link')?.classList.remove('d-none');
            }
        })
        .catch(() => {});
});

// ═══ 2FA Management ════════════════════════════════════════════
let _totpQRInstance = null;

async function open2FAModal() {
    const modal = new bootstrap.Modal(document.getElementById('twoFAModal'));
    modal.show();
    document.getElementById('totp-setup-panel').classList.add('d-none');
    document.getElementById('totp-disable-panel').classList.add('d-none');
    document.getElementById('totp-status-badge').textContent = 'در حال بررسی...';
    document.getElementById('totp-status-badge').className = 'badge bg-secondary';
    document.getElementById('totp-btn-setup').classList.add('d-none');
    document.getElementById('totp-btn-disable').classList.add('d-none');

    loadEmail2faState();

    try {
        const data = await apiCall('/users/me/totp/status');
        if (data.enabled) {
            document.getElementById('totp-status-badge').textContent = 'فعال';
            document.getElementById('totp-status-badge').className = 'badge bg-success';
            document.getElementById('totp-btn-disable').classList.remove('d-none');
        } else {
            document.getElementById('totp-status-badge').textContent = 'غیرفعال';
            document.getElementById('totp-status-badge').className = 'badge bg-secondary';
            document.getElementById('totp-btn-setup').classList.remove('d-none');
        }
    } catch(e) {
        document.getElementById('totp-status-badge').textContent = 'خطا';
    }
}

async function showTotpSetup() {
    document.getElementById('totp-setup-panel').classList.remove('d-none');
    document.getElementById('totp-disable-panel').classList.add('d-none');
    document.getElementById('totp-btn-setup').classList.add('d-none');
    document.getElementById('totp-btn-disable').classList.add('d-none');
    document.getElementById('totp-enable-code').value = '';

    try {
        const data = await apiCall('/users/me/totp/setup', { method: 'POST' });
        document.getElementById('totp-secret-display').value = data.secret;

        const qrContainer = document.getElementById('totp-qrcode');
        qrContainer.innerHTML = '';
        if (typeof QRCode !== 'undefined') {
            _totpQRInstance = new QRCode(qrContainer, {
                text: data.qr_uri,
                width: 180,
                height: 180,
                colorDark: '#000000',
                colorLight: '#ffffff',
                correctLevel: QRCode.CorrectLevel.M,
            });
        } else {
            qrContainer.innerHTML = `<div class="small text-muted">${esc(data.qr_uri)}</div>`;
        }
    } catch(e) {
        showToast('خطا', 'خطا در دریافت اطلاعات 2FA', 'danger');
    }
}

function showTotpDisable() {
    document.getElementById('totp-disable-panel').classList.remove('d-none');
    document.getElementById('totp-setup-panel').classList.add('d-none');
    document.getElementById('totp-btn-setup').classList.add('d-none');
    document.getElementById('totp-btn-disable').classList.add('d-none');
    document.getElementById('totp-disable-password').value = '';
}

async function enableTotp() {
    const code = document.getElementById('totp-enable-code').value.trim();
    if (!code || code.length !== 6) { showToast('خطا', 'کد ۶ رقمی را وارد کنید', 'warning'); return; }

    try {
        await apiCall('/users/me/totp/enable', {
            method: 'POST',
            body: JSON.stringify({ code })
        });
        showToast('موفق', 'احراز هویت دو مرحله‌ای فعال شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('twoFAModal'))?.hide();
    } catch(e) {
        showToast('خطا', e.message, 'danger');
    }
}

async function disableTotp() {
    const password = document.getElementById('totp-disable-password').value;
    if (!password) { showToast('خطا', 'رمز عبور را وارد کنید', 'warning'); return; }

    try {
        await apiCall('/users/me/totp/disable', {
            method: 'POST',
            body: JSON.stringify({ password })
        });
        showToast('موفق', 'احراز هویت دو مرحله‌ای غیرفعال شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('twoFAModal'))?.hide();
    } catch(e) {
        showToast('خطا', e.message, 'danger');
    }
}

// ═══ Avatars ═══════════════════════════════════════════════════
// One helper for every place a person appears, so the picture, the initial
// fallback and the presence dot look the same in the sidebar, the users list
// and the profile header.
const PRESENCE_FA = { available: 'در دسترس', busy: 'مشغول', away: 'دور از میز' };

function avatarHtml(u, size = 34, cls = 'avatar', id = '') {
    const name = (u && (u.full_name || u.username)) || '';
    const initial = name.trim().charAt(0) || '?';
    const presence = (u && u.presence) || 'available';
    const inner = (u && u.avatar_url)
        ? `<img src="${safeUrl(u.avatar_url)}" alt="${esc(name)}" loading="lazy">`
        : `<span class="av-initial">${esc(initial)}</span>`;
    return `<span class="${cls} av av-${presence}" ${id ? `id="${id}"` : ''}
                  style="--av:${size}px" title="${esc(name)} — ${PRESENCE_FA[presence] || ''}">${inner}<i class="av-dot"></i></span>`;
}

// ═══ My profile ════════════════════════════════════════════════
let _pfMe = null;

async function loadProfile() {
    try {
        _pfMe = await apiCall('/users/me');
    } catch (e) { showToast('خطا', e.message, 'danger'); return; }
    pfRender(_pfMe);
    loadPhoneState();
}

function pfRender(me) {
    const roleMap = { root: 'Root', super_admin: 'مدیر ارشد', admin: 'مدیر', visitor: 'بازدیدکننده' };
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    const av = document.getElementById('pf-avatar');
    if (av) av.outerHTML = avatarHtml(me, 96, 'pf-avatar', 'pf-avatar');
    document.getElementById('pf-avatar-remove')?.classList.toggle('d-none', !me.avatar_url);
    set('pf-name', me.full_name || me.username);
    set('pf-headline', me.headline || 'سمت خود را در «مشخصات» بنویسید');
    const roleEl = document.getElementById('pf-role');
    if (roleEl) {
        const rl = (typeof ROLE_LABELS !== 'undefined' && ROLE_LABELS[me.role]) || { label: roleMap[me.role] || me.role, cls: 'bg-dark' };
        roleEl.textContent = rl.label; roleEl.className = 'badge ' + rl.cls;
    }
    set('pf-username', '@' + me.username);
    const fa = d => new Date(d).toLocaleDateString('fa-IR');
    set('pf-since', me.created_at ? fa(me.created_at) : '—');
    set('pf-last', me.last_login ? fa(me.last_login) : '—');
    // a 10.42.x.x here means the cluster is still hiding callers' addresses
    apiCall('/users/me/ip').then(r => set('pf-ip', r.ip)).catch(() => {});
    const pres = document.getElementById('pf-presence');
    if (pres) pres.value = me.presence || 'available';

    // the form
    const f = id => document.getElementById(id);
    if (f('pf-full-name')) {
        f('pf-full-name').value = me.full_name || '';
        f('pf-username-in').value = me.username || '';
        f('pf-headline-in').value = me.headline || '';
        f('pf-bio').value = me.bio || '';
        const l = me.links || {};
        f('pf-website').value = l.website || '';
        f('pf-instagram').value = l.instagram || '';
        f('pf-linkedin').value = l.linkedin || '';
    }

    // contact
    set('pf-email', me.email || '—');
    const eb = document.getElementById('pf-email-badge');
    if (eb) {
        const ok = !!me.email_verified;
        eb.textContent = !me.email ? 'ثبت نشده' : (ok ? 'تأیید شده' : 'تأیید نشده');
        eb.className = 'badge ms-1 ' + (ok ? 'bg-success' : (me.email ? 'bg-warning text-dark' : 'bg-secondary'));
        document.getElementById('pf-email-btn').textContent = me.email && !ok ? 'ارسال کد' : 'تغییر ایمیل';
    }
    pfLoadDivarAccounts();

    // the sidebar card follows
    if (_currentUser) { Object.assign(_currentUser, me); applyRoleUI(); }
}

async function pfSaveProfile(ev) {
    ev.preventDefault();
    const v = id => (document.getElementById(id)?.value || '').trim();
    const body = {
        full_name: v('pf-full-name'), username: v('pf-username-in'),
        headline: v('pf-headline-in'), bio: v('pf-bio'),
        links: { website: v('pf-website'), instagram: v('pf-instagram'), linkedin: v('pf-linkedin') },
    };
    if (body.username !== (_pfMe?.username || '')) {
        const ok = await askConfirm({
            icon: 'bi-person-badge', title: 'تغییر نام کاربری', tone: 'warning', okLabel: 'تغییر بده',
            body: `از این پس با <b dir="ltr">${esc(body.username)}</b> وارد می‌شوید. ادامه؟`,
        });
        if (!ok) return;
    }
    try {
        const r = await apiCall('/users/me', { method: 'PATCH', body: JSON.stringify(body) });
        // A rename comes with a fresh token: the old one names a user that no
        // longer exists and would fail on the very next request.
        if (r.access_token) setToken(r.access_token);
        _pfMe = r.user; pfRender(_pfMe);
        showToast('ذخیره شد', '', 'success');
    } catch (e) { showToast('خطا', e.message, 'danger'); }
    return false;
}

async function pfSetPresence(value) {
    try {
        const r = await apiCall('/users/me', { method: 'PATCH', body: JSON.stringify({ presence: value }) });
        _pfMe = r.user; pfRender(_pfMe);
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function pfUploadAvatar(input) {
    const file = input.files && input.files[0];
    input.value = '';
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) { showToast('خطا', 'حجم تصویر باید کمتر از ۵ مگابایت باشد', 'warning'); return; }
    const fd = new FormData();
    fd.append('file', file);
    try {
        // Not apiCall: it forces a JSON content-type and multipart needs the
        // browser to write the boundary itself.
        const res = await fetch(`${API_BASE}/users/me/avatar`, {
            method: 'POST', body: fd, headers: { 'Authorization': `Bearer ${getToken()}` },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
        showToast('عکس عوض شد', '', 'success');
        loadProfile();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function pfRemoveAvatar() {
    if (!await askConfirm({ icon: 'bi-person-x', title: 'حذف عکس', tone: 'danger', okLabel: 'حذف', body: 'عکس پروفایل حذف شود؟' })) return;
    try {
        await apiCall('/users/me/avatar', { method: 'DELETE' });
        loadProfile();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function pfRequestEmailCode() {
    const out = document.getElementById('pf-email-result');
    const fresh = (document.getElementById('pf-email-new')?.value || '').trim();
    const btn = document.getElementById('pf-email-btn');
    if (!fresh && !_pfMe?.email) { out.textContent = 'ابتدا ایمیل تازه را بنویسید'; out.className = 'small mt-2 text-warning'; return; }
    if (!fresh && _pfMe?.email_verified) { document.getElementById('pf-email-new')?.focus(); return; }
    btn.disabled = true;
    out.textContent = 'در حال ارسال…'; out.className = 'small mt-2 text-muted';
    try {
        const d = await apiCall('/users/me/email/request', {
            method: 'POST', body: JSON.stringify(fresh ? { email: fresh } : {}),
        });
        if (d.verified) { out.textContent = d.message; out.className = 'small mt-2 text-success'; loadProfile(); return; }
        document.getElementById('pf-email-code-wrap').classList.remove('d-none');
        document.getElementById('pf-email-code').focus();
        out.textContent = `${d.message} (${d.email})`; out.className = 'small mt-2 text-success';
    } catch (e) {
        out.textContent = e.message || 'خطا'; out.className = 'small mt-2 text-danger';
    }
    btn.disabled = false;
}

async function pfConfirmEmailCode() {
    const out = document.getElementById('pf-email-result');
    const code = (document.getElementById('pf-email-code')?.value || '').trim();
    if (!code) return;
    out.textContent = 'در حال بررسی…'; out.className = 'small mt-2 text-muted';
    try {
        const d = await apiCall('/users/me/email/verify', { method: 'POST', body: JSON.stringify({ code }) });
        out.textContent = d.message; out.className = 'small mt-2 text-success';
        document.getElementById('pf-email-code-wrap').classList.add('d-none');
        document.getElementById('pf-email-code').value = '';
        document.getElementById('pf-email-new').value = '';
        loadProfile();
        if (typeof loadUsers === 'function' && document.getElementById('users-table')) loadUsers();
    } catch (e) {
        out.textContent = e.message || 'کد نادرست است'; out.className = 'small mt-2 text-danger';
    }
}

async function pfChangePassword(ev) {
    ev.preventDefault();
    const v = id => document.getElementById(id).value;
    const out = document.getElementById('pf-pw-result');
    if (v('pf-pw-new').length < 8) { out.textContent = 'رمز تازه باید دست‌کم ۸ نویسه باشد'; out.className = 'small mt-2 text-warning'; return false; }
    if (v('pf-pw-new') !== v('pf-pw-new2')) { out.textContent = 'تکرار رمز یکی نیست'; out.className = 'small mt-2 text-warning'; return false; }
    out.textContent = 'در حال تغییر…'; out.className = 'small mt-2 text-muted';
    try {
        const r = await apiCall('/users/me/password', {
            method: 'POST',
            body: JSON.stringify({ current_password: v('pf-pw-current'), new_password: v('pf-pw-new') }),
        });
        // Every other device is signed out by the version bump; this one
        // keeps the fresh token the server minted for it.
        if (r.access_token) setToken(r.access_token);
        ['pf-pw-current', 'pf-pw-new', 'pf-pw-new2'].forEach(id => document.getElementById(id).value = '');
        out.textContent = r.message; out.className = 'small mt-2 text-success';
        showToast('رمز عوض شد', 'دستگاه‌های دیگر از حساب خارج شدند', 'success');
    } catch (e) {
        out.textContent = e.message || 'خطا'; out.className = 'small mt-2 text-danger';
    }
    return false;
}

// ── my Divar accounts: as many numbers as I have logged in ──
const _digits = v => String(v || '').replace(/\D/g, '');

async function pfLoadDivarAccounts() {
    const box = document.getElementById('pf-divar-list');
    const count = document.getElementById('pf-divar-count');
    if (!box) return;
    if (!_hasPerm('divar_auth')) {
        box.innerHTML = '<div class="pf-note">برای افزودن حساب دیوار به دسترسی «حساب‌های دیوار» نیاز دارید — از مدیر بخواهید.</div>';
        return;
    }
    try {
        const d = await apiCall('/auth/cookies?mine=1');
        const mine = d.cookies || [];
        if (count) count.textContent = formatNumber(mine.length);
        if (!mine.length) {
            box.innerHTML = '<div class="pf-note">هنوز با هیچ شماره‌ای وارد دیوار نشده‌اید. «افزودن شماره» را بزنید.</div>';
            return;
        }
        const primary = _digits(_pfMe?.divar_phone);
        box.innerHTML = mine.map(c => {
            const isPrimary = primary && _digits(c.phone_number) === primary;
            return `<div class="pf-acct ${isPrimary ? 'is-primary' : ''}">
                <div class="pf-acct-main">
                  <b dir="ltr">${esc(c.phone_number)}</b>
                  <span class="badge ${c.is_valid ? 'bg-success' : 'bg-secondary'}">${c.is_valid ? 'معتبر' : 'منقضی'}</span>
                  ${isPrimary ? '<span class="badge bg-primary">پیش‌فرض</span>' : ''}
                  <span class="pf-note">${formatNumber(c.reveals || 0)} شماره‌گیری</span>
                </div>
                <div class="pf-acct-actions">
                  ${isPrimary ? '' : `<button class="btn btn-sm btn-outline-primary" onclick="pfSetPrimaryDivar(${jsArg(c.phone_number)})" title="پیش‌فرض کن">پیش‌فرض</button>`}
                  <button class="btn btn-sm btn-outline-danger" onclick="pfDeleteDivar(${esc(c.id)})" title="حذف نشست"><i class="bi bi-trash"></i></button>
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        box.innerHTML = `<div class="pf-note text-danger">${esc(e.message || 'خطا')}</div>`;
    }
}

async function pfSetPrimaryDivar(phone) {
    try {
        await apiCall('/users/me/divar-phone', { method: 'PATCH', body: JSON.stringify({ divar_phone: phone }) });
        showToast('ذخیره شد', `${phone} شمارهٔ پیش‌فرض شما شد`, 'success');
        loadProfile();
        if (typeof checkCookieStatus === 'function') checkCookieStatus();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function pfDeleteDivar(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف نشست', tone: 'danger', okLabel: 'حذف', body: 'این نشست دیوار حذف شود؟ برای استفادهٔ دوباره باید با همین شماره وارد شوید.' })) return;
    try {
        await apiCall(`/auth/cookies/${id}`, { method: 'DELETE' });
        showToast('حذف شد', '', 'success');
        pfLoadDivarAccounts();
        if (typeof checkCookieStatus === 'function') checkCookieStatus();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

function pfAddDivarAccount() {
    // Logging in IS adding: the number that answers Divar's code joins this
    // person's pool. The login form lives on «احراز هویت دیوار».
    if (!_hasPerm('divar_auth')) { showToast('دسترسی', 'به دسترسی «حساب‌های دیوار» نیاز دارید', 'warning'); return; }
    showSection('auth');
    setTimeout(() => document.getElementById('auth-phone')?.focus(), 300);
}

// Admin: ask a person to verify what is still unverified on their account.
/** root only: tick a user's phone or email verified — or take it back. */
async function setUserVerified(id, kind, value, input) {
    if (input) input.disabled = true;
    try {
        const u = await apiCall(`/users/${Number(id)}/verification`, {
            method: 'PATCH', body: JSON.stringify({ [`${kind}_verified`]: !!value }) });
        if (_usersById && _usersById[id]) Object.assign(_usersById[id], u);
        showToast(value ? 'تأیید شد' : 'تأیید برداشته شد',
            `${kind === 'email' ? 'ایمیل' : 'شمارهٔ'} ${u.full_name || u.username} ${value ? 'تأییدشده' : 'تأییدنشده'} علامت خورد`,
            value ? 'success' : 'warning');
    } catch (e) {
        if (input) input.checked = !value;
        showToast('خطا', e.message, 'danger');
    } finally {
        if (input) input.disabled = false;
        if (typeof loadUsers === 'function') loadUsers();
    }
}

async function nudgeVerify(id) {
    const u = _usersById[id] || {};
    const what = [u.email && !u.email_verified ? 'ایمیل' : '', u.phone && !u.phone_verified ? 'شمارهٔ موبایل' : ''].filter(Boolean).join(' و ');
    if (!await askConfirm({
        icon: 'bi-send-check', title: 'درخواست تأیید', okLabel: 'بفرست',
        body: `برای <b>${esc(u.full_name || u.username)}</b> پیام فرستاده می‌شود که ${what} خود را در پروفایلش تأیید کند.`,
        note: 'هر یک ساعت یک بار برای هر کاربر.',
    })) return;
    try {
        const r = await apiCall(`/users/${id}/verification-request`, { method: 'POST' });
        showToast('فرستاده شد', r.message, 'success');
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function loadPhoneState() {
    const cur = document.getElementById('phone-current');
    const badge = document.getElementById('phone-badge');
    const btn = document.getElementById('phone-verify-btn');
    const out = document.getElementById('phone-result');
    if (out) out.textContent = '';
    document.getElementById('phone-code-wrap')?.classList.add('d-none');
    try {
        const me = await apiCall('/users/me');
        if (cur) cur.textContent = me.phone || '—';
        if (badge) {
            const ok = !!me.phone_verified;
            badge.textContent = !me.phone ? 'ثبت نشده' : (ok ? 'تأیید شده' : 'تأیید نشده');
            badge.className = 'badge ms-1 ' + (ok ? 'bg-success' : (me.phone ? 'bg-warning text-dark' : 'bg-secondary'));
            // A verified number needs no button; leaving one there invites
            // somebody to burn a code proving what is already proved.
            if (btn) btn.classList.toggle('d-none', ok);
            document.getElementById('phone-edit-wrap')?.classList.toggle('d-none', ok);
        }
    } catch (e) {
        if (cur) cur.textContent = '—';
    }
}

async function requestPhoneCode() {
    const out = document.getElementById('phone-result');
    const btn = document.getElementById('phone-verify-btn');
    const fresh = (document.getElementById('phone-new')?.value || '').trim();
    if (btn) btn.disabled = true;
    if (out) { out.textContent = 'در حال ارسال…'; out.className = 'small mt-2 text-muted'; }
    try {
        const d = await apiCall('/users/me/phone/request', {
            method: 'POST',
            body: JSON.stringify(fresh ? { phone: fresh } : {})
        });
        if (d.verified) {           // already done; nothing was sent
            if (out) { out.textContent = d.message; out.className = 'small mt-2 text-success'; }
            loadPhoneState();
            return;
        }
        document.getElementById('phone-code-wrap')?.classList.remove('d-none');
        document.getElementById('phone-code')?.focus();
        if (out) { out.textContent = d.message; out.className = 'small mt-2 text-success'; }
    } catch (e) {
        if (out) { out.textContent = e.message || 'خطا'; out.className = 'small mt-2 text-danger'; }
    }
    if (btn) btn.disabled = false;
}

async function confirmPhoneCode() {
    const out = document.getElementById('phone-result');
    const code = (document.getElementById('phone-code')?.value || '').trim();
    if (!code) return;
    if (out) { out.textContent = 'در حال بررسی…'; out.className = 'small mt-2 text-muted'; }
    try {
        const d = await apiCall('/users/me/phone/verify', {
            method: 'POST', body: JSON.stringify({ code })
        });
        if (out) { out.textContent = d.message; out.className = 'small mt-2 text-success'; }
        loadPhoneState();
        // the users list carries the same badge
        if (typeof loadUsers === 'function') loadUsers();
    } catch (e) {
        if (out) { out.textContent = e.message || 'کد نادرست است'; out.className = 'small mt-2 text-danger'; }
    }
}

async function loadEmail2faState() {
    const box = document.getElementById('email-2fa-toggle');
    const addr = document.getElementById('email-2fa-address');
    box.disabled = true;
    try {
        const me = await apiCall('/users/me');
        box.checked = !!me.email_2fa_enabled;
        // No address on file means the switch would lock the account out of
        // its own panel, so the server refuses it — say so here rather than
        // letting the user find out by being refused.
        addr.textContent = me.email || '—';
        box.disabled = !me.email;
    } catch (e) {
        addr.textContent = '—';
    }
}

async function toggleEmail2fa(enabled) {
    const box = document.getElementById('email-2fa-toggle');
    box.disabled = true;
    try {
        const r = await apiCall('/users/me/email-2fa', {
            method: 'POST',
            body: JSON.stringify({ enabled })
        });
        showToast('موفق', r.message, 'success');
    } catch (e) {
        box.checked = !enabled;   // the server said no; the switch must agree
        showToast('خطا', e.message, 'danger');
    }
    box.disabled = false;
}

function copyTotpSecret() {
    const val = document.getElementById('totp-secret-display').value;
    navigator.clipboard.writeText(val).then(() => showToast('کپی شد', 'کلید در کلیپ‌بورد کپی شد', 'success'));
}

// ═══ Hash router: #/login, #/dashboard, #/properties, ... ═══════
const ROUTE_SECTIONS = ['dashboard', 'properties', 'scraper', 'crm', 'insights', 'auth', 'forwarder', 'proxies', 'portal', 'monitoring', 'ai', 'sms', 'email', 'users', 'profile'];
let _currentSection = null;
let _intendedRoute = null;   // deep link requested before login
let _suppressHashNav = false;

function _hashToSection(hash) {
    const name = (hash || '').replace(/^#\/?/, '');
    return ROUTE_SECTIONS.includes(name) ? name : null;
}

function _setRoute(name) {
    const h = '#/' + name;
    if (location.hash !== h) {
        _suppressHashNav = true;
        location.hash = h;
    }
}

addEventListener('hashchange', () => {
    if (_suppressHashNav) { _suppressHashNav = false; return; }
    const hash = location.hash;
    if (hash === '#/login' || hash === '#/login/') {
        if (!_currentUser) showLoginPage();
        else _setRoute(_currentSection || _defaultSection()); // logged in — bounce back
        return;
    }
    const section = _hashToSection(hash);
    if (!section) return;
    if (!_currentUser) {
        _intendedRoute = section;
        showLoginPage();
        return;
    }
    if (section !== _currentSection) showSection(section);
});

function showLoginPage() {
    _setRoute('login');
    document.title = 'SorinFlow — ورود';
    document.getElementById('login-page').style.display = 'flex';
    document.getElementById('main-app').style.display = 'none';
    // Reset to step 1 (login form)
    document.getElementById('login-step-1')?.classList.remove('d-none');
    document.getElementById('login-step-2')?.classList.add('d-none');
    document.getElementById('login-step-register')?.classList.add('d-none');
    // The email and reset steps too. Missing them leaves the login page
    // showing two forms at once after a logout from an email-2FA account.
    document.getElementById('login-step-email')?.classList.add('d-none');
    document.getElementById('login-step-reset')?.classList.add('d-none');
    _totpSession = null;
    _emailSession = null;
    document.getElementById('login-error')?.classList.add('d-none');
    document.getElementById('login-toggle-link') && (document.getElementById('login-toggle-link').style.display = '');
    document.getElementById('register-toggle-link') && (document.getElementById('register-toggle-link').style.display = 'none');
    document.getElementById('login-totp-code') && (document.getElementById('login-totp-code').value = '');
}

function showMainApp() {
    document.getElementById('login-page').style.display = 'none';
    document.getElementById('main-app').style.display = 'flex';
    applyRoleUI();
    initApp();
    _pwaMaybeHint();
    // Deep link (#/crm etc.) wins over the role's default section
    const target = _intendedRoute || _hashToSection(location.hash) || _defaultSection();
    _intendedRoute = null;
    showSection(target);
}

// Nav visibility follows the permissions the server reports for this account,
// not the role name. The role alone stopped being enough once admins could be
// given different areas — and the server enforces the same keys on the routers,
// so hiding a link and refusing the request can no longer disagree.
const NAV_PERMISSION = {
    'nav-link-dashboard':  'stats',
    'nav-link-properties': 'properties',
    'nav-link-scraper':    'scraper',
    'nav-link-crm':        'crm',
    'nav-link-auth':       'divar_auth',
    'nav-link-forwarder':  'forwarder',
    'nav-link-proxies':    'proxies',
    'nav-link-portal':     'portal',
    'nav-link-monitoring': 'monitoring',
    'nav-link-sms':        'sms',
    'nav-link-email':      'email',
};
// Sections that are not permission-gated but role-gated.
const NAV_ROLE_ONLY = { 'nav-users': ['root', 'super_admin'], 'nav-link-ai': ['root', 'super_admin'] };

const SECTION_PERMISSION = {
    dashboard: 'stats', properties: 'properties', scraper: 'scraper',
    crm: 'crm', insights: 'crm', auth: 'divar_auth', forwarder: 'forwarder', proxies: 'proxies', portal: 'portal',
    monitoring: 'monitoring', sms: 'sms', email: 'email',
};

function _perms() { return (_currentUser && _currentUser.permissions) || []; }
function _hasPerm(p) { return _perms().includes(p); }

function applyRoleUI() {
    if (!_currentUser) return;
    const { role, username, full_name } = _currentUser;

    const elName = document.getElementById('sidebar-username');
    if (elName) elName.textContent = full_name || username;
    const elRole = document.getElementById('sidebar-role');
    const roleMap = { root: 'Root', super_admin: 'مدیر ارشد', admin: 'مدیر', visitor: 'بازدیدکننده' };
    // the headline is what the person says they do; the role is what the
    // system says they may do — the card shows the first when there is one
    if (elRole) elRole.textContent = _currentUser.headline || roleMap[role] || role;
    const elAv = document.getElementById('sidebar-avatar');
    if (elAv) elAv.outerHTML = avatarHtml(_currentUser, 34, 'user-avatar', 'sidebar-avatar');

    Object.entries(NAV_PERMISSION).forEach(([id, perm]) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('d-none', !_hasPerm(perm));
    });
    Object.entries(NAV_ROLE_ONLY).forEach(([id, roles]) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('d-none', !roles.includes(role));
    });
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    const token = getToken();
    if (!token) {
        _intendedRoute = _hashToSection(location.hash);
        showLoginPage();
        return;
    }
    _authToken = token;
    // Verify the token by fetching /me — which also tells us what this account
    // may open, so the nav is built from the server's answer rather than from
    // whatever the token happens to claim.
    fetch(`${API_BASE}/users/me`, { headers: { 'Authorization': `Bearer ${token}` } })
        .then(r => {
            if (!r.ok) throw new Error('invalid');
            return r.json();
        })
        .then(user => {
            if (user.role === 'visitor') { window.location.href = '/portal'; return; }
            _currentUser = { ...user, permissions: user.permissions || [] };
            showMainApp();
        })
        .catch(() => {
            clearToken();
            _intendedRoute = _hashToSection(location.hash);
            showLoginPage();
        });
});


function initApp() {
    // Only preload what this account may actually fetch. These four call
    // routers that are now permission-gated, so firing them unconditionally
    // greets an admin with a row of 403 toasts for areas they were never
    // given — the request fails and the panel looks broken on every login.
    if (_hasPerm('stats')) loadDashboard();
    if (_hasPerm('properties')) { loadCities(); loadCategories(); }
    if (_hasPerm('divar_auth')) checkCookieStatus();
    initOtpBoxes();

    document.getElementById('scraper-form').addEventListener('submit', startScraping);
    document.getElementById('proxy-form').addEventListener('submit', addProxy);
    document.getElementById('user-create-form')?.addEventListener('submit', createUser);
    document.getElementById('crm-filter-search')?.addEventListener('keydown', e => {
        if (e.key === 'Enter') loadLeads();
    });
    document.getElementById('customer-search')?.addEventListener('keydown', e => {
        if (e.key === 'Enter') loadCustomers();
    });
    document.getElementById('dpa-search')?.addEventListener('keydown', e => {
        if (e.key === 'Enter') loadDpa();
    });

    setInterval(() => {
        const dash = document.getElementById('section-dashboard');
        if (dash && dash.style.display !== 'none' && _hasPerm('stats')) loadDashboard();
    }, 60000);
    setInterval(() => { if (_hasPerm('divar_auth')) checkCookieStatus(); }, 300000);
}

// Section titles for the topbar
const SECTION_META = {
    dashboard:  { title: 'داشبورد',             subtitle: 'خلاصه وضعیت و آمار کلی سیستم' },
    properties: { title: 'لیست املاک',          subtitle: 'مدیریت و جستجوی ملک‌های اسکرپ‌شده' },
    scraper:    { title: 'اسکرپر دیوار',         subtitle: 'تنظیم و اجرای تسک‌های اسکرپینگ' },
    crm:        { title: 'CRM — مدیریت لیدها',  subtitle: 'سیستم CRM و اطلاع‌رسانی' },
    insights:   { title: 'هوش تصویری',           subtitle: 'قیف فروش، عملکرد مشاوران و لیدهای معطل‌مانده' },
    auth:       { title: 'احراز هویت دیوار',     subtitle: 'مدیریت نشست و کوکی حساب دیوار' },
    forwarder:  { title: 'فرستندهٔ پیامک',       subtitle: 'گوشی‌هایی که کدهای دیوار را خودکار به سرور می‌رسانند' },
    proxies:    { title: 'مدیریت پراکسی‌ها',     subtitle: 'افزودن، تست و مدیریت پراکسی‌ها' },
    portal:     { title: 'درخواست‌های مشتریان',  subtitle: 'ملک‌هایی که بازدیدکنندگان سایت دنبالش هستند' },
    monitoring: { title: 'پایش سامانه',          subtitle: 'سلامت سرویس‌ها، منابع و لاگ زندهٔ سامانه' },
    ai:         { title: 'هوش مصنوعی',           subtitle: 'ایجنت‌ها، مصرف، لاگ فراخوانی‌ها و تنظیمات مدل' },
    email:      { title: 'ایمیل',                subtitle: 'تنظیمات SMTP، قالب‌های سایت و گزارش ارسال' },
    sms:        { title: 'پیامک',                subtitle: 'تنظیمات کاوه‌نگار، ارسال تکی و گروهی، و گزارش تحویل' },
    users:      { title: 'مدیریت کاربران',       subtitle: 'حساب‌ها، دسترسی‌ها و درخواست‌های ارتقا' },
    profile:    { title: 'پروفایل من',           subtitle: 'مشخصات، تماس و تأیید، امنیت حساب' },
};

// ═══ Navigation: four groups, and a way to type your way to any of them ═══
//
// Fifteen screens in one flat list meant reading all fifteen to find one, and
// the two labels it had ("منو اصلی", "تنظیمات") put the scraper's own session
// page under Settings, three screens away from the scraper. They are grouped by
// the work now, and the group holding the open screen opens itself.

const NAV_GROUPS = {
    daily:  ['dashboard', 'properties', 'crm', 'portal'],
    scrape: ['scraper', 'auth', 'proxies', 'insights'],
    comms:  ['sms', 'email', 'forwarder'],
    system: ['ai', 'monitoring', 'users'],
};
const _NAV_SHUT_KEY = 'sf_nav_shut';

function _navShut() {
    try { return new Set(JSON.parse(localStorage.getItem(_NAV_SHUT_KEY) || '[]')); }
    catch (e) { return new Set(); }
}

function toggleNavGroup(key) {
    const shut = _navShut();
    shut.has(key) ? shut.delete(key) : shut.add(key);
    try { localStorage.setItem(_NAV_SHUT_KEY, JSON.stringify([...shut])); } catch (e) {}
    _paintNavGroups();
}

function _paintNavGroups() {
    const shut = _navShut();
    // The group holding the open screen is never collapsed: hiding the active
    // item leaves the panel with nothing highlighted and no clue where you are.
    const live = Object.keys(NAV_GROUPS).find(k => NAV_GROUPS[k].includes(_currentSection));
    document.querySelectorAll('.nav-group').forEach(g => {
        const key = g.dataset.group;
        const open = key === live || !shut.has(key);
        g.classList.toggle('shut', !open);
        g.querySelector('.nav-group-head')?.setAttribute('aria-expanded', String(open));
    });
    // A group whose every screen is hidden from this role is itself pointless.
    document.querySelectorAll('.nav-group').forEach(g => {
        const any = [...g.querySelectorAll('.nav-item-link')].some(a => !a.classList.contains('d-none'));
        g.classList.toggle('d-none', !any);
    });
}

// ── The palette ──────────────────────────────────────────────────────────────
// Keywords are what someone would actually type, including the word they used
// before the screen was renamed — «کوکی» still finds the Divar session page.
const PALETTE_EXTRA_WORDS = {
    dashboard:  'خانه آمار وضعیت خلاصه',
    properties: 'ملک آگهی خانه آپارتمام جستجو لیست',
    crm:        'مشتری لید تماس وظیفه یادآور معامله دفترچه تلفن تقویم',
    portal:     'سایت درخواست بازدیدکننده فرم',
    scraper:    'اجرا تسک جاب زمان‌بندی دیوار استخراج',
    auth:       'کوکی نشست ورود شماره حساب دیوار session',
    proxies:    'پروکسی آی‌پی ip proxy',
    insights:   'نمودار قیف گزارش عملکرد تحلیل',
    sms:        'پیامک کاوه‌نگار ارسال اس ام اس',
    email:      'ایمیل smtp میل قالب',
    forwarder:  'گوشی کد فوروارد forwarder',
    ai:         'هوش ایجنت مدل توکن سورین دستیار',
    monitoring: 'لاگ سلامت سرور منابع مانیتور',
    users:      'کاربر دسترسی نقش حساب',
    profile:    'پروفایل من رمز عبور دو مرحله‌ای امنیت',
};

// Things people do, not places they go. Each one lands on the screen that does
// it — the palette is the shortest route to a task, not a menu with a filter.
const PALETTE_ACTIONS = [
    { label: 'اجرای اسکرپ تازه', icon: 'play-circle', section: 'scraper', words: 'شروع استخراج جدید run' },
    { label: 'تماس‌های امروز', icon: 'telephone-outbound', section: 'crm', words: 'صف تماس زنگ' },
    { label: 'افزودن شمارهٔ دیوار', icon: 'key', section: 'auth', words: 'حساب جدید کوکی ورود' },
    { label: 'لاگ زندهٔ سامانه', icon: 'terminal', section: 'monitoring', words: 'خطا error لاگ' },
    { label: 'مصرف و لاگ هوش مصنوعی', icon: 'stars', section: 'ai', words: 'هزینه توکن ایجنت' },
    { label: 'پروفایل و امنیت من', icon: 'person-circle', section: 'profile', words: 'رمز دو مرحله‌ای' },
    { label: 'خروج از حساب', icon: 'box-arrow-right', run: 'doLogout()', words: 'logout بیرون' },
];

// The CRM's own thirteen screens. They live behind a tab strip that scrolls
// sideways on a phone with two of the thirteen in view, so «معاملات» was a
// drag through eleven others to reach — unless you can just type it.
const CRM_TABS = [
    { tab: 'calls',     label: 'تماس‌های امروز',  icon: 'telephone-outbound', words: 'صف زنگ تماس سررسید' },
    { tab: 'tasks',     label: 'وظایف',           icon: 'check2-square',      words: 'کار تسک انجام' },
    { tab: 'filing',    label: 'کمد و زونکن',     icon: 'archive',            words: 'بایگانی پرونده مدرک' },
    { tab: 'calendar',  label: 'تقویم',           icon: 'calendar3',          words: 'قرار بازدید روز هفته' },
    { tab: 'customers', label: 'مشتریان',         icon: 'person-vcard',       words: 'خریدار مستاجر نیاز بودجه' },
    { tab: 'contacts',  label: 'دفترچه تلفن',     icon: 'person-lines-fill',  words: 'شماره مخاطب تلفن' },
    { tab: 'deals',     label: 'معاملات',         icon: 'handshake',          words: 'قرارداد فروش کمیسیون' },
    { tab: 'notes',     label: 'یادداشت‌ها',       icon: 'journal-text',       words: 'نوشته یادداشت' },
    { tab: 'reminders', label: 'یادآورها',        icon: 'alarm',              words: 'هشدار یادآوری زنگ' },
    { tab: 'sms',       label: 'پیامک مشتریان',   icon: 'chat-dots',          words: 'اس ام اس ارسال گروهی' },
    { tab: 'leads',     label: 'لیدها',           icon: 'people',             words: 'سرنخ مشتری تازه' },
    { tab: 'dpa',       label: 'ارزیابی روزانه',  icon: 'clipboard-data',     words: 'dpa امتیاز عملکرد مشاور' },
    { tab: 'report',    label: 'گزارش CRM',       icon: 'graph-up',           words: 'ریپورت آمار خلاصه' },
];

let _paletteItems = [];
let _paletteAt = 0;

function _paletteBuild() {
    const out = [];
    for (const [sec, meta] of Object.entries(SECTION_META)) {
        if (!_isSectionAllowed(sec)) continue;
        out.push({ kind: 'صفحه', label: meta.title, hint: meta.subtitle, section: sec,
                   icon: _navIcon(sec), words: `${meta.title} ${meta.subtitle} ${PALETTE_EXTRA_WORDS[sec] || ''} ${sec}` });
    }
    for (const a of PALETTE_ACTIONS) {
        if (a.section && !_isSectionAllowed(a.section)) continue;
        out.push({ kind: 'کار', label: a.label, hint: a.section ? (SECTION_META[a.section] || {}).title : '',
                   section: a.section, run: a.run, icon: a.icon, words: `${a.label} ${a.words}` });
    }
    if (_isSectionAllowed('crm')) {
        for (const t of CRM_TABS) {
            out.push({ kind: 'CRM', label: t.label, hint: 'CRM — لیدها', crmTab: t.tab,
                       icon: t.icon, words: `${t.label} ${t.words} crm` });
        }
    }
    return out;
}

const NAV_ICONS = {
    dashboard: 'speedometer2', properties: 'house-door', crm: 'people', portal: 'inbox',
    scraper: 'robot', auth: 'key', proxies: 'shield-check', insights: 'graph-up-arrow',
    sms: 'chat-left-text', email: 'envelope-at', forwarder: 'phone-vibrate',
    ai: 'stars', monitoring: 'activity', users: 'person-gear', profile: 'person-circle',
};

function _navIcon(sec) {
    return NAV_ICONS[sec] || 'arrow-left-circle';
}

// Persian is typed several ways: the Arabic ي/ك reach the field from phone
// keyboards, and the digits come in both scripts. Fold them, or a search for
// «کاربران» typed on a phone finds nothing.
function _fold(t) {
    return (t || '').toLowerCase()
        .replace(/[يى]/g, 'ی').replace(/ك/g, 'ک').replace(/[ۀة]/g, 'ه')
        .replace(/[\u200c\u064b-\u0652]/g, '')
        .replace(/[۰-۹]/g, d => '٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹'.indexOf(d) % 10)
        .replace(/\s+/g, ' ').trim();
}

// «معامله» has to find «معاملات», «قرارداد» has to find «قراردادها». Persian
// plurals and the ezafe are suffixes, so a substring match on the written form
// misses the word somebody actually typed. Trimming the handful of endings
// that matter off both sides is enough — this ranks results, it does not
// parse the language.
function _stem(w) {
    return w.length > 4 ? w.replace(/(هایی|های|ها|ات|ان|ی|ه)$/, '') : w;
}

function _stemAll(t) {
    return t.split(' ').map(_stem).join(' ');
}

function openPalette() {
    _paletteItems = _paletteBuild();
    const box = document.getElementById('palette');
    if (!box) return;
    box.hidden = false;
    document.body.classList.add('palette-open');
    const q = document.getElementById('palette-q');
    q.value = '';
    paletteFilter();
    setTimeout(() => q.focus(), 20);
}

function closePalette() {
    const box = document.getElementById('palette');
    if (box) box.hidden = true;
    document.body.classList.remove('palette-open');
}

function paletteFilter() {
    const raw = document.getElementById('palette-q')?.value || '';
    const q = _fold(raw);
    const terms = q ? q.split(' ') : [];
    const hits = _paletteItems
        .map(it => {
            const hay = _fold(it.words);
            const stemmed = _stemAll(hay);
            if (!terms.every(t => hay.includes(t) || stemmed.includes(_stem(t)))) return null;
            // A match on the name itself beats one buried in the keywords —
            // and it counts when «معامله» meets «معاملات», or the tab loses to
            // the section that merely mentions it.
            const head = _fold(it.label);
            const headStem = _stemAll(head);
            const qs = _stemAll(q);
            const score = (q && (head.startsWith(q) || headStem.startsWith(qs))) ? 0
                        : (head.includes(q) || headStem.includes(qs)) ? 1 : 2;
            return { it, score };
        })
        .filter(Boolean)
        .sort((a, b) => a.score - b.score)
        .slice(0, 12)
        .map(x => x.it);
    _paletteAt = 0;
    _paletteRender(hits);
}

function _paletteRender(hits) {
    const list = document.getElementById('palette-list');
    if (!list) return;
    if (!hits.length) {
        list.innerHTML = '<div class="palette-empty">چیزی پیدا نشد</div>';
        list.dataset.n = '0';
        return;
    }
    list.dataset.n = String(hits.length);
    list.innerHTML = hits.map((it, i) => `
        <button class="palette-row${i === _paletteAt ? ' on' : ''}" data-i="${i}"
                onclick="paletteGo(${i})" onmousemove="paletteAt(${i})">
            <span class="pr-icon"><i class="bi bi-${it.icon}"></i></span>
            <span class="pr-text">
                <span class="pr-label">${esc(it.label)}</span>
                ${it.hint ? `<span class="pr-hint">${esc(it.hint)}</span>` : ''}
            </span>
            <span class="pr-kind">${it.kind}</span>
        </button>`).join('');
    list._hits = hits;
}

function paletteAt(i) {
    _paletteAt = i;
    document.querySelectorAll('.palette-row').forEach(r => r.classList.toggle('on', +r.dataset.i === i));
}

function paletteGo(i) {
    const list = document.getElementById('palette-list');
    const it = (list?._hits || [])[i];
    if (!it) return;
    closePalette();
    if (it.crmTab) { goCrm(it.crmTab); return; }
    if (it.run) { try { eval(it.run); } catch (e) {} return; }
    if (it.section) showSection(it.section);
}

function _paletteKeys(e) {
    const open = !document.getElementById('palette')?.hidden;
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        open ? closePalette() : openPalette();
        return;
    }
    if (!open) return;
    const n = +(document.getElementById('palette-list')?.dataset.n || 0);
    if (e.key === 'Escape') { e.preventDefault(); closePalette(); }
    else if (e.key === 'ArrowDown' && n) { e.preventDefault(); paletteAt((_paletteAt + 1) % n); }
    else if (e.key === 'ArrowUp' && n) { e.preventDefault(); paletteAt((_paletteAt - 1 + n) % n); }
    else if (e.key === 'Enter' && n) { e.preventDefault(); paletteGo(_paletteAt); }
}
document.addEventListener('keydown', _paletteKeys);

// The call queue's explanations are clamped to two lines on a phone; a tap
// opens the one you tapped. Delegated, because the panes render on demand.
document.addEventListener('click', e => {
    const intro = e.target.closest?.('.cq-intro');
    if (intro) intro.classList.toggle('open');
});


// Section Navigation
function _defaultSection() {
    // Land on the first area this account may actually open, so an admin
    // without the dashboard permission does not arrive on a blocked screen.
    if (_hasPerm('stats')) return 'dashboard';
    const first = Object.entries(SECTION_PERMISSION).find(([, p]) => _hasPerm(p));
    return first ? first[0] : 'dashboard';
}

function _isSectionAllowed(sectionName) {
    const role = _currentUser?.role || 'visitor';
    if (sectionName === 'users') return ['root', 'super_admin'].includes(role);
    const perm = SECTION_PERMISSION[sectionName];
    return perm ? _hasPerm(perm) : true;
}


function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    const isOpen  = sidebar.classList.toggle('open');
    overlay.classList.toggle('open', isOpen);
    document.body.style.overflow = isOpen ? 'hidden' : '';
}

function closeSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    sidebar.classList.remove('open');
    overlay.classList.remove('open');
    document.body.style.overflow = '';
}

function showSection(sectionName) {
    // Guard: redirect to default section if not allowed for this role
    if (!_isSectionAllowed(sectionName)) {
        showSection(_defaultSection());
        return;
    }

    // Close mobile sidebar when navigating
    closeSidebar();

    stopOtpPolling();
    stopJobPolling();
    document.querySelectorAll('.section-content').forEach(el => {
        el.style.display = 'none';
    });
    const target = document.getElementById(`section-${sectionName}`);
    if (target) target.style.display = 'block';

    // Update sidebar active state
    document.querySelectorAll('.nav-item-link').forEach(el => el.classList.remove('active'));
    const navLink = document.getElementById(`nav-link-${sectionName}`) ||
                    (sectionName === 'users' ? document.getElementById('nav-users') : null);
    if (navLink) navLink.classList.add('active');
    _paintNavGroups();

    // Update topbar
    const meta = SECTION_META[sectionName] || {};
    const ttEl = document.getElementById('topbar-title');
    const tsEl = document.getElementById('topbar-subtitle');
    if (ttEl) ttEl.textContent = meta.title || sectionName;
    if (tsEl) tsEl.textContent = meta.subtitle || '';

    // Publish route + title
    _currentSection = sectionName;
    _setRoute(sectionName);
    const meta2 = SECTION_META[sectionName];
    document.title = 'SorinFlow — ' + (meta2 ? meta2.title : sectionName);

    // Load section data
    switch (sectionName) {
        case 'dashboard':  loadDashboard(); break;
        case 'properties': loadProperties(); break;
        case 'scraper':    loadJobs(); loadSchedules(); loadScraperAccounts(); _wireEstimateRefresh(); scheduleEstimate(); checkDivarSessionBanner(); startOtpPolling(); startJobPolling(); checkPhoneGate();
                           _initScraperDatePicker(); refreshDivarSessionCount();
                           setTimeout(restoreScraperForm, 200); break;
        case 'auth':       checkAuthStatus(); loadCookies(); loadNumbersRegistry(); checkPhoneGate(); break;
        case 'forwarder':  loadForwarders(); loadForwarderLog(); checkPhoneGate(); break;
        case 'profile':    loadProfile(); break;
        case 'proxies':    loadProxies(); break;
        case 'crm':        _applyCrmRoleVisibility(); loadCalls(); loadMatches(); loadPriceDrops(); break;
        case 'insights':   insTab(_insTab); break;
        case 'portal':     loadPortalRequests(); break;
        case 'monitoring': loadMonitoring(); loadClientErrors(); break;
        case 'ai':         if (['root', 'super_admin'].includes(_currentUser?.role)) {
                               loadAiScreen(); loadAi(); loadAiLog(); loadAiChats();
                           } break;
        case 'sms':        loadSms(); break;
        case 'email':      _applyCrmRoleVisibility(); loadEmail(); break;
        case 'users':      if (['root', 'super_admin'].includes(_currentUser?.role)) {
                               loadUsers(); loadMaintenance(); loadBackup(); initPermsUI(); loadTickets();
                           } break;
    }
}

// Toast Notification
function showToast(title, message, type = 'info') {
    const toast = document.getElementById('toast');
    const toastTitle = document.getElementById('toast-title');
    const toastMessage = document.getElementById('toast-message');
    
    toastTitle.textContent = title;
    toastMessage.textContent = message;
    
    toast.className = `toast bg-${type} text-white`;
    
    const bsToast = new bootstrap.Toast(toast);
    bsToast.show();
}


// A URL from the database is not safe to put in href/src just because it is
// escaped: «javascript:alert(1)» contains nothing that needs escaping, and
// clicking the link (or, for some tags, even loading it) runs it in the
// panel's origin — where the token lives. Only http(s) and our own relative
// paths survive; anything else becomes an inert '#'.
function safeUrl(u) {
    const raw = String(u ?? '').trim();
    // "/\evil.com" is read by browsers as "//evil.com": another site
    if (!/^https?:\/\//i.test(raw) && !/^\/(?![\/\\])/.test(raw)) return '#';
    return esc(raw);
}

// The manual lead photo is narrower still: it only ever needs to reproduce
// exactly what POST /crm/upload-image hands back, so anything else —
// including a path-traversal attempt riding along in the field — becomes
// an empty (broken-image, harmless) src instead of '#'.
const MANUAL_PHOTO_RE = /^\/images\/manual\/[0-9a-f]{32}\.jpg$/;
function safeManualPhoto(u) {
    const raw = String(u ?? '');
    return MANUAL_PHOTO_RE.test(raw) ? raw : '';
}

// tel: has the same problem in a smaller way. Phone numbers here come from
// scraped ads and hand-typed forms, so reduce to what a dialler can use.
function safeTel(p) {
    return esc(String(p ?? '').replace(/[^\d+]/g, ''));
}

// Format Numbers (Persian) — 0 is a real value, only null/undefined mean "no data"
function formatNumber(num) {
    if (num === null || num === undefined || isNaN(num)) return '---';
    return new Intl.NumberFormat('fa-IR').format(num);
}

// کد ملک is an identifier, not a quantity: «۱۰۴۲», never «۱٬۰۴۲». Grouping it
// also stops the displayed code from matching what you type into search.
function formatSerial(num) {
    if (num === null || num === undefined || isNaN(num)) return '—';
    return new Intl.NumberFormat('fa-IR', { useGrouping: false }).format(num);
}

// Normalize tags → array. Backend stores them as a comma-separated string,
// but older/imported rows may already be an array (or null).
function _tagList(tags) {
    if (Array.isArray(tags)) return tags.filter(Boolean);
    if (typeof tags === 'string') return tags.split(',').map(t => t.trim()).filter(Boolean);
    return [];
}

// Escape user/scraped content before injecting into innerHTML templates.
// Covers both text and attribute context (& < > " ') plus the backtick, so
// an escaped value can never close out of either an HTML attribute or a
// template literal it ends up quoted inside of.
function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"'`]/g, m =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;', '`': '&#96;' }[m]));
}

// html`...` — a tagged template that escapes every interpolated value by
// default, so a call site has to opt out on purpose instead of forgetting
// to opt in. Existing code mostly calls esc() by hand inline; this is for
// new/rewritten spots where that got missed often enough to matter. Wrap
// markup that is already safe (built from esc()'d parts, or another html`` )
// in raw(...) — that keeps the one opt-out greppable as `raw(`.
// A value inside an inline handler — onclick="f(…)". The browser decodes the
// attribute's entities before it runs the code, so esc() alone let a quote
// through: a contact phone of  ');alert(1);('  broke out of quickSmsToContact.
// JSON makes it a JS string literal; esc keeps it inside the attribute.
function jsArg(v) { return esc(JSON.stringify(v == null ? '' : String(v))); }
function raw(s) { return { __html: String(s ?? '') }; }
function html(strings, ...values) {
    let out = strings[0];
    for (let i = 0; i < values.length; i++) {
        const v = values[i];
        out += (v && typeof v === 'object' && '__html' in v) ? v.__html : esc(v);
        out += strings[i + 1];
    }
    return out;
}

// Format Price — keeps one decimal so ۳٫۵ میلیارد doesn't round to ۴
function formatPrice(price) {
    if (price === null || price === undefined || isNaN(price) || price === 0) return '---';
    if (price >= 1000000000) {
        return formatNumber(Math.round(price / 100000000) / 10) + ' میلیارد';
    } else if (price >= 1000000) {
        return formatNumber(Math.round(price / 100000) / 10) + ' میلیون';
    }
    return formatNumber(price) + ' تومان';
}

// API Helper
async function apiCall(endpoint, options = {}) {
    try {
        return await _apiCallOnce(endpoint, options);
    } catch (error) {
        // «شماره‌ات را تأیید کن»: the server refused an action that leans on
        // the caller's own number. Open the verification popup — it sends the
        // code itself — and, once the number is verified, do what was asked,
        // so the click that was refused is the click that goes through.
        if (error.code === 'phone_unverified' && !options._phoneGateRetried) {
            const ok = await requirePhoneVerified(error.detail || {});
            if (ok) return await apiCall(endpoint, { ...options, _phoneGateRetried: true });
        }
        console.error('API Error:', error);
        throw error;
    }
}

async function _apiCallOnce(endpoint, options = {}) {
    const token = getToken();
    // `raw` returns the body as text instead of parsing JSON — the email
    // template preview is an HTML document, and it still has to go through
    // here so the Authorization header travels with it.
    const { raw, _phoneGateRetried, ...fetchOptions } = options;
    const response = await fetch(`${API_BASE}${endpoint}`, {
        headers: {
            'Content-Type': 'application/json',
            ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
            ...options.headers
        },
        ...fetchOptions
    });

    if (response.status === 401) {
        clearToken();
        showLoginPage();
        throw new Error('نشست منقضی شده. لطفاً دوباره وارد شوید.');
    }

    if (!response.ok) {
        // An error body is not always JSON — an HTML endpoint that fails
        // returns a page, and calling .json() on it throws a parse error
        // that hides the real status.
        let error = {};
        try { error = await response.json(); } catch (_) { error = {}; }
        let message = `Request failed (${response.status})`;
        if (error.detail) {
            if (typeof error.detail === 'string') {
                message = error.detail;
            } else if (Array.isArray(error.detail)) {
                message = error.detail.map(e => e.msg || JSON.stringify(e)).join(' | ');
            } else if (error.detail.message) {
                // A structured refusal: its sentence for people, its code
                // for the panel.
                message = error.detail.message;
            } else {
                message = JSON.stringify(error.detail);
            }
        }
        const err = new Error(message);
        err.status = response.status;
        err.detail = error.detail;
        err.code = error.detail && typeof error.detail === 'object' && !Array.isArray(error.detail)
            ? error.detail.code : undefined;
        throw err;
    }

    return raw ? await response.text() : await response.json();
}

/* ── the phone-verification popup ─────────────────────────────────────────
 *
 * «هر جایی که کاربر نیاز به استفاده از شماره را دارد و شماره‌اش را تأیید نکرده،
 * جلوی فعالیتش را بگیر… ارور «شماره تأیید نشده و باید تأیید شود» بده و با زدن
 * «تأیید شماره» کد برایش ارسال شود.» The server decides (require_verified_phone);
 * this is the door it points at. It says what is wrong first; the code is
 * texted only when they press «تأیید شماره» — never just for opening it.
 * Resolves true once the number is verified, false if they leave.
 * One popup at a time: two refused calls share the same answer.            */
let _phoneGatePromise = null;

function requirePhoneVerified(detail = {}) {
    if (_phoneGatePromise) return _phoneGatePromise;
    _phoneGatePromise = _openPhoneGate(detail).finally(() => { _phoneGatePromise = null; });
    return _phoneGatePromise;
}

/** Ask the server up front, on arriving somewhere that needs a verified
 *  number — once per page load, and never for root. */
let _phoneGateChecked = false;
async function checkPhoneGate() {
    if (_phoneGateChecked || !_currentUser || _currentUser.role === 'root') return;
    _phoneGateChecked = true;
    try {
        const g = await apiCall('/users/me/phone-gate');
        if (g.required) await requirePhoneVerified(g);
    } catch (_) { /* the action itself will still ask */ }
}

function _openPhoneGate(detail) {
    return new Promise(resolve => {
        const known = (detail.phone || _currentUser?.phone || '').trim();
        const overlay = document.createElement('div');
        overlay.className = 'ask-overlay';
        overlay.innerHTML = `
          <div class="ask-card is-warning pv-card" role="dialog" aria-modal="true" aria-label="تأیید شمارهٔ موبایل">
            <div class="ask-ring"><i class="bi bi-phone-vibrate"></i></div>
            <h5>تأیید شمارهٔ موبایل</h5>
            <p class="ask-body">${esc(detail.message || 'شمارهٔ موبایل شما تأیید نشده است و برای ادامه باید تأیید شود.')}</p>
            <div class="pv-intro d-none" id="pv-intro-step">
                <div class="pv-number" dir="ltr">${esc(known)}</div>
                <div class="ask-hint">با زدن «تأیید شماره» یک کد به همین شماره پیامک می‌شود.</div>
            </div>
            <div class="ask-field" id="pv-phone-step">
                <label for="pv-phone">شمارهٔ موبایل شما</label>
                <input id="pv-phone" type="tel" inputmode="tel" dir="ltr" placeholder="09123456789" value="${esc(known)}">
                <div class="ask-hint">کد تأیید به همین شماره پیامک می‌شود.</div>
            </div>
            <div class="ask-field d-none" id="pv-code-step">
                <label for="pv-code">کد تأیید پیامک‌شده</label>
                <input id="pv-code" type="text" inputmode="numeric" dir="ltr" maxlength="8" autocomplete="one-time-code" placeholder="—————">
                <div class="ask-hint" id="pv-hint"></div>
            </div>
            <div class="ask-error" id="pv-error"></div>
            <div class="ask-actions">
              <button class="ask-ok" id="pv-ok">ارسال کد</button>
              <button class="ask-cancel" id="pv-cancel">بعداً</button>
            </div>
            <div class="pv-links d-none" id="pv-links">
              <button type="button" class="btn btn-link btn-sm" id="pv-resend">ارسال دوباره کد</button>
              <button type="button" class="btn btn-link btn-sm" id="pv-change">شمارهٔ دیگری دارم</button>
            </div>
          </div>`;
        document.body.appendChild(overlay);
        const $ = id => overlay.querySelector('#' + id);
        const err = $('pv-error'), ok = $('pv-ok');
        let step = 'phone', busy = false;
        const showIntro = () => {
            step = 'intro';
            $('pv-phone-step').classList.add('d-none');
            $('pv-code-step').classList.add('d-none');
            $('pv-intro-step').classList.remove('d-none');
            $('pv-links').classList.remove('d-none');
            $('pv-resend').classList.add('d-none');
            ok.textContent = 'تأیید شماره';
            setTimeout(() => ok.focus(), 30);
        };

        const close = value => {
            if (overlay.dataset.closing) return;
            overlay.dataset.closing = '1';
            document.removeEventListener('keydown', onKey);
            overlay.remove();
            resolve(value);
        };
        const showCode = (hint) => {
            step = 'code';
            $('pv-intro-step').classList.add('d-none');
            $('pv-phone-step').classList.add('d-none');
            $('pv-code-step').classList.remove('d-none');
            $('pv-links').classList.remove('d-none');
            $('pv-resend').classList.remove('d-none');
            ok.textContent = 'تأیید';
            $('pv-hint').textContent = hint || '';
            setTimeout(() => $('pv-code').focus(), 30);
        };
        const showPhone = () => {
            step = 'phone';
            $('pv-intro-step').classList.add('d-none');
            $('pv-code-step').classList.add('d-none');
            $('pv-links').classList.add('d-none');
            $('pv-phone-step').classList.remove('d-none');
            ok.textContent = 'ارسال کد';
            err.textContent = '';
            setTimeout(() => $('pv-phone').focus(), 30);
        };
        const send = async (phone) => {
            err.textContent = '';
            try {
                const r = await apiCall('/users/me/phone/request', {
                    method: 'POST', body: JSON.stringify(phone ? { phone } : {}) });
                if (r.verified) { _markPhoneVerified(); close(true); return; }
                if (_currentUser) { _currentUser.phone = r.phone || phone || _currentUser.phone; _currentUser.phone_verified = false; }
                showCode(`کد به ${r.phone || phone || known} پیامک شد.`);
            } catch (e) {
                // 429 on a resend is the cooldown: a code went out moments ago
                // and is still good — let them type it. On a NEW number it is
                // not: nothing was sent there, and the number did not change.
                if (e.status === 429 && !phone) {
                    showCode(e.message);
                } else {
                    err.textContent = e.message;
                    if (step === 'code') showPhone();
                }
            }
        };
        const submit = async () => {
            if (busy) return;
            busy = true; ok.disabled = true;
            try {
                if (step === 'intro') {
                    await send(null);
                } else if (step === 'phone') {
                    const phone = _digitsOnly($('pv-phone').value);
                    const norm = phone.startsWith('98') && phone.length === 12 ? '0' + phone.slice(2)
                        : phone.length === 10 && phone.startsWith('9') ? '0' + phone : phone;
                    if (!/^09\d{9}$/.test(norm)) { err.textContent = 'شماره را مثل 09123456789 بنویسید'; return; }
                    await send(norm === _digitsOnly(known) ? null : norm);
                } else {
                    const code = _digitsOnly($('pv-code').value);
                    if (code.length < 4) { err.textContent = 'کد کامل نیست'; return; }
                    try {
                        await apiCall('/users/me/phone/verify', { method: 'POST', body: JSON.stringify({ code }) });
                        _markPhoneVerified();
                        showToast('تأیید شد', 'شمارهٔ موبایل شما تأیید شد', 'success');
                        close(true);
                    } catch (e) { err.textContent = e.message; $('pv-code').select(); }
                }
            } finally { busy = false; ok.disabled = false; }
        };
        const onKey = e => {
            if (e.key === 'Escape') close(false);
            // Only from a field: Enter on «بعداً» must not send a code. (The
            // intro's «تأیید شماره» is a button and answers its own click.)
            if (e.key === 'Enter' && e.target && e.target.tagName === 'INPUT') submit();
        };
        ok.addEventListener('click', submit);
        $('pv-cancel').addEventListener('click', () => close(false));
        $('pv-change').addEventListener('click', showPhone);
        $('pv-resend').addEventListener('click', async e => {
            const b = e.currentTarget; b.disabled = true;
            await send(null);
            setTimeout(() => { b.disabled = false; }, 60000);
        });
        document.addEventListener('keydown', onKey);

        // A number on file: say it is unverified and offer «تأیید شماره»;
        // the SMS goes when they press it. No number: ask for one.
        if (known) showIntro();
        else setTimeout(() => $('pv-phone').focus(), 30);
    });
}

function _markPhoneVerified() {
    if (_currentUser) _currentUser.phone_verified = true;
    if (typeof loadPhoneState === 'function') { try { loadPhoneState(); } catch (_) {} }
}

// ==================== Dashboard ====================

// Animated count-up for stat tiles (first paint only; refreshes just set text)
let _dashCounted = false;
function _setStat(id, val) {
    const el = document.getElementById(id);
    if (!el) return;
    if (val === null || val === undefined || isNaN(val)) { el.textContent = '۰'; return; }
    if (_dashCounted) { el.textContent = formatNumber(val); return; }
    const t0 = performance.now(), dur = 900;
    (function tick(t) {
        const p = Math.min((t - t0) / dur, 1), k = 1 - Math.pow(1 - p, 3);
        el.textContent = formatNumber(Math.round(val * k));
        if (p < 1) requestAnimationFrame(tick);
    })(t0);
}

function _updateWelcomeBanner() {
    const g = document.getElementById('wb-greeting');
    if (g && _currentUser) {
        g.textContent = `سلام، ${_currentUser.full_name || _currentUser.username} 👋`;
    }
    const d = document.getElementById('wb-date');
    if (d) {
        d.textContent = new Date().toLocaleDateString('fa-IR',
            { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
    }
}

async function loadDashboard() {
    _updateWelcomeBanner();
    try {
        // Isolation is enforced server-side — no need to pass owner_phone manually
        const [stats, health] = await Promise.all([
            apiCall('/stats/dashboard'),
            apiCall('/stats/health')
        ]);

        // Update stats
        _setStat('stat-total-properties', stats.total_properties);
        _setStat('stat-with-phone', stats.properties_with_phone);
        _setStat('stat-today', stats.properties_today);
        _setStat('stat-active-jobs', stats.active_jobs);
        _dashCounted = true;
        
        // Update health
        updateHealthStatus('health-db', health.database);
        updateHealthStatus('health-redis', health.redis);
        updateHealthStatus('health-scraper', health.scraper);
        updateHealthStatus('health-cookie', health.cookie_status);
        
        // Update charts
        updateCityChart(stats.city_distribution);
        updateTrendChart(stats.daily_scraping);

    } catch (error) {
        showToast('خطا', 'بارگیری داشبورد ناموفق بود', 'danger');
    }

    _loadDashboardWidgets();
    loadUpcomingEvents();
    _loadToday();
}


// ── «امروز» — the only part of the dashboard that asks for something back ────

function goCrm(tab, scrollTo) {
    showSection('crm');
    setTimeout(() => {
        document.querySelector(`[data-bs-target="#crm-tab-${tab}"]`)?.click();
        if (scrollTo) {
            setTimeout(() => document.getElementById(scrollTo)
                ?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 150);
        }
    }, 250);
}

async function _loadToday() {
    const note = document.getElementById('today-note');
    let t;
    try {
        t = await apiCall('/stats/today');
    } catch (e) {
        if (note) note.textContent = 'خوانده نشد';
        return;
    }
    const put = (id, n, card) => {
        const el = document.getElementById(id);
        if (el) el.textContent = formatNumber(n || 0);
        // Nothing waiting is worth seeing at a glance, so an empty card goes
        // quiet instead of sitting there looking like work.
        document.getElementById(card)?.classList.toggle('idle', !n);
    };
    put('today-calls', t.calls_due, 'todo-calls');
    put('today-matches', t.matches_waiting, 'todo-matches');
    put('today-drops', t.price_drops_new, 'todo-drops');
    put('today-new', t.listings_today, 'todo-new');

    const waiting = (t.calls_due || 0) + (t.matches_waiting || 0) + (t.price_drops_new || 0);
    if (note) {
        note.textContent = waiting
            ? `${formatNumber(waiting)} مورد منتظر شماست`
            : 'چیزی معطل نمانده — کارتان تمام است';
        note.classList.toggle('clear', !waiting);
    }
}

// ── Latest-activity widgets (recent properties & leads) ──
async function _loadDashboardWidgets() {
    const propsEl = document.getElementById('dash-latest-props');
    if (propsEl) {
        try {
            const data = await apiCall('/properties?page=1&size=5');
            propsEl.innerHTML = data.items.length ? data.items.map(p => `
                <div class="mini-item" onclick="viewProperty(${p.id})">
                    <div class="mi-ico"><i class="bi bi-house-door"></i></div>
                    <div class="mi-body">
                        <div class="mi-title">${esc(p.title) || '---'}</div>
                        <div class="mi-sub">${p.city_name || '---'}${p.area ? ' · ' + formatNumber(p.area) + ' متر' : ''}${p.rooms != null ? ' · ' + formatNumber(p.rooms) + ' خواب' : ''}</div>
                    </div>
                    <span class="mi-tag">${formatPrice(p.total_price || p.price || p.rent_price)}</span>
                </div>`).join('')
                : '<div class="mini-empty">هنوز ملکی اسکرپ نشده — از بخش اسکرپر شروع کنید</div>';
        } catch (e) {
            propsEl.innerHTML = '<div class="mini-empty">بارگیری ناموفق بود</div>';
        }
    }

    const leadsEl = document.getElementById('dash-latest-leads');
    if (leadsEl) {
        try {
            const data = await apiCall('/crm/leads?limit=5');
            leadsEl.innerHTML = data.items.length ? data.items.map(l => {
                const st = CRM_STATUS_LABELS[l.status] || { label: esc(l.status), cls: 'bg-secondary' };
                return `
                <div class="mini-item" onclick="viewLead(${l.id})">
                    <div class="mi-ico"><i class="bi bi-person"></i></div>
                    <div class="mi-body">
                        <div class="mi-title">${esc(l.property_title) || '---'}</div>
                        <div class="mi-sub">${l.city_name || '---'}${l.phone_number ? ' · ' + l.phone_number : ''}</div>
                    </div>
                    <span class="badge ${st.cls}">${st.label}</span>
                </div>`;
            }).join('')
                : '<div class="mini-empty">هنوز لیدی ثبت نشده</div>';
        } catch (e) {
            leadsEl.innerHTML = '<div class="mini-empty">بارگیری ناموفق بود</div>';
        }
    }
}

function updateHealthStatus(elementId, status) {
    const element = document.getElementById(elementId);
    let badgeClass = 'bg-success';
    let text = status;
    
    if (status.includes('unhealthy') || status.includes('expired') || status === 'no session') {
        badgeClass = 'bg-danger';
    } else if (status.includes('degraded') || status.includes('unavailable')) {
        badgeClass = 'bg-warning';
    }
    
    element.className = `badge ${badgeClass}`;
    element.textContent = text;
}

// ── Chart plugins: neon glow + doughnut center text ──
const _sfGlow = {
    id: 'sfGlow',
    beforeDatasetsDraw(chart, args, opts) {
        chart.ctx.save();
        chart.ctx.shadowColor = (opts && opts.color) || 'rgba(167,139,250,.55)';
        chart.ctx.shadowBlur = (opts && opts.blur) || 18;
    },
    afterDatasetsDraw(chart) { chart.ctx.restore(); }
};
const _sfCenter = {
    id: 'sfCenter',
    afterDraw(chart, args, opts) {
        if (!opts || !opts.big) return;
        const meta = chart.getDatasetMeta(0);
        if (!meta || !meta.data || !meta.data.length) return;
        const { x, y } = meta.data[0];
        const ctx = chart.ctx;
        ctx.save();
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.font = `800 26px ${FA_FONT}`;
        ctx.fillStyle = opts.color || '#fff';
        ctx.fillText(opts.big, x, y - 9);
        ctx.font = `500 12px ${FA_FONT}`;
        ctx.fillStyle = opts.subColor || '#8f96a8';
        ctx.fillText(opts.sub || '', x, y + 17);
        ctx.restore();
    }
};
const _sfTooltip = themeC => ({
    rtl: true, textDirection: 'rtl',
    backgroundColor: 'rgba(12,12,20,.92)',
    borderColor: 'rgba(167,139,250,.35)', borderWidth: 1,
    titleFont: { family: FA_FONT, weight: '700' },
    bodyFont: { family: FA_FONT },
    padding: 12, cornerRadius: 12, displayColors: false,
});

function updateCityChart(data) {
    const ctx = document.getElementById('cityChart').getContext('2d');
    if (cityChart) cityChart.destroy();

    const themeC = chartColors();
    const total = data.reduce((s, d) => s + d.count, 0);

    cityChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: data.map(d => d.city),
            datasets: [{
                data: data.map(d => d.count),
                backgroundColor: [
                    '#a78bfa','#f0a6ff','#67e8f9','#6366f1','#fcd34d',
                    '#fb7185','#8b5cf6','#2dd4bf','#ec4899','#64748b'
                ],
                borderColor: themeC.surface,
                borderWidth: 0,
                borderRadius: 10,
                spacing: 4,
                hoverOffset: 16,
            }]
        },
        plugins: [_sfGlow, _sfCenter],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '74%',
            animation: { duration: 1100, easing: 'easeOutQuart', animateRotate: true },
            plugins: {
                sfGlow: { color: 'rgba(167,139,250,.4)', blur: 22 },
                sfCenter: {
                    big: formatNumber(total), sub: 'ملک ثبت‌شده',
                    color: themeC.text === '#475569' ? '#1e2740' : '#f2f3f8',
                    subColor: themeC.text,
                },
                legend: {
                    position: 'right',
                    labels: {
                        color: themeC.text, font: { family: FA_FONT, size: 12 },
                        usePointStyle: true, pointStyle: 'circle', boxWidth: 8, padding: 14,
                    }
                },
                tooltip: {
                    ..._sfTooltip(themeC),
                    callbacks: {
                        label: c => ` ${formatNumber(c.parsed)} ملک (${formatNumber(Math.round(c.parsed * 100 / total))}٪)`
                    }
                }
            }
        }
    });
}

function updateTrendChart(data) {
    const canvas = document.getElementById('trendChart');
    const ctx = canvas.getContext('2d');
    if (trendChart) trendChart.destroy();

    const themeC = chartColors();

    // holographic vertical gradient under the line
    const h = canvas.parentElement?.clientHeight || 260;
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, 'rgba(240,166,255,.34)');
    grad.addColorStop(.5, 'rgba(167,139,250,.14)');
    grad.addColorStop(1, 'rgba(103,232,249,.02)');

    const labels = data.map(d => {
        const dt = new Date(d.date);
        return isNaN(dt) ? d.date
            : dt.toLocaleDateString('fa-IR', { day: 'numeric', month: 'long' });
    });

    trendChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'تعداد اسکرپ',
                data: data.map(d => d.count),
                borderColor: '#c4a5fc',
                backgroundColor: grad,
                pointBackgroundColor: '#f0a6ff',
                pointBorderColor: themeC.surface,
                pointRadius: 0,
                pointHoverRadius: 7,
                pointHoverBorderWidth: 3,
                borderWidth: 3.5,
                fill: true,
                tension: 0.45,
            }]
        },
        plugins: [_sfGlow],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            animation: { duration: 1300, easing: 'easeOutQuart' },
            plugins: {
                sfGlow: { color: 'rgba(196,165,252,.5)', blur: 14 },
                legend: { display: false },
                tooltip: {
                    ..._sfTooltip(themeC),
                    callbacks: { label: c => ` ${formatNumber(c.parsed.y)} آگهی اسکرپ شد` }
                }
            },
            scales: {
                x: {
                    grid: { color: 'transparent' },
                    border: { display: false },
                    ticks: { color: themeC.tick, font: { family: FA_FONT, size: 11 }, maxRotation: 0 }
                },
                y: {
                    beginAtZero: true,
                    grid: { color: themeC.grid, tickBorderDash: [4, 5] },
                    border: { display: false, dash: [4, 5] },
                    ticks: {
                        color: themeC.tick, font: { family: FA_FONT, size: 11 },
                        callback: v => formatNumber(v), maxTicksLimit: 6, padding: 8,
                    }
                }
            }
        }
    });
}

// ==================== Properties ====================

// Type (buy/rent) is implied by the selected category
function _selectedCategoryType() {
    const sel = document.getElementById('filter-category');
    if (!sel || !sel.value) return '';
    return sel.selectedOptions[0]?.dataset.type || '';
}

function onFilterCategoryChange() {
    const rentFilters = document.getElementById('rent-filters');
    rentFilters.classList.toggle('d-none', _selectedCategoryType() !== 'rent');
}

async function loadProperties() {
    const search = document.getElementById('search-properties').value;
    const city   = document.getElementById('filter-city-hidden')?.value || '';
    const category = document.getElementById('filter-category')?.value || '';
    const type = _selectedCategoryType();
    const minDeposit = document.getElementById('filter-min-deposit').value;
    const maxDeposit = document.getElementById('filter-max-deposit').value;
    const minRent = document.getElementById('filter-min-rent').value;
    const maxRent = document.getElementById('filter-max-rent').value;

    try {
        let url = `/properties?page=${currentPage}&size=20`;
        if (search) url += `&search=${encodeURIComponent(search)}`;
        if (city) url += `&city=${encodeURIComponent(city)}`;
        if (type === 'buy' || type === 'rent') url += `&listing_type=${type}`;
        if (category) url += `&category=${encodeURIComponent(category)}`;
        if (minDeposit) url += `&min_deposit=${minDeposit}`;
        if (maxDeposit) url += `&max_deposit=${maxDeposit}`;
        if (minRent) url += `&min_rent_price=${minRent}`;
        if (maxRent) url += `&max_rent_price=${maxRent}`;

        // Isolation is enforced server-side via current_user.divar_phone
        
        const data = await apiCall(url);
        
        const tbody = document.getElementById('properties-table');
        tbody.innerHTML = '';
        
        if (data.items.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" class="text-center text-muted py-4">
                        <i class="bi bi-inbox" style="font-size: 2rem;"></i>
                        <p class="mt-2">هیچ ملکی یافت نشد</p>
                    </td>
                </tr>
            `;
            return;
        }
        
        data.items.forEach(property => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td data-l="کد"><span class="serial-badge">${formatSerial(property.serial_no)}</span></td>
                <td data-l="عنوان" class="pt-title" title="${esc(property.title)}">${esc(
                    // «…» only when something was actually cut off; it used to
                    // be appended to every title, short ones included.
                    property.title.length > 40 ? property.title.slice(0, 40) + '…' : property.title
                )} ${agencyBadge(property)}</td>
                <td data-l="شهر">${property.city_name || '---'}</td>
                <td data-l="متراژ">${formatNumber(property.area)} متر</td>
                <td data-l="اتاق">${property.rooms != null ? formatNumber(property.rooms) : '---'}</td>
                <td data-l="قیمت" class="pt-price">
                    ${property.listing_type === 'rent'
                        ? `<small class="d-block text-muted">رهن: ${formatPrice(property.deposit)}</small><small class="d-block">اجاره: ${formatPrice(property.rent_price)}</small>`
                        : formatPrice(property.total_price || property.price)
                    }
                </td>
                <td data-l="شماره تماس" class="pt-phone">
                    ${property.phone_number 
                        ? `<a href="tel:${safeTel(property.phone_number)}" class="text-success">${property.phone_number}</a>`
                        : noPhoneCell(property)
                    }
                </td>
                <td data-l="" class="pt-actions">
                    <button class="btn btn-sm btn-outline-primary" onclick="viewProperty(${property.id})">
                        <i class="bi bi-eye"></i>
                    </button>
                    <a href="${safeUrl(property.url)}" target="_blank" class="btn btn-sm btn-outline-secondary">
                        <i class="bi bi-box-arrow-up-left"></i>
                    </a>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteProperty(${property.id})">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(row);
        });
        
        // Update pagination
        updatePagination(data.page, data.pages);
        
    } catch (error) {
        showToast('خطا', 'بارگیری لیست املاک ناموفق بود', 'danger');
    }
}

function updatePagination(current, total) {
    const pagination = document.getElementById('properties-pagination');
    pagination.innerHTML = '';
    if (total <= 1) return;

    const add = (label, page, opts = {}) => {
        const li = document.createElement('li');
        li.className = `page-item ${opts.active ? 'active' : ''} ${opts.disabled ? 'disabled' : ''}`;
        li.innerHTML = opts.gap
            ? `<span class="page-link">…</span>`
            : `<a class="page-link" href="#" onclick="goToPage(${page}); return false;">${label}</a>`;
        pagination.appendChild(li);
    };

    add('‹', Math.max(current - 1, 1), { disabled: current === 1 });
    const win = 2;
    let last = 0;
    for (let i = 1; i <= total; i++) {
        if (i === 1 || i === total || Math.abs(i - current) <= win) {
            if (i - last > 1) add('', 0, { gap: true });
            add(formatNumber(i), i, { active: i === current });
            last = i;
        }
    }
    add('›', Math.min(current + 1, total), { disabled: current === total });
}

function goToPage(page) {
    currentPage = page;
    loadProperties();
}

async function viewProperty(id) {
    try {
        const property = await apiCall(`/properties/${id}`);
        
        const modal = document.getElementById('property-detail');
        modal.innerHTML = `
            <div class="property-detail">
                ${property.images && property.images.length > 0 ? `
                    <div class="mb-3">
                        <div id="propertyCarousel" class="carousel slide" data-bs-ride="carousel">
                            <div class="carousel-inner">
                                ${property.images.map((img, idx) => `
                                    <div class="carousel-item ${idx === 0 ? 'active' : ''}">
                                        <img src="${safeUrl(img)}" class="d-block w-100 rounded" alt="تصویر ${idx + 1}"
                                             style="max-height: 400px; object-fit: cover;"
                                             onclick="openImageLightbox(this.src)" title="کلیک برای بزرگ‌نمایی">
                                    </div>
                                `).join('')}
                            </div>
                            ${property.images.length > 1 ? `
                                <button class="carousel-control-prev" type="button" data-bs-target="#propertyCarousel" data-bs-slide="prev">
                                    <span class="carousel-control-prev-icon" aria-hidden="true"></span>
                                </button>
                                <button class="carousel-control-next" type="button" data-bs-target="#propertyCarousel" data-bs-slide="next">
                                    <span class="carousel-control-next-icon" aria-hidden="true"></span>
                                </button>
                            ` : ''}
                        </div>
                        <p class="text-center text-muted mt-2 small">
                            <i class="bi bi-images"></i> ${property.images.length} تصویر
                        </p>
                    </div>
                ` : '<div class="alert alert-secondary text-center mb-3"><i class="bi bi-image"></i> بدون تصویر</div>'}
                <!-- what the vision model saw in the first photos (js/ai/photo.js); empty stays hidden -->
                <div class="ai-photo" id="ai-photo-${property.id}"></div>
                
                <h5 class="mb-3">${esc(property.title)} ${typeof aiDuplicateBadge === 'function' ? aiDuplicateBadge(property) : ''}</h5>
                
                <!-- Basic Info -->
                <div class="card mb-3">
                    <div class="card-header bg-primary text-white">
                        <i class="bi bi-info-circle"></i> اطلاعات پایه
                    </div>
                    <div class="card-body">
                        <div class="row g-3">
                            <div class="col-md-6">
                                <label class="text-muted small">کد ملک</label>
                                <div><span class="serial-badge" style="font-size:1rem">${formatSerial(property.serial_no)}</span>
                                     <code class="ms-2 text-muted" style="font-size:.72rem">${property.tag_number}</code></div>
                            </div>
                            <div class="col-md-6">
                                <label class="text-muted small">شناسه دیوار</label>
                                <div><code>${property.divar_id}</code></div>
                            </div>
                            <div class="col-md-6">
                                <label class="text-muted small">نوع آگهی</label>
                                <div>${property.listing_type === 'buy' ? '🏷️ خرید' : '📋 اجاره'}</div>
                            </div>
                            <div class="col-md-6">
                                <label class="text-muted small">نوع ملک</label>
                                <div>${property.property_type || '---'}</div>
                            </div>
                            <div class="col-md-6">
                                <label class="text-muted small">دسته‌بندی</label>
                                <div>${property.category_name || '---'}</div>
                            </div>
                            <div class="col-md-6">
                                <label class="text-muted small">دارای تصویر</label>
                                <div>${property.has_images ? '✅ بله' : '❌ خیر'}</div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Price Info -->
                <div class="card mb-3">
                    <div class="card-header bg-success text-white">
                        <i class="bi bi-currency-exchange"></i> اطلاعات قیمت
                    </div>
                    <div class="card-body">
                        <div class="row g-3">
                            ${property.total_price ? `
                                <div class="col-md-6">
                                    <label class="text-muted small">قیمت کل</label>
                                    <div class="h5 text-success mb-0">${formatPrice(property.total_price)}</div>
                                </div>
                            ` : ''}
                            ${property.price_per_meter ? `
                                <div class="col-md-6">
                                    <label class="text-muted small">قیمت هر متر</label>
                                    <div class="h5 text-info mb-0">${formatPrice(property.price_per_meter)}</div>
                                </div>
                            ` : ''}
                            ${property.rent_price ? `
                                <div class="col-md-6">
                                    <label class="text-muted small">اجاره ماهانه</label>
                                    <div class="h5 text-warning mb-0">${formatPrice(property.rent_price)}</div>
                                </div>
                            ` : ''}
                            ${property.deposit ? `
                                <div class="col-md-6">
                                    <label class="text-muted small">ودیعه</label>
                                    <div class="h5 text-primary mb-0">${formatPrice(property.deposit)}</div>
                                </div>
                            ` : ''}
                        </div>
                    </div>
                </div>
                
                <!-- Property Details -->
                <div class="card mb-3">
                    <div class="card-header bg-info text-white">
                        <i class="bi bi-house-door"></i> مشخصات ملک
                    </div>
                    <div class="card-body">
                        <div class="row g-3">
                            <div class="col-md-4">
                                <label class="text-muted small">متراژ</label>
                                <div><strong>${property.area ? formatNumber(property.area) + ' متر' : '---'}</strong></div>
                            </div>
                            <div class="col-md-4">
                                <label class="text-muted small">متراژ زمین</label>
                                <div>${property.land_area ? formatNumber(property.land_area) + ' متر' : '---'}</div>
                            </div>
                            <div class="col-md-4">
                                <label class="text-muted small">زیربنا</label>
                                <div>${property.built_area ? formatNumber(property.built_area) + ' متر' : '---'}</div>
                            </div>
                            <div class="col-md-4">
                                <label class="text-muted small">تعداد اتاق</label>
                                <div><strong>${property.rooms !== null && property.rooms !== undefined ? formatNumber(property.rooms) : '---'}</strong></div>
                            </div>
                            <div class="col-md-4">
                                <label class="text-muted small">طبقه</label>
                                <div>${property.floor !== null && property.floor !== undefined ? formatNumber(property.floor) : '---'}</div>
                            </div>
                            <div class="col-md-4">
                                <label class="text-muted small">کل طبقات</label>
                                <div>${property.total_floors ? formatNumber(property.total_floors) : '---'}</div>
                            </div>
                            <div class="col-md-6">
                                <label class="text-muted small">سال ساخت</label>
                                <div>${property.year_built ? formatNumber(property.year_built) : '---'}</div>
                            </div>
                            <div class="col-md-6">
                                <label class="text-muted small">سن بنا</label>
                                <div>${property.building_age || '---'}</div>
                            </div>
                            <div class="col-md-6">
                                <label class="text-muted small">جهت ساختمان</label>
                                ${_propertyFieldSelect(property.id, 'building_direction', property.building_direction, DIRECTION_OPTIONS, 'جهت ساختمان')}
                            </div>
                            <div class="col-md-6">
                                <label class="text-muted small">نبش</label>
                                ${_propertyFieldSelect(property.id, 'corner_type', property.corner_type, CORNER_OPTIONS, 'نبش')}
                            </div>
                            <div class="col-md-6">
                                <label class="text-muted small">بر (متر)</label>
                                <div>${property.frontage ? formatNumber(property.frontage) + ' متر' : '---'}</div>
                            </div>
                            <div class="col-md-6">
                                <label class="text-muted small">وضعیت واحد</label>
                                <div>${property.unit_status || '---'}</div>
                            </div>
                            <div class="col-md-6">
                                <label class="text-muted small">نوع سند</label>
                                <div>${property.document_type || '---'}</div>
                            </div>
                            <div class="col-md-6">
                                <label class="text-muted small">نوع کاربری</label>
                                <div>${property.usage_type || '---'}</div>
                            </div>
                        </div>
                    </div>
                </div>
                
                ${Object.keys(property.extra_attrs || {}).length ? `
                <!-- Extra structured attributes (manual leads) -->
                <div class="card mb-3">
                    <div class="card-header bg-secondary text-white">
                        <i class="bi bi-list-columns"></i> مشخصات تکمیلی
                    </div>
                    <div class="card-body">
                        <div class="row g-3">
                            ${Object.entries(property.extra_attrs).map(([k, v]) => `
                                <div class="col-md-4">
                                    <label class="text-muted small">${LEAD_ATTR_FA[k] || esc(k)}</label>
                                    <div>${esc(v)}</div>
                                </div>`).join('')}
                        </div>
                    </div>
                </div>` : ''}

                <!-- برداشت هوش مصنوعی: what the reader found in the ad's own text (js/ai/reader.js) -->
                <div id="ai-facts" class="ai-facts" data-id="${property.id}" data-read-at="${property.ai_read_at || ''}"></div>

                <!-- Location -->
                <div class="card mb-3">
                    <div class="card-header bg-warning text-dark">
                        <i class="bi bi-geo-alt"></i> موقعیت مکانی
                    </div>
                    <div class="card-body">
                        <div class="row g-3">
                            <div class="col-md-4">
                                <label class="text-muted small">شهر</label>
                                <div><strong>${property.city_name || '---'}</strong></div>
                            </div>
                            <div class="col-md-4">
                                <label class="text-muted small">منطقه</label>
                                <div>${esc(property.district) || '---'}</div>
                            </div>
                            <div class="col-md-4">
                                <label class="text-muted small">محله</label>
                                <div>${esc(property.neighborhood) || '---'}</div>
                            </div>
                            ${property.address ? `
                                <div class="col-12">
                                    <label class="text-muted small">آدرس</label>
                                    <div>${esc(property.address)}</div>
                                </div>
                            ` : ''}
                            ${property.latitude && property.longitude ? `
                                <div class="col-12">
                                    <label class="text-muted small">مختصات جغرافیایی</label>
                                    <div>
                                        <a href="https://www.google.com/maps?q=${property.latitude},${property.longitude}" target="_blank" class="btn btn-sm btn-outline-primary">
                                            <i class="bi bi-map"></i> مشاهده در نقشه
                                        </a>
                                    </div>
                                </div>
                            ` : ''}
                        </div>
                    </div>
                </div>
                

                <!-- Description -->
                <div class="card mb-3">
                    <div class="card-header bg-dark text-white">
                        <i class="bi bi-card-text"></i> توضیحات
                    </div>
                    <div class="card-body">
                        ${property.description
                            ? `<pre style="white-space:pre-wrap;font-family:inherit;font-size:0.92rem;margin:0;line-height:1.7">${esc(property.description)}</pre>`
                            : '<span class="text-muted">---</span>'}
                    </div>
                </div>

                <!-- Contact -->
                <div class="card mb-3">
                    <div class="card-header bg-danger text-white">
                        <i class="bi bi-telephone"></i> اطلاعات تماس
                    </div>
                    <div class="card-body">
                        <div class="row g-3">
                            <div class="col-md-6">
                                <label class="text-muted small">شماره تماس</label>
                                <div class="h5 mb-0">
                                    ${property.phone_number
                                        ? `<a href="tel:${safeTel(property.phone_number)}" class="text-success">${esc(property.phone_number)}</a>`
                                        : noPhoneCell(property)}
                                </div>
                            </div>
                            <div class="col-md-6">
                                <label class="text-muted small">فروشنده</label>
                                <div>${esc(property.seller_name) || '---'}</div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Meta -->
                <div class="card mb-3">
                    <div class="card-body bg-light">
                        <div class="row g-2 small text-muted">
                            <div class="col-md-6">
                                <i class="bi bi-clock"></i> اسکرپ شده: ${property.scraped_at ? new Date(property.scraped_at).toLocaleString('fa-IR') : '---'}
                            </div>
                            <div class="col-md-6">
                                <i class="bi bi-pencil"></i> آخرین بروزرسانی: ${property.updated_at ? new Date(property.updated_at).toLocaleString('fa-IR') : '---'}
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Actions -->
                <div class="d-flex gap-2">
                    <a href="${safeUrl(property.url)}" target="_blank" class="btn btn-primary flex-grow-1">
                        <i class="bi bi-box-arrow-up-right"></i> مشاهده در دیوار
                    </a>
                    <button class="btn btn-match" onclick="showSimilarForProperty(${property.id})">
                        <i class="bi bi-diagram-3"></i> ملک‌های مشابه
                    </button>
                    <button class="btn btn-outline-danger" onclick="deleteProperty(${property.id}); bootstrap.Modal.getInstance(document.getElementById('propertyModal')).hide();">
                        <i class="bi bi-trash"></i> حذف
                    </button>
                </div>
            </div>
        `;
        
        // the AI pieces draw into their own boxes after the template is in place
        if (typeof aiRenderFacts === 'function') aiRenderFacts(document.getElementById('ai-facts'), property.ai_facts);
        if (typeof aiLoadPhotoTags === 'function' && ['root', 'super_admin'].includes(_currentUser?.role)) {
            aiLoadPhotoTags(property.id, document.getElementById(`ai-photo-${property.id}`));
        }

        const modalElement = new bootstrap.Modal(document.getElementById('propertyModal'));
        modalElement.show();
        
    } catch (error) {
        showToast('خطا', 'بارگیری جزئیات ملک ناموفق بود', 'danger');
    }
}

// ═══ Image lightbox (zoom / pan) ═══════════════════════════════
const _lb = { scale: 1, x: 0, y: 0, dragging: false, sx: 0, sy: 0 };

function _lbApply() {
    document.getElementById('img-lightbox-img').style.transform =
        `translate(${_lb.x}px, ${_lb.y}px) scale(${_lb.scale})`;
}

function openImageLightbox(src) {
    const box = document.getElementById('img-lightbox');
    const img = document.getElementById('img-lightbox-img');
    _lb.scale = 1; _lb.x = 0; _lb.y = 0;
    img.src = src;
    _lbApply();
    box.classList.add('open');
    document.body.style.overflow = 'hidden';
}

function closeImageLightbox() {
    document.getElementById('img-lightbox').classList.remove('open');
    document.getElementById('img-lightbox-img').src = '';
    document.body.style.overflow = '';
}

document.addEventListener('DOMContentLoaded', () => {
    const box = document.getElementById('img-lightbox');
    const img = document.getElementById('img-lightbox-img');
    if (!box || !img) return;

    box.addEventListener('wheel', e => {
        e.preventDefault();
        const factor = e.deltaY < 0 ? 1.2 : 1 / 1.2;
        _lb.scale = Math.min(8, Math.max(1, _lb.scale * factor));
        if (_lb.scale === 1) { _lb.x = 0; _lb.y = 0; }
        _lbApply();
    }, { passive: false });

    img.addEventListener('dblclick', () => {
        _lb.scale = _lb.scale > 1 ? 1 : 2.5;
        if (_lb.scale === 1) { _lb.x = 0; _lb.y = 0; }
        _lbApply();
    });

    img.addEventListener('pointerdown', e => {
        e.preventDefault();
        _lb.dragging = true; _lb.sx = e.clientX - _lb.x; _lb.sy = e.clientY - _lb.y;
        img.classList.add('dragging');
        img.setPointerCapture(e.pointerId);
    });
    img.addEventListener('pointermove', e => {
        if (!_lb.dragging) return;
        _lb.x = e.clientX - _lb.sx; _lb.y = e.clientY - _lb.sy;
        _lbApply();
    });
    img.addEventListener('pointerup', () => {
        _lb.dragging = false;
        img.classList.remove('dragging');
    });

    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && box.classList.contains('open')) closeImageLightbox();
    });
});

async function deleteProperty(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'آیا از حذف این ملک اطمینان دارید؟' })) return;
    
    try {
        await apiCall(`/properties/${id}`, { method: 'DELETE' });
        showToast('موفق', 'ملک با موفقیت حذف شد', 'success');
        loadProperties();
    } catch (error) {
        showToast('خطا', 'حذف ملک ناموفق بود', 'danger');
    }
}

async function exportProperties() {
    try {
        const city = document.getElementById('filter-city-hidden')?.value || '';
        const type = _selectedCategoryType();

        const data = await apiCall('/properties/export', {
            method: 'POST',
            body: JSON.stringify({ city, listing_type: (type === 'buy' || type === 'rent') ? type : '' })
        });
        
        // Download as JSON
        const blob = new Blob([JSON.stringify(data.data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'properties-export.json';
        a.click();
        
        showToast('موفق', 'فایل دانلود شد', 'success');
    } catch (error) {
        showToast('خطا', 'خروجی گرفتن ناموفق بود', 'danger');
    }
}

// ==================== Scraper ====================

// ═══ City Picker Component ════════════════════════════════════════════════
/**
 * initCityPicker(containerId, cities, opts)
 * Builds a searchable city picker inside `containerId`.
 *
 * opts.valueId   — id of the hidden <input> that stores the selected value
 * opts.useSlug   — true: store city.slug as value (scraper), false: city.name (filter)
 * opts.allLabel  — label for the "all" option (default: 'همه شهرها')
 * opts.allValue  — value for the "all" option (default: '')
 * opts.placeholder — search box placeholder
 * opts.onChange  — callback(value, label)
 */
function initCityPicker(containerId, cities, opts = {}) {
    const container  = document.getElementById(containerId);
    if (!container) return;

    const {
        valueId     = null,
        useSlug     = false,
        allLabel    = 'همه شهرها',
        allValue    = '',
        placeholder = 'جستجو در شهرها...',
        onChange    = null,
    } = opts;

    // Group cities by province
    const byProvince = {};
    cities.forEach(c => {
        const p = c.province || 'سایر';
        if (!byProvince[p]) byProvince[p] = [];
        byProvince[p].push(c);
    });
    const provinces = Object.keys(byProvince).sort();

    let selectedValue = allValue;
    let selectedLabel = allLabel;
    let focusedIndex  = -1;
    let flatFiltered  = [];

    // ── Build DOM ──────────────────────────────────
    container.innerHTML = `
      <div class="city-picker__trigger" tabindex="0">
        <i class="bi bi-geo-alt"></i>
        <span class="city-picker__label">${allLabel}</span>
        <i class="bi bi-chevron-down caret"></i>
      </div>
      <div class="city-picker__panel">
        <div class="city-picker__search-wrap">
          <i class="bi bi-search"></i>
          <input class="city-picker__search" type="text" placeholder="${placeholder}" autocomplete="off">
        </div>
        <div class="city-picker__list"></div>
      </div>`;

    const trigger   = container.querySelector('.city-picker__trigger');
    const panel     = container.querySelector('.city-picker__panel');
    const searchEl  = container.querySelector('.city-picker__search');
    const listEl    = container.querySelector('.city-picker__list');
    const labelEl   = container.querySelector('.city-picker__label');
    const hiddenEl  = valueId ? document.getElementById(valueId) : null;

    // ── Render list ────────────────────────────────
    function hl(text, q) {
        if (!q) return text;
        const idx = text.indexOf(q);
        if (idx === -1) return text;
        return text.slice(0, idx)
            + `<span class="city-picker__highlight">${text.slice(idx, idx + q.length)}</span>`
            + text.slice(idx + q.length);
    }

    function renderList(query = '') {
        listEl.innerHTML = '';
        focusedIndex = -1;

        // Always show "همه" row
        const allEl = document.createElement('div');
        allEl.className = 'city-picker__item city-picker__item--all' +
                          (selectedValue === allValue ? ' selected' : '');
        allEl.innerHTML = `<i class="bi bi-globe2"></i> ${allLabel}`;
        allEl.addEventListener('mousedown', () => pick(allValue, allLabel));
        listEl.appendChild(allEl);

        if (query) {
            // Flat filtered list
            flatFiltered = [];
            provinces.forEach(p => {
                byProvince[p].forEach(c => {
                    if (c.name.includes(query)) flatFiltered.push(c);
                });
            });

            if (flatFiltered.length === 0) {
                listEl.innerHTML += `<div class="city-picker__empty">شهری یافت نشد</div>`;
                return;
            }

            flatFiltered.forEach((c, i) => {
                const val = useSlug ? c.slug : c.name;
                const el  = document.createElement('div');
                el.className = 'city-picker__item' + (val === selectedValue ? ' selected' : '');
                el.dataset.idx = i;
                el.innerHTML   = `<span>${hl(c.name, query)}</span>`;
                el.addEventListener('mousedown', () => pick(val, c.name));
                listEl.appendChild(el);
            });
        } else {
            // Grouped by province
            flatFiltered = [];
            provinces.forEach(p => {
                const groupLabel = document.createElement('div');
                groupLabel.className = 'city-picker__group-label';
                groupLabel.textContent = p;
                listEl.appendChild(groupLabel);

                byProvince[p].forEach(c => {
                    const val = useSlug ? c.slug : c.name;
                    const idx = flatFiltered.push(c) - 1;
                    const el  = document.createElement('div');
                    el.className = 'city-picker__item' + (val === selectedValue ? ' selected' : '');
                    el.dataset.idx = idx;
                    el.textContent = c.name;
                    el.addEventListener('mousedown', () => pick(val, c.name));
                    listEl.appendChild(el);
                });
            });
        }
    }

    // ── Open / close ───────────────────────────────
    function open() {
        panel.classList.add('open');
        trigger.classList.add('open');
        searchEl.value = '';
        renderList('');
        // Scroll selected item into view
        setTimeout(() => {
            const sel = listEl.querySelector('.selected');
            if (sel) sel.scrollIntoView({ block: 'nearest' });
            searchEl.focus();
        }, 30);
    }

    function close() {
        panel.classList.remove('open');
        trigger.classList.remove('open');
        focusedIndex = -1;
    }

    // ── Select a city ──────────────────────────────
    function pick(val, label) {
        selectedValue = val;
        selectedLabel = label;
        labelEl.textContent = label;
        if (hiddenEl) hiddenEl.value = val;
        if (onChange) onChange(val, label);
        close();
    }

    // ── Events ─────────────────────────────────────
    trigger.addEventListener('click', () => panel.classList.contains('open') ? close() : open());
    trigger.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') open(); });

    searchEl.addEventListener('input', () => renderList(searchEl.value.trim()));

    // Keyboard nav in list
    searchEl.addEventListener('keydown', e => {
        const items = [...listEl.querySelectorAll('.city-picker__item')];
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            focusedIndex = Math.min(focusedIndex + 1, items.length - 1);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            focusedIndex = Math.max(focusedIndex - 1, 0);
        } else if (e.key === 'Enter' && focusedIndex >= 0) {
            e.preventDefault();
            items[focusedIndex]?.dispatchEvent(new MouseEvent('mousedown'));
            return;
        } else if (e.key === 'Escape') {
            close(); return;
        }
        items.forEach((el, i) => el.classList.toggle('focused', i === focusedIndex));
        items[focusedIndex]?.scrollIntoView({ block: 'nearest' });
    });

    // Close when clicking outside
    document.addEventListener('mousedown', e => {
        if (!container.contains(e.target)) close();
    });

    // Expose getter
    container._getCityValue = () => selectedValue;
    // programmatic restore (used by scraper form memory)
    container._setCityValue = (val) => {
        if (!val) return;
        for (const p of provinces) {
            const c = byProvince[p].find(c => (useSlug ? c.slug : c.name) === val);
            if (c) { pick(val, c.name); return; }
        }
    };

    renderList('');
    return { getValue: () => selectedValue };
}

async function loadCities() {
    try {
        const resp = await apiCall('/scraper/cities');
        const cities = Array.isArray(resp) ? resp : (resp?.items || []);
        if (!cities.length) { console.warn('No cities returned'); return; }

        initCityPicker('scraper-city-picker', cities, {
            valueId:     'scraper-city',
            useSlug:     true,
            allLabel:    'انتخاب شهر...',
            allValue:    '',
            placeholder: 'جستجو در شهرها...',
        });

        initCityPicker('filter-city-picker', cities, {
            valueId:     'filter-city-hidden',
            useSlug:     false,
            allLabel:    'همه شهرها',
            allValue:    '',
            placeholder: 'جستجو...',
        });

        // مخاطب جدید: pick a city off the list instead of spelling it, so the
        // name matches what every other row stores and stays searchable
        initCityPicker('contact-city-picker', cities, {
            valueId:     'contact-city',
            useSlug:     false,
            allLabel:    'انتخاب شهر...',
            allValue:    '',
            placeholder: 'جستجو در شهرها...',
        });

    } catch (error) {
        console.error('Failed to load cities:', error);
    }
}

async function loadCategories() {
    try {
        const _catResp = await apiCall('/scraper/categories');
        const categories = Array.isArray(_catResp) ? _catResp : (_catResp?.items || []);

        const select = document.getElementById('scraper-category');
        categories.forEach(cat => {
            select.innerHTML += `<option value="${cat.slug}">${cat.name}</option>`;
        });
        onScraperCategoryChange();

        // Same categories drive the properties-list and CRM-leads filters;
        // those filter by category_name, so the option value is the name.
        // data-type (buy/rent) drives the rent-only inputs' visibility.
        ['filter-category', 'crm-filter-category', 'jobs-filter-category'].forEach(id => {
            const sel = document.getElementById(id);
            if (!sel) return;
            categories.forEach(cat => {
                sel.innerHTML += `<option value="${cat.name}" data-type="${cat.type}">${cat.name}</option>`;
            });
        });
    } catch (error) {
        console.error('Failed to load categories:', error);
    }
}

function onScraperCategoryChange() {
    const cat = document.getElementById('scraper-category').value;
    const isRent = cat.startsWith('rent-');
    const isBuy  = cat.startsWith('buy-');
    document.getElementById('scraper-buy-filters').classList.toggle('d-none', !isBuy);
    document.getElementById('scraper-rent-filters').classList.toggle('d-none', !isRent);
    document.getElementById('scraper-common-filters').classList.toggle('d-none', !isBuy && !isRent);
}

// ═══ Scraper publish-date (Jalali) ════════════════════════════
let _scraperDatePickerInit = false;

function _initScraperDatePicker() {
    if (_scraperDatePickerInit) return;
    _scraperDatePickerInit = true;
    try {
        $('#scraper-posted-date').persianDatepicker({
            format: 'YYYY/MM/DD',
            autoClose: true,
            observer: true,
            calendar: { persian: { locale: 'fa' } },
            onSelect: _onScraperDateChange,
        });
    } catch (e) { console.warn('scraper datepicker init failed:', e); }
    // Switching deal type hides one price block and shows another, so what
    // counts as a set filter changes with it.
    try { _scraperMoreSync(); } catch (_) {}
}

function _onScraperDateChange() {
    const el = document.getElementById('scraper-posted-date');
    const hasDate = !!el.value.trim();
    // persianDatepicker writes today into the field the moment it initialises,
    // so a value alone does not mean anybody picked one. This fires when they
    // do — and it is what «فیلترهای بیشتر» counts, or the fold would sit open
    // on every visit announcing a filter nobody set.
    if (hasDate) el.dataset.userSet = '1'; else delete el.dataset.userSet;
    const pages = document.getElementById('scraper-pages');
    // In date mode the count is an optional cap: empty = the whole day
    document.getElementById('scraper-pages-hint').classList.toggle('d-none', !hasDate);
    if (hasDate) {
        pages.value = '';
        pages.placeholder = 'خالی = همه آگهی‌های آن روز';
        pages.removeAttribute('min');
    } else {
        pages.placeholder = '';
        pages.setAttribute('min', '1');
        if (!pages.value) pages.value = '50';
    }
}

function clearScraperDate() {
    document.getElementById('scraper-posted-date').value = '';
    _onScraperDateChange();
}

function _intOrNull(id) {
    // money inputs carry «/» separators (and may hold Persian digits)
    const raw = _digitsOnly(document.getElementById(id)?.value || '');
    const v = parseInt(raw, 10);
    return isNaN(v) || v <= 0 ? null : v;
}

// ═══ Money inputs: live «123/321/111/001» grouping ═══════════════
// Divar-style thousands separator using «/» as the user asked.
function _digitsOnly(str) {
    return String(str)
        // Persian ۰-۹ and Arabic ٠-٩ → ASCII
        .replace(/[۰-۹]/g, d => String(d.charCodeAt(0) - 1776))
        .replace(/[٠-٩]/g, d => String(d.charCodeAt(0) - 1632))
        .replace(/\D/g, '');
}

function _groupMoney(digits) {
    if (!digits) return '';
    // strip leading zeros but keep a single "0"
    const clean = digits.replace(/^0+(?=\d)/, '');
    return clean.replace(/\B(?=(\d{3})+(?!\d))/g, '/');
}

function _formatMoneyInput(el) {
    const before = el.value;
    // how many digits sit left of the caret, so we can restore the position
    const caret = el.selectionStart ?? before.length;
    const digitsBeforeCaret = _digitsOnly(before.slice(0, caret)).length;

    const formatted = _groupMoney(_digitsOnly(before));
    if (formatted === before) return;
    el.value = formatted;

    // put the caret back after the same number of digits
    let seen = 0, pos = formatted.length;
    for (let i = 0; i < formatted.length; i++) {
        if (/\d/.test(formatted[i])) seen++;
        if (seen === digitsBeforeCaret) { pos = i + 1; break; }
        if (digitsBeforeCaret === 0) { pos = 0; break; }
    }
    try { el.setSelectionRange(pos, pos); } catch (_) {}
}

function initMoneyInputs(root = document) {
    root.querySelectorAll('input.money-input').forEach(el => {
        if (el.dataset.moneyBound) return;
        el.dataset.moneyBound = '1';
        el.addEventListener('input', () => _formatMoneyInput(el));
        el.addEventListener('blur', () => _formatMoneyInput(el));
        // keep a slider bound to this input in step with what is typed
        el.addEventListener('input', () => el._syncRange?.());
        el.addEventListener('blur', () => el._syncRange?.());
    });
}

document.addEventListener('DOMContentLoaded', () => initMoneyInputs());


// ═══ Draggable price ranges ══════════════════════════════════════
// Every price band in the panel is both typeable and draggable: the two money
// inputs stay the source of truth, and a two-handle slider writes into them.
//
// Markup: <div class="range-slider" data-min-input="x" data-max-input="y"
//              data-ceiling="100000000000" data-on-change="loadLeads"></div>
//
// The handles run on a cubic curve, not linearly. A band that has to reach
// ۱۰۰ میلیارد would otherwise spend its first pixel on the entire range a
// rental actually lives in.
const RANGE_STEPS = 1000;

function _rangeSnap(v) {
    if (v <= 0) return 0;
    // round to ~3 significant figures so a drag lands on a number a person
    // would actually say out loud
    const mag = Math.pow(10, Math.max(Math.floor(Math.log10(v)) - 2, 0));
    return Math.round(v / mag) * mag;
}
function _rangePosToValue(pos, ceiling) {
    if (pos <= 0) return 0;
    if (pos >= RANGE_STEPS) return ceiling;
    return _rangeSnap(ceiling * Math.pow(pos / RANGE_STEPS, 3));
}
function _rangeValueToPos(v, ceiling) {
    if (!v || v <= 0) return 0;
    return Math.round(RANGE_STEPS * Math.pow(Math.min(v, ceiling) / ceiling, 1 / 3));
}

function _buildRangeSlider(el) {
    const loInput = document.getElementById(el.dataset.minInput);
    const hiInput = document.getElementById(el.dataset.maxInput);
    if (!loInput || !hiInput) return;
    const ceiling = Number(el.dataset.ceiling) || 100000000000;
    const onChange = el.dataset.onChange;

    el.innerHTML = `
        <div class="range-slider__rail"><div class="range-slider__fill"></div></div>
        <input type="range" class="range-slider__thumb lo" min="0" max="${RANGE_STEPS}" value="0">
        <input type="range" class="range-slider__thumb hi" min="0" max="${RANGE_STEPS}" value="${RANGE_STEPS}">
        <div class="range-slider__readout"><span class="lo"></span><span class="hi"></span></div>`;

    const loThumb = el.querySelector('.range-slider__thumb.lo');
    const hiThumb = el.querySelector('.range-slider__thumb.hi');
    const fill    = el.querySelector('.range-slider__fill');
    const loOut   = el.querySelector('.range-slider__readout .lo');
    const hiOut   = el.querySelector('.range-slider__readout .hi');

    let timer = null;
    const fireChange = () => {
        if (!onChange || typeof window[onChange] !== 'function') return;
        clearTimeout(timer);                       // one call per drag, not per pixel
        timer = setTimeout(() => window[onChange](), 350);
    };

    function paint() {
        const lo = Number(loThumb.value), hi = Number(hiThumb.value);
        fill.style.right = (lo / RANGE_STEPS * 100) + '%';   // RTL: fill from the right
        fill.style.width = ((hi - lo) / RANGE_STEPS * 100) + '%';
        const loVal = _rangePosToValue(lo, ceiling);
        const hiVal = _rangePosToValue(hi, ceiling);
        loOut.textContent = lo <= 0 ? 'از هر قیمتی' : formatPrice(loVal);
        hiOut.textContent = hi >= RANGE_STEPS ? 'بی‌سقف' : formatPrice(hiVal);
    }

    // slider → inputs. A handle parked at either end means "no bound", so it
    // clears its input instead of writing 0 or the ceiling into the filter.
    function pushToInputs() {
        const lo = Number(loThumb.value), hi = Number(hiThumb.value);
        loInput.value = lo <= 0 ? '' : _groupMoney(String(_rangePosToValue(lo, ceiling)));
        hiInput.value = hi >= RANGE_STEPS ? '' : _groupMoney(String(_rangePosToValue(hi, ceiling)));
        paint();
        fireChange();
    }

    // inputs → slider (typing, or a programmatic restore)
    function pullFromInputs() {
        const lo = parseInt(_digitsOnly(loInput.value), 10);
        const hi = parseInt(_digitsOnly(hiInput.value), 10);
        loThumb.value = isNaN(lo) ? 0 : _rangeValueToPos(lo, ceiling);
        hiThumb.value = isNaN(hi) ? RANGE_STEPS : _rangeValueToPos(hi, ceiling);
        paint();
    }

    loThumb.addEventListener('input', () => {
        // handles must not cross
        if (Number(loThumb.value) > Number(hiThumb.value)) loThumb.value = hiThumb.value;
        pushToInputs();
    });
    hiThumb.addEventListener('input', () => {
        if (Number(hiThumb.value) < Number(loThumb.value)) hiThumb.value = loThumb.value;
        pushToInputs();
    });

    loInput._syncRange = pullFromInputs;
    hiInput._syncRange = pullFromInputs;
    el._syncRange = pullFromInputs;
    pullFromInputs();
}

function initRangeSliders(root = document) {
    root.querySelectorAll('.range-slider').forEach(el => {
        if (el.dataset.rangeBound) return;
        el.dataset.rangeBound = '1';
        _buildRangeSlider(el);
    });
}

document.addEventListener('DOMContentLoaded', () => initRangeSliders());


// ═══ Scraper form memory (last used filters persist across visits) ═══
// note: scraper-posted-date is intentionally NOT persisted — it's a
// per-run choice and the picker auto-fills today, which would force date mode
// «تعداد آگهی» is deliberately NOT here. It is a decision about this run,
// not a preference to carry between runs: a 50 left over from last time
// silently capped the next scrape, and the box read as if somebody had
// typed it. Empty means «all of them», and empty is what it should open as.
const _SCRAPER_TEXT_FIELDS = [
    'scraper-category', 'scraper-advertiser-type', 'scraper-rotate-every',
    'scraper-min-price', 'scraper-max-price', 'scraper-min-ppm', 'scraper-max-ppm',
    'scraper-min-deposit', 'scraper-max-deposit', 'scraper-min-rent', 'scraper-max-rent',
    'scraper-min-area', 'scraper-max-area', 'scraper-min-rooms', 'scraper-max-rooms',
];
const _SCRAPER_CHECKS = ['scraper-has-images', 'scraper-has-elevator', 'scraper-has-parking',
    'scraper-has-storage', 'scraper-has-balcony', 'scraper-images'];

/* ── «از روی لینک دیوار» ──────────────────────────────────────────────────
 *
 * Filters set on Divar itself and pasted here, rather than retyped. The
 * answer FILLS THE FORM instead of starting a run: a link that quietly
 * became a scrape would hide whichever half of it did not carry over — a
 * polygon drawn on the map cannot be expressed as a scrape filter, and
 * finding that out from the results is finding out too late.               */
const _LINK_FIELD_IDS = {
    min_price: 'scraper-min-price',   max_price: 'scraper-max-price',
    min_deposit: 'scraper-min-deposit', max_deposit: 'scraper-max-deposit',
    min_rent: 'scraper-min-rent',     max_rent: 'scraper-max-rent',
    min_area: 'scraper-min-area',     max_area: 'scraper-max-area',
    min_rooms: 'scraper-min-rooms',   max_rooms: 'scraper-max-rooms',
};

/* ── choosing which Divar number a run uses ───────────────────────────────
 *
 * «user can choose for each cookie i want use and i can change manauaili in
 * session». The default stays automatic — least-spent first — because that
 * is the right answer most days. The picker is for the days it is not: a
 * number you want rested, or one you want to prove works.
 *
 * The list comes from /auth/cookies, which now returns only the caller's own
 * sessions, so this cannot offer somebody else's number.                   */
async function loadScraperAccounts() {
    const sel = document.getElementById('scraper-account');
    if (!sel) return;
    const chosen = sel.value;
    try {
        const d = await apiCall('/auth/cookies?mine=1');
        const rows = (d.cookies || []).slice().sort(
            (a, b) => (a.reveals || 0) - (b.reveals || 0));
        _myDivarAccounts = rows;
        sel.innerHTML = '<option value="">خودکار — کم‌مصرف‌ترین</option>'
            + rows.map(c => {
                const bits = [`${c.reveals || 0} افشا`];
                if (c.is_enabled === false) bits.push('خاموش');
                if (!c.is_valid) bits.push('نامعتبر');
                if (c.identity_required_at) bits.push('احراز هویت لازم');
                else if (c.challenged_at) bits.push('اخیراً کد خواسته');
                return `<option value="${esc(c.phone_number)}"${_divarUsable(c) ? '' : ' disabled'}>`
                     + `${esc(c.phone_number)} — ${esc(bits.join('، '))}</option>`;
            }).join('');
        // A pick that has since been switched off or gone bad falls back to
        // «خودکار» rather than quietly staying selected-but-disabled.
        const keep = rows.find(c => c.phone_number === chosen);
        sel.value = keep && _divarUsable(keep) ? chosen : '';
        _renderScraperAccountList(rows);
        onScraperAccountChange();
    } catch (_) {
        // The form still works on «خودکار»; a picker that failed to load is
        // not a reason to block a scrape.
    }
}

/* ── my Divar numbers, each with its own on/off ─────────────────────────
 *
 * «امکان فعال و غیرفعال کردن شماره با تاگل — شاید یک شماره در دسترس نباشد و
 * در اسکرپ چرخشی به مشکل بخوریم: کد به آن شماره ارسال شود و آن شماره در
 * دسترس نباشد.» Off = rotation, «خودکار» and the picker all pass it by, and
 * a run that is on it right now moves to another of your numbers.        */
let _myDivarAccounts = [];

function _divarUsable(c) {
    return !!c && c.is_valid && c.is_enabled !== false && !c.identity_required_at;
}

function _renderScraperAccountList(rows) {
    const box = document.getElementById('scraper-account-list');
    if (!box) return;
    if (!rows.length) {
        box.innerHTML = `<div class="acct-empty">هیچ شمارهٔ دیواری به نام شما ثبت نشده —
            از <a href="#" onclick="showSection('auth');return false">احراز هویت دیوار</a> شمارهٔ خودتان را وارد کنید.</div>`;
        return;
    }
    box.innerHTML = rows.map(c => {
        const on = c.is_enabled !== false;
        const state = !c.is_valid ? '<span class="acct-flag bad">نامعتبر</span>'
            : c.identity_required_at ? '<span class="acct-flag warn">احراز هویت</span>'
            : c.challenged_at ? '<span class="acct-flag warn" title="دیوار اخیراً برای این شماره کد خواسته">کد خواسته</span>'
            : '';
        // A div, not a <label>: switching a number off moves live runs, so
        // only the switch itself may do it — not a tap on the number.
        return `<div class="acct-row${on ? '' : ' is-off'}" title="${on ? 'روشن — در چرخش و «خودکار» استفاده می‌شود' : 'خاموش — هیچ اسکرپی از این شماره استفاده نمی‌کند'}">
            <span class="form-check form-switch m-0">
                <input class="form-check-input" type="checkbox" role="switch" ${on ? 'checked' : ''}
                       onchange="toggleDivarNumber(${Number(c.id)}, this.checked, this)"
                       aria-label="روشن/خاموش ${esc(c.phone_number)}">
            </span>
            <span class="acct-phone" dir="ltr">${esc(c.phone_number)}</span>
            <span class="acct-meta">${formatNumber(c.reveals || 0)} افشا</span>
            ${state}
        </div>`;
    }).join('');
}

async function toggleDivarNumber(id, enabled, input) {
    if (input) input.disabled = true;
    try {
        const r = await apiCall(`/auth/cookies/${id}`, {
            method: 'PATCH', body: JSON.stringify({ enabled }) });
        const moved = (r.moved_jobs || []).length;
        showToast(enabled ? 'روشن شد' : 'خاموش شد',
            enabled ? `${r.phone_number} دوباره در چرخش است`
                    : `${r.phone_number} دیگر استفاده نمی‌شود`
                      + (moved ? ` — ${formatNumber(moved)} اسکرپ در حال اجرا به شمارهٔ دیگر شما منتقل می‌شود` : ''),
            enabled ? 'success' : 'warning');
        if (moved) loadJobs();
    } catch (e) {
        showToast('خطا', e.message, 'danger');
    } finally {
        loadScraperAccounts();
        if (typeof checkCookieStatus === 'function') checkCookieStatus();
    }
}

/* ── switching a running scrape onto another number ─────────────────────
 *
 * «وقتی با یک شماره در حال اسکرپ به مشکل خورد، بتوان شماره را در حین اسکرپ
 * عوض کرد و ادامه را با شمارهٔ جدید ادامه داد.» The run does not stop: the
 * switch is taken at its next listing, or at once if it is parked on a code
 * prompt for the current number.                                         */
async function switchJobAccount(jobId, currentPhone) {
    let rows = [];
    try { rows = (await apiCall('/auth/cookies?mine=1')).cookies || []; } catch (_) {}
    const cur = _digits(currentPhone);
    const choices = rows.filter(c => _divarUsable(c) && _digits(c.phone_number) !== cur)
        .sort((a, b) => (a.reveals || 0) - (b.reveals || 0));
    if (!choices.length) {
        const go = await askConfirm({
            icon: 'bi-sim-slash', title: 'شمارهٔ دیگری نیست',
            body: 'فقط از شماره‌هایی که در پنل خودتان اضافه و تأیید شده‌اند می‌شود استفاده کرد، و شمارهٔ روشن و معتبر دیگری ندارید.',
            note: 'برای شمارهٔ جدید، آن را در «احراز هویت دیوار» اضافه کنید و کد دیوار را وارد کنید؛ یا شمارهٔ خاموش را در فرم اسکرپر روشن کنید.',
            okLabel: 'رفتن به احراز هویت دیوار', cancelLabel: 'بستن',
        });
        if (go) showSection('auth');
        return false;
    }
    const picked = await askText({
        icon: 'bi-arrow-left-right', title: 'تعویض شمارهٔ دیوار',
        okLabel: 'تعویض و ادامه',
        body: currentPhone
            ? `اسکرپ الان روی <b dir="ltr">${esc(currentPhone)}</b> است. بدون توقف، با شمارهٔ دیگری از شماره‌های خودتان ادامه می‌دهد.`
            : 'بدون توقف، با شمارهٔ دیگری از شماره‌های خودتان ادامه می‌دهد.',
        note: 'فقط شماره‌های تأییدشدهٔ خودتان در این فهرست‌اند؛ شمارهٔ جدید را از «احراز هویت دیوار» اضافه کنید. '
            + 'اگر گوشی این شماره در دسترس نیست، بهتر است آن را در فرم اسکرپر خاموش کنید تا دوباره انتخاب نشود.',
        field: {
            label: 'شمارهٔ جدید', value: '',
            options: [['', 'خودکار — کم‌مصرف‌ترین شمارهٔ دیگر من'],
                      ...choices.map(c => [c.phone_number, `${c.phone_number} — ${c.reveals || 0} افشا`])],
        },
    });
    if (picked === null) return false;
    try {
        const r = await apiCall(`/scraper/jobs/${encodeURIComponent(jobId)}/switch-account`, {
            method: 'POST', body: JSON.stringify(picked ? { phone: picked } : {}) });
        showToast('ثبت شد', r.message || 'تعویض شماره ثبت شد', 'success');
        loadJobs();
        return true;
    } catch (e) {
        showToast('تعویض نشد', e.message, 'danger');
        return false;
    }
}

/** From the code prompt: the phone that should receive it is not in reach. */
async function switchFromOtp() {
    const key = document.getElementById('divar-otp-key')?.value || '';
    const jobId = key ? key.split(':')[0] : '';
    if (!jobId) return;
    const phone = document.getElementById('otp2-phone')?.textContent || '';
    const modalEl = document.getElementById('divarOtpModal');
    // Out of the way while the picker asks; back if they change their mind.
    bootstrap.Modal.getInstance(modalEl)?.hide();
    const ok = await switchJobAccount(jobId, phone);
    if (ok) {
        _dismissedOtpKeys.add(key);          // this prompt is being abandoned
        _otp2StopTimer();
    } else {
        new bootstrap.Modal(modalEl).show();
    }
}

function onScraperAccountChange() {
    const sel = document.getElementById('scraper-account');
    const note = document.getElementById('scraper-account-note');
    if (!note) return;
    if (!sel?.value) {
        note.className = 'form-text small';
        note.textContent = 'کم‌مصرف‌ترین شمارهٔ شما انتخاب می‌شود.';
        return;
    }
    const rotate = _intOrNull('scraper-rotate-every');
    note.className = 'form-text small' + (rotate === 0 ? '' : ' text-warning');
    note.textContent = rotate === 0
        ? 'فقط از همین شماره استفاده می‌شود.'
        : 'با این حال چرخش شماره ممکن است وسط کار عوضش کند — برای ثابت ماندن، «چرخش شماره» را ۰ بگذارید.';
}

async function applyDivarLink() {
    const input = document.getElementById('scraper-link');
    const note = document.getElementById('scraper-link-note');
    const btn = document.getElementById('scraper-link-btn');
    const url = (input?.value || '').trim();
    if (!url) { showToast('خطا', 'اول لینک را بچسبانید', 'warning'); return; }

    if (btn) { btn.disabled = true; }
    if (note) { note.className = 'small mt-1 text-muted'; note.textContent = 'در حال خواندن…'; }
    try {
        const d = await apiCall('/scraper/parse-link', {
            method: 'POST', body: JSON.stringify({ url }),
        });

        const picker = document.getElementById('scraper-city-picker');
        if (d.city && picker?._setCityValue) picker._setCityValue(d.city);
        const cat = document.getElementById('scraper-category');
        if (d.category && cat) {
            cat.value = d.category;
            try { onScraperCategoryChange(); } catch (_) {}
        }

        // Clear every band this form can hold before writing the new ones, or
        // a leftover from the previous link silently narrows the next scrape.
        for (const id of Object.values(_LINK_FIELD_IDS)) {
            const el = document.getElementById(id);
            if (el) el.value = '';
        }
        for (const [key, id] of Object.entries(_LINK_FIELD_IDS)) {
            const el = document.getElementById(id);
            if (el && d.filters?.[key] != null) {
                el.value = d.filters[key];
                if (el.classList.contains('money-input')) _formatMoneyInput(el);
            }
        }
        const adv = document.getElementById('scraper-advertiser-type');
        if (adv) adv.value = d.filters?.advertiser_type || '';
        const img = document.getElementById('scraper-has-images');
        if (img) img.checked = !!d.filters?.has_images;

        saveScraperForm();

        const where = [d.city_name, d.category_name].filter(Boolean).join(' — ');
        if (note) {
            const lost = (d.ignored || []).length
                ? `<div class="text-warning">این‌ها منتقل نشدند: ${esc(d.ignored.join('، '))}</div>`
                : '';
            note.className = 'small mt-1';
            note.innerHTML = `<span class="text-success">فرم پر شد — ${esc(where)}</span>${lost}`;
        }
        showToast('انجام شد', `فرم از روی لینک پر شد — ${where}`, 'success');
    } catch (e) {
        if (note) {
            note.className = 'small mt-1 text-danger';
            note.textContent = e.message || 'این لینک خوانده نشد';
        }
    } finally {
        if (btn) btn.disabled = false;
    }
}

function saveScraperForm() {
    try {
        const data = { city: document.getElementById('scraper-city')?.value || '' };
        _SCRAPER_TEXT_FIELDS.forEach(id => { data[id] = document.getElementById(id)?.value ?? ''; });
        _SCRAPER_CHECKS.forEach(id => { data[id] = !!document.getElementById(id)?.checked; });
        localStorage.setItem('sf_scraper_form', JSON.stringify(data));
    } catch (_) {}
}

function restoreScraperForm() {
    let data;
    try { data = JSON.parse(localStorage.getItem('sf_scraper_form') || 'null'); } catch (_) { return; }
    if (!data) return;
    _SCRAPER_TEXT_FIELDS.forEach(id => {
        const el = document.getElementById(id);
        if (el && data[id] != null && data[id] !== '') {
            el.value = data[id];
            if (el.classList.contains('money-input')) _formatMoneyInput(el);
        }
    });
    _SCRAPER_CHECKS.forEach(id => {
        const el = document.getElementById(id);
        if (el && typeof data[id] === 'boolean') el.checked = data[id];
    });
    const dateEl = document.getElementById('scraper-posted-date');
    if (dateEl && (data['scraper-posted-date'] || '').trim()) dateEl.dataset.userSet = '1';
    // city picker + category-driven filter visibility
    const picker = document.getElementById('scraper-city-picker');
    if (picker && picker._setCityValue && data.city) picker._setCityValue(data.city);
    if (document.getElementById('scraper-category')?.value) {
        try { onScraperCategoryChange(); } catch (_) {}
    }
    _scraperMoreSync();
}


// ── «فیلترهای بیشتر» ─────────────────────────────────────────────────────────
// The optional filters are folded away, which is only safe as long as a filter
// can never be applied out of sight: the panel remembers the form, so somebody
// could return to a run narrowed by a price set last week and never see it.
// The summary counts what is set, and a set filter forces the fold open.

const _SCRAPER_FILTER_IDS = [
    'scraper-min-price', 'scraper-max-price', 'scraper-min-ppm', 'scraper-max-ppm',
    'scraper-min-deposit', 'scraper-max-deposit', 'scraper-min-rent', 'scraper-max-rent',
    'scraper-min-area', 'scraper-max-area', 'scraper-min-rooms', 'scraper-max-rooms',
    'scraper-advertiser-type', 'scraper-posted-date',
    'scraper-has-images', 'scraper-has-elevator', 'scraper-has-parking',
    'scraper-has-storage', 'scraper-has-balcony',
];

function _scraperActiveFilters() {
    return _SCRAPER_FILTER_IDS.filter(id => {
        const el = document.getElementById(id);
        if (!el) return false;
        // A hidden block belongs to the other deal type — its leftovers are
        // not sent and must not be counted.
        if (el.closest('.d-none')) return false;
        if (id === 'scraper-posted-date') return el.dataset.userSet === '1';
        return el.type === 'checkbox' ? el.checked : !!(el.value || '').trim();
    }).length;
}

function _scraperMoreSync() {
    const box = document.getElementById('scraper-more');
    if (!box) return;
    const n = _scraperActiveFilters();
    const tag = document.getElementById('scraper-more-count');
    if (tag) tag.textContent = n ? ` · ${formatNumber(n)} فعال` : '';
    box.classList.toggle('has-filters', n > 0);
    if (n > 0) box.open = true;
}

document.addEventListener('change', e => {
    if (e.target.closest?.('#scraper-more')) _scraperMoreSync();
});

// ─── «چند آگهی با این فیلترها هست؟» ──────────────────────────────────────────
// Divar prints this above its own results («۳۴۳ آگهی در این محدوده») and its
// search API carries the same number. One request answers what would otherwise
// take opening every ad in the city.
/* The count Divar gives for these filters, kept current as the form
 * changes rather than only on the button. It is the number the progress bar
 * is now measured against, so it should be on screen before the run starts,
 * not discovered in the log afterwards. Debounced: every keystroke in a
 * price box is not a request to Divar. */
let _estimateTimer = null;
function scheduleEstimate() {
    clearTimeout(_estimateTimer);
    const city = document.getElementById('scraper-city')?.value;
    const cat = document.getElementById('scraper-category')?.value;
    if (!city || !cat) return;
    _estimateTimer = setTimeout(() => estimateScrape(true), 900);
}

function _wireEstimateRefresh() {
    const form = document.getElementById('scraper-form');
    if (!form || form._estimateWired) return;
    form._estimateWired = true;
    form.addEventListener('input', e => {
        if (e.target?.id === 'scraper-pages' || e.target?.id === 'scraper-rotate-every') return;
        scheduleEstimate();
    });
    form.addEventListener('change', scheduleEstimate);
}

async function estimateScrape(quiet = false) {
    const box = document.getElementById('scraper-estimate');
    const btn = document.getElementById('scraper-estimate-btn');
    const city = document.getElementById('scraper-city').value;
    if (!city) { if (!quiet) showToast('خطا', 'اول شهر را انتخاب کنید', 'warning'); return; }

    const p = new URLSearchParams({ city });
    const cat = document.getElementById('scraper-category')?.value;
    if (cat) p.set('category', cat);
    const adv = document.getElementById('scraper-advertiser-type')?.value;
    if (adv) p.set('advertiser_type', adv);
    // «دارای عکس» is the filter; «scraper-images» is the download-images
    // toggle and has nothing to do with what Divar returns
    if (document.getElementById('scraper-has-images')?.checked) p.set('has_images', 'true');
    const nums = {
        min_price: 'scraper-min-price', max_price: 'scraper-max-price',
        min_deposit: 'scraper-min-deposit', max_deposit: 'scraper-max-deposit',
        min_rent: 'scraper-min-rent', max_rent: 'scraper-max-rent',
        min_area: 'scraper-min-area', max_area: 'scraper-max-area',
        min_rooms: 'scraper-min-rooms', max_rooms: 'scraper-max-rooms',
        min_price_per_meter: 'scraper-min-ppm', max_price_per_meter: 'scraper-max-ppm',
    };
    for (const [key, id] of Object.entries(nums)) {
        const v = _intOrNull(id);
        if (v != null) p.set(key, v);
    }
    for (const [key, id] of [['has_elevator','scraper-has-elevator'],
                             ['has_parking','scraper-has-parking'],
                             ['has_storage','scraper-has-storage']]) {
        if (document.getElementById(id)?.checked) p.set(key, 'true');
    }

    box.classList.remove('d-none');
    box.innerHTML = '<div class="text-muted small"><span class="spinner-border spinner-border-sm"></span> در حال پرسیدن از دیوار...</div>';
    if (btn) btn.disabled = true;
    try {
        const r = await apiCall(`/scraper/estimate?${p.toString()}`);
        if (r.count == null) {
            box.innerHTML = `<div class="alert alert-warning py-2 mb-0" style="font-size:.75rem">
                ${esc(r.error || 'تعداد را نتوانستم بگیرم')}</div>`;
            return;
        }
        // Divar cannot narrow on some of our filters; the scraper applies those
        // itself after opening each ad, so the real yield is at most this.
        const rest = r.applied_after_scrape || [];
        box.innerHTML = `
            <div class="alert alert-info py-2 mb-0" style="font-size:.8rem">
                <b style="font-size:1.05rem">${formatNumber(r.count)}</b> آگهی با این فیلترها در دیوار هست.
                ${rest.length ? `<div class="mt-1" style="font-size:.7rem">
                    ${esc(rest.join('، '))} را دیوار فیلتر نمی‌کند — اسکرپر خودش بعد از باز کردن هر آگهی
                    اعمالش می‌کند، پس نتیجهٔ نهایی از این عدد کمتر می‌شود.</div>` : ''}
            </div>`;
    } catch (e) {
        box.innerHTML = `<div class="alert alert-danger py-2 mb-0" style="font-size:.75rem">${esc(e.message)}</div>`;
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function startScraping(e) {
    e.preventDefault();

    const city     = document.getElementById('scraper-city').value;
    const category = document.getElementById('scraper-category').value;
    let maxItems   = parseInt(document.getElementById('scraper-pages').value);
    const downloadImages = document.getElementById('scraper-images').checked;
    const hasPostedDate = !!document.getElementById('scraper-posted-date')?.value.trim();
    // Empty count: in date mode it means "the whole day" (send nothing);
    // in normal mode fall back to the default of 50
    if (isNaN(maxItems) && !hasPostedDate) maxItems = 50;

    const _chk = id => document.getElementById(id)?.checked ? true : null;

    const filters = {
        // قیمت خرید
        min_price:             _intOrNull('scraper-min-price'),
        max_price:             _intOrNull('scraper-max-price'),
        min_price_per_meter:   _intOrNull('scraper-min-ppm'),
        max_price_per_meter:   _intOrNull('scraper-max-ppm'),
        // قیمت اجاره
        min_deposit:           _intOrNull('scraper-min-deposit'),
        max_deposit:           _intOrNull('scraper-max-deposit'),
        min_rent:              _intOrNull('scraper-min-rent'),
        max_rent:              _intOrNull('scraper-max-rent'),
        // متراژ و اتاق
        min_area:              _intOrNull('scraper-min-area'),
        max_area:              _intOrNull('scraper-max-area'),
        min_rooms:             _intOrNull('scraper-min-rooms'),
        max_rooms:             _intOrNull('scraper-max-rooms'),
        // ویژگی‌ها
        has_images:            _chk('scraper-has-images'),
        has_elevator:          _chk('scraper-has-elevator'),
        has_parking:           _chk('scraper-has-parking'),
        has_storage:           _chk('scraper-has-storage'),
        has_balcony:           _chk('scraper-has-balcony'),
        // آگهی‌دهنده
        advertiser_type:       document.getElementById('scraper-advertiser-type')?.value || null,
        // چرخش شماره دیوار (خالی = پیش‌فرض سرور)
        rotate_every:          _intOrNull('scraper-rotate-every'),
    };

    // Date mode: scrape the selected Jalali day (count becomes an optional cap)
    const postedJalali = document.getElementById('scraper-posted-date')?.value.trim() || '';
    if (postedJalali) {
        const g = jalaliToGregorian(postedJalali);
        if (g) filters.posted_date = g;
    }

    // Check cookie status before scraping
    // remember this configuration for next time
    saveScraperForm();

    if (!cookieStatus.is_valid) {
        pendingScrapingAction = { type: 'bulk', city, category, maxItems, downloadImages, filters };
        showCookieWarning();
        return;
    }

    await executeBulkScraping(city, category, maxItems, downloadImages, filters);
}

/** The scrape form as the API wants it — what «شروع» sends, minus the
 *  session pick, so a schedule saves exactly what a click would run. */
function _scrapeFormConfig() {
    const city     = document.getElementById('scraper-city').value;
    const category = document.getElementById('scraper-category').value;
    let maxItems   = parseInt(document.getElementById('scraper-pages').value);
    const hasPostedDate = !!document.getElementById('scraper-posted-date')?.value.trim();
    if (isNaN(maxItems) && !hasPostedDate) maxItems = 50;
    const _chk = id => document.getElementById(id)?.checked ? true : null;
    const cfg = {
        city, category,
        download_images: document.getElementById('scraper-images').checked,
        min_price: _intOrNull('scraper-min-price'), max_price: _intOrNull('scraper-max-price'),
        min_price_per_meter: _intOrNull('scraper-min-ppm'), max_price_per_meter: _intOrNull('scraper-max-ppm'),
        min_deposit: _intOrNull('scraper-min-deposit'), max_deposit: _intOrNull('scraper-max-deposit'),
        min_rent: _intOrNull('scraper-min-rent'), max_rent: _intOrNull('scraper-max-rent'),
        min_area: _intOrNull('scraper-min-area'), max_area: _intOrNull('scraper-max-area'),
        min_rooms: _intOrNull('scraper-min-rooms'), max_rooms: _intOrNull('scraper-max-rooms'),
        has_images: _chk('scraper-has-images'), has_elevator: _chk('scraper-has-elevator'),
        has_parking: _chk('scraper-has-parking'), has_storage: _chk('scraper-has-storage'),
        has_balcony: _chk('scraper-has-balcony'),
        advertiser_type: document.getElementById('scraper-advertiser-type')?.value || null,
        rotate_every: _intOrNull('scraper-rotate-every'),
        max_age_hours: _intOrNull('scraper-max-age'),
    };
    if (Number.isFinite(maxItems) && maxItems > 0) cfg.max_items = maxItems;
    const picked = document.getElementById('scraper-account')?.value || '';
    if (picked) cfg.divar_phone = picked;
    return Object.fromEntries(Object.entries(cfg).filter(([, v]) => v !== null && v !== undefined));
}

// ═══ Scheduled scrapes ═════════════════════════════════════════
async function saveAsSchedule() {
    const cfg = _scrapeFormConfig();
    if (!cfg.city || !cfg.category) { showToast('توجه', 'اول شهر و دسته‌بندی را انتخاب کنید', 'warning'); return; }
    const when = await askText({
        icon: 'bi-alarm', title: 'اجرای روزانه',
        body: `هر روز <b>${esc(cityName(cfg.city))} / ${esc(categoryName(cfg.category))}</b>${cfg.max_items ? ` تا ${formatNumber(cfg.max_items)} آگهی` : ''} با همین فیلترها اجرا می‌شود — با حساب‌های دیوار خودتان.`,
        note: cfg.max_age_hours ? '' : 'چون «حداکثر سن آگهی» خالی است، فقط آگهی‌های ۲۴ ساعت اخیر گرفته می‌شود.',
        field: { label: 'ساعت اجرا (به وقت تهران)', value: '08:00', placeholder: '08:00', dir: 'ltr',
                 validate: v => /^([01]?\d|2[0-3]):[0-5]\d$/.test(v.trim()) ? '' : 'ساعت را مثل 08:00 بنویسید' },
    });
    if (when === null) return;
    const [h, m] = when.trim().split(':').map(Number);
    const name = await askText({
        icon: 'bi-tag', title: 'اسم زمان‌بندی', body: 'برای این‌که بعداً بین چند زمان‌بندی پیدایش کنید.',
        field: { label: 'اسم', value: `${cityName(cfg.city)} — ${categoryName(cfg.category)}` },
    });
    if (name === null) return;
    try {
        await apiCall('/scraper/schedules', { method: 'POST', body: JSON.stringify({ name: name.trim(), config: cfg, hour: h, minute: m, enabled: true }) });
        showToast('ذخیره شد', `هر روز ساعت ${when.trim()} اجرا می‌شود`, 'success');
        loadSchedules();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

function cityName(slug) { const o = document.querySelector(`#scraper-city option[value="${slug}"]`); return o ? o.textContent.trim() : slug; }
function categoryName(slug) { const o = document.querySelector(`#scraper-category option[value="${slug}"]`); return o ? o.textContent.trim() : slug; }

async function loadSchedules() {
    const card = document.getElementById('schedules-card');
    const tb = document.getElementById('schedules-table');
    if (!card || !tb) return;
    try {
        const d = await apiCall('/scraper/schedules');
        const rows = d.schedules || [];
        document.getElementById('schedules-count').textContent = formatNumber(rows.length);
        card.classList.toggle('d-none', rows.length === 0);
        const fa = iso => iso ? `${new Date(iso).toLocaleDateString('fa-IR')} ${new Date(iso).toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' })}` : '—';
        tb.innerHTML = rows.map(s => {
            const c = s.config || {};
            const what = `${esc(s.city_name || c.city)} / ${esc(s.category_name || c.category)}` +
                (c.max_items ? ` · ${formatNumber(c.max_items)} آگهی` : '') +
                (c.max_age_hours ? ` · ${formatNumber(c.max_age_hours)} ساعت اخیر` : '');
            const lr = s.last_result || {};
            const cls = { started: 'text-success', failed: 'text-danger', skipped: 'text-warning' }[lr.status] || 'text-muted';
            const last = s.last_run_at ? `<div class="small ${cls}">${esc(lr.detail || lr.status || '')}</div><div class="small text-muted">${fa(s.last_run_at)}</div>` : '<span class="text-muted small">هنوز اجرا نشده</span>';
            const hh = String(s.hour).padStart(2, '0'), mm = String(s.minute).padStart(2, '0');
            return `<tr class="${s.enabled ? '' : 'opacity-50'}">
                <td>${esc(s.name)}${d.can_see_all && s.owner_name ? `<div class="small text-muted">${esc(s.owner_name)}</div>` : ''}</td>
                <td class="small">${what}</td>
                <td dir="ltr">${hh}:${mm}<div class="small text-muted">بعدی: ${fa(s.next_run_at)}</div></td>
                <td>${last}</td>
                <td><div class="form-check form-switch m-0"><input class="form-check-input" type="checkbox" ${s.enabled ? 'checked' : ''} onchange="toggleSchedule(${s.id}, this.checked)"></div></td>
                <td class="text-nowrap">
                    <button class="btn btn-sm btn-outline-primary" onclick="runScheduleNow(${s.id})" title="همین حالا اجرا کن"><i class="bi bi-play-fill"></i></button>
                    <button class="btn btn-sm btn-outline-secondary" onclick="editScheduleTime(${s.id}, '${hh}:${mm}')" title="تغییر ساعت"><i class="bi bi-clock"></i></button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteSchedule(${s.id})" title="حذف"><i class="bi bi-trash"></i></button>
                </td></tr>`;
        }).join('');
    } catch (e) { /* the section works without the card */ }
}

async function toggleSchedule(id, enabled) {
    try {
        await apiCall(`/scraper/schedules/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) });
        showToast(enabled ? 'روشن شد' : 'خاموش شد', '', 'success');
        loadSchedules();
    } catch (e) { showToast('خطا', e.message, 'danger'); loadSchedules(); }
}

async function editScheduleTime(id, current) {
    const when = await askText({
        icon: 'bi-clock', title: 'ساعت اجرا', body: 'به وقت تهران.',
        field: { label: 'ساعت', value: current, dir: 'ltr',
                 validate: v => /^([01]?\d|2[0-3]):[0-5]\d$/.test(v.trim()) ? '' : 'ساعت را مثل 08:00 بنویسید' },
    });
    if (when === null) return;
    const [h, m] = when.trim().split(':').map(Number);
    try {
        await apiCall(`/scraper/schedules/${id}`, { method: 'PATCH', body: JSON.stringify({ hour: h, minute: m }) });
        loadSchedules();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function runScheduleNow(id) {
    if (!await askConfirm({ icon: 'bi-play-fill', title: 'اجرای فوری', okLabel: 'اجرا کن', body: 'همین حالا با تنظیمات این زمان‌بندی یک اسکرپ شروع شود؟' })) return;
    try {
        const r = await apiCall(`/scraper/schedules/${id}/run`, { method: 'POST' });
        showToast(r.status === 'started' ? 'شروع شد' : 'اجرا نشد', r.detail || '', r.status === 'started' ? 'success' : 'warning');
        loadSchedules(); loadJobs();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function deleteSchedule(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف زمان‌بندی', tone: 'danger', okLabel: 'حذف', body: 'این زمان‌بندی حذف شود؟ اسکرپ‌های قبلی‌اش می‌مانند.' })) return;
    try {
        await apiCall(`/scraper/schedules/${id}`, { method: 'DELETE' });
        loadSchedules();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function executeBulkScraping(city, category, maxItems, downloadImages, filters = {}) {
    try {
        // A number picked by hand wins. «خودکار» sends none, and the server
        // picks the least-spent of YOUR switched-on numbers — which is what
        // the option says. It used to send the primary/newest session, so
        // «کم‌مصرف‌ترین» always meant the same number.
        const picked = document.getElementById('scraper-account')?.value || '';
        const session = picked ? { phone_number: picked } : null;
        // Strip null/undefined values so the API doesn't receive empty fields
        const cleanFilters = Object.fromEntries(
            Object.entries(filters).filter(([, v]) => v !== null && v !== undefined)
        );
        const body = {
            city,
            category,
            download_images: downloadImages,
            ...cleanFilters,
        };
        if (Number.isFinite(maxItems) && maxItems > 0) body.max_items = maxItems;
        if (session) body.divar_phone = session.phone_number;

        const result = await apiCall('/scraper/start', {
            method: 'POST',
            body: JSON.stringify(body)
        });

        const phoneLabel = result.divar_phone ? ` (${result.divar_phone})` : '';
        showToast('موفق', `اسکرپینگ شروع شد: ${result.job_id}${phoneLabel}`, 'success');
        loadJobs();

    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

function _jobsUrl() {
    let url = '/scraper/jobs?limit=20';
    const cat = document.getElementById('jobs-filter-category')?.value || '';
    if (cat) url += `&category=${encodeURIComponent(cat)}`;
    return url;
}

async function loadJobs() {
    try {
        const data = await apiCall(_jobsUrl());
        // Seed snapshot so first poll doesn't false-trigger a refresh
        for (const job of data.items) {
            _jobPollSnapshot[job.job_id] = { new_items: job.new_items, status: job.status };
        }
        _renderJobsTable(data.items);
    } catch (error) {
        showToast('خطا', 'بارگیری تسک‌ها ناموفق بود', 'danger');
    }
}

// ─── چرخش شماره: what the number actually does ───────────────────────────────
// It is «how many ads before switching accounts», so a bigger value rotates
// less often and leans harder on one account — which is what triggers Divar's
// verification SMS. Setting it to the maximum does the opposite of what the
// name suggests, and rotation does nothing at all below two valid sessions.
let _validDivarSessions = null;

async function refreshDivarSessionCount() {
    try {
        const r = await apiCall('/auth/cookies?mine=1');
        _validDivarSessions = (r.cookies || []).filter(c => c.is_valid).length;
    } catch (e) {
        _validDivarSessions = null;
    }
    renderRotationHint();
}

function renderRotationHint() {
    const box = document.getElementById('rotate-hint');
    if (!box) return;
    const raw = document.getElementById('scraper-rotate-every')?.value.trim();
    const n = raw === '' ? 100 : parseInt(_digitsOnly(raw), 10);  // empty = server default
    const parts = [];

    if (_validDivarSessions === 0 || _validDivarSessions === 1) {
        parts.push(`<span class="text-danger">با ${formatNumber(_validDivarSessions)} حساب فعال،
            چرخش هیچ کاری نمی‌کند — هر عددی بگذارید فرقی ندارد. برای اینکه کار کند،
            در «احراز هویت دیوار» حساب دوم اضافه کنید.</span>`);
    } else if (_validDivarSessions > 1) {
        parts.push(`<span class="text-success">${formatNumber(_validDivarSessions)} حساب فعال دارید،
            پس چرخش کار می‌کند.</span>`);
    }

    if (!isNaN(n)) {
        if (n === 0) {
            parts.push('<span class="text-warning">۰ یعنی بدون چرخش — همهٔ بار روی یک شماره، بیشترین پیامک.</span>');
        } else if (n > 150) {
            parts.push(`<span class="text-warning">هر ${formatNumber(n)} آگهی یک بار سوییچ می‌کند —
                چرخش خیلی کم. اگر پیامک احراز هویت زیاد شد، عدد را <b>پایین</b> بیاورید نه بالا.</span>`);
        } else if (n >= 40) {
            parts.push(`<span class="text-muted">هر ${formatNumber(n)} آگهی سوییچ می‌کند —
                هر شماره ${formatNumber(n)} درخواست پشت‌سرهم می‌دهد. اگر دیوار زیاد کد خواست،
                عدد را پایین بیاورید.</span>`);
        } else if (n <= 10) {
            parts.push(`<span class="text-muted">هر ${formatNumber(n)} آگهی سوییچ می‌کند —
                کمترین پیامک، ولی هر سوییچ چند ثانیه به هر اسکرپ اضافه می‌کند.</span>`);
        }
    }
    box.innerHTML = parts.join('<br>');
}

// ─── حالت تعمیر ──────────────────────────────────────────────────────────────
// Paint the card from a state object — used both on load and after a toggle,
// so the two can never disagree about what is currently set.
function renderMaintenanceState(r) {
    const badge = document.getElementById('maintenance-badge');
    if (badge) {
        badge.textContent = r.enabled ? 'روشن — سایت بسته است' : 'خاموش — سایت باز است';
        badge.className = 'badge ' + (r.enabled ? 'bg-danger' : 'bg-success');
    }
    const set = (id, v) => {
        const el = document.getElementById(id);
        if (el && v !== undefined && v !== null) el.value = v;
    };
    const msg = document.getElementById('maintenance-message');
    if (msg && !msg.value) msg.value = r.message || '';
    set('maintenance-phone', r.contact_phone || '');
    set('maintenance-email', r.contact_email || '');

    // Show what the visitor is actually seeing right now, so nobody has to open
    // the closed site in a private window to find out.
    const eta = document.getElementById('maintenance-eta');
    if (eta) {
        if (r.enabled && r.seconds_left > 0) {
            const h = Math.floor(r.seconds_left / 3600);
            const d = Math.floor(h / 24);
            eta.textContent = d >= 1
                ? `زمان‌شمار: حدود ${formatNumber(d)} روز و ${formatNumber(h % 24)} ساعت باقی مانده`
                : `زمان‌شمار: حدود ${formatNumber(h)} ساعت باقی مانده`;
            eta.classList.remove('d-none');
        } else if (r.enabled && r.until) {
            eta.textContent = 'زمان‌شمار به پایان رسیده — بازدیدکننده «در حال نهایی‌سازی» می‌بیند';
            eta.classList.remove('d-none');
        } else {
            eta.classList.add('d-none');
        }
    }
}

// ═══ هوش مصنوعی — what is connected, what it costs ═════════════════════
//
// The key and the base URL are not editable here: they live in GitHub and
// reach the pod as environment variables, so the card shows their state and
// never their value. What IS editable is which model does which job, the
// daily cap, and the office's standing instructions for text people read.
const AI_JOBS = [['write', 'نوشتن فارسی'], ['read', 'خواندن'], ['vision', 'تصویر'], ['embed', 'جستجوی معنایی']];
let _aiStatus = null;

function _aiMoney(usd, toman) {
    const t = toman ? `${formatNumber(Math.round(toman))} تومان` : '۰ تومان';
    const d = usd || 0;
    return `${t} <span class="text-muted" dir="ltr">($${d >= 0.01 ? d.toFixed(2) : d.toFixed(4)})</span>`;
}

// The circuit breaker around the gateway: closed the whole time except right
// after repeated failures. `until` is only set while open.
function _aiUntil(iso) {
    return iso ? new Date(iso).toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' }) : '';
}
function _aiBreakerText(brk) {
    if (!brk || brk.state === 'closed') return null;
    return brk.until ? `مکث تا ${_aiUntil(brk.until)}` : 'در حال تلاش دوباره…';
}

async function loadAi() {
    const badge = document.getElementById('ai-badge');
    if (!badge) return;
    try {
        const s = await apiCall('/ai/status');
        _aiStatus = s;
        const ok = s.configured && s.enabled;
        const paused = _aiBreakerText(s.breaker);
        badge.textContent = !s.configured ? 'تنظیم نشده' : (!s.enabled ? 'خاموش' : (paused || (s.cap_reached ? 'سقف امروز پر شد' : 'فعال')));
        badge.className = 'badge ' + (!s.configured ? 'bg-secondary' : (!s.enabled ? 'bg-secondary' : (paused || s.cap_reached ? 'bg-warning text-dark' : 'bg-success')));

        document.getElementById('ai-conn').innerHTML = s.configured
            ? `<span class="text-success">✓ وصل است</span> <span class="text-muted small">· پروژهٔ ${esc(s.workspace || '—')}</span>`
            : '<span class="text-warning">کلید یا آدرس سرویس در GitHub تنظیم نشده</span>';
        const u = s.usage || { today: {}, month: {} };
        document.getElementById('ai-today').innerHTML =
            `${_aiMoney(u.today.cost_usd, u.today.cost_toman)} <span class="text-muted small">· ${formatNumber(u.today.calls || 0)} درخواست · سقف $${(s.cap_usd || 0).toFixed(2)}</span>`;
        document.getElementById('ai-month').innerHTML =
            `${_aiMoney(u.month.cost_usd, u.month.cost_toman)} <span class="text-muted small">· ${formatNumber(u.month.calls || 0)} درخواست${s.liara ? ` · لیارا: ${formatNumber(s.liara.cost_toman)} تومان` : ''}</span>`;

        const box = document.getElementById('ai-agents');
        if (box) {
            box.innerHTML = (s.agents || []).map(a => `
                <div class="ai-agent ${a.live ? 'is-live' : ''}">
                    <span class="ai-agent-dot"></span>
                    <div>
                        <b>${esc(a.name)}</b>
                        <span class="badge bg-secondary-subtle text-secondary-emphasis">${esc((AI_JOBS.find(j => j[0] === a.job) || [])[1] || a.job)}</span>
                        <div class="text-muted small">${esc(a.desc)}</div>
                    </div>
                    <span class="ai-agent-state">${a.live ? 'فعال' : 'به‌زودی'}</span>
                </div>`).join('');
        }
        for (const [job] of AI_JOBS) {
            const input = document.getElementById(`ai-model-${job}`);
            const hint = document.getElementById(`ai-model-${job}-hint`);
            if (input && !input.dataset.touched) input.value = (s.model_sources || {})[job] === 'panel' ? (s.models || {})[job] || '' : '';
            if (input) input.placeholder = (s.env_models || {})[job] || '';
            if (hint) hint.textContent = (s.model_sources || {})[job] === 'panel'
                ? `از پنل: ${(s.models || {})[job]} — خالی کنید تا به ${((s.env_models || {})[job] || '—')} برگردد`
                : `از سرور: ${(s.env_models || {})[job] || 'تنظیم نشده'}`;
        }
        const cap = document.getElementById('ai-cap');
        if (cap && !cap.dataset.touched) cap.value = s.cap_usd ?? '';
        const notes = document.getElementById('ai-notes');
        if (notes && !notes.dataset.touched) notes.value = s.notes || '';
        const en = document.getElementById('ai-enabled');
        if (en) en.checked = !!s.enabled;
        _aiAssistantStatus();
    } catch (e) {
        badge.textContent = 'نامشخص'; badge.className = 'badge bg-secondary';
    }
}

['ai-cap', 'ai-notes', 'ai-model-write', 'ai-model-read', 'ai-model-vision', 'ai-model-embed'].forEach(id => {
    document.addEventListener('input', e => { if (e.target && e.target.id === id) e.target.dataset.touched = '1'; });
});

async function aiSave() {
    const out = document.getElementById('ai-result');
    const val = id => (document.getElementById(id)?.value ?? '').trim();
    const body = {
        enabled: document.getElementById('ai-enabled')?.checked,
        notes: val('ai-notes'),
        model_write: val('ai-model-write'), model_read: val('ai-model-read'),
        model_vision: val('ai-model-vision'), model_embed: val('ai-model-embed'),
    };
    const cap = val('ai-cap');
    if (cap !== '') body.daily_cap_usd = Number(_digitsOnly(cap));
    try {
        await apiCall('/ai/settings', { method: 'PUT', body: JSON.stringify(body) });
        ['ai-cap', 'ai-notes', 'ai-model-write', 'ai-model-read', 'ai-model-vision', 'ai-model-embed']
            .forEach(id => { const el = document.getElementById(id); if (el) delete el.dataset.touched; });
        out.textContent = 'ذخیره شد'; out.className = 'small text-success';
        loadAi();
    } catch (e) { out.textContent = e.message; out.className = 'small text-danger'; }
}

async function aiTest() {
    const btn = document.getElementById('ai-test');
    const out = document.getElementById('ai-result');
    btn.disabled = true; out.textContent = 'در حال تست…'; out.className = 'small text-muted';
    try {
        const r = await apiCall('/ai/test', { method: 'POST' });
        out.innerHTML = `✓ ${esc(r.model)} در ${formatNumber(r.ms)} میلی‌ثانیه پاسخ داد — «${esc(r.reply)}»`;
        out.className = 'small text-success';
        loadAi();
    } catch (e) { out.textContent = e.message; out.className = 'small text-danger'; }
    btn.disabled = false;
}

// ── the AI screen: every agent, its state, its cost, its log ──
// How an agent runs. Named for what it is: the listing reader has its own
// kinds — apartment, land, shop — and both were called AI_KIND_FA at the
// top level of files loaded into the same scope, so the second one to load
// threw «already been declared» and took its whole file down with it.
const AI_AGENT_KIND_FA = { loop: 'پس‌زمینه', on_demand: 'هنگام استفاده', telegram: 'تلگرام' };
const AI_JOB_FA = { write: 'نوشتن فارسی', read: 'خواندن', vision: 'تصویر', embed: 'جستجوی معنایی' };

function _aiTokens(n) {
    if (!n && n !== 0) return '—';
    return n >= 1000 ? `${formatNumber(Math.round(n / 1000))} هزار` : formatNumber(n);
}

/** What an agent's own status endpoint said, as one readable line. */
function _aiAgentState(a) {
    const st = a.state || {};
    if (st.error) return '<span class="text-warning">وضعیت در دسترس نیست</span>';
    const bits = [];
    if (a.key === 'reader') {
        bits.push(`خوانده: ${formatNumber(st.read || 0)}`);
        if (st.behind != null) bits.push(`در نوبت: ${formatNumber(st.behind)}`);
        if (st.prompt_version != null) bits.push(`پرامپت نسخهٔ ${formatNumber(st.prompt_version)}`);
    } else if (a.key === 'embed') {
        bits.push(`بردار: ${formatNumber(st.embedded || 0)}`);
        if (st.behind != null) bits.push(`در نوبت: ${formatNumber(st.behind)}`);
        bits.push(`تکراری: ${formatNumber(st.duplicates || 0)}`);
    } else if (a.key === 'vision') {
        bits.push(`برچسب‌خورده: ${formatNumber(st.tagged || 0)}`);
        if (st.skipped) bits.push(`بدون عکس: ${formatNumber(st.skipped)}`);
        if (st.behind != null) bits.push(`در نوبت: ${formatNumber(st.behind)}`);
    } else if (a.key === 'assistant') {
        bits.push(`${formatNumber((st.allowed_chats || []).length)} چت مجاز`);
        bits.push(`امروز ${formatNumber(st.questions_today || 0)} سؤال`);
        if (!st.telegram_configured) bits.push('<span class="text-warning">تلگرام تنظیم نشده</span>');
    } else {
        bits.push('بدون صف — هر بار که لازم شود اجرا می‌شود');
    }
    return bits.join(' · ');
}

function _aiAgentCard(a) {
    const st = a.state || {};
    const runnable = a.kind === 'loop';
    const err = (a.today.failed || 0);
    const spent = a.today.cost_usd || 0;
    const capPct = a.cap_usd ? Math.min(100, Math.round(spent / a.cap_usd * 100)) : 0;
    return `
    <div class="ai-card ${a.enabled ? '' : 'is-off'}">
        <div class="ai-card-head">
            <span class="ai-dot ${a.enabled ? 'on' : ''}"></span>
            <div>
                <b>${esc(a.name)}</b>
                <span class="badge bg-secondary-subtle text-secondary-emphasis">${esc(AI_AGENT_KIND_FA[a.kind] || a.kind)}</span>
                <span class="badge bg-primary-subtle text-primary-emphasis" title="کاری که این ایجنت از مدل می‌خواهد">${esc(AI_JOB_FA[a.job] || a.job)}</span>
            </div>
            <div class="form-check form-switch m-0 ms-auto">
                <input class="form-check-input" type="checkbox" id="ai-sw-${esc(a.key)}" ${a.enabled ? 'checked' : ''}
                       onchange="aiAgentToggle(${jsArg(a.key)}, this.checked)">
            </div>
        </div>
        <div class="ai-card-desc">${esc(a.desc)}</div>
        <div class="ai-card-where">${(a.where || []).map(w => `<span class="pill">${esc(w)}</span>`).join('')}</div>
        <div class="ai-card-state">${_aiAgentState(a)}</div>
        <div class="ai-card-budget">
            <span>بودجهٔ امروز: $${spent >= 0.01 ? spent.toFixed(2) : spent.toFixed(4)} از $${(a.cap_usd || 0).toFixed(2)}</span>
            <div class="ai-cap-bar"><span style="width:${capPct}%" class="${capPct >= 100 ? 'is-full' : ''}"></span></div>
            <button class="ai-photo-btn" onclick="aiAgentCapEdit(${jsArg(a.key)}, ${jsArg(a.cap_usd)})">ویرایش سقف</button>
        </div>
        <div class="ai-card-foot">
            <span title="مدل این کار" dir="ltr">${esc(a.model || '—')}</span>
            <span>امروز: ${formatNumber(a.today.calls || 0)} فراخوانی · ${formatNumber(a.today.cost_toman || 0)} تومان</span>
            ${err ? `<button class="ai-err ${(a.today.ok_since_error || 0) >= 5 ? 'is-stale' : ''}"
                onclick="aiShowErrors(${jsArg(a.key)})"
                title="${esc(a.today.last_error || '')}">
                ${formatNumber(err)} خطا${(a.today.ok_since_error || 0) >= 5
                    ? ` · از آن به بعد ${formatNumber(a.today.ok_since_error)} موفق`
                    : ''}${a.today.last_error_at ? ` · آخری ${esc(a.today.last_error_at.slice(11, 16))}` : ''}
            </button>` : ''}
            <span class="text-muted">ماه: ${formatNumber(a.month.calls || 0)} · ${formatNumber(a.month.cost_toman || 0)} تومان</span>
            ${runnable ? `<button class="btn btn-sm btn-outline-primary ms-auto" onclick="aiRunAgent(${jsArg(a.key)}, this)">
                <i class="bi bi-play-fill"></i> اجرای یک دور</button>` : ''}
        </div>
    </div>`;
}

async function loadAiScreen() {
    const grid = document.getElementById('ai-agents-grid');
    if (!grid) return;
    try {
        const s = await apiCall('/ai/overview');
        const u = s.usage || { today: {}, month: {} };
        const paused = _aiBreakerText(s.breaker);
        const conn = document.getElementById('ai-t-conn');
        conn.textContent = paused ? 'موقتاً متوقف' : (s.configured ? (s.enabled ? 'وصل است' : 'خاموش') : 'تنظیم نشده');
        conn.className = 'stat-value ' + (paused ? 'text-warning' : (s.configured && s.enabled ? 'text-success' : 'text-warning'));
        conn.style.fontSize = '1rem';
        document.getElementById('ai-t-conn-sub').textContent = paused || (s.workspace ? `پروژهٔ ${s.workspace}` : 'کلید در GitHub تنظیم نشده');
        document.getElementById('ai-t-today').textContent = `${formatNumber(u.today.cost_toman || 0)} تومان`;
        const capPct = s.cap_usd ? Math.min(100, Math.round((u.today.cost_usd || 0) / s.cap_usd * 100)) : 0;
        document.getElementById('ai-t-cap').innerHTML =
            `${formatNumber(u.today.calls || 0)} فراخوانی · ${formatNumber(capPct)}٪ سقف روزانه` +
            `<div class="ai-cap-bar"><span style="width:${capPct}%" class="${s.cap_reached ? 'is-full' : ''}"></span></div>`;
        document.getElementById('ai-t-month').textContent = `${formatNumber(u.month.cost_toman || 0)} تومان`;
        document.getElementById('ai-t-month-sub').textContent =
            `${formatNumber(u.month.calls || 0)} فراخوانی · ${_aiTokens(u.month.tokens)} توکن` + (s.liara ? ` · لیارا: ${formatNumber(s.liara.cost_toman)} تومان` : '');
        const q = s.quota && s.quota.daily;
        document.getElementById('ai-t-quota').textContent = q
            ? `${_aiTokens(q.remainingPromptFreeTokens)} ورودی`
            : (s.quota === null ? 'توکن لیارا تنظیم نشده' : '—');
        document.getElementById('ai-t-quota-sub').textContent = q
            ? `${_aiTokens(q.remainingCompletionFreeTokens)} خروجی باقی مانده${s.quota.plan ? ` · پلن ${s.quota.plan}` : ''}`
            : '';

        grid.innerHTML = (s.agents || []).map(_aiAgentCard).join('');
        const sel = document.getElementById('ai-log-agent');
        if (sel && sel.options.length <= 1) {
            sel.innerHTML = '<option value="">همهٔ ایجنت‌ها</option>' +
                (s.agents || []).map(a => `<option value="${esc(a.key)}">${esc(a.name)}</option>`).join('');
        }
    } catch (e) {
        grid.innerHTML = `<div class="text-danger small">${esc(e.message || 'خطا')}</div>`;
    }
}

async function aiAgentToggle(key, on) {
    try {
        await apiCall(`/ai/agents/${encodeURIComponent(key)}`, { method: 'PUT', body: JSON.stringify({ enabled: !!on }) });
        showToast(on ? 'روشن شد' : 'خاموش شد', '', 'success');
        loadAiScreen();
    } catch (e) { showToast('خطا', e.message, 'danger'); loadAiScreen(); }
}

/** This agent's own daily ceiling — on top of the shared one, so a noisy
 * agent stops alone instead of using up everyone else's budget too. */
async function aiAgentCapEdit(key, current) {
    const v = await askText({
        icon: 'bi-cash-coin', title: 'سقف روزانهٔ این ایجنت',
        body: 'با پر شدن این سقف، فقط همین ایجنت تا فردا صبر می‌کند؛ بقیه ادامه می‌دهند.',
        field: { label: 'سقف (دلار)', type: 'number', inputmode: 'decimal', dir: 'ltr',
                 value: current || '0', placeholder: '۰ تا ۱۰۰',
                 validate: v => {
                     const n = Number(v);
                     return v !== '' && !isNaN(n) && n >= 0 && n <= 100 ? '' : 'عددی بین ۰ و ۱۰۰ وارد کنید';
                 } } });
    if (v === null) return;
    try {
        await apiCall(`/ai/agents/${encodeURIComponent(key)}/cap`, { method: 'PUT', body: JSON.stringify({ cap_usd: Number(v) }) });
        showToast('ذخیره شد', '', 'success');
        loadAiScreen();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

/** «اجرای یک دور» — one pass now, for an agent that otherwise waits for its tick. */
async function aiRunAgent(key, btn) {
    const url = { reader: '/ai/reader/run', embed: '/ai/embed/run', vision: '/ai/photo/run' }[key];
    if (!url) return;
    if (btn) btn.disabled = true;
    try {
        const r = await apiCall(url, { method: 'POST' });
        const done = r.read ?? r.embedded ?? r.tagged ?? 0;
        showToast('اجرا شد', `${formatNumber(r.scanned || 0)} بررسی، ${formatNumber(done)} انجام${r.failed ? `، ${formatNumber(r.failed)} خطا` : ''}${r.stopped ? ` — متوقف: ${r.stopped}` : ''}`, r.failed ? 'warning' : 'success');
        loadAiScreen(); loadAiLog();
    } catch (e) { showToast('اجرا نشد', e.message, 'danger'); }
    if (btn) btn.disabled = false;
}

/** The error chip: the log, filtered to this agent's failures, in view. */
function aiShowErrors(key) {
    const sel = document.getElementById('ai-log-agent');
    const only = document.getElementById('ai-log-failed');
    if (sel) sel.value = key;
    if (only) only.checked = true;
    loadAiLog();
    document.getElementById('ai-log-table')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function loadAiLog() {
    const body = document.getElementById('ai-log-rows');
    if (!body) return;
    const agent = document.getElementById('ai-log-agent')?.value || '';
    const failed = document.getElementById('ai-log-failed')?.checked;
    try {
        const d = await apiCall(`/ai/log?limit=60${agent ? '&agent=' + encodeURIComponent(agent) : ''}${failed ? '&failed_only=true' : ''}`);
        const items = d.items || [];
        document.getElementById('ai-log-note').textContent = `${formatNumber(items.length)} فراخوانی اخیر`;
        body.innerHTML = items.length ? items.map(i => `
            <tr class="${i.ok ? '' : 'ai-log-bad'}">
                <td class="small text-muted" dir="ltr">${esc((i.created_at || '').slice(5, 16).replace('T', ' '))}</td>
                <td>${esc(i.agent)}</td>
                <td class="small">${esc(AI_JOB_FA[i.job] || i.job)}</td>
                <td class="small text-muted" dir="ltr">${esc(i.model || '—')}</td>
                <td>${formatNumber((i.prompt_tokens || 0) + (i.completion_tokens || 0))}</td>
                <td>${formatNumber(Math.round(i.cost_toman || 0))}</td>
                <td>${formatNumber(i.ms || 0)}</td>
                <td>${i.ok ? '<span class="text-success">✓</span>' : `<span class="text-danger" title="${esc(i.error || '')}">✗ ${esc((i.error || '').slice(0, 40))}</span>`}</td>
            </tr>`).join('') : '<tr><td colspan="8" class="text-center text-muted py-3">چیزی ثبت نشده</td></tr>';
    } catch (e) {
        body.innerHTML = `<tr><td colspan="8" class="text-danger text-center py-3">${esc(e.message)}</td></tr>`;
    }
}

async function loadAiChats() {
    const box = document.getElementById('ai-chats');
    if (!box) return;
    try {
        const d = await apiCall('/ai/assistant/log?limit=20');
        const items = d.items || [];
        box.innerHTML = items.length ? items.map(i => `
            <div class="ai-q">
                <div class="ai-q-head"><b>${esc(i.who || '—')}</b>
                    <span class="text-muted">${esc((i.created_at || '').slice(5, 16).replace('T', ' '))}${i.chat_id ? '' : ' · از پنل'}${(i.tools || []).length ? ' · ' + esc(i.tools.join('، ')) : ''}</span></div>
                <div class="ai-q-q">${esc(i.question)}</div>
                <div class="ai-q-a ${i.ok ? '' : 'text-danger'}">${esc(i.answer || '')}</div>
            </div>`).join('') : '<div class="text-muted small">هنوز کسی چیزی نپرسیده — در تلگرام به ربات پیام بدهید یا «بپرس» را بزنید.</div>';
    } catch (e) {
        box.innerHTML = `<div class="text-danger small">${esc(e.message || 'خطا')}</div>`;
    }
}

// ── «سورین», the Telegram assistant ──
async function _aiAssistantStatus() {
    try {
        const s = await apiCall('/ai/assistant/status');
        const sw = document.getElementById('ai-assistant-enabled');
        if (sw) sw.checked = !!s.enabled;
        const last = document.getElementById('ai-assistant-last');
        if (last) {
            last.textContent = !s.telegram_configured ? 'تا تلگرام (کارت بکاپ) تنظیم نشود، جواب نمی‌دهد'
                : !s.configured ? 'تا هوش مصنوعی وصل نشود، جواب نمی‌دهد'
                : `${formatNumber(s.allowed_chats.length)} چت مجاز · امروز ${formatNumber(s.questions_today)} سؤال${s.last ? ' · آخری: «' + (s.last.question || '').slice(0, 40) + '»' : ''}`;
        }
    } catch (_) {}
}

async function aiAssistantToggle(on) {
    try {
        await apiCall('/ai/assistant/settings', { method: 'PUT', body: JSON.stringify({ enabled: !!on }) });
        showToast(on ? 'روشن شد' : 'خاموش شد', on ? 'سورین به چت‌های مجاز جواب می‌دهد' : 'سورین دیگر جواب نمی‌دهد', 'success');
        _aiAssistantStatus();
    } catch (e) { showToast('خطا', e.message, 'danger'); _aiAssistantStatus(); }
}

/** «بپرس»: the same assistant, from the panel — for a question without opening Telegram. */
async function aiAssistantAsk() {
    const q = await askText({ icon: 'bi-chat-dots', title: 'از سورین بپرسید', okLabel: 'بپرس',
        body: 'همان جوابی که در تلگرام می‌دهد؛ اینجا ثبت می‌شود که از پنل پرسیده شده.',
        field: { label: 'سؤال', placeholder: 'مثلاً: امروز چند آگهی تازه اومد؟', validate: v => v ? '' : 'سؤال خالی است' } });
    if (q === null) return;
    let r;
    try { r = await apiCall('/ai/assistant/ask', { method: 'POST', body: JSON.stringify({ text: q }) }); }
    catch (e) { showToast('جواب نگرفتم', e.message, 'danger'); return; }
    await askInfo({ icon: 'bi-chat-square-text', title: 'سورین', okLabel: 'بستن',
        body: `<span class="ask-pre">${esc(r.text)}</span>`,
        note: `${formatNumber(r.ms)} میلی‌ثانیه${(r.tools || []).length ? ' · ابزارها: ' + esc(r.tools.join('، ')) : ''}` });
    _aiAssistantStatus();
    if (document.getElementById('ai-chats')) { loadAiChats(); loadAiLog(); loadAiScreen(); }
}

async function aiAssistantLog() {
    let d;
    try { d = await apiCall('/ai/assistant/log?limit=30'); }
    catch (e) { showToast('خطا', e.message, 'danger'); return; }
    const rows = (d.items || []).map(i => `
        <div class="ai-q">
            <div class="ai-q-head"><b>${esc(i.who || '—')}</b> <span class="text-muted">${esc((i.created_at || '').slice(5, 16).replace('T', ' '))}${i.chat_id ? '' : ' · از پنل'}</span></div>
            <div class="ai-q-q">${esc(i.question)}</div>
            <div class="ai-q-a ${i.ok ? '' : 'text-danger'}">${esc(i.answer || '')}</div>
        </div>`).join('');
    await askInfo({ icon: 'bi-list-ul', title: 'سؤال‌های سورین', okLabel: 'بستن',
        body: `<div class="ai-usage-wrap">${rows || '<div class="text-muted">هنوز کسی چیزی نپرسیده</div>'}</div>` });
}

/** «آخرین مصرف‌ها» — what each call cost, newest first. */
async function aiUsage() {
    let d;
    try { d = await apiCall('/ai/usage?limit=30'); }
    catch (e) { showToast('خطا', e.message, 'danger'); return; }
    const rows = (d.items || []).map(i => `
        <tr>
            <td>${esc(i.agent)}</td>
            <td class="text-muted" dir="ltr">${esc(i.model || '—')}</td>
            <td>${formatNumber((i.prompt_tokens || 0) + (i.completion_tokens || 0))}</td>
            <td>${formatNumber(Math.round(i.cost_toman || 0))}</td>
            <td>${formatNumber(i.ms || 0)}</td>
            <td>${i.ok ? '<span class="text-success">✓</span>' : `<span class="text-danger" title="${esc(i.error || '')}">✗</span>`}</td>
            <td class="small text-muted">${esc((i.created_at || '').slice(11, 16))}</td>
        </tr>`).join('');
    const by = (d.summary?.by_agent || []).map(a =>
        `<span class="pill">${esc(a.agent)}: ${formatNumber(Math.round(a.cost_toman))} تومان${a.failed ? ` · ${formatNumber(a.failed)} خطا` : ''}</span>`).join(' ');
    await askInfo({
        icon: 'bi-list-ul', title: 'آخرین مصرف‌های هوش مصنوعی', okLabel: 'بستن',
        body: `<div class="ai-usage-wrap"><div class="mb-2">${by || 'هنوز مصرفی ثبت نشده'}</div>
            <table class="table table-sm mb-0"><thead><tr><th>ایجنت</th><th>مدل</th><th>توکن</th><th>تومان</th><th>ms</th><th></th><th>ساعت</th></tr></thead>
            <tbody>${rows || '<tr><td colspan="7" class="text-center text-muted">—</td></tr>'}</tbody></table></div>`,
    });
}

// ═══ Backup — the nightly snapshot and its copy off the server ═════
function _bkWhen(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return `${d.toLocaleDateString('fa-IR')} ${d.toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' })}`;
}

async function loadBackup() {
    const badge = document.getElementById('bk-badge');
    if (!badge) return;
    try {
        const s = await apiCall('/backup/status');
        const snap = s.snapshots && s.snapshots[0];
        document.getElementById('bk-local').innerHTML = snap
            ? `${_bkWhen(snap.at)} <span class="text-muted">· ${formatNumber(snap.size_kb)} KB · ${formatNumber(s.snapshot_count)} نسخه</span>`
            : '<span class="text-warning">هنوز نسخه‌ای گرفته نشده</span>';
        const lo = s.last_offsite || {};
        const off = document.getElementById('bk-offsite');
        if (!s.configured) {
            off.innerHTML = '<span class="text-warning">تنظیم نشده — تا تنظیم نشود، نسخه فقط روی همین سرور است</span>';
        } else if (!lo.at) {
            off.innerHTML = '<span class="text-muted">تنظیم شده؛ اولین ارسال امشب — یا همین حالا دکمه را بزنید</span>';
        } else if (lo.ok) {
            const to = (lo.delivered || []).length ? ` · به ${formatNumber((lo.delivered || []).length)} چت` : '';
            const partial = lo.error ? ` <span class="text-warning">— نرسید: ${esc(lo.error)}</span>` : '';
            off.innerHTML = `<span class="text-success">✓ ${_bkWhen(lo.at)}</span> <span class="text-muted">· ${esc(lo.file || '')}${to}</span>${partial}`;
        } else {
            off.innerHTML = `<span class="text-danger">✗ ${_bkWhen(lo.at)} — ${esc(lo.error || 'ارسال نشد')}</span>`;
        }
        badge.textContent = !s.configured ? 'بدون نسخهٔ خارج از سرور' : (lo.at && !lo.ok ? 'ارسال ناموفق' : 'فعال');
        badge.className = 'badge ' + (!s.configured ? 'bg-warning text-dark' : (lo.at && !lo.ok ? 'bg-danger' : 'bg-success'));
        document.getElementById('bk-token-hint').textContent = s.token_masked
            ? `ذخیره‌شده: ${s.token_masked}${s.source === 'env' ? ' (از محیط سرور — قابل تغییر از پنل نیست)' : ''}`
            : '';
        const chat = document.getElementById('bk-chat');
        if (s.chat_id && !chat.value) chat.value = s.chat_id;
        document.getElementById('bk-token').disabled = s.source === 'env';
        chat.disabled = s.source === 'env';
        _bkRenderRoute(s);
        _bkRenderDigest(s);
    } catch (e) {
        badge.textContent = 'نامشخص'; badge.className = 'badge bg-secondary';
    }
    loadDr();
}

// ── the full bundle (بکاپ کامل): built and shipped by the host, not the pod ──
const _drVia = v => v === 'direct' ? 'مستقیم' : v === 'relay' ? 'رله' : v;

async function loadDr() {
    const badge = document.getElementById('dr-badge');
    if (!badge) return null;
    let s;
    try { s = await apiCall('/backup/dr'); }
    catch (e) { badge.textContent = 'نامشخص'; badge.className = 'badge bg-secondary'; return null; }
    const run = s.last_run || {}, sent = run.sent || {};
    // a run that died before shipping only leaves an alert; newer than the
    // last shipment means the last shipment is not the current state
    const failed = s.last_alert && (!sent.at || s.last_alert.at > sent.at);
    const last = document.getElementById('dr-last');
    if (!sent.at) {
        last.innerHTML = html`<span class="text-muted">هنوز اجرا نشده — ${s.schedule_fa}</span>`;
    } else if (sent.ok) {
        const size = run.bytes ? ` · ${formatNumber(Math.max(1, Math.round(run.bytes / 1048576)))} مگابایت در ${formatNumber(run.parts || 1)} تکه` : '';
        last.innerHTML = html`<span class="text-success">✓ ${_bkWhen(sent.at)}</span> <span class="text-muted">· به ${formatNumber((sent.delivered || []).length)} چت · ${(sent.via || []).map(_drVia).join('، ')}${size}</span>`
            + (sent.error ? html` <span class="text-warning">— نرسید: ${sent.error}</span>` : '');
    } else {
        last.innerHTML = html`<span class="text-danger">✗ ${_bkWhen(sent.at)} — ${sent.error || 'ارسال نشد'}</span>`;
    }
    const state = [];
    if (failed) state.push(html`<div class="text-danger">✗ ${_bkWhen(s.last_alert.at)} — ${s.last_alert.text}</div>`);
    if (s.requested) state.push('<div class="text-info">در صف — سرور تا چند دقیقهٔ دیگر شروع می‌کند</div>');
    if ((s.undelivered || []).length) state.push(html`<div class="text-warning">${formatNumber(s.undelivered.length)} بستهٔ ارسال‌نشده منتظر تلاش بعدی</div>`);
    document.getElementById('dr-state').innerHTML = state.join('')
        || html`<span class="text-muted">${s.schedule_fa}</span>`;
    const bad = failed || (sent.at && !sent.ok);
    badge.textContent = bad ? 'ناموفق' : s.requested ? 'در صف' : sent.ok ? 'فعال' : 'در انتظار اولین اجرا';
    badge.className = 'badge ' + (bad ? 'bg-danger' : s.requested ? 'bg-info text-dark' : sent.ok ? 'bg-success' : 'bg-secondary');
    return s;
}

/** «همین حالا»: the pod only drops a request; the host's watcher builds the bundle. */
async function drRunNow() {
    const btn = document.getElementById('dr-run');
    btn.disabled = true;
    try {
        await apiCall('/backup/dr/run', { method: 'POST' });
        showToast('درخواست ثبت شد', 'سرور بکاپ کامل را می‌سازد و به تلگرام می‌فرستد — چند دقیقه طول می‌کشد', 'success');
    } catch (e) {
        showToast('ثبت نشد', e.message, 'warning');
    }
    btn.disabled = false;
    // follow the host for a few minutes, until it has picked the request up
    for (let i = 0; i < 12; i++) {
        const s = await loadDr();
        if (!s || !s.requested) break;
        await new Promise(r => setTimeout(r, 15000));
    }
}

/** «تست همهٔ راه‌ها»: getMe straight, through the relay, and through each proxy. */
async function drDiagnose() {
    const btn = document.getElementById('dr-diag');
    const box = document.getElementById('dr-diag-rows');
    btn.disabled = true;
    box.innerHTML = '<span class="text-muted">هر راه جدا امتحان می‌شود…</span>';
    try {
        const r = await apiCall('/backup/diagnose', { method: 'POST' });
        const name = { direct: 'مستقیم', relay: 'رله', proxy: 'پراکسی' };
        box.innerHTML = (r.rows || []).map(x => html`<div class="${x.ok ? 'text-success' : 'text-danger'}">${x.ok ? '✓' : '✗'} ${name[x.route] || x.route} <span dir="ltr" class="text-muted">${x.target}</span> · ${formatNumber(x.ms)} ms${x.ok ? '' : ' — ' + (x.error || 'نرسید')}</div>`).join('')
            || '<span class="text-muted">هیچ راهی تنظیم نشده است</span>';
    } catch (e) {
        box.innerHTML = html`<span class="text-danger">${e.message}</span>`;
    }
    btn.disabled = false;
}

/** The Worker's code, read from the one the repo tests (deploy/telegram-relay/worker.js). */
async function bkToggleWorker() {
    const pre = document.getElementById('bk-relay-code');
    pre.classList.toggle('d-none');
    if (pre.classList.contains('d-none') || pre.textContent) return;
    pre.textContent = 'در حال خواندن…';
    try {
        pre.textContent = await apiCall('/backup/relay-worker', { raw: true });
    } catch (e) {
        pre.textContent = `کد Worker خوانده نشد: ${e.message}`;
    }
}

// ── the morning digest ──
function _bkRenderDigest(s) {
    const hour = document.getElementById('bk-digest-hour');
    const last = document.getElementById('bk-digest-last');
    if (!hour || !last) return;
    if (s.digest_hour != null && s.digest_hour < 0) {
        hour.textContent = '—';
        last.textContent = 'خاموش است (DIGEST_HOUR=-1 روی سرور)';
        return;
    }
    if (s.digest_hour != null) hour.textContent = formatNumber(s.digest_hour);
    last.textContent = s.digest_last_sent
        ? `آخرین ارسال: ${new Date(s.digest_last_sent + 'T00:00:00').toLocaleDateString('fa-IR')}`
        : (s.configured ? 'هنوز فرستاده نشده — اولی بعد از همین ساعت می‌رود' : 'تا تلگرام تنظیم نشود، فرستاده نمی‌شود');
}

/** «پیش‌نمایش و ارسال الان»: the message as it would go now, then one extra send. */
async function bkDigest() {
    const btn = document.getElementById('bk-digest-btn');
    btn.disabled = true;
    let pv;
    try { pv = await apiCall('/backup/digest'); }
    catch (e) { showToast('خطا', e.message, 'danger'); btn.disabled = false; return; }
    const go = await askConfirm({
        icon: 'bi-brightness-high', title: 'خلاصهٔ صبحگاهی',
        body: `<span class="ask-pre">${esc(pv.text)}</span>`,
        note: pv.configured ? 'یک نسخهٔ اضافه همین حالا فرستاده می‌شود؛ خلاصهٔ روزانه سر ساعت خودش می‌رود.'
                            : 'تلگرام تنظیم نشده است — فقط پیش‌نمایش.',
        okLabel: pv.configured ? 'ارسال به تلگرام' : 'باشه', cancelLabel: 'بستن',
    });
    if (go && pv.configured) {
        try {
            const r = await apiCall('/backup/digest/send', { method: 'POST' });
            showToast('فرستاده شد', `به ${formatNumber((r.delivered || []).length)} چت${r.error ? ' — ' + r.error : ''}`, r.error ? 'warning' : 'success');
        } catch (e) { showToast('فرستاده نشد', e.message, 'danger'); }
    }
    btn.disabled = false;
}

// ── the way out to Telegram: manual proxy, the dashboard's pool, or a relay ──
let _bkPool = [];            // the dashboard's proxies, for the pool pane
let _bkStatus = null;

function _bkMode() {
    return document.querySelector('input[name=bk-mode]:checked')?.value || 'manual';
}

function _bkRenderRoute(s) {
    _bkStatus = s;
    const mode = s.proxy_mode || 'manual';
    const r = document.querySelector(`input[name=bk-mode][value=${mode}]`);
    if (r) r.checked = true;
    const px = document.getElementById('bk-proxy'), ph = document.getElementById('bk-proxy-hint');
    if (px && ph) {
        if (s.proxy_masked) {
            ph.innerHTML = `ذخیره‌شده: <span dir="ltr">${esc(s.proxy_masked)}</span>${s.proxy_source === 'env' ? ' (از محیط سرور)' : ''}
                — برای عوض کردن، آدرس تازه را بنویسید و ذخیره کنید؛ <a href="#" onclick="event.preventDefault(); bkClearProxy()">حذف پراکسی</a>`;
            px.placeholder = 'بدون تغییر';
        } else {
            ph.textContent = 'یک پراکسی خارج از ایران (http یا socks5).';
        }
    }
    const rl = document.getElementById('bk-relay');
    if (rl && s.relay && !rl.value) rl.value = s.relay;
    const rk = document.getElementById('bk-relay-key');
    if (rk) rk.placeholder = s.relay_key_set ? 'کلید ذخیره شده — خالی یعنی بدون تغییر' : 'کلید رله (اختیاری)';
    const now = document.getElementById('bk-route-now');
    if (now) now.textContent = s.route_label ? `الان: ${s.route_label}` : 'هنوز راهی تنظیم نشده';
    document.querySelectorAll('input[name=bk-mode]').forEach(i => { i.disabled = s.proxy_source === 'env'; });
    bkModeChanged(true);
    if (mode === 'pool' || _bkPool.length === 0) bkLoadPool(s.proxy_pool || '*');
}

function bkModeChanged(keep) {
    const mode = _bkMode();
    ['manual', 'pool', 'relay'].forEach(m => document.getElementById('bk-pane-' + m)?.classList.toggle('d-none', m !== mode));
    if (mode === 'pool' && !_bkPool.length) bkLoadPool(_bkStatus?.proxy_pool || '*');
    if (!keep) { const out = document.getElementById('bk-proxy-result'); if (out) out.textContent = ''; }
}

/** The dashboard's proxy list, as checkboxes; the saved selection ticked. */
async function bkLoadPool(spec) {
    const box = document.getElementById('bk-pool-list');
    if (!box) return;
    try {
        const d = await apiCall('/proxies?active_only=true');
        _bkPool = d.items || [];
        const chosen = new Set((spec || '*') === '*' ? _bkPool.map(p => String(p.id)) : String(spec).split(/[\s,،;]+/).filter(Boolean));
        const all = (spec || '*') === '*';
        const allBox = document.getElementById('bk-pool-all');
        if (allBox) allBox.checked = all;
        box.innerHTML = _bkPool.length ? _bkPool.map(p => `
            <label>
              <input type="checkbox" class="form-check-input m-0 bk-pool-item" value="${p.id}" ${chosen.has(String(p.id)) ? 'checked' : ''} ${all ? 'disabled' : ''}
                     onchange="document.getElementById('bk-pool-all').checked=false">
              <span dir="ltr">${esc(p.protocol)}://${esc(p.address)}:${esc(String(p.port))}</span>
              <span class="flag">${p.exit_country ? esc(p.exit_country) + (p.exit_country === 'IR' ? ' — به تلگرام نمی‌رسد' : '') : '—'}</span>
              ${p.is_working === false ? '<span class="badge bg-warning text-dark">در تست دیوار ناموفق</span>' : ''}
            </label>`).join('')
            : '<span class="text-muted small">پراکسی فعالی در فهرست نیست — در بخش «پراکسی‌ها» اضافه کنید.</span>';
    } catch (e) {
        box.innerHTML = `<span class="text-danger small">${esc(e.message || 'فهرست خوانده نشد')}</span>`;
    }
}

function bkPoolAll(on) {
    document.querySelectorAll('.bk-pool-item').forEach(i => { i.disabled = on; if (on) i.checked = true; });
}

function _bkPoolSpec() {
    if (document.getElementById('bk-pool-all')?.checked) return '*';
    return [...document.querySelectorAll('.bk-pool-item:checked')].map(i => i.value).join(',');
}

/** What the form says right now, for a test or a save. */
function _bkRouteBody() {
    const mode = _bkMode();
    const body = { proxy_mode: mode };
    if (mode === 'manual') { const v = document.getElementById('bk-proxy').value.trim(); if (v) body.proxy = v; }
    if (mode === 'pool') body.proxy_pool = _bkPoolSpec();
    if (mode === 'relay') {
        body.relay = document.getElementById('bk-relay').value.trim();
        const k = document.getElementById('bk-relay-key').value.trim();
        if (k) body.relay_key = k;
    }
    return body;
}

/** getMe the way the form says: the bot's name and the round trip, or the
 *  reason it did not answer — per proxy when the pool is being tested. */
async function bkProxyTest() {
    const out = document.getElementById('bk-proxy-result');
    const btn = document.getElementById('bk-proxy-test');
    const tok = document.getElementById('bk-token').value.trim();
    const res = document.getElementById('bk-pool-results');
    btn.disabled = true; out.className = 'small text-muted'; out.textContent = 'در حال تست…';
    if (res) res.innerHTML = '';
    try {
        const body = _bkRouteBody();
        if (body.proxy_mode === 'manual' && !body.proxy && !_bkStatus?.proxy_masked) throw new Error('آدرس پراکسی را بنویسید');
        if (body.proxy_mode === 'pool' && !body.proxy_pool) throw new Error('حداقل یک پراکسی را تیک بزنید');
        if (body.proxy_mode === 'relay' && !body.relay) throw new Error('آدرس رله را بنویسید');
        // manual with nothing typed tests the saved one
        if (body.proxy_mode === 'manual' && !body.proxy) delete body.proxy_mode;
        const r = await apiCall('/backup/proxy-test', { method: 'POST', body: JSON.stringify({ ...body, bot_token: tok || null }) });
        out.className = 'small text-success';
        out.innerHTML = `✓ ربات <b dir="ltr">@${esc(r.bot)}</b> در ${formatNumber(r.ms)} ms جواب داد <span class="text-muted">(${esc(r.proxy || r.via || '')})</span>`;
        if (res && r.results) res.innerHTML = r.results.map(x => `<div dir="ltr" class="${x.ok ? 'text-success' : 'text-danger'}">${x.ok ? '✓' : '✗'} ${esc(x.proxy)} ${x.ok ? formatNumber(x.ms) + ' ms' : esc(x.error || '')}</div>`).join('');
    } catch (e) {
        out.className = 'small text-danger'; out.textContent = e.message;
    }
    btn.disabled = false;
}

async function bkClearProxy() {
    if (!await askConfirm({ icon: 'bi-x-circle', title: 'حذف پراکسی', tone: 'warning', okLabel: 'حذف',
        body: 'پراکسی تلگرام حذف شود؟ تا پراکسی تازه‌ای ندهید، نسخه‌ها به تلگرام نمی‌رسند.' })) return;
    try {
        await apiCall('/backup/settings', { method: 'PUT', body: JSON.stringify({ proxy: '' }) });
        document.getElementById('bk-proxy').value = '';
        showToast('حذف شد', 'پراکسی تلگرام حذف شد', 'success');
        loadBackup();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function bkProbe() {
    const box = document.getElementById('bk-chats');
    const tok = document.getElementById('bk-token').value.trim();
    box.classList.remove('d-none');
    box.innerHTML = '<span class="small text-muted">در حال پرسیدن از تلگرام…</span>';
    try {
        const proxy = document.getElementById('bk-proxy')?.value.trim() || null;
        const r = await apiCall('/backup/probe', { method: 'POST', body: JSON.stringify({ bot_token: tok || null, proxy }) });
        if (!r.chats.length) {
            box.innerHTML = `<div class="small text-warning">ربات <b dir="ltr">@${esc(r.bot)}</b> پیدا شد، ولی ${esc(r.hint_fa || 'چتی ندارد')}</div>`;
            return;
        }
        box.innerHTML = `<div class="small text-muted mb-1">ربات <b dir="ltr">@${esc(r.bot)}</b> — یکی را انتخاب کنید:</div>` +
            r.chats.map(c => `<button class="btn btn-sm btn-outline-secondary me-1 mb-1" onclick="bkPickChat(${jsArg(c.id)})">
                ${esc(c.name || c.id)} <span class="text-muted" dir="ltr">${esc(c.id)}</span></button>`).join('') +
            '<div class="small text-muted mt-1">هر کدام را بزنید به فهرست اضافه می‌شود؛ بعد «ذخیره».</div>';
    } catch (e) {
        box.innerHTML = `<span class="small text-danger">${esc(e.message)}</span>`;
    }
}

/** A picked chat joins the list rather than replacing it. */
function bkPickChat(id) {
    const el = document.getElementById('bk-chat');
    const have = el.value.split(/[\s,،;]+/).filter(Boolean);
    if (!have.includes(id)) have.push(id);
    el.value = have.join(', ');
}

async function bkSave() {
    const tok = document.getElementById('bk-token').value.trim();
    const chat = document.getElementById('bk-chat').value.trim();
    const body = { chat_id: chat, ..._bkRouteBody() };
    if (tok) body.bot_token = tok;      // an empty field means «leave the saved one»
    // manual with nothing typed keeps the saved URL — «حذف پراکسی» is the way to clear it
    try {
        await apiCall('/backup/settings', { method: 'PUT', body: JSON.stringify(body) });
        document.getElementById('bk-token').value = '';
        if (body.proxy) document.getElementById('bk-proxy').value = '';
        if (body.relay_key) document.getElementById('bk-relay-key').value = '';
        showToast('ذخیره شد', 'حالا «همین حالا بکاپ بگیر و بفرست» را بزنید تا ببینید می‌رسد', 'success');
        loadBackup();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function bkRunNow() {
    const btn = document.getElementById('bk-run');
    const out = document.getElementById('bk-result');
    btn.disabled = true; out.textContent = 'در حال گرفتن نسخه و ارسال…';
    try {
        const r = await apiCall('/backup/run', { method: 'POST' });
        out.textContent = r.telegram_sent
            ? `✓ ${r.file} (${formatNumber(r.size_kb)} KB) به تلگرام رسید`
            : `نسخه گرفته شد (${formatNumber(r.size_kb)} KB) ولی به تلگرام نرسید${r.last_offsite && r.last_offsite.error ? ': ' + r.last_offsite.error : ''}`;
        out.className = 'small ' + (r.telegram_sent ? 'text-success' : 'text-warning');
        loadBackup();
    } catch (e) {
        out.textContent = e.message; out.className = 'small text-danger';
    }
    btn.disabled = false;
}

async function loadMaintenance() {
    const badge = document.getElementById('maintenance-badge');
    if (!badge) return;
    try {
        renderMaintenanceState(await apiCall('/maintenance'));
    } catch (e) {
        badge.textContent = 'نامشخص';
        badge.className = 'badge bg-secondary';
    }
}

async function setMaintenance(enabled, opts = {}) {
    const message = document.getElementById('maintenance-message')?.value.trim() || null;
    const hours   = document.getElementById('maintenance-hours')?.value ?? '';
    const phone   = document.getElementById('maintenance-phone')?.value.trim() || '';
    const email   = document.getElementById('maintenance-email')?.value.trim() || '';

    // re-saving settings on an already-closed site should not ask again
    if (enabled && !opts.silent && !await askConfirm({ icon: 'bi-question-lg', title: 'تأیید', okLabel: 'تأیید', body: 'سایت برای همه بسته می‌شود. مطمئن هستید؟' })) return;
    try {
        const data = await apiCall('/maintenance', {
            method: 'POST',
            body: JSON.stringify({
                enabled, message,
                // only meaningful when closing; the server clears it on reopen
                hours: enabled && hours !== '' ? Number(hours) : null,
                contact_phone: phone, contact_email: email,
            }),
        });
        if (!opts.silent) {
            showToast('انجام شد', enabled ? 'سایت بسته شد' : 'سایت باز شد', 'success');
        }
        renderMaintenanceState(data);
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

// Update the countdown, contacts and message on a site that is ALREADY closed,
// without toggling it. Without this the only way to set a deadline was to
// reopen and re-close, which is a visible outage just to change a label.
async function saveMaintenanceSettings() {
    const enabled = document.getElementById('maintenance-badge')
        ?.textContent.includes('روشن');
    if (!enabled) {
        showToast('توجه', 'سایت باز است — تنظیمات هنگام بستن اعمال می‌شود', 'info');
        return;
    }
    await setMaintenance(true, { silent: true });
    showToast('ذخیره شد', 'تنظیمات صفحهٔ تعمیر بروزرسانی شد', 'success');
}

async function copyMaintenanceLink() {
    const el = document.getElementById('maintenance-bypass-url');
    if (!el) return;
    try {
        await navigator.clipboard.writeText(el.value);
        showToast('کپی شد', 'لینک دسترسی کپی شد', 'success');
    } catch (_) {
        el.select();
        showToast('انتخاب شد', 'با Ctrl+C کپی کنید', 'info');
    }
}

// ─── Scraper log viewer ───────────────────────────────────────────────────────
// The scraper explains every decision it makes — which listing it skipped and
// why — but until now that only ever went to a file on the server. A scrape
// that saved nothing was indistinguishable from a scrape that was broken.
function openScraperLog() {
    bootstrap.Modal.getOrCreateInstance(document.getElementById('scraperLogModal')).show();
    loadScraperLog();
}

async function loadScraperLog() {
    const body = document.getElementById('scraper-log-body');
    if (!body) return;
    const grep = document.getElementById('scraper-log-grep')?.value.trim() || '';
    body.textContent = 'در حال بارگیری...';
    try {
        const data = await apiCall(
            `/stats/logs?lines=300${grep ? '&grep=' + encodeURIComponent(grep) : ''}`);
        const lines = data.lines || [];
        if (data.note) { body.textContent = data.note; return; }
        if (!lines.length) {
            body.textContent = grep
                ? 'خطی با این عبارت پیدا نشد.'
                : 'لاگی ثبت نشده است.';
            return;
        }
        // newest last is how a log reads; scroll there
        body.textContent = lines.join('\n');
        body.scrollTop = body.scrollHeight;
    } catch (e) {
        body.textContent = 'خطا در خواندن لاگ: ' + (e?.message || '');
    }
}

/* «ادامه» — a stopped run, picked up where it left off.
 *
 * A new run with the old one's exact settings. Nothing has to remember a
 * position: every listing the earlier run saved with a number is skipped by
 * the same rule that skips duplicates, so the second run starts at the first
 * listing the first one did not finish.                                     */
async function resumeJob(jobId) {
    try {
        const r = await apiCall(`/scraper/jobs/${encodeURIComponent(jobId)}/resume`, { method: 'POST' });
        showToast('ادامه', `اسکرپ از همان‌جا ادامه یافت: ${r.job_id}`, 'success');
        loadJobs();
    } catch (e) {
        showToast('خطا', e.message, 'danger');
    }
}

async function cancelJob(jobId) {
    if (!await askConfirm({ icon: 'bi-x-octagon', title: 'لغو', tone: 'warning', okLabel: 'لغو کن', body: 'آیا از لغو این تسک اطمینان دارید؟' })) return;

    try {
        const r = await apiCall(`/scraper/jobs/${jobId}/cancel`, { method: 'POST' });
        // cancelling while it waited for a code also closes that prompt
        if (r.otp_cleared) {
            _otp2StopTimer();
            bootstrap.Modal.getInstance(document.getElementById('divarOtpModal'))?.hide();
        }
        showToast('موفق', r.was === 'paused'
            ? 'تسک متوقف‌شده لغو شد' : 'تسک لغو شد', 'success');
        loadJobs();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function deleteJob(jobId) {
    if (!await askConfirm({
        icon: 'bi-trash', title: 'حذف تسک', tone: 'danger', okLabel: 'حذف کن',
        body: 'این اجرا با گزارش و ردشده‌هایش از فهرست پاک می‌شود. آگهی‌هایی که آورده دست‌نخورده می‌مانند. برگشت‌پذیر نیست.'
    })) return;

    try {
        await apiCall(`/scraper/jobs/${jobId}`, { method: 'DELETE' });
        showToast('موفق', 'تسک حذف شد', 'success');
        loadJobs();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function scrapeSingle() {
    const url = document.getElementById('single-url').value;
    
    if (!url || !url.includes('divar.ir/v/')) {
        showToast('خطا', 'لطفاً یک آدرس معتبر دیوار وارد کنید', 'warning');
        return;
    }
    
    // Check cookie status before scraping
    if (!cookieStatus.is_valid) {
        pendingScrapingAction = { type: 'single', url };
        showCookieWarning();
        return;
    }
    
    await executeSingleScraping(url);
}

async function executeSingleScraping(url) {
    const btn = document.getElementById('single-scrape-btn');
    const originalHtml = btn ? btn.innerHTML : null;
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> در حال اسکرپ...';
    }
    try {
        // A job of one. It used to run inside this request and answer
        // «با موفقیت اسکرپ شد» whenever a row was saved — number or not —
        // and had nowhere to put a code prompt. Now it is a run like any
        // other: it appears in the table, its log says what happened, and
        // a code prompt opens the same dialog it would for any run.
        const result = await apiCall('/scraper/scrape-single', {
            method: 'POST',
            body: JSON.stringify({ url })
        });
        showToast('شروع شد', `اسکرپ تکی به‌عنوان تسک ${String(result.job_id).slice(0, 8)} شروع شد — نتیجه در جدول تسک‌ها`, 'info');
        loadJobs();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    }
}

// Cookie Warning Modal Functions
function showCookieWarning() {
    const message = document.getElementById('cookie-warning-message');
    if (cookieStatus.has_cookies) {
        message.textContent = 'نشست شما منقضی شده است. لطفاً دوباره وارد شوید.';
    } else {
        message.textContent = 'شما هنوز وارد حساب دیوار نشده‌اید.';
    }
    
    const modal = new bootstrap.Modal(document.getElementById('cookieWarningModal'));
    modal.show();
    
    // Setup continue button handler
    document.getElementById('continue-scraping-btn').onclick = function() {
        modal.hide();
        continueScraping();
    };
}

function continueScraping() {
    if (!pendingScrapingAction) return;
    
    if (pendingScrapingAction.type === 'bulk') {
        const { city, category, maxItems, downloadImages, filters } = pendingScrapingAction;
        executeBulkScraping(city, category, maxItems, downloadImages, filters);
    } else if (pendingScrapingAction.type === 'single') {
        executeSingleScraping(pendingScrapingAction.url);
    }
    
    pendingScrapingAction = null;
}

function goToAuthSection() {
    const modal = bootstrap.Modal.getInstance(document.getElementById('cookieWarningModal'));
    if (modal) modal.hide();
    showSection('auth');
}

// ==================== Authentication ====================

async function _getActiveSession() {
    // My valid session — the default number from the profile if it is one,
    // else the most recently added. Never a colleague's: `mine=1` narrows the
    // list to what this person may use, whatever their role, which is the
    // rule the run itself enforces. root used to be shown the whole pool here
    // and read another user's number as «شمارهٔ فعال».
    try {
        const data = await apiCall('/auth/cookies?mine=1');
        // Switched off, or waiting on Divar's identity check, is not «active».
        const valid = (data.cookies || []).filter(_divarUsable);
        if (!valid.length) return null;
        const primary = _digits(_currentUser?.divar_phone);
        const mine = primary && valid.find(c => _digits(c.phone_number) === primary);
        if (mine) return mine;
        // sort by id descending (most recently added) as a proxy for recency
        valid.sort((a, b) => b.id - a.id);
        return valid[0];
    } catch (e) {
        return null;
    }
}

async function checkCookieStatus() {
    try {
        const session = await _getActiveSession();
        const textEl = document.getElementById('cookie-status');
        const dotEl  = document.getElementById('cookie-dot');

        if (session) {
            cookieStatus = { is_valid: true, has_cookies: true, phone_number: session.phone_number };
            // Say how old the answer is. «فعال» on its own was the whole
            // problem: it read as "checked just now" for a belief that had
            // been wrong since the previous morning.
            const checked = session.last_checked_at ? Date.parse(session.last_checked_at) : null;
            const ageMin = checked ? (Date.now() - checked) / 60000 : null;
            const fresh = ageMin !== null && ageMin <= 25;
            if (textEl) {
                textEl.textContent = fresh
                    ? `کوکی فعال (${session.phone_number})`
                    : `کوکی فعال؟ (${session.phone_number}) — بررسی نشده`;
                textEl.title = checked
                    ? `آخرین بررسی واقعی: ${new Date(checked).toLocaleString('fa-IR')}`
                    : 'هنوز از دیوار پرسیده نشده است';
            }
            if (dotEl)  dotEl.className = 'dot ' + (fresh ? 'dot-success' : 'dot-warning');
        } else {
            // check if there are any (expired) cookies
            let hasCookies = false;
            try {
                const data = await apiCall('/auth/cookies?mine=1');
                hasCookies = (data.cookies || []).length > 0;
            } catch (e) {}
            cookieStatus = { is_valid: false, has_cookies: hasCookies };
            if (hasCookies) {
                if (textEl) textEl.textContent = 'کوکی منقضی';
                if (dotEl)  dotEl.className = 'dot dot-warning';
            } else {
                if (textEl) textEl.textContent = 'نیاز به ورود';
                if (dotEl)  dotEl.className = 'dot dot-danger';
            }
        }
    } catch (error) {
        console.error('Failed to check cookie status:', error);
    }
}

async function checkAuthStatus() {
    const statusDiv = document.getElementById('auth-status');
    if (!statusDiv) return;
    try {
        const session = await _getActiveSession();

        if (session) {
            statusDiv.className = 'alert alert-success';
            statusDiv.innerHTML = `
                <i class="bi bi-check-circle"></i>
                <strong>وضعیت: متصل</strong><br>
                شماره فعال: <strong>${esc(session.phone_number)}</strong>
            `;
        } else {
            // check if any (expired) cookies exist
            let hasCookies = false;
            try {
                const data = await apiCall('/auth/cookies?mine=1');
                hasCookies = (data.cookies || []).length > 0;
            } catch (e) {}

            if (hasCookies) {
                statusDiv.className = 'alert alert-warning';
                statusDiv.innerHTML = `<i class="bi bi-exclamation-triangle"></i>
                    <strong>وضعیت: منقضی شده</strong><br>
                    لطفاً دوباره وارد شوید.`;
            } else {
                statusDiv.className = 'alert alert-info';
                statusDiv.innerHTML = `<i class="bi bi-info-circle"></i>
                    هیچ نشست فعالی یافت نشد. شماره موبایل خود را وارد کنید.`;
            }
        }
    } catch (error) {
        console.error('Failed to check auth status:', error);
    }
}

async function checkDivarSessionBanner() {
    const badge = document.getElementById('divar-session-badge');
    if (!badge) return;
    try {
        const session = await _getActiveSession();
        if (session) {
            badge.className = 'badge bg-success ms-2';
            badge.textContent = '● فعال';
            badge.title = `نشست دیوار فعال — ${session.phone_number}`;
        } else {
            badge.className = 'badge bg-warning text-dark ms-2';
            badge.textContent = '● غیرفعال';
            badge.title = 'نشست دیوار غیرفعال — شماره تماس اسکرپ نمی‌شود';
        }
    } catch(e) {
        badge.className = 'badge bg-secondary ms-2';
        badge.textContent = '●';
        badge.title = 'وضعیت نامشخص';
    }
}

// ─── Divar OTP polling ────────────────────────────────────────────────────────
let _otpPollTimer = null;

function startOtpPolling() {
    if (_otpPollTimer) return;
    _otpPollTimer = setInterval(pollDivarOtp, 4000);
}

function stopOtpPolling() {
    if (_otpPollTimer) { clearInterval(_otpPollTimer); _otpPollTimer = null; }
}

// ─── Job auto-refresh polling ─────────────────────────────────────────────────
let _jobPollTimer = null;
let _jobPollSnapshot = {}; // { job_id: { new_items, status } }

function startJobPolling() {
    if (_jobPollTimer) return;
    _jobPollTimer = setInterval(_pollJobs, 5000);
}

function stopJobPolling() {
    if (_jobPollTimer) { clearInterval(_jobPollTimer); _jobPollTimer = null; }
    _jobPollSnapshot = {};
}

async function _pollJobs() {
    try {
        const data = await apiCall(_jobsUrl());
        let shouldRefreshProps = false;

        for (const job of data.items) {
            const prev = _jobPollSnapshot[job.job_id];
            if (prev) {
                // New items added since last poll → refresh list
                if (job.new_items > prev.new_items) shouldRefreshProps = true;
                // Job just finished → final refresh (pausing for OTP isn't "finished")
                if (prev.status === 'running' && !['running', 'paused'].includes(job.status)) shouldRefreshProps = true;
            }
            _jobPollSnapshot[job.job_id] = { new_items: job.new_items, status: job.status };
        }

        // Re-render the jobs table
        _renderJobsTable(data.items);

        if (shouldRefreshProps) loadProperties();
    } catch (_) {}
}

function _renderJobsTable(items) {
    const tbody = document.getElementById('jobs-table');
    if (!tbody) return;
    tbody.innerHTML = '';
    if (items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="10" class="text-center text-muted py-4">هیچ تسکی وجود ندارد</td></tr>`;
        return;
    }
    const JOB_STATUS_FA = {
        pending: 'در صف', running: 'در حال اجرا', paused: '⏸ متوقف — منتظر کد',
        completed: 'تکمیل شده', failed: 'ناموفق', cancelled: 'لغو شده',
    };
    items.forEach(job => {
        const row = document.createElement('tr');
        const statusClass = `status-${esc(job.status)}`;
        const statusLabel = JOB_STATUS_FA[job.status] || esc(job.status);
        row.innerHTML = `
            <td><code class="job-id" title="${esc(job.job_id)}">${job.job_id.substring(0, 6)}</code></td>
            <td>${job.category_name ? `<span class="badge bg-primary">${esc(job.category_name)}</span>` : '—'}</td>
            <td>${esc(job.city_name) || '—'}</td>
            <!-- who started it, and the Divar account the run is on; a rotated
                 run names every account it went through in the tooltip -->
            <td class="job-who">
                <div>${esc(job.owner_name || '—')}</div>
                ${job.divar_phone ? `<div class="job-acct" dir="ltr" title="${esc((job.accounts_used || []).length > 1 ? 'حساب‌ها به ترتیب: ' + job.accounts_used.join('، ') : 'حساب دیوار این اجرا')}">${esc(job.divar_phone)}${(job.accounts_used || []).length > 1 ? ` <span class="job-acct-more">+${formatNumber(job.accounts_used.length - 1)}</span>` : ''}</div>`
                                 : `<div class="job-acct text-muted">${job.status === 'pending' ? 'خودکار' : '—'}</div>`}
            </td>
            <td>
                <span class="badge ${statusClass}">${statusLabel}</span>
                ${job.resumed_from ? `<div class="text-muted" style="font-size:.66rem" title="این اجرا ادامهٔ اجرای قبلی است">
                    <i class="bi bi-arrow-return-left"></i> ادامهٔ ${esc(String(job.resumed_from).slice(0, 8))}</div>` : ''}
                ${job.finish_reason ? `
                    <div class="job-reason" title="${esc(job.finish_reason)}">
                        ${esc(job.finish_reason)}
                    </div>` : ''}
            </td>
            <td>
                <div class="job-progress">
                    <div class="progress" style="height:5px;background:var(--border,#333);border-radius:3px;">
                        <div class="progress-bar" role="progressbar"
                             style="width:${job.progress}%;border-radius:3px;"></div>
                    </div>
                    <div style="font-size:.72rem;color:var(--text-muted,#aaa);text-align:center;margin-top:2px;">${Math.round(job.progress)}%</div>
                </div>
            </td>
            <!-- The two counts under the percent were unreadable crammed into
                 the bar's cell; they get a column. Same <bdi> reasoning as
                 «جدید / بروز»: isolated so the pair keeps the header's order. -->
            <td style="text-align:center;white-space:nowrap"
                title="${job.divar_count ? 'کل = تعدادی که دیوار برای این فیلترها اعلام کرد' : 'کل = نامزدهای جمع‌شده'}">
                ${job.total_items
                    ? `<bdi title="بررسی‌شده">${job.scraped_items}</bdi> <span class="text-muted">/</span> <bdi title="کل">${job.total_items}</bdi>`
                    : '<span class="text-muted">---</span>'}
            </td>
            <!-- «جدید / بروز» reads right-to-left, so «جدید» is the RIGHT
                 column. dir="ltr" here put the new count on the LEFT, under
                 «بروز» — the two numbers were swapped against their own
                 header. <bdi> isolates each one so the digits stay readable
                 while the pair follows the header's direction. -->
            <td style="text-align:center">
                <bdi class="text-success" title="ردیف تازه — قبلاً در پایگاه داده نبود">${job.new_items}</bdi>
                <span class="text-muted">/</span>
                <bdi class="text-muted" title="از قبل موجود بود — یا همان بود و رد شد، یا با اطلاعات تازه به‌روز شد">${job.updated_items}</bdi>
            </td>
            <td class="job-when">${job.started_at ? new Date(job.started_at).toLocaleString('fa-IR') : '---'}</td>
            <td class="job-actions">
                <button class="btn btn-sm btn-outline-secondary" onclick="showJobLog('${job.job_id}')"
                        title="گزارش این اسکرپ">
                    <i class="bi bi-list-ul"></i>
                </button>
                <button class="btn btn-sm btn-outline-secondary" onclick="showSkipped('${job.job_id}')"
                        title="آگهی‌هایی که این اسکرپ ذخیره نکرد">
                    <i class="bi bi-slash-circle"></i>
                </button>
                ${['running', 'paused', 'pending'].includes(job.status)
                  && job.owner_user_id != null && job.owner_user_id === _currentUser?.id ? `
                    <button class="btn btn-sm btn-outline-warning" onclick="switchJobAccount('${job.job_id}', '${_digits(job.divar_phone)}')"
                            title="تعویض شمارهٔ دیوار بدون توقف اسکرپ">
                        <i class="bi bi-arrow-left-right"></i>
                    </button>
                ` : ''}
                ${['running', 'paused', 'pending'].includes(job.status) ? `
                    <button class="btn btn-sm btn-outline-danger" onclick="cancelJob('${job.job_id}')"
                            title="لغو تسک">
                        <i class="bi bi-stop-fill"></i>
                    </button>
                ` : ''}
                ${job.can_resume ? `
                    <button class="btn btn-sm btn-outline-primary" onclick="resumeJob('${job.job_id}')"
                            title="ادامه از همان‌جا — آگهی‌های ذخیره‌شده رد می‌شوند">
                        <i class="bi bi-play-fill"></i>
                    </button>
                ` : ''}
                ${['completed', 'failed', 'cancelled'].includes(job.status) ? `
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteJob('${job.job_id}')"
                            title="حذف این تسک از فهرست — آگهی‌ها دست‌نخورده می‌مانند">
                        <i class="bi bi-trash"></i>
                    </button>
                ` : ''}
            </td>
        `;
        tbody.appendChild(row);
    });
}

// keys the user explicitly dismissed this session — don't re-pop them
const _dismissedOtpKeys = new Set();

// ═══ OTP v2 — segmented entry, auto-submit, live countdown ═══════
let _otp2Timer = null;
let _otp2Deadline = 0;
// The window is the server's to state — it is the one that gives up on the
// request. A constant here drifted from settings.otp_wait_timeout and the
// countdown promised minutes that the scraper had already stopped waiting.
let _otp2Window = 300;     // replaced by /scraper/otp-pending's «timeout»

function _otp2Els() { return [...document.querySelectorAll('#otp2-boxes .otp2-box')]; }
function _otp2Code() { return _otp2Els().map(b => b.value).join(''); }

function _otp2SetStatus(text, kind = '') {
    const wrap = document.getElementById('otp2-status');
    const label = document.getElementById('otp2-status-text');
    if (label) label.textContent = text;
    if (wrap) wrap.className = 'otp2-status' + (kind ? ' ' + kind : '');
}

function _otp2Reset() {
    _otp2Els().forEach(b => { b.value = ''; b.classList.remove('filled'); });
    document.getElementById('otp2-boxes')?.classList.remove('error', 'done');
    _otp2SetStatus('اسکرپر متوقف است و منتظر کد می‌ماند');
    const btn = document.getElementById('otp2-submit');
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-check-circle"></i> تأیید و ادامه اسکرپ'; }
}

function _otp2StartTimer(remaining = _otp2Window) {
    _otp2StopTimer();
    _otp2Deadline = Date.now() + Math.max(remaining, 0) * 1000;
    const tick = () => {
        const left = Math.max(Math.round((_otp2Deadline - Date.now()) / 1000), 0);
        const el = document.getElementById('otp2-timer');
        if (el) {
            const h = Math.floor(left / 3600);
            const m = String(Math.floor((left % 3600) / 60)).padStart(2, '0');
            const s = String(left % 60).padStart(2, '0');
            // The window is six hours when wait-for-human is on, and "360:12"
            // is not a duration anybody reads.
            const clock = h > 0
                ? `${formatNumber(h)}:${formatNumber(m)}:${formatNumber(s)}`
                : `${formatNumber(m)}:${formatNumber(s)}`;
            el.textContent = left > 0
                ? `⏳ مهلت ورود کد: ${clock}`
                : 'مهلت تمام شد — اسکرپر بدون این شماره ادامه می‌دهد';
            el.classList.toggle('text-danger', left <= 0);
        }
        if (left <= 0) _otp2StopTimer();
    };
    tick();
    _otp2Timer = setInterval(tick, 1000);
}

function _otp2StopTimer() {
    if (_otp2Timer) { clearInterval(_otp2Timer); _otp2Timer = null; }
}

function initOtp2Boxes() {
    const boxes = _otp2Els();
    if (!boxes.length || boxes[0].dataset.bound) return;
    boxes.forEach((box, i) => {
        box.dataset.bound = '1';
        box.addEventListener('input', () => {
            box.value = (box.value.replace(/\D/g, '')[0] || '');
            box.classList.toggle('filled', !!box.value);
            document.getElementById('otp2-boxes')?.classList.remove('error');
            if (box.value && i < boxes.length - 1) boxes[i + 1].focus();
            if (_otp2Code().length === boxes.length) submitDivarOtp();
        });
        box.addEventListener('keydown', e => {
            if (e.key === 'Backspace' && !box.value && i > 0) {
                boxes[i - 1].focus(); boxes[i - 1].value = ''; boxes[i - 1].classList.remove('filled');
                e.preventDefault();
            } else if (e.key === 'ArrowLeft' && i < boxes.length - 1) { boxes[i + 1].focus(); e.preventDefault(); }
            else if (e.key === 'ArrowRight' && i > 0) { boxes[i - 1].focus(); e.preventDefault(); }
            else if (e.key === 'Enter') submitDivarOtp();
        });
        box.addEventListener('paste', e => {
            e.preventDefault();
            const digits = (e.clipboardData || window.clipboardData).getData('text').replace(/\D/g, '').slice(0, boxes.length);
            digits.split('').forEach((d, k) => { boxes[k].value = d; boxes[k].classList.add('filled'); });
            boxes[Math.min(digits.length, boxes.length - 1)].focus();
            if (digits.length === boxes.length) submitDivarOtp();
        });
    });
}

/* ── Divar's identity wall ────────────────────────────────────────────────
 *
 * «دیوار ازش احراز هویت با کد ملی خواست.» Divar sometimes asks an account
 * to prove who it is — national ID, birth date — and nothing automated can
 * answer that. The scraper recognises the page, sets the account aside, and
 * says so here: the only useful response is a person logging in on Divar
 * with that number and doing it, then telling the panel it is done.
 *
 * Once per account per session unless the person says «بعداً», and never
 * stacked on top of itself.                                                 */
const _identityShown = new Set();
let _identityOpen = false;

async function _showIdentityWall(items) {
    if (_identityOpen) return;
    const item = items.find(i => !_identityShown.has(i.phone));
    if (!item) return;
    _identityOpen = true;
    _identityShown.add(item.phone);
    try {
        const ok = await _askOpen({
            icon: 'bi-person-badge',
            tone: 'danger',
            title: 'دیوار احراز هویت می‌خواهد',
            body: `دیوار برای شمارهٔ <b dir="ltr">${esc(item.phone)}</b> احراز هویت با <b>کد ملی</b> خواسته است.
                   اسکرپر نمی‌تواند این را انجام دهد و این شماره را کنار گذاشته؛ اجرا با شماره‌های دیگر ادامه می‌یابد.`,
            note: `با همین شماره در <a href="https://divar.ir/my-divar" target="_blank" rel="noopener">divar.ir</a> وارد شوید و احراز هویت را انجام دهید.
                   بعد اینجا «انجام شد» را بزنید تا شماره دوباره به چرخش برگردد.
                   ${item.text ? `<div class="mt-2 opacity-75" style="font-size:.7rem">دیوار: «${esc(item.text.slice(0, 160))}»</div>` : ''}`,
            okLabel: 'انجام شد — احراز هویت کردم',
            cancelLabel: 'بعداً',
        });
        if (ok) await _identityCleared(item.phone);
    } finally {
        _identityOpen = false;
    }
}

async function _identityCleared(phone) {
    try {
        const d = await apiCall('/auth/cookies?mine=1');
        const c = (d.cookies || []).find(x => (x.phone_number || '').replace(/\D/g, '') === String(phone).replace(/\D/g, ''));
        if (!c) { showToast('خطا', 'نشست این شماره پیدا نشد', 'warning'); return; }
        await apiCall(`/auth/cookies/${c.id}/identity-cleared`, { method: 'POST' });
        showToast('انجام شد', `${phone} دوباره در چرخش است`, 'success');
        _identityShown.delete(phone);
        loadScraperAccounts();
    } catch (e) {
        showToast('خطا', e.message, 'danger');
    }
}

// keys the user explicitly dismissed this session — don't re-pop them
async function pollDivarOtp() {
    try {
        const data = await apiCall('/scraper/otp-pending');
        if (data.timeout) _otp2Window = data.timeout;
        // An account Divar wants identified takes precedence over a code
        // prompt: no code will get past it, and the person has to know now.
        await _showIdentityWall(data.identity_required || []);
        const pending = data.pending || [];
        const modal = document.getElementById('divarOtpModal');
        if (!modal) return;

        const openKey = modal.classList.contains('show')
            ? document.getElementById('divar-otp-key').value : '';
        if (openKey) {
            // The scraper drops a request it has waited out. Saying so beats
            // leaving a live-looking box that answers "no pending OTP" —
            // which is what a code typed one second too late used to hit.
            if (!pending.some(p => p.key === openKey)) _otp2MarkExpired();
            return;                       // never reopen over an open prompt
        }

        const item = pending.find(p => !_dismissedOtpKeys.has(p.key));
        if (!item) return;
        document.getElementById('divar-otp-key').value = item.key;
        const phoneEl = document.getElementById('otp2-phone');
        if (phoneEl) phoneEl.textContent = item.phone_hint || 'دیوار';
        // Is a phone forwarding this account's SMS? Then the code will most
        // likely type itself and this modal is a fallback; say so, because a
        // person who reaches for their phone is racing a machine that will
        // win by twenty seconds.
        _otp2SetMode(item.phone_hint, data.forwarders || {});
        initOtp2Boxes();
        _otp2Reset();
        // the request started before the poll saw it — count what is left
        _otp2StartTimer(item.remaining != null ? item.remaining : _otp2Window);
        // focus the first box once Bootstrap finished its own focus handling
        modal.addEventListener('shown.bs.modal', () => _otp2Els()[0]?.focus(), { once: true });
        new bootstrap.Modal(modal).show();
    } catch(e) { /* silent */ }
}

function _otp2SetMode(phone, forwarders) {
    const el = document.getElementById('otp2-mode');
    if (!el) return;
    const digits = String(phone || '').replace(/[۰-۹]/g, d => '۰۱۲۳۴۵۶۷۸۹'.indexOf(d)).replace(/\D/g, '').slice(-10);
    const fw = forwarders[digits];
    if (fw && fw.online) {
        el.className = 'badge bg-success';
        el.textContent = 'خودکار — گوشی متصل است' + (fw.battery != null ? ` · ${formatNumber(fw.battery)}٪` : '');
        el.title = 'کد از گوشی به‌طور خودکار می‌رسد؛ اگر نیامد، دستی وارد کنید';
    } else if (fw) {
        el.className = 'badge bg-warning text-dark';
        el.textContent = 'گوشی آفلاین — دستی وارد کنید';
        el.title = 'آخرین تماس گوشی بیش از ۱۰ دقیقه پیش بود';
    } else {
        el.className = 'badge bg-secondary';
        el.textContent = 'دستی';
        el.title = 'برای این شماره گوشی‌ای متصل نیست';
    }
}

/** The request is gone: stop the clock and stop accepting digits for it. */
function _otp2MarkExpired() {
    _otp2StopTimer();
    document.getElementById('otp2-boxes')?.classList.add('error');
    _otp2SetStatus('مهلت این کد تمام شد — اسکرپر بدون این شماره ادامه داد', 'err');
    const btn = document.getElementById('otp2-submit');
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="bi bi-x-circle"></i> منقضی شد'; }
    const t = document.getElementById('otp2-timer');
    if (t) t.textContent = 'برای شمارهٔ بعدی دوباره پرسیده می‌شود';
}

async function resendDivarOtp() {
    const key = document.getElementById('divar-otp-key')?.value;
    if (!key) return;
    const btn = document.getElementById('otp2-resend');
    const label = document.getElementById('otp2-resend-label');
    if (btn) btn.disabled = true;
    if (label) label.textContent = 'در حال درخواست…';
    try {
        const r = await apiCall(`/scraper/otp/${encodeURIComponent(key)}/resend`, { method: 'POST' });
        showToast('ارسال دوباره', r.message || 'درخواست ثبت شد', 'success');
        // The browser presses Divar's button within a couple of seconds and
        // restarts its own clock; restart ours to match rather than leaving a
        // countdown that belongs to the code which never arrived.
        _otp2StartTimer(_otp2Window);
        const st = document.getElementById('otp2-status-text');
        if (st) st.textContent = 'کد دوباره خواسته شد — چند لحظه صبر کنید';
    } catch (e) {
        showToast('ارسال دوباره نشد', e.message || 'خطا', 'warning');
    } finally {
        // A short lock-out, because each press is a real SMS Divar sends.
        setTimeout(() => {
            if (btn) btn.disabled = false;
            if (label) label.textContent = 'ارسال دوباره کد';
        }, 15000);
    }
}

async function dismissDivarOtp() {
    const key = document.getElementById('divar-otp-key').value;
    if (key) _dismissedOtpKeys.add(key);           // stop the poll from re-opening it
    _otp2StopTimer();
    bootstrap.Modal.getInstance(document.getElementById('divarOtpModal'))?.hide();
    // The key is «{job_id}:{divar_id}», so dismissing this prompt suppresses OTP
    // for this job only. Up to three scrapes run at once, and closing one modal
    // used to stop all of them collecting phone numbers for fifteen minutes.
    const jobId = key ? key.split(':')[0] : '';
    const qs = jobId ? `?job_id=${encodeURIComponent(jobId)}` : '';
    try { await apiCall(`/scraper/otp-cancel${qs}`, { method: 'POST' }); } catch (_) {}
}

async function submitDivarOtp() {
    const key  = document.getElementById('divar-otp-key').value;
    const code = _otp2Code().trim();
    const boxesWrap = document.getElementById('otp2-boxes');
    if (code.length < 6) {
        boxesWrap?.classList.add('error');
        _otp2SetStatus('کد ۶ رقمی کامل نیست', 'err');
        return;
    }
    const btn = document.getElementById('otp2-submit');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> در حال ارسال...'; }
    _otp2SetStatus('در حال ارسال کد به دیوار...');
    try {
        await apiCall(`/scraper/otp/${encodeURIComponent(key)}`, { method: 'POST', body: JSON.stringify({ code }) });
        boxesWrap?.classList.add('done');
        _otp2SetStatus('کد تأیید شد — اسکرپر ادامه می‌دهد', 'ok');
        _otp2StopTimer();
        setTimeout(() => {
            bootstrap.Modal.getInstance(document.getElementById('divarOtpModal'))?.hide();
            showToast('تأیید', 'کد ارسال شد و اسکرپ ادامه یافت', 'success');
        }, 700);
    } catch(e) {
        boxesWrap?.classList.add('error');
        const expired = (e?.message || '').includes('No pending OTP');
        _otp2SetStatus(expired ? 'درخواست منقضی شده — منتظر درخواست بعدی بمانید' : 'ارسال کد ناموفق بود', 'err');
        if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-check-circle"></i> تأیید و ادامه اسکرپ'; }
    }
}

async function initiateLogin() {
    const phone = document.getElementById('auth-phone').value;

    if (!phone || !/^09\d{9}$/.test(phone)) {
        showToast('خطا', 'لطفاً شماره موبایل معتبر وارد کنید', 'warning');
        return;
    }

    const btn = document.querySelector('#auth-login-form button');
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> در حال ارسال کد...';

    try {
        const result = await apiCall('/auth/login', {
            method: 'POST',
            body: JSON.stringify({ phone_number: phone })
        });

        if (result.requires_code) {
            loginPhoneNumber = phone;
            document.getElementById('auth-login-form').style.display = 'none';
            const verifyForm = document.getElementById('auth-verify-form');
            verifyForm.style.display = 'block';

            // Show waiting message above the code input
            const waitMsg = verifyForm.querySelector('.otp-wait-msg') || (() => {
                const el = document.createElement('div');
                el.className = 'alert alert-warning otp-wait-msg mb-3';
                verifyForm.insertBefore(el, verifyForm.firstChild);
                return el;
            })();
            waitMsg.innerHTML = `<i class="bi bi-phone"></i> کد تأیید به <strong>${esc(phone)}</strong> ارسال شد.<br>
                <small class="text-muted">ممکن است تا ۳۰ ثانیه طول بکشد. منتظر SMS باشید.</small>`;

            _clearOtpBoxes();
            document.querySelector('.otp-box')?.focus();
        } else {
            showToast('خطا', result.message || 'خطا در ارسال کد', 'danger');
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    } catch (error) {
        showToast('خطا', error.message, 'danger');
        btn.disabled = false;
        btn.innerHTML = originalHtml;
    }
}

function _getOtpCode() {
    return [...document.querySelectorAll('.otp-box')].map(b => b.value).join('');
}

function _clearOtpBoxes() {
    document.querySelectorAll('.otp-box').forEach(b => {
        b.value = '';
        b.classList.remove('filled');
    });
}

function initOtpBoxes() {
    const boxes = [...document.querySelectorAll('.otp-box')];
    let _verifying = false;

    boxes.forEach((box, idx) => {
        box.addEventListener('keydown', e => {
            if (e.key === 'Backspace') {
                if (box.value) {
                    box.value = '';
                    box.classList.remove('filled');
                } else if (idx > 0) {
                    boxes[idx - 1].focus();
                    boxes[idx - 1].value = '';
                    boxes[idx - 1].classList.remove('filled');
                }
                e.preventDefault();
            } else if (e.key === 'ArrowLeft' && idx < boxes.length - 1) {
                boxes[idx + 1].focus(); e.preventDefault();
            } else if (e.key === 'ArrowRight' && idx > 0) {
                boxes[idx - 1].focus(); e.preventDefault();
            }
        });

        box.addEventListener('input', () => {
            const val = box.value.replace(/\D/g, '');
            box.value = val ? val[0] : '';
            box.classList.toggle('filled', !!box.value);
            if (box.value && idx < boxes.length - 1) boxes[idx + 1].focus();
            // auto-submit when all filled
            if (boxes.every(b => b.value) && !_verifying) {
                _verifying = true;
                verifyCode().finally(() => { _verifying = false; });
            }
        });

        box.addEventListener('paste', e => {
            e.preventDefault();
            const text = (e.clipboardData || window.clipboardData).getData('text').replace(/\D/g, '');
            boxes.forEach((b, i) => {
                b.value = text[i] || '';
                b.classList.toggle('filled', !!b.value);
            });
            const nextEmpty = boxes.findIndex(b => !b.value);
            (nextEmpty === -1 ? boxes[5] : boxes[nextEmpty]).focus();
            if (text.length >= 6 && !_verifying) {
                _verifying = true;
                verifyCode().finally(() => { _verifying = false; });
            }
        });

        box.addEventListener('click', () => box.select());
    });
}

function cancelDivarOtp() {
    _clearOtpBoxes();
    loginPhoneNumber = '';
    // Remove the wait message if it was injected
    const waitMsg = document.querySelector('#auth-verify-form .otp-wait-msg');
    if (waitMsg) waitMsg.remove();
    document.getElementById('auth-verify-form').style.display = 'none';
    const loginForm = document.getElementById('auth-login-form');
    loginForm.style.display = 'block';
    const btn = loginForm.querySelector('button');
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-send"></i> ارسال کد تأیید'; }
}

async function verifyCode() {
    const code = _getOtpCode();

    if (code.length !== 6) {
        showToast('خطا', 'لطفاً کد ۶ رقمی را وارد کنید', 'warning');
        return;
    }

    const btn = document.getElementById('otp-verify-btn');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> در حال تأیید...'; }
    
    try {
        const result = await apiCall(`/auth/verify?phone_number=${loginPhoneNumber}`, {
            method: 'POST',
            body: JSON.stringify({ code })
        });
        
        if (result.success) {
            showToast('موفق', `ورود موفقیت‌آمیز بود (${loginPhoneNumber})`, 'success');
            // Reset login form for next use
            document.getElementById('auth-phone').value = '';
            _clearOtpBoxes();
            const btn = document.querySelector('#auth-login-form button');
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-send"></i> ارسال کد تأیید'; }
            document.getElementById('auth-login-form').style.display = 'block';
            document.getElementById('auth-verify-form').style.display = 'none';
            loadCookies();
            loadScraperAccounts();
            checkAuthStatus();
            checkCookieStatus();
        } else {
            showToast('خطا', result.message, 'danger');
            _clearOtpBoxes();
            document.querySelector('.otp-box')?.focus();
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-check-circle"></i> تأیید و ورود'; }
        }
    } catch (error) {
        showToast('خطا', error.message, 'danger');
        _clearOtpBoxes();
        document.querySelector('.otp-box')?.focus();
        if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-check-circle"></i> تأیید و ورود'; }
    }
}

async function refreshSession() {
    try {
        const result = await apiCall('/auth/refresh', { method: 'POST' });
        
        if (result.success) {
            showToast('موفق', result.message, 'success');
        } else {
            showToast('هشدار', result.message, 'warning');
        }
        
        checkAuthStatus();
        checkCookieStatus();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function logout() {
    if (!await askConfirm({ icon: 'bi-box-arrow-right', title: 'خروج از حساب', tone: 'warning', okLabel: 'خروج', body: 'آیا از خروج اطمینان دارید؟' })) return;
    
    try {
        await apiCall('/auth/logout', { method: 'POST' });
        showToast('موفق', 'خروج موفقیت‌آمیز بود', 'success');
        checkAuthStatus();
        checkCookieStatus();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function importCookies() {
    const phone = document.getElementById('import-phone').value.trim();
    const raw   = document.getElementById('import-cookies-json').value.trim();

    if (!phone || !/^09\d{9}$/.test(phone)) {
        showToast('خطا', 'شماره موبایل معتبر وارد کنید', 'warning');
        return;
    }
    if (!raw) {
        showToast('خطا', 'JSON کوکی‌ها را وارد کنید', 'warning');
        return;
    }

    let cookies;
    try {
        cookies = JSON.parse(raw);
        if (!Array.isArray(cookies)) throw new Error('باید آرایه باشد');
    } catch (e) {
        showToast('خطا', 'فرمت JSON نادرست است: ' + e.message, 'danger');
        return;
    }

    try {
        // The server verifies against Divar before answering, so this reports
        // whether the session actually works rather than merely that the text
        // was saved. Saying «موفق» for a jar Divar rejects is what sent people
        // back to the login form with no idea why.
        const r = await apiCall('/auth/cookies/import', {
            method: 'POST',
            body: JSON.stringify({ phone_number: phone, cookies })
        });
        showToast(
            r.alive === true ? 'تأیید شد' : r.alive === false ? 'رد شد' : 'نامشخص',
            r.message || 'کوکی‌ها وارد شدند',
            r.alive === true ? 'success' : r.alive === false ? 'danger' : 'warning');
        if (r.alive === true) document.getElementById('import-cookies-json').value = '';
        checkCookieStatus();
        loadCookies();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function loadCookies() {
    try {
        const data = await apiCall('/auth/cookies?mine=1');
        
        const container = document.getElementById('cookies-list');
        
        if (data.cookies.length === 0) {
            container.innerHTML = '<p class="text-muted text-center">هیچ نشستی ذخیره نشده</p>';
            return;
        }
        
        container.innerHTML = data.cookies.map(cookie => `
            <div class="d-flex justify-content-between align-items-center p-2 border-bottom${cookie.is_enabled === false ? ' opacity-50' : ''}">
                <div>
                    <strong dir="ltr">${esc(cookie.phone_number)}</strong>
                    <br>
                    <small class="text-muted">${cookie.is_valid ? 'معتبر' : 'منقضی'}${cookie.is_enabled === false ? ' · خاموش' : ''}</small>
                    ${cookie.identity_required_at ? `
                        <div class="mt-1">
                            <span class="badge bg-danger" title="دیوار برای این شماره احراز هویت با کد ملی می‌خواهد؛ تا انجام نشود در چرخش نیست">
                                <i class="bi bi-person-badge"></i> احراز هویت لازم
                            </span>
                            <button class="btn btn-sm btn-link p-0 ms-1 small" onclick="_identityCleared(${jsArg(cookie.phone_number)})">انجام شد</button>
                        </div>` : ''}
                </div>
                <div class="d-flex align-items-center gap-2">
                    <span class="form-check form-switch m-0" title="روشن/خاموش برای اسکرپ — خاموش یعنی گوشی این شماره در دسترس نیست">
                        <input class="form-check-input" type="checkbox" role="switch" ${cookie.is_enabled !== false ? 'checked' : ''}
                               onchange="toggleDivarNumber(${Number(cookie.id)}, this.checked, this).then(loadCookies)"
                               aria-label="روشن/خاموش ${esc(cookie.phone_number)}">
                    </span>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteCookie(${Number(cookie.id)})">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </div>
        `).join('');
        
    } catch (error) {
        console.error('Failed to load cookies:', error);
    }
}

/* ── root: every Divar number and its owner ────────────────────────────────
 *
 * «یک بخش فقط برای root که همهٔ شماره‌ها را با صاحبشان نشان دهد و بتوان
 * مالکیت را اصلاح کرد.» The only place ownership changes by hand; the server
 * refuses everyone but root and writes each change to the audit log.     */
let _registryUsers = [];

async function loadNumbersRegistry() {
    const card = document.getElementById('numbers-registry-card');
    const tb = document.getElementById('numbers-registry');
    if (!card || !tb) return;
    if (_currentUser?.role !== 'root') { card.classList.add('d-none'); return; }
    card.classList.remove('d-none');
    tb.innerHTML = '<tr><td colspan="6" class="text-muted small p-3">در حال بارگذاری…</td></tr>';
    try {
        const d = await apiCall('/auth/registry');
        _registryUsers = d.users || [];
        const rows = d.numbers || [];
        const off = rows.filter(r => r.suggested_owner).length;
        document.getElementById('numbers-registry-summary').textContent =
            `${formatNumber(rows.length)} شماره` + (off ? ` · ${formatNumber(off)} مورد برای بررسی` : '');
        if (!rows.length) {
            tb.innerHTML = '<tr><td colspan="6" class="text-muted small p-3">هیچ شمارهٔ دیواری ذخیره نشده است</td></tr>';
            return;
        }
        const opts = sel => _registryUsers.map(u =>
            `<option value="${Number(u.id)}"${u.id === sel ? ' selected' : ''}>${esc(u.name)}${u.is_active ? '' : ' (غیرفعال)'}</option>`).join('');
        tb.innerHTML = rows.map(r => {
            const state = [
                r.is_valid ? '<span class="badge bg-success">معتبر</span>' : '<span class="badge bg-secondary">نامعتبر</span>',
                r.is_enabled ? '' : '<span class="badge bg-dark">خاموش</span>',
                r.identity_required_at ? '<span class="badge bg-danger">احراز هویت</span>' : '',
                r.in_use ? '<span class="badge bg-info text-dark">در حال اسکرپ</span>' : '',
            ].join(' ');
            const hint = r.suggested_owner
                ? `<button class="btn btn-sm btn-link p-0" onclick="document.getElementById('reg-owner-${Number(r.id)}').value='${Number(r.suggested_owner.id)}'"
                           title="${r.suggested_owner.why === 'forwarder' ? 'سیم‌کارت این شماره در گوشی این کاربر است' : 'این کاربر این شماره را شمارهٔ دیوار خود اعلام کرده'}">
                       ${esc(r.suggested_owner.name)} <i class="bi bi-arrow-return-left"></i></button>`
                : '<span class="text-muted small">—</span>';
            return `<tr class="${r.suggested_owner ? 'registry-flag' : ''}">
                <td dir="ltr" class="fw-semibold">${esc(r.phone_number)}</td>
                <td><select class="form-select form-select-sm" id="reg-owner-${Number(r.id)}">
                    ${r.owner_user_id == null ? '<option value="" selected>— بدون صاحب —</option>' : ''}${opts(r.owner_user_id)}</select></td>
                <td class="small">${state}</td>
                <td class="small">${formatNumber(r.reveals || 0)}</td>
                <td class="small">${hint}</td>
                <td class="text-nowrap">
                    <button class="btn btn-sm btn-primary" onclick="saveNumberOwner(${Number(r.id)}, ${jsArg(r.phone_number)})">ذخیره</button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteCookie(${Number(r.id)}).then(loadNumbersRegistry)" title="حذف نشست">
                        <i class="bi bi-trash"></i></button>
                </td></tr>`;
        }).join('');
    } catch (e) {
        tb.innerHTML = `<tr><td colspan="6" class="text-danger small p-3">${esc(e.message || 'خطا')}</td></tr>`;
    }
}

async function saveNumberOwner(id, phone) {
    const sel = document.getElementById(`reg-owner-${Number(id)}`);
    const uid = parseInt(sel?.value || '', 10);
    if (!uid) { showToast('توجه', 'صاحب شماره را انتخاب کنید', 'warning'); return; }
    const who = (_registryUsers.find(u => u.id === uid) || {}).name || '';
    if (!await askConfirm({ icon: 'bi-person-check', title: 'تغییر صاحب شماره', okLabel: 'ثبت',
        body: `شمارهٔ <b dir="ltr">${esc(phone)}</b> از این پس فقط در اختیار <b>${esc(who)}</b> است.`,
        note: 'اگر اسکرپی از صاحب قبلی روی این شماره در حال اجراست، به شمارهٔ دیگری از خودش منتقل می‌شود.' })) {
        loadNumbersRegistry();
        return;
    }
    try {
        const r = await apiCall(`/auth/registry/${Number(id)}/owner`, {
            method: 'PATCH', body: JSON.stringify({ owner_user_id: uid }) });
        showToast('ثبت شد', r.changed ? `${phone} به ${r.owner_name} داده شد` : 'تغییری لازم نبود', 'success');
    } catch (e) { showToast('خطا', e.message, 'danger'); }
    loadNumbersRegistry();
    loadCookies();
}

async function deleteCookie(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'آیا از حذف این نشست اطمینان دارید؟' })) return;
    
    try {
        await apiCall(`/auth/cookies/${id}`, { method: 'DELETE' });
        showToast('موفق', 'نشست حذف شد', 'success');
        loadCookies();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

// ==================== Proxies ====================

let _proxyCount = 0;

/** Remove every proxy in one go.
 *
 *  Sends back the count the table was drawn with. If the list has changed since
 *  — someone else adding one, a stale tab left open — the server refuses with a
 *  409 rather than deleting rows this person never saw.
 *
 *  Scraping is unaffected: with an empty table the scraper falls back to a
 *  direct connection rather than failing. */
async function deleteAllProxies() {
    if (!_proxyCount) { showToast('توجه', 'پراکسی‌ای برای حذف وجود ندارد', 'warning'); return; }
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: `همهٔ ${_proxyCount} پراکسی حذف شود؟ این کار قابل بازگشت نیست.` })) return;

    const btn = document.getElementById('proxy-wipe');
    if (btn) btn.disabled = true;
    try {
        const r = await apiCall(`/proxies?confirm_count=${_proxyCount}`, { method: 'DELETE' });
        showToast('موفق', `${r.deleted} پراکسی حذف شد`, 'success');
        loadProxies();
    } catch (e) {
        showToast('خطا', e.message || 'حذف ناموفق بود', 'danger');
    } finally {
        if (btn) btn.disabled = false;
    }
}

// Where a proxy comes out, and whether that helps.
//
// «فعال» only means Divar answered. For an Iranian site the exit country is
// the number that decides whether the proxy makes us look more like a person
// or less: a foreign hosting IP passes the test and is the least convincing
// thing a visitor can be. Say it in the row, not in a tooltip.
function _proxyExitCell(p) {
    if (!p.exit_country) return '<span class="text-muted small">— تست نشده</span>';
    const ir = p.exit_country === 'IR';
    const kind = p.is_hosting ? 'دیتاسنتر' : 'خانگی/موبایل';
    const cls = ir && !p.is_hosting ? 'bg-success' : ir ? 'bg-warning text-dark' : 'bg-danger';
    const note = ir ? '' : ' — برای دیوار مناسب نیست';
    return `<span class="badge ${cls}" title="${esc(p.exit_ip || '')}">${esc(p.exit_country)} · ${kind}</span>` +
           `<span class="small text-muted">${note}</span>`;
}

// ═══ ask() — the panel's own dialog ════════════════════════════════════
//
// window.prompt/confirm/alert render as browser chrome: «sorinflow.com says»
// above a bare sentence, no icon, no explanation, and light-on-light against
// a black panel. A destructive action should look destructive and a question
// should have room to say why it is being asked.
//
// Promise-based so a call site reads the way prompt() did:
//     if (!await askConfirm({...})) return;
//     const v = await askText({...});

function _askClose(overlay, resolve, value) {
    if (!overlay || overlay.dataset.closing) return;
    overlay.dataset.closing = '1';
    document.removeEventListener('keydown', overlay._onKey);
    overlay.remove();
    resolve(value);
}

function _askOpen({ icon, title, body, note, tone, okLabel, cancelLabel, field }) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'ask-overlay';
        const toneCls = tone === 'danger' ? 'is-danger' : tone === 'warning' ? 'is-warning' : '';
        overlay.innerHTML = `
          <div class="ask-card ${toneCls}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
            <div class="ask-ring"><i class="bi ${esc(icon || 'bi-question-lg')}"></i></div>
            <h5>${esc(title)}</h5>
            ${body ? `<p class="ask-body">${body}</p>` : ''}
            ${note ? `<div class="ask-note">${note}</div>` : ''}
            ${field ? `<div class="ask-field">
                ${field.label ? `<label for="ask-input">${esc(field.label)}</label>` : ''}
                ${field.options ? `<select id="ask-input" dir="${esc(field.dir || 'auto')}">
                    ${field.options.map(([v, l]) => `<option value="${esc(v)}" ${v === field.value ? 'selected' : ''}>${esc(l)}</option>`).join('')}
                  </select>` : field.multiline ? `<textarea id="ask-input" rows="${esc(field.rows || 7)}"
                       dir="${esc(field.dir || 'auto')}" placeholder="${esc(field.placeholder || '')}">${esc(field.value || '')}</textarea>`
                  : `<input id="ask-input" type="${esc(field.type || 'text')}"
                       inputmode="${esc(field.inputmode || 'text')}"
                       dir="${esc(field.dir || 'auto')}"
                       placeholder="${esc(field.placeholder || '')}"
                       value="${esc(field.value || '')}">`}
                ${field.hint ? `<div class="ask-hint">${esc(field.hint)}</div>` : ''}
                <div class="ask-error" id="ask-error"></div>
              </div>` : ''}
            <div class="ask-actions">
              <button class="ask-ok" id="ask-ok">${esc(okLabel || 'تأیید')}</button>
              <button class="ask-cancel" id="ask-cancel">${esc(cancelLabel || 'انصراف')}</button>
            </div>
          </div>`;
        document.body.appendChild(overlay);

        const input = overlay.querySelector('#ask-input');
        const err = overlay.querySelector('#ask-error');
        const cancelValue = field ? null : false;

        const submit = () => {
            if (!field) return _askClose(overlay, resolve, true);
            const v = (input.value || '').trim();
            if (field.validate) {
                const msg = field.validate(v);
                if (msg) { err.textContent = msg; input.focus(); return; }
            }
            _askClose(overlay, resolve, v);
        };

        overlay.querySelector('#ask-ok').addEventListener('click', submit);
        overlay.querySelector('#ask-cancel')
            .addEventListener('click', () => _askClose(overlay, resolve, cancelValue));
        overlay.addEventListener('mousedown', e => {
            if (e.target === overlay) _askClose(overlay, resolve, cancelValue);
        });
        overlay._onKey = e => {
            if (e.key === 'Escape') _askClose(overlay, resolve, cancelValue);
            // a textarea keeps Enter for a new line; Ctrl/⌘+Enter sends
            if (e.key === 'Enter' && field && document.activeElement === input
                && (!field.multiline || e.ctrlKey || e.metaKey)) submit();
        };
        document.addEventListener('keydown', overlay._onKey);

        // Focus what the person will act on: the field if there is one, else
        // the safe button — so Enter on a delete dialog does not delete.
        setTimeout(() => {
            if (input) { input.focus(); if (input.select && input.tagName === 'INPUT') input.select(); }
            else overlay.querySelector(tone === 'danger' ? '#ask-cancel' : '#ask-ok').focus();
        }, 30);
    });
}

/** A yes/no. Resolves true only if they pressed the confirm button. */
function askConfirm(opts) {
    return _askOpen({ icon: 'bi-question-lg', okLabel: 'تأیید', ...opts });
}

/** One value. Resolves the trimmed string, or null if they cancelled. */
function askText(opts) {
    return _askOpen({ icon: 'bi-pencil', okLabel: 'ذخیره', ...opts,
                      field: { ...(opts.field || {}) } });
}

/** Something they only need to acknowledge. */
function askInfo(opts) {
    return _askOpen({ icon: 'bi-info-lg', okLabel: 'باشه', cancelLabel: 'بستن', ...opts });
}

// ═══ SMS forwarder — my phones ══════════════════════════════════════════
//
// The whole point of this section is that somebody who has never heard of a
// webhook can get their phone forwarding Divar's codes. So the guide is
// filled in with THEIR values — their URL, their secret, their SIM — and
// every field is one copy button away. Nothing here asks them to understand
// what a payload template is.

const FW_STATE = {
    ok:           { cls: 'bg-success',                fa: 'سالم' },
    offline:      { cls: 'bg-danger',                 fa: 'آفلاین' },
    never_seen:   { cls: 'bg-secondary',              fa: 'هنوز وصل نشده' },
    no_codes_yet: { cls: 'bg-warning text-dark',      fa: 'وصل، بدون کد' },
    disabled:     { cls: 'bg-secondary',              fa: 'غیرفعال' },
};

function _fwAgo(sec) {
    if (sec == null) return '—';
    if (sec < 90) return 'همین الان';
    if (sec < 3600) return `${formatNumber(Math.round(sec / 60))} دقیقه پیش`;
    if (sec < 86400) return `${formatNumber(Math.round(sec / 3600))} ساعت پیش`;
    return `${formatNumber(Math.round(sec / 86400))} روز پیش`;
}

async function loadForwarders() {
    const tb = document.getElementById('forwarder-table');
    if (!tb) return;
    try {
        const d = await apiCall('/forwarder/devices');
        const rows = d.devices || [];
        if (!rows.length) {
            tb.innerHTML = `<tr><td colspan="6" class="text-muted small p-3">
                هنوز گوشی‌ای اضافه نکرده‌اید. با «افزودن گوشی» شروع کنید —
                بعد از آن راهنمای نصب با تنظیمات خودتان پر می‌شود.</td></tr>`;
            document.getElementById('fw-guide-for').textContent = '—';
            return;
        }
        tb.innerHTML = rows.map(dv => {
            const h = dv.health || {};
            const st = FW_STATE[h.state] || FW_STATE.never_seen;
            return `<tr>
                <td>${esc(dv.label || '—')}<div class="small text-muted" dir="ltr">${esc(dv.device_id)}</div></td>
                <td>${_fwSimsCell(dv)}</td>
                <td><span class="badge ${st.cls}">${st.fa}</span>
                    <div class="small text-muted">${esc(h.message_fa || '')}</div></td>
                <td class="small">${_fwAgo(h.seconds_since_code)}</td>
                <td class="small">${formatNumber(dv.codes_forwarded || 0)}</td>
                <td class="text-nowrap">
                  <button class="btn btn-sm btn-outline-primary" onclick="fwGuide(${dv.id})"
                          title="راهنمای نصب و تنظیمات"><i class="bi bi-book"></i></button>
                  <button class="btn btn-sm btn-outline-secondary" onclick="fwEditPhone(${dv.id})"
                          title="شمارهٔ سیم‌کارت اول"><i class="bi bi-sim"></i> ۱</button>
                  <button class="btn btn-sm btn-outline-secondary" onclick="fwEditPhone(${dv.id}, 2)"
                          title="شمارهٔ سیم‌کارت دوم (گوشی دو سیم‌کارته)"><i class="bi bi-sim"></i> ۲</button>
                  <button class="btn btn-sm btn-outline-secondary" onclick="fwTest(${dv.id})"
                          title="بررسی اتصال"><i class="bi bi-activity"></i></button>
                  <button class="btn btn-sm btn-outline-warning" onclick="fwRotate(${dv.id})"
                          title="کلید تازه (اگر گوشی گم شد)"><i class="bi bi-key"></i></button>
                  <button class="btn btn-sm btn-outline-danger" onclick="fwDelete(${dv.id})"
                          title="حذف"><i class="bi bi-trash"></i></button>
                </td></tr>`;
        }).join('');
        // open the guide on the first device so a new user lands on it
        if (rows.length && !document.getElementById('fw-guide-body').dataset.filled) {
            fwGuide(rows[0].id);
        }
    } catch (e) {
        tb.innerHTML = `<tr><td colspan="6" class="text-danger small p-3">${esc(e.message || 'خطا')}</td></tr>`;
    }
}

/** Both numbers with their slot, so a dual-SIM phone reads as one phone. */
function _fwSimsCell(dv) {
    const sim = (n, p) => p
        ? `<div class="small" dir="ltr"><span class="text-muted">SIM ${n}</span> ${esc(p)}</div>`
        : (n === 1 ? '<div class="small text-muted">—</div>' : '');
    return sim(1, dv.sim_phone) + sim(2, dv.sim_phone2);
}

const FW_REASON = {
    matched:      { cls: 'text-success', fa: 'وارد شد' },
    parked_early: { cls: 'text-info',    fa: 'زودتر رسید' },
    stale_code:   { cls: 'text-warning', fa: 'کد قدیمی' },
    no_code_in_text: { cls: 'text-warning', fa: 'کدی در متن نبود' },
    no_pending_for_account: { cls: 'text-muted', fa: 'درخواستی نبود' },
    already_answered: { cls: 'text-muted', fa: 'قبلاً جواب داده' },
    test:         { cls: 'text-muted',   fa: 'آزمایشی' },
};

// Sent→received, and what to do when that number is impossible.
//
// It is measured across TWO clocks — the phone's sentStamp against our own —
// so a handset whose time is wrong produces a nonsense figure: this column
// showed «۳۱٬۵۳۶٬۰۰۰s» and «-۲۹۹٫۸s» from a phone set to the wrong year.
// Negative transit cannot happen and minutes-long transit is not transit, so
// both are reported as what they actually are: a clock that needs fixing.
function _fwLatency(ms) {
    if (ms == null) return '<span class="text-muted">—</span>';
    if (ms < 0 || ms > 300000) {
        return `<span class="text-warning" title="ساعت گوشی با ساعت سرور هم‌خوان نیست — این عدد قابل اتکا نیست">`
             + `<i class="bi bi-clock-history"></i> ساعت گوشی</span>`;
    }
    const s = Math.round(ms / 100) / 10;
    const cls = s <= 10 ? 'text-success' : s <= 60 ? 'text-warning' : 'text-danger';
    return `<span class="${cls}">${formatNumber(s)}s</span>`;
}

async function loadForwarderLog() {
    const tb = document.getElementById('fw-log-table');
    if (!tb) return;
    const f = document.getElementById('fw-log-filter')?.value || '';
    tb.innerHTML = '<tr><td colspan="4" class="text-muted small p-3">در حال بارگذاری…</td></tr>';
    try {
        const d = await apiCall('/sms/events?limit=100&stage=inbound');
        let rows = d.events || [];
        // «مشکل‌دار» is anything that did not end with the code in the browser
        if (f === 'problem') rows = rows.filter(e => !['matched', 'parked_early', 'test'].includes(e.details?.reason));
        else if (f) rows = rows.filter(e => e.details?.reason === f);
        if (!rows.length) {
            tb.innerHTML = '<tr><td colspan="4" class="text-muted small p-3">چیزی ثبت نشده است</td></tr>';
            return;
        }
        tb.innerHTML = rows.map(e => {
            const d = e.details || {};
            const r = d.reason || '—';
            const m = FW_REASON[r] || { cls: 'text-danger', fa: r };
            const when = e.at ? new Date(e.at).toLocaleString('fa-IR',
                { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
            return `<tr>
                <td class="small text-muted" dir="ltr">${esc(when)}</td>
                <td class="small ${m.cls}">${esc(m.fa)}</td>
                <td class="small" dir="ltr" style="font-family:var(--bs-font-monospace)">${esc(d.code || '—')}</td>
                <td class="small">${esc(e.message)}</td>
                <td class="small" dir="ltr">${_fwLatency(d.latency_ms)}</td>
            </tr>`;
        }).join('');
    } catch (e) {
        tb.innerHTML = `<tr><td colspan="4" class="text-danger small p-3">${esc(e.message || 'خطا')}</td></tr>`;
    }
}

async function addForwarderDevice() {
    const label = await askText({
        icon: 'bi-phone-vibrate', title: 'افزودن گوشی',
        body: 'یک اسم بگذارید تا بعداً بین چند گوشی پیدایش کنید.',
        field: { label: 'اسم گوشی', placeholder: 'گوشی سبحان', value: 'گوشی من' },
    });
    if (label === null) return;
    const sim = await askText({
        icon: 'bi-sim', title: 'شمارهٔ سیم‌کارت',
        body: 'شمارهٔ سیم‌کارتی که داخل این گوشی است — <b>همان شماره‌ای که کد دیوار روی آن می‌آید</b>.',
        note: 'این شماره باید یکی از حساب‌های دیوار خودتان باشد، وگرنه کدهایش پذیرفته نمی‌شود.',
        field: { label: 'شمارهٔ موبایل', placeholder: '09123456789',
                 dir: 'ltr', inputmode: 'numeric',
                 validate: v => /^0?9\d{9}$/.test(v.replace(/\D/g, '')) ? '' : 'شمارهٔ موبایل معتبر نیست' },
    });
    if (sim === null) return;
    const sim2 = await askText({
        icon: 'bi-sim', title: 'سیم‌کارت دوم (اختیاری)',
        body: 'اگر گوشی <b>دو سیم‌کارته</b> است و از هر دو برای دیوار استفاده می‌کنید، شمارهٔ سیم‌کارت دوم را بدهید؛ وگرنه خالی بگذارید.',
        note: 'برنامه برای هر سیم‌کارت جداگانه گوش می‌دهد و کد هر شماره را برای همان حساب دیوار می‌فرستد.',
        okLabel: 'ادامه',
        field: { label: 'شمارهٔ سیم‌کارت دوم', placeholder: '09xxxxxxxxx یا خالی',
                 dir: 'ltr', inputmode: 'numeric',
                 validate: v => (!v.trim() || /^0?9\d{9}$/.test(v.replace(/\D/g, ''))) ? '' : 'شمارهٔ موبایل معتبر نیست' },
    });
    if (sim2 === null) return;
    try {
        const d = await apiCall('/forwarder/devices', {
            method: 'POST',
            body: JSON.stringify({ label: label.trim(), sim_phone: sim.trim(), sim_phone2: sim2.trim() || null }),
        });
        showToast('اضافه شد', 'حالا راهنما را دنبال کنید', 'success');
        await loadForwarders();
        fwGuide(d.id);
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function fwTest(id) {
    try {
        const r = await apiCall(`/forwarder/devices/${id}/test`, { method: 'POST' });
        showToast(r.ok ? 'سالم' : 'هنوز کامل نیست', r.hint_fa || '', r.ok ? 'success' : 'warning');
        loadForwarders();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function fwRotate(id) {
    if (!await askConfirm({ icon: 'bi-key', title: 'کلید تازه', tone: 'warning', okLabel: 'کلید تازه بساز', body: 'کلید تازه ساخته می‌شود و گوشی تا وارد کردن کلید جدید کار نمی‌کند. ادامه؟' })) return;
    try {
        await apiCall(`/forwarder/devices/${id}/rotate`, { method: 'POST' });
        await loadForwarders();
        // The guide is rebuilt from the server, not the page: the QR, the
        // headers and the template all carry the key, and the old one is dead
        // the moment the rotate returns.
        await fwGuide(id);
        showToast('کلید عوض شد', 'کد QR تازه را با گوشی اسکن کنید', 'warning');
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function fwEditPhone(id, slot = 1) {
    const second = slot === 2;
    const sim = await askText({
        icon: 'bi-sim', title: second ? 'سیم‌کارت دوم' : 'سیم‌کارت اول',
        body: second
            ? 'شمارهٔ سیم‌کارت <b>دوم</b> این گوشی — برای گوشی دو سیم‌کارته که از هر دو برای دیوار استفاده می‌کنید. خالی یعنی سیم‌کارت دوم ندارد.'
            : 'شمارهٔ سیم‌کارتی که داخل این گوشی است — <b>همان شماره‌ای که کد دیوار روی آن می‌آید</b>.',
        note: 'کد راه‌اندازی با شمارهٔ تازه ساخته می‌شود؛ گوشی را دوباره اسکن کنید.',
        okLabel: 'ذخیره',
        field: { label: 'شمارهٔ موبایل', placeholder: second ? '09xxxxxxxxx یا خالی' : '09123456789',
                 dir: 'ltr', inputmode: 'numeric',
                 validate: v => ((second && !v.trim()) || /^0?9\d{9}$/.test(v.replace(/\D/g, ''))) ? '' : 'شمارهٔ موبایل معتبر نیست' },
    });
    if (sim === null) return;
    try {
        await apiCall(`/forwarder/devices/${id}`, {
            method: 'PATCH', body: JSON.stringify(second ? { sim_phone2: sim.trim() } : { sim_phone: sim.trim() }),
        });
        await loadForwarders();
        // The number is inside the QR and the template; a guide open on this
        // device would be showing the old one.
        if (_fwQrDeviceId === id) {
            await fwGuide(id);
            showToast('شماره عوض شد', 'کد راه‌اندازی تازه شد — دوباره اسکن کنید', 'warning');
        } else showToast('شماره عوض شد', '', 'success');
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function fwDelete(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'این گوشی حذف شود؟ کلیدش بلافاصله از کار می‌افتد.' })) return;
    try {
        await apiCall(`/forwarder/devices/${id}`, { method: 'DELETE' });
        showToast('حذف شد', '', 'success');
        // A guide open on the deleted device is cleared, not left showing a
        // code for a phone that no longer exists; the list then opens the
        // guide on whichever device remains.
        if (_fwQrDeviceId === id) {
            fwHideQr();
            _fwQrDeviceId = null; _fwSetupPayload = '';
            const box = document.getElementById('fw-guide-body');
            box.dataset.filled = '';
            box.innerHTML = '<p class="text-muted small">گوشی حذف شد. با «افزودن گوشی» راهنما دوباره پر می‌شود.</p>';
            document.getElementById('fw-guide-for').textContent = '—';
        }
        loadForwarders();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

function _fwCopyRow(labelFa, value, hintFa) {
    const id = 'fwv' + Math.random().toString(36).slice(2, 9);
    // Persian values get the panel's face and RTL; only real code gets
    // monospace, which has no Arabic shaping and renders «اطلاعات تماس» as
    // disconnected letters.
    const persian = /[؀-ۿ]/.test(String(value));
    return `<div class="fw-row">
        <label for="${id}">${esc(labelFa)}</label>
        <div class="fw-copy">
          <input id="${id}" class="${persian ? '' : 'is-code'}"
                 dir="${persian ? 'rtl' : 'ltr'}" readonly value="${esc(value)}">
          <button onclick="_fwCopy('${id}', this)"><i class="bi bi-clipboard"></i> کپی</button>
        </div>
        ${hintFa ? `<div class="fw-note">${esc(hintFa)}</div>` : ''}
    </div>`;
}

function _fwCopy(id, btn) {
    const el = document.getElementById(id);
    if (!el) return;
    const done = () => {
        // Feedback on the button itself: a toast saying «copied» in a list of
        // eight fields tells you something was copied, not which.
        if (!btn) return;
        const was = btn.innerHTML;
        btn.classList.add('copied');
        btn.innerHTML = '<i class="bi bi-check-lg"></i> کپی شد';
        setTimeout(() => { btn.classList.remove('copied'); btn.innerHTML = was; }, 1400);
    };
    navigator.clipboard.writeText(el.value).then(done)
        .catch(() => { el.select(); document.execCommand('copy'); done(); });
}

async function fwGuide(id, cfg) {
    const box = document.getElementById('fw-guide-body');
    if (!box) return;
    box.innerHTML = '<p class="text-muted small">در حال آماده‌سازی…</p>';
    document.getElementById('fw-guide').classList.remove('d-none');
    try {
        const c = cfg || await apiCall(`/forwarder/devices/${id}/config`);
        box.dataset.filled = '1';
        document.getElementById('fw-guide-for').textContent = c.device.label || c.device.device_id;
        const r1 = c.rules[0], r2 = c.rules[1];
        const hdr = JSON.stringify(c.headers);
        _fwSetupPayload = c.setup_payload || '';

        box.innerHTML = `
        <ol class="fw-steps">
          <li><b>برنامه را نصب کنید.</b>
            <div class="mt-1 mb-2 d-flex align-items-center gap-2 flex-wrap">
              <a class="btn btn-sm btn-primary" href="${esc(c.android_apk_url)}">
                <i class="bi bi-android2"></i> دانلود برای اندروید${c.android_apk_version ? ` <span class="badge bg-light text-dark ms-1" dir="ltr">${esc(c.android_apk_version)}</span>` : ''}
              </a>
              <a class="small" href="${esc(c.android_release_url)}" target="_blank" rel="noopener">
                <i class="bi bi-github"></i> یا از GitHub
              </a>
              <span class="badge bg-secondary-subtle text-secondary">
                <i class="bi bi-apple"></i> ${esc(c.ios.message_fa)}
              </span>
            </div>
            <div class="fw-note">
              برنامهٔ <b>SorinFlow Forwarder</b> در گوگل‌پلی نیست، چون پیامک‌ها را می‌خواند و گوگل
              برای این کار اجازه نمی‌دهد. فایل را دانلود و نصب کنید. <b>Play Protect هشدار می‌دهد</b> —
              «جزئیات بیشتر ← به هر حال نصب کن» را بزنید؛ برنامه فقط کد دیوار را به همین سرور می‌فرستد.
              برنامه خودش نسخه‌های تازه را از GitHub پیشنهاد می‌دهد.
            </div>
          </li>

          <li><b>به برنامه اجازهٔ خواندن پیامک بدهید.</b>
            <div class="fw-note">اولین بار که باز می‌کنید می‌پرسد (پیامک و اعلان). اگر اشتباهی «نه» زدید:</div>
            <div class="fw-path">
              <b>تنظیمات گوشی</b><span class="sep">←</span><b>برنامه‌ها</b><span class="sep">←</span>
              <b>SorinFlow Forwarder</b><span class="sep">←</span><b>مجوزها</b><span class="sep">←</span>
              <b>پیامک</b><span class="sep">←</span><span class="goal">اجازه</span>
            </div>
            <div class="fw-note">اندروید ۱۳ به بالا برای برنامه‌های نصب‌شده از فایل، مجوز پیامک را قفل می‌کند. اول این را باز کنید:</div>
            <div class="fw-path">
              <b>تنظیمات گوشی</b><span class="sep">←</span><b>برنامه‌ها</b><span class="sep">←</span>
              <b>SorinFlow Forwarder</b><span class="sep">←</span><b>⋮ (بالا راست)</b><span class="sep">←</span>
              <span class="goal">Allow restricted settings</span>
            </div>
          </li>

          <li><b>نگذارید گوشی برنامه را ببندد.</b> <span class="fw-warn">(مهم‌ترین قدم)</span>
            <div class="fw-path">
              <b>تنظیمات گوشی</b><span class="sep">←</span><b>باتری</b><span class="sep">←</span>
              <b>SorinFlow Forwarder</b><span class="sep">←</span><span class="goal">بدون محدودیت</span>
            </div>
            <div class="fw-path">
              <b>تنظیمات گوشی</b><span class="sep">←</span><b>برنامه‌ها</b><span class="sep">←</span>
              <b>SorinFlow Forwarder</b><span class="sep">←</span><span class="goal">Autostart</span>
              <span class="sep">(شیائومی)</span>
            </div>
            <div class="fw-path">
              <b>تنظیمات گوشی</b><span class="sep">←</span><b>برنامه‌ها</b><span class="sep">←</span>
              <b>SorinFlow Forwarder</b><span class="sep">←</span><b>Pause app activity if unused</b><span class="sep">←</span>
              <span class="goal">خاموش</span>
            </div>
            <div class="fw-note">
              و برنامه را در لیست برنامه‌های باز <b>قفل کنید</b>. برنامه خودش هم هر ۱۵ دقیقه
              سرویسش را زنده می‌کند و اجازهٔ «بدون محدودیت» را می‌پرسد — قبولش کنید.
              اگر این کارها را نکنید، گوشی بعد از چند ساعت برنامه را می‌بندد و کدها نمی‌رسند.
              <br>اگر پیامک دیوار در «پیام‌رسان» گوگل می‌آید، <b>RCS را خاموش کنید</b> — پیام RCS اصلاً پیامک نیست.
            </div>
          </li>

          <li><b>تنظیمات را وارد کنید.</b>
            <div class="fw-qr">
              <div class="fw-qr-wrap" id="fw-qr-wrap">
                <div id="fw-setup-qrcode" style="display:inline-block;background:#fff;padding:10px;border-radius:10px"></div>
                <div class="fw-qr-shield" id="fw-qr-shield" role="button" tabindex="0"
                     onclick="fwRevealQr()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();fwRevealQr()}">
                  <i class="bi bi-eye"></i>
                  <span>برای دیدن کد ضربه بزنید</span>
                  <span style="opacity:.7">۳۰ ثانیه نمایش داده می‌شود</span>
                </div>
              </div>
              <div class="fw-qr-timer" id="fw-qr-timer"></div>
              <div class="fw-note">
                در برنامهٔ <b>SorinFlow Forwarder</b>: روی کارت SorinFlow دکمهٔ <b>Set up</b> ←
                <b>Scan QR</b> ← این کد را اسکن کنید ← فیلدها پر می‌شود ← <b>Save</b>.
                همین لینک را اگر روی خود گوشی باز کنید (دوربین یا یک پیام)
                صفحهٔ راه‌اندازی از قبل پر می‌شود.
                <br>این کد شامل <b>کلید مخصوص همین گوشی</b> است؛ آن را برای کسی نفرستید.
                با هر تغییر در پنل (چرخاندن کلید، تغییر شماره) کد تازه می‌شود — کافی است
                دوباره اسکن کنید.
              </div>
              <button onclick="fwCopyPayload()"><i class="bi bi-link-45deg"></i> کپی لینک راه‌اندازی</button>
            </div>
            <div class="fw-qr-or">یا اگر برنامهٔ عمومی SMS Forwarder را دارید، دستی وارد کنید</div>
            <div class="fw-fields">
              ${_fwCopyRow('فرستنده (Sender)', '*', 'ستاره یعنی همهٔ پیامک‌ها — فیلتر متن کار جداسازی را می‌کند')}
              ${_fwCopyRow('فیلتر متن (Text filter)', r1.text_filter, r1.why_fa)}
              ${_fwCopyRow('آدرس (Webhook URL)', c.endpoints.inbound)}
              ${_fwCopyRow('هدرها (Headers)', hdr, 'این شامل کلید مخصوص گوشی شماست — با کسی به اشتراک نگذارید')}
              ${_fwCopyRow('قالب پیام (Json Payload Template)', r1.template)}
              <div class="fw-note">
                در «تنظیمات پیشرفته»: تعداد تلاش مجدد ${formatNumber(c.advanced_fa.retries)}،
                «ذخیرهٔ پیام‌های ناموفق» روشن، «نادیده گرفتن خطای SSL» خاموش.
                <br>${esc(c.advanced_fa.note)}
              </div>
            </div>
          </li>

          <li><b>دکمهٔ «Send test to server» را در برنامه بزنید.</b>
            <div class="fw-note">
              چند ثانیه بعد وضعیت HTTP، زمان رفت‌وبرگشت و پاسخ سرور را نشان می‌دهد و خط سرور
              روی کارت سبز می‌شود. بعد اینجا دکمهٔ
              <i class="bi bi-activity"></i> را بزنید تا از این طرف هم تأیید شود.
            </div>
          </li>

          <li><b>یک قانون دوم برای کد ورود بسازید</b> (اختیاری ولی بهتر است).
            <div class="fw-fields">
              ${_fwCopyRow('فیلتر متن', r2.text_filter, r2.why_fa)}
              ${_fwCopyRow('قالب پیام', r2.template)}
              <div class="fw-note">آدرس و هدرها همان قبلی است.</div>
            </div>
            ${(c.rules_sim2 || []).length ? `
            <div class="fw-note mt-2"><b>گوشی دو سیم‌کارته:</b> کد QR بالا هر دو شماره را دارد و برنامهٔ SorinFlow Forwarder
              خودش برای هر سیم‌کارت یک جفت قانون می‌سازد (SIM 1 ← <span dir="ltr">${esc(c.accounts[0])}</span>،
              SIM 2 ← <span dir="ltr">${esc(c.accounts[1])}</span>). در برنامهٔ عمومی، همین دو قانون را یک بار دیگر
              با «SIM slot = 2» و این قالب‌ها بسازید:</div>
            <div class="fw-fields">
              ${_fwCopyRow('قالب پیام — سیم‌کارت دوم، اطلاعات تماس', c.rules_sim2[0].template)}
              ${_fwCopyRow('قالب پیام — سیم‌کارت دوم، کد ورود', c.rules_sim2[1].template)}
            </div>` : ''}
          </li>
        </ol>

        <div class="fw-tip">
          <b>اگر کدی نرسید چه؟</b> اسکرپر خودش دو بار از دیوار کد تازه می‌خواهد.
          اگر باز هم نیامد، در پنجرهٔ کد دکمهٔ «ارسال دوباره کد» را بزنید.
          وارد کردن دستی کد آخرین گزینه است — و اگر گوشی مشکل داشته باشد،
          برایتان ایمیل می‌فرستیم و می‌گوییم چه چیزی را درست کنید.
        </div>`;
        // After innerHTML, so the container exists. Covered until asked for.
        _fwQrDeviceId = id;
        fwHideQr();
        _fwDrawQr();
    } catch (e) {
        box.innerHTML = `<p class="text-danger small">${esc(e.message || 'خطا')}</p>`;
    }
}

let _fwSetupPayload = '';
let _fwQrTimer = null;
let _fwQrDeviceId = null;

// How long the code stays uncovered. Long enough to line a camera up, short
// enough that walking away does not leave a live credential on the screen.
const FW_QR_REVEAL_SECONDS = 30;

function fwHideQr() {
    if (_fwQrTimer) { clearInterval(_fwQrTimer); _fwQrTimer = null; }
    document.getElementById('fw-qr-wrap')?.classList.remove('revealed');
    const t = document.getElementById('fw-qr-timer');
    if (t) { t.textContent = ''; t.classList.remove('live'); }
}

async function fwRevealQr() {
    _fwUncover();
    // Meanwhile, check it is still the server's code. Rotated from another
    // tab, number changed from the phone — the camera must see the live one,
    // so if a newer one exists the guide is rebuilt and shown uncovered.
    if (await fwRefreshQr()) _fwUncover();
}

function _fwUncover() {
    const wrap = document.getElementById('fw-qr-wrap');
    const t = document.getElementById('fw-qr-timer');
    if (!wrap) return;
    wrap.classList.add('revealed');
    let left = FW_QR_REVEAL_SECONDS;
    const tick = () => {
        if (t) {
            t.classList.add('live');
            t.innerHTML = `${formatNumber(left)} ثانیه تا پنهان شدن دوباره
                — <a href="#" onclick="fwHideQr();return false">همین حالا پنهان کن</a>`;
        }
        if (left-- <= 0) fwHideQr();
    };
    if (_fwQrTimer) clearInterval(_fwQrTimer);
    tick();
    _fwQrTimer = setInterval(tick, 1000);
}

/** Is the guide on screen still what the server would hand out?
 *
 *  Re-fetches this device's config and, only if the setup link moved,
 *  rebuilds the guide from it — the QR, the headers and the template all
 *  carry the key and the number, so none of them may be left behind. No page
 *  reload; an unchanged payload changes nothing on screen. Returns whether
 *  the guide was rebuilt.
 */
async function fwRefreshQr(id) {
    const target = id || _fwQrDeviceId;
    if (!target) return false;
    try {
        const c = await apiCall(`/forwarder/devices/${target}/config`);
        if (c.setup_payload === _fwSetupPayload) return false;   // nothing moved
        await fwGuide(target, c);
        showToast('کد راه‌اندازی تازه شد', 'کد تازه را اسکن کنید', 'warning');
        return true;
    } catch (e) {
        showToast('خطا', e.message || 'کد تازه گرفته نشد', 'danger');
        return false;
    }
}

function _fwDrawQr() {
    const qr = document.getElementById('fw-setup-qrcode');
    if (!qr) return;
    qr.innerHTML = '';
    if (typeof QRCode !== 'undefined' && _fwSetupPayload) {
        new QRCode(qr, {
            text: _fwSetupPayload, width: 220, height: 220,
            correctLevel: QRCode.CorrectLevel.M,
        });
    } else {
        // The library is the only way to draw it; without it, show the link
        // itself so the phone can still be set up.
        qr.innerHTML = `<code style="font-size:.7rem;word-break:break-all;color:#111">${esc(_fwSetupPayload)}</code>`;
    }
}

function fwCopyPayload() {
    if (!_fwSetupPayload) return;
    navigator.clipboard.writeText(_fwSetupPayload)
        .then(() => showToast('کپی شد', 'لینک را روی گوشی باز کنید', 'success'))
        .catch(() => showToast('خطا', 'کپی نشد', 'warning'));
}

async function loadProxies() {
    try {
        const data = await apiCall('/proxies');
        
        const tbody = document.getElementById('proxies-table');
        tbody.innerHTML = '';
        
        if (data.items.length === 0) {
            _proxyCount = 0;
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center text-muted py-4">
                        هیچ پراکسی‌ای وجود ندارد
                    </td>
                </tr>
            `;
            return;
        }
        
        _proxyCount = data.items.length;

        data.items.forEach(proxy => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${esc(proxy.address)}</td>
                <td>${proxy.port}</td>
                <td>
                    <span class="badge ${proxy.is_working ? 'bg-success' : 'bg-danger'}">
                        ${proxy.is_working ? 'فعال' : 'غیرفعال'}
                    </span>
                </td>
                <td>${_proxyExitCell(proxy)}</td>
                <td>${proxy.success_count} / ${proxy.fail_count}</td>
                <td>${proxy.avg_response_time ? proxy.avg_response_time.toFixed(2) + 's' : '---'}</td>
                <td>
                    <button class="btn btn-sm btn-outline-primary" onclick="testProxy(${proxy.id})">
                        <i class="bi bi-speedometer2"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-warning" onclick="toggleProxy(${proxy.id})">
                        <i class="bi bi-toggle-on"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteProxy(${proxy.id})">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(row);
        });
        
    } catch (error) {
        showToast('خطا', 'بارگیری پراکسی‌ها ناموفق بود', 'danger');
    }
}

async function addProxy(e) {
    e.preventDefault();
    
    const address = document.getElementById('proxy-address').value;
    const port = parseInt(document.getElementById('proxy-port').value);
    const protocol = document.getElementById('proxy-protocol').value;
    const username = document.getElementById('proxy-username').value;
    const password = document.getElementById('proxy-password').value;
    
    try {
        await apiCall('/proxies', {
            method: 'POST',
            body: JSON.stringify({ address, port, protocol, username, password })
        });
        
        showToast('موفق', 'پراکسی اضافه شد', 'success');
        e.target.reset();
        loadProxies();
        
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function testProxy(id) {
    try {
        showToast('در حال تست', 'لطفاً صبر کنید...', 'info');
        const result = await apiCall(`/proxies/${id}/test`, { method: 'POST' });
        
        if (result.success) {
            showToast('موفق', `زمان پاسخ: ${result.response_time.toFixed(2)}s`, 'success');
        } else {
            showToast('ناموفق', result.error || 'پراکسی کار نمی‌کند', 'danger');
        }
        
        loadProxies();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function toggleProxy(id) {
    try {
        const result = await apiCall(`/proxies/${id}/toggle`, { method: 'POST' });
        showToast('موفق', result.message, 'success');
        loadProxies();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function deleteProxy(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'آیا از حذف این پراکسی اطمینان دارید؟' })) return;
    
    try {
        await apiCall(`/proxies/${id}`, { method: 'DELETE' });
        showToast('موفق', 'پراکسی حذف شد', 'success');
        loadProxies();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function testAllProxies() {
    try {
        showToast('در حال تست', 'تست همه پراکسی‌ها شروع شد...', 'info');
        const result = await apiCall('/proxies/test-all', { method: 'POST' });
        showToast('موفق', `${result.working} از ${result.total} پراکسی فعال`, 'success');
        loadProxies();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

// ==================== CRM ====================

const CRM_STATUS_LABELS = {
    new: { label: 'جدید', cls: 'bg-warning text-dark' },
    contacted: { label: 'تماس گرفته', cls: 'bg-info text-white' },
    visit: { label: 'بازدید از فایل', cls: 'bg-warning text-dark' },
    contract_meeting: { label: 'نشست و تنظیم قرارداد', cls: 'bg-warning text-dark' },
    qualified: { label: 'واجد شرایط', cls: 'bg-primary text-white' },
    closed: { label: 'بسته شده', cls: 'bg-success text-white' },
    rented: { label: 'اجاره شده', cls: 'bg-purple text-white' },
    rejected: { label: 'رد شده', cls: 'bg-danger text-white' },
};

// جهت and نبش are missing from most Divar ads, so both are entered by hand:
// on the add-lead form below, and in place on any property that is displayed.
// Fixed option lists keep the wording consistent enough for the matching
// engine to compare two properties on them.
const DIRECTION_OPTIONS = ['شمالی', 'جنوبی', 'شرقی', 'غربی',
                           'شمالی جنوبی', 'شرقی غربی',
                           'شمالی شرقی', 'شمالی غربی', 'جنوبی شرقی', 'جنوبی غربی'];
// Only the two a consultant actually records. The scraper still reads
// «تک‌نبش» and «چهارنبش» out of ad text when an ad says so, and both inline
// selects keep an out-of-list value they were given, so nothing already
// stored disappears — it just cannot be chosen by hand any more.
const CORNER_OPTIONS = ['دونبش', 'سه‌نبش'];

// ═══ structured fields per property kind (add-lead form) ═══════
const LEAD_KIND_LABELS = { apartment: 'آپارتمان', villa: 'ویلایی', shop: 'مغازه', office: 'دفتر کار' };
const LEAD_KIND_FIELDS = {
    apartment: [
        { key: 'area', label: 'متراژ', type: 'num' },
        { key: 'floor', label: 'طبقه', type: 'num' },
        { key: 'units_per_floor', label: 'تعداد واحد در طبقه', type: 'num' },
        { key: 'rooms', label: 'تعداد خواب', type: 'num' },
        { key: 'year_built', label: 'سال ساخت', type: 'num' },
        { key: 'has_elevator', label: 'آسانسور', type: 'bool' },
        { key: 'has_parking', label: 'پارکینگ', type: 'bool' },
        { key: 'has_storage', label: 'انباری', type: 'bool' },
        { key: 'cabinets', label: 'کابینت', type: 'text' },
        { key: 'closet', label: 'کمد دیواری', type: 'bool' },
        { key: 'flooring', label: 'پوشش کف', type: 'text' },
        { key: 'has_balcony', label: 'بالکن', type: 'bool' },
        { key: 'delivery_date', label: 'تاریخ تحویل', type: 'text' },
        { key: 'hvac', label: 'گرمایش و سرمایش', type: 'text' },
        { key: 'document_type', label: 'سند', type: 'text' },
        { key: 'building_direction', label: 'جهت', type: 'pick', options: DIRECTION_OPTIONS },
        { key: 'corner_type', label: 'نبش', type: 'pick', options: CORNER_OPTIONS },
    ],
    villa: [
        { key: 'land_area', label: 'متراژ زمین', type: 'num' },
        { key: 'built_area', label: 'زیربنا', type: 'num' },
        { key: 'total_floors', label: 'تعداد طبقات', type: 'num' },
        { key: 'rooms', label: 'تعداد خواب', type: 'num' },
        { key: 'year_built', label: 'سال ساخت', type: 'num' },
        { key: 'has_parking', label: 'پارکینگ', type: 'bool' },
        { key: 'has_storage', label: 'انباری', type: 'bool' },
        { key: 'has_balcony', label: 'بالکن', type: 'bool' },
        { key: 'cabinets', label: 'کابینت', type: 'text' },
        { key: 'closet', label: 'کمد دیواری', type: 'bool' },
        { key: 'flooring', label: 'پوشش کف', type: 'text' },
        { key: 'yard', label: 'حیاط', type: 'text' },
        { key: 'document_type', label: 'سند', type: 'text' },
        { key: 'position', label: 'موقعیت', type: 'text' },
        { key: 'delivery_date', label: 'تاریخ تحویل', type: 'text' },
        { key: 'hvac', label: 'گرمایش و سرمایش', type: 'text' },
        { key: 'building_direction', label: 'جهت', type: 'pick', options: DIRECTION_OPTIONS },
        { key: 'corner_type', label: 'نبش', type: 'pick', options: CORNER_OPTIONS },
    ],
    shop: [
        { key: 'area', label: 'متراژ', type: 'num' },
        { key: 'frontage', label: 'دهنه (متر)', type: 'num' },
        { key: 'height', label: 'ارتفاع (متر)', type: 'text' },
        { key: 'mezzanine', label: 'نیم‌طبقه', type: 'text' },
        { key: 'document_type', label: 'سند', type: 'text' },
        { key: 'building_direction', label: 'جهت', type: 'pick', options: DIRECTION_OPTIONS },
        { key: 'corner_type', label: 'نبش', type: 'pick', options: CORNER_OPTIONS },
    ],
    office: [
        { key: 'floor', label: 'طبقه چندم', type: 'num' },
        { key: 'area', label: 'متراژ', type: 'num' },
        { key: 'rooms', label: 'اتاق', type: 'num' },
        { key: 'kitchen', label: 'آشپزخانه', type: 'text' },
        { key: 'units_per_floor', label: 'واحد در طبقات', type: 'num' },
        { key: 'document_type', label: 'سند', type: 'text' },
        { key: 'building_direction', label: 'جهت', type: 'pick', options: DIRECTION_OPTIONS },
        { key: 'corner_type', label: 'نبش', type: 'pick', options: CORNER_OPTIONS },
    ],
};
// Persian labels for showing extra_attrs in the property modal
const LEAD_ATTR_FA = {};
Object.values(LEAD_KIND_FIELDS).flat().forEach(f => { LEAD_ATTR_FA[f.key] = f.label; });

function renderLeadAttrs() {
    const kind = document.getElementById('add-lead-kind').value;
    const wrap = document.getElementById('add-lead-attrs');
    const fields = LEAD_KIND_FIELDS[kind] || [];
    wrap.innerHTML = fields.map(f => {
        if (f.type === 'bool') {
            return `<div class="col-md-4"><label class="form-label">${f.label}</label>
                <select class="form-select lead-attr" data-key="${f.key}">
                    <option value="">---</option><option value="true">دارد</option><option value="false">ندارد</option>
                </select></div>`;
        }
        if (f.type === 'pick') {
            return `<div class="col-md-4"><label class="form-label">${f.label}</label>
                <select class="form-select lead-attr" data-key="${f.key}">
                    <option value="">---</option>
                    ${(f.options || []).map(o => `<option value="${esc(o)}">${esc(o)}</option>`).join('')}
                </select></div>`;
        }
        const t = f.type === 'num' ? 'number' : 'text';
        return `<div class="col-md-4"><label class="form-label">${f.label}</label>
            <input type="${t}" class="form-control lead-attr" data-key="${f.key}" placeholder="${f.label}"></div>`;
    }).join('');
}

// ═══ lead photos ═══════════════════════════════════════════════
let _leadPhotos = [];

function _renderLeadPhotos() {
    const wrap = document.getElementById('add-lead-photo-previews');
    if (!wrap) return;
    wrap.innerHTML = _leadPhotos.map((u, i) => `
        <div class="lead-photo-thumb">
            <img src="${safeManualPhoto(u)}" alt="">
            <button type="button" onclick="_removeLeadPhoto(${i})">✕</button>
        </div>`).join('');
}

function _removeLeadPhoto(i) { _leadPhotos.splice(i, 1); _renderLeadPhotos(); }

async function uploadLeadPhotos(input) {
    const files = [...(input.files || [])];
    input.value = '';
    if (!files.length) return;
    const status = document.getElementById('add-lead-photo-status');
    for (const f of files) {
        if (_leadPhotos.length >= 20) break;
        status.textContent = `در حال آپلود ${esc(f.name)}...`;
        try {
            const fd = new FormData();
            fd.append('file', f);
            const resp = await fetch(`${API_BASE}/crm/upload-image`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${getToken()}` },
                body: fd,
            });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'خطا در آپلود');
            _leadPhotos.push(data.url);
            _renderLeadPhotos();
        } catch (e) {
            showToast('خطا', e.message || 'آپلود تصویر ناموفق بود', 'danger');
        }
    }
    status.textContent = '';
}

const TASK_PRIORITY_LABELS = {
    low: { label: 'کم', cls: 'bg-secondary' },
    medium: { label: 'متوسط', cls: 'bg-info text-white' },
    high: { label: 'زیاد', cls: 'bg-warning text-dark' },
    urgent: { label: 'فوری', cls: 'bg-danger text-white' },
};
const TASK_STATUS_LABELS = {
    todo: { label: 'انجام نشده', cls: 'bg-secondary' },
    in_progress: { label: 'در حال انجام', cls: 'bg-primary text-white' },
    done: { label: 'انجام شده', cls: 'bg-success text-white' },
};
const DEAL_STATUS_LABELS = {
    new: { label: 'جدید', cls: 'bg-warning text-dark' },
    negotiating: { label: 'مذاکره', cls: 'bg-info text-white' },
    contract: { label: 'قرارداد', cls: 'bg-primary text-white' },
    closed: { label: 'بسته', cls: 'bg-success text-white' },
    cancelled: { label: 'لغو', cls: 'bg-danger text-white' },
};
const CONTACT_TYPE_LABELS = {
    owner:    { label: 'مالکین',   cls: 'bg-primary text-white' },
    landlord: { label: 'موجرین',   cls: 'bg-info text-white' },
    tenant:   { label: 'مستاجرین', cls: 'bg-success text-white' },
    seeker:   { label: 'خواهان',   cls: 'bg-warning text-dark' },
    builder:  { label: 'سازندگان', cls: 'bg-purple text-white' },
    agency:   { label: 'املاک',    cls: 'bg-orange text-white' },
    // legacy values kept so old rows still render
    buyer:    { label: 'خواهان',   cls: 'bg-warning text-dark' },
    consultant:{ label: 'املاک',   cls: 'bg-orange text-white' },
    other:    { label: 'سایر',     cls: 'bg-secondary' },
};
// the six canonical categories (drives dropdowns + the report chart)
const CONTACT_TYPES = ['owner', 'landlord', 'tenant', 'seeker', 'builder', 'agency'];

async function loadCrmStats() {
    try {
        const data = await apiCall('/crm/stats');
        _renderCrmReportStats(data);
    } catch (error) {
        console.error('Failed to load CRM stats:', error);
    }
}

function _renderCrmReportStats(data) {
    const el = document.getElementById('crm-report-stats');
    if (!el) return;
    const cards = [
        { icon: 'bi-people', val: data.contacts?.total ?? 0, label: 'مخاطبین', color: 's-purple' },
        { icon: 'bi-check2-square', val: data.tasks?.todo ?? 0, label: 'وظایف انجام نشده', color: 's-orange' },
        { icon: 'bi-handshake', val: data.deals?.total ?? 0, label: 'معاملات', color: 's-green' },
        { icon: 'bi-alarm', val: data.reminders_due_today ?? 0, label: 'یادآور امروز', color: 's-red' },
        { icon: 'bi-chat-dots', val: data.total_sms ?? 0, label: 'پیامک ارسالی', color: 's-blue' },
        { icon: 'bi-person-check', val: data.leads?.total ?? 0, label: 'کل لیدها', color: 's-teal' },
    ];
    el.innerHTML = cards.map(c => `
        <div class="col-md-2 col-sm-4 col-6">
          <div class="stat-card ${c.color}">
            <div class="stat-icon"><i class="bi ${c.icon}"></i></div>
            <div class="stat-value">${formatNumber(c.val)}</div>
            <div class="stat-label">${c.label}</div>
            <i class="bi ${c.icon} stat-bg-icon"></i>
          </div>
        </div>`).join('');
    _renderCrmCharts(data);
}

// ── Lead funnel: new → contacted → visit → meeting → qualified → closed ──
const _FUNNEL_STAGES = [
    { key: 'new',              label: 'جدید',                grad: 'linear-gradient(90deg,#a78bfa,#8b5cf6)' },
    { key: 'contacted',        label: 'تماس گرفته',          grad: 'linear-gradient(90deg,#b898fb,#a78bfa)' },
    { key: 'visit',            label: 'بازدید از فایل',      grad: 'linear-gradient(90deg,#d3a5fd,#c084fc)' },
    { key: 'contract_meeting', label: 'نشست و تنظیم قرارداد', grad: 'linear-gradient(90deg,#f0a6ff,#e879f9)' },
    { key: 'qualified',        label: 'واجد شرایط',          grad: 'linear-gradient(90deg,#8ee8f8,#67e8f9)' },
    { key: 'closed',           label: 'بسته شده 🏆',          grad: 'linear-gradient(90deg,#5eead4,#34d399)' },
];

function _renderLeadFunnel(data) {
    const el = document.getElementById('crm-lead-funnel');
    if (!el) return;
    const by = data.leads?.by_status || {};
    const total = data.leads?.total || 0;
    const rejected = by.rejected ?? 0;
    const max = Math.max(...(_FUNNEL_STAGES.map(s => by[s.key] ?? 0)), 1);

    el.innerHTML = _FUNNEL_STAGES.map((s, i) => {
        const v = by[s.key] ?? 0;
        const w = Math.max((v / max) * 100, v > 0 ? 9 : 3);
        const pct = total ? Math.round(v * 100 / total) : 0;
        return `
        <div class="funnel-row" style="animation-delay:${i * 70}ms">
            <div class="funnel-label">${s.label}</div>
            <div class="funnel-track">
                <div class="funnel-bar" style="width:${w}%;background:${s.grad}"></div>
            </div>
            <div class="funnel-val">${formatNumber(v)} <small>(${formatNumber(pct)}٪)</small></div>
        </div>`;
    }).join('') + `
        <div class="funnel-foot">
            <span><i class="bi bi-people"></i> کل لیدها: <b>${formatNumber(total)}</b></span>
            <span class="text-danger"><i class="bi bi-x-circle"></i> رد شده: <b>${formatNumber(rejected)}</b></span>
            <span class="text-success"><i class="bi bi-trophy"></i> نرخ تبدیل: <b>${formatNumber(total ? Math.round((by.closed ?? 0) * 100 / total) : 0)}٪</b></span>
        </div>`;
}

// ── Performance summary: progress bars + closed amount ──
function _renderCrmSummary(data) {
    const el = document.getElementById('crm-perf-summary');
    if (!el) return;
    const t = data.tasks || {}, l = data.leads || {};
    const taskPct = t.total ? Math.round((t.done ?? 0) * 100 / t.total) : 0;
    const notifPct = l.total ? Math.round((l.notified ?? 0) * 100 / l.total) : 0;

    const bar = (label, pct, done, total, grad) => `
        <div class="perf-block">
            <div class="perf-head">
                <span>${label}</span>
                <b>${formatNumber(done)} از ${formatNumber(total)} — ${formatNumber(pct)}٪</b>
            </div>
            <div class="perf-track"><div class="perf-fill" style="width:${pct}%;background:${grad}"></div></div>
        </div>`;

    el.innerHTML = `
        ${bar('وظایف انجام‌شده', taskPct, t.done ?? 0, t.total ?? 0, 'linear-gradient(90deg,#a78bfa,#f0a6ff)')}
        ${bar('لیدهای اطلاع‌رسانی‌شده', notifPct, l.notified ?? 0, l.total ?? 0, 'linear-gradient(90deg,#67e8f9,#38bdf8)')}
        <div class="perf-badges">
            <span class="perf-chip ${t.overdue ? 'chip-danger' : ''}"><i class="bi bi-hourglass-split"></i> وظایف معوق: <b>${formatNumber(t.overdue ?? 0)}</b></span>
            <span class="perf-chip"><i class="bi bi-alarm"></i> یادآور امروز: <b>${formatNumber(data.reminders_due_today ?? 0)}</b></span>
        </div>
        <div class="perf-amount">
            <div class="pa-label">💰 جمع مبلغ قراردادهای بسته‌شده</div>
            <div class="pa-value">${formatPrice(data.deals?.closed_amount)}</div>
        </div>`;
}

function _renderCrmCharts(data) {
    _renderLeadFunnel(data);
    _renderCrmSummary(data);

    const dealsCtx = document.getElementById('crm-deals-chart');
    const contactsCtx = document.getElementById('crm-contacts-chart');
    if (!dealsCtx || !contactsCtx) return;

    const dealStatusLabels = Object.keys(DEAL_STATUS_LABELS);
    const dealValues = dealStatusLabels.map(k => data.deals?.by_status?.[k] ?? 0);
    const dealLabels = dealStatusLabels.map(k => DEAL_STATUS_LABELS[k].label);
    const dealsTotal = dealValues.reduce((s, v) => s + v, 0);

    const contactValues = CONTACT_TYPES.map(k => data.contacts?.by_type?.[k] ?? 0);
    const contactLabels = CONTACT_TYPES.map(k => CONTACT_TYPE_LABELS[k].label);

    if (window._crmDealsChart) window._crmDealsChart.destroy();
    if (window._crmContactsChart) window._crmContactsChart.destroy();

    const themeC = chartColors();

    // neon doughnut with center total (matches the dashboard charts)
    window._crmDealsChart = new Chart(dealsCtx, {
        type: 'doughnut',
        data: {
            labels: dealLabels,
            datasets: [{
                data: dealValues,
                backgroundColor: ['#fcd34d','#67e8f9','#a78bfa','#34d399','#fb7185'],
                borderWidth: 0, borderRadius: 9, spacing: 4, hoverOffset: 14,
            }]
        },
        plugins: [_sfGlow, _sfCenter],
        options: {
            responsive: true, maintainAspectRatio: false, cutout: '72%',
            animation: { duration: 1000, easing: 'easeOutQuart' },
            plugins: {
                sfGlow: { color: 'rgba(167,139,250,.35)', blur: 18 },
                sfCenter: {
                    big: formatNumber(dealsTotal), sub: 'معامله',
                    color: themeC.text === '#475569' ? '#1e2740' : '#f2f3f8',
                    subColor: themeC.text,
                },
                legend: {
                    position: 'bottom',
                    labels: {
                        color: themeC.text, font: { family: FA_FONT, size: 11 },
                        usePointStyle: true, pointStyle: 'circle', boxWidth: 7, padding: 12,
                    }
                },
                tooltip: {
                    ..._sfTooltip(themeC),
                    callbacks: { label: c => ` ${formatNumber(c.parsed)} معامله` }
                }
            }
        }
    });

    // horizontal gradient bars with integer Persian axis
    const cctx = contactsCtx.getContext('2d');
    const bgrad = cctx.createLinearGradient(0, 0, contactsCtx.parentElement?.clientWidth || 400, 0);
    bgrad.addColorStop(0, 'rgba(167,139,250,.9)');
    bgrad.addColorStop(1, 'rgba(103,232,249,.75)');

    window._crmContactsChart = new Chart(contactsCtx, {
        type: 'bar',
        data: {
            labels: contactLabels,
            datasets: [{ data: contactValues, backgroundColor: bgrad, borderRadius: 9, barThickness: 22 }]
        },
        options: {
            indexAxis: 'y',
            responsive: true, maintainAspectRatio: false,
            animation: { duration: 900, easing: 'easeOutQuart' },
            plugins: {
                legend: { display: false },
                tooltip: {
                    ..._sfTooltip(themeC),
                    callbacks: { label: c => ` ${formatNumber(c.parsed.x)} مخاطب` }
                }
            },
            scales: {
                x: {
                    beginAtZero: true,
                    grid: { color: themeC.grid }, border: { display: false },
                    ticks: {
                        color: themeC.tick, font: { family: FA_FONT, size: 11 },
                        precision: 0, callback: v => formatNumber(v),
                    }
                },
                y: {
                    grid: { color: 'transparent' }, border: { display: false },
                    ticks: { color: themeC.text, font: { family: FA_FONT, size: 12, weight: '600' } }
                }
            }
        }
    });
}

// ── Persian date helpers ──────────────────────────────────────────────────────

function _toDateStr(jsDate) {
    return `${jsDate.getFullYear()}-${String(jsDate.getMonth()+1).padStart(2,'0')}-${String(jsDate.getDate()).padStart(2,'0')}`;
}

function jalaliToGregorian(jalaliStr) {
    if (!jalaliStr || !jalaliStr.trim()) return '';
    try {
        const parts = jalaliStr.trim().split('/').map(Number);
        if (parts.length < 3 || parts.some(isNaN)) return '';
        const jsDate = new persianDate(parts).toDate();
        const result = _toDateStr(jsDate);
        // sanity check
        const y = jsDate.getFullYear();
        if (y < 2000 || y > 2100) return '';
        return result;
    } catch(e) { console.warn('jalaliToGregorian failed:', jalaliStr, e); return ''; }
}

function gregorianToJalali(jsDate) {
    try {
        const pd = new persianDate(jsDate);
        return `${pd.year()}/${String(pd.month()).padStart(2,'0')}/${String(pd.date()).padStart(2,'0')}`;
    } catch(e) { return ''; }
}

function _initLeadsDatePickers() {
    const opts = {
        format: 'YYYY/MM/DD',
        autoClose: true,
        observer: true,
        calendar: { persian: { locale: 'fa' } },
        onSelect: () => {
            const fromJ = document.getElementById('crm-filter-date-from').value;
            const toJ   = document.getElementById('crm-filter-date-to').value;
            _leadsDateFrom = jalaliToGregorian(fromJ);
            _leadsDateTo   = jalaliToGregorian(toJ);
            _updateActiveDateLabel();
            loadLeads();
        },
    };
    $('#crm-filter-date-from').persianDatepicker(opts);
    $('#crm-filter-date-to').persianDatepicker(opts);
}

function _updateActiveDateLabel() {
    const from = document.getElementById('crm-filter-date-from').value;
    const to   = document.getElementById('crm-filter-date-to').value;
    const label = document.getElementById('leads-active-date-label');
    if (!label) return;
    if (from || to) {
        label.textContent = `${from || '…'} تا ${to || '…'}`;
        label.classList.remove('d-none');
    } else {
        label.classList.add('d-none');
    }
}

function setLeadsDatePreset(preset) {
    const now = new Date();
    let from = new Date(now);
    if (preset === 'week')        { from.setDate(now.getDate() - 6); }
    else if (preset === 'month')  { from.setDate(1); }
    else if (preset === 'last30') { from.setDate(now.getDate() - 30); }
    // 'today': from = now (same date)

    // Store Gregorian directly — no Jalali round-trip needed
    _leadsDateFrom = _toDateStr(from);
    _leadsDateTo   = _toDateStr(now);

    document.getElementById('crm-filter-date-from').value = gregorianToJalali(from);
    document.getElementById('crm-filter-date-to').value   = gregorianToJalali(now);
    _updateActiveDateLabel();
    loadLeads();
}

function clearLeadsDateFilter() {
    _leadsDateFrom = '';
    _leadsDateTo   = '';
    document.getElementById('crm-filter-date-from').value = '';
    document.getElementById('crm-filter-date-to').value   = '';
    _updateActiveDateLabel();
    loadLeads();
}

function clearLeadsFilter() {
    document.getElementById('crm-filter-status').value   = '';
    document.getElementById('crm-filter-notified').value = '';
    const searchEl = document.getElementById('crm-filter-search');
    if (searchEl) searchEl.value = '';
    const catEl = document.getElementById('crm-filter-category');
    if (catEl) catEl.value = '';
    const kindEl = document.getElementById('crm-filter-kind');
    if (kindEl) kindEl.value = '';
    const advEl = document.getElementById('crm-filter-advertiser');
    if (advEl) advEl.value = '';
    _resetLeadsPriceInputs();
    clearLeadsDateFilter();          // reloads the list
}

/** Empty both price boxes and put the slider handles back at the ends. */
function _resetLeadsPriceInputs() {
    ['crm-filter-price-min', 'crm-filter-price-max'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    document.querySelector('.range-slider[data-min-input="crm-filter-price-min"]')?._syncRange?.();
}

function clearLeadsPriceFilter() {
    _resetLeadsPriceInputs();
    reloadLeadsFromFilter();
}

/** What the price slider calls: narrowing the band while sitting on page 5
 *  would otherwise land on a page the smaller result set does not have. */
function reloadLeadsFromFilter() {
    _leadsPage = 1;
    loadLeads();
}

// ═══ Leads: pagination, quick status change, bulk actions ═══════
let _leadsPage = 1;
const LEADS_PAGE_SIZE = 25;
const _selectedLeads = new Set();

function goToLeadsPage(page) { _leadsPage = Math.max(page, 1); loadLeads(); }

function _renderLeadsPagination(total) {
    const wrap = document.getElementById('leads-pagination');
    if (!wrap) return;
    const pages = Math.max(Math.ceil(total / LEADS_PAGE_SIZE), 1);
    if (pages <= 1) { wrap.innerHTML = ''; return; }
    const add = (label, page, opts = {}) =>
        `<li class="page-item ${opts.active ? 'active' : ''} ${opts.disabled ? 'disabled' : ''}">`
        + (opts.gap ? `<span class="page-link">…</span>`
                    : `<a class="page-link" href="#" onclick="goToLeadsPage(${page}); return false;">${label}</a>`)
        + '</li>';
    let html = add('‹', Math.max(_leadsPage - 1, 1), { disabled: _leadsPage === 1 });
    let last = 0;
    for (let i = 1; i <= pages; i++) {
        if (i === 1 || i === pages || Math.abs(i - _leadsPage) <= 2) {
            if (i - last > 1) html += add('', 0, { gap: true });
            html += add(formatNumber(i), i, { active: i === _leadsPage });
            last = i;
        }
    }
    html += add('›', Math.min(_leadsPage + 1, pages), { disabled: _leadsPage === pages });
    wrap.innerHTML = `<ul class="pagination pagination-sm justify-content-center mb-0">${html}</ul>`;
}

function toggleLeadSelection(id, checked) {
    if (checked) _selectedLeads.add(id); else _selectedLeads.delete(id);
    _updateBulkBar();
}

function toggleAllLeads(checked) {
    document.querySelectorAll('.lead-check').forEach(cb => {
        cb.checked = checked;
        const id = Number(cb.dataset.id);
        if (checked) _selectedLeads.add(id); else _selectedLeads.delete(id);
    });
    _updateBulkBar();
}

function _updateBulkBar() {
    const bar = document.getElementById('leads-bulk-bar');
    const count = document.getElementById('leads-bulk-count');
    if (!bar) return;
    bar.classList.toggle('d-none', _selectedLeads.size === 0);
    if (count) count.textContent = formatNumber(_selectedLeads.size);
}

function clearLeadSelection() {
    _selectedLeads.clear();
    document.querySelectorAll('.lead-check').forEach(cb => { cb.checked = false; });
    const all = document.getElementById('leads-check-all');
    if (all) all.checked = false;
    _updateBulkBar();
}

async function quickLeadStatus(id, status, selectEl) {
    try {
        await apiCall(`/crm/leads/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) });
        showToast('موفق', 'وضعیت لید تغییر کرد', 'success');
        loadLeads();
        loadCrmStats();
    } catch (e) {
        showToast('خطا', e.message, 'danger');
        if (selectEl) loadLeads();   // revert the visual change
    }
}

async function bulkLeadStatus(status) {
    if (!_selectedLeads.size || !status) return;
    try {
        const r = await apiCall('/crm/leads/bulk', {
            method: 'POST',
            body: JSON.stringify({ ids: [..._selectedLeads], action: 'status', status })
        });
        showToast('موفق', `${formatNumber(r.updated)} لید بروزرسانی شد`, 'success');
        clearLeadSelection();
        loadLeads(); loadCrmStats();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function bulkDeleteLeads() {
    if (!_selectedLeads.size) return;
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: `${_selectedLeads.size} لید انتخاب‌شده حذف شوند؟ این عمل قابل بازگشت نیست.` })) return;
    try {
        const r = await apiCall('/crm/leads/bulk', {
            method: 'POST',
            body: JSON.stringify({ ids: [..._selectedLeads], action: 'delete' })
        });
        showToast('موفق', `${formatNumber(r.deleted)} لید حذف شد`, 'success');
        clearLeadSelection();
        loadLeads(); loadCrmStats();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

// Compact «مشخصات» cell for the leads table: سند / پارکینگ / آسانسور / جهت.
// Four separate columns would push the table into horizontal scrolling, so
// they share one cell as chips — full text stays in the tooltip and the modal.
function _leadSpecChips(lead) {
    const chips = [];
    const short = (v, n) => (v.length > n ? v.substring(0, n) + '…' : v);

    if (lead.document_type) {
        chips.push(`<span class="lead-chip" title="سند: ${esc(lead.document_type)}">
            <i class="bi bi-file-earmark-text"></i>${esc(short(lead.document_type, 9))}</span>`);
    }
    const bool = (val, icon, label) => {
        if (val === null || val === undefined) return;
        chips.push(`<span class="lead-chip ${val ? 'on' : 'off'}" title="${label}: ${val ? 'دارد' : 'ندارد'}">
            <i class="bi ${icon}"></i></span>`);
    };
    bool(lead.has_parking, 'bi-car-front', 'پارکینگ');
    bool(lead.has_elevator, 'bi-arrow-up-square', 'آسانسور');

    if (lead.building_direction) {
        chips.push(`<span class="lead-chip" title="جهت: ${esc(lead.building_direction)}">
            <i class="bi bi-compass"></i>${esc(short(lead.building_direction, 8))}</span>`);
    }
    if (lead.corner_type) {
        chips.push(`<span class="lead-chip corner" title="نبش: ${esc(lead.corner_type)}">
            <i class="bi bi-bounding-box"></i>${esc(lead.corner_type)}</span>`);
    }
    return chips.length
        ? `<div class="lead-chips">${chips.join('')}</div>`
        : '<span class="text-muted">---</span>';
}

/** Every active leads filter as a query string — no paging.
 *  The list and the Excel export both build on this, so the file you download
 *  is the view you were looking at. */
function _leadsQueryString() {
    const status   = document.getElementById('crm-filter-status')?.value || '';
    const notified = document.getElementById('crm-filter-notified')?.value ?? '';
    const search   = document.getElementById('crm-filter-search')?.value.trim() || '';
    const category = document.getElementById('crm-filter-category')?.value || '';
    const kind     = document.getElementById('crm-filter-kind')?.value || '';
    const adv      = document.getElementById('crm-filter-advertiser')?.value || '';
    // _intOrNull reads the field itself so it can strip the «/» separators
    const priceMin = _intOrNull('crm-filter-price-min');
    const priceMax = _intOrNull('crm-filter-price-max');

    const parts = [];
    if (status)           parts.push(`status=${encodeURIComponent(status)}`);
    if (notified !== '')  parts.push(`notified=${notified}`);
    if (search)           parts.push(`search=${encodeURIComponent(search)}`);
    if (category)         parts.push(`category=${encodeURIComponent(category)}`);
    if (kind)             parts.push(`property_kind=${encodeURIComponent(kind)}`);
    if (adv)              parts.push(`advertiser=${encodeURIComponent(adv)}`);
    if (_leadsDateFrom)   parts.push(`date_from=${_leadsDateFrom}`);
    if (_leadsDateTo)     parts.push(`date_to=${_leadsDateTo}`);
    if (priceMin != null) parts.push(`price_min=${priceMin}`);
    if (priceMax != null) parts.push(`price_max=${priceMax}`);
    return parts.join('&');
}

async function loadLeads() {
    const filters = _leadsQueryString();
    let url = `/crm/leads?limit=${LEADS_PAGE_SIZE}&offset=${(_leadsPage - 1) * LEADS_PAGE_SIZE}`;
    if (filters) url += '&' + filters;

    try {
        const data = await apiCall(url);
        const tbody = document.getElementById('crm-leads-table');
        tbody.innerHTML = '';

        const badge = document.getElementById('leads-count-badge');
        if (badge) badge.textContent = data.total ?? data.items.length;

        if (data.items.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="9" class="text-center text-muted py-4">
                        <i class="bi bi-inbox" style="font-size:2rem;"></i>
                        <p class="mt-2">هیچ لیدی یافت نشد</p>
                    </td>
                </tr>`;
            _renderLeadsPagination(data.total ?? 0);
            return;
        }

        data.items.forEach(lead => {
            const st = CRM_STATUS_LABELS[lead.status] || { label: lead.status, cls: 'bg-secondary' };
            // «اطلاع‌رسانی» lost its column: it is a line under the date now
            const notifiedLine = lead.notified
                ? `<small class="lead-subline text-success" title="اطلاع‌رسانی انجام شده"><i class="bi bi-bell-fill"></i> اطلاع داده شد</small>`
                : '';
            const where = [lead.city_name, lead.area ? formatNumber(lead.area) + ' متر' : ''].filter(Boolean).join(' · ');
            const createdAt = lead.created_at
                ? new Date(lead.created_at).toLocaleDateString('fa-IR')
                : '---';
            const scrapedAt = lead.scraped_at
                ? new Date(lead.scraped_at).toLocaleDateString('fa-IR')
                : '';

            const row = document.createElement('tr');
            row.innerHTML = `
                <td><input type="checkbox" class="form-check-input lead-check" data-id="${lead.id}"
                           ${_selectedLeads.has(lead.id) ? 'checked' : ''}
                           onchange="toggleLeadSelection(${lead.id}, this.checked)"></td>
                <td>${lead.serial_no != null
                        ? `<span class="serial-badge" title="کد ملک — همان کدی که در لیست املاک است">${formatSerial(lead.serial_no)}</span>`
                        : '<span class="text-muted" title="ملک این لید حذف شده است">—</span>'}</td>
                <td class="leads-title" title="${esc(lead.property_title)}">
                    <div class="leads-title-text">${esc((lead.property_title || '---').substring(0, 35))}... ${agencyBadge(lead)} ${typeof aiDuplicateBadge === 'function' ? aiDuplicateBadge(lead) : ''}</div>
                    ${where ? `<small class="lead-subline">${esc(where)}</small>` : ''}
                </td>
                <td class="leads-price">
                    <div>${formatPrice(lead.price)}</div>
                    ${lead.price_per_meter ? `<small class="lead-subline" title="قیمت هر متر">${formatPrice(lead.price_per_meter)} <span class="opacity-75">/ متر</span></small>` : ''}
                </td>
                <td class="leads-spec">${_leadSpecChips(lead)}</td>
                <td class="leads-phone">
                    ${lead.phone_number
                        ? `<a href="tel:${safeTel(lead.phone_number)}" class="text-success fw-bold">${esc(lead.phone_number)}</a>`
                        : noPhoneCell(lead)}
                </td>
                <td>
                    <select class="form-select form-select-sm status-quick ${st.cls}"
                            onchange="quickLeadStatus(${lead.id}, this.value, this)">
                        ${Object.entries(CRM_STATUS_LABELS).map(([val, info]) =>
                            `<option value="${val}" ${lead.status === val ? 'selected' : ''}>${info.label}</option>`
                        ).join('')}
                    </select>
                </td>
                <td class="leads-date">
                    <div>${createdAt}</div>
                    ${scrapedAt ? `<small class="lead-subline" title="تاریخ برداشت آگهی"><i class="bi bi-download"></i> ${scrapedAt}</small>` : ''}
                    ${notifiedLine}
                </td>
                <td class="u-menu">
                    <div class="leads-actions">
                        <button class="btn btn-sm btn-outline-primary" onclick="viewLead(${lead.id})" title="ویرایش لید">
                            <i class="bi bi-pencil"></i>
                        </button>
                        <div class="dropdown">
                            <button class="u-kebab" data-bs-toggle="dropdown" aria-expanded="false" title="بیشتر">
                                <i class="bi bi-three-dots-vertical"></i>
                            </button>
                            <ul class="dropdown-menu">
                                ${!lead.notified ? `<li><button class="dropdown-item" onclick="notifyLead(${lead.id})"><i class="bi bi-bell"></i> ارسال اطلاع</button></li>` : ''}
                                <li><a class="dropdown-item" href="${safeUrl(lead.property_url)}" target="_blank" rel="noopener"><i class="bi bi-box-arrow-up-left"></i> باز کردن آگهی</a></li>
                                <li><button class="dropdown-item" onclick="showSimilarForLead(${lead.id})"><i class="bi bi-diagram-3"></i> ملک‌های مشابه</button></li>
                                <li><button class="dropdown-item" onclick="moveFilePick(${lead.property_id})"><i class="bi bi-folder-symlink"></i> بایگانی در زونکن</button></li>
                                <li><hr class="dropdown-divider"></li>
                                <li><button class="dropdown-item text-danger" onclick="deleteLead(${lead.id})"><i class="bi bi-trash"></i> حذف لید</button></li>
                            </ul>
                        </div>
                    </div>
                </td>
            `;
            tbody.appendChild(row);
        });

        _renderLeadsPagination(data.total ?? data.items.length);
        // keep the header checkbox in sync with the rendered page
        const allBox = document.getElementById('leads-check-all');
        if (allBox) {
            const boxes = [...document.querySelectorAll('.lead-check')];
            allBox.checked = boxes.length > 0 && boxes.every(cb => cb.checked);
        }
        _updateBulkBar();
    } catch (error) {
        showToast('خطا', 'بارگیری لیدها ناموفق بود', 'danger');
    }
}

/**
 * A select that writes straight back to the property.
 * Free-typed values already on the record (e.g. scraped wording that is not
 * in the list) are kept as an extra option so editing never silently drops them.
 */
function _propertyFieldSelect(propId, field, value, options, label) {
    if (!propId) return `<div>${esc(value) || '---'}</div>`;
    const all = value && !options.includes(value) ? [value, ...options] : options;
    const opts = ['<option value="">— نامشخص —</option>']
        .concat(all.map(o => `<option value="${esc(o)}"${o === value ? ' selected' : ''}>${esc(o)}</option>`))
        .join('');
    return `<select class="form-select form-select-sm prop-inline-edit" title="${label} — با انتخاب، ذخیره می‌شود"
                    onchange="savePropertyField(${propId}, '${field}', this.value, this)">${opts}</select>`;
}

async function savePropertyField(propId, field, value, el) {
    const previous = el?.dataset.previous ?? '';
    if (el) el.disabled = true;
    try {
        await apiCall(`/properties/${propId}`, {
            method: 'PATCH', body: JSON.stringify({ [field]: value || null })
        });
        if (el) el.dataset.previous = value;
        showToast('ذخیره شد', value ? `ثبت شد: ${value}` : 'مقدار پاک شد', 'success');
    } catch (e) {
        if (el) el.value = previous;      // put the old choice back on failure
        showToast('خطا', e.message, 'danger');
    } finally {
        if (el) el.disabled = false;
    }
}

/* «املاکی» — what the ad says about who posted it.
 *
 * Divar asks the poster to declare this and the answer is not always right:
 * a listing whose description ends «املاک هستم» came back from a search
 * filtered to «شخصی», and Divar's own site returns it there too. So the two
 * answers are shown as two answers. Red is the disagreement — the ad arrived
 * through a «شخصی» filter and should not have; grey is an agency that said
 * so itself. The phrase that gave it away is in the tooltip, because a label
 * nobody can check is a label nobody will trust. */
/* «---» in the phone column said nothing. Three different facts hide behind
 * a blank: the poster took contact through chat only (their choice — no run
 * will ever fill it), the reveal failed (ours — worth a retry), or the row
 * predates the distinction. Say which. */
function noPhoneCell(p) {
    const ch = p && p.contact_channel;
    if (ch === 'chat_only') {
        return '<span class="badge bg-secondary" title="آگهی‌دهنده شماره را مخفی کرده و فقط از طریق چت دیوار پاسخ می‌دهد">'
             + '<i class="bi bi-chat-dots"></i> فقط چت</span>';
    }
    if (ch === 'unavailable') {
        return '<span class="text-warning small" title="شماره در این اسکرپ گرفته نشد — در اجرای بعدی دوباره تلاش می‌شود">'
             + 'گرفته نشد</span>';
    }
    return '<span class="text-muted">---</span>';
}

function agencyBadge(p) {

    if (!p || !p.agency_suspected) return '';
    // On a lead row Divar's declaration arrives under its own name: `Lead`
    // has no advertiser_type of its own, and reusing the property's name for
    // a column copied off the linked property is how the two get confused.
    const declared = p.advertiser_type || p.lead_advertiser_type || '';
    const clash = declared === 'personal';
    const evidence = p.agency_evidence ? ` — «${p.agency_evidence}»` : '';
    const title = clash
        ? `متن آگهی می‌گوید مشاور املاک است${evidence}؛ ولی دیوار آن را «شخصی» ثبت کرده`
        : `متن آگهی می‌گوید مشاور املاک است${evidence}`;
    return `<span class="badge ${clash ? 'bg-danger' : 'bg-secondary'}"
                  style="font-size:.65rem;vertical-align:middle"
                  title="${esc(title)}">املاکی</span>`;
}

// Full property details block — identical data to the لیست املاک modal,
// reused inside the CRM lead modal.
function _renderPropertyDetails(p) {
    if (!p) return '';
    const row = (label, value) => value === null || value === undefined || value === '' || value === '---'
        ? '' : `<div class="col-md-4"><label class="text-muted small">${label}</label><div>${value}</div></div>`;
    const num = v => (v === null || v === undefined) ? '' : formatNumber(v);
    const yn  = v => v ? '✅ دارد' : '❌ ندارد';

    const specs = [
        row('کد ملک', p.serial_no != null ? `<span class="serial-badge">${formatSerial(p.serial_no)}</span>` : ''),
        row('نوع ملک', esc(p.property_type)),
        row('دسته‌بندی', esc(p.category_name)),
        row('متراژ', p.area ? num(p.area) + ' متر' : ''),
        row('متراژ زمین', p.land_area ? num(p.land_area) + ' متر' : ''),
        row('زیربنا', p.built_area ? num(p.built_area) + ' متر' : ''),
        row('تعداد اتاق', p.rooms != null ? num(p.rooms) : ''),
        row('طبقه', p.floor != null ? num(p.floor) : ''),
        row('کل طبقات', p.total_floors ? num(p.total_floors) : ''),
        row('سال ساخت', p.year_built ? num(p.year_built) : ''),
        row('سن بنا', esc(p.building_age)),
        // always rendered, even when empty — these two are meant to be filled in
        `<div class="col-md-4"><label class="text-muted small">جهت ساختمان</label>
            ${_propertyFieldSelect(p.id, 'building_direction', p.building_direction, DIRECTION_OPTIONS, 'جهت ساختمان')}</div>`,
        `<div class="col-md-4"><label class="text-muted small">نبش</label>
            ${_propertyFieldSelect(p.id, 'corner_type', p.corner_type, CORNER_OPTIONS, 'نبش')}</div>`,
        row('بر', p.frontage ? num(p.frontage) + ' متر' : ''),
        row('وضعیت واحد', esc(p.unit_status)),
        row('نوع سند', esc(p.document_type)),
        row('نوع کاربری', esc(p.usage_type)),
        row('آگهی‌دهنده (دیوار)',
            p.advertiser_type === 'agency' ? 'مشاور املاک'
            : p.advertiser_type === 'personal' ? 'شخصی' : ''),
        // Our own reading, next to Divar's, never over it.
        row('آگهی‌دهنده (متن آگهی)', p.agency_suspected
            ? `مشاور املاک ${agencyBadge(p)}${p.agency_evidence
                 ? `<div class="text-muted" style="font-size:.72rem">«${esc(p.agency_evidence)}»</div>` : ''}`
            : ''),
    ].join('');

    const prices = [
        row('قیمت کل', p.total_price ? formatPrice(p.total_price) : ''),
        row('قیمت هر متر', p.price_per_meter ? formatPrice(p.price_per_meter) : ''),
        row('ودیعه', p.deposit ? formatPrice(p.deposit) : ''),
        row('اجاره ماهانه', p.rent_price ? formatPrice(p.rent_price) : ''),
    ].join('');

    const amenities = [
        row('آسانسور', yn(p.has_elevator)), row('پارکینگ', yn(p.has_parking)),
        row('انباری', yn(p.has_storage)),  row('بالکن', yn(p.has_balcony)),
    ].join('');

    const location = [
        row('شهر', esc(p.city_name)), row('منطقه', esc(p.district)), row('محله', esc(p.neighborhood)),
        p.address ? `<div class="col-12"><label class="text-muted small">آدرس</label><div>${esc(p.address)}</div></div>` : '',
        (p.latitude && p.longitude)
            ? `<div class="col-12"><a href="https://www.google.com/maps?q=${p.latitude},${p.longitude}" target="_blank" class="btn btn-sm btn-outline-primary"><i class="bi bi-map"></i> مشاهده در نقشه</a></div>`
            : '',
    ].join('');

    const extras = Object.entries(p.extra_attrs || {})
        .map(([k, v]) => row(LEAD_ATTR_FA[k] || esc(k), esc(v))).join('');

    const images = (p.images && p.images.length) ? `
        <div class="card mb-3">
            <div class="card-header"><i class="bi bi-images"></i> تصاویر (${formatNumber(p.images.length)})</div>
            <div class="card-body">
                <div class="lead-photo-strip">
                    ${p.images.map((img, i) => `
                        <div class="lead-photo-thumb" style="width:92px;height:92px;cursor:zoom-in">
                            <img src="${safeUrl(img)}" alt="تصویر ${i + 1}" onclick="openImageLightbox(this.src)">
                        </div>`).join('')}
                </div>
            </div>
        </div>` : '';

    const section = (icon, title, body) => body.trim()
        ? `<div class="card mb-3"><div class="card-header"><i class="bi ${icon}"></i> ${title}</div>
             <div class="card-body"><div class="row g-3">${body}</div></div></div>` : '';

    return `
        <hr>
        <h6 class="mb-3"><i class="bi bi-house-door"></i> جزئیات کامل ملک</h6>
        ${images}
        ${section('bi-info-circle', 'مشخصات ملک', specs)}
        ${section('bi-currency-exchange', 'اطلاعات قیمت', prices)}
        ${section('bi-stars', 'امکانات', amenities)}
        ${section('bi-list-columns', 'مشخصات تکمیلی', extras)}
        ${section('bi-geo-alt', 'موقعیت مکانی', location)}
        ${p.description ? `<div class="card mb-3"><div class="card-header"><i class="bi bi-card-text"></i> توضیحات</div>
            <div class="card-body"><pre style="white-space:pre-wrap;font-family:inherit;font-size:.9rem;margin:0;line-height:1.7">${esc(p.description)}</pre></div></div>` : ''}
    `;
}

// ═══ Activity timeline & lead → deal conversion ═══════════════════
const ACTIVITY_ICONS = {
    status_change: 'bi-arrow-repeat', note: 'bi-journal-text', created: 'bi-plus-circle',
    converted: 'bi-handshake', notified: 'bi-bell',
};

async function loadActivity(entityType, entityId, containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = '<div class="text-muted small">در حال بارگیری تاریخچه...</div>';
    try {
        const data = await apiCall(`/crm/activity/${entityType}/${entityId}`);
        if (!data.items.length) {
            el.innerHTML = '<div class="text-muted small">هنوز فعالیتی ثبت نشده است</div>';
            return;
        }
        el.innerHTML = `<div class="timeline">${data.items.map(a => `
            <div class="tl-item">
                <div class="tl-dot"><i class="bi ${ACTIVITY_ICONS[a.action] || 'bi-dot'}"></i></div>
                <div class="tl-body">
                    <div class="tl-text">${esc(a.detail)}</div>
                    <div class="tl-meta">
                        ${a.actor ? esc(a.actor) + ' · ' : ''}
                        ${a.created_at ? new Date(a.created_at).toLocaleString('fa-IR') : ''}
                    </div>
                </div>
            </div>`).join('')}</div>`;
    } catch (e) {
        el.innerHTML = '<div class="text-muted small">بارگیری تاریخچه ناموفق بود</div>';
    }
}

async function convertLeadToDeal(leadId) {
    if (!await askConfirm({ icon: 'bi-question-lg', title: 'تأیید', okLabel: 'تأیید', body: 'از این لید یک معامله ساخته شود؟' })) return;
    try {
        const r = await apiCall(`/crm/leads/${leadId}/convert-to-deal`, { method: 'POST' });
        showToast('موفق', `معامله #${r.deal.id} ساخته شد`, 'success');
        bootstrap.Modal.getInstance(document.getElementById('leadModal'))?.hide();
        document.querySelector('[data-bs-target="#crm-tab-deals"]')?.click();
        loadDeals();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

// ═══ تطابق‌سازی — similar properties & customer suggestions ═══════
/** Listings grouped the way a consultant thinks: this neighbourhood first,
 *  then the rest of the city. Only when the server says which is which. */
function _matchGroups(items, source) {
    if (!items.some(m => m.same_district !== undefined)) {
        return `<div class="match-list">${items.map(_matchCard).join('')}</div>`;
    }
    const here = items.filter(m => m.same_district), there = items.filter(m => !m.same_district);
    const where = source?.district ? esc(source.district) : 'همین منطقه';
    const city = source?.city_name ? esc(source.city_name) : '';
    let html = '';
    if (here.length) html += `<div class="match-group"><i class="bi bi-geo-alt-fill"></i> ${where} <span>${formatNumber(here.length)} مورد</span></div>
        <div class="match-list">${here.map(_matchCard).join('')}</div>`;
    if (there.length) html += `<div class="match-group"><i class="bi bi-geo"></i> مناطق دیگر${city ? ` ${city}` : ''} <span>${formatNumber(there.length)} مورد</span></div>
        <div class="match-list">${there.map(_matchCard).join('')}</div>`;
    return html;
}

/** The money on a match card. A rental is two numbers — the deposit alone
 *  read as «۱٫۵ میلیارد» next to a 150M base, when the base was 150M plus
 *  40M a month — so both halves are shown, and under them the one figure
 *  the comparison is made on. */
function _matchMoney(m) {
    if (m.listing_type !== 'rent') return m.price ? formatPrice(m.price) : '—';
    const dep = m.deposit ? formatPrice(m.deposit) : '—';
    const rent = m.rent_price ? formatPrice(m.rent_price) : 'بدون اجاره';
    const total = m.comparable ? `<div class="match-conv" title="ودیعه + ۳۰ × اجارهٔ ماهانه — عددی که مقایسه روی آن انجام می‌شود">رهن کامل ≈ ${formatPrice(m.comparable)}</div>` : '';
    return `<div class="match-rent"><span class="match-rent-l">ودیعه</span> ${dep}</div>
            <div class="match-rent"><span class="match-rent-l">اجاره</span> ${rent}</div>${total}`;
}

function _matchCard(m) {
    // the reverse direction returns people, not listings
    if (m.full_name !== undefined) return _customerMatchCard(m);
    const price = _matchMoney(m);
    const reasons = (m.reasons || []).slice(0, 3)
        .map(r => `<span class="match-tag">${esc(r)}</span>`).join('');
    const ai = m.ai_reason ? `<div class="match-ai"><i class="bi bi-stars"></i> ${esc(m.ai_reason)}</div>` : '';
    const scoreCls = m.score >= 75 ? 'high' : m.score >= 50 ? 'mid' : 'low';
    return `
    <div class="match-card">
        <div class="match-score ${scoreCls}">${formatNumber(m.score)}<small>٪</small></div>
        <div class="match-body">
            <div class="match-title" title="${esc(m.title)}">${esc(m.title)}</div>
            <div class="match-meta">
                ${m.serial_no != null ? `<span class="serial-badge">${formatSerial(m.serial_no)}</span>` : ''}
                ${m.city_name ? esc(m.city_name) : ''}${m.district ? ' · ' + esc(m.district) : ''}
                ${m.area ? ' · ' + formatNumber(m.area) + ' متر' : ''}
                ${m.rooms != null ? ' · ' + formatNumber(m.rooms) + ' خواب' : ''}
            </div>
            <div class="match-tags">${reasons}</div>
            ${ai}
        </div>
        <div class="match-side">
            <div class="match-price">${price}</div>
            ${m.price_gap_pct != null ? `<div class="match-gap ${m.price_gap_pct <= 15 ? 'near' : 'far'}" title="${m.listing_type === 'rent' ? 'نسبت به مبنا، روی رهن کامل (ودیعه + ۳۰ × اجاره)' : 'نسبت به قیمت مبنا'}">${m.price_gap_pct === 0 ? 'همین قیمت' : `${formatNumber(m.price_gap_pct)}٪ ${m.price_direction === 'higher' ? 'گران‌تر' : 'ارزان‌تر'}`}</div>` : ''}
            <button class="btn btn-sm btn-outline-primary" onclick="viewProperty(${m.id})">
                <i class="bi bi-eye"></i> جزئیات
            </button>
            ${m.phone_number ? `<a href="tel:${safeTel(m.phone_number)}" class="btn btn-sm btn-outline-success">
                <i class="bi bi-telephone"></i> ${esc(m.phone_number)}</a>` : ''}
        </div>
    </div>`;
}

const MATCH_TYPE_FA = { apartment: 'آپارتمان', house: 'ویلایی / خانه', land: 'زمین',
                        shop: 'مغازه', office: 'دفتر کار' };

/** The criteria the server actually filtered on — shown so a short or empty
 *  list is explainable rather than mysterious. */
function _matchCriteria(intent) {
    if (!intent) return '';
    const bits = [intent.listing_type === 'rent' ? 'رهن و اجاره' : 'خرید'];
    if (intent.family) bits.push(MATCH_TYPE_FA[intent.family] || esc(intent.family));
    if (intent.city) bits.push(esc(intent.city));
    return bits.join(' • ');
}

async function _openMatchModal(title, url, emptyMsg) {
    const modalEl = document.getElementById('matchModal');
    document.getElementById('match-modal-title').innerHTML = title;
    document.getElementById('match-modal-body').innerHTML =
        '<div class="text-center py-5 text-muted"><span class="spinner-border"></span><p class="mt-3">در حال یافتن بهترین موارد...</p></div>';
    new bootstrap.Modal(modalEl).show();
    try {
        const data = await apiCall(url);
        const items = data.items || [];
        const criteria = _matchCriteria(data.intent);
        if (!items.length) {
            // an empty list is almost always a criterion that is too narrow,
            // so show what was searched for instead of a bare "not found"
            document.getElementById('match-modal-body').innerHTML = `
                <div class="text-center py-5 text-muted">
                    <i class="bi bi-search" style="font-size:2rem"></i>
                    <p class="mt-3">${emptyMsg}</p>
                    ${criteria ? `<p class="small">جستجو بر اساس: ${criteria}<br>
                        اگر انتظار نتیجه داشتید، این معیارها را در پروندهٔ مشتری بازبینی کنید.</p>` : ''}
                </div>`;
            return;
        }
        const src = data.source?.title || data.source?.name || '';
        const s0 = data.source || {};
        const money = s0.listing_type === 'rent'
            ? (s0.deposit || s0.rent_price ? `ودیعه ${s0.deposit ? formatPrice(s0.deposit) : '—'} · اجاره ${s0.rent_price ? formatPrice(s0.rent_price) : 'ندارد'}${s0.comparable ? ` — رهن کامل ≈ ${formatPrice(s0.comparable)}` : ''}` : '')
            : (s0.price ? `قیمت ${formatPrice(s0.price)}` : '');
        document.getElementById('match-modal-body').innerHTML = `
            ${src ? `<div class="match-source">مبنای تطابق: <b>${esc(src)}</b> — ${formatNumber(items.length)} مورد یافت شد
                ${money ? `<div class="small mt-1 match-source-money">${money}${s0.listing_type === 'rent' ? ' <span class="text-muted">· اختلاف قیمت‌ها روی رهن کامل حساب می‌شود</span>' : ''}</div>` : ''}
                ${criteria ? `<div class="small mt-1">${criteria}</div>` : ''}</div>` : ''}
            ${_matchGroups(items, data.source)}`;
    } catch (e) {
        document.getElementById('match-modal-body').innerHTML =
            `<div class="alert alert-danger">خطا در تطابق‌سازی: ${esc(e.message)}</div>`;
    }
}

function showSimilarForLead(leadId) {
    _openMatchModal('<i class="bi bi-diagram-3"></i> ملک‌های مشابه',
        `/crm/match/lead/${leadId}?limit=12`,
        'ملک مشابهی پیدا نشد — با اسکرپ بیشتر، نتایج بهتر می‌شود.');
}

function showSimilarForProperty(propertyId) {
    _openMatchModal('<i class="bi bi-diagram-3"></i> ملک‌های مشابه',
        `/crm/match/property/${propertyId}?limit=12`,
        'ملک مشابهی پیدا نشد.');
}

function showMatchesForCustomer(customerId) {
    _openMatchModal('<i class="bi bi-magic"></i> ملک‌های پیشنهادی برای مشتری',
        `/crm/match/customer/${customerId}?limit=12`,
        'ملکی مطابق بودجه و درخواست این مشتری پیدا نشد.');
}

async function viewLead(id) {
    try {
        const lead = await apiCall(`/crm/leads/${id}`);
        const st = CRM_STATUS_LABELS[lead.status] || { label: lead.status, cls: 'bg-secondary' };

        document.getElementById('lead-detail-body').innerHTML = `
            <div class="row g-3">
                <div class="col-md-6">
                    <label class="text-muted small">عنوان ملک</label>
                    <div class="fw-bold">${esc(lead.property_title) || '---'} ${agencyBadge(lead)} ${typeof aiDuplicateBadge === 'function' ? aiDuplicateBadge(lead.property_detail || lead) : ''}</div>
                </div>
                <div class="col-md-6">
                    <label class="text-muted small">لینک</label>
                    <div class="d-flex gap-2 flex-wrap">
                        <a href="${safeUrl(lead.property_url)}" target="_blank" class="btn btn-sm btn-outline-primary">
                            <i class="bi bi-box-arrow-up-right"></i> مشاهده آگهی
                        </a>
                        <button class="btn btn-sm btn-match" onclick="showSimilarForLead(${lead.id})">
                            <i class="bi bi-diagram-3"></i> ملک‌های مشابه
                        </button>
                        <button class="btn btn-sm btn-outline-success" onclick="convertLeadToDeal(${lead.id})">
                            <i class="bi bi-handshake"></i> تبدیل به معامله
                        </button>
                        <button class="btn btn-sm btn-outline-info" onclick="scheduleVisitForLead(${lead.id})">
                            <i class="bi bi-calendar-plus"></i> ثبت بازدید
                        </button>
                        <button class="btn btn-sm btn-outline-warning" onclick="moveFilePick(${lead.property_id})" title="این فایل را در کمد و زونکن بگذارید">
                            <i class="bi bi-folder-symlink"></i> بایگانی در زونکن
                        </button>
                    </div>
                </div>
                <div class="col-md-4">
                    <label class="text-muted small">شماره تماس</label>
                    <div class="h5 text-success mb-0">
                        ${lead.phone_number
                            ? `<a href="tel:${safeTel(lead.phone_number)}">${esc(lead.phone_number)}</a>`
                            : '---'}
                    </div>
                </div>
                <div class="col-md-4">
                    <label class="text-muted small">فروشنده</label>
                    <div>${esc(lead.seller_name) || '---'}</div>
                </div>
                <div class="col-md-4">
                    <label class="text-muted small">شهر</label>
                    <div>${esc(lead.city_name) || '---'}</div>
                </div>
                <div class="col-md-4">
                    <label class="text-muted small">قیمت</label>
                    <div>${formatPrice(lead.price)}</div>
                </div>
                <div class="col-md-4">
                    <label class="text-muted small">متراژ</label>
                    <div>${lead.area ? formatNumber(lead.area) + ' متر' : '---'}</div>
                </div>
                <div class="col-md-4">
                    <label class="text-muted small">نوع</label>
                    <div>${lead.listing_type === 'buy' ? 'خرید' : lead.listing_type === 'rent' ? 'اجاره' : '---'}</div>
                </div>
                <div class="col-md-4">
                    <label class="text-muted small">اطلاع‌رسانی</label>
                    <div>
                        ${lead.notified
                            ? `<span class="badge bg-success">بله (${esc(lead.notification_channel)})</span>`
                            : '<span class="badge bg-secondary">خیر</span>'}
                    </div>
                </div>
                <div class="col-12">${_renderPropertyDetails(lead.property_detail)}</div>
                <div class="col-12">
                    <hr>
                    <h6 class="mb-2"><i class="bi bi-clock-history"></i> تاریخچه فعالیت</h6>
                    <div id="lead-activity"></div>
                </div>
                <hr>
                <div class="col-md-6">
                    <label class="form-label">وضعیت CRM</label>
                    <select id="lead-edit-status" class="form-select">
                        ${Object.entries(CRM_STATUS_LABELS).map(([val, info]) =>
                            `<option value="${val}" ${lead.status === val ? 'selected' : ''}>${info.label}</option>`
                        ).join('')}
                    </select>
                </div>
                <div class="col-md-6">
                    <label class="form-label">مسئول پیگیری</label>
                    <input type="text" id="lead-edit-assigned" class="form-control"
                           value="${esc(lead.assigned_to)}" placeholder="نام مسئول...">
                </div>
                <div class="col-md-6">
                    <label class="form-label">منطقه</label>
                    <input type="text" id="lead-edit-district" class="form-control"
                           value="${esc(lead.district || '')}" placeholder="مثلاً: خیابان کاشانی">
                </div>
                <div class="col-12">
                    <label class="form-label">یادداشت</label>
                    <textarea id="lead-edit-notes" class="form-control" rows="3"
                              placeholder="یادداشت...">${esc(lead.notes)}</textarea>
                </div>
            </div>
        `;

        document.getElementById('lead-save-btn').onclick = () => saveLead(id);
        loadActivity('lead', id, 'lead-activity');

        new bootstrap.Modal(document.getElementById('leadModal')).show();
    } catch (error) {
        showToast('خطا', 'بارگیری لید ناموفق بود', 'danger');
    }
}

async function saveLead(id) {
    const status = document.getElementById('lead-edit-status').value;
    const notes = document.getElementById('lead-edit-notes').value;
    const assigned_to = document.getElementById('lead-edit-assigned').value;
    const district = document.getElementById('lead-edit-district')?.value ?? '';

    try {
        await apiCall(`/crm/leads/${id}`, {
            method: 'PATCH',
            body: JSON.stringify({ status, notes, assigned_to, district })
        });
        showToast('موفق', 'لید بروزرسانی شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('leadModal')).hide();
        loadLeads();
        loadCrmStats();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function notifyLead(id) {
    try {
        showToast('در حال ارسال', 'اطلاع‌رسانی در حال انجام...', 'info');
        const result = await apiCall(`/crm/leads/${id}/notify`, { method: 'POST' });
        if (result.success) {
            showToast('موفق', `اطلاع‌رسانی از طریق ${result.channel} انجام شد`, 'success');
        } else {
            showToast('هشدار', 'کانال اطلاع‌رسانی تنظیم نشده', 'warning');
        }
        loadLeads();
        loadCrmStats();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function deleteLead(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'این لید و ملکِ متصل به آن از همه‌جا (لیست املاک، یادداشت‌ها و تصاویر) حذف می‌شوند. ادامه می‌دهید؟ این عمل قابل بازگشت نیست.' })) return;
    try {
        await apiCall(`/crm/leads/${id}`, { method: 'DELETE' });
        showToast('موفق', 'لید حذف شد', 'success');
        loadLeads();
        loadCrmStats();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

// ==================== DPA (فرم ارزیابی عملکرد روزانه) ====================

let _dpaEditId = null;
const DPA_ROLE_LABELS = { hunter: 'Hunter 🏹', closer: 'Closer 🤝' };

// must mirror DailyPerformance.ACTIVITIES on the backend
const DPA_ACTIVITIES = [
    { key: 'call',      points: 2,  label: 'تماس با مشتری',           auto: true },
    { key: 'showing',   points: 10, label: 'پرزنت / بازدید ملک',       auto: true },
    { key: 'new_file',  points: 15, label: 'ثبت فایل جدید',            auto: true },
    { key: 'meeting',   points: 20, label: 'نشست و تنظیم قرارداد',     auto: true },
    { key: 'exclusive', points: 30, label: 'ثبت فایل انحصاری',         auto: false },
    { key: 'offer',     points: 20, label: 'دریافت آفر کتبی و بیعانه', auto: false },
    { key: 'close',     points: 50, label: 'بستن قرارداد نهایی',       auto: false },
];

function _renderDpaActivities(autoCounts = {}, manualCounts = {}) {
    const tbody = document.getElementById('dpa-activities-body');
    if (!tbody) return;
    tbody.innerHTML = DPA_ACTIVITIES.map(a => {
        const auto = Number(autoCounts[a.key] || 0);
        return `
        <tr>
            <td>${a.label}</td>
            <td><span class="badge bg-primary">${formatNumber(a.points)}+</span></td>
            <td class="text-center">
                <span class="badge ${auto ? 'bg-success' : 'bg-secondary'}" id="dpa-auto-${a.key}" data-count="${auto}">${formatNumber(auto)}</span>
                ${a.auto ? '' : '<div class="small text-muted" style="font-size:.6rem">دستی</div>'}
            </td>
            <td>
                <input type="number" class="form-control form-control-sm dpa-act-manual" style="width:90px"
                       id="dpa-manual-${a.key}" data-key="${a.key}" value="${Number(manualCounts[a.key] || 0)}"
                       min="0" onchange="updateDpaScore()" oninput="updateDpaScore()">
            </td>
            <td class="fw-bold" id="dpa-total-${a.key}">۰</td>
            <td class="fw-bold text-info" id="dpa-pts-${a.key}">۰</td>
        </tr>`;
    }).join('');
}

function _dpaActivityScore() {
    let sum = 0;
    DPA_ACTIVITIES.forEach(a => {
        const auto = Number(document.getElementById(`dpa-auto-${a.key}`)?.dataset.count || 0);
        const manual = Math.max(Number(document.getElementById(`dpa-manual-${a.key}`)?.value) || 0, 0);
        const total = auto + manual, pts = total * a.points;
        const tEl = document.getElementById(`dpa-total-${a.key}`);
        const pEl = document.getElementById(`dpa-pts-${a.key}`);
        if (tEl) tEl.textContent = formatNumber(total);
        if (pEl) pEl.textContent = formatNumber(pts);
        sum += pts;
    });
    return sum;
}

function _dpaScoreParts() {
    let base = 0;
    document.querySelectorAll('.dpa-task:checked').forEach(el => { base += Number(el.dataset.weight); });
    const n = id => Math.max(Number(document.getElementById(id).value) || 0, 0);
    const activity = _dpaActivityScore();
    const bonus = n('dpa-bonus-exclusive') * 30 + n('dpa-bonus-offer') * 20 + n('dpa-bonus-close') * 50;
    const penalty = n('dpa-pen-crm') * 10 + n('dpa-pen-cancel') * 15 + n('dpa-pen-hotlead') * 20;
    return { base, activity, bonus, penalty, total: base + activity + bonus - penalty };
}

function updateDpaScore() {
    const s = _dpaScoreParts();
    document.getElementById('dpa-score-base').textContent = s.base;
    const actEl = document.getElementById('dpa-score-activity');
    if (actEl) actEl.textContent = `+${formatNumber(s.activity)}`;
    document.getElementById('dpa-score-bonus').textContent = `+${s.bonus}`;
    document.getElementById('dpa-score-penalty').textContent = `-${s.penalty}`;
    const totalEl = document.getElementById('dpa-score-total');
    totalEl.textContent = s.total;
    const target = Number(document.getElementById('dpa-target').value) || 100;
    totalEl.className = 'h3 mb-0 ' + (s.total >= target ? 'text-success' : 'text-primary');
}

/** The daily-performance list's filters, shared with its export. */
function _dpaQueryString() {
    const search = document.getElementById('dpa-search')?.value.trim() || '';
    return search ? `search=${encodeURIComponent(search)}` : '';
}

function exportDpaExcel() {
    const f = _dpaQueryString();
    _downloadExport(`${API_BASE}/crm/dpa/export/excel${f ? '?' + f : ''}`, 'dpa.xlsx');
}

async function loadDpa() {
    const f = _dpaQueryString();
    let url = `/crm/dpa?limit=100${f ? '&' + f : ''}`;

    try {
        const data = await apiCall(url);
        const tbody = document.getElementById('crm-dpa-table');
        tbody.innerHTML = '';

        const badge = document.getElementById('dpa-count-badge');
        if (badge) badge.textContent = data.total ?? data.items.length;

        if (data.items.length === 0) {
            tbody.innerHTML = `
                <tr><td colspan="11" class="text-center text-muted py-4">
                    <i class="bi bi-clipboard-data" style="font-size:2rem;"></i>
                    <p class="mt-2">هنوز فرمی ثبت نشده</p>
                </td></tr>`;
            return;
        }

        data.items.forEach(d => {
            const hitTarget = d.total_score >= (d.target_points || 100);
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${d.id}</td>
                <td>${esc(d.date_jalali) || '---'}</td>
                <td class="fw-bold">${esc(d.agent_name)}</td>
                <td>${DPA_ROLE_LABELS[d.role] || esc(d.role) || '---'}</td>
                <td>${d.base_score}</td>
                <td class="text-info">+${formatNumber(d.activity_score ?? 0)}</td>
                <td class="text-success">+${d.bonus_score}</td>
                <td class="text-danger">-${d.penalty_score}</td>
                <td><span class="badge ${hitTarget ? 'bg-success' : 'bg-warning'}">${d.total_score}</span></td>
                <td>${d.target_points || 100}</td>
                <td>
                    <button class="btn btn-sm btn-outline-primary" onclick="openDpaModal(${d.id})" title="ویرایش">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteDpa(${d.id})" title="حذف">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>`;
            tbody.appendChild(row);
        });
    } catch (error) {
        showToast('خطا', 'بارگیری فرم‌های ارزیابی ناموفق بود', 'danger');
    }
}

function _resetDpaForm() {
    document.getElementById('dpa-agent').value = _currentUser?.full_name || _currentUser?.username || '';
    document.getElementById('dpa-role').value = 'hunter';
    document.getElementById('dpa-date').value = gregorianToJalali(new Date());
    document.getElementById('dpa-target').value = 100;
    ['dpa-new-files', 'dpa-showings', 'dpa-offers', 'dpa-closed',
     'dpa-bonus-exclusive', 'dpa-bonus-offer', 'dpa-bonus-close',
     'dpa-pen-crm', 'dpa-pen-cancel', 'dpa-pen-hotlead']
        .forEach(id => { document.getElementById(id).value = 0; });
    document.querySelectorAll('.dpa-task').forEach(el => { el.checked = false; });
    document.getElementById('dpa-rca').value = '';
    document.getElementById('dpa-mentor').value = '';
    _renderDpaActivities();
}

async function openDpaModal(id = null) {
    _dpaEditId = id;
    _resetDpaForm();

    if (id) {
        try {
            const d = await apiCall(`/crm/dpa/${id}`);
            document.getElementById('dpa-agent').value = d.agent_name || '';
            document.getElementById('dpa-role').value = d.role || 'hunter';
            document.getElementById('dpa-date').value = d.date_jalali || '';
            document.getElementById('dpa-target').value = d.target_points ?? 100;
            document.getElementById('dpa-new-files').value = d.new_files ?? 0;
            document.getElementById('dpa-showings').value = d.showings_count ?? 0;
            document.getElementById('dpa-offers').value = d.offers_count ?? 0;
            document.getElementById('dpa-closed').value = d.closed_count ?? 0;
            document.getElementById('dpa-bonus-exclusive').value = d.bonus_exclusive ?? 0;
            document.getElementById('dpa-bonus-offer').value = d.bonus_offer ?? 0;
            document.getElementById('dpa-bonus-close').value = d.bonus_close ?? 0;
            document.getElementById('dpa-pen-crm').value = d.pen_crm_delay ?? 0;
            document.getElementById('dpa-pen-cancel').value = d.pen_cancel ?? 0;
            document.getElementById('dpa-pen-hotlead').value = d.pen_hot_lead ?? 0;
            document.querySelectorAll('.dpa-task').forEach(el => {
                el.checked = !!(d.base_tasks || {})[el.dataset.task];
            });
            _renderDpaActivities(d.auto_activities || {}, d.activities || {});
            document.getElementById('dpa-rca').value = d.rca || '';
            document.getElementById('dpa-mentor').value = d.mentor_feedback || '';
        } catch (e) {
            showToast('خطا', 'بارگیری فرم ناموفق بود', 'danger');
            return;
        }
    }
    updateDpaScore();
    new bootstrap.Modal(document.getElementById('dpaModal')).show();
}

async function saveDpa() {
    const agent_name = document.getElementById('dpa-agent').value.trim();
    if (!agent_name) { showToast('خطا', 'نام مشاور الزامی است', 'warning'); return; }

    const base_tasks = {};
    document.querySelectorAll('.dpa-task').forEach(el => { base_tasks[el.dataset.task] = el.checked; });
    const activities = {};
    document.querySelectorAll('.dpa-act-manual').forEach(el => {
        activities[el.dataset.key] = Math.max(Number(el.value) || 0, 0);
    });
    const n = id => Math.max(Number(document.getElementById(id).value) || 0, 0);

    const payload = {
        agent_name,
        role: document.getElementById('dpa-role').value,
        date_jalali: document.getElementById('dpa-date').value.trim() || null,
        target_points: n('dpa-target') || 100,
        new_files: n('dpa-new-files'),
        showings_count: n('dpa-showings'),
        offers_count: n('dpa-offers'),
        closed_count: n('dpa-closed'),
        base_tasks,
        activities,
        bonus_exclusive: n('dpa-bonus-exclusive'),
        bonus_offer: n('dpa-bonus-offer'),
        bonus_close: n('dpa-bonus-close'),
        pen_crm_delay: n('dpa-pen-crm'),
        pen_cancel: n('dpa-pen-cancel'),
        pen_hot_lead: n('dpa-pen-hotlead'),
        rca: document.getElementById('dpa-rca').value.trim() || null,
        mentor_feedback: document.getElementById('dpa-mentor').value.trim() || null,
    };

    const btn = document.getElementById('dpa-save-btn');
    btn.disabled = true;
    try {
        if (_dpaEditId) {
            await apiCall(`/crm/dpa/${_dpaEditId}`, { method: 'PUT', body: JSON.stringify(payload) });
        } else {
            await apiCall('/crm/dpa', { method: 'POST', body: JSON.stringify(payload) });
        }
        showToast('موفق', 'فرم ارزیابی ذخیره شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('dpaModal')).hide();
        loadDpa();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    } finally {
        btn.disabled = false;
    }
}

async function deleteDpa(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'این فرم ارزیابی حذف شود؟' })) return;
    try {
        await apiCall(`/crm/dpa/${id}`, { method: 'DELETE' });
        showToast('موفق', 'فرم حذف شد', 'success');
        loadDpa();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

// ==================== Customers (فرم پروفایل مشتری) ====================

const CUSTOMER_TEMP_LABELS = {
    hot:  { label: '🔥 داغ',  cls: 'bg-danger' },
    warm: { label: '🌤 گرم',  cls: 'bg-warning' },
    cold: { label: '❄️ سرد', cls: 'bg-info' },
};
const CUSTOMER_SOURCE_LABELS = { in_person: 'حضوری', divar: 'دیوار', referral: 'معرف', portal: 'پرتال' };
const SHOWING_STEP_LABELS = { meeting: 'نشست', archive: 'بایگانی', second_visit: 'بازدید دوم' };
let _customerEditId = null;

/** The customer list's filters as a query string. Shared with the Excel
 *  export so the file cannot stop matching the screen. */
function _customersQueryString() {
    const search = document.getElementById('customer-search')?.value.trim() || '';
    const temp   = document.getElementById('customer-filter-temp')?.value || '';
    const source = document.getElementById('customer-filter-source')?.value || '';
    const sort   = document.getElementById('customer-sort')?.value || 'newest';

    const parts = [];
    if (search) parts.push(`search=${encodeURIComponent(search)}`);
    if (temp)   parts.push(`temperature=${encodeURIComponent(temp)}`);
    if (source) parts.push(`source=${encodeURIComponent(source)}`);
    parts.push(`sort=${encodeURIComponent(sort)}`);
    return parts.join('&');
}

function exportCustomersExcel() {
    const filters = _customersQueryString();
    _downloadExport(
        `${API_BASE}/crm/customers/export/excel${filters ? '?' + filters : ''}`,
        'customers.xlsx');
}

async function loadCustomers() {
    let url = `/crm/customers?limit=100&${_customersQueryString()}`;

    try {
        const data = await apiCall(url);
        const tbody = document.getElementById('crm-customers-table');
        tbody.innerHTML = '';

        const badge = document.getElementById('customers-count-badge');
        if (badge) badge.textContent = data.total ?? data.items.length;

        if (data.items.length === 0) {
            tbody.innerHTML = `
                <tr><td colspan="10" class="text-center text-muted py-4">
                    <i class="bi bi-person-vcard" style="font-size:2rem;"></i>
                    <p class="mt-2">هیچ مشتری‌ای ثبت نشده</p>
                </td></tr>`;
            return;
        }

        data.items.forEach(c => {
            const t = CUSTOMER_TEMP_LABELS[c.temperature] || { label: esc(c.temperature) || '---', cls: 'bg-secondary' };
            const nextFollowup = (c.followups && c.followups.length)
                ? `${c.followups[0].date || ''} ${c.followups[0].time || ''}`.trim() || '---'
                : '---';
            // "جدید" badge for customers added within the last 3 days
            const isNew = c.created_at && (Date.now() - new Date(c.created_at).getTime()) < 3 * 86400000;
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${c.id}</td>
                <td class="fw-bold">${esc(c.full_name)}${isNew ? ' <span class="badge bg-success" style="font-size:.6rem;vertical-align:middle">جدید</span>' : ''}</td>
                <td>${c.mobile1 ? `<a href="tel:${safeTel(c.mobile1)}" class="text-success">${esc(c.mobile1)}</a>` : '---'}</td>
                <td><span class="badge ${t.cls}">${t.label}</span></td>
                <td>${CUSTOMER_SOURCE_LABELS[c.source] || '---'}</td>
                <td>${c.budget_max ? formatPrice(c.budget_max) : '---'}</td>
                <td>${esc(c.desired_district) || '---'}</td>
                <td>${esc(c.consultant_name) || '---'}</td>
                <td>${esc(nextFollowup)}</td>
                <td>
                    <button class="btn btn-sm btn-match" onclick="showMatchesForCustomer(${c.id})" title="ملک‌های پیشنهادی">
                        <i class="bi bi-magic"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-primary" onclick="openCustomerModal(${c.id})" title="ویرایش">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteCustomer(${c.id})" title="حذف">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>`;
            tbody.appendChild(row);
        });
    } catch (error) {
        showToast('خطا', 'بارگیری مشتریان ناموفق بود', 'danger');
    }
}

function _custRemoveRow(btn) { btn.closest('tr').remove(); }

function addShowingRow(data = {}) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
        <td><input type="text" class="form-control form-control-sm cust-sh-code" value="${esc(data.file_code || '')}" placeholder="SF-..."></td>
        <td><input type="text" class="form-control form-control-sm cust-sh-desc" value="${esc(data.description || '')}" placeholder="شرح ملک"></td>
        <td><input type="text" class="form-control form-control-sm cust-sh-feedback" value="${esc(data.feedback || '')}" placeholder="بازخورد"></td>
        <td>
            <select class="form-select form-select-sm cust-sh-step">
                <option value="">---</option>
                ${Object.entries(SHOWING_STEP_LABELS).map(([v, l]) =>
                    `<option value="${esc(v)}" ${data.next_step === v ? 'selected' : ''}>${esc(l)}</option>`).join('')}
            </select>
        </td>
        <td><button type="button" class="btn btn-sm btn-outline-danger" onclick="_custRemoveRow(this)"><i class="bi bi-x"></i></button></td>`;
    document.getElementById('cust-showings-body').appendChild(tr);
}

function addFollowupRow(data = {}) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
        <td><input type="text" class="form-control form-control-sm cust-fu-date" value="${esc(data.date || '')}" placeholder="۱۴۰۵/۰۵/۰۱"></td>
        <td><input type="text" class="form-control form-control-sm cust-fu-time" value="${esc(data.time || '')}" placeholder="۱۴:۳۰"></td>
        <td><input type="text" class="form-control form-control-sm cust-fu-action" value="${esc(data.action || '')}" placeholder="چه چیزی باید پیگیری یا ارائه شود؟"></td>
        <td><button type="button" class="btn btn-sm btn-outline-danger" onclick="_custRemoveRow(this)"><i class="bi bi-x"></i></button></td>`;
    document.getElementById('cust-followups-body').appendChild(tr);
}

const _CUST_PAY_MAP = { cash: 'cust-pay-cash', loan: 'cust-pay-loan', has_property: 'cust-pay-property', barter: 'cust-pay-barter' };

/** Suggest the cities we actually hold listings for — a typo here silently
 *  empties the customer's suggestion list, since it filters on exact match. */
async function _fillCustomerCityOptions() {
    const list = document.getElementById('cust-city-options');
    if (!list || list.dataset.filled) return;
    try {
        const resp = await apiCall('/scraper/cities');
        const cities = Array.isArray(resp) ? resp : (resp?.items || []);
        list.innerHTML = cities
            .map(c => `<option value="${esc(c.name || c)}"></option>`).join('');
        list.dataset.filled = '1';
    } catch (e) { /* free text still works without the suggestions */ }
}

function _resetCustomerForm() {
    ['cust-full-name', 'cust-mobile1', 'cust-mobile2', 'cust-consultant',
     'cust-budget', 'cust-specs', 'cust-district', 'cust-city',
     'cust-desired-type', 'cust-redlines', 'cust-notes']
        .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    document.getElementById('cust-source').value = 'in_person';
    document.getElementById('cust-temperature').value = 'warm';
    document.getElementById('cust-deal-type').value = 'buy';
    _fillCustomerCityOptions();
    Object.values(_CUST_PAY_MAP).forEach(id => { document.getElementById(id).checked = false; });
    document.getElementById('cust-showings-body').innerHTML = '';
    document.getElementById('cust-followups-body').innerHTML = '';
}

async function openCustomerModal(id = null) {
    _customerEditId = id;
    _resetCustomerForm();
    document.getElementById('customer-modal-title').innerHTML =
        `<i class="bi bi-person-vcard"></i> ${id ? 'ویرایش مشتری' : 'مشتری جدید'}`;

    if (id) {
        try {
            const c = await apiCall(`/crm/customers/${id}`);
            document.getElementById('cust-full-name').value = c.full_name || '';
            document.getElementById('cust-mobile1').value = c.mobile1 || '';
            document.getElementById('cust-mobile2').value = c.mobile2 || '';
            document.getElementById('cust-consultant').value = c.consultant_name || '';
            document.getElementById('cust-source').value = c.source || 'in_person';
            document.getElementById('cust-temperature').value = c.temperature || 'warm';
            document.getElementById('cust-budget').value = c.budget_max || '';
            document.getElementById('cust-specs').value = c.desired_specs || '';
            document.getElementById('cust-district').value = c.desired_district || '';
            document.getElementById('cust-city').value = c.desired_city || '';
            document.getElementById('cust-desired-type').value = c.desired_type || '';
            document.getElementById('cust-deal-type').value = c.deal_type || 'buy';
            document.getElementById('cust-redlines').value = c.red_lines || '';
            document.getElementById('cust-notes').value = c.notes || '';
            const methods = (c.payment_methods || '').split(',').map(s => s.trim());
            Object.entries(_CUST_PAY_MAP).forEach(([key, elId]) => {
                document.getElementById(elId).checked = methods.includes(key);
            });
            (c.showings || []).forEach(s => addShowingRow(s));
            (c.followups || []).forEach(f => addFollowupRow(f));
        } catch (e) {
            showToast('خطا', 'بارگیری مشتری ناموفق بود', 'danger');
            return;
        }
    } else {
        addShowingRow();
        addFollowupRow();
    }
    new bootstrap.Modal(document.getElementById('customerModal')).show();
}

function _collectCustomerPayload() {
    const showings = [...document.querySelectorAll('#cust-showings-body tr')].map(tr => ({
        file_code:   tr.querySelector('.cust-sh-code').value.trim(),
        description: tr.querySelector('.cust-sh-desc').value.trim(),
        feedback:    tr.querySelector('.cust-sh-feedback').value.trim(),
        next_step:   tr.querySelector('.cust-sh-step').value,
    }));
    const followups = [...document.querySelectorAll('#cust-followups-body tr')].map(tr => ({
        date:   tr.querySelector('.cust-fu-date').value.trim(),
        time:   tr.querySelector('.cust-fu-time').value.trim(),
        action: tr.querySelector('.cust-fu-action').value.trim(),
    }));
    const payment_methods = Object.entries(_CUST_PAY_MAP)
        .filter(([, elId]) => document.getElementById(elId).checked)
        .map(([key]) => key).join(',');

    return {
        full_name: document.getElementById('cust-full-name').value.trim(),
        mobile1: document.getElementById('cust-mobile1').value.trim() || null,
        mobile2: document.getElementById('cust-mobile2').value.trim() || null,
        source: document.getElementById('cust-source').value,
        temperature: document.getElementById('cust-temperature').value,
        consultant_name: document.getElementById('cust-consultant').value.trim() || null,
        budget_max: document.getElementById('cust-budget').value ? Number(document.getElementById('cust-budget').value) : null,
        payment_methods: payment_methods || null,
        desired_specs: document.getElementById('cust-specs').value.trim() || null,
        desired_district: document.getElementById('cust-district').value.trim() || null,
        desired_city: document.getElementById('cust-city').value.trim() || null,
        desired_type: document.getElementById('cust-desired-type').value || null,
        deal_type: document.getElementById('cust-deal-type').value || 'buy',
        red_lines: document.getElementById('cust-redlines').value.trim() || null,
        notes: document.getElementById('cust-notes').value.trim() || null,
        showings,
        followups,
    };
}

async function saveCustomer() {
    const payload = _collectCustomerPayload();
    if (!payload.full_name) { showToast('خطا', 'نام و نام خانوادگی الزامی است', 'warning'); return; }

    const btn = document.getElementById('customer-save-btn');
    btn.disabled = true;
    try {
        if (_customerEditId) {
            await apiCall(`/crm/customers/${_customerEditId}`, { method: 'PUT', body: JSON.stringify(payload) });
        } else {
            await apiCall('/crm/customers', { method: 'POST', body: JSON.stringify(payload) });
        }
        showToast('موفق', 'مشتری ذخیره شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('customerModal')).hide();
        loadCustomers();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    } finally {
        btn.disabled = false;
    }
}

async function deleteCustomer(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'این مشتری حذف شود؟ این عمل قابل بازگشت نیست.' })) return;
    try {
        await apiCall(`/crm/customers/${id}`, { method: 'DELETE' });
        showToast('موفق', 'مشتری حذف شد', 'success');
        loadCustomers();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

function onAddLeadTypeChange() {
    const isRent = document.getElementById('add-lead-listing-type').value === 'rent';
    document.getElementById('add-lead-price-buy').classList.toggle('d-none', isRent);
    document.querySelectorAll('.add-lead-rent-field').forEach(el =>
        el.classList.toggle('d-none', !isRent));
}

function openAddLeadModal() {
    ['title', 'city', 'category', 'price', 'deposit', 'rent', 'area', 'phone', 'seller', 'url', 'notes'].forEach(f => {
        const el = document.getElementById(`add-lead-${f}`);
        if (el) el.value = '';
    });
    document.getElementById('add-lead-listing-type').value = '';
    document.getElementById('add-lead-status').value = 'new';
    document.getElementById('add-lead-kind').value = '';
    renderLeadAttrs();
    _leadPhotos = [];
    _renderLeadPhotos();
    onAddLeadTypeChange();
    new bootstrap.Modal(document.getElementById('addLeadModal')).show();
}

async function submitAddLead() {
    const property_title = document.getElementById('add-lead-title').value.trim();
    if (!property_title) { showToast('خطا', 'عنوان ملک الزامی است', 'warning'); return; }

    const isRent = document.getElementById('add-lead-listing-type').value === 'rent';
    const numOrNull = id => {
        const v = document.getElementById(id).value;
        return v ? Number(v) : null;
    };
    const payload = {
        property_title,
        city_name: document.getElementById('add-lead-city').value.trim() || null,
        category_name: document.getElementById('add-lead-category').value.trim() || null,
        listing_type: document.getElementById('add-lead-listing-type').value || null,
        price: isRent ? null : numOrNull('add-lead-price'),
        deposit: isRent ? numOrNull('add-lead-deposit') : null,
        rent_price: isRent ? numOrNull('add-lead-rent') : null,
        area: document.getElementById('add-lead-area').value ? Number(document.getElementById('add-lead-area').value) : null,
        phone_number: document.getElementById('add-lead-phone').value.trim() || null,
        seller_name: document.getElementById('add-lead-seller').value.trim() || null,
        property_url: document.getElementById('add-lead-url').value.trim() || null,
        status: document.getElementById('add-lead-status').value || 'new',
        notes: document.getElementById('add-lead-notes').value.trim() || null,
        property_kind: document.getElementById('add-lead-kind').value || null,
        images: _leadPhotos,
        attrs: Object.fromEntries([...document.querySelectorAll('#add-lead-attrs .lead-attr')]
            .map(el => [el.dataset.key, el.value.trim()])
            .filter(([, v]) => v !== '')),
    };

    const btn = document.getElementById('add-lead-save-btn');
    btn.disabled = true;
    try {
        await apiCall('/crm/leads', { method: 'POST', body: JSON.stringify(payload) });
        showToast('موفق', 'لید جدید ثبت شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('addLeadModal')).hide();
        loadLeads();
        loadCrmStats();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    } finally {
        btn.disabled = false;
    }
}

// ==================== Users (super_admin) ====================

const ROLE_LABELS = {
    root:        { label: '🔧 Root',          cls: 'bg-dark' },
    super_admin: { label: '👑 مدیر ارشد',     cls: 'bg-danger' },
    admin:       { label: '🛠 مدیر',           cls: 'bg-primary' },
    visitor:     { label: '👤 بازدیدکننده',   cls: 'bg-secondary' },
    // 'user' no longer exists — the migration converted those accounts to
    // admin. Kept so a row written before the rollout still renders a label.
    user:        { label: '👤 کاربر (قدیمی)', cls: 'bg-secondary' },
};

let _usersById = {};

let _usersAll = [];

async function loadUsers() {
    try {
        const data = await apiCall('/users');
        _usersById = {};
        _usersAll = data.items || [];
        _usersAll.forEach(u => { _usersById[u.id] = u; });
        await loadPermCatalog();
        renderUsers();
    } catch (error) {
        showToast('خطا', 'بارگیری کاربران ناموفق بود', 'danger');
    }
}

function openNewUserModal() {
    syncNewUserPerms();
    bootstrap.Modal.getOrCreateInstance(document.getElementById('newUserModal')).show();
    setTimeout(() => document.getElementById('new-username')?.focus(), 300);
}

/** The members table: whatever the search and the two filters leave. */
function renderUsers() {
    const tbody = document.getElementById('users-table');
    if (!tbody) return;
    const q = (document.getElementById('users-search')?.value || '').trim().toLowerCase();
    const role = document.getElementById('users-role-filter')?.value || '';
    const state = document.getElementById('users-state-filter')?.value || '';
    const rows = _usersAll.filter(u =>
        (!role || u.role === role) &&
        (!state || (state === 'active') === !!u.is_active) &&
        (!q || [u.full_name, u.username, u.email, u.phone, u.divar_phone].some(v => (v || '').toLowerCase().includes(q))));
    const count = document.getElementById('users-count');
    if (count) count.textContent = formatNumber(rows.length) + (rows.length !== _usersAll.length ? ` از ${formatNumber(_usersAll.length)}` : '');
    document.getElementById('users-empty')?.classList.toggle('d-none', rows.length > 0);
    tbody.innerHTML = '';
    rows.forEach(u => tbody.appendChild(_userRow(u)));
}

function _userRow(u) {
    const rl = ROLE_LABELS[u.role] || { label: u.role, cls: 'bg-dark' };
    const roleText = String(rl.label).replace(/^[^\w؀-ۿ]+/, '').trim();   // the pill carries no emoji
    const lastLogin = u.last_login
        ? new Date(u.last_login).toLocaleDateString('fa-IR')
        : '—';
    const isSelf = u.username === _currentUser?.username;

    // Every field below is attacker-reachable: a visitor picks their own
    // full_name at public sign-up and it lands in this table the moment a
    // super_admin opens the page. Unescaped, an <img onerror> here runs in
    // the panel origin and the bearer token is in localStorage — so the
    // whole row goes through esc(), and the phone is read from
    // _usersById by id rather than interpolated into an onclick where a
    // single quote would break out of the attribute.
    const permLabels = (u.permissions || []).map(k => {
        const c = (_permCatalog || []).find(x => x.key === k);
        return c ? c.label : k;
    });
    const perms = u.role === 'admin'
        ? (permLabels.length
            ? `<button class="u-perms-pill" onclick="openPermsEditor(${esc(u.id)})" title="${esc(permLabels.join('، '))}">
                   ${formatNumber(permLabels.length)} بخش <i class="bi bi-chevron-down"></i></button>`
            : '<span class="u-muted">هیچ بخشی</span>')
        : (u.role === 'visitor' ? '<span class="u-muted">فقط پورتال</span>' : '<span class="u-muted">همهٔ بخش‌ها</span>');

    // Whether the number has actually been proven, said plainly.
    //
    // A code that arrives by email proves the address and nothing about
    // the phone, and until there is an SMS provider that is every code
    // we send. Someone about to ring this number needs to know which of
    // the two they are looking at, so each contact carries its own tick.
    //
    // root sees a switch instead of the dot: «فقط اکانت root می‌تواند به صورت
    // دستی و با تاگل، شماره و ایمیل کاربران را تأیید کند». Everybody else
    // still sees the dot — the server refuses the change for any other role.
    const canVerify = _currentUser?.role === 'root';
    const tick = (val, ok, okText, noText, kind) => !val ? '' :
        `<div class="u-line"><i class="bi ${kind === 'email' ? 'bi-envelope' : 'bi-telephone'}"></i>
           <span dir="ltr" class="u-val">${esc(val)}</span>
           ${canVerify
             ? `<span class="form-check form-switch m-0 u-verify" title="${ok ? okText : noText} — تأیید دستی (فقط root)">
                  <input class="form-check-input" type="checkbox" role="switch" ${ok ? 'checked' : ''}
                         onchange="setUserVerified(${Number(u.id)}, '${kind === 'email' ? 'email' : 'phone'}', this.checked, this)"
                         aria-label="${kind === 'email' ? 'تأیید ایمیل' : 'تأیید شماره'} ${esc(val)}">
                </span>`
             : `<i class="u-tick ${ok ? 'ok' : 'no'}" title="${ok ? okText : noText}"></i>`}
         </div>`;
    // Somebody with a stuck «!» can be asked to fix it from here — the
    // one screen where the person who notices is already looking.
    const unverified = (u.email && !u.email_verified) || (u.phone && !u.phone_verified);
    const nudge = unverified && !isSelf ? `
        <button class="btn btn-sm btn-link p-0 u-nudge" onclick="nudgeVerify(${esc(u.id)})"
                title="درخواست تأیید ایمیل / شماره از خود کاربر">
            <i class="bi bi-send-check"></i> درخواست تأیید
        </button>` : '';
    const headline = u.headline ? `<div class="u-headline">${esc(u.headline)}</div>` : '';
    // Issue #13. Both recovery paths — password reset and the email
    // second factor — refuse an account with no address, correctly:
    // enabling a factor an account cannot receive would lock it out.
    // But that leaves a super_admin with no address and no way back
    // except psql, and nothing on this screen said so. A privileged
    // account you cannot recover is a latent lockout; name it here,
    // where the person who can fix it is already looking.
    const privileged = ['root', 'super_admin', 'admin'].includes(u.role);
    const noRecovery = privileged && !(u.email || '').trim() && u.is_active;
    const contact =
        (tick(u.phone, u.phone_verified, 'شماره تأیید شده', 'شماره تأیید نشده — کدی با پیامک پاسخ داده نشده است', 'phone') +
         tick(u.email, u.email_verified, 'ایمیل تأیید شده', 'ایمیل تأیید نشده', 'email') +
         (u.divar_phone ? `<div class="u-line u-divar" title="شماره‌ای که با آن در دیوار وارد می‌شود">
             <i class="bi bi-phone"></i><span dir="ltr" class="u-val">${esc(u.divar_phone)}</span><span class="u-muted">دیوار</span></div>` : ''))
        || '<span class="u-muted">—</span>';
    const recoveryWarning = noRecovery ? `
        <div class="badge bg-danger mt-1 u-warn"
             title="بازنشانی رمز و تأیید دومرحله‌ای هر دو به ایمیل نیاز دارند. بدون آن، اگر رمز این حساب گم شود تنها راه برگشت پایگاه داده است.">
            <i class="bi bi-exclamation-triangle"></i> بدون ایمیل — قابل بازیابی نیست
        </div>` : '';

    // One menu instead of a row of coloured buttons. Root is never edited
    // from here; nobody switches off, re-keys or deletes their own account.
    const item = (fn, icon, label, cls = '') =>
        `<li><button class="dropdown-item ${cls}" onclick="${fn}"><i class="bi ${icon}"></i> ${label}</button></li>`;
    const menu = [
        u.role !== 'root' ? item(`openPermsEditor(${esc(u.id)})`, 'bi-sliders', 'نقش و دسترسی‌ها') : '',
        item(`promptSetDivarPhone(${esc(u.id)})`, 'bi-phone', 'شمارهٔ دیوار'),
        !isSelf && u.role !== 'root' ? item(`promptResetPassword(${esc(u.id)})`, 'bi-key', 'تغییر رمز') : '',
        !isSelf && u.role !== 'root' ? item(`toggleUserActive(${esc(u.id)}, ${!!u.is_active})`,
            u.is_active ? 'bi-pause-circle' : 'bi-play-circle', u.is_active ? 'غیرفعال کردن' : 'فعال کردن') : '',
        !isSelf && u.role !== 'root' ? '<li><hr class="dropdown-divider"></li>' +
            item(`deleteUser(${esc(u.id)})`, 'bi-trash', 'حذف حساب', 'text-danger') : '',
    ].join('');

    const row = document.createElement('tr');
    row.className = 'u-row' + (u.is_active ? '' : ' is-off');
    row.innerHTML = `
        <td class="u-who">
            <div class="u-id">
                ${avatarHtml(u, 36)}
                <div class="u-names">
                    <div class="u-name">${esc(u.full_name || u.username)}${isSelf ? '<span class="u-you">شما</span>' : ''}</div>
                    <div class="u-handle" dir="ltr">${esc(u.username)}</div>
                    ${headline}
                </div>
            </div>
        </td>
        <td data-l="نقش"><span class="pill role-${esc(u.role)}">${esc(roleText)}</span></td>
        <td data-l="دسترسی" class="u-perms">${perms}</td>
        <td data-l="تماس" class="u-contact">${contact}${nudge}${recoveryWarning}</td>
        <td data-l="وضعیت"><span class="u-state ${u.is_active ? 'on' : 'off'}"><i></i>${u.is_active ? 'فعال' : 'غیرفعال'}</span></td>
        <td data-l="آخرین ورود" class="u-login">${esc(lastLogin)}</td>
        <td class="u-menu">
            <div class="dropdown">
                <button class="u-kebab" data-bs-toggle="dropdown" aria-expanded="false" title="عملیات">
                    <i class="bi bi-three-dots-vertical"></i>
                </button>
                <ul class="dropdown-menu">${menu}</ul>
            </div>
        </td>`;
    return row;
}


// Change an existing account's role and, for an admin, exactly which areas it
// may open. This is the control the owner uses day to day — approving a ticket
// only sets the starting point.
async function openPermsEditor(userId) {
    const u = _usersById[userId];
    if (!u) return;
    await loadPermCatalog();

    const body = document.getElementById('perms-editor-body');
    body.innerHTML = `
        <p class="small text-muted mb-2">
            <strong>${esc(u.full_name || u.username)}</strong>
            <span dir="ltr">${esc(u.phone || '')}</span>
        </p>
        <label class="form-label small">نقش</label>
        <select id="pe-role" class="form-select form-select-sm mb-2" onchange="syncPermsEditor()">
            ${u.role === 'root' ? '<option value="root">Root — بالاترین سطح</option>' : ''}
            <option value="admin">مدیر — دسترسی‌های انتخابی</option>
            <option value="visitor">بازدیدکننده — فقط پورتال</option>
            <option value="super_admin">مدیر ارشد — دسترسی کامل</option>
        </select>
        ${u.role === 'root' ? `<div class="small text-warning mb-2">
            این حساب Root است. اگر نقش را عوض کنید، دیگر نمی‌توانید از این صفحه
            برش گردانید — فقط از طریق پایگاه داده.</div>` : ''}
        <div id="pe-perms-wrap">
            <label class="form-label small">دسترسی‌ها</label>
            <div id="pe-perms-box" class="perm-box"></div>
        </div>`;
    // Never silently pick a different role.
    //
    // This read `['admin','visitor','super_admin'].includes(u.role) ? u.role
    // : 'admin'`, and root is in none of those — so opening this editor on a
    // root account preselected «مدیر», and saving demoted it without anyone
    // choosing to. That is exactly how the owner's own root account became an
    // admin between one screenshot and the next, silently, with nothing in the
    // logs but a successful PATCH.
    //
    // Root now appears in the list when the account already has it, so the
    // preselection is the truth and saving is a no-op unless somebody
    // deliberately picks something else.
    document.getElementById('pe-role').value =
        ['root', 'admin', 'visitor', 'super_admin'].includes(u.role) ? u.role : 'admin';
    renderPermBox('pe-perms-box', u.permissions || [], 'pe');
    syncPermsEditor();
    document.getElementById('perms-editor-save').onclick = () => savePermsEditor(userId);
    new bootstrap.Modal(document.getElementById('permsEditorModal')).show();
}

function syncPermsEditor() {
    const role = document.getElementById('pe-role')?.value;
    const wrap = document.getElementById('pe-perms-wrap');
    if (wrap) wrap.style.display = role === 'admin' ? '' : 'none';
}

async function savePermsEditor(userId) {
    const role = document.getElementById('pe-role').value;
    try {
        await apiCall(`/users/${userId}`, {
            method: 'PATCH',
            body: JSON.stringify({
                role,
                permissions: role === 'admin' ? readPermBox('pe-perms-box') : [],
            }),
        });
        showToast('موفق', 'دسترسی‌ها بروزرسانی شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('permsEditorModal'))?.hide();
        loadUsers();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}


// Restored: these three were lost when loadUsers was rewritten. initApp binds
// createUser to the new-user form, so losing it threw during login and left
// showMainApp() short of showSection() — the panel kept whatever section the
// previous session was on.
async function createUser(e) {
    e.preventDefault();
    const username    = document.getElementById('new-username').value.trim();
    const full_name   = document.getElementById('new-fullname').value.trim();
    const email       = document.getElementById('new-email').value.trim();
    const password    = document.getElementById('new-password').value;
    const role        = document.getElementById('new-role').value;
    const divar_phone = document.getElementById('new-divar-phone').value.trim() || null;

    try {
        await apiCall('/users', {
            method: 'POST',
            body: JSON.stringify({ username, full_name, email, password, role, divar_phone,
                                   permissions: role === 'admin' ? readPermBox('new-perms-box') : [] })
        });
        showToast('موفق', 'کاربر ساخته شد', 'success');
        e.target.reset();
        bootstrap.Modal.getInstance(document.getElementById('newUserModal'))?.hide();
        loadUsers();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function toggleUserActive(id, currentlyActive) {
    try {
        await apiCall(`/users/${id}`, {
            method: 'PATCH',
            body: JSON.stringify({ is_active: !currentlyActive })
        });
        showToast('موفق', `کاربر ${currentlyActive ? 'غیرفعال' : 'فعال'} شد`, 'success');
        loadUsers();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function promptResetPassword(id) {
    const newPass = await askText({
        icon: 'bi-key', title: 'رمز عبور تازه', tone: 'warning',
        body: 'رمز تازه بلافاصله جایگزین می‌شود و کاربر باید با همین وارد شود.',
        field: { label: 'رمز عبور جدید', type: 'password', dir: 'ltr',
                 validate: v => v.length >= 6 ? '' : 'حداقل ۶ کاراکتر' },
    });
    if (!newPass || newPass.length < 6) {
        showToast('خطا', 'رمز عبور باید حداقل ۶ کاراکتر باشد', 'warning');
        return;
    }
    try {
        await apiCall(`/users/${id}/password`, {
            method: 'POST',
            body: JSON.stringify({ new_password: newPass })
        });
        showToast('موفق', 'رمز عبور تغییر کرد', 'success');
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function promptSetDivarPhone(id) {
    const currentPhone = (_usersById[id] || {}).divar_phone || '';
    const newPhone = await askText({
        icon: 'bi-person-badge', title: 'شمارهٔ دیوار کاربر',
        body: 'حساب دیواری که به این کاربر تعلق دارد.',
        note: 'برای پاک کردن، خالی بگذارید و ذخیره کنید.',
        field: { label: 'شمارهٔ دیوار', value: currentPhone || '',
                 placeholder: '09123456789', dir: 'ltr', inputmode: 'numeric' },
    });
    if (newPhone === null) return; // cancelled
    try {
        await apiCall(`/users/${id}`, {
            method: 'PATCH',
            body: JSON.stringify({ divar_phone: newPhone.trim() || null })
        });
        showToast('موفق', 'شماره دیوار بروزرسانی شد', 'success');
        loadUsers();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function deleteUser(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'آیا از حذف این کاربر اطمینان دارید؟' })) return;
    try {
        await apiCall(`/users/${id}`, { method: 'DELETE' });
        showToast('موفق', 'کاربر حذف شد', 'success');
        loadUsers();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function importProxies() {
    const proxyList = document.getElementById('proxy-import').value;

    if (!proxyList.trim()) {
        showToast('خطا', 'لطفاً لیست پراکسی‌ها را وارد کنید', 'warning');
        return;
    }

    try {
        const result = await apiCall('/proxies/import', {
            method: 'POST',
            body: JSON.stringify({ proxy_list: proxyList })
        });

        showToast('موفق', result.message, 'success');
        document.getElementById('proxy-import').value = '';
        loadProxies();

    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

// ═══════════════════════════════════════════════════════════════
//  CRM — TASKS
// ═══════════════════════════════════════════════════════════════

async function loadTasks() {
    const status = document.getElementById('task-filter-status')?.value || '';
    const priority = document.getElementById('task-filter-priority')?.value || '';
    let url = '/crm/tasks?limit=100';
    if (status) url += `&status=${status}`;
    if (priority) url += `&priority=${priority}`;
    try {
        const data = await apiCall(url);
        const tbody = document.getElementById('tasks-table');
        if (!tbody) return;
        if (!data.items?.length) { tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">وظیفه‌ای یافت نشد</td></tr>'; return; }
        tbody.innerHTML = data.items.map(t => {
            const p = TASK_PRIORITY_LABELS[t.priority] || { label: esc(t.priority), cls: 'bg-secondary' };
            const s = TASK_STATUS_LABELS[t.status] || { label: esc(t.status), cls: 'bg-secondary' };
            const due = t.due_date ? new Date(t.due_date).toLocaleDateString('fa-IR') : '—';
            return `<tr>
                <td>${esc(t.title)}</td>
                <td><span class="badge ${p.cls}">${p.label}</span></td>
                <td><span class="badge ${s.cls}">${s.label}</span></td>
                <td>${due}</td>
                <td>${t.assigned_to || '—'}</td>
                <td>
                    <button class="btn btn-xs btn-outline-primary" onclick="openTaskModal(${t.id})"><i class="bi bi-pencil"></i></button>
                    <button class="btn btn-xs btn-outline-danger" onclick="deleteTask(${t.id})"><i class="bi bi-trash"></i></button>
                    ${t.status !== 'done' ? `<button class="btn btn-xs btn-outline-success" onclick="markTaskDone(${t.id})"><i class="bi bi-check2"></i></button>` : ''}
                </td>
            </tr>`;
        }).join('');
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function openTaskModal(id = null) {
    document.getElementById('task-edit-id').value = id || '';
    document.getElementById('taskModalTitle').textContent = id ? 'ویرایش وظیفه' : 'وظیفه جدید';
    document.getElementById('task-title').value = '';
    document.getElementById('task-description').value = '';
    document.getElementById('task-priority').value = 'medium';
    document.getElementById('task-status').value = 'todo';
    document.getElementById('task-due-date').value = '';
    // a task belongs to whoever opens the form unless they reassign it — the
    // board only shows a non-super_admin their own rows
    document.getElementById('task-assigned').value =
        _currentUser?.full_name || _currentUser?.username || '';
    if (id) {
        try {
            const t = await apiCall(`/crm/tasks/${id}`);
            document.getElementById('task-title').value = t.title || '';
            document.getElementById('task-description').value = t.description || '';
            document.getElementById('task-priority').value = t.priority || 'medium';
            document.getElementById('task-status').value = t.status || 'todo';
            document.getElementById('task-due-date').value = t.due_date ? t.due_date.slice(0,16) : '';
            document.getElementById('task-assigned').value = t.assigned_to || '';
        } catch(e) { showToast('خطا', e.message, 'danger'); return; }
    }
    new bootstrap.Modal(document.getElementById('taskModal')).show();
}

async function saveTask() {
    const id = document.getElementById('task-edit-id').value;
    const payload = {
        title: document.getElementById('task-title').value.trim(),
        description: document.getElementById('task-description').value.trim() || null,
        priority: document.getElementById('task-priority').value,
        status: document.getElementById('task-status').value,
        due_date: document.getElementById('task-due-date').value || null,
        assigned_to: document.getElementById('task-assigned').value.trim() || null,
    };
    if (!payload.title) { showToast('خطا', 'عنوان الزامی است', 'warning'); return; }
    try {
        if (id) {
            await apiCall(`/crm/tasks/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
        } else {
            await apiCall('/crm/tasks', { method: 'POST', body: JSON.stringify(payload) });
        }
        showToast('موفق', 'وظیفه ذخیره شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('taskModal')).hide();
        loadTasks();
    } catch(e) { showToast('خطا', e.message, 'danger'); }
}

async function deleteTask(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'حذف شود؟' })) return;
    try { await apiCall(`/crm/tasks/${id}`, { method: 'DELETE' }); showToast('موفق', 'حذف شد', 'success'); loadTasks(); }
    catch(e) { showToast('خطا', e.message, 'danger'); }
}

async function markTaskDone(id) {
    try { await apiCall(`/crm/tasks/${id}`, { method: 'PUT', body: JSON.stringify({ status: 'done' }) }); loadTasks(); }
    catch(e) { showToast('خطا', e.message, 'danger'); }
}

// ═══════════════════════════════════════════════════════════════
//  CRM — CONTACTS
// ═══════════════════════════════════════════════════════════════

/** The phone book's filters as a query string, shared with its exports. */
function _contactsQueryString() {
    const search = document.getElementById('contact-search')?.value.trim() || '';
    const type = document.getElementById('contact-filter-type')?.value || '';
    const category = document.getElementById('contact-filter-category')?.value || '';
    const parts = [];
    if (search) parts.push(`search=${encodeURIComponent(search)}`);
    if (type) parts.push(`contact_type=${encodeURIComponent(type)}`);
    if (category) parts.push(`category=${encodeURIComponent(category)}`);
    return parts.join('&');
}

function exportContactsExcel() {
    const f = _contactsQueryString();
    _downloadExport(`${API_BASE}/crm/contacts/export/excel${f ? '?' + f : ''}`,
                    'contacts.xlsx');
}

async function loadContacts() {
    const f = _contactsQueryString();
    let url = `/crm/contacts?limit=200${f ? '&' + f : ''}`;
    try {
        const data = await apiCall(url);
        const tbody = document.getElementById('contacts-table');
        if (!tbody) return;
        if (!data.items?.length) { tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">مخاطبی یافت نشد</td></tr>'; return; }
        tbody.innerHTML = data.items.map(c => {
            const typeInfo = CONTACT_TYPE_LABELS[c.contact_type] || { label: esc(c.contact_type), cls: 'bg-secondary' };
            const catCls = c.category === 'VIP' ? 'bg-warning text-dark' : c.category === 'cold' ? 'bg-secondary' : 'bg-info text-white';
            const tags = _tagList(c.tags).map(t => `<span class="badge bg-dark me-1">${esc(t)}</span>`).join('');
            return `<tr>
                <td>${esc(c.name)}</td>
                <td>${esc(c.phone) || '—'}</td>
                <td><span class="badge ${typeInfo.cls}">${typeInfo.label}</span></td>
                <td><span class="badge ${catCls}">${esc(c.category) || 'عادی'}</span></td>
                <td>${esc(c.city) || '—'}</td>
                <td>${tags || '—'}</td>
                <td>
                    <button class="btn btn-xs btn-outline-primary" onclick="openContactModal(${c.id})"><i class="bi bi-pencil"></i></button>
                    <button class="btn btn-xs btn-outline-danger" onclick="deleteContact(${c.id})"><i class="bi bi-trash"></i></button>
                    <button class="btn btn-xs btn-outline-info" onclick="quickSmsToContact(${jsArg(c.phone || '')})"><i class="bi bi-chat-dots"></i></button>
                </td>
            </tr>`;
        }).join('');
    } catch(e) { showToast('خطا', e.message, 'danger'); }
}

/** Drive the contact city picker. A city typed by hand before this form used
 *  a picker will not be in the list, so it is shown as-is rather than dropped. */
function _setContactCity(value) {
    const picker = document.getElementById('contact-city-picker');
    const hidden = document.getElementById('contact-city');
    if (hidden) hidden.value = value || '';
    if (!picker) return;
    if (value && picker._setCityValue) picker._setCityValue(value);
    const label = picker.querySelector('.city-picker__label');
    if (label && (!value || label.textContent !== value)) {
        label.textContent = value || 'انتخاب شهر...';
    }
}

async function openContactModal(id = null) {
    document.getElementById('contact-edit-id').value = id || '';
    document.getElementById('contactModalTitle').textContent = id ? 'ویرایش مخاطب' : 'مخاطب جدید';
    ['name','phone','phone2','email','city','address','tags','notes'].forEach(f => document.getElementById(`contact-${f}`).value = '');
    document.getElementById('contact-type').value = 'owner';
    document.getElementById('contact-category').value = 'normal';
    // the city is a picker now: its hidden input was cleared above, but the
    // visible label has to be reset too or it keeps the last contact's city
    _setContactCity('');
    if (id) {
        try {
            const c = await apiCall(`/crm/contacts/${id}`);
            document.getElementById('contact-name').value = c.name || '';
            document.getElementById('contact-phone').value = c.phone || '';
            document.getElementById('contact-phone2').value = c.phone2 || '';
            document.getElementById('contact-email').value = c.email || '';
            document.getElementById('contact-type').value = c.contact_type || 'owner';
            document.getElementById('contact-category').value = c.category || 'normal';
            _setContactCity(c.city || '');
            document.getElementById('contact-address').value = c.address || '';
            document.getElementById('contact-tags').value = _tagList(c.tags).join(', ');
            document.getElementById('contact-notes').value = c.notes || '';
        } catch(e) { showToast('خطا', e.message, 'danger'); return; }
    }
    new bootstrap.Modal(document.getElementById('contactModal')).show();
}

async function saveContact() {
    const id = document.getElementById('contact-edit-id').value;
    const tagsRaw = document.getElementById('contact-tags').value;
    const payload = {
        name: document.getElementById('contact-name').value.trim(),
        phone: document.getElementById('contact-phone').value.trim() || null,
        phone2: document.getElementById('contact-phone2').value.trim() || null,
        email: document.getElementById('contact-email').value.trim() || null,
        contact_type: document.getElementById('contact-type').value,
        category: document.getElementById('contact-category').value,
        city: document.getElementById('contact-city').value.trim() || null,
        address: document.getElementById('contact-address').value.trim() || null,
        tags: tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(Boolean) : [],
        notes: document.getElementById('contact-notes').value.trim() || null,
    };
    if (!payload.name) { showToast('خطا', 'نام الزامی است', 'warning'); return; }
    try {
        if (id) {
            await apiCall(`/crm/contacts/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
        } else {
            await apiCall('/crm/contacts', { method: 'POST', body: JSON.stringify(payload) });
        }
        showToast('موفق', 'مخاطب ذخیره شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('contactModal')).hide();
        loadContacts();
    } catch(e) { showToast('خطا', e.message, 'danger'); }
}

async function deleteContact(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'حذف شود؟' })) return;
    try { await apiCall(`/crm/contacts/${id}`, { method: 'DELETE' }); showToast('موفق', 'حذف شد', 'success'); loadContacts(); }
    catch(e) { showToast('خطا', e.message, 'danger'); }
}

function quickSmsToContact(phone) {
    if (!phone) return;
    document.getElementById('sms-to').value = phone;
    const smsTab = document.querySelector('[data-bs-target="#crm-tab-sms"]');
    if (smsTab) bootstrap.Tab.getOrCreateInstance(smsTab).show();
}

// ═══════════════════════════════════════════════════════════════
//  CRM — DEALS
// ═══════════════════════════════════════════════════════════════

/** The deals list's filters as a query string, shared with its export. */
function _dealsQueryString() {
    const status = document.getElementById('deal-filter-status')?.value || '';
    return status ? `status=${encodeURIComponent(status)}` : '';
}

function exportDealsExcel() {
    const f = _dealsQueryString();
    _downloadExport(`${API_BASE}/crm/deals/export/excel${f ? '?' + f : ''}`,
                    'deals.xlsx');
}

async function loadDeals() {
    const f = _dealsQueryString();
    let url = `/crm/deals?limit=100${f ? '&' + f : ''}`;
    try {
        const data = await apiCall(url);
        const tbody = document.getElementById('deals-table');
        if (!tbody) return;
        if (!data.items?.length) { tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">معامله‌ای یافت نشد</td></tr>'; return; }
        tbody.innerHTML = data.items.map(d => {
            const s = DEAL_STATUS_LABELS[d.status] || { label: esc(d.status), cls: 'bg-secondary' };
            const dealTypeLabel = { buy: 'خرید', rent: 'اجاره', lease: 'رهن' }[d.deal_type] || esc(d.deal_type);
            const amount = d.amount ? formatNumber(d.amount) + ' ت' : '—';
            const date = d.contract_date ? new Date(d.contract_date).toLocaleDateString('fa-IR') : '—';
            return `<tr>
                <td>${esc(d.title)}</td>
                <td>${dealTypeLabel}</td>
                <td><span class="badge ${s.cls}">${s.label}</span></td>
                <td>${amount}</td>
                <td>${d.buyer_name ? esc(d.buyer_name) : (d.buyer_contact_id ? `#${d.buyer_contact_id}` : '—')}</td>
                <td>${d.seller_name ? esc(d.seller_name) : (d.seller_contact_id ? `#${d.seller_contact_id}` : '—')}</td>
                <td>${date}</td>
                <td>
                    <button class="btn btn-xs btn-outline-primary" onclick="openDealModal(${d.id})"><i class="bi bi-pencil"></i></button>
                    <button class="btn btn-xs btn-outline-danger" onclick="deleteDeal(${d.id})"><i class="bi bi-trash"></i></button>
                </td>
            </tr>`;
        }).join('');
    } catch(e) { showToast('خطا', e.message, 'danger'); }
}

async function openDealModal(id = null) {
    document.getElementById('deal-edit-id').value = id || '';
    document.getElementById('dealModalTitle').textContent = id ? 'ویرایش معامله' : 'معامله جدید';
    ['title','amount','commission','buyer-id','seller-id','notes'].forEach(f => document.getElementById(`deal-${f}`).value = '');
    document.getElementById('deal-type').value = 'buy';
    document.getElementById('deal-status').value = 'new';
    document.getElementById('deal-commission-paid').value = 'false';
    document.getElementById('deal-contract-date').value = '';
    if (id) {
        try {
            const d = await apiCall(`/crm/deals/${id}`);
            document.getElementById('deal-title').value = d.title || '';
            document.getElementById('deal-type').value = d.deal_type || 'buy';
            document.getElementById('deal-status').value = d.status || 'new';
            document.getElementById('deal-amount').value = d.amount || '';
            document.getElementById('deal-commission').value = d.commission || '';
            document.getElementById('deal-commission-paid').value = d.commission_paid ? 'true' : 'false';
            document.getElementById('deal-contract-date').value = d.contract_date ? d.contract_date.slice(0,10) : '';
            document.getElementById('deal-buyer-id').value = d.buyer_contact_id || '';
            document.getElementById('deal-seller-id').value = d.seller_contact_id || '';
            document.getElementById('deal-notes').value = d.notes || '';
        } catch(e) { showToast('خطا', e.message, 'danger'); return; }
    }
    new bootstrap.Modal(document.getElementById('dealModal')).show();
}

async function saveDeal() {
    const id = document.getElementById('deal-edit-id').value;
    const payload = {
        title: document.getElementById('deal-title').value.trim(),
        deal_type: document.getElementById('deal-type').value,
        status: document.getElementById('deal-status').value,
        amount: parseFloat(document.getElementById('deal-amount').value) || null,
        commission: parseFloat(document.getElementById('deal-commission').value) || null,
        commission_paid: document.getElementById('deal-commission-paid').value === 'true',
        contract_date: document.getElementById('deal-contract-date').value || null,
        buyer_contact_id: parseInt(document.getElementById('deal-buyer-id').value) || null,
        seller_contact_id: parseInt(document.getElementById('deal-seller-id').value) || null,
        notes: document.getElementById('deal-notes').value.trim() || null,
    };
    if (!payload.title) { showToast('خطا', 'عنوان الزامی است', 'warning'); return; }
    try {
        if (id) {
            await apiCall(`/crm/deals/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
        } else {
            await apiCall('/crm/deals', { method: 'POST', body: JSON.stringify(payload) });
        }
        showToast('موفق', 'معامله ذخیره شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('dealModal')).hide();
        loadDeals();
    } catch(e) { showToast('خطا', e.message, 'danger'); }
}

async function deleteDeal(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'حذف شود؟' })) return;
    try { await apiCall(`/crm/deals/${id}`, { method: 'DELETE' }); showToast('موفق', 'حذف شد', 'success'); loadDeals(); }
    catch(e) { showToast('خطا', e.message, 'danger'); }
}

// ═══════════════════════════════════════════════════════════════
//  CRM — NOTES
// ═══════════════════════════════════════════════════════════════

async function loadNotes() {
    try {
        const data = await apiCall('/crm/notes?limit=100');
        const el = document.getElementById('notes-list');
        if (!el) return;
        if (!data.items?.length) { el.innerHTML = '<p class="text-muted text-center py-3">یادداشتی یافت نشد</p>'; return; }
        el.innerHTML = data.items.map(n => {
            const date = n.created_at ? new Date(n.created_at).toLocaleString('fa-IR') : '';
            return `<div class="note-card mb-2 p-3 rounded" style="background:var(--bg-secondary);border-right:3px solid var(--accent);">
                <div class="d-flex justify-content-between align-items-start">
                    <p class="mb-1" style="white-space:pre-wrap;">${esc(n.content)}</p>
                    <button class="btn btn-xs btn-outline-danger ms-2" onclick="deleteNote(${n.id})"><i class="bi bi-trash"></i></button>
                </div>
                <small class="text-muted">${date}${n.created_by ? ' — ' + n.created_by : ''}</small>
            </div>`;
        }).join('');
    } catch(e) { showToast('خطا', e.message, 'danger'); }
}

function openNoteModal() {
    document.getElementById('note-content').value = '';
    document.getElementById('note-contact-id').value = '';
    document.getElementById('note-deal-id').value = '';
    document.getElementById('note-property-id').value = '';
    new bootstrap.Modal(document.getElementById('noteModal')).show();
}

async function saveNote() {
    const content = document.getElementById('note-content').value.trim();
    if (!content) { showToast('خطا', 'متن یادداشت الزامی است', 'warning'); return; }
    const payload = {
        content,
        contact_id: parseInt(document.getElementById('note-contact-id').value) || null,
        deal_id: parseInt(document.getElementById('note-deal-id').value) || null,
        property_id: parseInt(document.getElementById('note-property-id').value) || null,
    };
    try {
        await apiCall('/crm/notes', { method: 'POST', body: JSON.stringify(payload) });
        showToast('موفق', 'یادداشت ذخیره شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('noteModal')).hide();
        loadNotes();
    } catch(e) { showToast('خطا', e.message, 'danger'); }
}

async function deleteNote(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'حذف شود؟' })) return;
    try { await apiCall(`/crm/notes/${id}`, { method: 'DELETE' }); showToast('موفق', 'حذف شد', 'success'); loadNotes(); }
    catch(e) { showToast('خطا', e.message, 'danger'); }
}

// ═══════════════════════════════════════════════════════════════
//  CRM — REMINDERS
// ═══════════════════════════════════════════════════════════════

async function loadReminders() {
    const isSent = document.getElementById('reminder-filter-sent')?.value;
    let url = '/crm/reminders?limit=100';
    if (isSent !== '') url += `&is_sent=${isSent}`;
    try {
        const data = await apiCall(url);
        const tbody = document.getElementById('reminders-table');
        if (!tbody) return;
        if (!data.items?.length) { tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">یادآوری یافت نشد</td></tr>'; return; }
        tbody.innerHTML = data.items.map(r => {
            const dt = r.remind_at ? new Date(r.remind_at).toLocaleString('fa-IR') : '—';
            const channelLabel = r.channel === 'sms' ? '<span class="badge bg-primary">پیامک</span>' : '<span class="badge bg-secondary">در برنامه</span>';
            const repeatLabel = { none: 'بدون تکرار', daily: 'روزانه', weekly: 'هفتگی', monthly: 'ماهانه' }[r.repeat] || r.repeat;
            const statusBadge = r.is_sent ? '<span class="badge bg-success">ارسال شده</span>' : '<span class="badge bg-warning text-dark">فعال</span>';
            return `<tr>
                <td>${esc(r.title)}</td>
                <td>${dt}</td>
                <td>${channelLabel}</td>
                <td>${repeatLabel}</td>
                <td>${statusBadge}</td>
                <td>
                    <button class="btn btn-xs btn-outline-danger" onclick="deleteReminder(${r.id})"><i class="bi bi-trash"></i></button>
                </td>
            </tr>`;
        }).join('');
    } catch(e) { showToast('خطا', e.message, 'danger'); }
}

function openReminderModal() {
    document.getElementById('reminder-title').value = '';
    document.getElementById('reminder-at').value = '';
    document.getElementById('reminder-repeat').value = 'none';
    document.getElementById('reminder-channel').value = 'in_app';
    document.getElementById('reminder-sms-to').value = '';
    document.getElementById('reminder-contact-id').value = '';
    document.getElementById('reminder-sms-to-group').style.display = 'none';
    new bootstrap.Modal(document.getElementById('reminderModal')).show();
}

function toggleSmsTo() {
    const ch = document.getElementById('reminder-channel').value;
    document.getElementById('reminder-sms-to-group').style.display = ch === 'sms' ? '' : 'none';
}

async function saveReminder() {
    const title = document.getElementById('reminder-title').value.trim();
    const remindAt = document.getElementById('reminder-at').value;
    if (!title || !remindAt) { showToast('خطا', 'عنوان و زمان الزامی است', 'warning'); return; }
    const payload = {
        title,
        remind_at: new Date(remindAt).toISOString(),
        repeat: document.getElementById('reminder-repeat').value,
        channel: document.getElementById('reminder-channel').value,
        sms_to: document.getElementById('reminder-sms-to').value.trim() || null,
        contact_id: parseInt(document.getElementById('reminder-contact-id').value) || null,
    };
    try {
        await apiCall('/crm/reminders', { method: 'POST', body: JSON.stringify(payload) });
        showToast('موفق', 'یادآور ذخیره شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('reminderModal')).hide();
        loadReminders();
    } catch(e) { showToast('خطا', e.message, 'danger'); }
}

async function deleteReminder(id) {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'حذف شود؟' })) return;
    try { await apiCall(`/crm/reminders/${id}`, { method: 'DELETE' }); showToast('موفق', 'حذف شد', 'success'); loadReminders(); }
    catch(e) { showToast('خطا', e.message, 'danger'); }
}

// ═══════════════════════════════════════════════════════════════
//  CRM — SMS
// ═══════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    const msgEl = document.getElementById('sms-message');
    const countEl = document.getElementById('sms-char-count');
    if (msgEl && countEl) {
        msgEl.addEventListener('input', () => {
            countEl.textContent = `${msgEl.value.length} کاراکتر`;
        });
    }
});

document.addEventListener('DOMContentLoaded', () => {
    if (typeof $ !== 'undefined' && $.fn.persianDatepicker) {
        _initLeadsDatePickers();
    } else {
        // jQuery or persian-datepicker not yet loaded — retry after scripts settle
        window.addEventListener('load', _initLeadsDatePickers);
    }
});

async function sendSms() {
    const to = document.getElementById('sms-to').value.trim();
    const message = document.getElementById('sms-message').value.trim();
    const provider = document.getElementById('sms-provider').value;
    if (!to || !message) { showToast('خطا', 'شماره و متن الزامی است', 'warning'); return; }
    try {
        const result = await apiCall('/crm/sms/send', {
            method: 'POST',
            body: JSON.stringify({ to_number: to, message, provider })
        });
        if (result.success) {
            showToast('موفق', 'پیامک ارسال شد', 'success');
            document.getElementById('sms-message').value = '';
            loadSmsLogs();
        } else {
            showToast('خطا', result.response || result.error || 'خطا در ارسال', 'danger');
        }
    } catch(e) { showToast('خطا', e.message, 'danger'); }
}

async function loadSmsLogs() {
    try {
        const data = await apiCall('/crm/sms/logs?limit=50');
        const tbody = document.getElementById('sms-logs-table');
        if (!tbody) return;
        if (!data.items?.length) { tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">تاریخچه‌ای وجود ندارد</td></tr>'; return; }
        tbody.innerHTML = data.items.map(s => {
            const providerLabel = s.provider === 'kavenegar' ? 'کاوه‌نگار' : 'ملی پیامک';
            const statusCls = s.status === 'sent' ? 'bg-success' : 'bg-danger';
            const statusLabel = s.status === 'sent' ? 'ارسال شد' : 'خطا';
            const dt = s.sent_at ? new Date(s.sent_at).toLocaleString('fa-IR') : '—';
            const msg = s.message?.length > 50 ? s.message.slice(0, 50) + '…' : (s.message || '—');
            return `<tr>
                <td>${esc(s.to_number)}</td>
                <td>${providerLabel}</td>
                <td title="${esc(s.message || '')}">${esc(msg)}</td>
                <td><span class="badge ${statusCls}">${statusLabel}</span></td>
                <td>${dt}</td>
            </tr>`;
        }).join('');
    } catch(e) { console.error('SMS logs error:', e); }
}

// ═══════════════════════════════════════════════════════════════
//  CRM — Export (Excel for all / JSON for super_admin only)
// ═══════════════════════════════════════════════════════════════

async function _downloadExport(url, filename) {
    const token = getToken();
    try {
        const resp = await fetch(url, {
            headers: token ? { 'Authorization': `Bearer ${token}` } : {}
        });
        if (resp.status === 403) { showToast('خطا', 'دسترسی ندارید', 'danger'); return; }
        if (!resp.ok) { showToast('خطا', 'خروجی با خطا مواجه شد', 'danger'); return; }
        const blob = await resp.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        a.click();
        URL.revokeObjectURL(a.href);
    } catch(e) { showToast('خطا', e.message, 'danger'); }
}

function exportExcel(type) {
    _downloadExport(`${API_BASE}/crm/${type}/export/excel`, `${type}.xlsx`);
}

// ── Excel exports that mirror the current filters of each list ──
function exportPropertiesExcel() {
    const params = new URLSearchParams();
    const city = document.getElementById('filter-city-hidden')?.value || '';
    const category = document.getElementById('filter-category')?.value || '';
    const search = document.getElementById('search-properties')?.value.trim() || '';
    const type = _selectedCategoryType();
    if (city) params.set('city', city);
    if (category) params.set('category', category);
    if (search) params.set('search', search);
    if (type === 'buy' || type === 'rent') params.set('listing_type', type);
    // The two rental bands were on the screen and not in the file, so a list
    // narrowed by ودیعه or اجاره exported everything.
    for (const [id, param] of [['filter-min-deposit', 'min_deposit'],
                               ['filter-max-deposit', 'max_deposit'],
                               ['filter-min-rent', 'min_rent_price'],
                               ['filter-max-rent', 'max_rent_price']]) {
        const v = document.getElementById(id)?.value;
        if (v) params.set(param, v);
    }
    _downloadExport(`${API_BASE}/properties/export/excel?${params}`, 'properties.xlsx');
}

function exportCalendarExcel() {
    const { start, end } = _calRange();
    // The type dropdown narrows the calendar; the file has to be narrowed the
    // same way or it silently contains appointments the screen was hiding.
    const type = _calType ? `&event_type=${encodeURIComponent(_calType)}` : '';
    _downloadExport(
        `${API_BASE}/crm/calendar/export/excel?date_from=${_isoLocal(start)}&date_to=${_isoLocal(end)}${type}`,
        'calendar.xlsx');
}

function exportLeadsExcel() {
    // the export takes the same filters as the list, so the file matches the
    // screen — reuse the very query string the list was built with
    const filters = _leadsQueryString();
    _downloadExport(`${API_BASE}/crm/leads/export/excel${filters ? '?' + filters : ''}`, 'leads.xlsx');
}

function exportJson(type) {
    _downloadExport(`${API_BASE}/crm/${type}/export/json`, `${type}.json`);
}

// ═══ Call queue — the day's list, one tap per outcome ══════════
let _cqItems = [];

function _cqWhen(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return `${d.toLocaleDateString('fa-IR')} ${d.toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' })}`;
}

async function loadCalls() {
    const box = document.getElementById('calls-list');
    if (!box) return;
    try {
        const d = await apiCall('/crm/calls/today?limit=40');
        _cqItems = d.items || [];
        document.getElementById('calls-count').textContent = formatNumber(d.total || 0);
        document.getElementById('calls-stats').textContent =
            `${formatNumber(d.done_today || 0)} تماس امروز · ${formatNumber(d.due_callbacks || 0)} تماس مجدد سررسیده`;
        const badge = document.getElementById('calls-due-badge');
        if (badge) { badge.textContent = formatNumber(d.total || 0); badge.classList.toggle('d-none', !d.total); }
        if (!_cqItems.length) {
            box.innerHTML = `<div class="cq-empty"><i class="bi bi-cup-hot"></i> فعلاً کسی منتظر تماس نیست.
                ${d.total ? '' : 'اسکرپ بعدی که تمام شود، لیدهای تازه اینجا می‌آیند.'}</div>`;
        } else {
            box.innerHTML = _cqItems.map(_cqCard).join('');
        }
        if (['root', 'super_admin'].includes(_currentUser?.role)) loadCallsSummary();
    } catch (e) {
        box.innerHTML = `<div class="text-danger small">${esc(e.message || 'خطا')}</div>`;
    }
}

// Seven buttons on a call card, five on a match, four on a price drop — and
// six cards to a screen meant forty-two buttons competing for one glance. The
// two that carry almost every card stay out; the rest go behind a disclosure.
// <details> rather than a class and a listener: the browser already has this
// widget, keyboard and screen-reader behaviour included.
function _cqMore(html) {
    return `<details class="cq-more"><summary>بیشتر</summary>
        <div class="cq-more-row">${html}</div>
    </details>`;
}

function _cqCard(l) {
    const price = l.price ? formatNumber(l.price) + ' تومان' : '';
    const meta = [l.city_name, l.category_name, l.district, l.area ? `${formatNumber(l.area)} متر` : '', price]
        .filter(Boolean).map(esc).join(' · ');
    const due = l.next_call_at ? `<span class="badge bg-warning text-dark"><i class="bi bi-arrow-repeat"></i> تماس مجدد ${_cqWhen(l.next_call_at)}</span>` : '';
    const tries = l.call_attempts ? `<span class="badge bg-secondary">${formatNumber(l.call_attempts)} تماس قبلی</span>` : '<span class="badge bg-success">اولین تماس</span>';
    const mine = l.assigned_to ? `<span class="badge bg-primary-subtle text-primary-emphasis">${esc(l.assigned_to)}</span>` : '';
    const chat = l.contact_channel === 'chat_only' ? '<span class="badge bg-info text-dark">فقط چت</span>' : '';
    const phone = esc(l.phone_number || '');
    return `<div class="cq-card" id="cq-${l.id}">
        <div class="cq-head">
            <a class="cq-phone" href="tel:${safeTel(l.phone_number)}" dir="ltr"><i class="bi bi-telephone-fill"></i> ${phone}</a>
            <div class="cq-title">
                <a href="${esc(l.property_url || '#')}" target="_blank" rel="noopener">${esc(l.property_title || 'بدون عنوان')}</a>
                <div class="cq-meta">${meta}${l.serial_no ? ` · کد ${formatNumber(l.serial_no)}` : ''}</div>
            </div>
            <div class="cq-badges">${due}${tries}${mine}${chat}</div>
        </div>
        ${l.notes ? `<div class="cq-notes">${esc(l.notes).replace(/\n/g, '<br>')}</div>` : ''}
        <div class="cq-actions">
            <button class="btn btn-sm btn-success" onclick="cqOutcome(${l.id}, 'answered')"><i class="bi bi-check-lg"></i> پاسخ داد</button>
            <button class="btn btn-sm btn-outline-secondary" onclick="cqOutcome(${l.id}, 'no_answer')"><i class="bi bi-telephone-x"></i> پاسخ نداد</button>
            ${_cqMore(`
            <button class="btn btn-sm btn-outline-primary" onclick="cqVisit(${l.id})"><i class="bi bi-calendar-check"></i> بازدید</button>
            <button class="btn btn-sm btn-outline-warning" onclick="cqCallback(${l.id})"><i class="bi bi-alarm"></i> دوباره زنگ بزن</button>
            <button class="btn btn-sm btn-outline-secondary" onclick="cqOutcome(${l.id}, 'busy')">مشغول</button>
            <button class="btn btn-sm btn-outline-danger" onclick="cqOutcome(${l.id}, 'not_interested')">علاقه ندارد</button>
            <button class="btn btn-sm btn-outline-danger" onclick="cqOutcome(${l.id}, 'wrong_number')">شماره اشتباه</button>`)}
        </div>
    </div>`;
}

async function _cqSend(id, body, doneMsg) {
    try {
        const r = await apiCall(`/crm/leads/${id}/call`, { method: 'POST', body: JSON.stringify(body) });
        const card = document.getElementById(`cq-${id}`);
        if (card) { card.classList.add('cq-done'); setTimeout(() => card.remove(), 350); }
        showToast(r.label || 'ثبت شد', doneMsg || (r.next_call_at ? `دوباره: ${_cqWhen(r.next_call_at)}` : ''), 'success');
        setTimeout(loadCalls, 400);
        if (typeof loadLeads === 'function' && document.querySelector('#crm-tab-leads.active')) loadLeads();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function cqOutcome(id, outcome) {
    if (outcome === 'answered') {
        const note = await askText({
            icon: 'bi-chat-left-text', title: 'پاسخ داد', okLabel: 'ثبت',
            body: 'چه گفت؟ یک خط کافی است — روی لید می‌ماند.',
            field: { label: 'یادداشت (اختیاری)', placeholder: 'مثلاً: قیمت قطعی ۲ میلیارد، هفتهٔ بعد خالی می‌شود' },
        });
        if (note === null) return;
        return _cqSend(id, { outcome, note: note.trim() || null });
    }
    if (outcome === 'not_interested' || outcome === 'wrong_number') {
        if (!await askConfirm({ icon: 'bi-x-circle', title: outcome === 'wrong_number' ? 'شماره اشتباه' : 'علاقه ندارد', tone: 'danger', okLabel: 'ثبت',
                                body: 'این لید بسته می‌شود و دیگر در لیست تماس نمی‌آید.' })) return;
    }
    return _cqSend(id, { outcome });
}

async function cqCallback(id) {
    // the three answers people actually give, and a free one
    const pick = await _askOpen({
        icon: 'bi-alarm', title: 'دوباره زنگ بزن', okLabel: 'ثبت', cancelLabel: 'انصراف',
        body: 'کِی؟',
        field: { label: 'زمان', value: '1h', options: [
            ['1h', 'یک ساعت دیگر'], ['3h', 'سه ساعت دیگر'], ['tomorrow', 'فردا ۱۰ صبح'], ['custom', 'تاریخ و ساعت دیگر…'],
        ] },
    });
    if (pick === null || pick === false) return;
    let at = new Date();
    if (pick === '1h') at.setHours(at.getHours() + 1);
    else if (pick === '3h') at.setHours(at.getHours() + 3);
    else if (pick === 'tomorrow') { at.setDate(at.getDate() + 1); at.setHours(10, 0, 0, 0); }
    else {
        const raw = await askText({ icon: 'bi-calendar', title: 'زمان تماس مجدد', body: 'به وقت تهران.',
            field: { label: 'تاریخ و ساعت', placeholder: '1405/06/28 16:30', dir: 'ltr',
                     validate: v => /^\d{4}\/\d{1,2}\/\d{1,2}\s+([01]?\d|2[0-3]):[0-5]\d$/.test(v.trim()) ? '' : 'مثل 1405/06/28 16:30 بنویسید' } });
        if (raw === null) return;
        const [dpart, tpart] = raw.trim().split(/\s+/);
        const g = jalaliToGregorian(dpart);
        if (!g) { showToast('خطا', 'تاریخ نامعتبر است', 'warning'); return; }
        const [hh, mm] = tpart.split(':').map(Number);
        at = new Date(`${g}T${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}:00`);
    }
    return _cqSend(id, { outcome: 'callback', callback_at: at.toISOString() }, `دوباره: ${_cqWhen(at.toISOString())}`);
}

async function cqVisit(id) {
    const raw = await askText({ icon: 'bi-calendar-check', title: 'بازدید گذاشتیم', okLabel: 'ثبت',
        body: 'زمان بازدید را بنویسید تا در تقویم ثبت شود؛ خالی بگذارید اگر هنوز مشخص نیست.',
        field: { label: 'تاریخ و ساعت (اختیاری)', placeholder: '1405/06/28 16:30', dir: 'ltr',
                 validate: v => !v.trim() || /^\d{4}\/\d{1,2}\/\d{1,2}\s+([01]?\d|2[0-3]):[0-5]\d$/.test(v.trim()) ? '' : 'مثل 1405/06/28 16:30 بنویسید' } });
    if (raw === null) return;
    const body = { outcome: 'visit' };
    if (raw.trim()) {
        const [dpart, tpart] = raw.trim().split(/\s+/);
        const g = jalaliToGregorian(dpart);
        if (g) {
            const [hh, mm] = tpart.split(':').map(Number);
            body.visit_at = new Date(`${g}T${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}:00`).toISOString();
        }
    }
    return _cqSend(id, body, body.visit_at ? 'در تقویم ثبت شد' : '');
}

async function loadCallsSummary() {
    const box = document.getElementById('calls-summary');
    if (!box) return;
    try {
        const d = await apiCall('/crm/calls/summary?days=1');
        if (!d.agents.length) { box.innerHTML = ''; return; }
        box.innerHTML = `<div class="cq-sum-title"><i class="bi bi-people"></i> امروز، به تفکیک مشاور</div>
            <div class="table-responsive"><table class="table table-sm mb-0 align-middle"><thead><tr>
            <th>مشاور</th><th>تماس</th><th>پاسخ داد</th><th>پاسخ نداد</th><th>تماس مجدد</th><th>بازدید</th><th>بسته شد</th></tr></thead><tbody>` +
            d.agents.map(a => `<tr><td>${esc(a.agent)}</td><td>${formatNumber(a.calls)}</td><td>${formatNumber(a.answered)}</td><td>${formatNumber(a.no_answer)}</td><td>${formatNumber(a.callback)}</td><td>${formatNumber(a.visit)}</td><td>${formatNumber(a.rejected)}</td></tr>`).join('') +
            `</tbody></table></div>`;
    } catch (_) { box.innerHTML = ''; }
}
// ── تطبیق خودکار: the engine's fits, one card per customer × listing ──
async function loadMatches() {
    const box = document.getElementById('matches-list');
    if (!box) return;
    try {
        const [d, s] = await Promise.all([apiCall('/crm/matches?status=new&limit=40'), apiCall('/crm/matches/summary')]);
        const items = d.items || [];
        document.getElementById('matches-count').textContent = formatNumber(d.total || 0);
        document.getElementById('matches-stats').textContent =
            `هر ${formatNumber(s.every_minutes)} دقیقه · آستانهٔ ${formatNumber(s.min_score)}٪`;
        const badge = document.getElementById('matches-due-badge');
        if (badge) { badge.textContent = formatNumber(s.new || 0); badge.classList.toggle('d-none', !s.new); }
        box.innerHTML = items.length
            ? items.map(_matchQueueCard).join('')
            : `<div class="cq-empty"><i class="bi bi-bullseye"></i> فعلاً آگهی تازه‌ای با معیار مشتری‌ها نخوانده.
               ${s.cursor ? '' : 'اولین اسکرپ که تمام شود، موتور تطبیق شروع می‌کند.'}</div>`;
    } catch (e) {
        box.innerHTML = `<div class="text-danger small">${esc(e.message || 'خطا')}</div>`;
    }
}

function _matchQueueCard(m) {
    const p = m.property || {}, c = m.customer || {};
    const price = p.listing_type === 'rent'
        ? [p.deposit ? 'رهن ' + formatPrice(p.deposit) : '', p.rent_price ? 'اجاره ' + formatPrice(p.rent_price) : ''].filter(Boolean).join(' · ')
        : (p.price ? formatPrice(p.price) : '');
    const meta = [p.district || p.city_name, p.area ? `${formatNumber(p.area)} متر` : '', p.rooms != null ? `${formatNumber(p.rooms)} خواب` : '', price]
        .filter(Boolean).map(esc).join(' · ');
    const wants = [c.desired_district, c.desired_specs, c.budget_max ? 'تا ' + formatPrice(c.budget_max) : '']
        .filter(Boolean).map(esc).join(' · ');
    const temp = { hot: ['bg-danger', 'داغ'], warm: ['bg-warning text-dark', 'گرم'], cold: ['bg-secondary', 'سرد'] }[c.temperature] || ['bg-secondary', esc(c.temperature || '')];
    const reasons = (m.reasons || []).map(r => `<span class="mq-reason">${esc(r)}</span>`).join('');
    return `<div class="cq-card mq-card" id="mq-${m.id}">
        <div class="cq-head">
            <a class="cq-phone" href="tel:${safeTel(c.mobile1 || '')}" dir="ltr"><i class="bi bi-telephone-fill"></i> ${esc(c.mobile1 || '—')}</a>
            <div class="cq-title">
                <div><b>${esc(c.full_name || 'مشتری')}</b> <span class="badge ${temp[0]}">${temp[1]}</span>
                    ${c.consultant_name ? `<span class="badge bg-primary-subtle text-primary-emphasis">${esc(c.consultant_name)}</span>` : ''}
                    <span class="text-muted small">می‌خواست: ${wants || '—'}</span></div>
                <div class="mq-prop"><i class="bi bi-house-door"></i>
                    <a href="#" onclick="event.preventDefault(); viewProperty(${p.id})">${esc(p.title || 'آگهی')}</a>
                    ${p.serial_no ? `<span class="serial-badge">${formatSerial(p.serial_no)}</span>` : ''}</div>
                <div class="cq-meta">${meta}</div>
                <div class="mq-reasons">${reasons}</div>
            </div>
            <div class="cq-badges"><span class="mq-score" title="میزان همخوانی">${formatNumber(m.score)}٪</span></div>
        </div>
        <div class="cq-actions">
            <button class="btn btn-sm btn-success" onclick="mqDecide(${m.id}, 'contacted')"><i class="bi bi-check-lg"></i> تماس گرفتم</button>
            <button class="btn btn-sm btn-primary" onclick="mqSms(${m.id})" ${c.mobile1 ? '' : 'disabled title="مشتری شماره ندارد"'}><i class="bi bi-chat-dots"></i> پیامک به مشتری</button>
            ${_cqMore(`
            <button class="btn btn-sm btn-outline-primary" onclick="viewProperty(${p.id})"><i class="bi bi-eye"></i> جزئیات ملک</button>
            <button class="btn btn-sm btn-outline-secondary" onclick="shareFile(${p.id})" title="واتساپ / تلگرام / کپی"><i class="bi bi-share"></i> ارسال</button>
            <button class="btn btn-sm btn-outline-danger" onclick="mqDecide(${m.id}, 'dismissed')"><i class="bi bi-x-lg"></i> مناسب نیست</button>`)}
        </div>
    </div>`;
}

async function mqDecide(id, status) {
    let note = null;
    if (status === 'contacted') {
        note = await askText({ icon: 'bi-chat-left-text', title: 'تماس گرفتم', okLabel: 'ثبت',
            body: 'نتیجه در پروندهٔ مشتری ثبت می‌شود.', field: { label: 'یادداشت (اختیاری)', placeholder: 'مثلاً بازدید فردا ۱۰' } });
        if (note === null) return;
    }
    try {
        await apiCall(`/crm/matches/${id}/decide`, { method: 'POST', body: JSON.stringify({ status, note }) });
        const card = document.getElementById(`mq-${id}`);
        if (card) { card.classList.add('cq-done'); setTimeout(() => card.remove(), 350); }
        showToast('ثبت شد', status === 'contacted' ? 'در پروندهٔ مشتری نوشته شد' : '', 'success');
        setTimeout(loadMatches, 400);
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

/** «پیامک به مشتری»: the customer-safe card, to the customer's own number,
 *  shown first so the consultant can read and edit what goes out. */
async function mqSms(id) {
    let pv;
    try { pv = await apiCall(`/crm/matches/${id}/sms`); }
    catch (e) { showToast('خطا', e.message, 'danger'); return; }
    const text = await askText({
        icon: 'bi-chat-dots', title: 'پیامک به مشتری', okLabel: 'ارسال پیامک',
        body: `به <b dir="ltr">${esc(pv.to)}</b> (${esc(pv.customer || 'مشتری')}) فرستاده می‌شود.`,
        field: { label: `متن پیامک — حدود ${formatNumber(pv.segments)} بخش`, multiline: true, value: pv.text,
                 validate: v => v ? '' : 'متن پیامک خالی است' },
    });
    if (text === null) return;
    try {
        const r = await apiCall(`/crm/matches/${id}/sms`, { method: 'POST', body: JSON.stringify({ message: text }) });
        const card = document.getElementById(`mq-${id}`);
        if (card) { card.classList.add('cq-done'); setTimeout(() => card.remove(), 350); }
        showToast('پیامک رفت', `به ${r.to} · ${formatNumber(r.segments)} بخش`, 'success');
        setTimeout(loadMatches, 400);
    } catch (e) { showToast('پیامک ارسال نشد', e.message, 'danger'); }
}

async function runMatchesNow() {
    try {
        const r = await apiCall('/crm/matches/run', { method: 'POST' });
        showToast('بررسی شد', `${formatNumber(r.scanned || 0)} آگهی سنجیده شد، ${formatNumber(r.matched || 0)} تطبیق تازه`, 'success');
        loadMatches();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

// ── هشدار کاهش قیمت: the watcher's alerts, newest cut first ──
async function loadPriceDrops() {
    const box = document.getElementById('drops-list');
    if (!box) return;
    try {
        const [d, s] = await Promise.all([apiCall('/crm/price-drops?status=new&limit=40'), apiCall('/crm/price-drops/summary')]);
        const items = d.items || [];
        document.getElementById('drops-count').textContent = formatNumber(d.total || 0);
        document.getElementById('drops-stats').textContent =
            `هر ${formatNumber(s.every_minutes)} دقیقه · کاهش ${formatNumber(s.min_drop_pct)}٪ به بالا`;
        box.innerHTML = items.length
            ? items.map(_dropCard).join('')
            : '<div class="cq-empty"><i class="bi bi-graph-down-arrow"></i> از آخرین بررسی، قیمتی پایین نیامده.</div>';
    } catch (e) {
        box.innerHTML = `<div class="text-danger small">${esc(e.message || 'خطا')}</div>`;
    }
}

function _dropCard(a) {
    const p = a.property || {};
    const rent = a.listing_type === 'rent';
    const now = rent
        ? [p.deposit ? 'رهن ' + formatPrice(p.deposit) : '', p.rent_price ? 'اجاره ' + formatPrice(p.rent_price) : ''].filter(Boolean).join(' · ')
        : formatPrice(p.total_price || p.price);
    const meta = [p.district || p.city_name, p.area ? `${formatNumber(p.area)} متر` : '', p.rooms != null ? `${formatNumber(p.rooms)} خواب` : '']
        .filter(Boolean).map(esc).join(' · ');
    const phone = p.phone_number ? `<a class="cq-phone" href="tel:${safeTel(p.phone_number)}" dir="ltr"><i class="bi bi-telephone-fill"></i> ${esc(p.phone_number)}</a>` : '';
    const lead = a.lead ? `<span class="badge bg-secondary">لید #${formatNumber(a.lead.id)}${a.lead.assigned_to ? ' · ' + esc(a.lead.assigned_to) : ''}</span>` : '';
    const fits = a.matches_created ? `<span class="badge bg-info text-dark"><i class="bi bi-bullseye"></i> ${formatNumber(a.matches_created)} مشتری هم‌خوان</span>` : '';
    return `<div class="cq-card pd-card" id="pd-${a.id}">
        <div class="cq-head">
            ${phone}
            <div class="cq-title">
                <a href="#" onclick="event.preventDefault(); viewProperty(${p.id})">${esc(p.title || 'آگهی')}</a>
                ${p.serial_no ? `<span class="serial-badge">${formatSerial(p.serial_no)}</span>` : ''}
                <div class="cq-meta">${meta}</div>
                <div class="pd-move"><span class="pd-from">${formatPrice(a.from_amount)}</span> <i class="bi bi-arrow-left"></i>
                    <span class="pd-to">${formatPrice(a.to_amount)}</span>
                    ${rent ? `<span class="text-muted small">(رهن + ۳۰ × اجاره) — الان: ${now}</span>` : ''}</div>
                <div class="mt-1">${lead} ${fits}</div>
            </div>
            <div class="cq-badges"><span class="pd-pct">${formatNumber(a.delta_pct)}٪</span>
                <span class="small text-muted">${_cqWhen(a.moved_at)}</span></div>
        </div>
        <div class="cq-actions">
            <button class="btn btn-sm btn-outline-success" onclick="showCustomersForProperty(${p.id})"><i class="bi bi-person-check"></i> متقاضیان هم‌خوان</button>
            <button class="btn btn-sm btn-outline-secondary" onclick="pdDecide(${a.id}, 'seen')"><i class="bi bi-check2"></i> دیدم</button>
            ${_cqMore(`
            <button class="btn btn-sm btn-outline-primary" onclick="viewProperty(${p.id})"><i class="bi bi-eye"></i> جزئیات</button>
            <button class="btn btn-sm btn-outline-secondary" onclick="shareFile(${p.id})"><i class="bi bi-share"></i> ارسال برای مشتری</button>`)}
        </div>
    </div>`;
}

async function pdDecide(id, status) {
    try {
        await apiCall(`/crm/price-drops/${id}/decide`, { method: 'POST', body: JSON.stringify({ status }) });
        const card = document.getElementById(`pd-${id}`);
        if (card) { card.classList.add('cq-done'); setTimeout(() => card.remove(), 350); }
        setTimeout(loadPriceDrops, 400);
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function runPriceWatchNow() {
    try {
        const r = await apiCall('/crm/price-drops/run', { method: 'POST' });
        showToast('بررسی شد', `${formatNumber(r.scanned || 0)} تغییر قیمت سنجیده شد، ${formatNumber(r.drops || 0)} کاهش، ${formatNumber(r.matches || 0)} تطبیق تازه`, 'success');
        loadPriceDrops(); loadMatches();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

// ═══ End call queue ═══════════════════════════════════════════

function _applyCrmRoleVisibility() {
    const isSuperAdmin = ['root', 'super_admin'].includes(_currentUser?.role);
    document.querySelectorAll('.crm-superadmin-only').forEach(el => {
        el.style.display = isSuperAdmin ? '' : 'none';
    });
}

// ═══════════════════════════════════════════════════════════════
//  CRM — Tab activation hooks
// ═══════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('#crm-main-tabs .nav-link').forEach(tab => {
        tab.addEventListener('shown.bs.tab', e => {
            const target = e.target.getAttribute('data-bs-target');
            if (target === '#crm-tab-calls')     loadCalls();
            if (target === '#crm-tab-tasks')     loadTasks();
            if (target === '#crm-tab-calendar')  { loadCalendar(); loadUpcomingEvents(); }
            if (target === '#crm-tab-filing')    loadFiling();
            if (target === '#crm-tab-customers') loadCustomers();
            if (target === '#crm-tab-dpa')       loadDpa();
            if (target === '#crm-tab-contacts')  loadContacts();
            if (target === '#crm-tab-deals')     loadDeals();
            if (target === '#crm-tab-notes')     loadNotes();
            if (target === '#crm-tab-reminders') loadReminders();
            if (target === '#crm-tab-sms')       loadSmsLogs();
            if (target === '#crm-tab-leads')     loadLeads();
            if (target === '#crm-tab-report')    loadCrmStats();
        });
    });
});

// ═══════════════════════════════════════════════════════════════
// تقویم — Jalali calendar
// ═══════════════════════════════════════════════════════════════
//
// Jalali conversion runs on Intl's built-in Persian calendar rather than a
// library: persian-date is loaded from a CDN, and a CDN outage must not take
// the calendar down with it. Intl ships with the browser and is exact.

const _JFMT = new Intl.DateTimeFormat('en-u-ca-persian',
    { year: 'numeric', month: 'numeric', day: 'numeric' });

const J_MONTHS = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
                  'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'];
const J_WEEKDAYS = ['شنبه', 'یک‌شنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنج‌شنبه', 'جمعه'];
const J_WEEKDAYS_SHORT = ['ش', 'ی', 'د', 'س', 'چ', 'پ', 'ج'];

const CAL_TYPES = {
    visit:    { label: 'بازدید ملک',      color: '#34d399', icon: 'bi-geo-alt' },
    meeting:  { label: 'نشست و قرارداد',  color: '#a78bfa', icon: 'bi-file-earmark-text' },
    call:     { label: 'تماس تلفنی',      color: '#38bdf8', icon: 'bi-telephone' },
    showing:  { label: 'نمایش به مشتری',  color: '#fbbf24', icon: 'bi-eye' },
    personal: { label: 'شخصی',            color: '#94a3b8', icon: 'bi-person' },
    other:    { label: 'سایر',            color: '#f472b6', icon: 'bi-three-dots' },
    task:     { label: 'وظیفه',           color: '#60a5fa', icon: 'bi-check2-square' },
    reminder: { label: 'یادآور',          color: '#e879f9', icon: 'bi-alarm' },
};

/** Jalali year/month/day of a Date, via the platform calendar. */
function jParts(d) {
    const out = {};
    for (const p of _JFMT.formatToParts(d)) {
        if (p.type === 'year' || p.type === 'month' || p.type === 'day') out[p.type] = +p.value;
    }
    return { jy: out.year, jm: out.month, jd: out.day };
}

/** Noon copy — keeps day arithmetic clear of midnight/DST edges. */
function _noon(d) {
    const x = new Date(d);
    x.setHours(12, 0, 0, 0);
    return x;
}

function jStartOfMonth(d) {
    const x = _noon(d);
    x.setDate(x.getDate() - (jParts(x).jd - 1));
    return x;
}

/** Walk whole Jalali months. +32 days from the 1st always lands in the next
 *  month (they run 29–31 days), so snapping back to the 1st is exact. */
function jAddMonths(d, n) {
    let x = jStartOfMonth(d);
    for (let i = 0; i < Math.abs(n); i++) {
        x.setDate(x.getDate() + (n > 0 ? 32 : -1));
        x = jStartOfMonth(x);
    }
    return x;
}

function jDaysInMonth(d) {
    const start = jStartOfMonth(d), m = jParts(start).jm;
    for (let i = 28; i <= 32; i++) {
        const t = new Date(start);
        t.setDate(t.getDate() + i);
        if (jParts(t).jm !== m) return i;
    }
    return 31;
}

/** Jalali y/m/d → Date. Walks months instead of guessing offsets, so it never
 *  oscillates around a boundary. Returns null for a date that does not exist. */
function jalaliToDate(jy, jm, jd) {
    if (!jy || !jm || !jd || jm < 1 || jm > 12 || jd < 1 || jd > 31) return null;
    let cur = _noon(new Date(jy + 621, 2, 21));      // ≈ Nowruz of that year
    for (let guard = 0; guard < 40; guard++) {        // land in the right year
        const p = jParts(cur);
        if (p.jy === jy) break;
        cur.setDate(cur.getDate() + (jy - p.jy) * 365);
    }
    cur = jStartOfMonth(cur);
    for (let guard = 0; guard < 30; guard++) {        // then the right month
        const p = jParts(cur);
        if (p.jy === jy && p.jm === jm) break;
        const behind = p.jy < jy || (p.jy === jy && p.jm < jm);
        cur = jAddMonths(cur, behind ? 1 : -1);
    }
    const at = jParts(cur);
    if (at.jy !== jy || at.jm !== jm) return null;
    cur.setDate(cur.getDate() + (jd - 1));
    const got = jParts(cur);
    return (got.jy === jy && got.jm === jm && got.jd === jd) ? cur : null;
}

const _pad2 = n => String(n).padStart(2, '0');

/** Persian digits with no thousands separator — years and day numbers are
 *  labels, not quantities: formatNumber() would render 1405 as ۱٬۴۰۵. */
const _faNum = n => String(n).replace(/[0-9]/g, d => '۰۱۲۳۴۵۶۷۸۹'[+d]);

/** 1405/05/06 */
function jFormat(d) {
    const { jy, jm, jd } = jParts(d);
    return `${jy}/${_pad2(jm)}/${_pad2(jd)}`;
}

/** ۶ مرداد ۱۴۰۵ */
function jFormatLong(d) {
    const { jy, jm, jd } = jParts(d);
    return `${_faNum(jd)} ${J_MONTHS[jm - 1]} ${_faNum(jy)}`;
}

/** Parse 1405/05/06 (or ۱۴۰۵-۵-۶) back to a Date. */
function jParseInput(text) {
    if (!text) return null;
    const ascii = String(text).replace(/[۰-۹]/g, c => '۰۱۲۳۴۵۶۷۸۹'.indexOf(c));
    const m = ascii.match(/(\d{4})\s*[\/\-.]\s*(\d{1,2})\s*[\/\-.]\s*(\d{1,2})/);
    return m ? jalaliToDate(+m[1], +m[2], +m[3]) : null;
}

/** Column 0..6 with Saturday first, the way Persian weeks are laid out. */
const jWeekCol = d => (d.getDay() + 1) % 7;

function jStartOfWeek(d) {
    const x = _noon(d);
    x.setDate(x.getDate() - jWeekCol(x));
    return x;
}

/** Wall-clock ISO with no timezone suffix — the server stores what we send. */
function _isoLocal(d) {
    return `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())}` +
           `T${_pad2(d.getHours())}:${_pad2(d.getMinutes())}:00`;
}

const _sameDay = (a, b) => a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

// ── calendar state ──────────────────────────────────────────────
let _calCursor = new Date();     // any date inside the shown period
let _calView = 'month';          // month | week | day
let _calType = '';               // event-type filter
let _calRows = [];               // what the grid is currently drawing
const CAL_HOUR_H = 46;           // px per hour in the week/day grid

const _faTime = d => d.toLocaleTimeString('fa-IR',
    { hour: '2-digit', minute: '2-digit', hour12: false });

function _calMeta(row) {
    return CAL_TYPES[row.event_type] || CAL_TYPES.other;
}

/** [start, end) of the visible period, plus its Persian caption. */
function _calRange() {
    if (_calView === 'day') {
        const s = _noon(_calCursor); s.setHours(0, 0, 0, 0);
        const e = new Date(s); e.setDate(e.getDate() + 1);
        return { start: s, end: e, title: `${J_WEEKDAYS[jWeekCol(_calCursor)]}، ${jFormatLong(_calCursor)}` };
    }
    if (_calView === 'week') {
        const s = jStartOfWeek(_calCursor); s.setHours(0, 0, 0, 0);
        const e = new Date(s); e.setDate(e.getDate() + 7);
        const last = new Date(s); last.setDate(last.getDate() + 6);
        const a = jParts(s), b = jParts(last);
        // «۳ تا ۹ مرداد ۱۴۰۵» rather than repeating the month twice
        const title = (a.jy === b.jy && a.jm === b.jm)
            ? `${_faNum(a.jd)} تا ${_faNum(b.jd)} ${J_MONTHS[a.jm - 1]} ${_faNum(a.jy)}`
            : `${jFormatLong(s)} تا ${jFormatLong(last)}`;
        return { start: s, end: e, title };
    }
    // month view draws whole weeks, so the window spills into the neighbours
    const first = jStartOfMonth(_calCursor);
    const s = jStartOfWeek(first); s.setHours(0, 0, 0, 0);
    const e = new Date(s); e.setDate(e.getDate() + 42);
    const { jy, jm } = jParts(first);
    return { start: s, end: e, title: `${J_MONTHS[jm - 1]} ${_faNum(jy)}` };
}

function switchCalView(view) {
    _calView = view;
    document.querySelectorAll('#cal-view-switch .btn').forEach(b =>
        b.classList.toggle('active', b.dataset.view === view));
    loadCalendar();
}

function calStep(dir) {
    const x = _noon(_calCursor);
    if (_calView === 'month') {
        // Keep the day of month: jAddMonths lands on the 1st, and losing the
        // day means switching to هفته/روز afterwards jumps to the wrong week.
        const day = jParts(x).jd;
        const target = jAddMonths(x, dir);
        target.setDate(target.getDate() + Math.min(day, jDaysInMonth(target)) - 1);
        _calCursor = target;
    } else {
        x.setDate(x.getDate() + dir * (_calView === 'week' ? 7 : 1));
        _calCursor = x;
    }
    loadCalendar();
}

function calToday() { _calCursor = new Date(); loadCalendar(); }

async function loadCalendar() {
    const { start, end, title } = _calRange();
    const caption = document.getElementById('cal-title');
    if (caption) caption.textContent = title;

    let url = `/crm/calendar?date_from=${_isoLocal(start)}&date_to=${_isoLocal(end)}`;
    if (_calType) url += `&event_type=${_calType}`;

    const body = document.getElementById('cal-body');
    try {
        const data = await apiCall(url);
        _calRows = (data.items || []).filter(r => r.start_at);
        body.innerHTML = _calView === 'month'
            ? _renderCalMonth(start)
            : _renderCalTimeGrid(start, _calView === 'week' ? 7 : 1);
        _renderCalLegend();
    } catch (e) {
        body.innerHTML = `<div class="text-center text-muted py-5">
            <i class="bi bi-calendar-x" style="font-size:2rem"></i>
            <p class="mt-2">بارگیری تقویم ناموفق بود</p></div>`;
    }
}

/** Rows that fall on a given day, in time order. */
function _calRowsOn(day) {
    return _calRows
        .filter(r => _sameDay(new Date(r.start_at), day))
        .sort((a, b) => new Date(a.start_at) - new Date(b.start_at));
}

function _calChip(row, withTime = true) {
    const meta = _calMeta(row);
    const d = new Date(row.start_at);
    const done = row.status === 'done', canceled = row.status === 'canceled';
    const time = (row.all_day || !withTime) ? '' : `<b>${_faTime(d)}</b> `;
    const sms = row.sms_reminder
        ? `<i class="bi bi-chat-dots-fill cal-sms${row.sms_sent ? ' sent' : ''}"
              title="${row.sms_sent ? 'پیامک ارسال شد' : 'یادآوری پیامکی فعال است'}"></i> `
        : '';
    return `<button type="button" class="cal-chip${done ? ' done' : ''}${canceled ? ' canceled' : ''}"
            style="--c:${meta.color}" title="${esc(row.title)} — ${meta.label}"
            onclick="event.stopPropagation(); openCalRow('${row.kind}', ${row.id})">
        ${sms}${time}${esc(row.title)}</button>`;
}

function _renderCalMonth(gridStart) {
    const monthOf = jParts(jStartOfMonth(_calCursor)).jm;
    const today = new Date();
    let html = '<div class="cal-month">';
    // own classes rather than Bootstrap's d-md-* utilities: the vendored RTL
    // build does not apply the responsive display variants here
    html += J_WEEKDAYS.map((w, i) =>
        `<div class="cal-dow"><span class="dow-full">${w}</span>` +
        `<span class="dow-short">${J_WEEKDAYS_SHORT[i]}</span></div>`).join('');

    for (let i = 0; i < 42; i++) {
        const day = new Date(gridStart);
        day.setDate(day.getDate() + i);
        const { jm, jd } = jParts(day);
        const rows = _calRowsOn(day);
        const cls = [
            'cal-cell',
            jm !== monthOf ? 'muted' : '',
            _sameDay(day, today) ? 'today' : '',
            jWeekCol(day) === 6 ? 'holiday' : '',   // جمعه
        ].filter(Boolean).join(' ');

        const shown = rows.slice(0, 3).map(r => _calChip(r)).join('');
        const more = rows.length > 3
            ? `<button type="button" class="cal-more"
                 onclick="event.stopPropagation(); openCalDay('${_isoLocal(day)}')">
                 ${_faNum(rows.length - 3)}+ بیشتر</button>` : '';

        html += `<div class="${cls}" onclick="openEventModal(null, '${_isoLocal(day)}')"
                      title="افزودن قرار در ${jFormat(day)}">
            <div class="cal-daynum">${_faNum(jd)}</div>
            <div class="cal-chips">${shown}${more}</div>
        </div>`;
    }
    return html + '</div>';
}

/** Shared renderer for هفته (7 columns) and روز (1 column). */
function _renderCalTimeGrid(start, dayCount) {
    const days = [];
    for (let i = 0; i < dayCount; i++) {
        const d = new Date(start); d.setDate(d.getDate() + i); days.push(d);
    }
    const timed = _calRows.filter(r => !r.all_day);
    // default work window, widened so nothing sits outside the grid
    let minH = 7, maxH = 21;
    timed.forEach(r => {
        const h = new Date(r.start_at).getHours();
        minH = Math.min(minH, h);
        maxH = Math.max(maxH, h + 1);
    });
    const hours = [];
    for (let h = minH; h <= maxH; h++) hours.push(h);

    const today = new Date();
    const gridVars = `--cols:${dayCount}; --hh:${CAL_HOUR_H}px`;
    let head = `<div class="cal-tg-head" style="${gridVars}"><div class="cal-tg-gutter"></div>`;
    days.forEach(d => {
        const { jd, jm } = jParts(d);
        head += `<div class="cal-tg-day${_sameDay(d, today) ? ' today' : ''}"
                      onclick="openEventModal(null, '${_isoLocal(d)}')">
            <span class="dow">${J_WEEKDAYS[jWeekCol(d)]}</span>
            <span class="num">${_faNum(jd)} ${J_MONTHS[jm - 1]}</span></div>`;
    });
    head += '</div>';

    // all-day strip, only when something needs it
    const allDay = _calRows.filter(r => r.all_day);
    let strip = '';
    if (allDay.length) {
        strip = `<div class="cal-tg-allday" style="${gridVars}"><div class="cal-tg-gutter">تمام‌روز</div>`;
        days.forEach(d => {
            strip += `<div class="cal-tg-adcell">
                ${allDay.filter(r => _sameDay(new Date(r.start_at), d))
                        .map(r => _calChip(r, false)).join('')}</div>`;
        });
        strip += '</div>';
    }

    let gutter = '<div class="cal-tg-gutter">';
    hours.forEach(h => gutter += `<div class="cal-tg-hour">${_faNum(_pad2(h))}:۰۰</div>`);
    gutter += '</div>';

    let cols = '';
    days.forEach(d => {
        const rows = _calRowsOn(d).filter(r => !r.all_day);
        let cells = hours.map(() => '<div class="cal-tg-slot"></div>').join('');
        const lanes = _calLanes(rows);
        const blocks = rows.map((r, i) => {
            const s = new Date(r.start_at);
            const e = r.end_at ? new Date(r.end_at) : new Date(s.getTime() + 60 * 60 * 1000);
            const top = ((s.getHours() - minH) * 60 + s.getMinutes()) / 60 * CAL_HOUR_H;
            const mins = Math.max(30, (e - s) / 60000);
            const meta = _calMeta(r);
            const { lane, of } = lanes[i];
            return `<button type="button" class="cal-block${r.status === 'done' ? ' done' : ''}${r.status === 'canceled' ? ' canceled' : ''}"
                style="--c:${meta.color}; top:${top}px; height:${Math.max(24, mins / 60 * CAL_HOUR_H - 2)}px;
                       width:calc(${100 / of}% - 4px); right:calc(${(lane * 100) / of}% + 2px)"
                title="${esc(r.title)} — ${meta.label}"
                onclick="event.stopPropagation(); openCalRow('${r.kind}', ${r.id})">
                <span class="t">${_faTime(s)}</span> ${esc(r.title)}
                ${r.location ? `<span class="loc"><i class="bi bi-geo-alt"></i> ${esc(r.location)}</span>` : ''}
            </button>`;
        }).join('');
        cols += `<div class="cal-tg-col" onclick="openEventModal(null, '${_isoLocal(d)}')">
                    ${cells}${blocks}</div>`;
    });

    return `${head}${strip}<div class="cal-tg-body" style="${gridVars}">${gutter}${cols}</div>`;
}

/** Side-by-side placement for appointments that overlap in time. */
function _calLanes(rows) {
    const out = rows.map(() => ({ lane: 0, of: 1 }));
    const ends = [];   // end time per lane
    const spans = rows.map(r => {
        const s = new Date(r.start_at).getTime();
        const e = r.end_at ? new Date(r.end_at).getTime() : s + 3600000;
        return [s, Math.max(e, s + 1800000)];
    });
    let group = [], groupEnd = -Infinity;
    const flush = () => {
        const width = Math.max(1, ends.length);
        group.forEach(i => out[i].of = width);
        group = []; ends.length = 0; groupEnd = -Infinity;
    };
    rows.forEach((_r, i) => {
        const [s, e] = spans[i];
        if (s >= groupEnd && group.length) flush();
        let lane = ends.findIndex(end => end <= s);
        if (lane === -1) { lane = ends.length; ends.push(e); } else { ends[lane] = e; }
        out[i].lane = lane;
        group.push(i);
        groupEnd = Math.max(groupEnd, e);
    });
    if (group.length) flush();
    return out;
}

function _renderCalLegend() {
    const el = document.getElementById('cal-legend');
    if (!el) return;
    const used = new Set(_calRows.map(r => r.event_type));
    el.innerHTML = Object.entries(CAL_TYPES)
        .filter(([k]) => used.has(k))
        .map(([, m]) => `<span class="cal-legend-item"><i style="background:${m.color}"></i>${m.label}</span>`)
        .join('') || '<span class="text-muted">قراری در این بازه ثبت نشده است</span>';
}

/** Jump to the day view for a date the month grid could not fit. */
function openCalDay(iso) {
    _calCursor = new Date(iso);
    switchCalView('day');
}

/** Tasks and reminders are overlays — send the user to their own tab. */
function openCalRow(kind, id) {
    if (kind === 'event') return openEventModal(id);
    const tab = kind === 'task' ? '#crm-tab-tasks' : '#crm-tab-reminders';
    showToast('توجه', kind === 'task'
        ? 'این یک وظیفه است و در تب «وظایف» ویرایش می‌شود'
        : 'این یک یادآور است و در تب «یادآورها» ویرایش می‌شود', 'info');
    document.querySelector(`[data-bs-target="${tab}"]`)?.click();
}

// ── event modal ─────────────────────────────────────────────────
let _editingEventId = null;

const CAL_REMIND_OPTIONS = [
    [0, 'بدون یادآوری'], [15, '۱۵ دقیقه قبل'], [30, '۳۰ دقیقه قبل'],
    [60, '۱ ساعت قبل'], [180, '۳ ساعت قبل'], [1440, '۱ روز قبل'],
];

function _calSetForm(ev) {
    const v = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? ''; };
    const start = ev.start_at ? new Date(ev.start_at) : new Date();
    v('ev-title', ev.title || '');
    v('ev-type', ev.event_type || 'visit');
    v('ev-date', jFormat(start));
    v('ev-start-time', ev.all_day ? '' : `${_pad2(start.getHours())}:${_pad2(start.getMinutes())}`);
    v('ev-location', ev.location || '');
    // attendee_* is what rows created before the three-way split carry
    v('ev-owner-name', ev.owner_name || ev.attendee_name || '');
    v('ev-owner-phone', ev.owner_phone || ev.attendee_phone || '');
    v('ev-customer-name', ev.customer_name || '');
    v('ev-customer-phone', ev.customer_phone || '');
    v('ev-assigned', ev.assigned_to || '');
    v('ev-agent-phone', ev.agent_phone || '');
    v('ev-property', ev.property_serial || '');
    v('ev-description', ev.description || '');
    v('ev-outcome', ev.outcome || '');
    v('ev-status', ev.status || 'scheduled');
    v('ev-remind', ev.remind_before ?? 60);
    const allDay = document.getElementById('ev-all-day');
    if (allDay) { allDay.checked = !!ev.all_day; toggleEventAllDay(); }
    document.getElementById('ev-lead-id').value = ev.lead_id || '';
    document.getElementById('ev-customer-id').value = ev.customer_id || '';

    const sms = document.getElementById('ev-sms');
    if (sms) { sms.checked = !!ev.sms_reminder; sms.dataset.sent = ev.sms_sent ? '1' : ''; }
    // «ارسال الان» needs a saved event to send about
    document.getElementById('ev-sms-now-btn')?.classList.toggle('d-none', !ev.id);
    updateSmsHint();
}

/** Whoever will be texted: مالک / مشتری / کارشناس فروش, minus blanks and
 *  duplicates — the same rule the server applies. */
function _smsRecipients() {
    const val = id => document.getElementById(id)?.value.trim() || '';
    const rows = [
        { role: 'مالک', phone: val('ev-owner-phone') },
        { role: 'مشتری', phone: val('ev-customer-phone') },
        { role: 'کارشناس فروش', phone: val('ev-agent-phone') },
    ];
    const seen = new Set();
    return rows.filter(r => {
        const digits = r.phone.replace(/\D/g, '');
        if (!digits || seen.has(digits)) return false;
        seen.add(digits);
        return true;
    });
}

/** Explain what the SMS switch will actually do, given the rest of the form. */
function updateSmsHint() {
    const hint = document.getElementById('ev-sms-hint');
    const sms = document.getElementById('ev-sms');
    if (!hint || !sms) return;
    const remind = document.getElementById('ev-remind')?.value;
    const REMIND_FA = { '0': '', '15': '۱۵ دقیقه', '30': '۳۰ دقیقه', '60': '۱ ساعت',
                        '180': '۳ ساعت', '1440': '۱ روز' };
    const to = _smsRecipients();

    if (!sms.checked) { hint.textContent = ''; hint.className = 'form-text'; return; }
    if (!to.length) {
        hint.textContent = 'هیچ شماره‌ای وارد نشده — پیامکی ارسال نمی‌شود.';
        hint.className = 'form-text text-warning';
        return;
    }
    if (remind === '0') {
        hint.textContent = 'یادآوری روی «بدون یادآوری» است — زمان ارسال را انتخاب کنید.';
        hint.className = 'form-text text-warning';
        return;
    }
    hint.className = 'form-text text-info';
    hint.textContent = sms.dataset.sent
        ? 'پیامک این قرار قبلاً ارسال شده است.'
        : `${REMIND_FA[remind] || remind + ' دقیقه'} قبل از قرار به ${to.length} نفر پیامک می‌رود: `
          + to.map(r => `${r.role} (${r.phone})`).join('، ');
}

/** Send the appointment details to everyone involved right now (confirmations). */
async function sendEventSmsNow() {
    if (!_editingEventId) return;
    const to = _smsRecipients();
    if (!to.length) { showToast('خطا', 'هیچ شماره‌ای برای این قرار وارد نشده است', 'warning'); return; }
    const who = to.map(r => `${r.role} (${esc(r.phone)})`).join('\n');
    if (!await askConfirm({ icon: 'bi-question-lg', title: 'تأیید', okLabel: 'تأیید', body: `پیامک مشخصات این قرار برای ${to.length} نفر ارسال شود؟\n\n${who}` })) return;

    const btn = document.getElementById('ev-sms-now-btn');
    if (btn) btn.disabled = true;
    try {
        // save first, otherwise the server texts the numbers it already has
        await apiCall(`/crm/calendar/${_editingEventId}`, {
            method: 'PATCH', body: JSON.stringify(_calReadForm() || {})
        });
        const r = await apiCall(`/crm/calendar/${_editingEventId}/sms`, {
            method: 'POST', body: JSON.stringify({})
        });
        const ok = (r.sent || []).length, bad = (r.failed || []).length;
        showToast(bad ? 'ناقص' : 'موفق',
            bad ? `${formatNumber(ok)} پیامک ارسال شد، ${formatNumber(bad)} ناموفق`
                : `پیامک برای ${formatNumber(ok)} نفر ارسال شد`,
            bad ? 'warning' : 'success');
    } catch (e) {
        showToast('خطا', e.message, 'danger');
    } finally {
        if (btn) btn.disabled = false;
    }
}

function toggleEventAllDay() {
    const on = document.getElementById('ev-all-day')?.checked;
    document.getElementById('ev-time-wrap')?.classList.toggle('d-none', !!on);
}

/**
 * @param id       event to edit, or null to create
 * @param isoDate  day to prefill when creating from a grid cell
 * @param preset   extra fields (used by «ثبت بازدید» on a lead)
 */
async function openEventModal(id = null, isoDate = null, preset = {}) {
    _editingEventId = id;
    const modalEl = document.getElementById('eventModal');
    const editing = !!id;

    document.getElementById('eventModalTitle').innerHTML = editing
        ? '<i class="bi bi-calendar-check"></i> ویرایش قرار'
        : '<i class="bi bi-calendar-plus"></i> قرار جدید';
    document.getElementById('ev-delete-btn').classList.toggle('d-none', !editing);
    document.getElementById('ev-done-wrap').classList.toggle('d-none', !editing);

    let ev = { event_type: 'visit', remind_before: 60, status: 'scheduled', ...preset };
    if (editing) {
        try { ev = await apiCall(`/crm/calendar/${id}`); }
        catch (e) { showToast('خطا', 'قرار یافت نشد', 'danger'); return; }
    } else if (isoDate) {
        const d = new Date(isoDate);
        if (!d.getHours()) d.setHours(10, 0, 0, 0);      // sensible default slot
        ev.start_at = ev.start_at || _isoLocal(d);
    } else {
        ev.start_at = ev.start_at || _isoLocal(new Date());
    }
    _calSetForm(ev);
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
}

function _calReadForm() {
    const val = id => document.getElementById(id)?.value.trim() || '';
    const title = val('ev-title');
    if (!title) { showToast('خطا', 'عنوان قرار الزامی است', 'warning'); return null; }

    const day = jParseInput(val('ev-date'));
    if (!day) { showToast('خطا', 'تاریخ نامعتبر است — نمونهٔ درست: ۱۴۰۵/۰۵/۰۶', 'warning'); return null; }

    const allDay = document.getElementById('ev-all-day').checked;
    const start = new Date(day);
    if (allDay) {
        start.setHours(0, 0, 0, 0);
    } else {
        const [sh, sm] = (val('ev-start-time') || '10:00').split(':').map(Number);
        start.setHours(sh || 0, sm || 0, 0, 0);
    }
    const num = id => { const v = val(id); return v ? parseInt(v, 10) : null; };
    return {
        title,
        event_type: val('ev-type') || 'visit',
        start_at: _isoLocal(start),
        end_at: null,               // how long a visit runs is never known up front
        all_day: allDay,
        location: val('ev-location') || null,
        owner_name: val('ev-owner-name') || null,
        owner_phone: val('ev-owner-phone') || null,
        customer_name: val('ev-customer-name') || null,
        customer_phone: val('ev-customer-phone') || null,
        assigned_to: val('ev-assigned') || null,
        agent_phone: val('ev-agent-phone') || null,
        // the کد ملک the agent typed; the server turns it into the row id
        property_serial: num('ev-property'),
        lead_id: num('ev-lead-id'),
        customer_id: num('ev-customer-id'),
        description: val('ev-description') || null,
        outcome: val('ev-outcome') || null,
        status: val('ev-status') || 'scheduled',
        remind_before: num('ev-remind') ?? 60,
        sms_reminder: !!document.getElementById('ev-sms')?.checked,
    };
}

async function saveEvent() {
    const body = _calReadForm();
    if (!body) return;
    try {
        if (_editingEventId) {
            await apiCall(`/crm/calendar/${_editingEventId}`, { method: 'PATCH', body: JSON.stringify(body) });
            showToast('موفق', 'قرار به‌روزرسانی شد', 'success');
        } else {
            await apiCall('/crm/calendar', { method: 'POST', body: JSON.stringify(body) });
            showToast('موفق', 'قرار ثبت شد', 'success');
        }
        bootstrap.Modal.getInstance(document.getElementById('eventModal'))?.hide();
        loadCalendar(); loadUpcomingEvents();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function deleteEventFromModal() {
    if (!_editingEventId || !await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'این قرار حذف شود؟' })) return;
    try {
        await apiCall(`/crm/calendar/${_editingEventId}`, { method: 'DELETE' });
        showToast('موفق', 'قرار حذف شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('eventModal'))?.hide();
        loadCalendar(); loadUpcomingEvents();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

/** «ثبت بازدید» inside the lead modal — carries the property over. */
async function scheduleVisitForLead(leadId) {
    let lead = null;
    try { lead = await apiCall(`/crm/leads/${leadId}`); } catch (e) { /* fall through */ }
    const p = lead?.property_detail || {};
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    tomorrow.setHours(10, 0, 0, 0);

    bootstrap.Modal.getInstance(document.getElementById('leadModal'))?.hide();
    openEventModal(null, null, {
        event_type: 'visit',
        title: `بازدید — ${lead?.property_title || 'ملک'}`,
        start_at: _isoLocal(tomorrow),
        location: p.address || [p.city_name, p.district, p.neighborhood].filter(Boolean).join('، ') || '',
        property_serial: lead?.serial_no ?? p.serial_no ?? null,
        lead_id: leadId,
        // the lead's contact is the property owner; the customer side is
        // filled in by hand once we know who is being shown the place
        owner_name: lead?.seller_name || '',
        owner_phone: lead?.phone_number || '',
    });
}

/** Small «قرارهای پیشِ رو» list shown above the grid and on the dashboard. */
async function loadUpcomingEvents() {
    const boxes = ['cal-upcoming', 'dash-upcoming'].map(id => document.getElementById(id)).filter(Boolean);
    if (!boxes.length) return;
    try {
        const data = await apiCall('/crm/calendar/upcoming?days=7&limit=8');
        const items = data.items || [];
        const html = items.length ? items.map(r => {
            const d = new Date(r.start_at), meta = _calMeta(r);
            const today = _sameDay(d, new Date());
            return `<button type="button" class="cal-up-item" style="--c:${meta.color}"
                        onclick="openCalRow('${r.kind}', ${r.id})">
                <i class="bi ${meta.icon}"></i>
                <span class="up-when">${today ? 'امروز' : _faNum(jFormat(d))}${r.all_day ? '' : ' · ' + _faTime(d)}</span>
                <span class="up-title">${esc(r.title)}</span>
                ${r.location ? `<span class="up-loc"><i class="bi bi-geo-alt"></i>${esc(r.location)}</span>` : ''}
            </button>`;
        }).join('') : '<span class="text-muted small">قراری در ۷ روز آینده ثبت نشده است</span>';
        boxes.forEach(b => b.innerHTML = html);
    } catch (e) { /* the strip is a nicety — never block the page for it */ }
}

// ═══════════════════════════════════════════════════════════════════
//  کمد و زونکن — filing
//  An explorer: the tree of cabinets → binders → folders on one side,
//  the files of whatever is open on the other. A "file" is a property;
//  nothing is copied, only filed and marked. Files are dragged onto
//  the tree, or ticked and sent with «انتقال به…».
// ═══════════════════════════════════════════════════════════════════
const FILING_PALETTE = ['#fbbf24', '#34d399', '#38bdf8', '#a78bfa', '#f472b6',
                        '#fb923c', '#2dd4bf', '#f87171', '#a3e635', '#e879f9'];
// the shelf a cabinet can be labelled with
const FILING_ICONS = ['bi-archive', 'bi-house-door', 'bi-key', 'bi-people',
                      'bi-building', 'bi-shop', 'bi-geo-alt', 'bi-star',
                      'bi-briefcase', 'bi-folder'];
const FILING_PAGE = 60;          // one screenful of files per request
let _cabinets = [];
let _activeBinder = null;        // null = the unfiled tray; may be a folder
let _filingArchived = false;
let _filingOffset = 0;
let _filingShown = [];           // ids on screen, in order — for shift-click and «انتخاب همه»
let _selectedFiles = new Set();
let _lastPickedFile = null;
let _cabinetEditId = null, _binderEditId = null, _binderCabinetId = null, _binderParentId = null;
let _cabColor = FILING_PALETTE[0], _binColor = FILING_PALETTE[2];
let _cabIcon = FILING_ICONS[0];
let _dragIds = null;             // the files under the cursor while dragging
let _filingUnfiled = 0;          // for the tray's badge in the tree

function _paletteHtml(selected, onpick) {
    return FILING_PALETTE.map(c =>
        `<button type="button" class="filing-swatch${c === selected ? ' on' : ''}"
                 style="--c:${c}" onclick="${onpick}('${c}')" title="${c}"></button>`).join('');
}
function pickCabColor(c) { _cabColor = c; document.getElementById('cab-colors').innerHTML = _paletteHtml(c, 'pickCabColor'); }
function pickBinColor(c) { _binColor = c; document.getElementById('bin-colors').innerHTML = _paletteHtml(c, 'pickBinColor'); }

function pickCabIcon(icon) {
    _cabIcon = icon;
    const box = document.getElementById('cab-icons');
    if (box) box.innerHTML = FILING_ICONS.map(i =>
        `<button type="button" class="filing-icon-swatch${i === icon ? ' on' : ''}"
                 onclick="pickCabIcon('${i}')"><i class="bi ${i}"></i></button>`).join('');
}

async function loadFiling() {
    try {
        const [cabs, counts, tags] = await Promise.all([
            apiCall('/filing/cabinets'),
            apiCall('/filing/overview'),
            apiCall('/filing/tags'),
        ]);
        _cabinets = cabs.items || [];
        // the open box may have been renamed, emptied or deleted meanwhile
        if (_activeBinder) _activeBinder = _allBinders().find(b => b.id === _activeBinder.id) || null;
        _renderFilingCounts(counts);
        _renderTree();
        _renderTagFilter(tags.items || []);
        await loadFilingFiles();
    } catch (e) {
        showToast('خطا', 'بارگیری کمدها ناموفق بود', 'danger');
    }
}

function _renderFilingCounts(c) {
    _filingUnfiled = c.unfiled || 0;
    const box = document.getElementById('filing-counts');
    if (!box) return;
    const chip = (label, value, cls) =>
        `<span class="badge ${cls}">${label}: ${formatNumber(value || 0)}</span>`;
    box.innerHTML =
        chip('در زونکن', c.filed, 'bg-primary-subtle text-primary-emphasis') +
        chip('بدون زونکن', c.unfiled, 'bg-warning-subtle text-warning-emphasis') +
        chip('سنجاق', c.pinned, 'bg-info-subtle text-info-emphasis') +
        chip('بایگانی', c.archived, 'bg-secondary') +
        chip('شخصی', c.private, 'bg-danger-subtle text-danger-emphasis');
}

/** Every binder and folder, flat. */
function _allBinders() {
    return _cabinets.flatMap(c => (c.binders || []).flatMap(b => [b, ...(b.folders || [])]));
}
function _cabinetOf(b) { return _cabinets.find(c => c.id === b?.cabinet_id) || null; }
function _parentOf(b) { return b?.parent_id ? _allBinders().find(x => x.id === b.parent_id) || null : null; }

// ── the tree ────────────────────────────────────────────────────────
function _renderTree() {
    const wrap = document.getElementById('filing-tree');
    if (!wrap) return;
    const on = id => (_activeBinder ? _activeBinder.id === id : id === null) && !_filingArchived;
    const node = (b, depth) => `
        <div class="ftree-node${on(b.id) ? ' active' : ''} depth-${depth}" data-drop="${b.id}"
             style="--c:${esc(b.color)}" onclick="openBinder(${b.id})" title="${esc(b.name)}${b.description ? ' — ' + esc(b.description) : ''}">
            <i class="bi ${depth === 2 ? 'bi-folder-fill' : 'bi-journal-bookmark-fill'}"></i>
            <span class="ftree-name">${esc(b.name)}</span>
            <span class="ftree-count">${formatNumber(b.file_count || 0)}</span>
            <button class="ftree-edit" onclick="event.stopPropagation(); openBinderModal(${b.id}, ${b.cabinet_id})" title="ویرایش"><i class="bi bi-three-dots"></i></button>
        </div>
        ${(b.folders || []).map(f => node(f, 2)).join('')}`;
    const tray = `
        <div class="ftree-node tray${on(null) ? ' active' : ''}" data-drop="none" onclick="openBinder(null)">
            <i class="bi bi-inbox-fill"></i><span class="ftree-name">بدون زونکن</span>
            <span class="ftree-count">${formatNumber(_filingUnfiled)}</span>
        </div>`;
    if (!_cabinets.length) {
        wrap.innerHTML = tray + `<div class="text-center text-muted small py-4">
            <i class="bi bi-archive" style="font-size:1.8rem"></i>
            <p class="mt-2 mb-0">هنوز کمدی نساخته‌اید. با «+ کمد» شروع کنید.</p></div>`;
        _wireDropTargets(wrap);
        return;
    }
    wrap.innerHTML = tray + _cabinets.map(cab => `
        <div class="ftree-cabinet" style="--c:${esc(cab.color)}">
            <div class="ftree-cabinet-head">
                <i class="bi ${esc(cab.icon) || 'bi-archive'}"></i>
                <b>${esc(cab.name)}</b>
                ${cab.owner ? '<i class="bi bi-lock-fill text-danger small" title="کمد شخصی"></i>' : ''}
                <span class="ftree-count">${formatNumber(cab.file_count || 0)}</span>
                <button class="ftree-edit" onclick="openBinderModal(null, ${cab.id})" title="زونکن جدید"><i class="bi bi-plus-lg"></i></button>
                <button class="ftree-edit" onclick="openCabinetModal(${cab.id})" title="ویرایش کمد"><i class="bi bi-pencil"></i></button>
            </div>
            ${(cab.binders || []).map(b => node(b, 1)).join('') ||
              '<div class="text-muted small px-3 py-1">این کمد خالی است</div>'}
        </div>`).join('');
    _wireDropTargets(wrap);
}

/** Breadcrumb + the buttons that only make sense inside a box. */
function _renderCrumb() {
    const nav = document.getElementById('filing-crumb');
    if (!nav) return;
    const parts = [];
    if (_filingArchived) parts.push('<span class="crumb-here"><i class="bi bi-archive"></i> بایگانی</span>');
    else if (!_activeBinder) parts.push('<span class="crumb-here"><i class="bi bi-inbox"></i> فایل‌های بدون زونکن</span>');
    else {
        const cab = _cabinetOf(_activeBinder), parent = _parentOf(_activeBinder);
        if (cab) parts.push(`<span class="crumb-link" style="--c:${esc(cab.color)}"><i class="bi ${esc(cab.icon)}"></i> ${esc(cab.name)}</span>`);
        if (parent) parts.push(`<a href="#" class="crumb-link" onclick="event.preventDefault(); openBinder(${parent.id})"><i class="bi bi-journal-bookmark"></i> ${esc(parent.name)}</a>`);
        parts.push(`<span class="crumb-here" style="--c:${esc(_activeBinder.color)}"><i class="bi ${parent ? 'bi-folder-fill' : 'bi-journal-bookmark-fill'}"></i> ${esc(_activeBinder.name)}</span>`);
    }
    nav.innerHTML = parts.join('<i class="bi bi-chevron-left crumb-sep"></i>');
    // «پوشهٔ جدید» only inside a binder (folders do not nest); «ویرایش» for any open box
    document.getElementById('filing-new-folder-btn')?.classList.toggle('d-none', !_activeBinder || !!_activeBinder.parent_id || _filingArchived);
    document.getElementById('filing-edit-box-btn')?.classList.toggle('d-none', !_activeBinder || _filingArchived);
}

/** The folder chips above the files of an open binder. */
function _renderFolders() {
    const box = document.getElementById('filing-folders');
    if (!box) return;
    const folders = (!_filingArchived && _activeBinder && !_activeBinder.parent_id) ? (_activeBinder.folders || []) : [];
    box.classList.toggle('d-none', !folders.length);
    box.innerHTML = folders.map(f => `
        <div class="folder-chip" data-drop="${f.id}" style="--c:${esc(f.color)}" onclick="openBinder(${f.id})" title="باز کردن پوشه">
            <i class="bi bi-folder-fill"></i> ${esc(f.name)} <span class="ftree-count">${formatNumber(f.file_count || 0)}</span>
        </div>`).join('') + (folders.length ? `
        <div class="folder-chip is-own" data-drop="${_activeBinder.id}" title="فایل‌هایی که مستقیم در زونکن‌اند">
            <i class="bi bi-journal-bookmark"></i> خودِ زونکن <span class="ftree-count">${formatNumber(_activeBinder.own_count || 0)}</span>
        </div>` : '');
    _wireDropTargets(box);
}

function _renderTagFilter(tags) {
    const sel = document.getElementById('filing-tag');
    if (!sel) return;
    const keep = sel.value;
    sel.innerHTML = '<option value="">همه برچسب‌ها</option>' + tags.map(t =>
        `<option value="${esc(t.name)}">${esc(t.name)} (${formatNumber(t.count)})</option>`).join('');
    _selectTag(sel, keep);
}

/** A <select> silently falls back to its first option when handed a value it
 *  does not carry — which reads as "the filter cleared itself". */
function _selectTag(sel, value) {
    if (!value) { sel.value = ''; return; }
    if (![...sel.options].some(o => o.value === value)) {
        sel.insertAdjacentHTML('beforeend',
            `<option value="${esc(value)}">${esc(value)}</option>`);
    }
    sel.value = value;
}

/** «کمد › زونکن › پوشه» for every box, as [id, label] pairs for a picker. */
function _binderChoices(withNone = true) {
    const out = [];
    for (const c of _cabinets) for (const b of (c.binders || [])) {
        out.push([String(b.id), `${c.name} › ${b.name}`]);
        for (const f of (b.folders || [])) out.push([String(f.id), `${c.name} › ${b.name} › ${f.name}`]);
    }
    if (withNone) out.push(['none', '— خارج کردن از زونکن —']);
    return out;
}

function openBinder(id) {
    _activeBinder = id == null ? null : (_allBinders().find(b => b.id === id) || null);
    _setFilingArchived(false);
    _renderTree();
    loadFilingFiles();
}

/** Deleting a cabinet or binder unfiles everything behind it, so the server
 *  restricts it to an admin — hide the button rather than let it 403. */
function _canManageFiling() {
    return ['root', 'super_admin', 'admin'].includes(_currentUser?.role);
}

/** State and the button's lit/unlit look, always set together. */
function _setFilingArchived(on) {
    _filingArchived = on;
    document.getElementById('filing-archived-btn')?.classList.toggle('active', on);
}

function toggleFilingArchived() {
    _setFilingArchived(!_filingArchived);
    _renderTree();
    loadFilingFiles();
}

async function newFolderHere() {
    if (!_activeBinder || _activeBinder.parent_id) return;
    const name = await askText({
        icon: 'bi-folder-plus', title: 'پوشهٔ جدید',
        body: `داخل زونکن <b>${esc(_activeBinder.name)}</b> — مثلاً «یک‌خوابه»، «زیر ۲ میلیارد»، «فوری».`,
        field: { label: 'نام پوشه', placeholder: 'نام پوشه' },
    });
    if (!name) return;
    try {
        await apiCall('/filing/binders', { method: 'POST', body: JSON.stringify({ name, parent_id: _activeBinder.id }) });
        showToast('موفق', 'پوشه ساخته شد', 'success');
        loadFiling();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

function editOpenBox() {
    if (_activeBinder) openBinderModal(_activeBinder.id, _activeBinder.cabinet_id);
}

async function loadFilingFiles(append = false) {
    const box = document.getElementById('filing-files');
    if (!box) return;
    if (!append) {
        _filingOffset = 0;
        _filingShown = [];
        // a selection made before the filter changed would still be acted on
        // by the bulk bar while its cards are nowhere on screen
        clearFileSelection();
    }
    const search = document.getElementById('filing-search')?.value.trim() || '';
    const tag = document.getElementById('filing-tag')?.value || '';
    const advanced = _advancedFilterParams();
    const narrowed = !!(search || tag || advanced.length);

    let url = `/filing/files?limit=${FILING_PAGE}&offset=${_filingOffset}&archived=${_filingArchived}`;
    if (_activeBinder) url += `&binder_id=${_activeBinder.id}`;
    else if (!narrowed) url += '&unfiled=true';
    if (search) url += `&search=${encodeURIComponent(search)}`;
    if (tag) url += `&tag=${encodeURIComponent(tag)}`;
    if (advanced.length) url += '&' + advanced.join('&');

    // narrowing with no binder open searches every binder, so saying
    // "فایل‌های بدون زونکن" over those results would be a lie
    document.getElementById('filing-files-title').textContent = _filingArchived
        ? 'فایل‌های بایگانی‌شده'
        : (_activeBinder ? `زونکن: ${_activeBinder.name}`
                         : (narrowed ? 'نتیجهٔ جستجو در همهٔ زونکن‌ها' : 'فایل‌های بدون زونکن'));
    _renderCrumb();
    _renderFolders();
    try {
        const data = await apiCall(url);
        const items = data.items || [];
        const total = data.total || 0;
        document.getElementById('filing-files-count').textContent = formatNumber(total);
        _filingShown = append ? _filingShown.concat(items.map(f => f.id)) : items.map(f => f.id);
        if (!items.length && !append) {
            box.innerHTML = `<div class="text-center text-muted py-4">
                <i class="bi bi-inbox" style="font-size:2rem"></i>
                <p class="mt-2">${narrowed ? 'چیزی با این فیلتر پیدا نشد' : 'فایلی اینجا نیست'}</p>
                ${!narrowed && !_activeBinder && !_filingArchived ? '<p class="small">همهٔ فایل‌ها دسته‌بندی شده‌اند 👌</p>' : ''}</div>`;
            _renderFilingMore(0, 0);
            return;
        }
        const html = items.map(_fileCard).join('');
        if (append) box.insertAdjacentHTML('beforeend', html);
        else box.innerHTML = html;
        _renderFilingMore(_filingOffset + items.length, total);
        _updateFileBulkBar();
    } catch (e) {
        showToast('خطا', 'بارگیری فایل‌ها ناموفق بود', 'danger');
    }
}

/** The count badge shows the true total, so without this a binder of 200
 *  files claimed 200 and quietly drew 60. */
function _renderFilingMore(shown, total) {
    const btn = document.getElementById('filing-load-more');
    if (!btn) return;
    const remaining = total - shown;
    btn.classList.toggle('d-none', remaining <= 0);
    btn.innerHTML = `<i class="bi bi-arrow-down-circle"></i>
        نمایش ${formatNumber(Math.min(remaining, FILING_PAGE))} فایل بعدی
        <span class="text-muted">(${formatNumber(shown)} از ${formatNumber(total)})</span>`;
}

function loadMoreFilingFiles() {
    _filingOffset += FILING_PAGE;
    loadFilingFiles(true);
}

function _fileCard(f) {
    const marks = [];
    if (f.is_pinned)   marks.push('<i class="bi bi-pin-angle-fill file-mark pin" title="سنجاق‌شده"></i>');
    if (f.is_private)  marks.push('<i class="bi bi-lock-fill file-mark private" title="فایل شخصی"></i>');
    if (f.is_archived) marks.push('<i class="bi bi-archive-fill file-mark arch" title="بایگانی"></i>');
    if (f.is_draft)    marks.push('<i class="bi bi-pencil-square file-mark draft" title="پیش‌نویس"></i>');
    // the tag rides in a data attribute, not inside the handler's quotes —
    // esc() turns «'» into «&#39;», which the parser hands back to JS as a
    // quote and breaks the call
    const tags = (f.tags || []).map(t =>
        `<span class="file-tag" data-tag="${esc(t)}"
               onclick="event.stopPropagation(); filterByTag(this.dataset.tag)">${esc(t)}</span>`).join('');
    // inside a binder, say which folder the file sits in
    const where = f.binder_id && f.binder_id !== _activeBinder?.id ? _allBinders().find(b => b.id === f.binder_id) : null;
    const price = f.listing_type === 'rent'
        ? `${f.deposit ? 'رهن ' + formatPrice(f.deposit) : ''}${f.deposit && f.rent_price ? ' · ' : ''}${f.rent_price ? 'اجاره ' + formatPrice(f.rent_price) : ''}` || '—'
        : (f.price ? formatPrice(f.price) : '—');
    const sel = _selectedFiles.has(f.id);
    return `<div class="file-card${sel ? ' selected' : ''}" data-id="${f.id}" draggable="true"
                 onclick="onFileCardClick(event, ${f.id}, this)" ondblclick="viewProperty(${f.id})"
                 ondragstart="onFileDragStart(event, ${f.id})" ondragend="onFileDragEnd()">
        <div class="file-card-head">
            <input type="checkbox" class="form-check-input file-check" ${sel ? 'checked' : ''}
                   onclick="event.stopPropagation(); toggleFileSelection(${f.id}, this.closest('.file-card'))" title="انتخاب">
            <span class="serial-badge">${formatSerial(f.serial_no)}</span>
            <div class="file-marks">${marks.join('')}</div>
        </div>
        <div class="file-card-title" title="${esc(f.title)}">${esc(f.title)}</div>
        <div class="file-card-meta">
            ${f.area ? formatNumber(f.area) + ' متر' : ''}
            ${f.rooms != null ? ' • ' + formatNumber(f.rooms) + ' خواب' : ''}
            ${f.district ? ' • ' + esc(f.district) : (f.city_name ? ' • ' + esc(f.city_name) : '')}
        </div>
        <div class="file-card-price">${price}</div>
        ${where ? `<div class="file-where" style="--c:${esc(where.color)}"><i class="bi ${where.parent_id ? 'bi-folder-fill' : 'bi-journal-bookmark-fill'}"></i> ${esc(where.name)}</div>` : ''}
        ${tags ? `<div class="file-tags">${tags}</div>` : ''}
        <div class="file-card-actions" onclick="event.stopPropagation()">
            <button class="btn btn-sm btn-outline-primary" onclick="viewProperty(${f.id})" title="جزئیات"><i class="bi bi-eye"></i></button>
            <button class="btn btn-sm btn-outline-primary" onclick="openFileEdit(${f.id})" title="ویرایش فایل"><i class="bi bi-pencil"></i></button>
            <button class="btn btn-sm btn-outline-success" onclick="moveFilePick(${f.id})" title="انتقال به زونکن / پوشه"><i class="bi bi-folder-symlink"></i></button>
            <button class="btn btn-sm btn-outline-info" onclick="quickFileAction(${f.id}, '${f.is_pinned ? 'unpin' : 'pin'}')" title="سنجاق"><i class="bi bi-pin-angle"></i></button>
            <button class="btn btn-sm btn-outline-warning" onclick="quickFileAction(${f.id}, '${f.is_archived ? 'unarchive' : 'archive'}')" title="بایگانی"><i class="bi bi-archive"></i></button>
            <button class="btn btn-sm btn-outline-success" onclick="showSimilarForProperty(${f.id})" title="ملک‌های مشابه"><i class="bi bi-diagram-3"></i></button>
            <button class="btn btn-sm btn-outline-warning" onclick="showCustomersForProperty(${f.id})" title="متقاضیان هم‌خوان"><i class="bi bi-person-check"></i></button>
            <button class="btn btn-sm btn-outline-secondary" onclick="shareFile(${f.id})" title="اشتراک‌گذاری با مشتری"><i class="bi bi-share"></i></button>
        </div>
    </div>`;
}

function filterByTag(tag) {
    const sel = document.getElementById('filing-tag');
    if (!sel) return;
    _selectTag(sel, tag);
    loadFilingFiles();
}

// ── selecting ───────────────────────────────────────────────────────
// click = toggle, shift+click = the run since the last pick, double-click = open.
function onFileCardClick(e, id, el) {
    if (e.shiftKey && _lastPickedFile != null && _filingShown.includes(_lastPickedFile)) {
        const a = _filingShown.indexOf(_lastPickedFile), b = _filingShown.indexOf(id);
        const [lo, hi] = a < b ? [a, b] : [b, a];
        for (const fid of _filingShown.slice(lo, hi + 1)) _selectedFiles.add(fid);
        _paintSelection();
        return;
    }
    toggleFileSelection(id, el);
}
function toggleFileSelection(id, el) {
    if (_selectedFiles.has(id)) _selectedFiles.delete(id); else _selectedFiles.add(id);
    _lastPickedFile = id;
    _paintSelection();
}
function selectAllFiles(on) {
    if (on) _filingShown.forEach(id => _selectedFiles.add(id)); else _selectedFiles.clear();
    _paintSelection();
}
function clearFileSelection() {
    _selectedFiles.clear();
    _paintSelection();
}
function _paintSelection() {
    document.querySelectorAll('.file-card').forEach(c => {
        const on = _selectedFiles.has(Number(c.dataset.id));
        c.classList.toggle('selected', on);
        const chk = c.querySelector('.file-check');
        if (chk) chk.checked = on;
    });
    const all = document.getElementById('filing-select-all');
    if (all) all.checked = _filingShown.length > 0 && _filingShown.every(id => _selectedFiles.has(id));
    _updateFileBulkBar();
}
function _updateFileBulkBar() {
    const bar = document.getElementById('filing-bulk-bar');
    if (!bar) return;
    bar.classList.toggle('d-none', _selectedFiles.size === 0);
    const n = document.getElementById('filing-bulk-count');
    if (n) n.textContent = formatNumber(_selectedFiles.size);
}

// ── drag & drop onto the tree / folder chips ───────────────────────
function onFileDragStart(e, id) {
    // dragging a ticked card carries the whole selection
    _dragIds = _selectedFiles.has(id) ? [..._selectedFiles] : [id];
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', String(id)); } catch (_) {}
    document.body.classList.add('filing-dragging');
    const ghost = document.getElementById('filing-drag-ghost') || document.body.appendChild(Object.assign(document.createElement('div'), { id: 'filing-drag-ghost', className: 'filing-drag-ghost' }));
    ghost.textContent = _dragIds.length > 1 ? `${formatNumber(_dragIds.length)} فایل` : 'فایل';
    try { e.dataTransfer.setDragImage(ghost, 20, 20); } catch (_) {}
}
function onFileDragEnd() {
    _dragIds = null;
    document.body.classList.remove('filing-dragging');
    document.querySelectorAll('.drop-over').forEach(el => el.classList.remove('drop-over'));
}
function _wireDropTargets(root) {
    root.querySelectorAll('[data-drop]').forEach(el => {
        if (el.dataset.dropWired) return;
        el.dataset.dropWired = '1';
        el.addEventListener('dragover', e => { if (_dragIds) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; el.classList.add('drop-over'); } });
        el.addEventListener('dragleave', () => el.classList.remove('drop-over'));
        el.addEventListener('drop', e => {
            e.preventDefault(); el.classList.remove('drop-over');
            if (!_dragIds) return;
            const target = el.dataset.drop;
            _moveFiles(_dragIds, target === 'none' ? null : Number(target));
            onFileDragEnd();
        });
    });
}

async function _moveFiles(ids, binderId) {
    const box = binderId ? _allBinders().find(b => b.id === binderId) : null;
    await _fileBulk({ ids, action: 'move', binder_id: binderId },
                    binderId ? `به «${box?.name || 'زونکن'}» منتقل شد` : 'از زونکن خارج شد');
}

/** A picker listing every box as «کمد › زونکن › پوشه». */
async function _pickBinder(title, body) {
    // reachable from the lead modal before the filing tab was ever opened
    if (!_cabinets.length) {
        try { _cabinets = (await apiCall('/filing/cabinets')).items || []; } catch (_) {}
    }
    if (!_cabinets.length) { showToast('راهنما', 'اول در تب «کمد و زونکن» یک کمد و زونکن بسازید', 'info'); return undefined; }
    const v = await _askOpen({
        icon: 'bi-folder-symlink', title, body, okLabel: 'انتقال',
        field: { label: 'مقصد', options: _binderChoices(true), value: _activeBinder ? String(_activeBinder.id) : undefined },
    });
    if (v == null || v === '') return undefined;
    return v === 'none' ? null : Number(v);
}
async function moveFilePick(id) {
    const target = await _pickBinder('انتقال فایل', 'این فایل به کدام زونکن یا پوشه برود؟');
    if (target !== undefined) await _moveFiles([id], target);
}
async function bulkMovePick() {
    if (!_selectedFiles.size) return;
    const target = await _pickBinder('انتقال گروهی', `<b>${formatNumber(_selectedFiles.size)}</b> فایل انتخاب‌شده به کدام زونکن یا پوشه بروند؟`);
    if (target !== undefined) await _moveFiles([..._selectedFiles], target);
}

const FILE_ACTION_FA = {
    pin: 'سنجاق شد', unpin: 'سنجاق برداشته شد',
    archive: 'بایگانی شد', unarchive: 'از بایگانی خارج شد',
    private: 'شخصی شد', public: 'عمومی شد',
    draft: 'پیش‌نویس شد', undraft: 'از پیش‌نویس خارج شد',
    tag: 'برچسب خورد', untag: 'برچسب برداشته شد',
};

async function _fileBulk(body, okMsg) {
    try {
        const r = await apiCall('/filing/files/bulk', { method: 'POST', body: JSON.stringify(body) });
        let msg = `${okMsg} (${formatNumber(r.updated)} فایل)`;
        // the server drops files the caller may not see rather than failing
        if (r.skipped) msg += ` — ${formatNumber(r.skipped)} فایل تغییر نکرد (شخصیِ همکار دیگر یا حذف‌شده)`;
        showToast('موفق', msg, 'success');
        clearFileSelection();
        await loadFiling();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}
async function quickFileAction(id, action) {
    await _fileBulk({ ids: [id], action }, FILE_ACTION_FA[action] || 'انجام شد');
}
async function bulkFileAction(action) {
    if (!action || !_selectedFiles.size) return;
    if (action === 'tag' || action === 'untag') return bulkTagFiles(action);
    const n = formatNumber(_selectedFiles.size);
    if (action === 'archive' && !await askConfirm({ icon: 'bi-question-lg', title: 'تأیید', okLabel: 'تأیید', body: `${n} فایل بایگانی شود؟` })) return;
    if (action === 'private' && !await askConfirm({
        icon: 'bi-eye-slash', title: 'شخصی کردن فایل', okLabel: 'شخصی کن',
        body: `<b>${n}</b> فایل شخصی شود؟`,
        note: 'از این پس فقط شما و مدیر ارشد آن را می‌بینید.',
    })) return;
    await _fileBulk({ ids: [..._selectedFiles], action }, FILE_ACTION_FA[action] || 'انجام شد');
}
async function bulkMoveFiles(binderId) {
    if (!binderId || !_selectedFiles.size) return;
    await _moveFiles([..._selectedFiles], binderId === 'none' ? null : Number(binderId));
}
async function bulkTagFiles(action = 'tag') {
    if (!_selectedFiles.size) return;
    const adding = action === 'tag';
    const tags = await askText({
        icon: adding ? 'bi-tags' : 'bi-tag',
        title: adding ? 'افزودن برچسب' : 'برداشتن برچسب',
        body: `روی <b>${formatNumber(_selectedFiles.size)}</b> فایل انتخاب‌شده اعمال می‌شود.`,
        field: { label: 'برچسب‌ها', placeholder: 'قرارداد، اسکن، ۱۴۰۵',
                 hint: 'چند برچسب را با ویرگول جدا کنید' },
    });
    if (!tags || !tags.trim()) return;
    await _fileBulk({ ids: [..._selectedFiles], action, tags }, FILE_ACTION_FA[action]);
}

// ── ویرایش فایل ─────────────────────────────────────────────────────
let _fileEditId = null;
function _feTogglePrice() {
    const rent = document.getElementById('fe-listing').value === 'rent';
    document.querySelectorAll('#fileEditModal .fe-rent').forEach(el => el.classList.toggle('d-none', !rent));
    document.querySelectorAll('#fileEditModal .fe-buy').forEach(el => el.classList.toggle('d-none', rent));
}
async function openFileEdit(id) {
    try {
        const f = await apiCall(`/filing/files/${id}`);
        _fileEditId = id;
        const set = (k, v) => { const el = document.getElementById(k); if (el) el.value = v ?? ''; };
        const chk = (k, v) => { const el = document.getElementById(k); if (el) el.checked = !!v; };
        document.getElementById('fe-serial').textContent = formatSerial(f.serial_no);
        set('fe-title', f.title); set('fe-listing', f.listing_type === 'rent' ? 'rent' : 'buy');
        set('fe-ptype', f.property_type || f.category_name);
        set('fe-total', f.total_price || f.price ? _groupMoney(String(f.total_price || f.price)) : '');
        set('fe-deposit', f.deposit ? _groupMoney(String(f.deposit)) : '');
        set('fe-rent', f.rent_price ? _groupMoney(String(f.rent_price)) : '');
        set('fe-area', f.area); set('fe-rooms', f.rooms); set('fe-floor', f.floor); set('fe-year', f.year_built);
        set('fe-district', f.district); set('fe-address', f.address);
        set('fe-seller', f.seller_name); set('fe-phone', f.phone_number);
        chk('fe-elevator', f.has_elevator); chk('fe-parking', f.has_parking);
        chk('fe-storage', f.has_storage); chk('fe-balcony', f.has_balcony);
        chk('fe-pinned', f.is_pinned); chk('fe-private', f.is_private);
        set('fe-tags', Array.isArray(f.tags) ? f.tags.join('، ') : (f.tags || ''));
        set('fe-description', f.description);
        const sel = document.getElementById('fe-binder');
        sel.innerHTML = '<option value="">— بدون زونکن —</option>' +
            _binderChoices(false).map(([v, l]) => `<option value="${v}"${Number(v) === f.binder_id ? ' selected' : ''}>${esc(l)}</option>`).join('');
        document.getElementById('fe-view-btn').onclick = () => viewProperty(id);
        _feTogglePrice();
        initMoneyInputs(document.getElementById('fileEditModal'));
        bootstrap.Modal.getOrCreateInstance(document.getElementById('fileEditModal')).show();
    } catch (e) { showToast('خطا', e.message || 'بارگیری فایل ناموفق بود', 'danger'); }
}
async function saveFileEdit() {
    if (!_fileEditId) return;
    const v = k => document.getElementById(k)?.value.trim() ?? '';
    const on = k => !!document.getElementById(k)?.checked;
    const title = v('fe-title');
    if (!title) { showToast('خطا', 'عنوان فایل الزامی است', 'warning'); return; }
    const rent = v('fe-listing') === 'rent';
    const body = {
        title, listing_type: v('fe-listing'), property_type: v('fe-ptype'),
        total_price: rent ? null : _intOrNull('fe-total'),
        deposit: rent ? _intOrNull('fe-deposit') : null,
        rent_price: rent ? _intOrNull('fe-rent') : null,
        area: v('fe-area'), rooms: v('fe-rooms'), floor: v('fe-floor'), year_built: v('fe-year'),
        district: v('fe-district'), address: v('fe-address'),
        seller_name: v('fe-seller'), phone_number: v('fe-phone'),
        has_elevator: on('fe-elevator'), has_parking: on('fe-parking'),
        has_storage: on('fe-storage'), has_balcony: on('fe-balcony'),
        is_pinned: on('fe-pinned'), is_private: on('fe-private'),
        tags: v('fe-tags'), description: v('fe-description'),
        binder_id: v('fe-binder') ? Number(v('fe-binder')) : null,
    };
    try {
        await apiCall(`/filing/files/${_fileEditId}`, { method: 'PATCH', body: JSON.stringify(body) });
        showToast('موفق', 'فایل ذخیره شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('fileEditModal'))?.hide();
        loadFiling();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

// ── cabinet / binder modals ────────────────────────────────────────
function openCabinetModal(id = null) {
    _cabinetEditId = id;
    const cab = id ? _cabinets.find(c => c.id === id) : null;
    document.getElementById('cabinet-modal-title').innerHTML = id
        ? '<i class="bi bi-archive"></i> ویرایش کمد' : '<i class="bi bi-archive"></i> کمد جدید';
    document.getElementById('cab-name').value = cab?.name || '';
    document.getElementById('cab-delete-btn').classList.toggle('d-none', !id || !_canManageFiling());
    const chk = document.getElementById('cab-personal');
    if (chk) chk.checked = !!cab?.owner;
    pickCabColor(cab?.color || FILING_PALETTE[0]);
    pickCabIcon(cab?.icon || FILING_ICONS[0]);
    bootstrap.Modal.getOrCreateInstance(document.getElementById('cabinetModal')).show();
}

async function saveCabinet() {
    const name = document.getElementById('cab-name').value.trim();
    if (!name) { showToast('خطا', 'نام کمد الزامی است', 'warning'); return; }
    const body = JSON.stringify({
        name, color: _cabColor, icon: _cabIcon,
        personal: !!document.getElementById('cab-personal')?.checked,
    });
    try {
        if (_cabinetEditId) await apiCall(`/filing/cabinets/${_cabinetEditId}`, { method: 'PATCH', body });
        else await apiCall('/filing/cabinets', { method: 'POST', body });
        showToast('موفق', 'کمد ذخیره شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('cabinetModal'))?.hide();
        loadFiling();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function deleteCabinet() {
    if (!_cabinetEditId) return;
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف', body: 'این کمد و زونکن‌هایش حذف شوند؟ فایل‌ها حذف نمی‌شوند، فقط از زونکن خارج می‌شوند.' })) return;
    try {
        const r = await apiCall(`/filing/cabinets/${_cabinetEditId}`, { method: 'DELETE' });
        showToast('موفق', `کمد حذف شد — ${formatNumber(r.unfiled)} فایل بدون زونکن شد`, 'success');
        bootstrap.Modal.getInstance(document.getElementById('cabinetModal'))?.hide();
        _activeBinder = null;
        loadFiling();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

/** One modal for a binder and for a folder: a folder is a binder with a
 *  parent, so it has no kind or deal of its own (it inherits them). */
function openBinderModal(id = null, cabinetId = null, parentId = null) {
    _binderEditId = id;
    _binderCabinetId = cabinetId;
    const bin = id ? _allBinders().find(b => b.id === id) : null;
    _binderParentId = bin ? bin.parent_id : parentId;
    const folder = !!_binderParentId;
    document.getElementById('binder-modal-title').innerHTML = id
        ? `<i class="bi ${folder ? 'bi-folder' : 'bi-journal-bookmark'}"></i> ${folder ? 'ویرایش پوشه' : 'ویرایش زونکن'}`
        : `<i class="bi ${folder ? 'bi-folder-plus' : 'bi-journal-plus'}"></i> ${folder ? 'پوشهٔ جدید' : 'زونکن جدید'}`;
    document.getElementById('bin-name').value = bin?.name || '';
    document.getElementById('bin-kind').value = bin?.kind || 'property';
    document.getElementById('bin-deal').value = bin?.deal_type || '';
    document.getElementById('bin-description').value = bin?.description || '';
    document.getElementById('bin-kind-col')?.classList.toggle('d-none', folder);
    document.getElementById('bin-deal-col')?.classList.toggle('d-none', folder);
    const del = document.getElementById('bin-delete-btn');
    del.classList.toggle('d-none', !id || !_canManageFiling());
    del.innerHTML = `<i class="bi bi-trash"></i> ${folder ? 'حذف پوشه' : 'حذف زونکن'}`;
    pickBinColor(bin?.color || FILING_PALETTE[2]);
    bootstrap.Modal.getOrCreateInstance(document.getElementById('binderModal')).show();
}

async function saveBinder() {
    const name = document.getElementById('bin-name').value.trim();
    if (!name) { showToast('خطا', 'نام زونکن الزامی است', 'warning'); return; }
    const body = JSON.stringify({
        name, color: _binColor,
        kind: document.getElementById('bin-kind').value,
        deal_type: document.getElementById('bin-deal').value,
        description: document.getElementById('bin-description').value.trim(),
        cabinet_id: _binderCabinetId,
        parent_id: _binderParentId || null,
    });
    try {
        if (_binderEditId) await apiCall(`/filing/binders/${_binderEditId}`, { method: 'PATCH', body });
        else await apiCall('/filing/binders', { method: 'POST', body });
        showToast('موفق', _binderParentId ? 'پوشه ذخیره شد' : 'زونکن ذخیره شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('binderModal'))?.hide();
        loadFiling();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function deleteBinder() {
    if (!_binderEditId) return;
    const folder = !!_binderParentId;
    if (!await askConfirm({ icon: 'bi-trash3', title: 'حذف', tone: 'danger', okLabel: 'حذف',
        body: folder ? 'این پوشه حذف شود؟ فایل‌ها حذف نمی‌شوند، فقط از پوشه خارج می‌شوند.'
                     : 'این زونکن و پوشه‌هایش حذف شوند؟ فایل‌ها حذف نمی‌شوند، فقط از زونکن خارج می‌شوند.' })) return;
    try {
        const r = await apiCall(`/filing/binders/${_binderEditId}`, { method: 'DELETE' });
        showToast('موفق', `${folder ? 'پوشه' : 'زونکن'} حذف شد — ${formatNumber(r.unfiled)} فایل بدون زونکن شد`, 'success');
        bootstrap.Modal.getInstance(document.getElementById('binderModal'))?.hide();
        if (_activeBinder?.id === _binderEditId) _activeBinder = folder ? (_parentOf(_activeBinder) || null) : null;
        loadFiling();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

// ── جستجوی پیشرفته ─────────────────────────────────────────────────
function toggleAdvancedFilters() {
    document.getElementById('filing-advanced')?.classList.toggle('d-none');
}

/** Only the boxes that were actually filled become query parameters. */
function _advancedFilterParams() {
    const v = id => document.getElementById(id)?.value.trim() || '';
    const on = id => !!document.getElementById(id)?.checked;
    const parts = [];
    // _intOrNull takes the element id, not its value — it reads the field
    // itself so it can strip the «/» separators the money inputs add
    const money = id => { const n = _intOrNull(id); return n != null ? n : ''; };
    const pairs = [
        ['price_min', money('ff-price-min')], ['price_max', money('ff-price-max')],
        ['area_min', v('ff-area-min')], ['area_max', v('ff-area-max')],
        ['rooms_min', v('ff-rooms-min')], ['district', v('ff-district')],
        ['property_type', v('ff-type')], ['listing_type', v('ff-listing')],
    ];
    for (const [k, val] of pairs) if (val !== '' && val != null) parts.push(`${k}=${encodeURIComponent(val)}`);
    for (const [k, id] of [['has_elevator','ff-elevator'], ['has_parking','ff-parking'], ['has_storage','ff-storage']])
        if (on(id)) parts.push(`${k}=true`);

    const badge = document.getElementById('filing-filter-count');
    if (badge) {
        badge.textContent = formatNumber(parts.length);
        badge.classList.toggle('d-none', parts.length === 0);
    }
    return parts;
}

function clearAdvancedFilters() {
    ['ff-price-min','ff-price-max','ff-area-min','ff-area-max','ff-rooms-min','ff-district','ff-type']
        .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    const l = document.getElementById('ff-listing'); if (l) l.value = '';
    ['ff-elevator','ff-parking','ff-storage']
        .forEach(id => { const el = document.getElementById(id); if (el) el.checked = false; });
    loadFilingFiles();
}

// ── اشتراک‌گذاری امن ────────────────────────────────────────────────
let _shareText = '';

async function shareFile(propertyId) {
    try {
        const card = await apiCall(`/filing/files/${propertyId}/share`);
        _shareText = card.text || '';
        document.getElementById('share-text').value = _shareText;

        const FA = { phone_number: 'شماره مالک', seller_name: 'نام مالک',
                     owner_phone: 'شماره ثبت‌کننده', url: 'لینک آگهی', address: 'آدرس دقیق' };
        const removed = (card.removed || []).map(k => FA[k] || k);
        document.getElementById('share-removed').textContent = removed.length
            ? `از این متن حذف شد: ${removed.join('، ')} — مشتری نمی‌تواند مستقیم با مالک تماس بگیرد.`
            : 'اطلاعات محرمانه‌ای برای حذف در این فایل نبود.';

        const strip = document.getElementById('share-images');
        strip.innerHTML = (card.images || []).map(src =>
            `<div class="lead-photo-thumb" style="width:80px;height:80px;cursor:zoom-in">
                <img src="${safeUrl(src)}" alt="تصویر" onclick="openImageLightbox(this.src)"></div>`).join('');

        const enc = encodeURIComponent(_shareText);
        document.getElementById('share-whatsapp').href = `https://wa.me/?text=${enc}`;
        document.getElementById('share-telegram').href = `https://t.me/share/url?url=&text=${enc}`;
        bootstrap.Modal.getOrCreateInstance(document.getElementById('shareModal')).show();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function copyShareText() {
    const text = document.getElementById('share-text')?.value || _shareText;
    try {
        await navigator.clipboard.writeText(text);
        showToast('کپی شد', 'متن آمادهٔ ارسال است', 'success');
    } catch (e) {
        // clipboard is blocked outside https — fall back to selecting it
        document.getElementById('share-text')?.select();
        showToast('انتخاب شد', 'با Ctrl+C کپی کنید', 'info');
    }
}

async function shareViaSms() {
    const to = await askText({
        icon: 'bi-chat-dots', title: 'ارسال پیامک',
        body: 'پیامک به این شماره فرستاده می‌شود.',
        field: { label: 'شمارهٔ گیرنده', placeholder: '09123456789',
                 dir: 'ltr', inputmode: 'numeric',
                 validate: v => /^0?9\d{9}$/.test(v.replace(/\D/g, '')) ? '' : 'شمارهٔ موبایل معتبر نیست' },
    });
    if (!to || !to.trim()) return;
    const message = document.getElementById('share-text')?.value || _shareText;
    try {
        await apiCall('/crm/sms/send', {
            method: 'POST', body: JSON.stringify({ to_number: to.trim(), message })
        });
        showToast('موفق', 'پیامک ارسال شد', 'success');
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

/** «متقاضیان هم‌خوان» — who was already looking for a file like this. */
function showCustomersForProperty(propertyId) {
    _openMatchModal('<i class="bi bi-person-check"></i> متقاضیان هم‌خوان',
        `/crm/match/property/${propertyId}/customers?limit=12`,
        'مشتری‌ای با این مشخصات دنبال ملک نبوده است.');
}

/** A matched customer, rendered in the same modal as matched listings. */
function _customerMatchCard(m) {
    const reasons = (m.reasons || []).slice(0, 3)
        .map(r => `<span class="match-tag">${esc(r)}</span>`).join('');
    const scoreCls = m.score >= 75 ? 'high' : m.score >= 50 ? 'mid' : 'low';
    const temp = { hot: ['داغ', 'bg-danger'], warm: ['گرم', 'bg-warning text-dark'],
                   cold: ['سرد', 'bg-secondary'] }[m.temperature] || ['', ''];
    const phone = m.mobile1 || m.mobile2;
    return `
    <div class="match-card">
        <div class="match-score ${scoreCls}">${formatNumber(m.score)}<small>٪</small></div>
        <div class="match-body">
            <div class="match-title">${esc(m.full_name)}
                ${temp[0] ? `<span class="badge ${temp[1]}">${temp[0]}</span>` : ''}</div>
            <div class="match-meta">
                ${m.desired_district ? esc(m.desired_district) : ''}
                ${m.desired_specs ? ' · ' + esc(m.desired_specs) : ''}
                ${m.consultant_name ? ' · مشاور: ' + esc(m.consultant_name) : ''}
            </div>
            <div class="match-tags">${reasons}</div>
        </div>
        <div class="match-side">
            <div class="match-price">${m.budget_max ? formatPrice(m.budget_max) : '—'}</div>
            ${phone ? `<a href="tel:${esc(phone)}" class="btn btn-sm btn-outline-success">
                <i class="bi bi-telephone"></i> ${esc(phone)}</a>` : ''}
        </div>
    </div>`;
}

// ══════════ Portal: visitor requests + upgrade tickets (super_admin/admin) ══════════
// Server data is written through esc() before it reaches innerHTML. Everything
// here is user-supplied — a visitor types it and staff render it — which is
// exactly the shape that turns into stored XSS when it is skipped.

let _permCatalog = null;

async function loadPermCatalog() {
    if (_permCatalog) return _permCatalog;
    try {
        const data = await apiCall('/users/permissions/catalog');
        _permCatalog = data.items || [];
    } catch (_) { _permCatalog = []; }
    return _permCatalog;
}

function renderPermBox(containerId, selected, namePrefix) {
    const box = document.getElementById(containerId);
    if (!box) return;
    const chosen = new Set(selected || []);
    box.innerHTML = (_permCatalog || []).map(p => `
        <label>
            <input type="checkbox" value="${esc(p.key)}" id="${esc(namePrefix)}-${esc(p.key)}"
                   ${chosen.has(p.key) ? 'checked' : ''}>
            <span>${esc(p.label)}</span>
        </label>`).join('');
}

function readPermBox(containerId) {
    const box = document.getElementById(containerId);
    if (!box) return [];
    return Array.from(box.querySelectorAll('input[type=checkbox]:checked')).map(i => i.value);
}

function syncNewUserPerms() {
    const role = document.getElementById('new-role')?.value;
    const wrap = document.getElementById('new-perms-wrap');
    if (wrap) wrap.style.display = role === 'admin' ? '' : 'none';
}

async function initPermsUI() {
    await loadPermCatalog();
    renderPermBox('new-perms-box', (await loadPermCatalog()).length ? [] : [], 'np');
    syncNewUserPerms();
}

// ── upgrade tickets ──────────────────────────────────────────────────────────
async function loadTickets() {
    const box = document.getElementById('tickets-box');
    const countEl = document.getElementById('tickets-count');
    if (!box) return;
    try {
        await loadPermCatalog();
        const { items } = await apiCall('/portal/admin/tickets?status=pending');
        if (countEl) countEl.textContent = _faNum ? _faNum(items.length) : String(items.length);
        if (!items.length) {
            box.innerHTML = '<p class="text-muted small mb-0">درخواست جدیدی وجود ندارد.</p>';
            return;
        }
        box.innerHTML = items.map(t => `
            <div class="border rounded p-2 mb-2">
              <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
                <div>
                  <strong>${esc(t.user?.full_name || '—')}</strong>
                  <span class="text-muted small" dir="ltr">${esc(t.user?.phone || '')}</span>
                </div>
                <span class="badge bg-warning text-dark">در انتظار بررسی</span>
              </div>
              ${t.message ? `<div class="small mt-1">${esc(t.message)}</div>` : ''}
              <div class="mt-2">
                <label class="form-label small mb-1">دسترسی‌هایی که داده می‌شود</label>
                <div id="tk-perms-${t.id}" class="perm-box"></div>
              </div>
              <div class="d-flex gap-2 mt-2">
                <button class="btn btn-success btn-sm" onclick="decideTicket(${t.id}, true)">
                  <i class="bi bi-check-lg"></i> تأیید و ارتقا به مدیر
                </button>
                <button class="btn btn-outline-danger btn-sm" onclick="decideTicket(${t.id}, false)">
                  <i class="bi bi-x-lg"></i> رد
                </button>
              </div>
            </div>`).join('');
        items.forEach(t => renderPermBox(`tk-perms-${t.id}`, [], `tk${t.id}`));
    } catch (e) {
        box.innerHTML = `<p class="text-danger small mb-0">${esc(e.message)}</p>`;
    }
}

async function decideTicket(id, approve) {
    if (!await askConfirm(approve ? {
        icon: 'bi-person-check', title: 'ارتقا به مدیر', okLabel: 'ارتقا بده',
        body: 'این کاربر به نقش «مدیر» ارتقا می‌یابد.',
        note: 'دسترسی‌های او را بعداً می‌توانید در همین صفحه محدود کنید.',
    } : {
        icon: 'bi-person-x', title: 'رد درخواست', tone: 'warning', okLabel: 'رد کن',
        body: 'این درخواست رد شود؟',
    })) return;
    try {
        await apiCall(`/portal/admin/tickets/${id}/decide`, {
            method: 'POST',
            body: JSON.stringify({ approve, permissions: approve ? readPermBox(`tk-perms-${id}`) : null }),
        });
        showToast('انجام شد', approve ? 'کاربر به مدیر ارتقا یافت' : 'درخواست رد شد', 'success');
        await loadTickets();
        if (typeof loadUsers === 'function') loadUsers();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

// ── visitor property requests ────────────────────────────────────────────────
const PORTAL_STATUS = {
    new:       { label: 'ثبت شده',        cls: 'bg-primary' },
    in_review: { label: 'در حال بررسی',   cls: 'bg-warning text-dark' },
    matched:   { label: 'مورد پیدا شد',   cls: 'bg-success' },
    contacted: { label: 'تماس گرفته شد',  cls: 'bg-info text-dark' },
    closed:    { label: 'بسته شده',       cls: 'bg-secondary' },
};

function _money(n) {
    if (!n) return '—';
    return (typeof _faNum === 'function' ? _faNum(Number(n).toLocaleString('en-US')) : Number(n).toLocaleString('fa-IR'));
}

async function loadPortalRequests() {
    const body = document.getElementById('portal-requests-body');
    if (!body) return;
    const status = document.getElementById('portal-status-filter')?.value || '';
    body.innerHTML = '<tr><td colspan="6" class="text-center text-muted p-4">در حال بارگذاری…</td></tr>';
    try {
        const { items } = await apiCall(`/portal/admin/requests${status ? '?status=' + encodeURIComponent(status) : ''}`);
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="6" class="text-center text-muted p-4">درخواستی ثبت نشده است.</td></tr>';
            return;
        }
        body.innerHTML = items.map(r => {
            const st = PORTAL_STATUS[r.status] || { label: r.status, cls: 'bg-secondary' };
            const want = [r.deal_type === 'rent' ? 'اجاره' : 'خرید', r.city, r.districts]
                .filter(Boolean).map(esc).join(' • ');
            const budget = r.deal_type === 'rent'
                ? `ودیعه تا ${_money(r.deposit_max)} / اجاره تا ${_money(r.rent_max)}`
                : `${_money(r.budget_min)} تا ${_money(r.budget_max)}`;
            return `<tr>
                <td><div>${esc(r.contact_name || r.user?.full_name || '—')}</div>
                    <a class="small text-muted" dir="ltr" href="tel:${esc(r.contact_phone || '')}">${esc(r.contact_phone || '')}</a></td>
                <td>${want}${r.description ? `<div class="small text-muted">${esc(r.description.slice(0, 90))}</div>` : ''}</td>
                <td class="small">${esc(budget)}</td>
                <td><span class="badge ${st.cls}">${esc(st.label)}</span>
                    ${r.customer_id ? `<div class="small text-muted mt-1"><i class="bi bi-bullseye"></i> در موتور تطبیق</div>` : ''}</td>
                <td class="small">${esc((r.created_at || '').slice(0, 10))}</td>
                <td>
                  <div class="d-flex gap-1 align-items-center flex-wrap">
                    <select class="form-select form-select-sm" style="width:auto"
                            onchange="updatePortalRequest(${r.id}, this.value)">
                      ${Object.entries(PORTAL_STATUS).map(([k, v]) =>
                          `<option value="${esc(k)}" ${k === r.status ? 'selected' : ''}>${esc(v.label)}</option>`).join('')}
                    </select>
                    ${r.customer_id ? `<button class="btn btn-sm btn-outline-primary text-nowrap" onclick="showMatchesForCustomer(${r.customer_id})" title="ملک‌هایی که با این درخواست می‌خوانند"><i class="bi bi-house-check"></i> ملک‌های مناسب</button>` : ''}
                  </div>
                </td></tr>`;
        }).join('');
    } catch (e) {
        body.innerHTML = `<tr><td colspan="6" class="text-danger text-center p-3">${esc(e.message)}</td></tr>`;
    }
}

async function updatePortalRequest(id, status) {
    try {
        await apiCall(`/portal/admin/requests/${id}`, {
            method: 'PATCH', body: JSON.stringify({ status }),
        });
        showToast('انجام شد', 'وضعیت درخواست بروزرسانی شد', 'success');
    } catch (e) { showToast('خطا', e.message, 'danger'); await loadPortalRequests(); }
}


// ══════════ پایش سامانه ══════════════════════════════════════════════════
// Reads /api/monitoring/overview, /api/gcp/status and /api/stats/logs. Every
// value here comes from the server or from a log line, so it is written with
// textContent or through esc() — a log line is the least trustworthy string in
// the product, and this screen is only ever opened by an admin.

let _monTimer = null;

function _fmtBytes(n) {
    if (!n && n !== 0) return '—';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${formatNumber(Math.round(n * 10) / 10)} ${u[i]}`;
}

function _fmtUptime(sec) {
    if (!sec && sec !== 0) return '—';
    const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600),
          m = Math.floor(sec % 3600 / 60);
    if (d) return `${formatNumber(d)} روز و ${formatNumber(h)} ساعت`;
    if (h) return `${formatNumber(h)} ساعت و ${formatNumber(m)} دقیقه`;
    return `${formatNumber(m)} دقیقه`;
}

function _setTile(id, text, cls) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;                       // textContent: no markup path
    if (cls) el.className = cls;
}

const JOB_STATUS_FA = {
    completed: 'تکمیل‌شده', running: 'در حال اجرا', failed: 'ناموفق',
    pending: 'در صف', paused: 'متوقف', cancelled: 'لغو شده',
};

async function loadClientErrors() {
    const tb = document.getElementById('mon-cerr-table');
    if (!tb) return;
    try {
        const d = await apiCall('/monitoring/client-errors?limit=60');
        const rows = d.items || [];
        document.getElementById('mon-cerr-count').textContent = formatNumber(rows.length);
        if (!rows.length) {
            tb.innerHTML = '<tr><td colspan="4" class="text-muted small p-3">هیچ خطایی از مرورگر کاربران نرسیده — خبر خوبی است.</td></tr>';
            return;
        }
        const fa = iso => iso ? `${new Date(iso).toLocaleDateString('fa-IR')} ${new Date(iso).toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' })}` : '—';
        tb.innerHTML = rows.map(e => {
            const where = [e.source ? esc(e.source.replace(/^https?:\/\/[^/]+/, '')) : '', e.line ? `:${e.line}` : ''].join('');
            const page = (e.url || '').replace(/^https?:\/\/[^/]+/, '');
            return `<tr>
                <td class="small text-nowrap">${fa(e.received_at)}</td>
                <td class="small" dir="ltr">${esc(e.browser || '')}<div class="text-muted">${esc(e.screen || '')}</div></td>
                <td class="small" dir="ltr" style="max-width:420px;word-break:break-word">${esc(e.message || '')}${e.stack ? `<details><summary class="text-muted">stack</summary><pre class="small mb-0" style="white-space:pre-wrap">${esc(e.stack)}</pre></details>` : ''}</td>
                <td class="small" dir="ltr"><div>${where || '—'}</div><div class="text-muted">${esc(page)}</div></td>
            </tr>`;
        }).join('');
    } catch (e) {
        tb.innerHTML = `<tr><td colspan="4" class="text-danger small p-3">${esc(e.message || 'خطا')}</td></tr>`;
    }
}

async function clearClientErrors() {
    if (!await askConfirm({ icon: 'bi-trash3', title: 'پاک کردن فهرست خطاها', tone: 'danger', okLabel: 'پاک کن', body: 'فهرست خطاهای مرورگر پاک شود؟ خطاهای تازه دوباره ثبت می‌شوند.' })) return;
    try { await apiCall('/monitoring/client-errors', { method: 'DELETE' }); loadClientErrors(); }
    catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function loadMonitoring() {
    loadCookieHealth();
    try {
        const d = await apiCall('/monitoring/overview');

        const pg = d.services?.postgres || {}, rd = d.services?.redis || {};
        // keep the stat-value class the panel styles; only tint the text
        _setTile('mon-pg-state', pg.up ? 'سالم' : 'قطع',
                 'stat-value ' + (pg.up ? '' : 'text-danger'));
        _setTile('mon-pg-ms', pg.up ? `— ${pg.latency_ms} ms` : (pg.error || ''));
        _setTile('mon-redis-state', rd.up ? 'سالم' : 'قطع',
                 'stat-value ' + (rd.up ? '' : 'text-danger'));
        _setTile('mon-redis-ms', rd.up ? `— ${rd.latency_ms} ms` : (rd.error || ''));

        const st = d.resources?.storage || {};
        _setTile('mon-disk', st.used_percent != null
            ? `${formatNumber(st.used_percent)}٪` : '—', 'stat-value');
        const bar = document.getElementById('mon-disk-bar');
        if (bar) {
            bar.style.width = (st.used_percent || 0) + '%';
            // amber past 75, red past 90 — the volume filling stops the scraper
            // saving anything, and it arrives without warning
            bar.className = 'progress-bar ' + (st.used_percent > 90 ? 'bg-danger'
                : st.used_percent > 75 ? 'bg-warning' : 'bg-success');
        }
        _setTile('mon-uptime', _fmtUptime(d.uptime_seconds), 'stat-value');
        const mem = d.resources?.memory_used_bytes;
        _setTile('mon-mem', mem ? `— حافظه ${_fmtBytes(mem)}` : '');

        // scraper jobs
        const jobsBox = document.getElementById('mon-jobs');
        const jobs = d.scraper?.jobs_by_status || {};
        jobsBox.innerHTML = Object.keys(jobs).length
            ? Object.entries(jobs).map(([k, v]) =>
                `<span class="badge bg-secondary-subtle text-body-secondary">
                   ${esc(JOB_STATUS_FA[k] || k)}: ${esc(formatNumber(v))}</span>`).join('')
            : '<span class="text-muted small">تسکی ثبت نشده است</span>';
        const stale = document.getElementById('mon-stale');
        if (d.scraper?.stale_running > 0) {
            stale.textContent = `${formatNumber(d.scraper.stale_running)} تسک بیش از ۶ ساعت در حال اجرا`;
            stale.classList.remove('d-none');
        } else { stale.classList.add('d-none'); }
        _setTile('mon-lastjob', d.scraper?.last_completed_at
            ? 'آخرین تسک موفق: ' + new Date(d.scraper.last_completed_at).toLocaleString('fa-IR')
            : 'هنوز تسک موفقی ثبت نشده');

        // ── host, network, uptimes ──
        const sys = d.system || {}, net = d.network || {};
        _setTile('sys-os', sys.distro || sys.os || '—');
        _setTile('sys-arch', `${sys.arch || '—'} · Python ${sys.python || '—'}`);
        _setTile('sys-host', sys.hostname || '—');
        _setTile('sys-ip', net.server_ip || '—');
        _setTile('sys-domain', net.domain || '—');

        _setTile('up-panel', _fmtUptime(d.uptime_seconds));
        _setTile('up-host', sys.host_uptime_seconds != null
            ? _fmtUptime(sys.host_uptime_seconds) : '—');

        // The API being up says nothing about the scraper: the process can run
        // for days while no job has completed since Tuesday.
        const lastDone = d.scraper?.last_completed_at;
        const running = d.scraper_running || 0;
        const upScraper = document.getElementById('up-scraper');
        if (running > 0) {
            upScraper.innerHTML = `<span class="text-success">${formatNumber(running)} تسک در حال اجرا</span>`;
        } else if (lastDone) {
            const hrs = (Date.now() - new Date(lastDone)) / 3600000;
            const cls = hrs > 48 ? 'text-warning' : '';
            upScraper.innerHTML = `<span class="${cls}">آخرین تسک: ${esc(_fmtUptime(hrs * 3600))} پیش</span>`;
        } else {
            upScraper.textContent = 'هنوز تسکی اجرا نشده';
        }

        const dv = net.reachability?.divar;
        const dvEl = document.getElementById('net-divar');
        if (dv) {
            dvEl.innerHTML = dv.up
                ? `<span class="text-success">در دسترس</span>
                   <span class="text-muted small" dir="ltr"> ${esc(dv.setup_ms ?? dv.latency_ms)} ms</span>
                   <span class="text-muted" style="font-size:.68rem"> (برقراری اتصال)</span>`
                : `<span class="text-danger">در دسترس نیست</span>
                   <span class="text-muted small">${esc(dv.error || dv.status || '')}</span>`;
        }

        await Promise.all([loadGcpStatus(), loadMonitoringLogs()]);
        startLive();
    } catch (e) {
        showToast('خطا', 'خواندن وضعیت سامانه ناموفق بود', 'danger');
    }

    // Refresh only while this screen is open. An interval that keeps polling
    // after you navigate away is load on a single-replica box for nothing.
    clearInterval(_monTimer);
    _monTimer = setInterval(() => {
        const sec = document.getElementById('section-monitoring');
        if (sec && sec.style.display !== 'none') loadMonitoring();
        else clearInterval(_monTimer);
    }, 30000);
}

const GCP_STATE_FA = {
    disabled:     ['خاموش', 'bg-secondary'],
    unconfigured: ['تنظیم نشده', 'bg-warning text-dark'],
    unreachable:  ['در دسترس نیست', 'bg-danger'],
    connected:    ['متصل', 'bg-success'],
    starting:     ['در حال شروع', 'bg-info text-dark'],
};

async function loadGcpStatus() {
    try {
        const g = await apiCall('/gcp/status');
        const [label, cls] = GCP_STATE_FA[g.state] || [g.state, 'bg-secondary'];
        const badge = document.getElementById('mon-gcp-state');
        badge.textContent = label;
        badge.className = 'badge ' + cls;
        _setTile('mon-gcp-note', g.project_id ? `پروژه: ${g.project_id}` : 'پروژه‌ای تنظیم نشده');
        const detail = [];
        if (g.buffer_size != null) detail.push(`صف: ${formatNumber(g.buffer_size)}`);
        if (g.exported_logs) detail.push(`ارسال‌شده: ${formatNumber(g.exported_logs)}`);
        if (g.dropped) detail.push(`رهاشده: ${formatNumber(g.dropped)}`);
        const box = document.getElementById('mon-gcp-detail');
        box.innerHTML = detail.map(esc).join(' • ')
            + (g.last_error ? `<div class="text-danger mt-1">${esc(g.last_error)}</div>` : '');
        document.getElementById('mon-gcp-test')
            .classList.toggle('d-none', g.state === 'disabled');
    } catch (_) { /* GCP is optional; its absence is not a page error */ }
}

async function testGcp() {
    try {
        const r = await apiCall('/gcp/test', { method: 'POST' });
        showToast(r.ok ? 'موفق' : 'ناموفق', r.detail || '', r.ok ? 'success' : 'warning');
        loadGcpStatus();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function loadMonitoringLogs() {
    const box = document.getElementById('mon-logs');
    if (!box) return;
    const level = document.getElementById('mon-log-level')?.value || '';
    const grep = document.getElementById('mon-log-grep')?.value.trim() || '';
    try {
        const qs = new URLSearchParams({ lines: '200' });
        if (level) qs.set('level', level);
        if (grep) qs.set('grep', grep);
        const d = await apiCall('/stats/logs?' + qs.toString());
        if (!d.lines?.length) {
            box.textContent = d.note || 'چیزی مطابق این فیلتر پیدا نشد.';
            return;
        }
        // Colour by level, and esc() every line — a log line is the least
        // trustworthy string in the product.
        box.innerHTML = d.lines.map(l => {
            const cls = /\| ERROR/.test(l) ? 'text-danger'
                      : /\| WARNING/.test(l) ? 'text-warning'
                      : /\| SUCCESS/.test(l) ? 'text-success' : '';
            return `<span class="${cls}">${esc(l)}</span>`;
        }).join('\n');
        box.scrollTop = box.scrollHeight;
    } catch (e) {
        box.textContent = 'خواندن لاگ ناموفق بود: ' + e.message;
    }
}


// ══════════ live server status ═══════════════════════════════════════════
// Polls /api/monitoring/live every 5s and derives rates from the difference
// between two counter samples. The window lives in the browser, so there is no
// server-side history to store, expire or size — and "live" only ever means
// the last few minutes anyway.

let _liveTimer = null, _livePrev = null, _liveChart = null, _liveOn = true;
const LIVE_POINTS = 40;                      // ~3.5 minutes at 5s

function _liveChartInit() {
    const el = document.getElementById('liveChart');
    if (!el || _liveChart) return;
    const c = chartColors();
    _liveChart = new Chart(el, {
        type: 'line',
        data: { labels: [], datasets: [
            { label: 'درخواست/ثانیه', data: [], borderColor: '#a78bfa',
              backgroundColor: 'rgba(167,139,250,.12)', fill: true,
              tension: .35, pointRadius: 0, borderWidth: 2, yAxisID: 'y' },
            { label: 'پاسخ (ms)', data: [], borderColor: '#67e8f9',
              tension: .35, pointRadius: 0, borderWidth: 2, yAxisID: 'y1' },
        ]},
        options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: { labels: { color: c.text, boxWidth: 10, font: { size: 10 } } },
                // the other four charts all have this; this one was missed
                tooltip: _sfTooltip(c),
            },
            scales: {
                x: { ticks: { color: c.tick, maxTicksLimit: 6, font: { size: 9 } },
                     grid: { color: 'transparent' } },
                y: { position: 'right', beginAtZero: true,
                     ticks: { color: c.tick, font: { size: 9 } }, grid: { color: c.grid } },
                y1: { position: 'left', beginAtZero: true, grid: { display: false },
                      ticks: { color: c.tick, font: { size: 9 } } },
            },
        },
    });
}

// One place decides the colour, so CPU, RAM and swap agree on what "worrying"
// means instead of each picking its own thresholds.
function _setBar(barId, textId, percent, text) {
    const txt = document.getElementById(textId);
    if (txt) txt.textContent = text;
    const bar = document.getElementById(barId);
    if (!bar) return;
    if (percent == null || !isFinite(percent)) { bar.style.width = '0%'; return; }
    const p = Math.max(0, Math.min(percent, 100));
    bar.style.width = p + '%';
    bar.className = 'progress-bar ' + (p > 90 ? 'bg-danger' : p > 75 ? 'bg-warning' : 'bg-success');
}

function _fmtRate(n) {
    if (n == null || !isFinite(n)) return '—';
    return n < 10 ? n.toFixed(2) : Math.round(n).toString();
}

async function tickLive() {
    if (!_liveOn) return;
    try {
        const s = await apiCall('/monitoring/live');

        if (_livePrev) {
            const dt = Math.max(s.ts - _livePrev.ts, 0.001);
            const rps = (s.requests - _livePrev.requests) / dt;
            const eps = (s.errors - _livePrev.errors) / dt;
            const dCount = s.latency_count - _livePrev.latency_count;
            const dSum = s.latency_sum - _livePrev.latency_sum;
            // average over the interval, not since boot — a slow start would
            // otherwise hide behind hours of healthy traffic
            const lat = dCount > 0 ? (dSum / dCount) * 1000 : null;
            // cgroup usage is cumulative microseconds across all cores, so the
            // percentage is scaled by the core count the container may use —
            // otherwise a busy 4-core box reads as 400%.
            let cpu = null;
            if (s.cpu_usage_usec != null && _livePrev.cpu_usage_usec != null) {
                const cores = s.cpu_limit_cores || s.cpu_count || 1;
                cpu = ((s.cpu_usage_usec - _livePrev.cpu_usage_usec) / 1e6 / dt / cores) * 100;
            } else if (s.cpu_seconds != null && _livePrev.cpu_seconds != null) {
                cpu = ((s.cpu_seconds - _livePrev.cpu_seconds) / dt) * 100;   // process fallback
            }

            document.getElementById('live-rps').textContent = _fmtRate(rps);
            const epsEl = document.getElementById('live-eps');
            epsEl.textContent = _fmtRate(eps);
            epsEl.className = 'fs-5 fw-bold ' + (eps > 0 ? 'text-danger' : '');
            document.getElementById('live-lat').textContent =
                lat != null ? Math.round(lat) + ' ms' : '—';
            _setBar('live-cpu-bar', 'live-cpu-txt', cpu,
                    cpu != null ? cpu.toFixed(1) + '%' : '—');
            document.getElementById('live-cpu').textContent =
                cpu != null ? cpu.toFixed(1) + '%' : '—';   // null on macOS: no /proc
            document.getElementById('live-load').textContent = s.load_1m != null
                ? `load ${s.load_1m.toFixed(2)} / ${(s.load_5m ?? 0).toFixed(2)} / ${(s.load_15m ?? 0).toFixed(2)}`
                : '';

            _liveChartInit();
            if (_liveChart) {
                const t = new Date().toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
                _liveChart.data.labels.push(t);
                _liveChart.data.datasets[0].data.push(+rps.toFixed(2));
                _liveChart.data.datasets[1].data.push(lat != null ? Math.round(lat) : null);
                if (_liveChart.data.labels.length > LIVE_POINTS) {
                    _liveChart.data.labels.shift();
                    _liveChart.data.datasets.forEach(d => d.data.shift());
                }
                _liveChart.update('none');
            }
        }

        // RAM — against the cgroup limit where there is one, because that is
        // the number that decides whether the pod gets killed
        const mem = s.memory_used_bytes, lim = s.memory_limit_bytes;
        const memPct = (mem && lim) ? (mem / lim) * 100 : null;
        _setBar('live-mem-bar', 'live-mem-txt', memPct,
                mem ? _fmtBytes(mem) + (lim ? ' / ' + _fmtBytes(lim) : '') : '—');
        document.getElementById('live-mem-note').textContent =
            memPct != null ? `${formatNumber(memPct.toFixed(0))}٪ استفاده‌شده` : '';

        // Swap — the server runs with 4GB of it because RAM is tight, so swap
        // filling is a real signal that something is about to go badly.
        const swPct = s.swap_used_percent;
        _setBar('live-swap-bar', 'live-swap-txt', swPct,
                s.swap_total_bytes
                    ? _fmtBytes(s.swap_used_bytes) + ' / ' + _fmtBytes(s.swap_total_bytes)
                    : '—');
        document.getElementById('live-swap-note').textContent =
            swPct != null ? `${formatNumber(swPct)}٪ استفاده‌شده`
                          : (s.swap_total_bytes === 0 ? 'بدون swap' : '');

        document.getElementById('live-scraper').textContent =
            `${formatNumber(s.reveals || 0)} / ${formatNumber(s.challenges || 0)}`
            + (s.rotations ? ` — ${formatNumber(s.rotations)} چرخش` : '');
        document.getElementById('mon-live-updated').textContent =
            'آخرین بروزرسانی: ' + new Date().toLocaleTimeString('fa-IR');

        _livePrev = s;
        const badge = document.getElementById('mon-live-badge');
        badge.className = 'badge bg-success d-inline-flex align-items-center gap-1';
    } catch (e) {
        // A failed poll marks the indicator rather than throwing a toast every
        // five seconds — the screen itself is the error report.
        const badge = document.getElementById('mon-live-badge');
        if (badge) badge.className = 'badge bg-danger d-inline-flex align-items-center gap-1';
    }
}

function startLive() {
    clearInterval(_liveTimer);
    _livePrev = null;
    tickLive();
    _liveTimer = setInterval(() => {
        const sec = document.getElementById('section-monitoring');
        // stop as soon as the screen is not on show — a poll every 5s against a
        // single-replica box for a page nobody is looking at is pure load
        if (!sec || sec.style.display === 'none') { stopLive(); return; }
        tickLive();
    }, 5000);
}

function stopLive() { clearInterval(_liveTimer); _liveTimer = null; }

function toggleLive() {
    _liveOn = !_liveOn;
    const btn = document.getElementById('mon-live-toggle');
    const badge = document.getElementById('mon-live-badge');
    if (_liveOn) {
        btn.textContent = 'توقف';
        badge.className = 'badge bg-success d-inline-flex align-items-center gap-1';
        startLive();
    } else {
        btn.textContent = 'ادامه';
        badge.className = 'badge bg-secondary d-inline-flex align-items-center gap-1';
        stopLive();
    }
}


// ── real connectivity test ────────────────────────────────────────────────
// Four timed stages rather than one boolean. "Divar is unreachable" leaves you
// guessing; DNS / TCP / TLS / HTTP tells you which layer broke, and those are
// four different fixes — a resolver problem on the box, a blocked route, TLS
// interception, or Divar itself being down.

const PROBE_STAGE_FA = {
    DNS:  'نام دامنه (DNS)',
    TCP:  'اتصال TCP',
    TLS:  'گواهی امن (TLS)',
    HTTP: 'پاسخ HTTP',
};

async function runConnectivityTest() {
    const btn = document.getElementById('probe-btn');
    const row = document.getElementById('probe-row');
    const box = document.getElementById('probe-result');
    if (!btn || !box) return;

    const label = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> در حال تست…';
    row.classList.remove('d-none');
    box.innerHTML = '<span class="text-muted small">در حال اجرای تست واقعی…</span>';

    try {
        const d = await apiCall('/monitoring/connectivity-test?target=divar', { method: 'POST' });

        const rows = (d.stages || []).map(st => {
            const icon = st.ok
                ? '<i class="bi bi-check-circle-fill text-success"></i>'
                : '<i class="bi bi-x-circle-fill text-danger"></i>';
            return `<div class="d-flex align-items-center gap-2 py-1"
                         style="border-bottom:1px solid rgba(255,255,255,.05)">
                      ${icon}
                      <span style="min-width:120px">${esc(PROBE_STAGE_FA[st.stage] || st.stage)}</span>
                      <span dir="ltr" class="text-muted small" style="min-width:70px">${esc(st.ms)} ms</span>
                      <span class="small text-muted" dir="ltr">${esc(st.detail || '')}</span>
                    </div>`;
        }).join('');

        // Name the first failing stage: it is the one that matters, and the
        // ones after it did not run or are meaningless once it failed.
        const failed = (d.stages || []).find(st => !st.ok);
        // Lead with the round trip. The total is the sum of DNS, TLS handshake
        // and a request on a cold client — useful, but it is not latency, and
        // showing it as one number made a 1ms link look like a 2-second one.
        const verdict = d.ok
            ? `<div class="text-success fw-bold mb-1">
                 <i class="bi bi-check2-circle"></i> اتصال سالم است —
                 <span dir="ltr">${esc(d.ip || '')}</span>
                 <span class="text-muted small" dir="ltr">
                   (رفت‌وبرگشت ${esc(d.rtt_ms)} ms · مجموع با برقراری اتصال ${esc(d.total_ms)} ms)
                 </span></div>`
            : `<div class="text-danger fw-bold mb-1">
                 <i class="bi bi-exclamation-triangle"></i>
                 مشکل در مرحلهٔ «${esc(PROBE_STAGE_FA[failed?.stage] || failed?.stage || '—')}»
                 <span class="text-muted small" dir="ltr">${esc(failed?.detail || '')}</span></div>`;

        box.innerHTML = verdict + rows;
        loadGcpStatus();                       // the tile shares the cache this refreshed
        const el = document.getElementById('net-divar');
        if (el) {
            el.innerHTML = d.ok
                ? `<span class="text-success">در دسترس</span>
                   <span class="text-muted small" dir="ltr"> ${esc(d.rtt_ms)} ms</span>`
                : '<span class="text-danger">در دسترس نیست</span>';
        }
    } catch (e) {
        box.innerHTML = `<span class="text-danger small">${esc(e.message)}</span>`;
    } finally {
        btn.disabled = false;
        btn.innerHTML = label;
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// پیامک — the SMS panel.
//
// Reads /api/sms/*. Every value that came from a person (a message body, a
// campaign name, a phone number typed into a form) goes through esc() before
// it reaches innerHTML: the send log is written by staff and read by staff,
// which is exactly the shape that turns into stored XSS when it is skipped.
// ═══════════════════════════════════════════════════════════════════════════

let _smsOffset = 0;
let _smsAudience = null;
let _smsAudienceCount = 0;

const _FA_DIGITS = '۰۱۲۳۴۵۶۷۸۹';
function faNum(n) {
    if (n === null || n === undefined || n === '') return '—';
    return String(n).replace(/\d/g, d => _FA_DIGITS[d]);
}

function smsCount(fieldId, outId) {
    const el = document.getElementById(fieldId);
    const out = document.getElementById(outId);
    if (!el || !out) return;
    const len = el.value.length;
    // Persian text is billed at 70 characters per part, not 160 — saying so
    // here is the difference between a 1,000 تومان send and a 5,000 تومان one.
    const parts = len === 0 ? 0 : Math.ceil(len / 70);
    out.textContent = `${faNum(len)} کاراکتر · ${faNum(parts)} بخش پیامک`;
}

async function loadSms() {
    // Settings are super_admin-only; the card stays hidden for everyone else
    // rather than showing a form whose save would 403.
    const isBoss = ['root', 'super_admin'].includes(_currentUser?.role);
    document.getElementById('sms-settings-card')?.classList.toggle('d-none', !isBoss);
    // Stays disabled until an audience is picked — pickAudience() is what
    // enables it, and only then does the button know how many people it is
    // about to message, which is the number it has to show.
    const bulkBtn = document.getElementById('sms-bulk-btn');
    if (bulkBtn) bulkBtn.disabled = true;
    _smsAudience = null;
    _smsAudienceCount = 0;

    loadSmsStats();
    loadSmsCredit();
    if (isBoss) loadSmsSettings();
    loadSmsAudiences();
    _smsOffset = 0;
    loadSmsMessages();
    loadSmsEvents();
}

const SMS_STAGE_FA = {
    send: 'ارسال', test: 'آزمایشی', settings: 'تنظیمات',
    template: 'الگو', credit: 'اعتبار', delivery: 'تحویل', error: 'خطا',
};

async function loadSmsEvents() {
    const tb = document.getElementById('sms-events-table');
    if (!tb) return;
    const stage = document.getElementById('sms-events-stage')?.value || '';
    tb.innerHTML = '<tr><td colspan="4" class="text-muted small p-3">در حال بارگذاری…</td></tr>';
    try {
        const q = stage ? `?limit=100&stage=${encodeURIComponent(stage)}` : '?limit=100';
        const d = await apiCall('/sms/events' + q);
        const rows = d.events || [];
        if (!rows.length) {
            tb.innerHTML = '<tr><td colspan="4" class="text-muted small p-3">رویدادی ثبت نشده است</td></tr>';
            return;
        }
        tb.innerHTML = rows.map(e => {
            const cls = e.level === 'error' ? 'text-danger'
                      : e.level === 'warning' ? 'text-warning' : '';
            // The reason is the whole point of this table — a failure whose
            // cause is only in the pod log is what this exists to replace.
            const why = e.details && e.details.reason
                ? `<div class="small text-muted">${esc(e.details.reason)}</div>` : '';
            const via = e.route === 'verify' ? ' <span class="badge bg-info-subtle text-info">الگو</span>'
                      : e.route === 'sms' ? ' <span class="badge bg-secondary-subtle text-secondary">ارسال ساده</span>' : '';
            const when = e.at
                ? new Date(e.at).toLocaleString('fa-IR', {
                    month: 'numeric', day: 'numeric',
                    hour: '2-digit', minute: '2-digit' })
                : '—';
            return `<tr>
                <td class="small text-muted" dir="ltr">${esc(when)}</td>
                <td class="small">${esc(SMS_STAGE_FA[e.stage] || e.stage)}</td>
                <td class="small ${cls}">${esc(e.message)}${via}${why}</td>
                <td class="small text-muted">${esc(e.actor || '—')}</td>
            </tr>`;
        }).join('');
    } catch (err) {
        tb.innerHTML = `<tr><td colspan="4" class="text-danger small p-3">${esc(err.message || 'خطا')}</td></tr>`;
    }
}

async function loadSmsCredit() {
    const el = document.getElementById('sms-credit');
    if (!el) return;
    try {
        const d = await apiCall('/sms/account');
        if (!d.ok) {
            // A dash with the reason hidden in a title attribute is not an
            // error message — nobody hovers a number to find out why it is
            // blank. Kavenegar's own code is already mapped to Persian, so
            // show it where the value would have been.
            el.textContent = '—';
            el.title = d.error || '';
            const lbl = el.parentElement?.querySelector('.stat-label');
            if (lbl) lbl.innerHTML =
                `<span class="text-danger">${esc(d.error || 'اعتبار خوانده نشد')}</span>`;
            return;
        }
        const lbl0 = el.parentElement?.querySelector('.stat-label');
        if (lbl0) lbl0.textContent = 'اعتبار باقی‌مانده';
        el.textContent = faNum(Number(d.remaining_credit || 0).toLocaleString('en-US'));
        el.title = d.expire_date ? `انقضا: ${d.expire_date}` : '';
    } catch (_) { el.textContent = '—'; }
}

async function loadSmsStats() {
    try {
        const d = await apiCall('/sms/stats');
        const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = faNum(v); };
        set('sms-sent-30', d.last_30_days);
        set('sms-delivered', d.delivered_30_days);
        set('sms-failed', d.failed_30_days);
    } catch (_) { /* the cards keep their placeholder */ }
}

async function loadSmsSettings() {
    try {
        const d = await apiCall('/sms/settings');
        const badge = document.getElementById('sms-config-badge');
        if (badge) {
            badge.textContent = d.configured ? 'پیکربندی شده' : 'کلید API تنظیم نشده';
            badge.className = 'badge ' + (d.configured ? 'bg-success' : 'bg-danger');
        }
        const src = document.getElementById('sms-key-source');
        if (src) {
            src.textContent = d.key_source === 'env'
                ? 'کلید از تنظیمات سرور خوانده می‌شود'
                : (d.key_source === 'panel' ? 'کلید از همین پنل ذخیره شده است' : '');
        }
        const hint = document.getElementById('sms-key-hint');
        if (hint) hint.textContent = d.api_key_masked ? `کلید فعلی: ${d.api_key_masked}` : '';
        const v = (id, val) => { const e = document.getElementById(id); if (e) e.value = val || ''; };
        v('sms-sender', d.sender);
        v('sms-otp-template', d.otp_template);
        v('sms-signature', d.signature);
        const en = document.getElementById('sms-enabled');
        if (en) en.checked = !!d.enabled;
    } catch (e) { showToast('پیامک', 'تنظیمات خوانده نشد', 'error'); }
}

async function saveSmsSettings() {
    const out = document.getElementById('sms-settings-result');
    const body = {
        sender: document.getElementById('sms-sender')?.value || '',
        otp_template: document.getElementById('sms-otp-template')?.value || '',
        signature: document.getElementById('sms-signature')?.value || '',
        enabled: !!document.getElementById('sms-enabled')?.checked,
    };
    // Only send the key when one was typed — an empty box means "leave it",
    // not "delete it", which is what the user expects from a masked field.
    const keyEl = document.getElementById('sms-api-key');
    if (keyEl && keyEl.value.trim()) body.api_key = keyEl.value.trim();

    try {
        await apiCall('/sms/settings', { method: 'PUT', body: JSON.stringify(body) });
        if (keyEl) keyEl.value = '';
        if (out) { out.textContent = 'ذخیره شد'; out.className = 'small text-success'; }
        loadSmsSettings();
        loadSmsCredit();
    } catch (e) {
        if (out) { out.textContent = e.message || 'ذخیره نشد'; out.className = 'small text-danger'; }
    }
}

async function sendSmsTest() {
    const to = document.getElementById('sms-test-to')?.value?.trim();
    const out = document.getElementById('sms-settings-result');
    if (!to) { if (out) { out.textContent = 'شماره را وارد کنید'; out.className = 'small text-warning'; } return; }
    if (out) { out.textContent = 'در حال ارسال…'; out.className = 'small text-muted'; }
    try {
        const d = await apiCall(`/sms/test?to=${encodeURIComponent(to)}`, { method: 'POST' });
        if (out) {
            // Name the route. The two behave differently on this account
            // — a template needs no sender line, a plain send does — so a
            // bare «ناموفق» sends the reader back to the sender field for
            // a failure that has nothing to do with it.
            const via = d.via ? ' — از راه ' + d.via : '';
            out.textContent = (d.ok ? 'ارسال شد' : (d.error || 'ناموفق')) + via;
            out.className = 'small ' + (d.ok ? 'text-success' : 'text-danger');
        }
        loadSmsMessages(); loadSmsCredit();
    } catch (e) {
        if (out) { out.textContent = e.message || 'ناموفق'; out.className = 'small text-danger'; }
    }
}

async function sendSingleSms() {
    const to = document.getElementById('sms-one-to')?.value?.trim();
    const message = document.getElementById('sms-one-body')?.value?.trim();
    const out = document.getElementById('sms-one-result');
    if (!to || !message) {
        if (out) { out.textContent = 'شماره و متن لازم است'; out.className = 'small text-warning'; }
        return;
    }
    if (out) { out.textContent = 'در حال ارسال…'; out.className = 'small text-muted'; }
    try {
        const d = await apiCall('/sms/send', { method: 'POST', body: JSON.stringify({ to, message }) });
        if (out) {
            out.textContent = d.ok ? 'ارسال شد' : (d.error || 'ناموفق');
            out.className = 'small ' + (d.ok ? 'text-success' : 'text-danger');
        }
        if (d.ok) document.getElementById('sms-one-body').value = '';
        smsCount('sms-one-body', 'sms-one-count');
        loadSmsMessages(); loadSmsStats(); loadSmsCredit();
    } catch (e) {
        if (out) { out.textContent = e.message || 'ناموفق'; out.className = 'small text-danger'; }
    }
}

async function loadSmsAudiences() {
    const box = document.getElementById('sms-audiences');
    if (!box) return;
    try {
        const d = await apiCall('/sms/audiences');
        box.innerHTML = (d.audiences || []).map(a => `
            <label class="d-flex align-items-center gap-2">
              <input type="radio" name="sms-aud" value="${esc(a.key)}"
                     data-count="${a.count === null ? 0 : a.count}"
                     onchange="pickAudience(this)">
              <span>${esc(a.label)}</span>
              <span class="badge bg-secondary">${a.count === null ? '—' : faNum(a.count)}</span>
            </label>`).join('');
    } catch (_) {
        box.innerHTML = '<span class="text-muted small">گروه‌ها خوانده نشد</span>';
    }
}

function pickAudience(el) {
    _smsAudience = el.value;
    _smsAudienceCount = Number(el.dataset.count || 0);
    const btn = document.getElementById('sms-bulk-btn');
    if (btn) {
        btn.disabled = !['root', 'super_admin'].includes(_currentUser?.role) || !_smsAudienceCount;
        btn.innerHTML = `<i class="bi bi-megaphone"></i> ارسال به ${faNum(_smsAudienceCount)} گیرنده`;
    }
}

async function sendBroadcast() {
    const message = document.getElementById('sms-bulk-body')?.value?.trim();
    const campaign = document.getElementById('sms-campaign')?.value?.trim();
    const out = document.getElementById('sms-bulk-result');
    if (!_smsAudience || !message) {
        if (out) { out.textContent = 'گروه و متن لازم است'; out.className = 'small text-warning'; }
        return;
    }
    // The last stop before spending money on an irreversible action. The count
    // is repeated here on purpose — it is the number the server will verify.
    if (!await askConfirm({ icon: 'bi-question-lg', title: 'تأیید', okLabel: 'تأیید', body: `ارسال این پیام به ${_smsAudienceCount} گیرنده؟\n\nاین کار برگشت‌پذیر نیست و هزینه دارد.` })) return;

    const btn = document.getElementById('sms-bulk-btn');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> در حال ارسال…'; }
    if (out) { out.textContent = ''; }
    try {
        const d = await apiCall('/sms/broadcast', {
            method: 'POST',
            body: JSON.stringify({ audience: _smsAudience, message, campaign,
                                   confirm_count: _smsAudienceCount }),
        });
        if (out) {
            out.textContent = `ارسال‌شده: ${faNum(d.sent)} · ناموفق: ${faNum(d.failed)}`;
            out.className = 'small ' + (d.failed ? 'text-warning' : 'text-success');
        }
        document.getElementById('sms-bulk-body').value = '';
        smsCount('sms-bulk-body', 'sms-bulk-count');
        loadSmsMessages(); loadSmsStats(); loadSmsCredit();
    } catch (e) {
        if (out) { out.textContent = e.message || 'ناموفق'; out.className = 'small text-danger'; }
    } finally {
        if (btn) { btn.disabled = false; pickAudience({ value: _smsAudience, dataset: { count: _smsAudienceCount } }); }
    }
}

function smsPage(dir) {
    _smsOffset = Math.max(0, _smsOffset + dir * 50);
    loadSmsMessages();
}

async function loadSmsMessages() {
    const body = document.getElementById('sms-messages-body');
    if (!body) return;
    const status = document.getElementById('sms-filter-status')?.value || '';
    const search = document.getElementById('sms-search')?.value?.trim() || '';
    const qs = new URLSearchParams({ limit: '50', offset: String(_smsOffset) });
    if (status) qs.set('status', status);
    if (search) qs.set('search', search);

    try {
        const d = await apiCall(`/sms/messages?${qs}`);
        if (!d.items?.length) {
            body.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-3">پیامی نیست</td></tr>';
        } else {
            body.innerHTML = d.items.map(m => {
                const ok = m.status === 'sent';
                const delivered = m.delivery_status === 10;
                return `<tr>
                  <td dir="ltr">${esc(m.to_number)}</td>
                  <td class="text-truncate" style="max-width:260px" title="${esc(m.message || '')}">${esc(m.message || '—')}</td>
                  <td><span class="badge ${ok ? 'bg-success' : 'bg-danger'}">${ok ? 'ارسال‌شده' : 'ناموفق'}</span></td>
                  <td>${m.delivery_text
                        ? `<span class="${delivered ? 'text-success' : 'text-muted'}">${esc(m.delivery_text)}</span>`
                        : '<span class="text-muted">—</span>'}</td>
                  <td>${esc(m.campaign || '—')}</td>
                  <td>${esc(m.sent_by || '—')}</td>
                  <td dir="ltr" class="text-muted small">${m.sent_at ? new Date(m.sent_at).toLocaleString('fa-IR') : '—'}</td>
                </tr>`;
            }).join('');
        }
        const tot = document.getElementById('sms-messages-total');
        if (tot) tot.textContent = `${faNum(d.total)} پیام`;
    } catch (e) {
        body.innerHTML = `<tr><td colspan="7" class="text-center text-danger py-3">${esc(e.message || 'خطا')}</td></tr>`;
    }
}

async function refreshSmsDelivery() {
    const btn = document.getElementById('sms-refresh-btn');
    const label = btn?.innerHTML;
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>'; }
    try {
        const d = await apiCall('/sms/messages/refresh-status', { method: 'POST' });
        showToast('پیامک', d.ok ? `${d.updated} پیام بروزرسانی شد` : (d.error || 'ناموفق'),
                  d.ok ? 'success' : 'error');
        loadSmsMessages(); loadSmsStats();
    } catch (e) {
        showToast('پیامک', e.message || 'ناموفق', 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = label; }
    }
}


// ── Divar session health ────────────────────────────────────────────────────
//
// The scraper's rotation pool is exactly the set of sessions marked valid, so
// one that died without anyone noticing keeps being handed work until someone
// looks at this table. The list is cheap (database only); «بررسی واقعی» is the
// button, because it costs a real request to Divar.

// ── نشست دیوار: live state ──────────────────────────────────────────────────
//
// Two numbers the panel was not showing, and one it was showing dishonestly.
//
// «زمان باقی‌مانده» counts down in the browser from an absolute instant, so it
// is live without a request per second. «آخرین بررسی واقعی» is the one that
// matters most: is_valid is a stored belief, and until it carries the time
// Divar was actually asked, the header can say «کوکی فعال» about a session
// that has been rejected since yesterday — which is exactly what happened.
let _ckItems = [];
let _ckStaleAfterMin = 25;
let _ckTicker = null;

function _faDuration(sec) {
    if (sec === null || sec === undefined) return null;
    const s = Math.max(0, Math.floor(sec));
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60), ss = s % 60;
    if (d) return `${faNum(d)} روز و ${faNum(h)} ساعت`;
    if (h) return `${faNum(h)} ساعت و ${faNum(m)} دقیقه`;
    if (m) return `${faNum(m)} دقیقه و ${faNum(ss)} ثانیه`;
    return `${faNum(ss)} ثانیه`;
}

function _faAgo(sec) {
    const s = Math.max(0, Math.floor(sec));
    if (s < 60) return 'همین حالا';
    return `${_faDuration(s)} پیش`;
}

function _ckTick() {
    const now = Date.now();
    for (const it of _ckItems) {
        const left = document.getElementById(`ck-left-${it.phone}`);
        if (left) {
            if (it.expiresAt === null) {
                // A session cookie with no wall-clock expiry. Saying "منقضی"
                // here would be wrong — it has no expiry, which is not the
                // same as having passed one.
                left.textContent = 'بدون تاریخ انقضا';
                left.className = 'text-muted';
            } else {
                const sec = (it.expiresAt - now) / 1000;
                left.textContent = sec <= 0 ? 'منقضی شده' : _faDuration(sec);
                left.className = sec <= 0 ? 'text-danger'
                               : sec < 86400 ? 'text-warning' : 'text-muted';
            }
        }

        const seen = document.getElementById(`ck-seen-${it.phone}`);
        if (seen) {
            if (it.checkedAt === null) {
                seen.innerHTML = '<span class="text-warning">هرگز بررسی نشده</span>';
            } else {
                const ageMin = (now - it.checkedAt) / 60000;
                const fresh = ageMin <= _ckStaleAfterMin;
                seen.innerHTML = `<span class="${fresh ? 'text-success' : 'text-warning'}">`
                    + `${fresh ? '✓ ' : '⚠ '}${esc(_faAgo((now - it.checkedAt) / 1000))}</span>`;
            }
        }
    }
}

async function loadCookieHealth() {
    const rows = document.getElementById('ck-rows');
    const btn = document.getElementById('ck-refresh');
    if (!rows) return;
    if (btn) btn.disabled = true;

    try {
        const d = await apiCall('/monitoring/cookies');
        const items = d.items || [];

        const summary = document.getElementById('ck-summary');
        if (summary) {
            summary.textContent = `${faNum(d.usable)} از ${faNum(d.total)} قابل استفاده`;
            summary.className = 'badge ms-auto ' + (d.usable ? 'bg-success' : 'bg-danger');
        }

        // A single account cannot rotate at all — maybe_rotate_account needs
        // two before it will switch. Silent in the logs, so say it here.
        const warn = document.getElementById('ck-warn');
        if (warn) {
            if (!d.rotation_possible) {
                warn.textContent = d.usable === 0
                    ? 'هیچ نشست معتبری وجود ندارد — اسکرپر نمی‌تواند کار کند.'
                    : 'فقط یک حساب فعال است؛ چرخش شماره انجام نمی‌شود. برای چرخش حداقل دو حساب لازم است.';
                warn.classList.remove('d-none');
            } else {
                warn.classList.add('d-none');
            }
        }

        if (!items.length) {
            rows.innerHTML = '<tr><td colspan="6" class="text-muted">هیچ حساب دیواری ذخیره نشده است</td></tr>';
            return;
        }

        const badge = { active: 'bg-success', expiring: 'bg-warning text-dark', expired: 'bg-danger' };
        const label = { active: 'فعال', expiring: 'نزدیک انقضا', expired: 'باطل' };

        // Absolute instants, so the ticker below counts down without asking
        // the server again every second.
        _ckItems = items.map(i => ({
            phone: i.phone_number,
            expiresAt: i.expires_at ? Date.parse(i.expires_at) : null,
            checkedAt: i.last_checked_at ? Date.parse(i.last_checked_at) : null,
            verified: !!i.verified,
        }));
        _ckStaleAfterMin = d.stale_after_minutes || 25;

        // Refetching is already handled by _monTimer; this only animates the
        // countdown between those fetches. Self-clearing, so navigating away
        // does not leave a timer running against a detached table.
        clearInterval(_ckTicker);
        _ckTicker = setInterval(() => {
            if (document.getElementById('ck-rows')) _ckTick();
            else clearInterval(_ckTicker);
        }, 1000);

        rows.innerHTML = items.map(i => `
          <tr>
            <td dir="ltr">${esc(i.phone_number)}</td>
            <td><span class="badge ${badge[i.state] || 'bg-secondary'}">${label[i.state] || esc(i.state)}</span>
                <div class="text-muted" style="font-size:.72rem">${esc(i.note || '')}</div></td>
            <td class="text-muted" id="ck-left-${esc(i.phone_number)}">—</td>
            <td class="text-muted" id="ck-seen-${esc(i.phone_number)}">—</td>
            <td>${i.in_rotation ? '<i class="bi bi-check-circle-fill text-success"></i>'
                                : '<i class="bi bi-x-circle text-muted"></i>'}</td>
            <td class="text-start">
              <button class="btn btn-sm btn-outline-primary"
                      onclick="checkCookieSession(${jsArg(i.phone_number)}, this)">
                بررسی
              </button>
              <span class="small ms-2" id="ck-res-${esc(i.phone_number)}"></span>
            </td>
          </tr>`).join('');
        _ckTick();   // paint now rather than a second from now
    } catch (e) {
        rows.innerHTML = `<tr><td colspan="6" class="text-danger">${esc(e.message || 'خطا')}</td></tr>`;
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function checkCookieSession(phone, btn) {
    const out = document.getElementById(`ck-res-${phone}`);
    const label = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>'; }
    if (out) { out.textContent = ''; out.className = 'small ms-2'; }
    try {
        const d = await apiCall(`/monitoring/cookies/check?phone=${encodeURIComponent(phone)}`,
                                { method: 'POST' });
        if (out) {
            // alive === null is "Divar did not answer", which is deliberately
            // not the same as the session being dead — see the endpoint.
            out.textContent = d.message || '';
            out.className = 'small ms-2 ' + (d.alive === true ? 'text-success'
                                          : d.alive === false ? 'text-danger' : 'text-warning');
        }
        // Refresh BOTH renderers, and on either answer.
        //
        // The header pill and this table read the same fact from two different
        // endpoints, and only the table was being refreshed — so a check that
        // killed a session left the pill still saying «کوکی فعال» for the very
        // number the row underneath had just marked باطل. It corrected itself
        // on the 5-minute interval, which is exactly long enough to look like
        // the panel disagreeing with itself.
        //
        // `true` matters as much as `false`: a revived session must clear a
        // pill that says «کوکی منقضی». `null` is "Divar did not answer" and
        // changes no stored state, so nothing to re-read.
        if (d.alive === true || d.alive === false) {
            loadCookieHealth();
            checkCookieStatus();
        }
    } catch (e) {
        if (out) { out.textContent = e.message || 'خطا'; out.className = 'small ms-2 text-danger'; }
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = label; }
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// ایمیل — the email panel.
//
// Reads /api/email/*. The SMTP password is write-only: it is sent when typed
// and comes back only as a mask, so the field is left empty on load and an
// empty field on save means "leave it alone", not "delete it".
// ═══════════════════════════════════════════════════════════════════════════

async function loadEmail() {
    const isBoss = ['root', 'super_admin'].includes(_currentUser?.role);
    document.getElementById('em-settings-card')?.classList.toggle('d-none', !isBoss);

    loadEmailStats();
    if (isBoss) { loadEmailSettings(); loadEmailAudiences(); emPreviewSoon(); }
    loadEmailTemplates();
    loadEmailMessages();
}

// ── کمپین ایمیلی — the broadcast ────────────────────────────────────
// Same shape as the SMS panel: a named group, its count shown and sent
// back with the request, so a group that grew since is refused by the
// server instead of quietly mailed.
let _emAudience = null, _emAudienceCount = 0, _emPreviewTimer = null;

async function loadEmailAudiences() {
    const box = document.getElementById('em-audiences');
    if (!box) return;
    try {
        const d = await apiCall('/email/audiences');
        box.innerHTML = (d.audiences || []).map(a => `
            <label class="d-flex align-items-center gap-2">
              <input type="radio" name="em-aud" value="${esc(a.key)}"
                     data-count="${a.count === null ? 0 : a.count}" onchange="emPickAudience(this)">
              <span>${esc(a.label)}</span>
              <span class="badge bg-secondary">${a.count === null ? '—' : faNum(a.count)}</span>
              <button type="button" class="btn btn-sm btn-link p-0 ms-auto" onclick="exportEmailAudience(${jsArg(a.key)})"
                      title="دانلود آدرس‌ها (CSV)"><i class="bi bi-download"></i></button>
            </label>`).join('') || '<span class="text-muted small">گروهی نیست</span>';
    } catch (_) {
        box.innerHTML = '<span class="text-muted small">گروه‌ها خوانده نشد</span>';
    }
}

function emPickAudience(el) {
    _emAudience = el.value;
    _emAudienceCount = Number(el.dataset.count || 0);
    _emSyncButton();
}

function _emSyncButton() {
    const btn = document.getElementById('em-bc-btn');
    if (!btn) return;
    const ready = _emAudienceCount > 0
        && document.getElementById('em-bc-subject')?.value.trim()
        && document.getElementById('em-bc-body')?.value.trim();
    btn.disabled = !ready || !['root', 'super_admin'].includes(_currentUser?.role);
    btn.innerHTML = `<i class="bi bi-megaphone"></i> ارسال به ${faNum(_emAudienceCount)} گیرنده`;
}

/** The preview follows the form, a beat behind the typing. */
function emPreviewSoon() {
    const n = document.getElementById('em-bc-count');
    if (n) n.textContent = `${faNum((document.getElementById('em-bc-body')?.value || '').length)} کاراکتر`;
    _emSyncButton();
    clearTimeout(_emPreviewTimer);
    _emPreviewTimer = setTimeout(emPreview, 400);
}

async function emPreview() {
    const frame = document.getElementById('em-bc-preview');
    if (!frame) return;
    const body = {
        subject: document.getElementById('em-bc-subject')?.value.trim() || '',
        message: document.getElementById('em-bc-body')?.value.trim() || '',
        cta_label: document.getElementById('em-bc-cta')?.value.trim() || null,
        cta_url: document.getElementById('em-bc-url')?.value.trim() || null,
    };
    try {
        const html = await apiCall('/email/broadcast/preview', { method: 'POST', body: JSON.stringify(body), raw: true });
        frame.srcdoc = html;
    } catch (e) {
        frame.srcdoc = `<p style="font-family:sans-serif;color:#f87171;padding:1rem">${esc(e.message || 'پیش‌نمایش در دسترس نیست')}</p>`;
    }
}

async function sendEmailBroadcast() {
    const subject = document.getElementById('em-bc-subject')?.value.trim();
    const message = document.getElementById('em-bc-body')?.value.trim();
    const cta_label = document.getElementById('em-bc-cta')?.value.trim() || null;
    const cta_url = document.getElementById('em-bc-url')?.value.trim() || null;
    const out = document.getElementById('em-bc-result');
    if (!_emAudience || !subject || !message) {
        if (out) { out.textContent = 'گروه، موضوع و متن لازم است'; out.className = 'small text-warning'; }
        return;
    }
    if (cta_label && !/^https?:\/\//.test(cta_url || '')) {
        if (out) { out.textContent = 'لینک دکمه باید با http شروع شود'; out.className = 'small text-warning'; }
        return;
    }
    // The last stop before an irreversible send; the count repeated here is
    // the number the server will verify against the live audience.
    if (!await askConfirm({ icon: 'bi-megaphone', title: 'ارسال کمپین', tone: 'warning', okLabel: 'بفرست',
        body: `«<b>${esc(subject)}</b>» به <b>${faNum(_emAudienceCount)}</b> گیرنده فرستاده شود؟`,
        note: 'ارسال گروهی برگشت‌پذیر نیست. پیش‌نمایش را دیده‌اید؟' })) return;

    const btn = document.getElementById('em-bc-btn');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> در حال ارسال…'; }
    if (out) out.textContent = '';
    try {
        const d = await apiCall('/email/broadcast', {
            method: 'POST',
            body: JSON.stringify({ audience: _emAudience, subject, message, cta_label, cta_url,
                                   confirm_count: _emAudienceCount }),
        });
        if (out) {
            out.textContent = `ارسال‌شده: ${faNum(d.sent)} · ناموفق: ${faNum(d.failed)}`;
            out.className = 'small ' + (d.failed ? 'text-warning' : 'text-success');
        }
        showToast(d.failed ? 'ارسال با خطا' : 'ارسال شد', `${faNum(d.sent)} از ${faNum(d.total)} ایمیل رفت`, d.failed ? 'warning' : 'success');
        document.getElementById('em-bc-body').value = '';
        document.getElementById('em-bc-subject').value = '';
        emPreviewSoon();
        loadEmailMessages(); loadEmailStats();
    } catch (e) {
        if (out) { out.textContent = e.message || 'ناموفق'; out.className = 'small text-danger'; }
        // a 409 means the group changed under us: reload the counts
        if (/تغییر کرده/.test(e.message || '')) loadEmailAudiences();
    } finally {
        _emSyncButton();
    }
}

/** The addresses of one group as a CSV file, from the browser. */
async function exportEmailAudience(key) {
    try {
        const d = await apiCall(`/email/export?audience=${encodeURIComponent(key)}`);
        if (!d.emails?.length) { showToast('خالی', 'این گروه آدرسی ندارد', 'info'); return; }
        const csv = '\ufeffemail\n' + d.emails.join('\n') + '\n';
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
        a.download = `sorinflow-emails-${key}.csv`;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 2000);
        showToast('دانلود شد', `${faNum(d.count)} آدرس — ${esc(d.label)}`, 'success');
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function loadEmailStats() {
    try {
        const d = await apiCall('/email/stats');
        const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = faNum(v); };
        set('em-sent-30', d.last_30_days);
        set('em-codes', d.login_codes_30_days);
        set('em-failed', d.failed_30_days);
    } catch (_) { /* cards keep their placeholder */ }
}

async function loadEmailSettings() {
    try {
        const d = await apiCall('/email/settings');
        const badge = document.getElementById('em-config-badge');
        if (badge) {
            badge.textContent = d.configured ? 'پیکربندی شده' : 'تنظیم نشده';
            badge.className = 'badge ' + (d.configured ? 'bg-success' : 'bg-danger');
        }
        const state = document.getElementById('em-state');
        if (state) state.textContent = d.configured ? 'آماده' : 'تنظیم نشده';

        const src = document.getElementById('em-pw-source');
        if (src) {
            src.textContent = d.password_source === 'env'
                ? 'رمز از تنظیمات سرور خوانده می‌شود'
                : (d.password_source === 'panel' ? 'رمز از همین پنل ذخیره شده است' : '');
        }
        const hint = document.getElementById('em-pw-hint');
        if (hint) hint.textContent = d.password_masked ? `رمز فعلی: ${d.password_masked}` : '';

        // The App Password explainer is guidance for someone setting this up
        // for the first time. Once it works, leaving it on screen reads as an
        // unresolved warning — which is exactly how it was read.
        document.getElementById('em-apppw-hint')
                ?.classList.toggle('d-none', !!d.configured);

        const v = (id, val) => { const e = document.getElementById(id); if (e) e.value = val ?? ''; };
        // Prefill the Gmail defaults as REAL values when nothing is saved yet.
        // They used to be placeholders, which render as grey text that looks
        // identical to a filled field — so the form appeared complete while
        // host and user were empty, and «بررسی اتصال» kept refusing with
        // "تنظیمات SMTP کامل نیست" for no visible reason.
        v('em-host', d.host || 'smtp.gmail.com');
        v('em-port', d.port || 587);
        v('em-user', d.user);
        v('em-from-name', d.from_name); v('em-reply-to', d.reply_to);
        v('em-from-email', d.from_email);
        const sec = document.getElementById('em-security');
        if (sec) sec.value = d.security || 'starttls';
        const en = document.getElementById('em-enabled');
        if (en) en.checked = !!d.enabled;
    } catch (e) { showToast('ایمیل', 'تنظیمات خوانده نشد', 'error'); }
}

async function saveEmailSettings(opts = {}) {
    // quiet: called by the verify and test buttons, which report their own
    // outcome — a "ذخیره شد" flashing before the real answer reads as noise.
    const out = opts.quiet ? null : document.getElementById('em-settings-result');
    const val = id => document.getElementById(id)?.value?.trim() ?? '';
    const body = {
        host: val('em-host'), user: val('em-user'),
        from_name: val('em-from-name'), reply_to: val('em-reply-to'),
        from_email: val('em-from-email'),
        security: document.getElementById('em-security')?.value || 'starttls',
        enabled: !!document.getElementById('em-enabled')?.checked,
    };
    const port = parseInt(val('em-port'), 10);
    if (!Number.isNaN(port)) body.port = port;
    // Only send a password that was actually typed — see the header note.
    const pw = document.getElementById('em-password');
    if (pw && pw.value.trim()) body.password = pw.value.trim();

    try {
        await apiCall('/email/settings', { method: 'PUT', body: JSON.stringify(body) });
        if (pw) pw.value = '';
        if (out) { out.textContent = 'ذخیره شد'; out.className = 'small text-success'; }
        if (!opts.quiet) loadEmailSettings();
    } catch (e) {
        if (out) { out.textContent = e.message || 'ذخیره نشد'; out.className = 'small text-danger'; }
        if (opts.quiet) throw e;      // the caller reports it
    }
}

async function verifyEmailSmtp() {
    const out = document.getElementById('em-settings-result');
    if (out) { out.textContent = 'در حال بررسی…'; out.className = 'small text-muted'; }
    try {
        // Save first. /email/verify reads the database, not the form, so
        // checking without saving tests the *previous* settings — and the
        // panel prefills host and port as a convenience, which means a fresh
        // load shows a complete-looking form whose values the server has
        // never seen. Pressing «بررسی اتصال» then failed a third time with
        // the boxes visibly filled in.
        await saveEmailSettings({ quiet: true });
        const d = await apiCall('/email/verify', { method: 'POST' });
        if (out) {
            out.textContent = d.ok
                ? `اتصال برقرار شد (${d.host}:${d.port})`
                : (d.error || 'ناموفق');
            out.className = 'small ' + (d.ok ? 'text-success' : 'text-danger');
        }
        const state = document.getElementById('em-state');
        if (state) state.textContent = d.ok ? 'متصل' : 'خطا';
    } catch (e) {
        if (out) { out.textContent = e.message || 'ناموفق'; out.className = 'small text-danger'; }
    }
}

async function sendEmailTest() {
    const to = document.getElementById('em-test-to')?.value?.trim();
    const out = document.getElementById('em-settings-result');
    if (!to) { if (out) { out.textContent = 'آدرس گیرنده را وارد کنید'; out.className = 'small text-warning'; } return; }
    if (out) { out.textContent = 'در حال ارسال…'; out.className = 'small text-muted'; }
    try {
        await saveEmailSettings({ quiet: true });   // same reason as بررسی اتصال
        const d = await apiCall(`/email/test?to=${encodeURIComponent(to)}`, { method: 'POST' });
        if (out) {
            out.textContent = d.ok ? 'ارسال شد — صندوق ورودی را ببینید' : (d.error || 'ناموفق');
            out.className = 'small ' + (d.ok ? 'text-success' : 'text-danger');
        }
        loadEmailMessages(); loadEmailStats();
    } catch (e) {
        if (out) { out.textContent = e.message || 'ناموفق'; out.className = 'small text-danger'; }
    }
}

async function sendOneEmail() {
    const to = document.getElementById('em-one-to')?.value?.trim();
    const subject = document.getElementById('em-one-subject')?.value?.trim();
    const message = document.getElementById('em-one-body')?.value?.trim();
    const out = document.getElementById('em-one-result');
    if (!to || !subject || !message) {
        if (out) { out.textContent = 'گیرنده، موضوع و متن لازم است'; out.className = 'small text-warning'; }
        return;
    }
    if (out) { out.textContent = 'در حال ارسال…'; out.className = 'small text-muted'; }
    try {
        const d = await apiCall('/email/send', {
            method: 'POST', body: JSON.stringify({ to, subject, message }) });
        if (out) {
            out.textContent = d.ok ? 'ارسال شد' : (d.error || 'ناموفق');
            out.className = 'small ' + (d.ok ? 'text-success' : 'text-danger');
        }
        if (d.ok) document.getElementById('em-one-body').value = '';
        loadEmailMessages(); loadEmailStats();
    } catch (e) {
        if (out) { out.textContent = e.message || 'ناموفق'; out.className = 'small text-danger'; }
    }
}

async function loadEmailTemplates() {
    const sel = document.getElementById('em-template');
    if (!sel) return;
    try {
        const d = await apiCall('/email/templates');
        sel.innerHTML = (d.templates || [])
            .map(t => `<option value="${esc(t.key)}">${esc(t.label)}</option>`).join('');
        previewEmailTemplate();
    } catch (_) {
        sel.innerHTML = '<option>—</option>';
    }
}

async function previewEmailTemplate() {
    const frame = document.getElementById('em-preview');
    const name = document.getElementById('em-template')?.value;
    if (!frame || !name) return;
    try {
        // Fetched rather than pointed at with src, so the auth header goes
        // with it — the preview route sits behind the same permission as the
        // rest of the panel.
        const html = await apiCall(`/email/preview/${encodeURIComponent(name)}`, { raw: true });
        frame.srcdoc = typeof html === 'string' ? html : '';
    } catch (e) {
        frame.srcdoc = `<p style="font-family:Tahoma;color:#f88;padding:12px">${esc(e.message || 'خطا')}</p>`;
    }
}

async function loadEmailMessages() {
    const body = document.getElementById('em-messages-body');
    if (!body) return;
    const status = document.getElementById('em-filter-status')?.value || '';
    const template = document.getElementById('em-filter-template')?.value || '';
    const qs = new URLSearchParams({ limit: '50' });
    if (status) qs.set('status', status);
    if (template) qs.set('template', template);
    try {
        const d = await apiCall(`/email/messages?${qs}`);
        if (!d.items?.length) {
            body.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">ایمیلی نیست</td></tr>';
        } else {
            body.innerHTML = d.items.map(m => {
                const ok = m.status === 'sent';
                return `<tr>
                  <td dir="ltr">${esc(m.to_email)}</td>
                  <td class="text-truncate" style="max-width:220px" title="${esc(m.subject || '')}">${esc(m.subject || '—')}</td>
                  <td>${esc(m.template || '—')}</td>
                  <td><span class="badge ${ok ? 'bg-success' : 'bg-danger'}">${ok ? 'ارسال‌شده' : 'ناموفق'}</span>
                      ${m.error ? `<div class="text-danger" style="font-size:.7rem">${esc(m.error)}</div>` : ''}</td>
                  <td>${esc(m.sent_by || '—')}</td>
                  <td dir="ltr" class="text-muted small">${m.created_at ? new Date(m.created_at).toLocaleString('fa-IR') : '—'}</td>
                </tr>`;
            }).join('');
        }
        const tot = document.getElementById('em-messages-total');
        if (tot) tot.textContent = `${faNum(d.total)} ایمیل`;
    } catch (e) {
        body.innerHTML = `<tr><td colspan="6" class="text-center text-danger py-3">${esc(e.message || 'خطا')}</td></tr>`;
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// AUTH UX
//
// Built against authgear.com/post/login-signup-ux-guide. The parts that
// changed behaviour rather than markup:
//
//   · Errors land at the field that caused them, and every one names what to
//     do next. A dead end is what makes someone abandon a login.
//   · Inputs are never cleared on failure. Retyping a whole form because one
//     field was wrong is the single most irritating thing a login can do.
//   · Caps Lock is surfaced. Silent capitals are the most common cause of a
//     failure nobody can explain to themselves.
//   · The one-time code submits itself at six digits — there is nothing else
//     that field could be waiting for.
// ═══════════════════════════════════════════════════════════════════════════

function _authField(id) { return document.getElementById('f-' + id); }

function setFieldError(id, message) {
    const wrap = _authField(id);
    const box = document.getElementById('e-' + id);
    if (box) {
        box.textContent = message || '';
        box.classList.toggle('show', !!message);
    }
    if (wrap) wrap.classList.toggle('invalid', !!message);
}

function clearAuthErrors() {
    document.querySelectorAll('.field-error').forEach(e => {
        e.textContent = ''; e.classList.remove('show');
    });
    document.querySelectorAll('.auth-field').forEach(f => f.classList.remove('invalid'));
    const box = document.getElementById('login-error');
    if (box) { box.classList.remove('show'); box.innerHTML = ''; }
}

/** A summary problem plus the way out of it. `fix` is the recovery step. */
function showAuthAlert(message, fix = '', kind = 'err') {
    const box = document.getElementById('login-error');
    if (!box) return;
    box.className = `auth-alert ${kind} show`;
    box.innerHTML = esc(message) + (fix ? `<span class="fix">${esc(fix)}</span>` : '');
}

// ── show / hide password ───────────────────────────────────────────────────
// Paste stays enabled on purpose: blocking it breaks password managers, which
// hold most people's strongest passwords.
document.addEventListener('click', e => {
    const btn = e.target.closest('.pw-toggle');
    if (!btn) return;
    const input = document.getElementById(btn.dataset.for);
    if (!input) return;
    const showing = input.type === 'text';
    input.type = showing ? 'password' : 'text';
    btn.querySelector('i').className = showing ? 'bi bi-eye' : 'bi bi-eye-slash';
    const label = showing ? 'نمایش رمز عبور' : 'پنهان کردن رمز عبور';
    btn.setAttribute('aria-label', label);
    btn.title = label;
    input.focus();
});

// ── Caps Lock ──────────────────────────────────────────────────────────────
function _watchCaps(inputId, warnId) {
    const input = document.getElementById(inputId);
    const warn = document.getElementById(warnId);
    if (!input || !warn) return;
    const check = ev => {
        // getModifierState is unavailable on some mobile keyboards; absent is
        // simply "we cannot tell", so say nothing rather than guess.
        const on = ev.getModifierState && ev.getModifierState('CapsLock');
        warn.classList.toggle('show', !!on);
    };
    input.addEventListener('keyup', check);
    input.addEventListener('keydown', check);
    input.addEventListener('blur', () => warn.classList.remove('show'));
}

// ── password strength ──────────────────────────────────────────────────────
// Deliberately about composition and length, not an entropy score. A number a
// person cannot influence is not guidance.
function _pwScore(v) {
    return {
        len:   v.length >= 8,
        lower: /[a-z]/.test(v),
        digit: /[0-9]/.test(v),
        upper: /[A-Z]/.test(v) || /[^A-Za-z0-9]/.test(v),
    };
}

function _paintStrength() {
    const input = document.getElementById('reg-password');
    if (!input) return;
    const rules = _pwScore(input.value);
    const met = Object.values(rules).filter(Boolean).length;

    document.querySelectorAll('#pw-rules li').forEach(li => {
        li.classList.toggle('met', !!rules[li.dataset.rule]);
    });

    const bar = document.getElementById('pw-meter-bar');
    const label = document.getElementById('pw-meter-label');
    const steps = [
        { w: '0%',   c: 'transparent',      t: 'قدرت رمز عبور' },
        { w: '25%',  c: 'var(--danger)',    t: 'خیلی ضعیف' },
        { w: '50%',  c: 'var(--warning)',   t: 'ضعیف' },
        { w: '75%',  c: 'var(--warning2)',  t: 'متوسط' },
        { w: '100%', c: 'var(--accent3)',   t: 'قوی' },
    ][input.value ? met : 0];
    if (bar) { bar.style.width = steps.w; bar.style.background = steps.c; }
    if (label) label.textContent = steps.t;
}

// ── one-time code ──────────────────────────────────────────────────────────
function _wireOtp(inputId, submit) {
    const el = document.getElementById(inputId);
    if (!el) return;
    el.addEventListener('input', () => {
        // Persian digits arrive from a Persian keyboard; the server wants ASCII.
        el.value = el.value.replace(/[۰-۹]/g, d => '۰۱۲۳۴۵۶۷۸۹'.indexOf(d))
                           .replace(/[^0-9]/g, '');
        if (el.value.length === el.maxLength) submit();
    });
}

// ── recovery paths ─────────────────────────────────────────────────────────
// Honest rather than decorative: there is no self-service reset for panel
// accounts, so this says who can actually do it instead of linking to a page
// that would only apologise.
function showForgotHelp() {
    // There is a self-service reset now. This used to say «ask a super admin»,
    // which is no answer at all when the account locked out IS the super admin
    // — that was the situation the owner ended up in, and the only way back was
    // the database.
    showResetForm();
}

function showTotpHelp() {
    showAuthAlert(
        'به اپلیکیشن احراز هویت دسترسی ندارید؟',
        'تنها مدیر ارشد می‌تواند احراز هویت دو مرحله‌ای را برای حساب شما غیرفعال کند. با او تماس بگیرید — به دلایل امنیتی این کار از این صفحه ممکن نیست.',
        'warn');
}

document.addEventListener('DOMContentLoaded', () => {
    _watchCaps('login-password', 'caps-login');
    _watchCaps('reg-password', 'caps-reg');
    const pw = document.getElementById('reg-password');
    if (pw) pw.addEventListener('input', _paintStrength);
    // Every code field, not just the authenticator's. The two added with the
    // email second factor were left unwired, so on a Persian keyboard — the
    // default for this audience — the digits went up as ۱۲۳۴۵ and came back
    // «کد نادرست است», which reads as the code being wrong rather than the
    // keyboard.
    _wireOtp('login-email-code', () => verifyEmailLogin());
    _wireOtp('phone-code', () => confirmPhoneCode());
    _wireOtp('reset-code', () => {});
    _wireOtp('login-totp-code', () => {
        const b = document.getElementById('login-totp-btn');
        if (b && !b.disabled) verifyTotpLogin();
    });
});


// ── returning user ─────────────────────────────────────────────────────────
// Only the username, and only ever the username. Storing anything more would
// turn a convenience into a liability on a shared machine.
function _rememberUser(username) {
    try {
        if (document.getElementById('login-remember')?.checked === false) {
            localStorage.setItem('sf_remember', '0');
            localStorage.removeItem('sf_last_user');
            return;
        }
        localStorage.setItem('sf_remember', '1');
        localStorage.setItem('sf_last_user', username);
    } catch (_) { /* private mode — the greeting is optional */ }
}

function forgetUser() {
    try { localStorage.removeItem('sf_last_user'); } catch (_) {}
    document.getElementById('welcome-back')?.classList.add('d-none');
    const u = document.getElementById('login-username');
    if (u) { u.value = ''; u.focus(); }
}

function _greetReturningUser() {
    let last = null;
    try { last = localStorage.getItem('sf_last_user'); } catch (_) {}
    if (!last) return false;
    const u = document.getElementById('login-username');
    const box = document.getElementById('welcome-back');
    const name = document.getElementById('welcome-name');
    if (u) u.value = last;
    if (name) name.textContent = last;
    if (box) box.classList.remove('d-none');
    return true;
}

// ── validate as they go, not at the end ────────────────────────────────────
// Checking the whole form only on submit means someone completes six fields
// and is then told the second one was wrong. Validation runs on blur — after
// they have finished a field, never while they are still mid-word.
function _validateOnBlur(fieldId, check) {
    const el = document.querySelector(`#f-${fieldId} input`);
    if (!el) return;
    el.addEventListener('blur', () => {
        if (!el.value.trim()) return;            // empty is the submit's job
        setFieldError(fieldId, check(el.value.trim()) || '');
    });
    // Clear the moment they start fixing it — a red field they are actively
    // correcting is just nagging.
    el.addEventListener('input', () => setFieldError(fieldId, ''));
}

document.addEventListener('DOMContentLoaded', () => {
    // Autofocus: the password when we already know who this is, the username
    // otherwise. Skipped on touch devices, where focusing raises the keyboard
    // and hides the form the person was about to read.
    const touch = window.matchMedia?.('(pointer: coarse)').matches;
    const known = _greetReturningUser();
    if (!touch) {
        const target = known ? 'login-password' : 'login-username';
        document.getElementById(target)?.focus();
    }

    _validateOnBlur('reg-username', v =>
        v.length < 3 ? 'حداقل ۳ کاراکتر'
        : !/^[A-Za-z0-9._-]+$/.test(v) ? 'فقط حروف انگلیسی، عدد، نقطه، خط تیره و زیرخط' : '');
    _validateOnBlur('reg-fullname', v => v.length < 2 ? 'نام را کامل وارد کنید' : '');
    _validateOnBlur('reg-divar-phone', v =>
        /^09\d{9}$/.test(v) ? '' : 'شماره باید با فرمت ۰۹۱۲۳۴۵۶۷۸۹ باشد');
    _validateOnBlur('reg-password2', v =>
        v === document.getElementById('reg-password')?.value ? '' : 'با رمز عبور بالا یکسان نیست');
});


// ── animated brand mark ────────────────────────────────────────────────────
//
// Loaded after the form is interactive, never before. The player is 168 KB and
// the animation is decoration — making someone wait on it to reach a password
// field would be the wrong trade, and this page's whole job is to get out of
// the way.
//
// Both files are served from our own origin. The site makes no third-party
// requests, which matters on a network where a CDN may be slow or blocked, and
// lottie-web is MIT so vendoring it is allowed (see js/vendor/*.LICENSE.md).
//
// Skipped entirely when: the visitor asked for reduced motion, the pointer is
// coarse (the card should own a small screen, not share it with an ornament),
// or the browser is saving data.
function _initAuthMark() {
    const host = document.getElementById('auth-lottie');
    if (!host) return;

    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const coarse  = window.matchMedia?.('(pointer: coarse)').matches;
    const saver   = navigator.connection?.saveData;
    if (reduced || coarse || saver) return;

    const script = document.createElement('script');
    script.src = '/dashboard/js/vendor/lottie_light.min.js';
    script.async = true;
    script.onload = () => {
        try {
            window.lottie.loadAnimation({
                container: host,
                renderer: 'svg',
                loop: true,
                autoplay: true,
                // Versioned so a redeploy is never served a stale animation —
                // this cost real time to diagnose once already.
                path: '/dashboard/assets/auth-mark.json?v=2',
            });
            host.classList.add('ready');
        } catch (_) { /* the static icon is already there */ }
    };
    // No onerror handler beyond ignoring it: a missing ornament is not an
    // error worth telling anyone about.
    script.onerror = () => {};
    document.head.appendChild(script);
}

// requestIdleCallback where it exists, so this never competes with the form
// for the main thread on a slow device.
document.addEventListener('DOMContentLoaded', () => {
    const go = () => _initAuthMark();
    if ('requestIdleCallback' in window) requestIdleCallback(go, { timeout: 2500 });
    else setTimeout(go, 1200);
});

// ── گزارش اسکرپ ────────────────────────────────────────────────────────────
//
// scraper.log cannot answer «این ران چرا نصفه ماند؟»: no line in it carries a
// job id, so two runs in a day interleave, and it rotates at 10 MB with seven
// days of retention — the run somebody wants is usually the one that aged out.
// These events are written by the scraper on its own connection, so a run that
// dies still leaves its account of what it was doing.

const JOB_STAGE_FA = {
    start:     ['شروع',        'bi-play-circle',      'text-info'],
    session:   ['نشست دیوار',  'bi-person-badge',     'text-warning'],
    page:      ['صفحه',        'bi-file-earmark',     'text-muted'],
    item:      ['آگهی',        'bi-house',            'text-muted'],
    challenge: ['چالش دیوار',  'bi-shield-exclamation', 'text-warning'],
    pause:     ['توقف',        'bi-pause-circle',     'text-warning'],
    resume:    ['ادامه',       'bi-play-fill',        'text-info'],
    finish:    ['پایان',       'bi-check-circle',     'text-success'],
    error:     ['خطا',         'bi-x-octagon',        'text-danger'],
};

async function showJobLog(jobId) {
    const body = document.getElementById('joblog-body');
    const el = document.getElementById('jobLogModal');
    if (!body || !el) return;

    body.innerHTML = '<div class="text-muted small">در حال بارگذاری…</div>';
    new bootstrap.Modal(el).show();

    try {
        const d = await apiCall(`/scraper/jobs/${encodeURIComponent(jobId)}/events`);
        if (!d.items?.length) {
            body.innerHTML = `<div class="text-muted small">
                برای این اسکرپ رویدادی ثبت نشده است. رانی که پیش از افزوده‌شدن این
                گزارش اجرا شده باشد، سابقه‌ای ندارد.</div>`;
            return;
        }
        body.innerHTML = d.items.map(e => {
            const [label, icon, cls] = JOB_STAGE_FA[e.stage] || ['—', 'bi-dot', 'text-muted'];
            const lvl = e.level === 'error' ? 'text-danger'
                      : e.level === 'warning' ? 'text-warning' : cls;
            const extra = Object.keys(e.details || {}).length
                ? `<div class="text-muted" style="font-size:.72rem" dir="ltr">${
                     esc(JSON.stringify(e.details))}</div>` : '';
            return `<div class="d-flex gap-2 py-2" style="border-bottom:1px solid var(--border)">
                <i class="bi ${icon} ${lvl}" style="margin-top:.15rem"></i>
                <div class="flex-grow-1">
                  <div class="d-flex justify-content-between gap-2">
                    <span class="small ${lvl}">${esc(label)}</span>
                    <span class="text-muted" style="font-size:.72rem" dir="ltr">${
                       e.created_at ? new Date(e.created_at).toLocaleString('fa-IR') : ''}</span>
                  </div>
                  <div style="font-size:.85rem">${esc(e.message || '')}</div>
                  ${extra}
                </div>
              </div>`;
        }).join('');
    } catch (err) {
        body.innerHTML = `<div class="text-danger small">${esc(err.message || 'خطا')}</div>`;
    }
}


/* ══════════════════════════════════════════════════════
   آگهی‌های اسکرپ‌نشده
   ══════════════════════════════════════════════════════

   A run reports «۳۲ خارج از دسته‌بندی، ۲ ودیعه، ۳ ناموفق» and that number
   can be checked for arithmetic and nothing else — whether those 32 were
   promoted junk or 32 real apartments is not something a count can say.
   This is the listings themselves, each with the link that lets one be
   scraped again on its own.                                              */

let _skippedRows = [];      // what the open modal is showing
let _skippedFilter = null;  // the bucket being shown, or null for all

async function showSkipped(jobId) {
    const body = document.getElementById('skipped-body');
    const summary = document.getElementById('skipped-summary');
    const el = document.getElementById('skippedModal');
    if (!body || !el) return;

    _skippedFilter = null;
    summary.innerHTML = '';
    body.innerHTML = '<div class="text-muted small">در حال بارگذاری…</div>';
    new bootstrap.Modal(el).show();

    try {
        const d = await apiCall(`/scraper/jobs/${encodeURIComponent(jobId)}/skipped`);
        _skippedRows = d.items || [];
        if (!_skippedRows.length) {
            summary.innerHTML = '';
            body.innerHTML = `<div class="text-muted small">
                این اسکرپ همهٔ آگهی‌هایی را که دید ذخیره کرد — چیزی کنار گذاشته نشد.
                رانی که پیش از افزوده‌شدن این بخش اجرا شده باشد هم سابقه‌ای ندارد.</div>`;
            return;
        }
        renderSkippedSummary(d.by_reason || {});
        renderSkippedRows();
    } catch (err) {
        body.innerHTML = `<div class="text-danger small">${esc(err.message || 'خطا')}</div>`;
    }
}

function renderSkippedSummary(byReason) {
    const summary = document.getElementById('skipped-summary');
    const total = _skippedRows.length;
    const chip = (key, label, count) => `
        <button class="btn btn-sm ${_skippedFilter === key ? 'btn-primary' : 'btn-outline-secondary'}"
                onclick="filterSkipped(${key === null ? 'null' : `'${key}'`})">
            ${esc(label)} <bdi class="badge bg-secondary">${count}</bdi>
        </button>`;
    summary.innerHTML = `<div class="d-flex flex-wrap gap-2 align-items-center">
        ${chip(null, 'همه', total)}
        ${Object.entries(byReason).map(([k, v]) => chip(k, v.label, v.count)).join('')}
        <button class="btn btn-sm btn-outline-secondary ms-auto" onclick="copySkippedLinks()">
            <i class="bi bi-clipboard"></i> کپی همهٔ لینک‌ها
        </button>
        <button class="btn btn-sm btn-primary" onclick="rescrapeAllSkipped()"
                title="همهٔ آنچه الان نمایش داده می‌شود، در یک تسک دوباره باز می‌شود"
                ${rescrapeCandidates().length ? '' : 'disabled'}>
            <i class="bi bi-arrow-repeat"></i> بازاسکرپ همه (${rescrapeCandidates().length})
        </button>
    </div>`;
}

function filterSkipped(reason) {
    _skippedFilter = reason;
    const byReason = {};
    _skippedRows.forEach(r => {
        byReason[r.reason] = byReason[r.reason]
            || { label: r.reason_label || r.reason, count: 0 };
        byReason[r.reason].count++;
    });
    renderSkippedSummary(byReason);
    renderSkippedRows();
}

function visibleSkipped() {
    return _skippedFilter
        ? _skippedRows.filter(r => r.reason === _skippedFilter)
        : _skippedRows;
}

/* What «بازاسکرپ همه» would open right now: exactly the rows on screen.
 * The button's number and the action both read this, so they cannot
 * disagree — «(8)» over a list of four was the two being computed
 * separately. Chat-only rows are included too: that verdict has been
 * wrong before, and a second look is the only way to find out. */
function rescrapeCandidates() {
    return visibleSkipped();
}

function renderSkippedRows() {
    const body = document.getElementById('skipped-body');
    const rows = visibleSkipped();
    if (!rows.length) {
        body.innerHTML = '<div class="text-muted small">موردی با این دلیل نیست.</div>';
        return;
    }
    body.innerHTML = rows.map(r => `
        <div class="d-flex gap-2 py-2 align-items-start"
             style="border-bottom:1px solid var(--border)">
          <div class="flex-grow-1" style="min-width:0">
            <div style="font-size:.85rem">${esc(r.title || r.divar_id || '—')}</div>
            <div class="text-muted" style="font-size:.72rem">
              ${esc(r.reason_label || r.reason || '')}${r.detail ? ' — ' + esc(r.detail) : ''}
            </div>
          </div>
          <a class="btn btn-sm btn-outline-secondary" href="${esc(r.url)}"
             target="_blank" rel="noopener" title="باز کردن در دیوار">
            <i class="bi bi-box-arrow-up-left"></i>
          </a>
          <button class="btn btn-sm btn-outline-primary"
                  onclick="rescrapeSkipped(${jsArg(r.url)})" title="اسکرپ تکی این آگهی">
            <i class="bi bi-arrow-repeat"></i>
          </button>
        </div>`).join('');
}

function rescrapeSkipped(url) {
    const input = document.getElementById('single-url');
    if (input) input.value = url;
    const el = document.getElementById('skippedModal');
    const modal = el && bootstrap.Modal.getInstance(el);
    if (modal) modal.hide();
    // The single-scrape box is where this ends up either way; filling it and
    // running it is the same two steps done by hand.
    scrapeSingle();
}

/* «یه علامت رفرش کلی دقیقاً همین فیلد بذار وقتی اونو بزنم همه رو اسکرپ کنه.»
 * Whatever the modal is showing — all of it, or one bucket — as one run.
 * The bucket filter is respected: «بازاسکرپ همه» on «بدون شماره» re-opens
 * the phoneless ones and leaves the chat-only ones, which no run will ever
 * fill, alone. */
async function rescrapeAllSkipped() {
    const urls = rescrapeCandidates().map(r => r.url).filter(Boolean);
    if (!urls.length) { showToast('خبری نیست', 'چیزی برای بازاسکرپ نمایش داده نمی‌شود', 'warning'); return; }
    const ok = await askConfirm({
        icon: 'bi-arrow-repeat', title: 'بازاسکرپ همه',
        body: `${urls.length} آگهی در یک تسک دوباره باز می‌شود. برای هر کدام یک افشا خرج می‌شود.`,
        okLabel: `شروع (${urls.length})`,
    });
    if (!ok) return;
    try {
        const label = _skippedFilter
            ? `بازاسکرپ — ${(_skippedRows.find(r => r.reason === _skippedFilter) || {}).reason_label || _skippedFilter}`
            : 'بازاسکرپ';
        const r = await apiCall('/scraper/rescrape', {
            method: 'POST', body: JSON.stringify({ urls, label }),
        });
        const el = document.getElementById('skippedModal');
        const modal = el && bootstrap.Modal.getInstance(el);
        if (modal) modal.hide();
        showToast('شروع شد', `بازاسکرپ ${urls.length} آگهی به‌عنوان تسک ${String(r.job_id).slice(0, 8)} شروع شد`, 'success');
        loadJobs();
    } catch (e) {
        showToast('خطا', e.message, 'danger');
    }
}

async function copySkippedLinks() {
    const links = visibleSkipped().map(r => r.url).join('\n');
    try {
        await navigator.clipboard.writeText(links);
        showToast('کپی شد', `${visibleSkipped().length} لینک در حافظه است`, 'success');
    } catch (e) {
        showToast('خطا', 'مرورگر اجازهٔ کپی نداد', 'warning');
    }
}


/* ══════════════════════════════════════════════════════
   هوش تصویری — CRM insights
   ══════════════════════════════════════════════════════

   One request feeds the whole page, so nothing on it can disagree with
   anything else because a second call landed a minute later.

   The formatting rule throughout: a value the server could not compute comes
   back as null and is rendered «—». Never 0, and never «۰٪». A zero that is
   really an unknown is the one kind of wrong a dashboard cannot recover from,
   because it looks exactly like an answer. */

let insTempChart = null, insTrendChart = null, insCityChart = null;

const INS_PALETTE = ['#a78bfa','#f0a6ff','#67e8f9','#6366f1','#fcd34d',
                     '#fb7185','#8b5cf6','#2dd4bf','#ec4899','#64748b'];

function _insNum(v) {
    return (v === null || v === undefined) ? '—' : Number(v).toLocaleString('fa-IR');
}
function _insPct(v) {
    return (v === null || v === undefined) ? '—' : `${Number(v).toLocaleString('fa-IR')}٪`;
}
function _insToman(v) {
    if (v === null || v === undefined) return '—';
    const n = Number(v);
    if (n >= 1e9) return `${(n / 1e9).toLocaleString('fa-IR', {maximumFractionDigits: 1})} میلیارد`;
    if (n >= 1e6) return `${(n / 1e6).toLocaleString('fa-IR', {maximumFractionDigits: 0})} میلیون`;
    return n.toLocaleString('fa-IR');
}

async function loadInsights() {
    const days = parseInt(document.getElementById('ins-window')?.value || '30', 10);
    let d;
    try {
        d = await apiCall(`/crm/insights?days=${days}`);
    } catch (e) {
        showToast('خطا', 'گزارش تحلیلی بارگذاری نشد', 'error');
        return;
    }
    if (!d) return;

    // ── headline numbers ──
    document.getElementById('ins-leads').textContent = _insNum(d.totals?.leads);
    document.getElementById('ins-conv').textContent = _insPct(d.totals?.conversion_rate);
    document.getElementById('ins-stalled').textContent = _insNum(d.stalled?.items?.length);
    document.getElementById('ins-commission').textContent = _insToman(d.deals?.commission_due);

    _insRenderFunnel(d.funnel || []);
    _insRenderTemp(d.temperature || []);
    _insRenderTrend(d.series || {});
    _insRenderCities(d.cities || []);
    _insRenderAgents(d.agents || []);
    _insRenderStalled(d.stalled || {});

    // Say what the charts are standing on. A funnel built from leads that are
    // 40% missing a phone number is worth looking at — but only with the 40%
    // on screen beside it.
    const cov = d.coverage || {};
    const parts = [];
    if (cov.leads_with_phone !== null && cov.leads_with_phone !== undefined)
        parts.push(`${_insPct(cov.leads_with_phone)} لیدها شمارهٔ تماس دارند`);
    if (cov.properties_with_phone !== null && cov.properties_with_phone !== undefined)
        parts.push(`${_insPct(cov.properties_with_phone)} املاک شمارهٔ تماس دارند`);
    document.getElementById('ins-coverage').textContent = parts.join(' · ');
}

function _insRenderFunnel(stages) {
    const host = document.getElementById('ins-funnel');
    if (!host) return;
    const max = Math.max(1, ...stages.map(s => s.count));
    const total = stages.reduce((a, s) => a + s.count, 0);

    host.innerHTML = stages.map((s, i) => {
        const pct = Math.round(s.count / max * 100);
        // An unexpected status gets a different colour and a marker rather
        // than being hidden — it is a data-quality finding, not noise.
        const colour = s.unexpected ? 'var(--warning, #f59e0b)' : INS_PALETTE[i % INS_PALETTE.length];
        return `
        <div style="margin-bottom:.7rem">
          <div class="d-flex justify-content-between align-items-baseline" style="font-size:.82rem">
            <span>${esc(s.label)}${s.unexpected ? ' <i class="bi bi-exclamation-triangle" title="وضعیت ناشناخته"></i>' : ''}</span>
            <strong>${_insNum(s.count)}</strong>
          </div>
          <div style="height:10px;border-radius:6px;background:var(--surface3);overflow:hidden;margin-top:.25rem">
            <div style="height:100%;width:${pct}%;border-radius:6px;background:${colour};transition:width .5s ease"></div>
          </div>
        </div>`;
    }).join('') || '<p class="text-muted small mb-0">هنوز لیدی ثبت نشده است.</p>';

    const note = document.getElementById('ins-funnel-note');
    if (note) note.textContent = total ? `مجموع ${_insNum(total)}` : '';
}

function _insRenderTemp(buckets) {
    const el = document.getElementById('ins-temp-chart');
    if (!el) return;
    if (insTempChart) insTempChart.destroy();
    const FA = { hot: 'داغ', warm: 'گرم', cold: 'سرد' };
    insTempChart = new Chart(el.getContext('2d'), {
        type: 'doughnut',
        data: {
            labels: buckets.map(b => FA[b.label] || b.label),
            datasets: [{
                data: buckets.map(b => b.count),
                backgroundColor: ['#fb7185', '#fcd34d', '#67e8f9', '#64748b'],
                borderColor: chartColors().surface, borderWidth: 0,
                borderRadius: 10, spacing: 4, hoverOffset: 14,
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false, cutout: '62%',
            plugins: { legend: { position: 'bottom',
                labels: { color: chartColors().text, font: { family: 'inherit' } } } }
        }
    });
}

function _insRenderTrend(series) {
    const el = document.getElementById('ins-trend-chart');
    if (!el) return;
    if (insTrendChart) insTrendChart.destroy();
    const c = chartColors();
    const leads = series.leads || [], props = series.properties || [];
    const labels = leads.map(p => {
        const dt = new Date(p.date);
        return dt.toLocaleDateString('fa-IR', { day: 'numeric', month: 'long' });
    });
    insTrendChart = new Chart(el.getContext('2d'), {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: 'لید', data: leads.map(p => p.count), borderColor: '#a78bfa',
                  backgroundColor: 'rgba(167,139,250,.12)', fill: true, tension: .38,
                  pointRadius: 0, borderWidth: 2 },
                { label: 'ملک', data: props.map(p => p.count), borderColor: '#67e8f9',
                  backgroundColor: 'rgba(103,232,249,.10)', fill: true, tension: .38,
                  pointRadius: 0, borderWidth: 2 },
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: { legend: { labels: { color: c.text } } },
            scales: {
                x: { ticks: { color: c.tick, maxTicksLimit: 8 }, grid: { color: c.grid } },
                y: { beginAtZero: true, ticks: { color: c.tick, precision: 0 },
                     grid: { color: c.grid } },
            }
        }
    });
}

function _insRenderCities(buckets) {
    const el = document.getElementById('ins-city-chart');
    if (!el) return;
    if (insCityChart) insCityChart.destroy();
    insCityChart = new Chart(el.getContext('2d'), {
        type: 'bar',
        data: {
            labels: buckets.map(b => b.label),
            datasets: [{
                data: buckets.map(b => b.count),
                backgroundColor: buckets.map((b, i) =>
                    b.is_other ? '#64748b' : INS_PALETTE[i % INS_PALETTE.length]),
                borderRadius: 8, borderWidth: 0,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { beginAtZero: true, ticks: { color: chartColors().tick, precision: 0 },
                     grid: { color: chartColors().grid } },
                y: { ticks: { color: chartColors().tick }, grid: { display: false } },
            }
        }
    });
}

function _insRenderAgents(agents) {
    const tb = document.getElementById('ins-agents');
    if (!tb) return;
    if (!agents.length) {
        tb.innerHTML = '<tr><td colspan="6" class="text-muted small text-center py-4">' +
                       'هنوز عملکرد روزانه‌ای ثبت نشده است.</td></tr>';
        return;
    }
    tb.innerHTML = agents.map(a => `
        <tr>
          <td><strong>${esc(a.agent)}</strong>
              <div class="text-muted" style="font-size:.7rem">${_insNum(a.days)} روز ثبت‌شده</div></td>
          <td>${_insNum(a.new_files)}</td>
          <td>${_insNum(a.showings)}</td>
          <td>${_insNum(a.offers)}</td>
          <td><strong>${_insNum(a.closed)}</strong></td>
          <td>${a.showings_per_close === null ? '—' : _insNum(a.showings_per_close)}</td>
        </tr>`).join('');
}

function _insRenderStalled(stalled) {
    const tb = document.getElementById('ins-stalled-list');
    const note = document.getElementById('ins-stalled-note');
    if (!tb) return;
    const items = stalled.items || [];
    if (note) note.textContent = `بیش از ${_insNum(stalled.after_days)} روز`;
    if (!items.length) {
        tb.innerHTML = '<tr><td colspan="4" class="text-muted small text-center py-4">' +
                       'هیچ لید معطلی نیست.</td></tr>';
        return;
    }
    tb.innerHTML = items.map(l => `
        <tr>
          <td>${esc(l.seller_name || l.phone_number || '—')}</td>
          <td>${esc(l.city_name || '—')}</td>
          <td><span class="badge bg-secondary-subtle text-body-secondary">${esc(l.status_label)}</span></td>
          <td><strong>${_insNum(l.idle_days)}</strong> روز</td>
        </tr>`).join('');
}


/* ══════════════════════════════════════════════════════
   هوش تصویری — the visual half
   ══════════════════════════════════════════════════════

   What the photographs and the prices actually say, measured rather than
   estimated: listings priced away from their district, listings that look
   like the same flat posted twice, and galleries with weak photographs.

   Same rule as the pipeline half: a value the server could not compute
   renders «—». The valuation refuses to answer without enough comparables,
   and that refusal is shown as a count on the page rather than hidden — a
   headline of «۳ زیر قیمت» means something very different when only 40 of
   1100 listings could be judged at all. */

let _insTab = 'visual';

function insTab(which) {
    _insTab = which;
    for (const t of ['visual', 'pipeline']) {
        const pane = document.getElementById(`ins-pane-${t}`);
        const tab = document.getElementById(`ins-tab-${t}`);
        if (pane) pane.style.display = (t === which) ? '' : 'none';
        if (tab) tab.classList.toggle('active', t === which);
    }
    if (which === 'visual') loadVisual();
    else loadInsights();
}

async function loadVisual() {
    let d;
    try {
        d = await apiCall('/properties/visual/overview');
    } catch (e) {
        showToast('خطا', 'تحلیل تصویری بارگذاری نشد', 'error');
        return;
    }
    if (!d) return;

    const v = d.valuation || {}, dup = d.duplicates || {}, ph = d.photos || {};

    document.getElementById('vis-under').textContent = _insNum((v.under || []).length);
    document.getElementById('vis-dupes').textContent = _insNum(dup.total_pairs);
    document.getElementById('vis-weak').textContent = _insNum((ph.weak || []).length);
    document.getElementById('vis-judged').textContent = _insNum(v.judged);

    // Say what the numbers rest on, in the same breath as the numbers. A
    // headline built from 40 of 1100 listings is not wrong, but it is not
    // what it looks like either.
    document.getElementById('vis-coverage').innerHTML =
        `از ${_insNum(v.total)} ملک، <strong>${_insNum(v.judged)}</strong> قابل ارزش‌گذاری بود — ` +
        `بقیه یا متراژ ندارند یا محله‌شان هنوز به ${_insNum(v.min_comparables)} آگهی مشابه نرسیده. ` +
        `${_insNum(v.districts_with_a_benchmark)} محله از ${_insNum(v.districts_seen)} محله پایهٔ قیمت دارد.`;

    _visUnder(v.under || []);
    _visDupes(dup);
    _visPhotos(ph);
    if (typeof aiLoadPhotoStatus === 'function') aiLoadPhotoStatus(document.getElementById('ai-photo-status'));
}

function _visUnder(rows) {
    const tb = document.getElementById('vis-under-list');
    if (!tb) return;
    if (!rows.length) {
        tb.innerHTML = '<tr><td colspan="7" class="text-muted small text-center py-4">' +
            'هیچ ملکی به‌اندازهٔ قابل توجه زیر میانهٔ محله‌اش نیست.</td></tr>';
        return;
    }
    tb.innerHTML = rows.map(r => `
        <tr>
          <td><code>${_insNum(r.serial_no)}</code></td>
          <td style="max-width:260px" class="text-truncate" title="${esc(r.title)}">${esc(r.title)}</td>
          <td>${esc(r.district || '—')}</td>
          <td>${_insNum(r.area)}</td>
          <td>${_insToman(r.ppm)}</td>
          <td><span class="badge bg-success-subtle text-body-secondary">${_insNum(r.delta_pct)}٪</span></td>
          <td class="text-muted" style="font-size:.72rem">${_insNum(r.sample)} آگهی${r.confidence === 'thin' ? ' ⚠' : ''}</td>
        </tr>`).join('');
}

function _visDupes(dup) {
    const tb = document.getElementById('vis-dupe-list');
    const note = document.getElementById('vis-dupe-note');
    if (!tb) return;
    if (note) note.textContent =
        `${_insNum(dup.with_hashes)} آگهی عکس اثرانگشت‌شده دارد · ` +
        `${_insNum(dup.boilerplate_ignored)} عکس تکراری (لوگو/نما) نادیده گرفته شد`;
    const pairs = dup.pairs || [];
    if (!pairs.length) {
        tb.innerHTML = '<tr><td colspan="4" class="text-muted small text-center py-4">' +
            'ملک تکراری پیدا نشد. عکس‌ها از اسکرپ بعدی اثرانگشت می‌گیرند.</td></tr>';
        return;
    }
    tb.innerHTML = pairs.map(p => {
        // null when a price is missing; 0 when they genuinely match. Those
        // are different answers and «—» only belongs to the first — two
        // agencies quoting the same figure is a fact worth seeing, and the
        // first draft rendered it as unknown because 0 is falsy.
        const gap = (p.a.price && p.b.price) ? Math.abs(p.a.price - p.b.price) : null;
        const cls = p.verdict === 'duplicate' ? 'bg-danger-subtle' : 'bg-warning-subtle';
        return `
        <tr>
          <td><span class="badge ${cls} text-body-secondary">${esc(p.note)}</span></td>
          <td><code>${_insNum(p.a.serial_no)}</code> ${esc(p.a.seller || '—')}<br>
              <span class="text-muted" style="font-size:.72rem">${_insToman(p.a.price)}</span></td>
          <td><code>${_insNum(p.b.serial_no)}</code> ${esc(p.b.seller || '—')}<br>
              <span class="text-muted" style="font-size:.72rem">${_insToman(p.b.price)}</span></td>
          <td>${gap === null ? '—'
                : gap === 0 ? '<span class="text-muted">هر دو یک قیمت</span>'
                : `<strong>${_insToman(gap)}</strong>`}</td>
        </tr>`;
    }).join('');
}

const PHOTO_PROBLEM_FA = { blurry: 'تار', exposure: 'نور نامناسب', small: 'رزولوشن پایین' };

function _visPhotos(ph) {
    const tb = document.getElementById('vis-photo-list');
    const note = document.getElementById('vis-photo-note');
    if (!tb) return;
    const problems = ph.problems || {};
    if (note) {
        const parts = Object.entries(problems)
            .map(([k, n]) => `${PHOTO_PROBLEM_FA[k] || k}: ${_insNum(n)}`);
        note.textContent = parts.length
            ? parts.join(' · ')
            : `${_insNum(ph.scored_listings)} آگهی سنجیده شد`;
    }
    const weak = ph.weak || [];
    if (!weak.length) {
        tb.innerHTML = '<tr><td colspan="5" class="text-muted small text-center py-4">' +
            'عکس ضعیفی پیدا نشد. کیفیت عکس‌ها از اسکرپ بعدی سنجیده می‌شود.</td></tr>';
        return;
    }
    tb.innerHTML = weak.map(w => `
        <tr>
          <td><code>${_insNum(w.serial_no)}</code></td>
          <td style="max-width:280px" class="text-truncate" title="${esc(w.title)}">${esc(w.title)}</td>
          <td>${_insNum(w.count)}</td>
          <td><strong>${_insNum(w.worst)}</strong></td>
          <td>${(w.problems || []).map(p =>
              `<span class="badge bg-warning-subtle text-body-secondary me-1">${esc(PHOTO_PROBLEM_FA[p] || p)}</span>`
          ).join('') || '—'}</td>
        </tr>`).join('');
}

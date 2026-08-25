/**
 * SorinFlow Divar Scraper - Dashboard JavaScript
 */

const API_BASE = '/api';
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

function getToken() { return localStorage.getItem('sf_token'); }
function setToken(t) { localStorage.setItem('sf_token', t); _authToken = t; }
function clearToken() { localStorage.removeItem('sf_token'); _authToken = null; _currentUser = null; _totpSession = null; }

// ═══ Theme (dark / light) ═════════════════════════════════════
// data-theme is set on <html> before first paint by an inline
// script in index.html; persisted in localStorage "sf-theme".
function currentTheme() { return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'; }

function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
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

// ═══ Login / Logout ═══════════════════════════════════════════
async function doLogin() {
    const username = document.getElementById('login-username').value.trim();
    const password = document.getElementById('login-password').value;
    const errEl = document.getElementById('login-error');
    const btn = document.getElementById('login-btn');

    if (!username || !password) { errEl.textContent = 'نام کاربری و رمز عبور الزامی است'; errEl.classList.remove('d-none'); return; }

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> در حال ورود...';
    errEl.classList.add('d-none');

    try {
        const form = new URLSearchParams();
        form.append('username', username);
        form.append('password', password);

        const resp = await fetch(`${API_BASE}/users/token`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: form.toString()
        });
        const data = await resp.json();

        if (!resp.ok) { throw new Error(data.detail || 'خطا در ورود'); }

        if (data.requires_totp) {
            _totpSession = data.totp_session;
            document.getElementById('login-step-1').classList.add('d-none');
            document.getElementById('login-step-2').classList.remove('d-none');
            document.getElementById('login-totp-code').focus();
            return;
        }

        _finishLogin(data);

    } catch (err) {
        errEl.textContent = err.message;
        errEl.classList.remove('d-none');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-box-arrow-in-right"></i> ورود به داشبورد';
    }
}

async function verifyTotpLogin() {
    const code = document.getElementById('login-totp-code').value.trim();
    const errEl = document.getElementById('login-error');
    const btn = document.getElementById('login-totp-btn');

    if (!code || code.length !== 6) { errEl.textContent = 'کد ۶ رقمی را وارد کنید'; errEl.classList.remove('d-none'); return; }

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> در حال تأیید...';
    errEl.classList.add('d-none');

    try {
        const resp = await fetch(`${API_BASE}/users/token/verify-totp`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ totp_session: _totpSession, code })
        });
        const data = await resp.json();
        if (!resp.ok) { throw new Error(data.detail || 'کد اشتباه است'); }
        _finishLogin(data);
    } catch (err) {
        errEl.textContent = err.message;
        errEl.classList.remove('d-none');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-check-circle"></i> تأیید';
    }
}

function backToLogin() {
    _totpSession = null;
    document.getElementById('login-step-2').classList.add('d-none');
    document.getElementById('login-step-1').classList.remove('d-none');
    document.getElementById('login-totp-code').value = '';
    document.getElementById('login-error').classList.add('d-none');
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
    const username   = document.getElementById('reg-username').value.trim();
    const full_name  = document.getElementById('reg-fullname').value.trim();
    const divar_phone = document.getElementById('reg-divar-phone').value.trim() || null;
    const password   = document.getElementById('reg-password').value;
    const password2  = document.getElementById('reg-password2').value;
    const errEl      = document.getElementById('login-error');
    const btn        = document.getElementById('reg-btn');

    errEl.classList.add('d-none');

    if (!username) { errEl.textContent = 'نام کاربری الزامی است'; errEl.classList.remove('d-none'); return; }
    if (!password || password.length < 6) { errEl.textContent = 'رمز عبور باید حداقل ۶ کاراکتر باشد'; errEl.classList.remove('d-none'); return; }
    if (password !== password2) { errEl.textContent = 'رمزهای عبور یکسان نیستند'; errEl.classList.remove('d-none'); return; }
    if (divar_phone && !/^09\d{9}$/.test(divar_phone)) { errEl.textContent = 'شماره دیوار باید با فرمت 09xxxxxxxxx باشد'; errEl.classList.remove('d-none'); return; }

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> در حال ثبت‌نام...';

    try {
        const resp = await fetch(`${API_BASE}/users/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, full_name: full_name || null, divar_phone, password })
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || 'خطا در ثبت‌نام');

        // Auto-login after successful registration
        const form = new URLSearchParams();
        form.append('username', username);
        form.append('password', password);
        const loginResp = await fetch(`${API_BASE}/users/token`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: form.toString()
        });
        const loginData = await loginResp.json();
        if (!loginResp.ok) throw new Error(loginData.detail || 'ثبت نام موفق، لطفاً وارد شوید');
        _finishLogin(loginData);

    } catch (err) {
        errEl.textContent = err.message;
        errEl.classList.remove('d-none');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-person-plus"></i> ایجاد حساب کاربری';
    }
}

function _finishLogin(data) {
    setToken(data.access_token);
    _currentUser = { username: data.username, role: data.role, full_name: data.full_name };
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
            qrContainer.innerHTML = `<div class="small text-muted">${data.qr_uri}</div>`;
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

function copyTotpSecret() {
    const val = document.getElementById('totp-secret-display').value;
    navigator.clipboard.writeText(val).then(() => showToast('کپی شد', 'کلید در کلیپ‌بورد کپی شد', 'success'));
}

// ═══ Hash router: #/login, #/dashboard, #/properties, ... ═══════
const ROUTE_SECTIONS = ['dashboard', 'properties', 'scraper', 'crm', 'auth', 'proxies', 'users'];
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
    // Deep link (#/crm etc.) wins over the role's default section
    const target = _intendedRoute || _hashToSection(location.hash) || _defaultSection();
    _intendedRoute = null;
    showSection(target);
}

// Which nav items are visible per role
const ROLE_NAV_VISIBILITY = {
    super_admin: ['nav-link-dashboard', 'nav-link-properties', 'nav-link-scraper', 'nav-link-crm', 'nav-link-auth', 'nav-link-proxies', 'nav-users'],
    admin:       ['nav-link-dashboard', 'nav-link-properties', 'nav-link-scraper', 'nav-link-crm', 'nav-link-auth', 'nav-link-proxies'],
    user:        ['nav-link-dashboard', 'nav-link-properties'],
};

function applyRoleUI() {
    if (!_currentUser) return;
    const { role, username, full_name } = _currentUser;

    // Sidebar user card
    const elName = document.getElementById('sidebar-username');
    const elRole = document.getElementById('sidebar-role');
    const roleMap = { super_admin: 'Super Admin', admin: 'مدیر', user: 'کاربر' };
    if (elName) elName.textContent = full_name || username;
    if (elRole) elRole.textContent = roleMap[role] || role;

    // Show / hide nav items based on role
    const allowed = ROLE_NAV_VISIBILITY[role] || ROLE_NAV_VISIBILITY['user'];
    const allNavIds = ['nav-link-dashboard', 'nav-link-properties', 'nav-link-scraper',
                       'nav-link-crm', 'nav-link-auth', 'nav-link-proxies', 'nav-users'];
    allNavIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('d-none', !allowed.includes(id));
    });
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    const token = getToken();
    if (token) {
        _authToken = token;
        // Verify token by fetching /me
        fetch(`${API_BASE}/users/me`, { headers: { 'Authorization': `Bearer ${token}` } })
            .then(r => {
                if (!r.ok) throw new Error('invalid');
                return r.json();
            })
            .then(user => {
                _currentUser = { username: user.username, role: user.role, full_name: user.full_name };
                showMainApp();
            })
            .catch(() => {
                clearToken();
                _intendedRoute = _hashToSection(location.hash);
                showLoginPage();
            });
    } else {
        _intendedRoute = _hashToSection(location.hash);
        showLoginPage();
    }
});

function initApp() {
    loadDashboard();
    loadCities();
    loadCategories();
    checkCookieStatus();
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
        if (dash && dash.style.display !== 'none') loadDashboard();
    }, 60000);
    setInterval(checkCookieStatus, 300000);
}

// Section titles for the topbar
const SECTION_META = {
    dashboard:  { title: 'داشبورد',             subtitle: 'خلاصه وضعیت و آمار کلی سیستم' },
    properties: { title: 'لیست املاک',          subtitle: 'مدیریت و جستجوی ملک‌های اسکرپ‌شده' },
    scraper:    { title: 'اسکرپر دیوار',         subtitle: 'تنظیم و اجرای تسک‌های اسکرپینگ' },
    crm:        { title: 'CRM — مدیریت لیدها',  subtitle: 'سیستم CRM و اطلاع‌رسانی' },
    auth:       { title: 'احراز هویت دیوار',     subtitle: 'مدیریت نشست و کوکی حساب دیوار' },
    proxies:    { title: 'مدیریت پراکسی‌ها',     subtitle: 'افزودن، تست و مدیریت پراکسی‌ها' },
    users:      { title: 'مدیریت کاربران',       subtitle: 'حساب‌های کاربری (فقط Super Admin)' },
};

// Section Navigation
function _defaultSection() {
    // 'user' role lands on properties; others land on dashboard
    return (_currentUser?.role === 'user') ? 'properties' : 'dashboard';
}

function _isSectionAllowed(sectionName) {
    const role = _currentUser?.role || 'user';
    const allowed = ROLE_NAV_VISIBILITY[role] || ROLE_NAV_VISIBILITY['user'];
    // Map section names to nav IDs
    const sectionNavMap = {
        dashboard: 'nav-link-dashboard', properties: 'nav-link-properties',
        scraper: 'nav-link-scraper', crm: 'nav-link-crm',
        auth: 'nav-link-auth', proxies: 'nav-link-proxies', users: 'nav-users',
    };
    const navId = sectionNavMap[sectionName];
    return !navId || allowed.includes(navId);
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
        case 'scraper':    loadJobs(); checkDivarSessionBanner(); startOtpPolling(); startJobPolling();
                           _initScraperDatePicker(); refreshDivarSessionCount();
                           setTimeout(restoreScraperForm, 200); break;
        case 'auth':       checkAuthStatus(); loadCookies(); break;
        case 'proxies':    loadProxies(); break;
        case 'crm':        _applyCrmRoleVisibility(); loadTasks(); break;
        case 'users':      if (_currentUser?.role === 'super_admin') loadUsers(); break;
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

// Escape user/scraped content before injecting into innerHTML templates
function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, m =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
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
        const token = getToken();
        const response = await fetch(`${API_BASE}${endpoint}`, {
            headers: {
                'Content-Type': 'application/json',
                ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
                ...options.headers
            },
            ...options
        });

        if (response.status === 401) {
            clearToken();
            showLoginPage();
            throw new Error('نشست منقضی شده. لطفاً دوباره وارد شوید.');
        }

        if (!response.ok) {
            const error = await response.json();
            let message = 'Request failed';
            if (error.detail) {
                if (typeof error.detail === 'string') {
                    message = error.detail;
                } else if (Array.isArray(error.detail)) {
                    message = error.detail.map(e => e.msg || JSON.stringify(e)).join(' | ');
                } else {
                    message = JSON.stringify(error.detail);
                }
            }
            throw new Error(message);
        }

        return await response.json();
    } catch (error) {
        console.error('API Error:', error);
        throw error;
    }
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
                const st = CRM_STATUS_LABELS[l.status] || { label: l.status, cls: 'bg-secondary' };
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
        ctx.font = "800 26px Vazirmatn";
        ctx.fillStyle = opts.color || '#fff';
        ctx.fillText(opts.big, x, y - 9);
        ctx.font = "500 12px Vazirmatn";
        ctx.fillStyle = opts.subColor || '#8f96a8';
        ctx.fillText(opts.sub || '', x, y + 17);
        ctx.restore();
    }
};
const _sfTooltip = themeC => ({
    rtl: true, textDirection: 'rtl',
    backgroundColor: 'rgba(12,12,20,.92)',
    borderColor: 'rgba(167,139,250,.35)', borderWidth: 1,
    titleFont: { family: 'Vazirmatn', weight: '700' },
    bodyFont: { family: 'Vazirmatn' },
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
                        color: themeC.text, font: { family: 'Vazirmatn', size: 12 },
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
                    ticks: { color: themeC.tick, font: { family: 'Vazirmatn', size: 11 }, maxRotation: 0 }
                },
                y: {
                    beginAtZero: true,
                    grid: { color: themeC.grid, tickBorderDash: [4, 5] },
                    border: { display: false, dash: [4, 5] },
                    ticks: {
                        color: themeC.tick, font: { family: 'Vazirmatn', size: 11 },
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
                <td><span class="serial-badge">${formatSerial(property.serial_no)}</span></td>
                <td title="${esc(property.title)}">${esc(property.title.substring(0, 40))}...</td>
                <td>${property.city_name || '---'}</td>
                <td>${formatNumber(property.area)} متر</td>
                <td>${property.rooms != null ? property.rooms : '---'}</td>
                <td>
                    ${property.listing_type === 'rent'
                        ? `<small class="d-block text-muted">رهن: ${formatPrice(property.deposit)}</small><small class="d-block">اجاره: ${formatPrice(property.rent_price)}</small>`
                        : formatPrice(property.total_price || property.price)
                    }
                </td>
                <td>
                    ${property.phone_number 
                        ? `<a href="tel:${property.phone_number}" class="text-success">${property.phone_number}</a>`
                        : '<span class="text-muted">---</span>'
                    }
                </td>
                <td>
                    <button class="btn btn-sm btn-outline-primary" onclick="viewProperty(${property.id})">
                        <i class="bi bi-eye"></i>
                    </button>
                    <a href="${property.url}" target="_blank" class="btn btn-sm btn-outline-secondary">
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
                                        <img src="${img}" class="d-block w-100 rounded" alt="تصویر ${idx + 1}"
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
                
                <h5 class="mb-3">${esc(property.title)}</h5>
                
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
                                        ? `<a href="tel:${property.phone_number}" class="text-success">${property.phone_number}</a>` 
                                        : '<span class="text-muted">---</span>'}
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
                    <a href="${property.url}" target="_blank" class="btn btn-primary flex-grow-1">
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
    if (!confirm('آیا از حذف این ملک اطمینان دارید؟')) return;
    
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
}

function _onScraperDateChange() {
    const hasDate = !!document.getElementById('scraper-posted-date').value.trim();
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
const _SCRAPER_TEXT_FIELDS = [
    'scraper-category', 'scraper-pages', 'scraper-advertiser-type', 'scraper-rotate-every',
    'scraper-min-price', 'scraper-max-price', 'scraper-min-ppm', 'scraper-max-ppm',
    'scraper-min-deposit', 'scraper-max-deposit', 'scraper-min-rent', 'scraper-max-rent',
    'scraper-min-area', 'scraper-max-area', 'scraper-min-rooms', 'scraper-max-rooms',
];
const _SCRAPER_CHECKS = ['scraper-has-images', 'scraper-has-elevator', 'scraper-has-parking',
    'scraper-has-storage', 'scraper-has-balcony', 'scraper-images'];

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
    // city picker + category-driven filter visibility
    const picker = document.getElementById('scraper-city-picker');
    if (picker && picker._setCityValue && data.city) picker._setCityValue(data.city);
    if (document.getElementById('scraper-category')?.value) {
        try { onScraperCategoryChange(); } catch (_) {}
    }
}

// ─── «چند آگهی با این فیلترها هست؟» ──────────────────────────────────────────
// Divar prints this above its own results («۳۴۳ آگهی در این محدوده») and its
// search API carries the same number. One request answers what would otherwise
// take opening every ad in the city.
async function estimateScrape() {
    const box = document.getElementById('scraper-estimate');
    const btn = document.getElementById('scraper-estimate-btn');
    const city = document.getElementById('scraper-city').value;
    if (!city) { showToast('خطا', 'اول شهر را انتخاب کنید', 'warning'); return; }

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

async function executeBulkScraping(city, category, maxItems, downloadImages, filters = {}) {
    try {
        // Auto-use the active Divar session — no manual phone selection needed
        const session = await _getActiveSession();
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
        const r = await apiCall('/auth/cookies');
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
    const n = raw === '' ? 15 : parseInt(_digitsOnly(raw), 10);   // empty = server default
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
        } else if (n >= 50) {
            parts.push(`<span class="text-warning">هر ${formatNumber(n)} آگهی یک بار سوییچ می‌کند —
                یعنی چرخش <b>کم</b>. برای کم شدن پیامک، عدد را <b>پایین</b> بیاورید نه بالا.</span>`);
        } else if (n <= 10) {
            parts.push(`<span class="text-muted">هر ${formatNumber(n)} آگهی سوییچ می‌کند —
                کمترین پیامک، ولی هر سوییچ چند ثانیه به هر اسکرپ اضافه می‌کند.</span>`);
        }
    }
    box.innerHTML = parts.join('<br>');
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

async function cancelJob(jobId) {
    if (!confirm('آیا از لغو این تسک اطمینان دارید؟')) return;

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
    showToast('در حال اسکرپ', 'اسکرپ این ملک شروع شد، چند لحظه صبر کنید...', 'info');

    try {
        const result = await apiCall('/scraper/scrape-single', {
            method: 'POST',
            body: JSON.stringify({ url })
        });

        if (result.success) {
            showToast('موفق', 'ملک با موفقیت اسکرپ شد', 'success');
        } else {
            showToast('خطا', result.message || 'اسکرپ ناموفق بود', 'danger');
        }
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
    // Returns the most recently updated valid session, or null
    try {
        const data = await apiCall('/auth/cookies');
        const valid = (data.cookies || []).filter(c => c.is_valid);
        if (!valid.length) return null;
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
            if (textEl) textEl.textContent = `کوکی فعال (${session.phone_number})`;
            if (dotEl)  dotEl.className = 'dot dot-success';
        } else {
            // check if there are any (expired) cookies
            let hasCookies = false;
            try {
                const data = await apiCall('/auth/cookies');
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
                شماره فعال: <strong>${session.phone_number}</strong>
            `;
        } else {
            // check if any (expired) cookies exist
            let hasCookies = false;
            try {
                const data = await apiCall('/auth/cookies');
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
        tbody.innerHTML = `<tr><td colspan="8" class="text-center text-muted py-4">هیچ تسکی وجود ندارد</td></tr>`;
        return;
    }
    const JOB_STATUS_FA = {
        pending: 'در صف', running: 'در حال اجرا', paused: '⏸ متوقف — منتظر کد',
        completed: 'تکمیل شده', failed: 'ناموفق', cancelled: 'لغو شده',
    };
    items.forEach(job => {
        const row = document.createElement('tr');
        const statusClass = `status-${job.status}`;
        const statusLabel = JOB_STATUS_FA[job.status] || job.status;
        row.innerHTML = `
            <td><code>${job.job_id.substring(0, 8)}...</code></td>
            <td>${job.category_name ? `<span class="badge bg-primary">${esc(job.category_name)}</span>` : '—'}</td>
            <td>${esc(job.city_name) || '—'}</td>
            <td><span class="badge ${statusClass}">${statusLabel}</span></td>
            <td>
                <div style="min-width:90px">
                    <div class="progress" style="height:5px;background:var(--border,#333);border-radius:3px;">
                        <div class="progress-bar" role="progressbar"
                             style="width:${job.progress}%;border-radius:3px;"></div>
                    </div>
                    <div style="font-size:.72rem;color:var(--text-muted,#aaa);text-align:center;margin-top:2px;">${Math.round(job.progress)}%</div>
                </div>
            </td>
            <td>${job.new_items} / ${job.updated_items}</td>
            <td>${job.started_at ? new Date(job.started_at).toLocaleString('fa-IR') : '---'}</td>
            <td>
                ${['running', 'paused', 'pending'].includes(job.status) ? `
                    <button class="btn btn-sm btn-outline-danger" onclick="cancelJob('${job.job_id}')"
                            title="لغو تسک">
                        <i class="bi bi-stop-fill"></i>
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
            const m = String(Math.floor(left / 60)).padStart(2, '0');
            const s = String(left % 60).padStart(2, '0');
            el.textContent = left > 0
                ? `⏳ مهلت ورود کد: ${formatNumber(m)}:${formatNumber(s)}`
                : 'مهلت تمام شد — اسکرپر بدون این شماره ادامه می‌دهد';
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

// keys the user explicitly dismissed this session — don't re-pop them
async function pollDivarOtp() {
    try {
        const data = await apiCall('/scraper/otp-pending');
        if (data.timeout) _otp2Window = data.timeout;
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
        initOtp2Boxes();
        _otp2Reset();
        // the request started before the poll saw it — count what is left
        _otp2StartTimer(item.remaining != null ? item.remaining : _otp2Window);
        // focus the first box once Bootstrap finished its own focus handling
        modal.addEventListener('shown.bs.modal', () => _otp2Els()[0]?.focus(), { once: true });
        new bootstrap.Modal(modal).show();
    } catch(e) { /* silent */ }
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

async function dismissDivarOtp() {
    const key = document.getElementById('divar-otp-key').value;
    if (key) _dismissedOtpKeys.add(key);           // stop the poll from re-opening it
    _otp2StopTimer();
    bootstrap.Modal.getInstance(document.getElementById('divarOtpModal'))?.hide();
    try { await apiCall('/scraper/otp-cancel', { method: 'POST' }); } catch (_) {}
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
            waitMsg.innerHTML = `<i class="bi bi-phone"></i> کد تأیید به <strong>${phone}</strong> ارسال شد.<br>
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
    if (!confirm('آیا از خروج اطمینان دارید؟')) return;
    
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
        await apiCall('/auth/cookies/import', {
            method: 'POST',
            body: JSON.stringify({ phone_number: phone, cookies })
        });
        showToast('موفق', 'کوکی‌ها با موفقیت وارد شدند', 'success');
        document.getElementById('import-cookies-json').value = '';
        checkCookieStatus();
        loadCookies();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

async function loadCookies() {
    try {
        const data = await apiCall('/auth/cookies');
        
        const container = document.getElementById('cookies-list');
        
        if (data.cookies.length === 0) {
            container.innerHTML = '<p class="text-muted text-center">هیچ نشستی ذخیره نشده</p>';
            return;
        }
        
        container.innerHTML = data.cookies.map(cookie => `
            <div class="d-flex justify-content-between align-items-center p-2 border-bottom">
                <div>
                    <strong>${cookie.phone_number}</strong>
                    <br>
                    <small class="text-muted">${cookie.is_valid ? 'معتبر' : 'منقضی'}</small>
                </div>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteCookie(${cookie.id})">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        `).join('');
        
    } catch (error) {
        console.error('Failed to load cookies:', error);
    }
}

async function deleteCookie(id) {
    if (!confirm('آیا از حذف این نشست اطمینان دارید؟')) return;
    
    try {
        await apiCall(`/auth/cookies/${id}`, { method: 'DELETE' });
        showToast('موفق', 'نشست حذف شد', 'success');
        loadCookies();
    } catch (error) {
        showToast('خطا', error.message, 'danger');
    }
}

// ==================== Proxies ====================

async function loadProxies() {
    try {
        const data = await apiCall('/proxies');
        
        const tbody = document.getElementById('proxies-table');
        tbody.innerHTML = '';
        
        if (data.items.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="6" class="text-center text-muted py-4">
                        هیچ پراکسی‌ای وجود ندارد
                    </td>
                </tr>
            `;
            return;
        }
        
        data.items.forEach(proxy => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${proxy.address}</td>
                <td>${proxy.port}</td>
                <td>
                    <span class="badge ${proxy.is_working ? 'bg-success' : 'bg-danger'}">
                        ${proxy.is_working ? 'فعال' : 'غیرفعال'}
                    </span>
                </td>
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
    if (!confirm('آیا از حذف این پراکسی اطمینان دارید؟')) return;
    
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
            <img src="${u}" alt="">
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
                        color: themeC.text, font: { family: 'Vazirmatn', size: 11 },
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
                        color: themeC.tick, font: { family: 'Vazirmatn', size: 11 },
                        precision: 0, callback: v => formatNumber(v),
                    }
                },
                y: {
                    grid: { color: 'transparent' }, border: { display: false },
                    ticks: { color: themeC.text, font: { family: 'Vazirmatn', size: 12, weight: '600' } }
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
    if (!confirm(`${_selectedLeads.size} لید انتخاب‌شده حذف شوند؟ این عمل قابل بازگشت نیست.`)) return;
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
    // _intOrNull reads the field itself so it can strip the «/» separators
    const priceMin = _intOrNull('crm-filter-price-min');
    const priceMax = _intOrNull('crm-filter-price-max');

    const parts = [];
    if (status)           parts.push(`status=${encodeURIComponent(status)}`);
    if (notified !== '')  parts.push(`notified=${notified}`);
    if (search)           parts.push(`search=${encodeURIComponent(search)}`);
    if (category)         parts.push(`category=${encodeURIComponent(category)}`);
    if (kind)             parts.push(`property_kind=${encodeURIComponent(kind)}`);
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
                    <td colspan="11" class="text-center text-muted py-4">
                        <i class="bi bi-inbox" style="font-size:2rem;"></i>
                        <p class="mt-2">هیچ لیدی یافت نشد</p>
                    </td>
                </tr>`;
            _renderLeadsPagination(data.total ?? 0);
            return;
        }

        data.items.forEach(lead => {
            const st = CRM_STATUS_LABELS[lead.status] || { label: lead.status, cls: 'bg-secondary' };
            const notifiedBadge = lead.notified
                ? `<span class="badge bg-success"><i class="bi bi-check-circle"></i> بله</span>`
                : `<span class="badge bg-secondary">خیر</span>`;
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
                <td title="${esc(lead.property_title)}">${esc((lead.property_title || '---').substring(0, 35))}...</td>
                <td>${lead.city_name || '---'}</td>
                <td>
                    <div>${formatPrice(lead.price)}</div>
                    ${lead.price_per_meter ? `<small class="lead-subline" title="قیمت هر متر">${formatPrice(lead.price_per_meter)} <span class="opacity-75">/ متر</span></small>` : ''}
                </td>
                <td>${_leadSpecChips(lead)}</td>
                <td>
                    ${lead.phone_number
                        ? `<a href="tel:${lead.phone_number}" class="text-success fw-bold">${lead.phone_number}</a>`
                        : '<span class="text-muted">---</span>'}
                </td>
                <td>
                    <select class="form-select form-select-sm status-quick ${st.cls}" style="min-width:130px"
                            onchange="quickLeadStatus(${lead.id}, this.value, this)">
                        ${Object.entries(CRM_STATUS_LABELS).map(([val, info]) =>
                            `<option value="${val}" ${lead.status === val ? 'selected' : ''}>${info.label}</option>`
                        ).join('')}
                    </select>
                </td>
                <td>${notifiedBadge}</td>
                <td>
                    <div>${createdAt}</div>
                    ${scrapedAt ? `<small class="lead-subline" title="تاریخ برداشت آگهی"><i class="bi bi-download"></i> ${scrapedAt}</small>` : ''}
                </td>
                <td>
                    <button class="btn btn-sm btn-outline-primary" onclick="viewLead(${lead.id})" title="ویرایش">
                        <i class="bi bi-pencil"></i>
                    </button>
                    ${!lead.notified ? `
                    <button class="btn btn-sm btn-outline-success" onclick="notifyLead(${lead.id})" title="ارسال اطلاع">
                        <i class="bi bi-bell"></i>
                    </button>` : ''}
                    <a href="${lead.property_url}" target="_blank" class="btn btn-sm btn-outline-secondary" title="باز کردن آگهی">
                        <i class="bi bi-box-arrow-up-left"></i>
                    </a>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteLead(${lead.id})" title="حذف لید">
                        <i class="bi bi-trash"></i>
                    </button>
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
        row('آگهی‌دهنده', p.advertiser_type === 'agency' ? 'مشاور املاک' : p.advertiser_type === 'personal' ? 'شخصی' : ''),
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
                            <img src="${img}" alt="تصویر ${i + 1}" onclick="openImageLightbox(this.src)">
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
    if (!confirm('از این لید یک معامله ساخته شود؟')) return;
    try {
        const r = await apiCall(`/crm/leads/${leadId}/convert-to-deal`, { method: 'POST' });
        showToast('موفق', `معامله #${r.deal.id} ساخته شد`, 'success');
        bootstrap.Modal.getInstance(document.getElementById('leadModal'))?.hide();
        document.querySelector('[data-bs-target="#crm-tab-deals"]')?.click();
        loadDeals();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

// ═══ تطابق‌سازی — similar properties & customer suggestions ═══════
function _matchCard(m) {
    // the reverse direction returns people, not listings
    if (m.full_name !== undefined) return _customerMatchCard(m);
    const price = m.price ? formatPrice(m.price) : '—';
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
            <button class="btn btn-sm btn-outline-primary" onclick="viewProperty(${m.id})">
                <i class="bi bi-eye"></i> جزئیات
            </button>
            ${m.phone_number ? `<a href="tel:${m.phone_number}" class="btn btn-sm btn-outline-success">
                <i class="bi bi-telephone"></i> ${m.phone_number}</a>` : ''}
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
    if (intent.family) bits.push(MATCH_TYPE_FA[intent.family] || intent.family);
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
        document.getElementById('match-modal-body').innerHTML = `
            ${src ? `<div class="match-source">مبنای تطابق: <b>${esc(src)}</b> — ${formatNumber(items.length)} مورد یافت شد
                ${criteria ? `<div class="small mt-1">${criteria}</div>` : ''}</div>` : ''}
            <div class="match-list">${items.map(_matchCard).join('')}</div>`;
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
                    <div class="fw-bold">${esc(lead.property_title) || '---'}</div>
                </div>
                <div class="col-md-6">
                    <label class="text-muted small">لینک</label>
                    <div class="d-flex gap-2 flex-wrap">
                        <a href="${lead.property_url}" target="_blank" class="btn btn-sm btn-outline-primary">
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
                    </div>
                </div>
                <div class="col-md-4">
                    <label class="text-muted small">شماره تماس</label>
                    <div class="h5 text-success mb-0">
                        ${lead.phone_number
                            ? `<a href="tel:${lead.phone_number}">${lead.phone_number}</a>`
                            : '---'}
                    </div>
                </div>
                <div class="col-md-4">
                    <label class="text-muted small">فروشنده</label>
                    <div>${esc(lead.seller_name) || '---'}</div>
                </div>
                <div class="col-md-4">
                    <label class="text-muted small">شهر</label>
                    <div>${lead.city_name || '---'}</div>
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
                            ? `<span class="badge bg-success">بله (${lead.notification_channel})</span>`
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
                           value="${lead.district || ''}" placeholder="مثلاً: خیابان کاشانی">
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
    if (!confirm('این لید و ملکِ متصل به آن از همه‌جا (لیست املاک، یادداشت‌ها و تصاویر) حذف می‌شوند. ادامه می‌دهید؟ این عمل قابل بازگشت نیست.')) return;
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

async function loadDpa() {
    const search = document.getElementById('dpa-search')?.value.trim() || '';
    let url = '/crm/dpa?limit=100';
    if (search) url += `&search=${encodeURIComponent(search)}`;

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
                <td>${d.date_jalali || '---'}</td>
                <td class="fw-bold">${esc(d.agent_name)}</td>
                <td>${DPA_ROLE_LABELS[d.role] || d.role || '---'}</td>
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
    if (!confirm('این فرم ارزیابی حذف شود؟')) return;
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
const CUSTOMER_SOURCE_LABELS = { in_person: 'حضوری', divar: 'دیوار', referral: 'معرف' };
const SHOWING_STEP_LABELS = { meeting: 'نشست', archive: 'بایگانی', second_visit: 'بازدید دوم' };
let _customerEditId = null;

async function loadCustomers() {
    const search = document.getElementById('customer-search')?.value.trim() || '';
    const temp   = document.getElementById('customer-filter-temp')?.value || '';
    const source = document.getElementById('customer-filter-source')?.value || '';

    const sort = document.getElementById('customer-sort')?.value || 'newest';

    let url = '/crm/customers?limit=100';
    if (search) url += `&search=${encodeURIComponent(search)}`;
    if (temp)   url += `&temperature=${temp}`;
    if (source) url += `&source=${source}`;
    url += `&sort=${sort}`;

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
            const t = CUSTOMER_TEMP_LABELS[c.temperature] || { label: c.temperature || '---', cls: 'bg-secondary' };
            const nextFollowup = (c.followups && c.followups.length)
                ? `${c.followups[0].date || ''} ${c.followups[0].time || ''}`.trim() || '---'
                : '---';
            // "جدید" badge for customers added within the last 3 days
            const isNew = c.created_at && (Date.now() - new Date(c.created_at).getTime()) < 3 * 86400000;
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${c.id}</td>
                <td class="fw-bold">${esc(c.full_name)}${isNew ? ' <span class="badge bg-success" style="font-size:.6rem;vertical-align:middle">جدید</span>' : ''}</td>
                <td>${c.mobile1 ? `<a href="tel:${c.mobile1}" class="text-success">${c.mobile1}</a>` : '---'}</td>
                <td><span class="badge ${t.cls}">${t.label}</span></td>
                <td>${CUSTOMER_SOURCE_LABELS[c.source] || '---'}</td>
                <td>${c.budget_max ? formatPrice(c.budget_max) : '---'}</td>
                <td>${c.desired_district || '---'}</td>
                <td>${c.consultant_name || '---'}</td>
                <td>${nextFollowup}</td>
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
        <td><input type="text" class="form-control form-control-sm cust-sh-code" value="${data.file_code || ''}" placeholder="SF-..."></td>
        <td><input type="text" class="form-control form-control-sm cust-sh-desc" value="${data.description || ''}" placeholder="شرح ملک"></td>
        <td><input type="text" class="form-control form-control-sm cust-sh-feedback" value="${data.feedback || ''}" placeholder="بازخورد"></td>
        <td>
            <select class="form-select form-select-sm cust-sh-step">
                <option value="">---</option>
                ${Object.entries(SHOWING_STEP_LABELS).map(([v, l]) =>
                    `<option value="${v}" ${data.next_step === v ? 'selected' : ''}>${l}</option>`).join('')}
            </select>
        </td>
        <td><button type="button" class="btn btn-sm btn-outline-danger" onclick="_custRemoveRow(this)"><i class="bi bi-x"></i></button></td>`;
    document.getElementById('cust-showings-body').appendChild(tr);
}

function addFollowupRow(data = {}) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
        <td><input type="text" class="form-control form-control-sm cust-fu-date" value="${data.date || ''}" placeholder="۱۴۰۵/۰۵/۰۱"></td>
        <td><input type="text" class="form-control form-control-sm cust-fu-time" value="${data.time || ''}" placeholder="۱۴:۳۰"></td>
        <td><input type="text" class="form-control form-control-sm cust-fu-action" value="${data.action || ''}" placeholder="چه چیزی باید پیگیری یا ارائه شود؟"></td>
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
    if (!confirm('این مشتری حذف شود؟ این عمل قابل بازگشت نیست.')) return;
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
    super_admin: { label: '👑 Super Admin', cls: 'bg-danger' },
    admin:       { label: '🛠 Admin',        cls: 'bg-primary' },
    user:        { label: '👤 User',          cls: 'bg-secondary' },
};

async function loadUsers() {
    try {
        const data = await apiCall('/users');
        const tbody = document.getElementById('users-table');
        tbody.innerHTML = '';

        data.items.forEach(u => {
            const rl = ROLE_LABELS[u.role] || { label: u.role, cls: 'bg-dark' };
            const lastLogin = u.last_login
                ? new Date(u.last_login).toLocaleDateString('fa-IR')
                : '---';
            const isSelf = u.username === _currentUser?.username;

            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${u.id}</td>
                <td><strong>${u.username}</strong> ${isSelf ? '<span class="badge bg-info">شما</span>' : ''}</td>
                <td>${u.full_name || '---'}</td>
                <td><span class="badge ${rl.cls}">${rl.label}</span></td>
                <td>
                    <span class="text-monospace small">${u.divar_phone || '---'}</span>
                    <button class="btn btn-sm btn-link p-0 ms-1" onclick="promptSetDivarPhone(${u.id}, '${u.divar_phone || ''}')" title="ویرایش شماره دیوار">
                        <i class="bi bi-pencil-square"></i>
                    </button>
                </td>
                <td>
                    <span class="badge ${u.is_active ? 'bg-success' : 'bg-secondary'}">
                        ${u.is_active ? 'فعال' : 'غیرفعال'}
                    </span>
                </td>
                <td>${lastLogin}</td>
                <td>
                    ${!isSelf ? `
                    <button class="btn btn-sm btn-outline-warning" onclick="toggleUserActive(${u.id}, ${u.is_active})" title="${u.is_active ? 'غیرفعال' : 'فعال'} کردن">
                        <i class="bi bi-toggle-${u.is_active ? 'on' : 'off'}"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-info" onclick="promptResetPassword(${u.id})" title="تغییر رمز">
                        <i class="bi bi-key"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteUser(${u.id})" title="حذف">
                        <i class="bi bi-trash"></i>
                    </button>
                    ` : ''}
                </td>
            `;
            tbody.appendChild(row);
        });
    } catch (error) {
        showToast('خطا', 'بارگیری کاربران ناموفق بود', 'danger');
    }
}

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
            body: JSON.stringify({ username, full_name, email, password, role, divar_phone })
        });
        showToast('موفق', 'کاربر ساخته شد', 'success');
        e.target.reset();
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
    const newPass = prompt('رمز عبور جدید را وارد کنید (حداقل ۶ کاراکتر):');
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

async function promptSetDivarPhone(id, currentPhone) {
    const newPhone = prompt(`شماره دیوار مرتبط با این کاربر را وارد کنید:\n(برای پاک کردن، خالی بگذارید)`, currentPhone);
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
    if (!confirm('آیا از حذف این کاربر اطمینان دارید؟')) return;
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
            const p = TASK_PRIORITY_LABELS[t.priority] || { label: t.priority, cls: 'bg-secondary' };
            const s = TASK_STATUS_LABELS[t.status] || { label: t.status, cls: 'bg-secondary' };
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
    if (!confirm('حذف شود؟')) return;
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

async function loadContacts() {
    const search = document.getElementById('contact-search')?.value.trim() || '';
    const type = document.getElementById('contact-filter-type')?.value || '';
    const category = document.getElementById('contact-filter-category')?.value || '';
    let url = '/crm/contacts?limit=200';
    if (search) url += `&search=${encodeURIComponent(search)}`;
    if (type) url += `&contact_type=${type}`;
    if (category) url += `&category=${category}`;
    try {
        const data = await apiCall(url);
        const tbody = document.getElementById('contacts-table');
        if (!tbody) return;
        if (!data.items?.length) { tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">مخاطبی یافت نشد</td></tr>'; return; }
        tbody.innerHTML = data.items.map(c => {
            const typeInfo = CONTACT_TYPE_LABELS[c.contact_type] || { label: c.contact_type, cls: 'bg-secondary' };
            const catCls = c.category === 'VIP' ? 'bg-warning text-dark' : c.category === 'cold' ? 'bg-secondary' : 'bg-info text-white';
            const tags = _tagList(c.tags).map(t => `<span class="badge bg-dark me-1">${esc(t)}</span>`).join('');
            return `<tr>
                <td>${esc(c.name)}</td>
                <td>${c.phone || '—'}</td>
                <td><span class="badge ${typeInfo.cls}">${typeInfo.label}</span></td>
                <td><span class="badge ${catCls}">${c.category || 'عادی'}</span></td>
                <td>${esc(c.city) || '—'}</td>
                <td>${tags || '—'}</td>
                <td>
                    <button class="btn btn-xs btn-outline-primary" onclick="openContactModal(${c.id})"><i class="bi bi-pencil"></i></button>
                    <button class="btn btn-xs btn-outline-danger" onclick="deleteContact(${c.id})"><i class="bi bi-trash"></i></button>
                    <button class="btn btn-xs btn-outline-info" onclick="quickSmsToContact('${c.phone || ''}')"><i class="bi bi-chat-dots"></i></button>
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
    if (!confirm('حذف شود؟')) return;
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

async function loadDeals() {
    const status = document.getElementById('deal-filter-status')?.value || '';
    let url = '/crm/deals?limit=100';
    if (status) url += `&status=${status}`;
    try {
        const data = await apiCall(url);
        const tbody = document.getElementById('deals-table');
        if (!tbody) return;
        if (!data.items?.length) { tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">معامله‌ای یافت نشد</td></tr>'; return; }
        tbody.innerHTML = data.items.map(d => {
            const s = DEAL_STATUS_LABELS[d.status] || { label: d.status, cls: 'bg-secondary' };
            const dealTypeLabel = { buy: 'خرید', rent: 'اجاره', lease: 'رهن' }[d.deal_type] || d.deal_type;
            const amount = d.amount ? formatNumber(d.amount) + ' ت' : '—';
            const date = d.contract_date ? new Date(d.contract_date).toLocaleDateString('fa-IR') : '—';
            return `<tr>
                <td>${esc(d.title)}</td>
                <td>${dealTypeLabel}</td>
                <td><span class="badge ${s.cls}">${s.label}</span></td>
                <td>${amount}</td>
                <td>${d.buyer_contact_id || '—'}</td>
                <td>${d.seller_contact_id || '—'}</td>
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
    if (!confirm('حذف شود؟')) return;
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
                    <p class="mb-1" style="white-space:pre-wrap;">${n.content}</p>
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
    if (!confirm('حذف شود؟')) return;
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
    if (!confirm('حذف شود؟')) return;
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
                <td>${s.to_number}</td>
                <td>${providerLabel}</td>
                <td title="${s.message || ''}">${msg}</td>
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
    _downloadExport(`${API_BASE}/properties/export/excel?${params}`, 'properties.xlsx');
}

function exportCalendarExcel() {
    const { start, end } = _calRange();
    _downloadExport(
        `${API_BASE}/crm/calendar/export/excel?date_from=${_isoLocal(start)}&date_to=${_isoLocal(end)}`,
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

function _applyCrmRoleVisibility() {
    const isSuperAdmin = _currentUser?.role === 'super_admin';
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
    const who = to.map(r => `${r.role} (${r.phone})`).join('\n');
    if (!confirm(`پیامک مشخصات این قرار برای ${to.length} نفر ارسال شود؟\n\n${who}`)) return;

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
    if (!_editingEventId || !confirm('این قرار حذف شود؟')) return;
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
//  A cabinet holds binders, a binder holds files. A "file" is a
//  property; nothing is copied, only filed and marked.
// ═══════════════════════════════════════════════════════════════════
const FILING_PALETTE = ['#fbbf24', '#34d399', '#38bdf8', '#a78bfa', '#f472b6',
                        '#fb923c', '#2dd4bf', '#f87171', '#a3e635', '#e879f9'];
// the shelf a cabinet can be labelled with
const FILING_ICONS = ['bi-archive', 'bi-house-door', 'bi-key', 'bi-people',
                      'bi-building', 'bi-shop', 'bi-geo-alt', 'bi-star',
                      'bi-briefcase', 'bi-folder'];
const FILING_PAGE = 60;          // one screenful of files per request
let _cabinets = [];
let _activeBinder = null;        // null = the unfiled tray
let _filingArchived = false;
let _filingOffset = 0;
let _selectedFiles = new Set();
let _cabinetEditId = null, _binderEditId = null, _binderCabinetId = null;
let _cabColor = FILING_PALETTE[0], _binColor = FILING_PALETTE[2];
let _cabIcon = FILING_ICONS[0];

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
        _renderFilingCounts(counts);
        _renderShelves();
        _renderTagFilter(tags.items || []);
        _renderBinderPickers();
        await loadFilingFiles();
    } catch (e) {
        showToast('خطا', 'بارگیری کمدها ناموفق بود', 'danger');
    }
}

function _renderFilingCounts(c) {
    const box = document.getElementById('filing-counts');
    if (!box) return;
    const chip = (label, value, cls) =>
        `<span class="badge ${cls}">${label}: ${formatNumber(value || 0)}</span>`;
    box.innerHTML =
        chip('بایگانی‌شده در زونکن', c.filed, 'bg-primary-subtle text-primary-emphasis') +
        chip('بدون زونکن', c.unfiled, 'bg-warning-subtle text-warning-emphasis') +
        chip('سنجاق', c.pinned, 'bg-info-subtle text-info-emphasis') +
        chip('بایگانی', c.archived, 'bg-secondary') +
        chip('شخصی', c.private, 'bg-danger-subtle text-danger-emphasis');
}

function _renderShelves() {
    const wrap = document.getElementById('filing-shelves');
    if (!wrap) return;
    if (!_cabinets.length) {
        wrap.innerHTML = `<div class="text-center text-muted py-4">
            <i class="bi bi-archive" style="font-size:2rem"></i>
            <p class="mt-2">هنوز کمدی نساخته‌اید. با «کمد جدید» شروع کنید.</p></div>`;
        return;
    }
    wrap.innerHTML = _cabinets.map(cab => `
        <div class="filing-cabinet" style="--c:${cab.color}">
            <div class="filing-cabinet-head">
                <i class="bi ${esc(cab.icon) || 'bi-archive'}"></i>
                <b>${esc(cab.name)}</b>
                <span class="text-muted small">${formatNumber(cab.file_count || 0)} فایل</span>
                ${cab.owner ? '<span class="badge bg-danger-subtle text-danger-emphasis">شخصی</span>' : ''}
                <div class="ms-auto d-flex gap-1">
                    <button class="btn btn-sm btn-outline-success" onclick="openBinderModal(null, ${cab.id})" title="زونکن جدید">
                        <i class="bi bi-plus-lg"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-secondary" onclick="openCabinetModal(${cab.id})" title="ویرایش کمد">
                        <i class="bi bi-pencil"></i>
                    </button>
                </div>
            </div>
            <div class="filing-shelf">
                ${(cab.binders || []).map(_binderSpine).join('') ||
                  '<div class="text-muted small py-3">این کمد خالی است</div>'}
            </div>
        </div>`).join('');
}

/** A binder drawn as a spine on the shelf, like the paper it replaces. */
function _binderSpine(b) {
    const active = _activeBinder && _activeBinder.id === b.id;
    return `<div class="filing-binder${active ? ' active' : ''}" style="--c:${b.color}"
                 onclick="openBinder(${b.id})" title="${esc(b.name)}${b.description ? ' — ' + esc(b.description) : ''}">
        <button class="filing-binder-edit" onclick="event.stopPropagation(); openBinderModal(${b.id}, ${b.cabinet_id})" title="ویرایش">
            <i class="bi bi-three-dots"></i>
        </button>
        <div class="filing-binder-count">${formatNumber(b.file_count || 0)}</div>
        <div class="filing-binder-name">${esc(b.name)}</div>
        <div class="filing-binder-kind">${esc(b.kind_label)}${b.deal_label ? ' • ' + esc(b.deal_label) : ''}</div>
    </div>`;
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

/** The "move to binder" dropdown. Scoped by id: the bulk bar holds other
 *  selects now, and they must not be overwritten with binder options. */
function _renderBinderPickers() {
    const sel = document.getElementById('filing-move-select');
    if (!sel) return;
    sel.innerHTML = '<option value="">انتقال به زونکن...</option>' +
        _cabinets.map(c => `<optgroup label="${esc(c.name)}">` +
            (c.binders || []).map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join('') +
            '</optgroup>').join('') +
        '<option value="none">— خارج کردن از زونکن —</option>';
}

function openBinder(id) {
    _activeBinder = _allBinders().find(b => b.id === id) || null;
    _setFilingArchived(false);
    _renderShelves();
    loadFilingFiles();
}
function _allBinders() { return _cabinets.flatMap(c => c.binders || []); }

/** Deleting a cabinet or binder unfiles everything behind it, so the server
 *  restricts it to an admin — hide the button rather than let it 403. */
function _canManageFiling() {
    return _currentUser?.role === 'admin' || _currentUser?.role === 'super_admin';
}

/** State and the button's lit/unlit look, always set together. */
function _setFilingArchived(on) {
    _filingArchived = on;
    document.getElementById('filing-archived-btn')?.classList.toggle('active', on);
}

function toggleFilingArchived() {
    _setFilingArchived(!_filingArchived);
    loadFilingFiles();
}

async function loadFilingFiles(append = false) {
    const box = document.getElementById('filing-files');
    if (!box) return;
    if (!append) {
        _filingOffset = 0;
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
    try {
        const data = await apiCall(url);
        const items = data.items || [];
        const total = data.total || 0;
        document.getElementById('filing-files-count').textContent = formatNumber(total);
        if (!items.length && !append) {
            box.innerHTML = `<div class="text-center text-muted py-4">
                <i class="bi bi-inbox" style="font-size:2rem"></i>
                <p class="mt-2">فایلی اینجا نیست</p></div>`;
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
    return `<div class="file-card${_selectedFiles.has(f.id) ? ' selected' : ''}" data-id="${f.id}"
                 onclick="toggleFileSelection(${f.id}, this)">
        <div class="file-card-head">
            <span class="serial-badge">${formatSerial(f.serial_no)}</span>
            <div class="file-marks">${marks.join('')}</div>
        </div>
        <div class="file-card-title" title="${esc(f.title)}">${esc(f.title)}</div>
        <div class="file-card-meta">
            ${f.area ? formatNumber(f.area) + ' متر' : ''}
            ${f.rooms != null ? ' • ' + formatNumber(f.rooms) + ' خواب' : ''}
            ${f.district ? ' • ' + esc(f.district) : (f.city_name ? ' • ' + esc(f.city_name) : '')}
        </div>
        <div class="file-card-price">${f.price ? formatPrice(f.price) : '—'}</div>
        ${tags ? `<div class="file-tags">${tags}</div>` : ''}
        <div class="file-card-actions" onclick="event.stopPropagation()">
            <button class="btn btn-sm btn-outline-primary" onclick="viewProperty(${f.id})" title="جزئیات"><i class="bi bi-eye"></i></button>
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

function toggleFileSelection(id, el) {
    if (_selectedFiles.has(id)) _selectedFiles.delete(id); else _selectedFiles.add(id);
    el?.classList.toggle('selected', _selectedFiles.has(id));
    _updateFileBulkBar();
}
function clearFileSelection() {
    _selectedFiles.clear();
    document.querySelectorAll('.file-card.selected').forEach(c => c.classList.remove('selected'));
    _updateFileBulkBar();
}
function _updateFileBulkBar() {
    const bar = document.getElementById('filing-bulk-bar');
    if (!bar) return;
    bar.classList.toggle('d-none', _selectedFiles.size === 0);
    const n = document.getElementById('filing-bulk-count');
    if (n) n.textContent = formatNumber(_selectedFiles.size);
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
    if (action === 'archive' && !confirm(`${n} فایل بایگانی شود؟`)) return;
    if (action === 'private' && !confirm(
        `${n} فایل شخصی شود؟ از این پس فقط شما و مدیر ارشد آن را می‌بینید.`)) return;
    await _fileBulk({ ids: [..._selectedFiles], action }, FILE_ACTION_FA[action] || 'انجام شد');
}
async function bulkMoveFiles(binderId) {
    if (!binderId || !_selectedFiles.size) return;
    await _fileBulk({ ids: [..._selectedFiles], action: 'move',
                      binder_id: binderId === 'none' ? null : Number(binderId) },
                    binderId === 'none' ? 'از زونکن خارج شد' : 'به زونکن منتقل شد');
}
async function bulkTagFiles(action = 'tag') {
    if (!_selectedFiles.size) return;
    const tags = prompt(action === 'tag'
        ? 'برچسب‌ها را با ویرگول جدا کنید:'
        : 'کدام برچسب‌ها برداشته شوند؟ (با ویرگول جدا کنید)');
    if (!tags || !tags.trim()) return;
    await _fileBulk({ ids: [..._selectedFiles], action, tags }, FILE_ACTION_FA[action]);
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
    if (!confirm('این کمد و زونکن‌هایش حذف شوند؟ فایل‌ها حذف نمی‌شوند، فقط از زونکن خارج می‌شوند.')) return;
    try {
        const r = await apiCall(`/filing/cabinets/${_cabinetEditId}`, { method: 'DELETE' });
        showToast('موفق', `کمد حذف شد — ${formatNumber(r.unfiled)} فایل بدون زونکن شد`, 'success');
        bootstrap.Modal.getInstance(document.getElementById('cabinetModal'))?.hide();
        _activeBinder = null;
        loadFiling();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

function openBinderModal(id = null, cabinetId = null) {
    _binderEditId = id;
    _binderCabinetId = cabinetId;
    const bin = id ? _allBinders().find(b => b.id === id) : null;
    document.getElementById('binder-modal-title').innerHTML = id
        ? '<i class="bi bi-journal-bookmark"></i> ویرایش زونکن'
        : '<i class="bi bi-journal-plus"></i> زونکن جدید';
    document.getElementById('bin-name').value = bin?.name || '';
    document.getElementById('bin-kind').value = bin?.kind || 'property';
    document.getElementById('bin-deal').value = bin?.deal_type || '';
    document.getElementById('bin-description').value = bin?.description || '';
    document.getElementById('bin-delete-btn').classList.toggle('d-none', !id || !_canManageFiling());
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
    });
    try {
        if (_binderEditId) await apiCall(`/filing/binders/${_binderEditId}`, { method: 'PATCH', body });
        else await apiCall('/filing/binders', { method: 'POST', body });
        showToast('موفق', 'زونکن ذخیره شد', 'success');
        bootstrap.Modal.getInstance(document.getElementById('binderModal'))?.hide();
        loadFiling();
    } catch (e) { showToast('خطا', e.message, 'danger'); }
}

async function deleteBinder() {
    if (!_binderEditId) return;
    if (!confirm('این زونکن حذف شود؟ فایل‌ها حذف نمی‌شوند، فقط از زونکن خارج می‌شوند.')) return;
    try {
        const r = await apiCall(`/filing/binders/${_binderEditId}`, { method: 'DELETE' });
        showToast('موفق', `زونکن حذف شد — ${formatNumber(r.unfiled)} فایل بدون زونکن شد`, 'success');
        bootstrap.Modal.getInstance(document.getElementById('binderModal'))?.hide();
        if (_activeBinder?.id === _binderEditId) _activeBinder = null;
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
                <img src="${src}" alt="تصویر" onclick="openImageLightbox(this.src)"></div>`).join('');

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
    const to = prompt('شمارهٔ مشتری برای ارسال پیامک:');
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

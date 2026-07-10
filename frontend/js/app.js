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

function showLoginPage() {
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
    // Navigate to the correct default section for this role
    showSection(_defaultSection());
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
                showLoginPage();
            });
    } else {
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

    setInterval(loadDashboard, 60000);
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

    // Load section data
    switch (sectionName) {
        case 'dashboard':  loadDashboard(); break;
        case 'properties': loadProperties(); break;
        case 'scraper':    loadJobs(); checkDivarSessionBanner(); startOtpPolling(); startJobPolling(); _initScraperDatePicker(); break;
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

// Format Price
function formatPrice(price) {
    if (!price) return '---';
    if (price >= 1000000000) {
        return formatNumber(Math.round(price / 1000000000)) + ' میلیارد';
    } else if (price >= 1000000) {
        return formatNumber(Math.round(price / 1000000)) + ' میلیون';
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
                        <div class="mi-title">${p.title || '---'}</div>
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
                        <div class="mi-title">${l.property_title || '---'}</div>
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
                <td><code>${property.tag_number}</code></td>
                <td title="${property.title}">${property.title.substring(0, 40)}...</td>
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
    
    for (let i = 1; i <= total; i++) {
        const li = document.createElement('li');
        li.className = `page-item ${i === current ? 'active' : ''}`;
        li.innerHTML = `<a class="page-link" href="#" onclick="goToPage(${i})">${formatNumber(i)}</a>`;
        pagination.appendChild(li);
    }
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
                
                <h5 class="mb-3">${property.title}</h5>
                
                <!-- Basic Info -->
                <div class="card mb-3">
                    <div class="card-header bg-primary text-white">
                        <i class="bi bi-info-circle"></i> اطلاعات پایه
                    </div>
                    <div class="card-body">
                        <div class="row g-3">
                            <div class="col-md-6">
                                <label class="text-muted small">شناسه</label>
                                <div><strong><code>${property.tag_number}</code></strong></div>
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
                                <div>${property.building_direction || '---'}</div>
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
                                <div>${property.district || '---'}</div>
                            </div>
                            <div class="col-md-4">
                                <label class="text-muted small">محله</label>
                                <div>${property.neighborhood || '---'}</div>
                            </div>
                            ${property.address ? `
                                <div class="col-12">
                                    <label class="text-muted small">آدرس</label>
                                    <div>${property.address}</div>
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
                            ? `<pre style="white-space:pre-wrap;font-family:inherit;font-size:0.92rem;margin:0;line-height:1.7">${property.description}</pre>`
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
                                <div>${property.seller_name || '---'}</div>
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

    renderList('');
    return { getValue: () => selectedValue };
}

async function loadCities() {
    try {
        const cities = await apiCall('/scraper/cities');

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

    } catch (error) {
        console.error('Failed to load cities:', error);
    }
}

async function loadCategories() {
    try {
        const categories = await apiCall('/scraper/categories');

        const select = document.getElementById('scraper-category');
        categories.forEach(cat => {
            select.innerHTML += `<option value="${cat.slug}">${cat.name}</option>`;
        });
        onScraperCategoryChange();

        // Same categories drive the properties-list and CRM-leads filters;
        // those filter by category_name, so the option value is the name.
        // data-type (buy/rent) drives the rent-only inputs' visibility.
        ['filter-category', 'crm-filter-category'].forEach(id => {
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
    const v = parseInt(document.getElementById(id)?.value);
    return isNaN(v) || v <= 0 ? null : v;
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
    };

    // Date mode: scrape the selected Jalali day (count becomes an optional cap)
    const postedJalali = document.getElementById('scraper-posted-date')?.value.trim() || '';
    if (postedJalali) {
        const g = jalaliToGregorian(postedJalali);
        if (g) filters.posted_date = g;
    }

    // Check cookie status before scraping
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

async function loadJobs() {
    try {
        const data = await apiCall('/scraper/jobs?limit=20');
        // Seed snapshot so first poll doesn't false-trigger a refresh
        for (const job of data.items) {
            _jobPollSnapshot[job.job_id] = { new_items: job.new_items, status: job.status };
        }
        _renderJobsTable(data.items);
    } catch (error) {
        showToast('خطا', 'بارگیری تسک‌ها ناموفق بود', 'danger');
    }
}

async function cancelJob(jobId) {
    if (!confirm('آیا از لغو این تسک اطمینان دارید؟')) return;
    
    try {
        await apiCall(`/scraper/jobs/${jobId}/cancel`, { method: 'POST' });
        showToast('موفق', 'تسک لغو شد', 'success');
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
        const data = await apiCall('/scraper/jobs?limit=20');
        let shouldRefreshProps = false;

        for (const job of data.items) {
            const prev = _jobPollSnapshot[job.job_id];
            if (prev) {
                // New items added since last poll → refresh list
                if (job.new_items > prev.new_items) shouldRefreshProps = true;
                // Job just finished → final refresh
                if (prev.status === 'running' && job.status !== 'running') shouldRefreshProps = true;
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
        tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted py-4">هیچ تسکی وجود ندارد</td></tr>`;
        return;
    }
    items.forEach(job => {
        const row = document.createElement('tr');
        const statusClass = `status-${job.status}`;
        row.innerHTML = `
            <td><code>${job.job_id.substring(0, 8)}...</code></td>
            <td><span class="badge ${statusClass}">${job.status}</span></td>
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
                ${job.status === 'running' ? `
                    <button class="btn btn-sm btn-outline-danger" onclick="cancelJob('${job.job_id}')">
                        <i class="bi bi-stop-fill"></i>
                    </button>
                ` : ''}
            </td>
        `;
        tbody.appendChild(row);
    });
}

async function pollDivarOtp() {
    try {
        const data = await apiCall('/scraper/otp-pending');
        if (data.pending && data.pending.length > 0) {
            const item = data.pending[0];
            const modal = document.getElementById('divarOtpModal');
            if (modal && !modal.classList.contains('show')) {
                document.getElementById('divar-otp-key').value = item.key;
                document.getElementById('divar-otp-input').value = '';
                new bootstrap.Modal(modal).show();
                setTimeout(() => document.getElementById('divar-otp-input').focus(), 400);
            }
        }
    } catch(e) { /* silent */ }
}

async function submitDivarOtp() {
    const key  = document.getElementById('divar-otp-key').value;
    const code = document.getElementById('divar-otp-input').value.trim();
    if (!code || code.length < 4) { showToast('خطا', 'کد را وارد کنید', 'warning'); return; }
    const btn = document.querySelector('#divarOtpModal .btn-primary');
    if (btn) { btn.disabled = true; btn.textContent = 'در حال ارسال...'; }
    try {
        await apiCall(`/scraper/otp/${encodeURIComponent(key)}`, { method: 'POST', body: JSON.stringify({ code }) });
        bootstrap.Modal.getInstance(document.getElementById('divarOtpModal'))?.hide();
        showToast('تأیید', 'کد ارسال شد', 'success');
    } catch(e) {
        const msg = (e?.message || '').includes('No pending OTP') ? 'درخواست OTP منقضی شده — لطفاً صبر کنید تا scraper دوباره درخواست دهد' : 'ارسال کد ناموفق بود';
        showToast('خطا', msg, 'danger');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'تأیید'; }
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
    rejected: { label: 'رد شده', cls: 'bg-danger text-white' },
};

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
    buyer: { label: 'خریدار', cls: 'bg-primary text-white' },
    seller: { label: 'فروشنده', cls: 'bg-warning text-dark' },
    consultant: { label: 'مشاور', cls: 'bg-info text-white' },
    other: { label: 'سایر', cls: 'bg-secondary' },
};

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

    const contactTypeLabels = Object.keys(CONTACT_TYPE_LABELS);
    const contactValues = contactTypeLabels.map(k => data.contacts?.by_type?.[k] ?? 0);
    const contactLabels = contactTypeLabels.map(k => CONTACT_TYPE_LABELS[k].label);

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
    clearLeadsDateFilter();
}

async function loadLeads() {
    const status   = document.getElementById('crm-filter-status').value;
    const notified = document.getElementById('crm-filter-notified').value;
    const search   = document.getElementById('crm-filter-search')?.value.trim() || '';
    const category = document.getElementById('crm-filter-category')?.value || '';

    let url = '/crm/leads?limit=100';
    if (status)          url += `&status=${status}`;
    if (notified !== '') url += `&notified=${notified}`;
    if (search)          url += `&search=${encodeURIComponent(search)}`;
    if (category)        url += `&category=${encodeURIComponent(category)}`;
    if (_leadsDateFrom)  url += `&date_from=${_leadsDateFrom}`;
    if (_leadsDateTo)    url += `&date_to=${_leadsDateTo}`;

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

            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${lead.id}</td>
                <td title="${lead.property_title || ''}">${(lead.property_title || '---').substring(0, 35)}...</td>
                <td>${lead.city_name || '---'}</td>
                <td>${formatPrice(lead.price)}</td>
                <td>
                    ${lead.phone_number
                        ? `<a href="tel:${lead.phone_number}" class="text-success fw-bold">${lead.phone_number}</a>`
                        : '<span class="text-muted">---</span>'}
                </td>
                <td><span class="badge ${st.cls}">${st.label}</span></td>
                <td>${notifiedBadge}</td>
                <td>${createdAt}</td>
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
    } catch (error) {
        showToast('خطا', 'بارگیری لیدها ناموفق بود', 'danger');
    }
}

async function viewLead(id) {
    try {
        const lead = await apiCall(`/crm/leads/${id}`);
        const st = CRM_STATUS_LABELS[lead.status] || { label: lead.status, cls: 'bg-secondary' };

        document.getElementById('lead-detail-body').innerHTML = `
            <div class="row g-3">
                <div class="col-md-6">
                    <label class="text-muted small">عنوان ملک</label>
                    <div class="fw-bold">${lead.property_title || '---'}</div>
                </div>
                <div class="col-md-6">
                    <label class="text-muted small">لینک</label>
                    <div>
                        <a href="${lead.property_url}" target="_blank" class="btn btn-sm btn-outline-primary">
                            <i class="bi bi-box-arrow-up-right"></i> مشاهده آگهی
                        </a>
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
                    <div>${lead.seller_name || '---'}</div>
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
                           value="${lead.assigned_to || ''}" placeholder="نام مسئول...">
                </div>
                <div class="col-md-6">
                    <label class="form-label">منطقه</label>
                    <input type="text" id="lead-edit-district" class="form-control"
                           value="${lead.district || ''}" placeholder="مثلاً: خیابان کاشانی">
                </div>
                <div class="col-12">
                    <label class="form-label">یادداشت</label>
                    <textarea id="lead-edit-notes" class="form-control" rows="3"
                              placeholder="یادداشت...">${lead.notes || ''}</textarea>
                </div>
            </div>
        `;

        document.getElementById('lead-save-btn').onclick = () => saveLead(id);

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
    if (!confirm('این لید حذف شود؟ این عمل قابل بازگشت نیست.')) return;
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

function _dpaScoreParts() {
    let base = 0;
    document.querySelectorAll('.dpa-task:checked').forEach(el => { base += Number(el.dataset.weight); });
    const n = id => Math.max(Number(document.getElementById(id).value) || 0, 0);
    const bonus = n('dpa-bonus-exclusive') * 30 + n('dpa-bonus-offer') * 20 + n('dpa-bonus-close') * 50;
    const penalty = n('dpa-pen-crm') * 10 + n('dpa-pen-cancel') * 15 + n('dpa-pen-hotlead') * 20;
    return { base, bonus, penalty, total: base + bonus - penalty };
}

function updateDpaScore() {
    const s = _dpaScoreParts();
    document.getElementById('dpa-score-base').textContent = s.base;
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
                <tr><td colspan="10" class="text-center text-muted py-4">
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
                <td class="fw-bold">${d.agent_name}</td>
                <td>${DPA_ROLE_LABELS[d.role] || d.role || '---'}</td>
                <td>${d.base_score}</td>
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

    let url = '/crm/customers?limit=100';
    if (search) url += `&search=${encodeURIComponent(search)}`;
    if (temp)   url += `&temperature=${temp}`;
    if (source) url += `&source=${source}`;

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
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${c.id}</td>
                <td class="fw-bold">${c.full_name}</td>
                <td>${c.mobile1 ? `<a href="tel:${c.mobile1}" class="text-success">${c.mobile1}</a>` : '---'}</td>
                <td><span class="badge ${t.cls}">${t.label}</span></td>
                <td>${CUSTOMER_SOURCE_LABELS[c.source] || '---'}</td>
                <td>${c.budget_max ? formatPrice(c.budget_max) : '---'}</td>
                <td>${c.desired_district || '---'}</td>
                <td>${c.consultant_name || '---'}</td>
                <td>${nextFollowup}</td>
                <td>
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

function _resetCustomerForm() {
    ['cust-full-name', 'cust-mobile1', 'cust-mobile2', 'cust-consultant',
     'cust-budget', 'cust-specs', 'cust-district', 'cust-redlines', 'cust-notes']
        .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    document.getElementById('cust-source').value = 'in_person';
    document.getElementById('cust-temperature').value = 'warm';
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

function openAddLeadModal() {
    ['title', 'city', 'category', 'price', 'area', 'phone', 'seller', 'url', 'notes'].forEach(f => {
        const el = document.getElementById(`add-lead-${f}`);
        if (el) el.value = '';
    });
    document.getElementById('add-lead-listing-type').value = '';
    document.getElementById('add-lead-status').value = 'new';
    new bootstrap.Modal(document.getElementById('addLeadModal')).show();
}

async function submitAddLead() {
    const property_title = document.getElementById('add-lead-title').value.trim();
    if (!property_title) { showToast('خطا', 'عنوان ملک الزامی است', 'warning'); return; }

    const payload = {
        property_title,
        city_name: document.getElementById('add-lead-city').value.trim() || null,
        category_name: document.getElementById('add-lead-category').value.trim() || null,
        listing_type: document.getElementById('add-lead-listing-type').value || null,
        price: document.getElementById('add-lead-price').value ? Number(document.getElementById('add-lead-price').value) : null,
        area: document.getElementById('add-lead-area').value ? Number(document.getElementById('add-lead-area').value) : null,
        phone_number: document.getElementById('add-lead-phone').value.trim() || null,
        seller_name: document.getElementById('add-lead-seller').value.trim() || null,
        property_url: document.getElementById('add-lead-url').value.trim() || null,
        status: document.getElementById('add-lead-status').value || 'new',
        notes: document.getElementById('add-lead-notes').value.trim() || null,
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
                <td>${t.title}</td>
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
    document.getElementById('task-assigned').value = '';
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
            const tags = (c.tags || []).map(t => `<span class="badge bg-dark me-1">${t}</span>`).join('');
            return `<tr>
                <td>${c.name}</td>
                <td>${c.phone || '—'}</td>
                <td><span class="badge ${typeInfo.cls}">${typeInfo.label}</span></td>
                <td><span class="badge ${catCls}">${c.category || 'عادی'}</span></td>
                <td>${c.city || '—'}</td>
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

async function openContactModal(id = null) {
    document.getElementById('contact-edit-id').value = id || '';
    document.getElementById('contactModalTitle').textContent = id ? 'ویرایش مخاطب' : 'مخاطب جدید';
    ['name','phone','phone2','email','city','address','tags','notes'].forEach(f => document.getElementById(`contact-${f}`).value = '');
    document.getElementById('contact-type').value = 'buyer';
    document.getElementById('contact-category').value = 'normal';
    if (id) {
        try {
            const c = await apiCall(`/crm/contacts/${id}`);
            document.getElementById('contact-name').value = c.name || '';
            document.getElementById('contact-phone').value = c.phone || '';
            document.getElementById('contact-phone2').value = c.phone2 || '';
            document.getElementById('contact-email').value = c.email || '';
            document.getElementById('contact-type').value = c.contact_type || 'buyer';
            document.getElementById('contact-category').value = c.category || 'normal';
            document.getElementById('contact-city').value = c.city || '';
            document.getElementById('contact-address').value = c.address || '';
            document.getElementById('contact-tags').value = (c.tags || []).join(', ');
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
                <td>${d.title}</td>
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
                <td>${r.title}</td>
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

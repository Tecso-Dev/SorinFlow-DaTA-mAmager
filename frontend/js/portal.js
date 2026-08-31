/**
 * SorinFlow — visitor portal.
 *
 * Standalone from app.js on purpose: the portal is reachable by the public, so
 * it carries only what a visitor needs and none of the staff dashboard. Every
 * value that comes back from the server is written through esc() before it
 * touches innerHTML — the dashboard learned that the expensive way.
 */
const API = '/api';
const TOKEN_KEY = 'sf_portal_token';

let _pendingPhone = null;
let _resendTimer = null;

// ─── helpers ───────────────────────────────────────────────
function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}
function getToken() { return localStorage.getItem(TOKEN_KEY); }
function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
function clearToken() { localStorage.removeItem(TOKEN_KEY); }
function currentTheme() { return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'; }
function toggleTheme() {
  const t = currentTheme() === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = t;
  localStorage.setItem('sf-theme', t);
}
function $(id) { return document.getElementById(id); }

function showMsg(el, text, kind) {
  el.textContent = text;
  el.className = 'msg ' + (kind || 'err');
}
function hide(el) { el.classList.add('hide'); }
function show(el) { el.classList.remove('hide'); }

// digits: accept Persian/Arabic, store ASCII
function toAsciiDigits(s) {
  return (s || '').replace(/[۰-۹]/g, d => '۰۱۲۳۴۵۶۷۸۹'.indexOf(d))
                  .replace(/[٠-٩]/g, d => '٠١٢٣٤٥٦٧٨٩'.indexOf(d));
}
function numOrNull(id) {
  const v = toAsciiDigits($(id).value).replace(/[^\d]/g, '');
  return v ? parseInt(v, 10) : null;
}

/** FastAPI sends `detail` as a string, or as a list of {msg} objects on a 422.
 *  Passing the list to new Error() renders "[object Object]" into the form —
 *  which is what a visitor saw for any field the browser did not catch first. */
function detailText(detail) {
  if (!detail) return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(d => (d && d.msg) || JSON.stringify(d)).join(' — ');
  return JSON.stringify(detail);
}

async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (opts.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const tok = getToken();
  if (tok) headers['Authorization'] = 'Bearer ' + tok;
  const resp = await fetch(API + path, { ...opts, headers });
  let data = {};
  try { data = await resp.json(); } catch (_) {}
  if (resp.status === 401) { clearToken(); showAuth(); throw new Error(detailText(data.detail) || 'نیاز به ورود مجدد'); }
  if (!resp.ok) {
    const err = new Error(detailText(data.detail) || 'خطا در ارتباط با سرور');
    err.status = resp.status;            // callers need it to offer the right way out
    err.retryAfter = data.retry_after || 0;
    throw err;
  }
  return data;
}

function withSpinner(btn, on, label) {
  if (on) { btn.disabled = true; btn.dataset.label = btn.innerHTML; btn.innerHTML = '<span class="spinner"></span> ' + label; }
  else { btn.disabled = false; btn.innerHTML = btn.dataset.label || label; }
}

// ─── tabs ───────────────────────────────────────────────────
function switchTab(which) {
  hide($('auth-msg'));
  clearInterval(_resendTimer);
  ['login', 'register', 'verify'].forEach(p => hide($('pane-' + p)));
  $('tab-login').classList.toggle('on', which === 'login');
  $('tab-register').classList.toggle('on', which === 'register');
  show($('auth-tabs'));
  if (which === 'verify') hide($('auth-tabs'));
  show($('pane-' + which));
}

// ─── register / verify / login ──────────────────────────────
async function doPortalRegister() {
  const btn = $('rg-btn');
  const phone = toAsciiDigits($('rg-phone').value.trim());
  const name = $('rg-name').value.trim();
  const pass = $('rg-pass').value;
  const email = $('rg-email').value.trim();
  hide($('auth-msg'));

  clearFieldErrs();
  // At the field, and focus it — so the fix is where the eye already is.
  const bad = (id, msg) => { fieldErr(id, msg); const e = $(id); if (e) e.focus(); return true; };
  if (name.length < 2)            return bad('rg-name', 'نام خود را وارد کنید');
  if (!/^09\d{9}$/.test(phone))   return bad('rg-phone', 'شماره باید با فرمت ۰۹۱۲۳۴۵۶۷۸۹ باشد');
  const r = pwScore(pass);
  if (!r.len)                     return bad('rg-pass', 'رمز عبور باید حداقل ۸ کاراکتر باشد');
  if (Object.values(r).filter(Boolean).length < 3)
                                  return bad('rg-pass', 'رمز عبور ضعیف است — شرط‌های زیر را کامل کنید');
  // Checked here as well as server-side so the message is instant and names
  // the field, rather than coming back as a 422 the visitor has to decode.
  if (!/^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(email))
    return bad('rg-email', 'ایمیل معتبر وارد کنید — کد ورود به این آدرس فرستاده می‌شود');

  withSpinner(btn, true, 'در حال ثبت‌نام…');
  try {
    const res = await api('/public/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        full_name: name, phone, password: pass,
        email, marketing_opt_in: $('rg-optin').checked,
      }),
    });
    _pendingPhone = phone;
    startVerify(res);
  } catch (e) {
    showMsg($('auth-msg'), e.message, 'err'); show($('auth-msg'));
  } finally { withSpinner(btn, false, 'ثبت‌نام و دریافت کد'); }
}

function startVerify(res) {
  switchTab('verify');
  // The server already builds a channel-correct sentence — it knows whether
  // the code went by SMS or email, and this screen did not. Telling someone to
  // check their phone when it landed in their inbox is how a sign-up is
  // abandoned thirty seconds in.
  $('vf-hint').textContent = (res && res.message)
    || ('کد ارسال‌شده به ' + _pendingPhone + ' را وارد کنید.');
  $('vf-code').value = '';
  $('vf-code').focus();
  if (res && res.debug_code) { // only present off-production
    showMsg($('auth-msg'), 'کد تست: ' + res.debug_code, 'ok'); show($('auth-msg'));
  }
  startResendCountdown((res && res.cooldown) || 90);
}

function startResendCountdown(seconds) {
  clearInterval(_resendTimer);
  const btn = $('vf-resend'), timer = $('vf-timer');
  let left = seconds;
  btn.disabled = true;
  const tick = () => {
    if (left <= 0) { clearInterval(_resendTimer); timer.textContent = ''; btn.disabled = false; return; }
    timer.textContent = `ارسال دوباره تا ${left} ثانیه دیگر`;
    left--;
  };
  tick();
  _resendTimer = setInterval(tick, 1000);
}

async function doPortalResend() {
  hide($('auth-msg'));
  const btn = $('vf-resend');
  withSpinner(btn, true, 'در حال ارسال…');
  try {
    const res = await api('/public/auth/resend', { method: 'POST', body: JSON.stringify({ phone: _pendingPhone }) });
    showMsg($('auth-msg'), res.debug_code ? 'کد تست: ' + res.debug_code
                                          : (res.message || 'کد دوباره ارسال شد'), 'ok');
    show($('auth-msg'));
    // Restore the label first, then start the countdown — the countdown owns
    // the disabled state from here. Leaving it to the catch block meant a
    // successful resend left the button spinning "در حال ارسال…" forever.
    withSpinner(btn, false, 'ارسال دوباره کد');
    startResendCountdown(res.cooldown || 90);
  } catch (e) {
    showMsg($('auth-msg'), e.message, 'err'); show($('auth-msg'));
    withSpinner(btn, false, 'ارسال دوباره کد');
  }
}

async function doPortalVerify() {
  const btn = $('vf-btn');
  const code = toAsciiDigits($('vf-code').value.trim());
  hide($('auth-msg'));
  if (!code) return;
  withSpinner(btn, true, 'در حال تأیید…');
  try {
    const data = await api('/public/auth/verify', {
      method: 'POST', body: JSON.stringify({ phone: _pendingPhone, code }),
    });
    setToken(data.access_token);
    clearInterval(_resendTimer);
    await showPortal();
  } catch (e) {
    showMsg($('auth-msg'), e.message, 'err'); show($('auth-msg'));
  } finally { withSpinner(btn, false, 'تأیید و ورود'); }
}

async function doPortalLogin() {
  const btn = $('li-btn');
  const identifier = toAsciiDigits($('li-id').value.trim());
  const password = $('li-pass').value;
  hide($('auth-msg'));
  if (!identifier || !password) return showMsg($('auth-msg'), 'شماره/ایمیل و رمز عبور الزامی است', 'err'), show($('auth-msg'));

  withSpinner(btn, true, 'در حال ورود…');
  try {
    const data = await api('/public/auth/login', {
      method: 'POST', body: JSON.stringify({ identifier, password }),
    });
    if (data.pending) { // unverified account — server sent a fresh code
      _pendingPhone = identifier.match(/^09\d{9}$/) ? identifier : (data.phone || identifier);
      startVerify(data);
      showMsg($('auth-msg'), data.message || 'کد تأیید ارسال شد', 'ok'); show($('auth-msg'));
      return;
    }
    setToken(data.access_token);
    await showPortal();
  } catch (e) {
    showMsg($('auth-msg'), e.message, 'err'); show($('auth-msg'));
  } finally { withSpinner(btn, false, 'ورود'); }
}

function portalLogout() { clearToken(); showAuth(); }

// ─── views ──────────────────────────────────────────────────
function showAuth() { show($('auth-view')); hide($('portal-view')); switchTab('login'); }

async function showPortal() {
  try {
    const me = await api('/portal/me');
    $('pt-name').textContent = me.full_name || 'کاربر';
    $('pt-phone').textContent = me.phone || '';
    hide($('auth-view')); show($('portal-view'));
    syncDealFields();
    await Promise.all([loadRequests(), loadTicket(me.ticket)]);
  } catch (e) {
    showAuth();
  }
}

function syncDealFields() {
  const rent = $('rq-deal').value === 'rent';
  $('rent-fields').classList.toggle('hide', !rent);
  $('buy-fields').classList.toggle('hide', rent);
}

async function submitRequest() {
  const btn = $('rq-btn'); const msg = $('req-msg'); hide(msg);
  const deal = $('rq-deal').value;
  const body = {
    deal_type: deal,
    property_kind: $('rq-kind').value || null,
    city: $('rq-city').value.trim() || null,
    districts: $('rq-districts').value.trim() || null,
    budget_min: deal === 'buy' ? numOrNull('rq-bmin') : null,
    budget_max: deal === 'buy' ? numOrNull('rq-bmax') : null,
    deposit_max: deal === 'rent' ? numOrNull('rq-dep') : null,
    rent_max: deal === 'rent' ? numOrNull('rq-rent') : null,
    area_min: numOrNull('rq-amin'), area_max: numOrNull('rq-amax'),
    rooms_min: numOrNull('rq-rooms'), year_built_min: numOrNull('rq-year'),
    needs_elevator: $('rq-elev').checked, needs_parking: $('rq-park').checked,
    needs_storage: $('rq-store').checked,
    description: $('rq-desc').value.trim() || null,
  };
  withSpinner(btn, true, 'در حال ثبت…');
  try {
    await api('/portal/requests', { method: 'POST', body: JSON.stringify(body) });
    showMsg(msg, 'درخواست شما ثبت شد. کارشناسان ما بررسی می‌کنند.', 'ok'); show(msg);
    $('req-form').reset(); syncDealFields();
    await loadRequests();
  } catch (e) {
    showMsg(msg, e.message, 'err'); msg.className = 'msg err'; show(msg);
  } finally { withSpinner(btn, false, 'ثبت درخواست'); }
}

const STATUS_LABEL = {
  new: ['ثبت شده', 'b-new'], in_review: ['در حال بررسی', 'b-review'],
  matched: ['مورد پیدا شد', 'b-ok'], contacted: ['تماس گرفته شد', 'b-ok'],
  closed: ['بسته شده', 'b-off'],
};
const DEAL_LABEL = { buy: 'خرید', rent: 'اجاره' };

async function loadRequests() {
  const box = $('req-list');
  try {
    const { items } = await api('/portal/requests/mine');
    if (!items.length) { box.innerHTML = '<p class="muted">هنوز درخواستی ثبت نکرده‌اید.</p>'; return; }
    box.innerHTML = items.map(r => {
      const [lbl, cls] = STATUS_LABEL[r.status] || [r.status, 'b-off'];
      const bits = [DEAL_LABEL[r.deal_type] || r.deal_type, r.city, r.districts].filter(Boolean).map(esc).join(' • ');
      return `<div class="item"><div class="head">
        <strong>${bits || 'درخواست ملک'}</strong>
        <span class="badge ${cls}">${esc(lbl)}</span></div>
        ${r.description ? `<div class="muted" style="margin-top:.4rem">${esc(r.description)}</div>` : ''}
        ${r.admin_note ? `<div style="margin-top:.4rem;color:var(--brand)">پاسخ کارشناس: ${esc(r.admin_note)}</div>` : ''}
        <div style="margin-top:.6rem"><button class="btn sm danger" onclick="deleteRequest(${r.id})">حذف</button></div>
      </div>`;
    }).join('');
  } catch (e) { box.innerHTML = `<p class="msg err">${esc(e.message)}</p>`; }
}

async function deleteRequest(id) {
  if (!confirm('این درخواست حذف شود؟')) return;
  try { await api('/portal/requests/' + id, { method: 'DELETE' }); await loadRequests(); }
  catch (e) { alert(e.message); }
}

function loadTicket(ticket) {
  const box = $('ticket-box');
  if (ticket && ticket.status === 'pending') {
    box.innerHTML = `<div class="item"><div class="head"><strong>درخواست ارتقا</strong>
      <span class="badge b-review">در حال بررسی</span></div>
      <div class="muted" style="margin-top:.4rem">درخواست شما برای مدیر ارسال شده و در انتظار بررسی است.</div></div>`;
    return;
  }
  if (ticket && ticket.status === 'approved') {
    box.innerHTML = `<div class="item"><div class="head"><strong>درخواست ارتقا</strong>
      <span class="badge b-ok">تأیید شد</span></div>
      <div class="muted" style="margin-top:.4rem">دسترسی شما فعال شد. از
      <a href="/dashboard">پنل مدیریت</a> وارد شوید.</div></div>`;
    return;
  }
  const rejected = ticket && ticket.status === 'rejected'
    ? `<div class="muted" style="margin-bottom:.6rem">درخواست قبلی شما تأیید نشد${ticket.decision_note ? '؛ ' + esc(ticket.decision_note) : ''}.</div>` : '';
  box.innerHTML = rejected + `
    <label for="tk-msg">توضیح کوتاه (اختیاری)</label>
    <textarea id="tk-msg" placeholder="مثلاً: مشاور املاک در ارومیه هستم و به پنل نیاز دارم"></textarea>
    <button class="btn" id="tk-btn" onclick="submitTicket()">ارسال درخواست دسترسی</button>`;
}

async function submitTicket() {
  const btn = $('tk-btn');
  withSpinner(btn, true, 'در حال ارسال…');
  try {
    await api('/portal/tickets', { method: 'POST', body: JSON.stringify({ message: ($('tk-msg').value || '').trim() || null }) });
    const me = await api('/portal/me');
    loadTicket(me.ticket);
  } catch (e) { alert(e.message); withSpinner(btn, false, 'ارسال درخواست دسترسی'); }
}

// ─── boot ───────────────────────────────────────────────────
(async function init() {
  // If public auth is off, this page should not pretend to work.
  try {
    const st = await fetch(API + '/public/auth/status').then(r => r.json());
    if (!st.enabled) {
      document.body.innerHTML = '<div class="center"><div class="card auth-card" style="text-align:center">'
        + '<div class="brand"><div class="logo">سورین‌فلو</div></div>'
        + '<p>ثبت‌نام کاربران به‌زودی فعال می‌شود.</p>'
        + '<p class="muted">کاربر پنل هستید؟ <a href="/dashboard">ورود به پنل</a></p></div></div>';
      return;
    }
  } catch (_) { /* if status fails, still let them try to log in */ }

  $('rg-phone').addEventListener('input', e => { e.target.value = toAsciiDigits(e.target.value); });
  $('vf-code').addEventListener('input', e => { e.target.value = toAsciiDigits(e.target.value).replace(/\D/g, ''); });

  if (getToken()) { await showPortal(); } else { showAuth(); }
})();


// ═══ auth UX ═══════════════════════════════════════════════════════════════
// Same practices as the panel — see frontend/js/app.js. Kept deliberately
// small and dependency-free: this page is the first thing a customer sees and
// must render on a slow connection.

function fieldErr(id, msg) {
  const box = $('e-' + id), wrap = $('w-' + id);
  if (box) { box.textContent = msg || ''; box.classList.toggle('show', !!msg); }
  if (wrap) wrap.classList.toggle('invalid', !!msg);
}

function clearFieldErrs() {
  document.querySelectorAll('.f-err').forEach(e => { e.textContent=''; e.classList.remove('show'); });
  document.querySelectorAll('.fld').forEach(f => f.classList.remove('invalid'));
}

/** A problem plus the way out of it, rather than a bare statement of failure. */
function showMsgFix(el, message, fix, kind) {
  el.className = 'msg ' + (kind || 'err');
  el.innerHTML = escapeHtml(message) + (fix ? '<span class="fix">' + escapeHtml(fix) + '</span>' : '');
  show(el);
}

function escapeHtml(t) {
  return String(t == null ? '' : t).replace(/[&<>"']/g,
    c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}

// show / hide password — paste stays enabled, password managers depend on it
document.addEventListener('click', e => {
  const b = e.target.closest('.pw-eye');
  if (!b) return;
  const i = $(b.dataset.for);
  if (!i) return;
  const showing = i.type === 'text';
  i.type = showing ? 'password' : 'text';
  b.textContent = showing ? '👁' : '🙈';
  const lbl = showing ? 'نمایش رمز عبور' : 'پنهان کردن رمز عبور';
  b.setAttribute('aria-label', lbl); b.title = lbl;
  i.focus();
});

function watchCaps(inputId, warnId) {
  const i = $(inputId), w = $(warnId);
  if (!i || !w) return;
  const check = ev => w.classList.toggle('show',
    !!(ev.getModifierState && ev.getModifierState('CapsLock')));
  i.addEventListener('keyup', check);
  i.addEventListener('keydown', check);
  i.addEventListener('blur', () => w.classList.remove('show'));
}

function pwScore(v) {
  return { len: v.length >= 8, lower: /[a-z]/.test(v),
           digit: /[0-9]/.test(v), upper: /[A-Z]/.test(v) || /[^A-Za-z0-9]/.test(v) };
}

function paintStrength() {
  const i = $('rg-pass');
  if (!i) return;
  const r = pwScore(i.value);
  const met = Object.values(r).filter(Boolean).length;
  document.querySelectorAll('#rg-rules li').forEach(li => li.classList.toggle('met', !!r[li.dataset.rule]));
  const steps = [
    { w:'0%',   c:'transparent', t:'قدرت رمز عبور' },
    { w:'25%',  c:'var(--err)',  t:'خیلی ضعیف' },
    { w:'50%',  c:'#f0a35e',     t:'ضعیف' },
    { w:'75%',  c:'#f0c95e',     t:'متوسط' },
    { w:'100%', c:'var(--ok)',   t:'قوی' },
  ][i.value ? met : 0];
  const bar = $('rg-meter'), lab = $('rg-meter-label');
  if (bar) { bar.style.width = steps.w; bar.style.background = steps.c; }
  if (lab) lab.textContent = steps.t;
}

document.addEventListener('DOMContentLoaded', () => {
  watchCaps('li-pass', 'caps-li');
  watchCaps('rg-pass', 'caps-rg');
  const pw = $('rg-pass');
  if (pw) pw.addEventListener('input', paintStrength);

  // Autofocus the first field, but not on touch — raising the keyboard hides
  // the form the person was about to read.
  if (!window.matchMedia || !window.matchMedia('(pointer: coarse)').matches) {
    const first = $('li-id');
    if (first && !first.closest('.hide')) first.focus();
  }

  // Validate after a field is finished, not while it is being typed.
  const onBlur = (id, check) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('blur', () => { if (el.value.trim()) fieldErr(id, check(el.value.trim()) || ''); });
    el.addEventListener('input', () => fieldErr(id, ''));
  };
  onBlur('rg-email', v => /^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(v) ? '' : 'ایمیل معتبر وارد کنید');
  onBlur('rg-phone', v => /^09\d{9}$/.test(toAsciiDigits(v)) ? '' : 'شماره باید با فرمت ۰۹۱۲۳۴۵۶۷۸۹ باشد');
  onBlur('rg-name',  v => v.length >= 2 ? '' : 'نام را کامل وارد کنید');
});

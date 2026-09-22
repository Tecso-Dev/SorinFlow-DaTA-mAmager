/**
 * خوانندهٔ نیاز مشتری — the customer form fills itself from what the customer said.
 *
 * The consultant writes the phone call as it was («یه واحد ۱۰۰ متری نوساز طرف
 * گلها تا ۵ میلیارد، طبقهٔ اول نباشه») into #cust-ai-text and presses «پر کردن
 * از متن». The server (POST /ai/need/parse) turns it into the intake form's
 * columns; this file puts them into the form — and only into fields that are
 * still empty, because what the consultant typed with their own hands is
 * theirs. Nothing is saved here: the consultant reads, corrects, and presses
 * «ذخیره مشتری» as always.
 *
 * Panel helpers used: apiCall, showToast, formatNumber. Loaded after app.js.
 */
(function () {
    'use strict';

    // Customer column → the form control that holds it (see openCustomerModal in app.js).
    const FIELDS = {
        desired_city: 'cust-city',
        desired_district: 'cust-district',
        desired_type: 'cust-desired-type',
        deal_type: 'cust-deal-type',
        budget_max: 'cust-budget',
        desired_specs: 'cust-specs',
        red_lines: 'cust-redlines',
        notes: 'cust-notes',
        temperature: 'cust-temperature',
    };
    // A select still showing the value the form opened with was never "typed":
    // for a NEW customer it is free to fill. When editing, what is there came
    // from the database and stays.
    const DEFAULTS = { 'cust-deal-type': 'buy', 'cust-temperature': 'warm' };
    const HIGHLIGHT_MS = 2000;

    function isNewCustomer() {
        return typeof _customerEditId === 'undefined' || !_customerEditId;
    }

    function isEmpty(el) {
        const v = String(el.value ?? '').trim();
        if (v === '') return true;
        return isNewCustomer() && DEFAULTS[el.id] !== undefined && DEFAULTS[el.id] === v;
    }

    function fill(el, value) {
        el.value = value;
        // a select rejects a value it has no option for; count only what stuck
        if (String(el.value) !== String(value)) return false;
        el.classList.add('ai-filled');
        setTimeout(() => el.classList.remove('ai-filled'), HIGHLIGHT_MS);
        return true;
    }

    // «۷۰٪ تا ۹۵٪» — the model's own confidence, lowest to highest, so the
    // consultant knows how hard to look at what was filled
    function confidenceRange(conf) {
        const vals = Object.values(conf || {}).map(Number).filter(n => !isNaN(n));
        if (!vals.length) return null;
        const pct = n => formatNumber(Math.round(n * 100)) + '٪';
        const lo = Math.min(...vals), hi = Math.max(...vals);
        return lo === hi ? pct(lo) : `${pct(lo)} تا ${pct(hi)}`;
    }

    async function aiFillCustomerFromText() {
        const box = document.getElementById('cust-ai-text');
        const btn = document.getElementById('cust-ai-btn');
        const text = (box ? box.value : '').trim();
        if (!text) { showToast('هوش مصنوعی', 'اول حرف مشتری را بنویسید', 'warning'); return; }

        // what the form already knows helps the model read the rest
        const hint = {};
        const city = document.getElementById('cust-city');
        const deal = document.getElementById('cust-deal-type');
        if (city && !isEmpty(city)) hint.city = city.value.trim();
        if (deal && !isEmpty(deal)) hint.deal_type = deal.value;

        const label = btn ? btn.innerHTML : '';
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> در حال خواندن…'; }
        try {
            const out = await apiCall('/ai/need/parse', { method: 'POST', body: JSON.stringify({ text, hint }) });
            const customer = out.customer || {};
            let filled = 0;
            for (const [key, id] of Object.entries(FIELDS)) {
                const el = document.getElementById(id);
                const value = customer[key];
                if (!el || value === null || value === undefined || value === '') continue;
                if (!isEmpty(el)) continue;   // never over what the consultant typed
                if (fill(el, value)) filled++;
            }
            const range = confidenceRange((out.criteria || {}).confidence);
            if (filled) {
                showToast('هوش مصنوعی',
                    `${formatNumber(filled)} فیلد پر شد` + (range ? ` — اطمینان مدل ${range}` : '') + '. بررسی کنید و ذخیره بزنید.',
                    'success');
            } else {
                showToast('هوش مصنوعی', 'چیز تازه‌ای پیدا نشد — فیلدها یا پر بودند یا متن چیزی نگفته', 'warning');
            }
        } catch (e) {
            showToast('خطا', e.message, 'danger');
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = label; }
        }
    }
    window.aiFillCustomerFromText = aiFillCustomerFromText;

    // a fresh form gets a fresh text box; _resetCustomerForm (app.js) does not know this one
    const modal = document.getElementById('customerModal');
    if (modal) modal.addEventListener('show.bs.modal', () => {
        const box = document.getElementById('cust-ai-text');
        if (box) box.value = '';
        modal.querySelectorAll('.ai-filled').forEach(el => el.classList.remove('ai-filled'));
    });
})();

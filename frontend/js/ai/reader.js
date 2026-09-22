// خوانندهٔ آگهی — what the model read out of the listing's own words.
//
// Rendered inside the property modal by viewProperty(): the facts as chips,
// the deal flags set apart, the red flags in warning colour, the model's
// confidence per chip on hover, and «بازخوانی» for root / super_admin.
// The globals (esc, apiCall, formatNumber, showToast, _currentUser) are the
// ones app.js defines; this file adds nothing to the page on its own.

const AI_KIND_FA = { apartment: 'آپارتمان', house: 'خانه / ویلایی', land: 'زمین', shop: 'مغازه', office: 'دفتر', other: 'سایر' };
const AI_AMENITIES = [['has_elevator', 'آسانسور'], ['has_parking', 'پارکینگ'], ['has_storage', 'انباری'], ['has_balcony', 'بالکن']];
const AI_DEAL_FLAGS = [['convertible', 'قابل تبدیل'], ['exchange', 'معاوضه'], ['vacant', 'تخلیه'], ['negotiable', 'قابل مذاکره']];

function _aiChip(label, value, cls, conf) {
    const title = (conf !== undefined && conf !== null)
        ? ` title="اطمینان ${formatNumber(Math.round(conf * 100))}٪"` : '';
    return `<span class="ai-chip ${cls || ''}"${title}>` +
        (label ? `<span class="ai-chip-l">${esc(label)}</span>` : '') + esc(value) + `</span>`;
}

function _aiHead(id) {
    const canReread = ['root', 'super_admin'].includes(_currentUser?.role);
    return `<div class="ai-facts-head">
        <span class="ai-facts-title"><i class="bi bi-stars"></i> برداشت هوش مصنوعی</span>
        ${canReread ? `<button type="button" class="btn btn-sm btn-outline-secondary ai-facts-reread" onclick="aiReread(${Number(id)})">
            <i class="bi bi-arrow-clockwise"></i> بازخوانی</button>` : ''}
    </div>`;
}

// container: the <div id="ai-facts" data-id="…" data-read-at="…"> in the modal.
// facts: property.ai_facts (null until the reader has been through).
function aiRenderFacts(container, facts) {
    if (!container) return;
    const id = container.dataset.id;
    if (!facts) {
        container.innerHTML = _aiHead(id) + `<div class="ai-facts-empty">هنوز خوانده نشده — خواننده هر دو دقیقه آگهی‌های تازه را می‌خواند.</div>`;
        return;
    }
    const c = facts.confidence || {};
    const chips = [];
    if (facts.kind) chips.push(_aiChip('نوع', AI_KIND_FA[facts.kind] || facts.kind, '', c.kind));
    if (facts.floor !== null && facts.floor !== undefined) {
        const of = (facts.total_floors !== null && facts.total_floors !== undefined) ? ' از ' + formatNumber(facts.total_floors) : '';
        chips.push(_aiChip('طبقه', formatNumber(facts.floor) + of, '', c.floor));
    }
    if (facts.year_built) chips.push(_aiChip('ساخت', formatNumber(facts.year_built), '', c.year_built));
    if (facts.document) chips.push(_aiChip('سند', facts.document, '', c.document));
    if (facts.condition) chips.push(_aiChip('وضعیت', facts.condition, '', c.condition));
    if (facts.district) chips.push(_aiChip('منطقه', facts.district, '', c.district));
    AI_AMENITIES.forEach(([k, fa]) => {
        if (facts[k] === true) chips.push(_aiChip('', fa, 'is-amenity', c[k]));
        else if (facts[k] === false) chips.push(_aiChip('', 'بدون ' + fa, 'is-off', c[k]));
    });
    AI_DEAL_FLAGS.forEach(([k, fa]) => { if (facts[k] === true) chips.push(_aiChip('', fa, 'is-deal', c[k])); });
    if (facts.negotiable === false) chips.push(_aiChip('', 'قیمت مقطوع', 'is-off', c.negotiable));
    (facts.suitable_for || []).forEach(s => chips.push(_aiChip('مناسب', s, 'is-use', c.suitable_for)));
    (facts.red_flags || []).forEach(s => chips.push(_aiChip('', '⚠ ' + s, 'is-warn', c.red_flags)));

    const readAt = container.dataset.readAt;
    const meta = [facts.model || '', readAt ? new Date(readAt).toLocaleString('fa-IR') : ''].filter(Boolean).join(' · ');
    container.innerHTML = _aiHead(id) +
        (facts.summary ? `<p class="ai-facts-summary">${esc(facts.summary)}</p>` : '') +
        `<div class="ai-facts-chips">${chips.join('') || '<span class="ai-facts-empty">متن آگهی چیزی بیش از فیلدهای خودش نمی‌گفت.</span>'}</div>` +
        (meta ? `<div class="ai-facts-meta">${esc(meta)}</div>` : '');
}

async function aiReread(id) {
    const box = document.getElementById('ai-facts');
    const btn = box && box.querySelector('.ai-facts-reread');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> در حال خواندن…'; }
    try {
        const out = await apiCall('/ai/reader/' + id, { method: 'POST' });
        if (box) { box.dataset.readAt = new Date().toISOString(); aiRenderFacts(box, out.facts); }
        showToast('هوش مصنوعی', 'آگهی دوباره خوانده شد', 'success');
    } catch (e) {
        showToast('هوش مصنوعی', e.message || 'بازخوانی ناموفق بود', 'danger');
        if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-arrow-clockwise"></i> بازخوانی'; }
    }
}

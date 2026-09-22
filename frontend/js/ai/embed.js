// یابندهٔ معنایی و تکراری‌یاب — the panel's side of app/ai/embeddings.py.
//
// Loaded after app.js and uses its globals (esc, apiCall, formatPrice,
// formatNumber, formatSerial, showToast, viewProperty, _matchCard). Nothing
// here runs at load time, so the order only matters at call time.
// No native dialogs: the panel has its own (askInfo / askConfirm).

/**
 * «احتمالاً تکراری» — a small badge with a link to the original listing,
 * when the server has flagged this one (prop.ai_duplicate_of). Works for a
 * lead row and for a property: both carry the same field name.
 * Returns '' when there is nothing to say, so it can sit inline anywhere.
 */
function aiDuplicateBadge(prop) {
    const id = Number(prop && prop.ai_duplicate_of);
    if (!Number.isInteger(id) || id <= 0) return '';
    return `<span class="ai-dup-badge" title="متن این آگهی تقریباً همان آگهی دیگری است — احتمالاً همان ملک با عنوان دیگر. فقط یک نشانه است؛ چیزی ادغام یا حذف نشده.">` +
        `<i class="bi bi-files"></i> احتمالاً تکراری` +
        ` <a href="#" onclick="event.preventDefault(); event.stopPropagation(); viewProperty(${id});" title="باز کردن آگهی اصلی">اصل</a>` +
        `</span>`;
}

/**
 * POST /ai/embed/search — a customer's own words → the closest listings.
 * Resolves to the items (same shape as the match cards, plus `similarity`);
 * throws the API's message so the caller decides where to show it.
 */
async function aiSemanticSearch(text, opts = {}) {
    const body = { text: String(text || '').trim(), limit: opts.limit || 12 };
    if (opts.city) body.city = opts.city;
    if (opts.listing_type) body.listing_type = opts.listing_type;
    const data = await apiCall('/ai/embed/search', { method: 'POST', body: JSON.stringify(body) });
    return data.items || [];
}

/**
 * Draw semantic results into `container` (an element or its id). Uses the
 * panel's own match card when it is there, a compact card otherwise.
 */
function aiRenderSemanticResults(container, items, opts = {}) {
    const el = typeof container === 'string' ? document.getElementById(container) : container;
    if (!el) return;
    if (!items || !items.length) {
        el.innerHTML = `
            <div class="text-center py-5 text-muted">
                <i class="bi bi-search" style="font-size:2rem"></i>
                <p class="mt-3">${esc(opts.emptyMsg || 'آگهی نزدیکی به این متن پیدا نشد — آگهی‌های تازه چند دقیقه بعد از رسیدن، بردار می‌گیرند.')}</p>
            </div>`;
        return;
    }
    const head = opts.query
        ? `<div class="match-source">نزدیک‌ترین آگهی‌ها به: <b>${esc(opts.query)}</b> — ${formatNumber(items.length)} مورد</div>`
        : '';
    const cards = items.map(m => typeof _matchCard === 'function' ? _matchCard(m) : _aiPlainCard(m)).join('');
    el.innerHTML = `${head}<div class="match-list">${cards}</div>`;
}

// The fallback card, for a page that does not have the match modal's renderer.
function _aiPlainCard(m) {
    const where = [m.city_name, m.district].filter(Boolean).map(esc).join(' · ');
    return `
    <div class="match-card">
        <div class="match-score ${m.score >= 75 ? 'high' : m.score >= 50 ? 'mid' : 'low'}">${formatNumber(m.score)}<small>٪</small></div>
        <div class="match-body">
            <div class="match-title" title="${esc(m.title)}">${esc(m.title)} ${aiDuplicateBadge(m)}</div>
            <div class="match-meta">
                ${m.serial_no != null ? `<span class="serial-badge">${formatSerial(m.serial_no)}</span> ` : ''}${where}
                ${m.area ? ' · ' + formatNumber(m.area) + ' متر' : ''}${m.rooms != null ? ' · ' + formatNumber(m.rooms) + ' خواب' : ''}
            </div>
        </div>
        <div class="match-side">
            <div class="match-price">${m.price ? formatPrice(m.price) : 'توافقی'}</div>
            <button class="btn btn-sm btn-outline-primary" onclick="viewProperty(${m.id})"><i class="bi bi-eye"></i> جزئیات</button>
        </div>
    </div>`;
}

/**
 * Search and show in the existing match modal (#matchModal) — the one
 * «ملک‌های مشابه» uses — so the integrator needs no new markup.
 */
async function aiOpenSemanticSearch(text, opts = {}) {
    const query = String(text || '').trim();
    if (query.length < 2) { showToast('جستجوی معنایی', 'چند کلمه دربارهٔ آنچه مشتری می‌خواهد بنویسید', 'warning'); return; }
    const modalEl = document.getElementById('matchModal');
    const body = document.getElementById('match-modal-body');
    if (!modalEl || !body) return;
    document.getElementById('match-modal-title').innerHTML = '<i class="bi bi-stars"></i> جستجوی معنایی';
    body.innerHTML = '<div class="text-center py-5 text-muted"><span class="spinner-border"></span><p class="mt-3">در حال یافتن نزدیک‌ترین آگهی‌ها...</p></div>';
    new bootstrap.Modal(modalEl).show();
    try {
        const items = await aiSemanticSearch(query, opts);
        aiRenderSemanticResults(body, items, { query });
    } catch (e) {
        body.innerHTML = `<div class="alert alert-danger">جستجوی معنایی انجام نشد: ${esc(e.message)}</div>`;
    }
}

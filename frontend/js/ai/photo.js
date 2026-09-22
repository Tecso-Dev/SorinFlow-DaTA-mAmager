// برچسب‌زن عکس — what the vision model saw in a listing's photos, as chips.
//
// GET /ai/photo/<id> answers {id, tags, labels, tagged_at}: the labels are
// Persian and come from the server (photo_tagger.tags_fa), so the panel
// never re-translates the vocabulary. POST /ai/photo/<id> tags one listing
// again; GET /ai/photo/status and POST /ai/photo/run feed the card on the
// «هوش تصویری» page. root and super_admin only — for anyone else the API
// answers 403 and the boxes simply stay empty. Uses the panel's own globals
// (esc, apiCall, showToast, formatNumber); no native dialogs.

const AI_PHOTO_STOP_FA = {
    BudgetExceeded: 'سقف روزانهٔ هوش مصنوعی پر شد',
    Disabled: 'هوش مصنوعی از پنل خاموش است',
    NotConfigured: 'هوش مصنوعی تنظیم نشده است',
};

function aiRenderPhotoTags(container, data) {
    if (!container) return;
    const tags = (data && data.tags) || null;
    const labels = (data && data.labels) || [];
    const id = data && data.id;
    const q = tags && tags.quality ? Number(tags.quality) : 0;
    const chips = labels.length
        ? labels.map(l => `<span class="ai-photo-chip">${esc(l)}</span>`).join('')
        : `<span class="ai-photo-empty">${tags ? 'برچسبی پیدا نشد' : 'هنوز بررسی نشده'}</span>`;
    // five dots rather than a number: «۴ از ۵» reads as a grade, and it is an impression
    const meter = q ? `<span class="ai-photo-meter" title="کیفیت عکاسی ${formatNumber(q)} از ۵">${
        [1, 2, 3, 4, 5].map(i => `<i class="${i <= q ? 'on' : ''}"></i>`).join('')}</span>` : '';
    const meta = tags && !tags.skipped
        ? `<span class="ai-photo-meta">${formatNumber(tags.photos || 0)} عکس · ${esc(tags.model || '')}` +
          `${tags.notes ? ' · ' + esc(tags.notes) : ''}</span>`
        : '';
    container.classList.add('ai-photo');
    container.innerHTML = `
        <div class="ai-photo-head">
            <span class="ai-photo-title"><i class="bi bi-stars"></i> برچسب‌های هوش تصویری</span>
            ${meter}
            ${id ? `<button type="button" class="ai-photo-btn" onclick="aiRetagPhoto(${Number(id)}, this)">برچسب‌زنی دوباره</button>` : ''}
        </div>
        <div class="ai-photo-chips">${chips}</div>
        ${meta}`;
}

async function aiLoadPhotoTags(propertyId, container) {
    if (!container) return;
    try {
        aiRenderPhotoTags(container, await apiCall(`/ai/photo/${propertyId}`));
    } catch (e) {
        container.innerHTML = '';       // a consultant is not root: the box is simply not there
    }
}

async function aiRetagPhoto(propertyId, btn) {
    const container = btn && btn.closest('.ai-photo');
    if (btn) { btn.disabled = true; btn.textContent = 'در حال بررسی…'; }
    try {
        const data = await apiCall(`/ai/photo/${propertyId}`, { method: 'POST' });
        aiRenderPhotoTags(container, data);
        showToast('برچسب‌زن عکس', data.tags && data.tags.skipped ? 'عکسی روی دیسک نیست' : 'برچسب‌ها به‌روز شد', 'success');
    } catch (e) {
        if (btn) { btn.disabled = false; btn.textContent = 'برچسب‌زنی دوباره'; }
        showToast('برچسب‌زن عکس', e.message || 'انجام نشد', 'danger');
    }
}

// ── the «هوش تصویری» card: where the pass stands, and one pass on demand ──

function aiRenderPhotoStatus(container, s) {
    if (!container) return;
    const ready = s.configured && s.enabled;
    const state = !s.configured ? 'هوش مصنوعی تنظیم نشده' : !s.enabled ? 'از پنل خاموش است' : 'فعال — هر ۵ دقیقه یک دور';
    container.classList.add('ai-photo-status');
    container.innerHTML = `
        <div class="ai-photo-stats">
            <div><b>${formatNumber(s.tagged)}</b><span>برچسب‌خورده</span></div>
            <div><b>${formatNumber(s.skipped)}</b><span>بدون عکس</span></div>
            <div><b>${formatNumber(s.behind)}</b><span>در صف</span></div>
        </div>
        <div class="ai-photo-head">
            <span class="ai-photo-meta">${esc(state)} · مدل: ${esc(s.model || '—')} · نسخهٔ پرسش ${formatNumber(s.version)}</span>
            <button type="button" class="ai-photo-btn" onclick="aiRunPhotoPass(this)" ${ready ? '' : 'disabled'}>یک دور الان</button>
        </div>`;
}

async function aiLoadPhotoStatus(container) {
    if (!container) return;
    try {
        aiRenderPhotoStatus(container, await apiCall('/ai/photo/status'));
    } catch (e) {
        container.innerHTML = '';
    }
}

async function aiRunPhotoPass(btn) {
    const container = btn && btn.closest('.ai-photo-status');
    if (btn) { btn.disabled = true; btn.textContent = 'در حال اجرا…'; }
    try {
        const r = await apiCall('/ai/photo/run?limit=30', { method: 'POST' });
        const line = `${formatNumber(r.tagged)} برچسب‌خورده · ${formatNumber(r.skipped)} بدون عکس · ${formatNumber(r.failed)} ناموفق`;
        showToast('برچسب‌زن عکس',
                  r.stopped ? `${line} · ${AI_PHOTO_STOP_FA[r.stopped] || r.stopped}` : line,
                  r.stopped ? 'warning' : 'success');
    } catch (e) {
        showToast('برچسب‌زن عکس', e.message || 'انجام نشد', 'danger');
    }
    aiLoadPhotoStatus(container);
}

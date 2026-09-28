// Behavioural check for the scheduled-scrapes card, run against the REAL
// loadSchedules in frontend/js/app.js with the DOM and the API stubbed.
// Run with TZ=Europe/Berlin, the way Sobhan's laptop is: an 08:00 Tehran
// schedule must still read 08:00.
import { readFileSync } from 'node:fs';
import { strict as assert } from 'node:assert';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = readFileSync(path.join(here, '..', '..', 'frontend', 'js', 'app.js'), 'utf8');
const html = readFileSync(path.join(here, '..', '..', 'frontend', 'index.html'), 'utf8');

function extractFunction(name) {
    let start = src.indexOf(`function ${name}(`);
    if (start === -1) throw new Error(`${name}() not found in app.js`);
    if (src.slice(start - 6, start) === 'async ') start -= 6;
    let end = src.indexOf('{', start), depth = 0;
    for (; end < src.length; end++) {
        if (src[end] === '{') depth++;
        else if (src[end] === '}') { depth--; if (depth === 0) { end++; break; } }
    }
    return src.slice(start, end);
}

let passed = 0;

// the card is not born hidden, and it has its own «add» button
const card = html.slice(html.indexOf('id="schedules-card"') - 40, html.indexOf('id="schedules-card"') + 900);
assert.ok(!/class="[^"]*d-none[^"]*"\s+id="schedules-card"/.test(card), 'the card starts hidden');
assert.ok(card.includes('onclick="saveAsSchedule()"'), 'no add button on the card');
passed++;

function world(rows) {
    const el = {
        'schedules-card': { classList: { hidden: false, toggle(c, on) { if (c === 'd-none') this.hidden = !!on; } } },
        'schedules-table': { innerHTML: '' },
        'schedules-count': { textContent: '' },
    };
    const document = { getElementById: (id) => el[id] || null };
    const run = new Function('document', 'apiCall', `
        ${extractFunction('esc')}
        ${extractFunction('formatNumber')}
        ${extractFunction('loadSchedules')}
        return loadSchedules;`)(document, async () => ({ schedules: rows, can_see_all: false }));
    return { el, run };
}

{   // empty: still there, and it says how to make one
    const { el, run } = world([]);
    await run();
    assert.equal(el['schedules-card'].classList.hidden, false);
    assert.ok(el['schedules-table'].innerHTML.includes('هنوز زمان‌بندی‌ای نیست'));
    assert.ok(el['schedules-table'].innerHTML.includes('هر روز خودکار اجرا شود'));
    passed++;
}
{   // an 08:00 Tehran schedule reads 08:00 on a Berlin clock
    const { el, run } = world([{ id: 5, name: 'urmia — خرید آپارتمان', hour: 8, minute: 0, enabled: true,
        next_run_at: '2026-09-27T04:30:00+00:00', config: { city: 'urmia', category: 'buy-apartment' } }]);
    await run();
    const out = el['schedules-table'].innerHTML;
    assert.ok(out.includes('بعدی: ') && out.includes('۰۸:۰۰'), out);
    assert.ok(!out.includes('۰۶:۳۰'), 'the next run is shown on the viewer\'s clock');
    passed++;
}

// ── the publish date: «امروز», «دیروز», «N روز پیش» (#33) ──────────────────────

{   // the card says the date in words; a converted fixed date says so, escaped like any server text
    const { el, run } = world([{ id: 6, name: 'دیروزها', hour: 8, minute: 0, enabled: true, next_run_at: null,
        posted_days_ago: 1, posted_label: 'دیروز',
        date_note: 'تاریخ ثابت ۱۴۰۵/۰۷/۰۵ به «دیروز» تبدیل شد <img src=x onerror=alert(1)>',
        config: { city: 'urmia', category: 'rent-apartment', posted_days_ago: 1 } }]);
    await run();
    const out = el['schedules-table'].innerHTML;
    assert.ok(out.includes('انتشار: دیروز'), out);
    assert.ok(out.includes('تبدیل شد') && !out.includes('<img'), 'the conversion note is missing or not escaped');
    assert.ok(out.includes('editScheduleDate(6, 1)'), 'the date button does not know the current value');
    passed++;
}
{   // without a date it is still «the last 24 hours», and the button offers no current value
    const { el, run } = world([{ id: 7, name: 'صبح', hour: 8, minute: 0, enabled: true, next_run_at: null,
        posted_days_ago: null, posted_label: null, date_note: null,
        config: { city: 'urmia', category: 'rent-apartment', max_age_hours: 24 } }]);
    await run();
    const out = el['schedules-table'].innerHTML;
    assert.ok(out.includes('۲۴ ساعت اخیر') && !out.includes('انتشار:'), out);
    assert.ok(out.includes('editScheduleDate(7, null)'), out);
    passed++;
}

const maxDaysAgo = Number(/const SCHEDULE_MAX_DAYS_AGO = (\d+);/.exec(src)?.[1]);
assert.ok(maxDaysAgo > 1, 'SCHEDULE_MAX_DAYS_AGO is not in app.js');

/** The form's helpers with the dialogs, the API and the DOM stubbed. `answers`
 *  maps a dialog's title to what the person picked (absent: they cancelled). */
function formWorld(answers, { formCfg, pages = '' } = {}) {
    const asked = [], calls = [], toasts = [];
    const stubs = {
        askText: async (opts) => { asked.push(opts); const a = answers[opts.title]; return a === undefined ? null : a; },
        apiCall: async (url, opts) => { calls.push({ url, ...opts, body: JSON.parse(opts.body) }); return {}; },
        showToast: (title, msg, kind) => toasts.push({ title, msg, kind }),
        loadSchedules: () => {},
        _scrapeFormConfig: () => ({ city: 'urmia', category: 'rent-apartment', download_images: true, max_items: 50, ...formCfg }),
        cityName: (s) => s, categoryName: (s) => s,
        document: { getElementById: (id) => id === 'scraper-pages' ? { value: pages } : null },
    };
    const names = Object.keys(stubs);
    const api = new Function(...names, 'SCHEDULE_MAX_DAYS_AGO', `
        ${extractFunction('esc')}
        ${extractFunction('formatNumber')}
        ${extractFunction('_digitsOnly')}
        ${extractFunction('_postedDaysWords')}
        ${extractFunction('askPostedDaysAgo')}
        ${extractFunction('saveAsSchedule')}
        ${extractFunction('editScheduleDate')}
        return { askPostedDaysAgo, saveAsSchedule, editScheduleDate };`)(...names.map(n => stubs[n]), maxDaysAgo);
    return { ...api, asked, calls, toasts };
}
const DATE = 'تاریخ انتشار آگهی‌ها', HOW_MANY = 'چند روز پیش؟', WHEN = 'اجرای روزانه', NAME = 'اسم زمان‌بندی';

{   // the choices, and what each one answers
    for (const [pick, want] of [['none', null], ['0', 0], ['1', 1]]) {
        const w = formWorld({ [DATE]: pick });
        assert.equal(await w.askPostedDaysAgo(null), want, pick);
        assert.equal(w.asked.length, 1, 'a plain choice needs no second question');
    }
    const words = formWorld({ [DATE]: 'n', [HOW_MANY]: '۷' });         // typed with Persian digits
    assert.equal(await words.askPostedDaysAgo(null), 7);
    const labels = words.asked[0].field.options.map(([, l]) => l).join('|');
    assert.ok(['امروز', 'دیروز', 'چند روز پیش', '۲۴ ساعت'].every(t => labels.includes(t)), labels);
    assert.equal(await formWorld({}).askPostedDaysAgo(null), undefined, 'cancelling is not «no date»');
    assert.equal(await formWorld({ [DATE]: 'n' }).askPostedDaysAgo(null), undefined, 'cancelling the number cancels');
    passed++;
}
{   // the number is checked before the dialog closes, against the server's reach
    const w = formWorld({ [DATE]: 'n', [HOW_MANY]: '9' });
    await w.askPostedDaysAgo(null);
    const validate = w.asked[1].field.validate;
    for (const bad of ['', 'abc', '0', '1', String(maxDaysAgo + 1)]) assert.ok(validate(bad), `${bad} was accepted`);
    for (const good of ['2', '10', String(maxDaysAgo), '۲۰']) assert.equal(validate(good), '', `${good} was refused`);
    passed++;
}
{   // editing starts from what is set
    const w = formWorld({ [DATE]: 'n', [HOW_MANY]: '5' });
    await w.askPostedDaysAgo(5);
    assert.equal(w.asked[0].field.value, 'n');
    assert.equal(w.asked[1].field.value, '5');
    const today = formWorld({ [DATE]: '0' }); await today.askPostedDaysAgo(0);
    assert.equal(today.asked[0].field.value, '0');
    const none = formWorld({ [DATE]: 'none' }); await none.askPostedDaysAgo(null);
    assert.equal(none.asked[0].field.value, 'none');
    passed++;
}
{   // saving: the day is sent relative, and an empty count is the whole day
    const w = formWorld({ [DATE]: '1', [WHEN]: '08:30', [NAME]: 'دیروزها' }, { pages: '' });
    await w.saveAsSchedule();
    assert.equal(w.calls.length, 1);
    const sent = w.calls[0];
    assert.equal(sent.url, '/scraper/schedules');
    assert.equal(sent.body.config.posted_days_ago, 1);
    assert.ok(!('posted_date' in sent.body.config), 'a fixed date would repeat itself');
    assert.ok(!('max_items' in sent.body.config), 'the default 50 would cut a busy day short');
    assert.deepEqual([sent.body.hour, sent.body.minute, sent.body.name], [8, 30, 'دیروزها']);
    const note = w.asked.find(a => a.title === WHEN).note;
    assert.ok(note.includes('دیروز'), note);
    assert.ok(w.toasts[0].msg.includes('دیروز'), w.toasts[0].msg);
    passed++;
}
{   // a cap typed in the form stays; no date leaves the form as it was
    const capped = formWorld({ [DATE]: '2', [WHEN]: '08:00', [NAME]: 'x' }, { pages: '40', formCfg: { max_items: 40 } });
    await capped.saveAsSchedule();
    assert.equal(capped.calls[0].body.config.max_items, 40);
    assert.equal(capped.calls[0].body.config.posted_days_ago, 2);

    const plain = formWorld({ [DATE]: 'none', [WHEN]: '08:00', [NAME]: 'x' }, { pages: '' });
    await plain.saveAsSchedule();
    const cfg = plain.calls[0].body.config;
    assert.ok(!('posted_days_ago' in cfg) && cfg.max_items === 50, JSON.stringify(cfg));
    assert.ok(plain.asked.find(a => a.title === WHEN).note.includes('۲۴ ساعت'), 'no date: says it is the last day');
    passed++;
}
{   // cancelling any of the questions saves nothing
    for (const answers of [{}, { [DATE]: '1' }, { [DATE]: '1', [WHEN]: '08:00' }, { [DATE]: 'n' }]) {
        const w = formWorld(answers);
        await w.saveAsSchedule();
        assert.equal(w.calls.length, 0, JSON.stringify(answers));
    }
    passed++;
}
{   // changing the date on a saved schedule; «none» is sent as an explicit null
    const w = formWorld({ [DATE]: '1' });
    await w.editScheduleDate(9, null);
    assert.deepEqual([w.calls[0].url, w.calls[0].method, w.calls[0].body], ['/scraper/schedules/9', 'PATCH', { posted_days_ago: 1 }]);
    const off = formWorld({ [DATE]: 'none' });
    await off.editScheduleDate(9, 3);
    assert.deepEqual(off.calls[0].body, { posted_days_ago: null });
    const cancelled = formWorld({});
    await cancelled.editScheduleDate(9, 3);
    assert.equal(cancelled.calls.length, 0, 'cancelling changed the schedule');
    passed++;
}

console.log(`${passed} checks passed`);

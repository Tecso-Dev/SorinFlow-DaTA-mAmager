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

console.log(`${passed} checks passed`);

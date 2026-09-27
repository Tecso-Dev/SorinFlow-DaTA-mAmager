// Behavioural check for «تماس‌های امروز من»: the list says how much of the
// queue it shows and can show more, and a reload keeps what was expanded.
// Runs the REAL loadCalls / loadMoreCalls from frontend/js/app.js.
import { readFileSync } from 'node:fs';
import { strict as assert } from 'node:assert';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = readFileSync(path.join(here, '..', '..', 'frontend', 'js', 'app.js'), 'utf8');

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

const el = { 'calls-list': { innerHTML: '' }, 'calls-count': { textContent: '' }, 'calls-stats': { textContent: '' } };
const asked = [];
const TOTAL = 445;
const api = async (url) => {
    asked.push(url);
    const n = Math.min(Number(new URL('http://x' + url).searchParams.get('limit')), TOTAL);
    return { total: TOTAL, done_today: 0, due_callbacks: 0, items: Array.from({ length: n }, (_, i) => ({ id: i + 1 })) };
};
const { loadCalls, loadMoreCalls } = new Function('document', 'apiCall', '_currentUser', `
    let _cqItems = [];
    let _cqWant = 40;
    ${extractFunction('esc')}
    ${extractFunction('formatNumber')}
    function _cqCard(l) { return '<div class="cq">' + l.id + '</div>'; }
    ${extractFunction('loadMoreCalls')}
    ${extractFunction('loadCalls')}
    return { loadCalls, loadMoreCalls };`)({ getElementById: (id) => el[id] || null }, api, { role: 'admin' });

let passed = 0;
await loadCalls();
assert.equal(asked.at(-1), '/crm/calls/today?limit=40');
assert.ok(el['calls-list'].innerHTML.includes('نمایش ۴۰ از ۴۴۵'), el['calls-list'].innerHTML.slice(-200));
assert.ok(el['calls-list'].innerHTML.includes('loadMoreCalls()'));
passed++;

loadMoreCalls();
await new Promise((r) => setTimeout(r, 0));
assert.equal(asked.at(-1), '/crm/calls/today?limit=80');
assert.ok(el['calls-list'].innerHTML.includes('نمایش ۸۰ از ۴۴۵'));
passed++;

await loadCalls();            // what a recorded call does: the expansion is kept
assert.equal(asked.at(-1), '/crm/calls/today?limit=80');
passed++;

console.log(`${passed} checks passed`);

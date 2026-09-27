// Behavioural check for the scrape jobs table, run against the REAL
// _renderJobsTable in frontend/js/app.js with the DOM stubbed.
// Run with TZ=Europe/Berlin, the way Sobhan's laptop is: a run started at
// 08:00 Tehran must read 08:00, and a run for 200 says how near it is to 200.
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

function render(job) {
    const rows = [];
    const tbody = { innerHTML: '', appendChild: (r) => rows.push(r) };
    const document = {
        getElementById: (id) => (id === 'jobs-table' ? tbody : null),
        createElement: () => ({ innerHTML: '' }),
    };
    new Function('document', '_currentUser', `
        ${extractFunction('esc')}
        ${extractFunction('formatNumber')}
        ${src.match(/^const _digits = .*$/m)[0]}
        ${extractFunction('_renderJobsTable')}
        return _renderJobsTable;`)(document, { id: 1 })([job]);
    return rows.map(r => r.innerHTML).join('');
}

const base = {
    job_id: '5e1f', status: 'running', owner_user_id: 1, divar_phone: '09120000000',
    total_items: 4253, scraped_items: 182, new_items: 176, updated_items: 6,
    divar_count: 4253, accounts_used: [], can_resume: false,
};
let passed = 0;

{   // 04:30 UTC is 08:00 in Tehran, 06:30 in Berlin
    const out = render({ ...base, progress: 88, max_items: 200, started_at: '2026-09-27T04:30:00+00:00' });
    assert.ok(out.includes('۸:۰۰:۰۰') || out.includes('۰۸:۰۰:۰۰'), out.slice(out.indexOf('job-when'), out.indexOf('job-when') + 120));
    assert.ok(!out.includes('۶:۳۰:۰۰'), 'the start time is shown on the viewer\'s clock');
    passed++;
}
{   // the bar says what the percent is measured against
    const out = render({ ...base, progress: 88, max_items: 200, started_at: null });
    assert.ok(out.includes('title="176 از 200 آگهی تازهٔ درخواستی"'), 'no target on the bar');
    assert.ok(out.includes('width:88%'), 'the bar does not follow progress');
    passed++;
}
{   // a whole-day run has no target, so no such tooltip
    const out = render({ ...base, progress: 25, max_items: null, started_at: null });
    assert.ok(!out.includes('آگهی تازهٔ درخواستی'), 'a whole-day run claims a target');
    passed++;
}

console.log(`${passed} checks passed`);

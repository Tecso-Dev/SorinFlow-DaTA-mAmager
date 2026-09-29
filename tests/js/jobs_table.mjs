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

// #29 — job 44 read «251 / 251»: «کل» was Divar's count. Now the pair is this
// run's own (examined / its pool) and Divar's number sits under it.
const job44 = {
    ...base, status: 'completed', progress: 100, max_items: null, started_at: null,
    total_items: 24, scraped_items: 24, new_items: 8, updated_items: 0, divar_count: 251,
    finish_reason: 'آن روز بیش از این آگهی نداشت', reason_line: '16 تاریخ انتشار',
};
{   // the pair is the run's own, and Divar's number is labelled as Divar's
    const out = render(job44);
    assert.ok(out.includes('<bdi title="بررسی‌شده">24</bdi>') && out.includes('<bdi title="کل">24</bdi>'),
        'the pair is not examined / pool');
    assert.ok(out.includes('دیوار می‌گوید: 251'), 'Divar\'s own number is not shown beside the pool');
    assert.ok(!out.includes('<bdi title="کل">251</bdi>'), '«کل» is Divar\'s count again');
    const noCount = render({ ...job44, divar_count: null });
    assert.ok(!noCount.includes('دیوار می‌گوید'), 'an explicit list has no Divar count to show');
    const zero = render({ ...job44, divar_count: 0, total_items: 0, scraped_items: 0 });
    assert.ok(zero.includes('دیوار می‌گوید: 0'), 'Divar answering «none» is an answer too');
    passed++;
}
{   // a finished run's bar is full, and says so in its percent
    const out = render(job44);
    assert.ok(out.includes('width:100%') && out.includes('>100%<'), 'the finished bar is not full');
    passed++;
}
{   // under «تازه», the line that says where the rest went — escaped
    const out = render(job44);
    const cell = out.slice(out.indexOf('title="تازه'), out.indexOf('job-when'));
    assert.ok(cell.includes('16 تاریخ انتشار'), 'the reason line is not under «تازه»');
    const hostile = render({ ...job44, reason_line: '<img src=x onerror=alert(1)>' });
    assert.ok(!hostile.includes('<img src=x'), 'the reason line is not escaped');
    assert.ok(!render({ ...job44, reason_line: null }).includes('job-new-why'), 'an empty reason line leaves a box');
    passed++;
}
{   // the finish reason reaches the status cell, escaped
    const out = render({ ...job44, finish_reason: '<b>x</b> آن روز' });
    assert.ok(out.includes('&lt;b&gt;x&lt;/b&gt; آن روز') && !out.includes('<b>x</b>'));
    passed++;
}
{   // a run whose owner was deleted says so; a run nobody started keeps «—»
    const gone = render({ ...job44, owner_user_id: 7, owner_name: null, owner_deleted: true });
    assert.ok(gone.includes('کاربر حذف‌شده'), 'a deleted owner still reads «—»');
    const nobody = render({ ...job44, owner_user_id: null, owner_name: null, owner_deleted: false });
    assert.ok(!nobody.includes('کاربر حذف‌شده'), 'a run nobody started claims a deleted owner');
    const named = render({ ...job44, owner_name: 'کاربر آزمایشی', owner_deleted: false });
    assert.ok(named.includes('کاربر آزمایشی'));
    passed++;
}
{   // «ناقص»: the partial status has its label, its badge, delete and resume
    const out = render({ ...job44, status: 'partial', can_resume: true });
    assert.ok(out.includes('status-partial') && out.includes('>ناقص<'), 'partial has no label');
    assert.ok(out.includes("deleteJob('5e1f')"), 'a partial run cannot be removed from the list');
    assert.ok(out.includes("resumeJob('5e1f')"), 'a partial run cannot be continued');
    assert.ok(!out.includes("cancelJob('5e1f')"), 'a finished run offers cancel');
    passed++;
}

console.log(`${passed} checks passed`);

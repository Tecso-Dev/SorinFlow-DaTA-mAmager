// Behavioural check for «آگهی‌های اسکرپ‌نشده» (#58), run against the REAL
// functions in frontend/js/app.js with the DOM, Bootstrap, the timers and the
// API stubbed.
//
// While the window is open it reads the run's list again every minute; closed,
// it stops — and opening it again, for this run or another, never leaves a
// second timer behind. «تلاش دوباره» — one listing, one bucket, or all —
// asks the run itself (POST /scraper/jobs/{id}/retry), never /rescrape or
// the single-scrape box, which opened a new row in the jobs table.
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

function globalLine(re) {
    const m = src.match(re);
    if (!m) throw new Error(`${re} not found in app.js`);
    return m[0];
}

const FNS = ['esc', 'jsArg', 'showSkipped', 'loadSkipped', '_stopSkippedRefresh',
             'renderSkippedSummary', 'filterSkipped', 'visibleSkipped', 'rescrapeCandidates',
             'renderSkippedRows', 'retrySkippedListing', 'retryAllSkipped', '_retryInPlace'];

function world(answers) {
    const listeners = {};
    const el = {
        'skipped-body': { innerHTML: '' },
        'skipped-summary': { innerHTML: '' },
        'skippedModal': {
            addEventListener(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); },
        },
    };
    const calls = [], toasts = [], timers = new Map();
    const hooks = { askConfirm: async () => true };
    let nextTimer = 1, jobsLoaded = 0;
    const api = {
        calls,
        async apiCall(url, opts) {
            calls.push({ url, method: (opts && opts.method) || 'GET', body: opts && opts.body });
            const a = typeof answers === 'function' ? answers(url, opts, calls) : answers;
            if (a instanceof Error) throw a;
            return JSON.parse(JSON.stringify(a));
        },
    };
    const env = {
        document: { getElementById: (id) => el[id] || null },
        bootstrap: {
            Modal: class {
                constructor(e) { this.e = e; }
                show() {}
                hide() { (listeners['hidden.bs.modal'] || []).forEach(fn => fn()); }
                static getInstance(e) { return new this(e); }
                static getOrCreateInstance(e) { return new this(e); }
            },
        },
        setInterval(fn, ms) { const id = nextTimer++; timers.set(id, { fn, ms }); return id; },
        clearInterval(id) { timers.delete(id); },
        apiCall: api.apiCall,
        askConfirm: (...a) => hooks.askConfirm(...a),
        showToast: (...a) => toasts.push(a),
        loadJobs: () => { jobsLoaded++; },
        scrapeSingle: () => { throw new Error('the single-scrape box opens a new row'); },
    };
    const names = Object.keys(env);
    const lets = [globalLine(/^let _skippedRows = .*$/m), globalLine(/^let _skippedFilter = .*$/m),
                  globalLine(/^let _skippedJobId = .*$/m), globalLine(/^let _skippedTimer = .*$/m),
                  globalLine(/^const SKIPPED_REFRESH_MS = .*$/m)].join('\n');
    const fns = new Function(...names, `
        ${lets}
        ${FNS.map(extractFunction).join('\n')}
        return { ${FNS.join(', ')} };`)(...names.map(n => env[n]));
    return { el, listeners, calls, toasts, timers, fns, hooks, jobs: () => jobsLoaded };
}

const LIST = {
    job_id: 'j-1', count: 3,
    by_reason: { no_phone: { label: 'بدون شماره', count: 2 }, category: { label: 'خارج از دسته‌بندی', count: 1 } },
    items: [
        { divar_id: 'aaa1', url: 'https://divar.ir/v/aaa1', title: 'یک', reason: 'no_phone', reason_label: 'بدون شماره' },
        { divar_id: 'bbb2', url: 'https://divar.ir/v/bbb2', title: '<img src=x onerror=alert(1)>', reason: 'no_phone', reason_label: 'بدون شماره' },
        { divar_id: 'ccc3', url: 'https://divar.ir/v/ccc3', title: 'سه', reason: 'category', reason_label: 'خارج از دسته‌بندی' },
    ],
};

let passed = 0;

{   // open: it reads the list, and a one-minute refresh starts
    const w = world(LIST);
    await w.fns.showSkipped('j-1');
    assert.equal(w.calls.length, 1);
    assert.equal(w.calls[0].url, '/scraper/jobs/j-1/skipped');
    assert.equal(w.timers.size, 1, 'no refresh timer');
    const [t] = w.timers.values();
    assert.equal(t.ms, 60000, `refreshes every ${t.ms} ms, not every minute`);
    assert.ok(w.el['skipped-body'].innerHTML.includes('سه'));
    assert.ok(!w.el['skipped-body'].innerHTML.includes('<img'), 'a title was not escaped');
    passed++;
}
{   // a tick reads it again and shows what changed, without a «loading» flash
    let n = 0;
    const w = world(() => (++n === 1 ? LIST : { ...LIST, count: 1, by_reason: { category: { label: 'خارج از دسته‌بندی', count: 1 } },
                                                items: [LIST.items[2]] }));
    await w.fns.showSkipped('j-1');
    const [t] = w.timers.values();
    await t.fn();
    assert.equal(w.calls.length, 2);
    assert.equal(w.calls[1].url, '/scraper/jobs/j-1/skipped');
    const body = w.el['skipped-body'].innerHTML;
    assert.ok(body.includes('سه') && !body.includes('یک'), body);
    assert.ok(w.el['skipped-summary'].innerHTML.includes('(1)'), 'the retry button count did not follow the list');
    passed++;
}
{   // a refresh that fails keeps what is on screen
    let n = 0;
    const w = world(() => (++n === 1 ? LIST : new Error('شبکه قطع شد')));
    await w.fns.showSkipped('j-1');
    const before = w.el['skipped-body'].innerHTML;
    await [...w.timers.values()][0].fn();
    assert.equal(w.el['skipped-body'].innerHTML, before);
    passed++;
}
{   // closed: the refresh stops
    const w = world(LIST);
    await w.fns.showSkipped('j-1');
    (w.listeners['hidden.bs.modal'] || []).forEach(fn => fn());
    assert.equal(w.timers.size, 0, 'the refresh outlived the window');
    passed++;
}
{   // opened again — for another run — leaves one timer, and one close handler
    const w = world(LIST);
    await w.fns.showSkipped('j-1');
    await w.fns.showSkipped('j-2');
    await w.fns.showSkipped('j-2');
    assert.equal(w.timers.size, 1, `${w.timers.size} timers after three opens`);
    assert.equal((w.listeners['hidden.bs.modal'] || []).length, 1, 'a close handler per open');
    await [...w.timers.values()][0].fn();
    assert.equal(w.calls.at(-1).url, '/scraper/jobs/j-2/skipped', 'the timer reads the old run');
    passed++;
}
{   // an answer for a run no longer on screen is not drawn over the one that is
    let release;
    const slow = new Promise(r => { release = r; });
    const w = world(async (url) => (url.includes('j-1') ? (await slow, LIST) : { ...LIST, items: [LIST.items[2]] }));
    const first = w.fns.showSkipped('j-1');
    await w.fns.showSkipped('j-2');
    release();
    await first;
    assert.ok(!w.el['skipped-body'].innerHTML.includes('یک'), 'j-1 was drawn over j-2');
    passed++;
}
{   // one listing: retried in THIS run, by id
    const w = world((url) => (url.endsWith('/retry') ? { job_id: 'j-1', status: 'pending', count: 1 } : LIST));
    await w.fns.showSkipped('j-1');
    assert.ok(w.el['skipped-body'].innerHTML.includes('retrySkippedListing(&quot;aaa1&quot;)'),
              'the row button does not retry in this run');
    await w.fns.retrySkippedListing('aaa1');
    const post = w.calls.find(c => c.method === 'POST');
    assert.equal(post.url, '/scraper/jobs/j-1/retry');
    assert.deepEqual(JSON.parse(post.body), { divar_ids: ['aaa1'] });
    assert.ok(!w.calls.some(c => c.url.includes('/rescrape') || c.url.includes('scrape-single')));
    assert.equal(w.jobs(), 1, 'the jobs table was not refreshed');
    passed++;
}
{   // a bucket: exactly that bucket, in this run
    const w = world((url) => (url.endsWith('/retry') ? { job_id: 'j-1', status: 'pending', count: 2 } : LIST));
    await w.fns.showSkipped('j-1');
    w.fns.filterSkipped('no_phone');
    assert.ok(w.el['skipped-summary'].innerHTML.includes('تلاش دوباره') &&
              w.el['skipped-summary'].innerHTML.includes('(2)'), w.el['skipped-summary'].innerHTML);
    await w.fns.retryAllSkipped();
    const post = w.calls.find(c => c.method === 'POST');
    assert.equal(post.url, '/scraper/jobs/j-1/retry');
    assert.deepEqual(JSON.parse(post.body), { reason: 'no_phone' });
    assert.ok(w.toasts.some(t => String(t[1]).includes('همین اسکرپ')), 'the toast does not say where');
    passed++;
}
{   // everything: no bucket, no ids
    const w = world((url) => (url.endsWith('/retry') ? { job_id: 'j-1', status: 'pending', count: 3 } : LIST));
    await w.fns.showSkipped('j-1');
    await w.fns.retryAllSkipped();
    assert.deepEqual(JSON.parse(w.calls.find(c => c.method === 'POST').body), {});
    passed++;
}
{   // the bucket is the one on screen when «تلاش دوباره» was pressed: a refresh
    // that lands while the confirmation is open and drops the bucket (all of
    // it was retried elsewhere) must not turn the request into «everything»
    let n = 0;
    const w = world((url) => (url.endsWith('/retry') ? { job_id: 'j-1', status: 'pending', count: 2 }
        : (++n === 1 ? LIST : { ...LIST, items: [LIST.items[2]],
                                by_reason: { category: { label: 'خارج از دسته‌بندی', count: 1 } } })));
    await w.fns.showSkipped('j-1');
    w.fns.filterSkipped('no_phone');
    w.hooks.askConfirm = async () => { await [...w.timers.values()][0].fn(); return true; };
    await w.fns.retryAllSkipped();
    const post = w.calls.find(c => c.method === 'POST');
    assert.deepEqual(JSON.parse(post.body), { reason: 'no_phone' }, 'a one-bucket retry was sent as «everything»');
    passed++;
}
{   // rows a retry cannot change (chat-only, gone from Divar) are not in «all»,
    // but each keeps its own button
    const rows = { ...LIST, items: [...LIST.items,
        { divar_id: 'ddd4', url: 'https://divar.ir/v/ddd4', title: 'چهار', reason: 'chat_only',
          reason_label: 'فقط چت دیوار', retryable_in_bulk: false }] };
    const w = world(rows);
    await w.fns.showSkipped('j-1');
    assert.ok(w.el['skipped-summary'].innerHTML.includes('تلاش دوباره (3)'), w.el['skipped-summary'].innerHTML);
    assert.ok(w.el['skipped-body'].innerHTML.includes('retrySkippedListing(&quot;ddd4&quot;)'),
              'its own button is gone');
    passed++;
}
{   // a refusal (the run is still going) is said, and nothing else happens
    const w = world((url) => (url.endsWith('/retry') ? new Error('این اسکرپ هنوز در حال اجراست') : LIST));
    await w.fns.showSkipped('j-1');
    await w.fns.retryAllSkipped();
    assert.ok(w.toasts.some(t => t[2] === 'danger' && String(t[1]).includes('در حال اجراست')), JSON.stringify(w.toasts));
    assert.equal(w.jobs(), 0);
    passed++;
}

console.log(`${passed} checks passed`);

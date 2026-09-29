// Behavioural check for what the AI card shows while the gateway has no credit
// (#35), run against the REAL functions in frontend/js/app.js with the DOM and
// the API stubbed. The server sends `pause` = {since, until, status, message,
// agent} or null; the page must show the gateway's own words, until when the
// agents wait (Tehran time, whatever the viewer's clock says), and take the
// notice away the moment the pause is gone. Run with TZ=Europe/Berlin, the way
// Sobhan's laptop is.
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

function extractConst(name) {
    const m = src.match(new RegExp(`const ${name} = [\\s\\S]*?;\\n`));
    if (!m) throw new Error(`const ${name} not found in app.js`);
    return m[0];
}

const PAUSE = { since: '2026-09-28T12:00:00+00:00', until: '2026-09-28T20:30:00+00:00', status: 402,
                message: 'Workspace has insufficient credit', agent: 'vision', job: 'vision' };

function world(answers = {}) {
    const els = new Map();
    const make = (id) => {
        const e = {
            id, textContent: '', innerHTML: '', className: '', value: '', checked: false, placeholder: '', disabled: false,
            dataset: {}, style: {}, attrs: {}, children: [], options: { length: 2 },
            setAttribute(k, v) { this.attrs[k] = v; },
            remove() { els.delete(this.id); },
            prepend(child) { this.children.unshift(child); els.set(child.id, child); },
        };
        els.set(id, e);
        return e;
    };
    for (const id of ['section-ai', 'ai-badge', 'ai-conn', 'ai-today', 'ai-month', 'ai-agents', 'ai-cap', 'ai-notes', 'ai-enabled',
                      'ai-result', 'ai-test', 'ai-agents-grid', 'ai-t-conn', 'ai-t-conn-sub', 'ai-t-today', 'ai-t-cap',
                      'ai-t-month', 'ai-t-month-sub', 'ai-t-quota', 'ai-t-quota-sub', 'ai-log-agent',
                      ...['write', 'read', 'vision', 'embed'].flatMap(j => [`ai-model-${j}`, `ai-model-${j}-hint`])]) make(id);
    const document = {
        getElementById: (id) => els.get(id) || null,
        createElement: () => { const e = make(''); els.delete(''); return e; },
    };
    const asked = [];
    const apiCall = async (url, opts) => {
        asked.push(url);
        const a = answers[url];
        if (a instanceof Error) throw a;
        return typeof a === 'function' ? a() : a;
    };
    const toasts = [];
    const showToast = (...a) => toasts.push(a);
    const scope = new Function('document', 'apiCall', 'showToast', '_aiAssistantStatus', `
        ${extractFunction('esc')}
        ${extractFunction('raw')}
        ${extractFunction('html')}
        ${extractFunction('jsArg')}
        ${extractFunction('formatNumber')}
        ${extractConst('AI_JOBS')}
        ${extractConst('AI_AGENT_KIND_FA')}
        ${extractConst('AI_JOB_FA')}
        ${extractFunction('_aiMoney')}
        ${extractFunction('_aiUntil')}
        ${extractFunction('_aiBreakerText')}
        ${extractFunction('_aiPauseUntil')}
        ${extractFunction('_aiErrText')}
        ${extractFunction('_aiRenderPause')}
        ${extractFunction('_aiTokens')}
        ${extractFunction('_aiAgentState')}
        ${extractFunction('_aiAgentCard')}
        ${extractFunction('loadAi')}
        ${extractFunction('loadAiScreen')}
        ${extractFunction('loadAiLog')}
        ${extractFunction('aiTest')}
        return { _aiPauseUntil, _aiErrText, _aiRenderPause, _aiAgentCard, loadAi, loadAiScreen, loadAiLog, aiTest };`)(document, apiCall, showToast, () => {});
    return { els, asked, toasts, ...scope };
}

const STATUS = (pause) => ({
    configured: true, enabled: true, workspace: '6aa50e58', pause,
    usage: { today: { cost_usd: 0, cost_toman: 0, calls: 0 }, month: { cost_usd: 0, cost_toman: 0, calls: 0 } },
    cap_usd: 2, cap_reached: false, breaker: { state: 'closed', until: null }, liara: null,
    agents: [], models: {}, model_sources: {}, env_models: {}, notes: '',
});

const AGENT = (over = {}) => ({
    key: 'vision', name: 'برچسب‌زن عکس', job: 'vision', kind: 'loop', desc: 'x', where: [], enabled: true, model: 'm',
    cap_usd: 1, state: { tagged: 3, behind: 2 }, today: { calls: 0, cost_usd: 0, cost_toman: 0, failed: 0 },
    month: { calls: 0, cost_usd: 0, cost_toman: 0, failed: 0 }, ...over,
});

let passed = 0;

{   // the notice: the gateway's own words, the hour in Tehran, the way out
    const w = world();
    w._aiRenderPause(PAUSE);
    const box = w.els.get('ai-pause-banner');
    assert.ok(box, 'no banner was drawn');
    assert.equal(w.els.get('section-ai').children[0], box, 'it is the first thing on the AI page');
    assert.ok(box.innerHTML.includes('Workspace has insufficient credit'), 'the exact message is missing');
    assert.ok(box.innerHTML.includes('۰۰:۰۰'), 'the hour is not Tehran midnight: ' + box.innerHTML);
    assert.ok(box.innerHTML.includes('مهر'), 'the day is not written in the Persian calendar');
    assert.ok(box.innerHTML.includes('تست اتصال'), 'it does not say how to end the wait');
    assert.ok(box.innerHTML.includes('HTTP 402') && box.innerHTML.includes('vision'), 'the status and the agent are not there');
    assert.ok(box.className.includes('alert-warning'), 'not the site\'s own alert style');
    passed++;
}
{   // until is Tehran wall-clock time, not the viewer's
    const w = world();
    assert.ok(w._aiPauseUntil('2026-09-28T20:30:00+00:00').endsWith('۰۰:۰۰'));
    assert.ok(w._aiPauseUntil('2026-09-28T09:15:00+00:00').endsWith('۱۲:۴۵'), '09:15 UTC is 12:45 in Tehran');
    assert.equal(w._aiPauseUntil(''), '');
    assert.equal(w._aiPauseUntil('not a date'), '');
    passed++;
}
{   // whatever the gateway said is text, never markup
    const w = world();
    w._aiRenderPause({ ...PAUSE, message: '<img src=x onerror=alert(1)>', agent: '<b>x</b>' });
    const html = w.els.get('ai-pause-banner').innerHTML;
    assert.ok(!html.includes('<img') && !html.includes('<b>x</b>'), html);
    assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
    passed++;
}
{   // one banner however often it is drawn, and none once the pause is gone
    const w = world();
    w._aiRenderPause(PAUSE);
    const first = w.els.get('ai-pause-banner');
    w._aiRenderPause({ ...PAUSE, message: 'newer words' });
    assert.equal(w.els.get('ai-pause-banner'), first, 'a second banner was made');
    assert.equal(w.els.get('section-ai').children.length, 1);
    assert.ok(first.innerHTML.includes('newer words'));
    w._aiRenderPause(null);
    assert.equal(w.els.get('ai-pause-banner'), undefined, 'the notice outlived the pause');
    w._aiRenderPause(null);   // nothing to remove is not an error
    passed++;
}
{   // the settings card: the badge says it, the banner shows it
    const w = world({ '/ai/status': () => STATUS(PAUSE) });
    await w.loadAi();
    const badge = w.els.get('ai-badge');
    assert.equal(badge.textContent, 'متوقف — اعتبار تمام شده');
    assert.ok(badge.className.includes('bg-warning'));
    assert.ok(w.els.get('ai-pause-banner').innerHTML.includes('Workspace has insufficient credit'));
    passed++;
}
{   // and when there is no pause: as before, and no banner
    const w = world({ '/ai/status': () => STATUS(null) });
    await w.loadAi();
    assert.equal(w.els.get('ai-badge').textContent, 'فعال');
    assert.equal(w.els.get('ai-pause-banner'), undefined);
    passed++;
}
{   // a pause that ends is taken off the page by the next load
    let pause = PAUSE;
    const w = world({ '/ai/status': () => STATUS(pause) });
    await w.loadAi();
    assert.ok(w.els.get('ai-pause-banner'));
    pause = null;
    await w.loadAi();
    assert.equal(w.els.get('ai-pause-banner'), undefined);
    assert.equal(w.els.get('ai-badge').textContent, 'فعال');
    passed++;
}
{   // the AI screen: the tile, and every enabled agent says it is waiting
    const w = world({ '/ai/overview': () => ({ ...STATUS(PAUSE), quota: null,
        agents: [AGENT(), AGENT({ key: 'reader', name: 'خوانندهٔ آگهی', enabled: false })] }) });
    await w.loadAiScreen();
    assert.equal(w.els.get('ai-t-conn').textContent, 'متوقف — اعتبار تمام شده');
    assert.ok(w.els.get('ai-t-conn-sub').textContent.startsWith('تا ') && w.els.get('ai-t-conn-sub').textContent.endsWith('۰۰:۰۰'));
    const grid = w.els.get('ai-agents-grid').innerHTML;
    assert.equal((grid.match(/متوقف تا/g) || []).length, 1, 'only the agent that is switched on is waiting');
    assert.ok(w.els.get('ai-pause-banner'), 'the screen draws the banner too');
    passed++;
}
{   // an agent card drawn the old way (no pause) is unchanged
    const w = world();
    const card = w._aiAgentCard(AGENT());
    assert.ok(!card.includes('متوقف تا'));
    assert.ok(card.includes('برچسب‌خورده'));
    passed++;
}
{   // the test button: an answer that lifts the pause says so and clears the notice
    let pause = PAUSE;
    const w = world({ '/ai/test': () => { pause = null; return { model: 'm', ms: 120, reply: 'سلام', pause_cleared: true }; },
                      '/ai/status': () => STATUS(pause), '/ai/overview': () => ({ ...STATUS(pause), quota: null, agents: [] }) });
    w._aiRenderPause(PAUSE);
    await w.aiTest();
    assert.ok(w.els.get('ai-result').innerHTML.includes('دوباره ادامه می‌دهند'));
    assert.equal(w.els.get('ai-pause-banner'), undefined);
    assert.ok(w.asked.includes('/ai/status') && w.asked.includes('/ai/overview'));
    passed++;
}
{   // a test refused for credit shows the gateway's words on the button and the notice above
    const refused = Object.assign(new Error('اعتبار … — پیام سرویس: «Workspace has insufficient credit»'), { status: 502 });
    const w = world({ '/ai/test': refused, '/ai/status': () => STATUS(PAUSE), '/ai/overview': () => ({ ...STATUS(PAUSE), quota: null, agents: [] }) });
    await w.aiTest();
    assert.ok(w.els.get('ai-result').textContent.includes('Workspace has insufficient credit'));
    assert.ok(w.els.get('ai-result').className.includes('text-danger'));
    assert.ok(w.els.get('ai-pause-banner'), 'the refusal set the pause: the page must show it without a reload');
    assert.equal(w.els.get('ai-test').disabled, false);
    passed++;
}

{   // a ledger error reads as the gateway's sentence, not as a JSON dump
    const w = world();
    const t = w._aiErrText;
    assert.equal(t('HTTP 400: {"error":{"message":"unsupported image format","type":"x"}}'), 'HTTP 400 — unsupported image format');
    assert.equal(t('HTTP 402: {"error":{"message":"Workspace has insufficient cre'), 'HTTP 402 — Workspace has insufficient cre',
                 'the column cuts at 300 characters: a cut-off body still reads');
    assert.equal(t('HTTP 403: {"detail":"Your balance is not enough"}'), 'HTTP 403 — Your balance is not enough');
    assert.equal(t('HTTP 401: {"error":"Incorrect API key"}'), 'HTTP 401 — Incorrect API key');
    assert.equal(t('HTTP 400: {"error":{"message":"say \\"hi\\""}}'), 'HTTP 400 — say "hi"');
    assert.equal(t('HTTP 502: <html>Bad Gateway</html>'), 'HTTP 502 — <html>Bad Gateway</html>', 'not JSON: as it is');
    assert.equal(t('ReadTimeout: '), 'ReadTimeout:');
    assert.equal(t(null), '');
    passed++;
}
{   // the log table shows that sentence, and keeps the raw text for the tooltip
    const raw = 'HTTP 402: {"error":{"message":"Workspace has insufficient credit","type":"insufficient_credit"}}';
    const w = world({ '/ai/log?limit=60': { items: [{ created_at: '2026-09-28T12:00:00', agent: 'vision', job: 'vision', model: 'm',
        prompt_tokens: 0, completion_tokens: 0, cost_toman: 0, ms: 5, ok: false, error: raw }] } });
    w.els.set('ai-log-rows', { innerHTML: '' });
    w.els.set('ai-log-note', { textContent: '' });
    await w.loadAiLog();
    const row = w.els.get('ai-log-rows').innerHTML;
    assert.ok(row.includes('✗ HTTP 402 — Workspace has insufficient credit'), row);
    assert.ok(row.includes('title="HTTP 402: {&quot;error&quot;'), 'the full text is still on hover');
    passed++;
}

console.log(`${passed} checks passed`);

// Behavioural check for the Divar login form picking up a code the phone's
// forwarder already delivered (/scraper/login-code), run against the REAL
// _watchForwardedLoginCode in frontend/js/app.js — same extraction as
// panel_escaping.mjs, with the DOM, the API and the timer stubbed.
import { readFileSync } from 'node:fs';
import { strict as assert } from 'node:assert';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = readFileSync(path.join(here, '..', '..', 'frontend', 'js', 'app.js'), 'utf8');

function extractFunction(name) {
    const start = src.indexOf(`function ${name}(`);
    if (start === -1) throw new Error(`${name}() not found in app.js — did it get renamed?`);
    let end = src.indexOf('{', start), depth = 0;
    for (; end < src.length; end++) {
        if (src[end] === '{') depth++;
        else if (src[end] === '}') { depth--; if (depth === 0) { end++; break; } }
    }
    return src.slice(start, end);
}

assert.ok(/_watchForwardedLoginCode\(phone\)/.test(extractFunction('initiateLogin')),
    'initiateLogin must start watching for the forwarded code');

function world({ answers, typed = '' }) {
    const boxes = Array.from({ length: 6 }, (_, i) => ({
        value: i === 0 ? typed : '', classList: { toggle() {} } }));
    const form = { style: { display: 'block' } };
    const w = { calls: [], verified: 0, toasts: 0, tick: null, cleared: false };
    const document = {
        querySelectorAll: (s) => (s === '.otp-box' ? boxes : []),
        getElementById: (id) => (id === 'auth-verify-form' ? form : null),
    };
    const apiCall = async (url) => {
        w.calls.push(url);
        const a = answers.shift();
        if (a instanceof Error) throw a;
        return a;
    };
    const make = new Function('document', 'apiCall', 'showToast', 'verifyCode',
        'setInterval', 'clearInterval', `
        let loginPhoneNumber = '09058432452';
        let _loginCodeWatch = null;
        ${extractFunction('_getOtpCode')}
        ${extractFunction('_watchForwardedLoginCode')}
        return _watchForwardedLoginCode;`);
    const watch = make(document, apiCall, () => { w.toasts++; }, () => { w.verified++; },
        (fn) => { w.tick = fn; return 7; }, () => { w.cleared = true; });
    watch('09058432452');
    return { w, boxes };
}

let passed = 0;

{   // nothing yet, then the code: filled in and submitted, once
    const { w, boxes } = world({ answers: [{ code: null }, { code: '523969' }] });
    await w.tick();
    assert.equal(w.verified, 0);
    await w.tick();
    assert.equal(boxes.map((b) => b.value).join(''), '523969');
    assert.equal(w.verified, 1);
    assert.ok(w.cleared, 'polling stops once the code is in');
    assert.equal(w.calls[0], '/scraper/login-code/09058432452');
    passed++;
}
{   // the person is already typing: leave it to them, do not even ask
    const { w } = world({ answers: [{ code: '523969' }], typed: '5' });
    await w.tick();
    assert.equal(w.calls.length, 0);
    assert.equal(w.verified, 0);
    assert.ok(w.cleared);
    passed++;
}
{   // not allowed to read it: stop quietly, typing by hand still works
    const e = Object.assign(new Error('forbidden'), { status: 403 });
    const { w } = world({ answers: [e] });
    await w.tick();
    assert.ok(w.cleared);
    assert.equal(w.verified, 0);
    passed++;
}
{   // anything that is not six digits is never typed into Divar
    const { w, boxes } = world({ answers: [{ code: '12ab56' }] });
    await w.tick();
    assert.equal(w.verified, 0);
    assert.equal(boxes.map((b) => b.value).join(''), '');
    passed++;
}

console.log(`${passed} checks passed`);

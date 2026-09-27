// Behavioural check for the portal's delete confirmation and error messages,
// run against the REAL deleteRequest / submitTicket / askConfirm / showError
// in frontend/js/portal.js with a small DOM stand-in. The browser's
// confirm() and alert() are forbidden on the site; these must not call them.
import { readFileSync } from 'node:fs';
import { strict as assert } from 'node:assert';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = readFileSync(path.join(here, '..', '..', 'frontend', 'js', 'portal.js'), 'utf8');

function extractFunction(name) {
    let start = src.indexOf(`function ${name}(`);
    if (start === -1) throw new Error(`${name}() not found in portal.js`);
    if (src.slice(start - 6, start) === 'async ') start -= 6;
    let end = src.indexOf('{', start), depth = 0;
    for (; end < src.length; end++) {
        if (src[end] === '{') depth++;
        else if (src[end] === '}') { depth--; if (depth === 0) { end++; break; } }
    }
    return src.slice(start, end);
}

// ── a DOM just big enough for these four functions ──────────────────────
class El {
    constructor(tag) {
        this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {};
        this.className = ''; this.textContent = ''; this._html = ''; this.parent = null;
        this.buttons = {};
    }
    set innerHTML(v) {
        this._html = v;
        // the dialog's two buttons, found the way the code looks for them
        for (const key of ['data-ok', 'data-no']) {
            if (v.includes(key)) {
                const b = new El('button');
                b.label = (v.match(new RegExp(`${key}>([^<]*)<`)) || [])[1];
                this.buttons[`[${key}]`] = b;
            }
        }
    }
    get innerHTML() { return this._html; }
    setAttribute(k, v) { this.attrs[k] = v; }
    addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
    fire(t, ev = {}) { for (const fn of this.listeners[t] || []) fn({ target: this, ...ev }); }
    click() { this.fire('click'); }
    focus() { document.focused = this; }
    querySelector(sel) {
        if (this.buttons[sel]) return this.buttons[sel];
        if (sel === ':scope > .msg.err.pf-inline') return this.children.find(c => c.className === 'msg err pf-inline') || null;
        return null;
    }
    prepend(c) { c.parent = this; this.children.unshift(c); }
    appendChild(c) { c.parent = this; this.children.push(c); }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); this.parent = null; }
}

let docListeners = {};
const document = {
    body: new El('body'),
    focused: null,
    createElement: (t) => new El(t),
    addEventListener: (t, fn) => { (docListeners[t] ||= []).push(fn); },
    removeEventListener: (t, fn) => { docListeners[t] = (docListeners[t] || []).filter(f => f !== fn); },
};
const boxes = { 'req-list': new El('div'), 'ticket-box': new El('div'), 'tk-msg': { value: '' },
                'tk-btn': new El('button') };

function load(apiImpl, calls) {
    const forbidden = () => { throw new Error('the browser dialog was used'); };
    return new Function('document', 'api', 'loadRequests', 'loadTicket', 'withSpinner', '$',
                        'confirm', 'alert', 'prompt', `
        ${extractFunction('esc')}
        ${extractFunction('askConfirm')}
        ${extractFunction('showError')}
        ${extractFunction('deleteRequest')}
        ${extractFunction('submitTicket')}
        return { askConfirm, deleteRequest, submitTicket };`)(
        document, apiImpl, async () => { calls.push('reload'); }, () => {}, () => {},
        (id) => boxes[id], forbidden, forbidden, forbidden);
}

const tick = () => new Promise(r => setTimeout(r, 0));
const dialog = () => document.body.children.find(c => c.className === 'pf-ask');
let passed = 0;

{   // «انصراف» — nothing is deleted, the dialog goes away
    const calls = [];
    const f = load(async (p, o) => { calls.push([p, o?.method]); return {}; }, calls);
    const pending = f.deleteRequest(7);
    await tick();
    const d = dialog();
    assert.ok(d, 'no dialog on screen');
    assert.equal(d.attrs.role, 'dialog');
    assert.equal(d.buttons['[data-ok]'].label, 'حذف');
    assert.equal(document.focused, d.buttons['[data-no]'], 'focus should start on the safe button');
    d.buttons['[data-no]'].click();
    await pending;
    assert.deepEqual(calls, [], 'cancelling still deleted');
    assert.equal(dialog(), undefined, 'the dialog stayed');
    passed++;
}
{   // «حذف» — the request is deleted and the list reloaded
    const calls = [];
    const f = load(async (p, o) => { calls.push([p, o?.method]); return {}; }, calls);
    const pending = f.deleteRequest(7);
    await tick();
    dialog().buttons['[data-ok]'].click();
    await pending;
    assert.deepEqual(calls, [['/portal/requests/7', 'DELETE'], 'reload']);
    passed++;
}
{   // Escape cancels, and a failed delete is said in the list, not in alert()
    const calls = [];
    const f = load(async () => { throw new Error('این درخواست دیگر وجود ندارد'); }, calls);
    let pending = f.deleteRequest(8);
    await tick();
    for (const fn of docListeners.keydown || []) fn({ key: 'Escape' });
    assert.equal(await pending, undefined);
    assert.equal(dialog(), undefined);
    pending = f.deleteRequest(8);
    await tick();
    dialog().buttons['[data-ok]'].click();
    await pending;
    const err = boxes['req-list'].children[0];
    assert.ok(err && err.className === 'msg err pf-inline' && err.textContent === 'این درخواست دیگر وجود ندارد');
    assert.equal(err.attrs.role, 'alert');
    passed++;
}
{   // a failed access request is said under the form, once
    const calls = [];
    const f = load(async () => { throw new Error('درخواست قبلی هنوز باز است'); }, calls);
    await f.submitTicket();
    await f.submitTicket();
    const errs = boxes['ticket-box'].children.filter(c => c.className === 'msg err pf-inline');
    assert.equal(errs.length, 1, 'errors piled up');
    assert.equal(errs[0].textContent, 'درخواست قبلی هنوز باز است');
    passed++;
}
{   // the message is text, not markup
    const f = load(async () => ({}), []);
    const pending = f.askConfirm('<img src=x onerror=alert(1)>', 'حذف');
    await tick();
    assert.ok(!dialog().innerHTML.includes('<img'), 'the message was inserted as HTML');
    dialog().buttons['[data-no]'].click();
    await pending;
    passed++;
}

console.log(`${passed} checks passed`);

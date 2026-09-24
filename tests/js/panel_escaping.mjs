// Behavioural check for the panel's HTML-escaping helpers (esc/html/raw) and
// its href/src URL guard (safeUrl/safeManualPhoto), run against the REAL
// source in frontend/js/app.js rather than a copy — a copy would keep
// passing after the real function drifted.
//
// app.js is a classic <script>, not a module (it touches `document` and
// `window` at the top level), so it cannot be `import`ed under Node. This
// pulls just the handful of pure functions under test out of the source
// text by brace-counting and evaluates them in isolation.
import { readFileSync } from 'node:fs';
import { strict as assert } from 'node:assert';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const appJsPath = path.join(here, '..', '..', 'frontend', 'js', 'app.js');
const src = readFileSync(appJsPath, 'utf8');

function extractFunction(name) {
    const marker = `function ${name}(`;
    const start = src.indexOf(marker);
    if (start === -1) throw new Error(`${name}() not found in app.js — did it get renamed?`);
    let i = src.indexOf('{', start);
    let depth = 0, end = i;
    for (; end < src.length; end++) {
        if (src[end] === '{') depth++;
        else if (src[end] === '}') { depth--; if (depth === 0) { end++; break; } }
    }
    return src.slice(start, end);
}

function extractConst(name) {
    const marker = `const ${name} = `;
    const start = src.indexOf(marker);
    if (start === -1) throw new Error(`${name} not found in app.js`);
    const end = src.indexOf(';\n', start);
    return src.slice(start, end + 1);
}

// esc()/raw()/html() and safeUrl()/safeManualPhoto() (which needs esc() and
// the MANUAL_PHOTO_RE const it closes over) are all pulled out together and
// evaluated once, exactly as app.js defines them.
const body = [
    extractFunction('esc'),
    extractFunction('jsArg'),
    extractFunction('raw'),
    extractFunction('html'),
    extractFunction('safeUrl'),
    extractConst('MANUAL_PHOTO_RE'),
    extractFunction('safeManualPhoto'),
    '\nglobalThis.__panel = { esc, jsArg, raw, html, safeUrl, safeManualPhoto };',
].join('\n\n');

new Function(body)();
const { esc, jsArg, raw, html, safeUrl, safeManualPhoto } = globalThis.__panel;

let passed = 0;
function check(label, fn) {
    fn();
    passed++;
    console.log(`ok - ${label}`);
}

// ── esc(): the baseline the rest of app.js still calls directly ──────────

check('esc() escapes the HTML-special set including quotes and backtick', () => {
    assert.equal(esc(`<img src=x onerror=alert(1)>`),
        '&lt;img src=x onerror=alert(1)&gt;');
    assert.equal(esc(`"><svg onload=alert(1)>`),
        '&quot;&gt;&lt;svg onload=alert(1)&gt;');
    assert.equal(esc(`o'brien`), 'o&#39;brien');
    assert.equal(esc('a`b'), 'a&#96;b');
});

check('esc() is a safe no-op on null/undefined', () => {
    assert.equal(esc(null), '');
    assert.equal(esc(undefined), '');
});

// ── html`` / raw(): escape-by-default with a greppable opt-out ───────────

check('html`` escapes an interpolated attack payload', () => {
    const name = `<img src=x onerror=alert(1)>`;
    const out = html`<div title="${name}">${name}</div>`;
    assert.ok(!out.includes('<img'), `unescaped markup leaked through: ${out}`);
    assert.equal(out, `<div title="&lt;img src=x onerror=alert(1)&gt;">&lt;img src=x onerror=alert(1)&gt;</div>`);
});

check('html`` leaves raw(...) markup untouched — the one opt-out', () => {
    const out = html`<span>${raw('<b>bold</b>')}</span>`;
    assert.equal(out, '<span><b>bold</b></span>');
});

check('html`` still escapes a value next to a raw() one', () => {
    const evil = `"><script>1</script>`;
    const out = html`<span title="${evil}">${raw('<b>ok</b>')}</span>`;
    assert.ok(!out.includes('<script>'), `payload leaked: ${out}`);
});

// ── safeUrl(): href/src guard ─────────────────────────────────────────────

check('safeUrl() allows http(s) and escapes it', () => {
    assert.equal(safeUrl('https://divar.ir/v/x'), 'https://divar.ir/v/x');
    assert.equal(safeUrl('http://example.com/a"b'), 'http://example.com/a&quot;b');
});

check('safeUrl() allows a same-origin relative path', () => {
    assert.equal(safeUrl('/images/manual/abc.jpg'), '/images/manual/abc.jpg');
});

check('safeUrl() rejects javascript: and protocol-relative URLs', () => {
    assert.equal(safeUrl('javascript:alert(1)'), '#');
    assert.equal(safeUrl('//evil.example/x'), '#');
    assert.equal(safeUrl('data:text/html,<script>1</script>'), '#');
});

check('safeUrl() rejects an attribute-breakout attempt outright', () => {
    // even though the scheme check alone would reject this, this pins down
    // that a bare quote never survives as an unescaped '#' bypass either
    const out = safeUrl(`"><svg onload=alert(1)>`);
    assert.equal(out, '#');
});

// ── safeManualPhoto(): the strict manual-lead-photo guard ─────────────────

const validPhoto = '/images/manual/' + '0123456789abcdef0123456789abcdef' + '.jpg';

check('safeManualPhoto() allows exactly what upload-image can return', () => {
    assert.equal(safeManualPhoto(validPhoto), validPhoto);
});

check('safeManualPhoto() rejects a path-traversal attempt', () => {
    assert.equal(safeManualPhoto('/images/manual/../../x'), '');
});

check('safeManualPhoto() rejects anything not matching the exact shape', () => {
    assert.equal(safeManualPhoto('/images/manual/short.jpg'), '');
    assert.equal(safeManualPhoto('https://evil.example/images/manual/' + 'a'.repeat(32) + '.jpg'), '');
    assert.equal(safeManualPhoto('javascript:alert(1)'), '');
    assert.equal(safeManualPhoto(null), '');
});

// ── jsArg(): a value inside onclick="f(…)" ────────────────────────────────

// What the browser does: decode the attribute's entities, then run the code.
function runHandler(attrValue) {
    const decoded = attrValue.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&#96;/g, '`')
        .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
    const seen = [];
    new Function('f', `f(${decoded})`)(v => seen.push(v));
    return seen;
}

check('jsArg() hands the handler the value itself, whatever quotes it holds', () => {
    for (const v of [`');alert(1);('`, `");alert(1);("`, '\\\');alert(1)//', '`${alert(1)}`', '09121234567']) {
        assert.deepEqual(runHandler(jsArg(v)), [v]);
    }
});

check('esc() alone inside a handler is what let the quote through', () => {
    // the reason jsArg exists: the entity is decoded before the code runs,
    // so the payload's own call is reached
    assert.throws(() => runHandler(`'${esc(`');alert(1);('`)}'`), /alert is not defined/);
});

check('safeUrl() refuses a backslash-led path browsers read as another site', () => {
    assert.equal(safeUrl('/\\evil.example/x.png'), '#');
    assert.equal(safeUrl('/images/a.jpg'), '/images/a.jpg');
});

// ── the runtime card on پایش سامانه ──────────────────────────────────────
// Its values come from Redis, written there by each process about itself —
// host names, loop names, error lines, the sandbox's own report. Rendered
// through the real helpers and the real card functions.

new Function([
    body,                                   // esc, raw, html, … as above
    extractConst('_FA_DIGITS'),
    extractFunction('faNum'),
    extractConst('RT_ROLE_FA'),
    extractConst('RT_LOOP_FA'),
    extractFunction('_rtAgo'),
    extractFunction('_rtProc'),
    extractFunction('_rtLoop'),
    '\nglobalThis.__runtime = { _rtProc, _rtLoop };',
].join('\n\n'))();
const { _rtProc, _rtLoop } = globalThis.__runtime;

check('the runtime card escapes whatever a process reports about itself', () => {
    const evil = `"><img src=x onerror=alert(1)>`;
    const proc = _rtProc({ role: evil, host: evil, age_seconds: 3, draining: true,
                           running: ['a'], sandbox: { mode: evil } });
    assert.ok(!proc.includes('<img'), `payload leaked: ${proc}`);
    assert.ok(proc.includes('در حال تخلیه'), 'a draining process says so');
    const loop = _rtLoop({ name: evil, role: evil, restarts: 2, last_error: evil,
                           last_error_at: 1700000000, last_beat: Date.now() / 1000 - 5 });
    assert.ok(!loop.includes('<img'), `payload leaked: ${loop}`);
    assert.ok(loop.includes('بار ری‌استارت'), 'a restarted loop says how often');
    assert.ok(_rtLoop({ name: 'digest', stale: true }).includes('گیرکرده'));
    assert.ok(_rtLoop({ name: 'digest', off: true, stale: false }).includes('خاموش'));
});

console.log(`${passed} checks passed`);

// Behavioural check for the panel's log viewer, run against the REAL
// loadScraperLog in frontend/js/app.js with the DOM and the API stubbed:
// it reads the file that is picked, not always scraper.log.
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

function world(file, grep, answer) {
    const asked = [];
    const el = {
        'scraper-log-body': { textContent: '', scrollTop: 0, scrollHeight: 99 },
        'scraper-log-grep': { value: grep },
        'scraper-log-file': file === undefined ? undefined : { value: file },
    };
    const document = { getElementById: (id) => el[id] || null };
    const run = new Function('document', 'apiCall', `
        ${extractFunction('loadScraperLog')}
        return loadScraperLog;`)(document, async (url) => { asked.push(url); return answer; });
    return { el, asked, run };
}

let passed = 0;

{   // the three files are offered, scraper.log first as before
    const i = html.indexOf('id="scraper-log-file"');
    const sel = html.slice(i, html.indexOf('</select>', i));
    assert.deepEqual([...sel.matchAll(/value="([^"]+)"/g)].map(m => m[1]),
                     ['scraper.log', 'scheduler.log', 'api.log']);
    passed++;
}
{   // the picked file is the one asked for, with the search
    const { el, asked, run } = world('scheduler.log', '[photo]', { lines: ['a', 'b'] });
    await run();
    assert.equal(asked[0], '/stats/logs?lines=300&log=scheduler.log&grep=%5Bphoto%5D');
    assert.equal(el['scraper-log-body'].textContent, 'a\nb');
    passed++;
}
{   // no picker on the page: scraper.log, as it always was
    const { asked, run } = world(undefined, '', { lines: [] });
    await run();
    assert.equal(asked[0], '/stats/logs?lines=300&log=scraper.log');
    passed++;
}
{   // a file that does not exist yet is explained in Persian
    const { el, run } = world('api.log', '', { lines: [], note: 'log file not found' });
    await run();
    assert.ok(el['scraper-log-body'].textContent.startsWith('این لاگ هنوز ساخته نشده است'));
    passed++;
}

console.log(`${passed} checks passed`);

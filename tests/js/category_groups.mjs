// Behavioural check for the category selects (#56), run against the REAL
// loadCategories in frontend/js/app.js with the DOM and the API stubbed.
//
// Every category arrives from /api/scraper/categories with its `family` and
// the family's Persian name; the scrape form and the three filters built
// from the same list show rent categories under «اجاره», buy categories
// under «خرید», and short-term and services each under their own group —
// in the order the server sent them, and with nothing in the script naming
// a category. Server text is escaped like any other.
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

const SELECTS = ['scraper-category', 'filter-category', 'crm-filter-category', 'jobs-filter-category'];
const FIRST = '<option value="">همه دسته‌بندی‌ها</option>';

function world(categories) {
    const el = {};
    for (const id of SELECTS) el[id] = { innerHTML: id === 'scraper-category' ? '' : FIRST };
    const document = { getElementById: (id) => el[id] || null };
    const scraperCategories = {};
    const run = new Function('document', 'apiCall', '_scraperCategories', 'onScraperCategoryChange', `
        ${extractFunction('esc')}
        ${extractFunction('categoryOptionsHtml')}
        ${extractFunction('loadCategories')}
        return loadCategories;`)(document, async () => categories, scraperCategories, () => {});
    return { el, run, scraperCategories };
}

// The server's order: CATEGORIES in app/config.py interleaves buy and rent.
const CATS = [
    { slug: 'buy-residential', name: 'خرید مسکونی', type: 'buy', family: 'buy', family_name: 'خرید' },
    { slug: 'buy-old-house', name: 'خرید خانه کلنگی', type: 'buy', family: 'buy', family_name: 'خرید' },
    { slug: 'rent-apartment', name: 'اجاره آپارتمان', type: 'rent', family: 'rent', family_name: 'اجاره' },
    { slug: 'buy-office', name: 'خرید دفتر کار', type: 'buy', family: 'buy', family_name: 'خرید' },
    { slug: 'rent-industrial-agricultural-property', name: 'اجاره صنعتی و کشاورزی', type: 'rent',
      family: 'rent', family_name: 'اجاره' },
    { slug: 'rent-temporary', name: 'اجاره کوتاه مدت', type: 'rent', family: 'temporary',
      family_name: 'اجارهٔ کوتاه‌مدت' },
    { slug: 'real-estate-services', name: 'خدمات املاک', type: 'service', family: 'service',
      family_name: 'خدمات' },
];

// [group label, [option values]] in document order, from one select's HTML.
function groups(html) {
    const out = [];
    const re = /<optgroup label="([^"]*)">([\s\S]*?)<\/optgroup>/g;
    let m;
    while ((m = re.exec(html))) {
        out.push([m[1], [...m[2].matchAll(/<option value="([^"]*)"/g)].map(x => x[1])]);
    }
    return out;
}

let passed = 0;

{   // the scrape form: four groups, in the order the server sent the families
    const { el, run, scraperCategories } = world(CATS);
    await run();
    assert.deepEqual(groups(el['scraper-category'].innerHTML), [
        ['خرید', ['buy-residential', 'buy-old-house', 'buy-office']],
        ['اجاره', ['rent-apartment', 'rent-industrial-agricultural-property']],
        ['اجارهٔ کوتاه‌مدت', ['rent-temporary']],
        ['خدمات', ['real-estate-services']],
    ], el['scraper-category'].innerHTML);
    // nothing outside a group, and every category still reachable by slug
    const outside = el['scraper-category'].innerHTML.replace(/<optgroup[\s\S]*?<\/optgroup>/g, '').trim();
    assert.equal(outside, '', `an option outside every group: ${outside}`);
    assert.deepEqual(Object.keys(scraperCategories).sort(), CATS.map(c => c.slug).sort());
    passed++;
}
{   // the three filters: the same groups, their «همه» first, the value the name
    const { el, run } = world(CATS);
    await run();
    for (const id of ['filter-category', 'crm-filter-category', 'jobs-filter-category']) {
        const html = el[id].innerHTML;
        assert.ok(html.startsWith(FIRST), `${id} lost its «همه» option`);
        assert.deepEqual(groups(html).map(([label, vals]) => [label, vals.length]),
                         [['خرید', 3], ['اجاره', 2], ['اجارهٔ کوتاه‌مدت', 1], ['خدمات', 1]], id);
        const rent = groups(html)[1][1];
        assert.deepEqual(rent, ['اجاره آپارتمان', 'اجاره صنعتی و کشاورزی'], `${id}: ${rent}`);
        assert.ok(html.includes('data-type="rent"'), `${id} lost data-type`);
    }
    passed++;
}
{   // a family is grouped by the data, not by the slug's first word
    const odd = [{ slug: 'x-one', name: 'یک', type: 'rent', family: 'buy', family_name: 'خرید' },
                 { slug: 'buy-two', name: 'دو', type: 'buy', family: 'rent', family_name: 'اجاره' }];
    const { el, run } = world(odd);
    await run();
    assert.deepEqual(groups(el['scraper-category'].innerHTML),
                     [['خرید', ['x-one']], ['اجاره', ['buy-two']]]);
    passed++;
}
{   // a family without a Persian name still gets a group of its own, by its key;
    // a category with no family at all goes under «سایر»
    const cats = [{ slug: 'a', name: 'الف', type: 'buy', family: 'buy', family_name: 'خرید' },
                  { slug: 'b', name: 'ب', type: 'other', family: 'mystery' },
                  { slug: 'c', name: 'پ', type: 'other' }];
    const { el, run } = world(cats);
    await run();
    assert.deepEqual(groups(el['scraper-category'].innerHTML).map(g => g[0]), ['خرید', 'mystery', 'سایر']);
    passed++;
}
{   // server text is escaped: a name, a family name, a slug
    const cats = [{ slug: 'x"><img src=x>', name: '<b>نام</b>', type: 'buy', family: 'buy',
                    family_name: '"><script>alert(1)</script>' }];
    const { el, run } = world(cats);
    await run();
    for (const id of SELECTS) {
        const html = el[id].innerHTML;
        assert.ok(!html.includes('<img') && !html.includes('<script') && !html.includes('<b>'), `${id}: ${html}`);
    }
    passed++;
}

console.log(`${passed} checks passed`);

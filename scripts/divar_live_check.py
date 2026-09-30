"""Read Divar live, read-only, for what #27, #30, #31 and #57 still need.

The cloud sessions this project is worked in cannot reach Divar: its servers
do not answer from there. So this runs on a machine that can, and writes what
it saw into the repository, to be committed and read back:

    python scripts/divar_live_check.py                       # all of it, Urmia
    python scripts/divar_live_check.py --city tehran
    python scripts/divar_live_check.py --only counts,pages   # some of it
    python scripts/divar_live_check.py --gap 15              # slower still

Read-only. Only Divar's public pages and its public search API, the requests a
visitor's browser sends: no cookie, no login, no contact reveal, no database.
Pages are asked for `--gap` seconds apart (12 by default; Divar answered
empty pages at 3 s during the #27 read). All of it takes about 40 minutes.

What it writes:
  app/services/divar_filters.json    #27  every category's filter form, read off
                                          Divar (the four choice filters' options)
  tests/fixtures/divar_live/         #31  one owner's and one agency's real ad page
                                          per category, cleaned (no phone number,
                                          e-mail, address or person's name), and
                                          expected.json: what Divar's own data for
                                          the ad says each page must read as
                                     #30  search-*.json: a real search answer, cleaned
  docs/divar_live/report.md, .json   all  what was found, per issue
  .divar_live/                            the raw answers, for looking into a
                                          surprise; gitignored, never committed

The sections:
  filters   #27  each category's form from its page; the committed file updated
  page2     #27  a second search page with several filters, per family: HTTP 200
  counts    #57  per category, Divar's count against the whole list walked the way
                 a run builds its pool
  cards     #30  what a search card says about when the ad was posted, against the
                 publish date on the ad itself
  pages     #31  real ad pages read by the scraper's own code, against Divar's data;
            #57  and whether Divar's breadcrumb keeps each in its category
"""
import argparse
import asyncio
import contextlib
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))      # _divar_pages: the scraper, without a browser

# The app's settings want these; nothing here opens a database or Redis.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_divar_live_check.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import httpx  # noqa: E402

_real_sleep = asyncio.sleep                   # the pacing's own, whatever a reader patches

SECTIONS = ("filters", "page2", "counts", "cards", "pages")
FOCUS = ("buy-old-house", "rent-industrial-agricultural-property", "buy-office",
         "buy-store", "rent-store", "rent-temporary")   # #57's own list first
PAGE_URL = "https://divar.ir/v/{token}"
POST_API = "https://api.divar.ir/v8/posts-v2/web/{token}"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


# ── keeping people out of the repository ───────────────────────────────────

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "0123456789" * 2)
# on text whose digits were made Latin, index for index
_PRIVATE = [
    re.compile(r"(?<!\d)(?:\+?98|0098|0)?[\s\-]*9(?:[\s\-]*\d){9}(?!\d)"),   # mobile
    re.compile(r"(?<!\d)0\d{2}[\s\-]*\d(?:[\s\-]*\d){7}(?!\d)"),              # landline
    re.compile(r"(?<!\d)\d(?:[\s\-]?\d){7,}(?!\d)"),                         # any long number
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),                             # e-mail
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),                              # IP address
]
HIDDEN = "[حذف شد]"
NAME_STANDIN = "آزمون"
AGENCY_STANDIN = "املاک آزمون"


def mask_text(text: str) -> str:
    """Phone numbers, e-mails and long digit runs out of a piece of text."""
    if not text:
        return text
    flat = text.translate(_DIGITS)
    spans = sorted(m.span() for rx in _PRIVATE for m in rx.finditer(flat))
    out, at = [], 0
    for start, end in spans:
        if start < at:
            continue
        out.append(text[at:start])
        out.append(HIDDEN)
        at = end
    out.append(text[at:])
    return "".join(out)


def privacy_problems(text: str) -> List[str]:
    """The same checks tests/test_divar_fixture_pages.py makes of a fixture."""
    flat = text.translate(_DIGITS)
    found = []
    if re.search(r"(?<!\d)(?:\+?98|0098|0)?9\d{9}(?!\d)", flat.replace("-", "")):
        found.append("mobile number")
    if re.search(r"(?<!\d)0\d{2}[-\s]?\d{8}(?!\d)", flat):
        found.append("landline")
    if re.search(r"[\w.]+@[\w.]+\.\w+", text):
        found.append("e-mail")
    if re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
        found.append("IP address")
    if "tel:" in text:
        found.append("tel: link")
    return found


def _walk(node: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


_PEOPLE_WIDGETS = ("BUSINESS", "AGENCY", "AGENT", "SELLER", "USER", "BRAND", "PROFILE")


def names_in_post(post: Any) -> List[str]:
    """The advertiser's names in Divar's data for an ad: every text of a widget
    about the business or the person behind it."""
    names = set()
    for node in _walk(post):
        kind = str(node.get("widget_type") or node.get("type") or "")
        if not any(k in kind.upper() for k in _PEOPLE_WIDGETS):
            continue
        for inner in _walk(node):
            for key in ("title", "subtitle", "text", "name", "business_name", "agent_name"):
                v = inner.get(key)
                if isinstance(v, str) and 2 < len(v.strip()) <= 60:
                    names.add(v.strip())
    for node in _walk(post):
        for key in ("business_name", "agent_name", "agency_name", "user_name"):
            v = node.get(key)
            if isinstance(v, str) and 2 < len(v.strip()) <= 60:
                names.add(v.strip())
    return sorted(names, key=len, reverse=True)


def _standin(name: str) -> str:
    return AGENCY_STANDIN if any(w in name for w in ("املاک", "مشاور", "آژانس", "مسکن")) else NAME_STANDIN


def clean_json(data: Any, token: str, fake: str, names: Iterable[str] = ()) -> Any:
    """A copy of Divar's JSON with the token swapped, names stood in for and
    every string masked."""
    names = [n for n in names if n]

    def fix(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        if isinstance(v, list):
            return [fix(x) for x in v]
        if isinstance(v, str):
            v = v.replace(token, fake) if token else v
            for n in names:
                v = v.replace(n, _standin(n))
            return mask_text(v)
        return v
    return fix(data)


def clean_page(html: str, token: str, fake: str, names: Iterable[str] = ()) -> str:
    """A real ad page, safe to commit, with what the scraper reads left in place.

    Out: every script but the JSON-LD (whose description the room count falls
    back on), styles, SVG, iframes, comments, link targets, data attributes and
    phone-shaped text. The token becomes `fake` and the advertiser's names a
    stand-in. The rows, tables, breadcrumb, title, description, publish line
    and photos stay as Divar rendered them.
    """
    from bs4 import BeautifulSoup, Comment
    soup = BeautifulSoup(html, "lxml")
    for el in soup.find_all(["style", "svg", "iframe", "noscript", "link", "template"]):
        el.decompose()
    for el in soup.find_all("script"):
        if (el.get("type") or "") != "application/ld+json":
            el.decompose()
            continue
        try:
            data = json.loads(el.string or "{}")
        except ValueError:
            el.decompose()
            continue
        keep = {k: data[k] for k in ("@type", "name", "description", "numberOfRooms")
                if isinstance(data, dict) and k in data}
        el.string = json.dumps(clean_json(keep, token, fake, names), ensure_ascii=False)
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for el in soup.find_all("meta"):
        if not el.get("charset"):
            el.decompose()
    for el in soup.find_all(True):
        for attr in list(el.attrs):
            if attr in ("class", "dir", "lang", "colspan", "rowspan", "datetime", "alt", "charset", "type"):
                continue
            if attr == "src" and el.name == "img" and "divarcdn.com" in (el.get("src") or ""):
                continue
            if attr == "href" and el.name == "a":
                el["href"] = "#"
                continue
            del el.attrs[attr]
    text = str(soup)
    if token:
        text = text.replace(token, fake)
    for n in names:
        text = text.replace(n, _standin(n))
    # text nodes and the attributes left, masked; tags themselves untouched
    text = re.sub(r">([^<]+)<", lambda m: ">" + mask_text(m.group(1)) + "<", text)
    text = re.sub(r'(alt|datetime)="([^"]*)"', lambda m: f'{m.group(1)}="{mask_text(m.group(2))}"', text)
    return text


# ── what Divar's own data says an ad is ────────────────────────────────────

def divar_says(post: Any) -> Dict[str, Any]:
    """The facts Divar's data for one ad states, independent of our readers:
    its label/value rows, its amenity flags, its breadcrumb and who posted it."""
    rows: Dict[str, str] = {}
    flags: Dict[str, bool] = {}
    crumbs: List[str] = []
    business = None
    for node in _walk(post):
        title, value = node.get("title"), node.get("value")
        if isinstance(title, str) and isinstance(value, str) and title.strip() and value.strip() \
                and len(title) <= 40:
            rows.setdefault(title.strip(), value.strip())
        if isinstance(title, str) and isinstance(node.get("available"), bool):
            flags.setdefault(title.strip(), node["available"])
        for key in ("business_type", "business-type", "businessType"):
            if business is None and isinstance(node.get(key), str):
                business = node[key]
        parents = node.get("parent_items")
        if not crumbs and isinstance(parents, list) and parents:
            crumbs = [str(p["title"]) for p in parents if isinstance(p, dict) and p.get("title")]
            if isinstance(node.get("current_page_title"), str):
                crumbs.append(node["current_page_title"])
    return {"rows": rows, "flags": flags, "breadcrumb": crumbs, "business_type": business}


_ROW_KEYS = (
    ("area", ("متراژ",), "number"),
    ("land_area", ("متراژ زمین",), "number"),
    ("built_area", ("متراژ بنا", "زیربنا"), "number"),
    ("year_built", ("ساخت", "سال ساخت"), "number"),
    ("rooms", ("اتاق", "تعداد اتاق"), "rooms"),
    ("total_price", ("قیمت کل",), "price"),
    ("price_per_meter", ("قیمت هر متر",), "price"),
    ("deposit", ("ودیعه",), "price"),
    ("rent_price", ("اجارهٔ ماهانه", "اجاره ماهانه", "اجاره"), "price"),
)
_FLAG_KEYS = (("has_elevator", "آسانسور"), ("has_parking", "پارکینگ"),
              ("has_storage", "انباری"), ("has_balcony", "بالکن"))


def expected_from(says: Dict[str, Any]) -> Dict[str, Any]:
    """What our reader must find, from what Divar's data states. Only what
    could be read without doubt: «توافقی» and the like state no number."""
    from app.scraper.parsers import parse_persian_number, parse_price_with_unit
    out: Dict[str, Any] = {}
    rows = says.get("rows") or {}
    for key, titles, kind in _ROW_KEYS:
        value = next((rows[t] for t in titles if t in rows), None)
        if value is None:
            continue
        if kind == "rooms" and "بدون" in value:
            out[key] = 0
            continue
        n = parse_price_with_unit(value) if kind == "price" else parse_persian_number(value)
        if n is not None:
            out[key] = n
    floor = rows.get("طبقه")
    if floor:
        flat = floor.translate(_DIGITS)
        m = re.match(r"\s*(-?\d+)\s*از\s*(\d+)", flat)
        if m:
            out["floor"], out["total_floors"] = int(m.group(1)), int(m.group(2))
        elif "همکف" in floor:
            out["floor"] = 0
    flags = says.get("flags") or {}
    for key, word in _FLAG_KEYS:
        for title, on in flags.items():
            if title.startswith(word):
                out[key] = bool(on) and "ندارد" not in title
                break
    who = (says.get("business_type") or "").lower()
    if who:
        out["advertiser_type"] = "personal" if who in ("personal", "") else "agency"
    return out


# ── the run ─────────────────────────────────────────────────────────────────

@dataclass
class Where:
    root: Path

    @property
    def schema(self) -> Path:
        return self.root / "app" / "services" / "divar_filters.json"

    @property
    def fixtures(self) -> Path:
        return self.root / "tests" / "fixtures" / "divar_live"

    @property
    def report(self) -> Path:
        return self.root / "docs" / "divar_live"

    @property
    def raw(self) -> Path:
        return self.root / ".divar_live"


@dataclass
class Ctx:
    city: str
    gap: float
    where: Where
    client: httpx.AsyncClient
    max_walk: int = 960
    cards_open: int = 12
    report: Dict[str, Any] = field(default_factory=dict)
    last: float = 0.0
    sleep: Any = None

    async def pace(self) -> None:
        """Hold a request until `gap` seconds after the previous one."""
        wait = self.last + self.gap - time.monotonic()
        if self.last and wait > 0:
            await (self.sleep or _real_sleep)(wait)
        self.last = time.monotonic()

    def raw_write(self, name: str, content: Any) -> None:
        self.where.raw.mkdir(parents=True, exist_ok=True)
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=1)
        (self.where.raw / name).write_text(text, encoding="utf-8")


def say(msg: str) -> None:
    print(msg, flush=True)


def panel_categories() -> List[str]:
    from app.config import CATEGORIES
    slugs = [s for s, m in CATEGORIES.items() if m["type"] != "service"]
    return [s for s in FOCUS if s in slugs] + [s for s in slugs if s not in FOCUS]


async def get(ctx: Ctx, url: str, accept: str = "text/html") -> Tuple[Optional[int], Optional[str]]:
    await ctx.pace()
    try:
        resp = await ctx.client.get(url, headers={"User-Agent": _UA, "Accept": accept,
                                                  "Accept-Language": "fa-IR,fa;q=0.9"})
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    return resp.status_code, resp.text


async def search_page(ctx: Ctx, form: Dict[str, Any], after: Optional[dict] = None
                      ) -> Tuple[Optional[int], Any]:
    """One raw answer of Divar's search API, the way divar_count asks."""
    from app.services import divar_count as dc
    city_id = await dc.resolve_city_id(ctx.city, ctx.client)
    if not city_id:
        return None, f"city {ctx.city!r} not found"
    body: Dict[str, Any] = {"city_ids": [str(city_id)], "search_data": {"form_data": {"data": form}}}
    if after:
        body["pagination_data"] = after
    await ctx.pace()
    try:
        resp = await ctx.client.post(dc.SEARCH_URL, json=body, headers={
            "User-Agent": _UA, "Content-Type": "application/json", "Accept": "application/json"})
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, resp.text[:300]


# ── #27: the forms ──────────────────────────────────────────────────────────

async def check_filters(ctx: Ctx) -> None:
    from app.services import divar_count as dc
    from app.services import divar_filters as df
    import fetch_divar_filters as fdf
    base = df.committed()
    slugs = sorted(base["categories"])
    say(f"[filters] {len(slugs)} category pages, {max(ctx.gap, 15):g} s apart")
    got, names, failed = await df.read_divar(slugs, gap=max(ctx.gap, 15), client=ctx.client,
                                             sleep=ctx.sleep or _real_sleep)
    schema = df.merge_live(got, base)
    for slug, name in names.items():
        schema["categories"][slug]["token"] = name
    changes = df.changes(base, schema)
    tokens = {slug: {"committed": base["categories"][slug].get("token"), "divar": name,
                     "divar_count.py": dc.CATEGORY_TOKENS.get(slug)}
              for slug, name in names.items()
              if name != base["categories"][slug].get("token") or name != dc.CATEGORY_TOKENS.get(slug)}
    unknown = sorted(f"{slug}:{f['key']}" for slug, e in schema["categories"].items()
                     for f in e.get("filters") or [] if not df.options_known(f))
    if got:
        schema["source"] = "divar"
        schema["read_on"] = dc.tehran_today().isoformat()
        schema["categories"] = dict(sorted(schema["categories"].items()))
        ctx.where.schema.parent.mkdir(parents=True, exist_ok=True)
        fdf.write(schema, ctx.where.schema)
        if ctx.where.schema == df.COMMITTED_PATH:
            df._committed_text.cache_clear()
            df._committed_cached.cache_clear()
            df.forget()
    ctx.report["filters"] = {
        "read": sorted(got), "not_read": failed, "changes": changes,
        "token_differences": tokens, "options_still_unknown": unknown,
        "written": bool(got),
    }
    say(f"[filters] read {len(got)}, not read {len(failed)}, {len(changes)} change(s)")


# ── #27: a second page with several filters ────────────────────────────────

PAGE2_CASES = (
    ("buy-apartment", {"min_price": 1_000_000_000, "min_area": 50, "min_rooms": 2, "max_rooms": 3,
                       "has_parking": True, "divar_filters": {"building-age": {"max": 25}}}),
    ("rent-apartment", {"max_deposit": 3_000_000_000, "max_rent": 40_000_000, "min_area": 40,
                        "has_elevator": True}),
    ("rent-temporary", {"divar_filters": {"person_capacity": {"min": 2},
                                          "daily_rent": {"max": 10_000_000}}}),
    ("buy-office", {"min_area": 20, "divar_filters": {"bizzDeed": True}}),
    ("rent-industrial-agricultural-property", {"min_area": 50}),
)


async def check_page2(ctx: Ctx) -> None:
    from app.services import divar_count as dc
    out = []
    for slug, kw in PAGE2_CASES:
        plan = dc.plan_filters(slug, **kw)
        status1, first = await search_page(ctx, plan.form)
        row: Dict[str, Any] = {"category": slug, "sent": plan.query, "notes": plan.notes,
                               "page1": status1}
        pag = (first or {}).get("pagination") if isinstance(first, dict) else None
        n1 = len([w for w in (first or {}).get("list_widgets") or [] if w.get("widget_type") == "POST_ROW"]) \
            if isinstance(first, dict) else 0
        row["page1_rows"] = n1
        if status1 == 200 and pag and pag.get("has_next_page") and pag.get("data"):
            status2, second = await search_page(ctx, plan.form, pag["data"])
            row["page2"] = status2
            if status2 != 200:
                row["divar_said"] = second if isinstance(second, str) else (second or {}).get("message")
        else:
            row["page2"] = "no second page"
        out.append(row)
        say(f"[page2] {slug}: page 1 {status1} ({n1} rows), page 2 {row['page2']}")
    # The control: a filter the category does not have, sent anyway. Divar
    # refusing its second page is what #27 was about; it should still.
    form = dict(dc.plan_filters("buy-apartment").form)
    form["credit"] = {"number_range": {"maximum": "1000000000"}}
    s1, first = await search_page(ctx, form)
    pag = (first or {}).get("pagination") if isinstance(first, dict) else None
    control: Dict[str, Any] = {"category": "buy-apartment", "sent": "credit (not a filter of it)",
                               "page1": s1}
    if s1 == 200 and pag and pag.get("data"):
        s2, second = await search_page(ctx, form, pag["data"])
        control["page2"] = s2
        control["divar_said"] = second if isinstance(second, str) else (second or {}).get("message")
    ctx.report["page2"] = {"cases": out, "control": control}


# ── #57: Divar's count against the list a run would walk ───────────────────

async def check_counts(ctx: Ctx) -> None:
    from app.services import divar_count as dc
    rows = []
    for i, slug in enumerate(panel_categories()):
        if i:
            await (ctx.sleep or _real_sleep)(ctx.gap)
        plan = dc.plan_filters(slug)
        count, cerr = await dc.fetch_post_count(ctx.city, plan.form)
        rep = dc.FeedReport()
        listings, err = await dc.fetch_listings(ctx.city, plan.form, target=ctx.max_walk, report=rep)
        walked = len(listings) + len(rep.leftover)
        if count is None:
            verdict = f"دیوار عدد نداد ({cerr})"
        elif rep.stop == dc.STOP_TARGET:
            verdict = f"بیش از سقف بررسی ({ctx.max_walk})؛ مقایسه نشد"
        elif rep.stop == dc.STOP_END:
            verdict = "برابر" if walked == count else f"اختلاف {walked - count:+d}"
        else:
            verdict = f"پیمایش کامل نشد: {rep.stop}" + (f" (HTTP {rep.status})" if rep.status else "")
        rows.append({"category": slug, "divar_count": count, "walked": walked, "pages": rep.last_page,
                     "stop": rep.stop, "status": rep.status, "divar_said": rep.divar_message,
                     "error": err, "verdict": verdict})
        say(f"[counts] {slug}: Divar {count}, walked {walked} ({rep.stop}) — {verdict}")
    ctx.report["counts"] = {"city": ctx.city, "rows": rows}


# ── reading a page the way the scraper does ─────────────────────────────────

@contextlib.asynccontextmanager
async def _no_waits():
    """The scraper's own pauses, off while it reads a saved page."""
    real = asyncio.sleep

    async def nothing(*_a, **_k):
        return None
    asyncio.sleep = nothing  # type: ignore[assignment]
    try:
        yield
    finally:
        asyncio.sleep = real  # type: ignore[assignment]


async def read_page(html: str, token: str, category: str) -> Tuple[Any, List[str]]:
    """(what the scraper reads off the page, the breadcrumb), stopping before
    any contact reveal. False when the breadcrumb files it elsewhere."""
    import _divar_pages as dp
    from bs4 import BeautifulSoup
    from app.services import advertiser_signals
    crumbs = [a.get_text(strip=True) for a in BeautifulSoup(html, "lxml").select("a.kt-breadcrumbs__action")]
    page = dp.FixturePage({token: html})
    s = dp.make_scraper(page)
    async with _no_waits():
        got = await s.scrape_property_detail(dp.url_of(token), target_category=category,
                                             wants_contact=lambda _pd: "stop before the reveal")
    if isinstance(got, dict):
        advertiser_signals.annotate(got)
    return got, crumbs


_COMPARED = ("total_price", "price_per_meter", "deposit", "rent_price", "area", "land_area",
             "built_area", "rooms", "year_built", "floor", "total_floors", "has_elevator",
             "has_parking", "has_storage", "has_balcony", "advertiser_type")


# ── #31 and #57: real ad pages ──────────────────────────────────────────────

async def check_pages(ctx: Ctx) -> None:
    from app.config import CATEGORIES
    from app.services import divar_count as dc
    ctx.where.fixtures.mkdir(parents=True, exist_ok=True)
    manifest_path = ctx.where.fixtures / "expected.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    rows, used = [], set()
    for ci, slug in enumerate(panel_categories()):
        for kind in ("personal", "agency"):
            plan = dc.plan_filters(slug, advertiser_type=kind)
            listings, err = await dc.fetch_listings(ctx.city, plan.form, target=6)
            token = next((x["divar_id"] for x in listings if x["divar_id"] not in used), None)
            name = f"{slug}-{kind}.html"
            row: Dict[str, Any] = {"page": name, "category": slug, "advertiser": kind}
            if not token:
                row["problem"] = f"no listing to open ({err or 'Divar had none'})"
                rows.append(row)
                say(f"[pages] {name}: {row['problem']}")
                continue
            used.add(token)
            fake = f"lv{ci:02d}{kind[0]}{len(used):03d}"
            status, html = await get(ctx, PAGE_URL.format(token=token))
            pstatus, post_text = await get(ctx, POST_API.format(token=token), "application/json")
            post: Any = None
            if pstatus == 200 and post_text:
                with contextlib.suppress(ValueError):
                    post = json.loads(post_text)
            ctx.raw_write(f"{fake}.html", html or "")
            ctx.raw_write(f"{fake}.post.json", post if post is not None else {"status": pstatus})
            if status != 200 or not html:
                row["problem"] = f"the page did not open (HTTP {status})"
                rows.append(row)
                say(f"[pages] {name}: {row['problem']}")
                continue
            source = f"api.divar.ir (HTTP {pstatus})"
            if post is None:
                # the same ad data, as the page embeds it for its own scripts
                from app.services import divar_filters as df
                post = df.preloaded_state(html)
                source = f"the page's own state (the API answered {pstatus})" if post else \
                    f"none: the API answered {pstatus} and the page has no state"
            first, _ = await read_page(html, token, slug)
            names = names_in_post(post)
            if isinstance(first, dict) and first.get("seller_name"):
                names.append(str(first["seller_name"]))
            clean = clean_page(html, token, fake, names)
            got, crumbs = await read_page(clean, fake, slug)
            says = divar_says(post) if post is not None else {"rows": {}, "flags": {}}
            expect = expected_from(says)
            read = {k: got.get(k) for k in _COMPARED} if isinstance(got, dict) else {}
            wrong = {k: {"divar": v, "read": read.get(k)} for k, v in expect.items() if read.get(k) != v}
            problems = privacy_problems(clean)
            row.update({
                "token": fake, "kept": got is not False, "breadcrumb": crumbs,
                "rendered_rows": bool(re.search(r"kt-(base-row|unexpandable-row|group-row)", clean)),
                "divar_data": source, "divar_rows": sorted((says.get("rows") or {})),
                "expect": expect, "read": read,
                "mismatch": wrong, "privacy": problems,
            })
            if problems:
                row["problem"] = "not saved: " + ", ".join(problems) + " left after cleaning"
            else:
                (ctx.where.fixtures / name).write_text(clean, encoding="utf-8")
                if post is not None:
                    (ctx.where.fixtures / name.replace(".html", ".post.json")).write_text(
                        json.dumps(clean_json(post, token, fake, names), ensure_ascii=False, indent=1),
                        encoding="utf-8")
                manifest[name] = {
                    "category": slug, "kind": CATEGORIES[slug]["type"], "advertiser": kind,
                    "token": fake, "leaf": crumbs[-1] if crumbs else None,
                    "captured_on": dc.tehran_today().isoformat(), "city": ctx.city,
                    "expect": expect, "read_when_captured": read, "mismatch": wrong,
                }
            rows.append(row)
            say(f"[pages] {name}: kept={row['kept']} rows={row['rendered_rows']} "
                f"mismatch={sorted(wrong)} privacy={problems or 'ok'}")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    ctx.report["pages"] = {"rows": rows}


# ── #30: what a search card knows about the date ────────────────────────────

_TIME_WORDS = ("پیش", "نردبان", "دیروز", "لحظاتی", "ساعت", "روز", "هفته", "ماه", "فوری")


async def check_cards(ctx: Ctx, slug: str = "buy-apartment") -> None:
    from app.services import divar_count as dc
    yesterday = dc.tehran_today() - timedelta(days=1)
    plan = dc.plan_filters(slug, posted_date=yesterday.isoformat())
    status, first = await search_page(ctx, plan.form)
    report: Dict[str, Any] = {"category": slug, "day": yesterday.isoformat(),
                              "recent_ads": plan.recent_ads, "page1": status}
    if status != 200 or not isinstance(first, dict):
        report["problem"] = f"search answered {status}"
        ctx.report["cards"] = report
        return
    ctx.raw_write("search-page1.json", first)
    widgets = [w for w in first.get("list_widgets") or [] if w.get("widget_type") == "POST_ROW"]
    keys: Dict[str, int] = {}
    cards: List[Tuple[str, List[str]]] = []      # (token, the card's time texts)
    for w in widgets:
        d = w.get("data") or {}
        for k in d:
            keys[k] = keys.get(k, 0) + 1
        token = d.get("token") or ((d.get("action") or {}).get("payload") or {}).get("token")
        texts = sorted({v for n in _walk(d) for v in n.values()
                        if isinstance(v, str) and len(v) <= 60 and any(t in v for t in _TIME_WORDS)})
        if isinstance(token, str) and token:
            cards.append((token, [mask_text(t) for t in texts]))
    fakes = {token: f"lvcard{i:03d}" for i, (token, _) in enumerate(cards)}
    cursor = ((first.get("pagination") or {}).get("data") or {}).get("last_post_date")
    report.update({"cards": len(widgets), "card_keys": keys, "page1_cursor": cursor,
                   "cursor_moment": str(dc.cursor_moment(cursor)) if cursor else None})
    opened: List[Dict[str, Any]] = []
    for token, texts in cards[:ctx.cards_open]:
        s, html = await get(ctx, PAGE_URL.format(token=token))
        if s != 200 or not html:
            opened.append({"token": fakes[token], "card": texts, "problem": f"HTTP {s}"})
            continue
        got, _ = await read_page(html, token, slug)
        posted = got.get("posted_at") if isinstance(got, dict) else None
        day = posted.date() if posted else None
        opened.append({"token": fakes[token], "card": texts,
                       "posted_at": posted.isoformat() if posted else None,
                       "same_day": (day == yesterday) if day else None,
                       "card_says_ladder": any("نردبان" in t for t in texts)})
    report["opened"] = opened
    other = [o for o in opened if o.get("same_day") is False]
    report["summary"] = {
        "opened": len(opened), "not_the_day": len(other),
        "not_the_day_with_ladder_word": sum(1 for o in other if o.get("card_says_ladder")),
        "the_day_with_ladder_word": sum(1 for o in opened if o.get("same_day") and o.get("card_says_ladder")),
        "no_date_read": sum(1 for o in opened if o.get("same_day") is None),
    }
    # the answer itself, for #30's tests: tokens swapped, text masked
    fixture = first
    for real, fake in fakes.items():
        if real:
            fixture = clean_json(fixture, real, fake)
    ctx.where.fixtures.mkdir(parents=True, exist_ok=True)
    (ctx.where.fixtures / f"search-{slug}-page1.json").write_text(
        json.dumps(fixture, ensure_ascii=False, indent=1), encoding="utf-8")
    ctx.report["cards"] = report
    say(f"[cards] {slug}: {len(widgets)} cards, opened {len(opened)}: {report['summary']}")


# ── the report ──────────────────────────────────────────────────────────────

def write_report(ctx: Ctx) -> None:
    ctx.where.report.mkdir(parents=True, exist_ok=True)
    (ctx.where.report / "report.json").write_text(
        json.dumps(ctx.report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    r, lines = ctx.report, [f"# بررسی زندهٔ دیوار — {ctx.report.get('meta', {}).get('day')}", ""]
    lines.append(f"شهر: `{ctx.city}` — فاصلهٔ درخواست‌ها: {ctx.gap:g} ثانیه — بخش‌ها: "
                 + "، ".join(r.get("meta", {}).get("sections", [])))
    if "filters" in r:
        f = r["filters"]
        lines += ["", "## #27 طرح فیلترها", f"- خوانده شد: {len(f['read'])} دسته؛ خوانده نشد: "
                  + ("، ".join(f["not_read"]) or "هیچ"),
                  f"- تغییر نسبت به فایل مخزن: {len(f['changes'])}"]
        lines += [f"  - {c}" for c in f["changes"]]
        lines += [f"- گزینه‌های هنوز ناشناخته: {', '.join(f['options_still_unknown']) or 'هیچ'}",
                  f"- نام داخلی متفاوت: {json.dumps(f['token_differences'], ensure_ascii=False) or 'هیچ'}"]
    if "page2" in r:
        lines += ["", "## #27 صفحهٔ دوم با چند فیلتر", "", "| دسته | فیلترها | صفحهٔ ۱ | صفحهٔ ۲ |", "|---|---|---|---|"]
        for c in r["page2"]["cases"]:
            lines.append(f"| {c['category']} | `{unquote(c['sent'])}` | {c['page1']} ({c['page1_rows']}) | {c['page2']} |")
        k = r["page2"]["control"]
        lines.append(f"\nکنترل (ودیعه روی خرید آپارتمان): صفحهٔ ۱ {k.get('page1')}، صفحهٔ ۲ {k.get('page2')} "
                     f"— {k.get('divar_said') or ''}")
    if "counts" in r:
        lines += ["", "## #57 عدد دیوار و فهرست پیموده‌شده", "",
                  "| دسته | دیوار | پیموده | صفحه | پایان | نتیجه |", "|---|---|---|---|---|---|"]
        for c in r["counts"]["rows"]:
            lines.append(f"| {c['category']} | {c['divar_count']} | {c['walked']} | {c['pages']} | "
                         f"{c['stop']} | {c['verdict']} |")
    if "pages" in r:
        lines += ["", "## #31 و #57 صفحه‌های واقعی آگهی", "",
                  "| صفحه | در دسته ماند | ردیف‌ها رندر شده | ناهمخوان با دادهٔ دیوار | مشکل |", "|---|---|---|---|---|"]
        for p in r["pages"]["rows"]:
            wrong = ", ".join(f"{k}: دیوار {v['divar']} / ما {v['read']}" for k, v in (p.get("mismatch") or {}).items())
            note = p.get("problem") or ("" if p.get("expect") else f"دادهٔ دیوار برای مقایسه نیامد: {p.get('divar_data')}")
            lines.append(f"| {p['page']} | {p.get('kept', '—')} | {p.get('rendered_rows', '—')} | "
                         f"{wrong or '—'} | {note} |")
    if "cards" in r:
        c = r["cards"]
        lines += ["", "## #30 کارت جست‌وجو و تاریخ انتشار", f"- دسته `{c['category']}`، روز {c['day']}، "
                  f"recent_ads={c.get('recent_ads')}، کارت‌های صفحهٔ ۱: {c.get('cards')}",
                  f"- کلیدهای داده در کارت: {json.dumps(c.get('card_keys'), ensure_ascii=False)}",
                  f"- خلاصه: {json.dumps(c.get('summary'), ensure_ascii=False)}", "",
                  "| آگهی | متن زمان روی کارت | انتشار روی خود آگهی | همان روز |", "|---|---|---|---|"]
        for o in c.get("opened") or []:
            lines.append(f"| {o['token']} | {' / '.join(o['card']) or '—'} | {o.get('posted_at') or o.get('problem')} | "
                         f"{o.get('same_day')} |")
    (ctx.where.report / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run(argv: Optional[List[str]] = None, *, transport: Optional[httpx.AsyncBaseTransport] = None,
              sleep=None) -> Dict[str, Any]:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--city", default="urmia")
    ap.add_argument("--gap", type=float, default=12.0, help="seconds between two Divar requests")
    ap.add_argument("--only", default=",".join(SECTIONS), help="comma-separated: " + ",".join(SECTIONS))
    ap.add_argument("--max-walk", type=int, default=960, help="listings walked per category at most")
    ap.add_argument("--cards-open", type=int, default=12, help="ads opened for the card check")
    ap.add_argument("--root", default=str(ROOT), help="where the files are written")
    args = ap.parse_args(argv)
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    bad = [s for s in only if s not in SECTIONS]
    if bad:
        ap.error(f"unknown section(s): {', '.join(bad)}")
    from app.services import divar_count as dc
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, transport=transport) as client:
        ctx = Ctx(city=args.city, gap=args.gap, where=Where(Path(args.root)), client=client,
                  max_walk=args.max_walk, cards_open=args.cards_open, sleep=sleep)
        ctx.report["meta"] = {"day": dc.tehran_today().isoformat(), "city": args.city,
                              "sections": only, "gap": args.gap}
        if not await dc.resolve_city_id(args.city, client):
            raise SystemExit(f"Divar does not know the city {args.city!r}, or did not answer")
        steps = {"filters": check_filters, "page2": check_page2, "counts": check_counts,
                 "cards": check_cards, "pages": check_pages}
        for name in SECTIONS:
            if name not in only:
                continue
            try:
                await steps[name](ctx)
            except Exception as e:               # one section's surprise is not the others' end
                ctx.report[name] = {"problem": f"{type(e).__name__}: {e}"}
                say(f"[{name}] stopped: {type(e).__name__}: {e}")
            write_report(ctx)
    say(f"report: {ctx.where.report / 'report.md'}")
    return ctx.report


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()

"""
Which filters Divar has for each category — read off Divar itself (#27).

Divar's search form is different for every category: «ودیعه» exists for
apartment rentals and not for sales, «سند اداری» only for commercial sales.
A filter the category does not have is not ignored — Divar answers the first
page and refuses the second with HTTP 400 («invalid filter for
apartment-sell: credit»), which is how runs 39, 41, 44, 45 and 47 each saw
only 24 listings. So every filter the scraper sends, the estimate asks about
and the panel offers is looked up here first.

Two sources, in this order:

1. Divar's own category page, whose embedded state carries the form it shows
   (window.__PRELOADED_STATE__.nb.filtersPage.widgetList). Read at most once
   a day, 15 seconds apart per page, by one background loop, and kept in
   Redis for every process.
2. divar_filters.json beside this file, committed to the repository and
   written by scripts/fetch_divar_filters.py. It is what every process uses
   until a live read exists, and whenever Divar does not answer.

A live read that differs from the committed file is logged and kept in Redis
as a list of changes, which the runtime card (/api/monitoring/runtime) shows:
the committed file is then out of date, and a filter Divar dropped would
start costing runs their second page again.
"""
import asyncio
import copy
import json
import re
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from loguru import logger

COMMITTED_PATH = Path(__file__).with_name("divar_filters.json")

# The four shapes Divar's form_data takes.
NUMBER_RANGE = "number_range"
BOOLEAN = "boolean"
REPEATED = "repeated_string"
STRING = "str"
TYPES = (NUMBER_RANGE, BOOLEAN, REPEATED, STRING)

PAGE_URL = "https://divar.ir/s/{city}/{slug}"
FETCH_CITY = "tehran"          # the form is the same in every city
PAGE_GAP = 15.0                # seconds between two pages (#27: 3 s was throttled)
LIVE_KEY = "sf:divar_filters:live"
STATE_KEY = "sf:divar_filters:state"
LIVE_MAX_AGE = 24 * 3600       # a live read older than this is read again
LIVE_TTL = 3 * 24 * 3600       # and Redis forgets one after this
_MEMORY_TTL = 600              # a process asks Redis at most every ten minutes

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


# ── the committed schema ────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _committed_text() -> str:
    return COMMITTED_PATH.read_text(encoding="utf-8")


def committed() -> Dict[str, Any]:
    """The schema in the repository. A fresh copy: callers may change it."""
    return json.loads(_committed_text())


# The schema this process uses: the committed one until a live read reaches
# it through current(). A module global so the pure builders in divar_count
# can stay synchronous; (schema, when read) or None.
_active: Optional[Tuple[Dict[str, Any], float]] = None


def snapshot() -> Dict[str, Any]:
    """The schema as this process last knew it, without waiting on anything."""
    if _active is not None:
        return _active[0]
    return _committed_cached()


@lru_cache(maxsize=1)
def _committed_cached() -> Dict[str, Any]:
    return committed()


def category(slug: Optional[str], schema: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """One category's entry — token, family, filters — or None."""
    cats = (schema or snapshot()).get("categories") or {}
    return cats.get((slug or "").strip())


def filters_for(slug: Optional[str], schema: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """The category's filters by key. Empty for a category Divar has none for,
    and for one that is not in the schema at all."""
    entry = category(slug, schema)
    return {f["key"]: f for f in (entry or {}).get("filters") or [] if f.get("key")}


def definitions(schema: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """Every filter any category has, by key — for a filter's Persian title
    when the category in question does not have it."""
    out: Dict[str, Dict[str, Any]] = {}
    for entry in ((schema or snapshot()).get("categories") or {}).values():
        for f in entry.get("filters") or []:
            out.setdefault(f["key"], f)
    return out


def title_of(key: str, schema: Optional[Dict[str, Any]] = None) -> str:
    f = definitions(schema).get(key)
    return (f or {}).get("title") or key


def options_known(f: Dict[str, Any]) -> bool:
    """A choice filter whose options are not known yet cannot be offered.
    Ranges and switches have none to know."""
    if f.get("type") not in (REPEATED, STRING):
        return True
    return isinstance(f.get("options"), list) and bool(f["options"])


def option_values(f: Dict[str, Any]) -> List[str]:
    return [str(o.get("value")) for o in (f.get("options") or []) if isinstance(o, dict)]


# ── reading Divar's page ────────────────────────────────────────────────────

_STATE_MARK = re.compile(r"window\.__PRELOADED_STATE__\s*=\s*")


def preloaded_state(html: str) -> Optional[Dict[str, Any]]:
    """The JSON a Divar page embeds as window.__PRELOADED_STATE__, or None."""
    m = _STATE_MARK.search(html or "")
    if not m:
        return None
    text = html[m.end():].lstrip()
    if text.startswith(("'", '"')):
        # Some builds embed it as a JSON string holding the JSON.
        try:
            inner, _ = json.JSONDecoder().raw_decode(text)
            return json.loads(inner) if isinstance(inner, str) else None
        except ValueError:
            return None
    try:
        state, _ = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None
    return state if isinstance(state, dict) else None


def _walk(node: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _first_text(*values: Any) -> Optional[str]:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _options_in(widget: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
    """The choices a widget offers, as [{value, title}], or None.

    Divar's widgets name them in more than one way (options, items, chips…);
    any list of objects that carry a value is taken.
    """
    for node in _walk(widget):
        for name in ("options", "items", "chips", "values", "choices"):
            rows = node.get(name)
            if not isinstance(rows, list) or not rows:
                continue
            out = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                value = _first_text(row.get("value"), row.get("key"), row.get("id"))
                if value is None:
                    continue
                out.append({"value": value,
                            "title": _first_text(row.get("title"), row.get("display"),
                                                 row.get("text"), row.get("label")) or value})
            if out:
                return out
    return None


def parse_widget_list(widgets: Any) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """(filters, Divar's internal category name) from a filtersPage.widgetList.

    Every widget carrying a field with a key is one filter: field.key and
    field.type as Divar names them, the widget's title, and its options when
    it lists any. The category's internal name is in the widgets' cache_key.
    """
    found: Dict[str, Dict[str, Any]] = {}
    internal: Optional[str] = None
    for w in _walk(widgets):
        ck = w.get("cache_key")
        if internal is None and isinstance(ck, str) and ck.strip():
            internal = ck.strip()
        field = w.get("field")
        if not isinstance(field, dict) or not _first_text(field.get("key")):
            continue
        key = field["key"].strip()
        if key in found or key == "category":
            continue
        data: Dict[str, Any] = w["data"] if isinstance(w.get("data"), dict) else {}
        entry: Dict[str, Any] = {
            "key": key,
            "type": _first_text(field.get("type")) or "",
            "title": _first_text(w.get("title"), data.get("title"), field.get("title"),
                                 w.get("label"), data.get("label")),
        }
        opts = _options_in(w)
        if opts is not None:
            entry["options"] = opts
        found[key] = entry
    return sorted(found.values(), key=lambda f: f["key"]), internal


def filters_from_page(html: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """A category page's filters, or ([], None) when its state is not there."""
    state = preloaded_state(html)
    widgets = (((state or {}).get("nb") or {}).get("filtersPage") or {}).get("widgetList")
    if widgets is None:
        return [], None
    return parse_widget_list(widgets)


def merge_live(live: Dict[str, List[Dict[str, Any]]], base: Optional[Dict[str, Any]] = None
               ) -> Dict[str, Any]:
    """A schema from what Divar's pages said, per slug, filled in from `base`
    (the committed one) where the page is silent: a filter's Persian title,
    its unit, and its options when the page listed none. A category that was
    not read keeps its committed entry."""
    base = copy.deepcopy(base or committed())
    known = definitions(base)
    for slug, rows in live.items():
        entry = base.setdefault("categories", {}).setdefault(slug, {"filters": []})
        had = {f["key"]: f for f in entry.get("filters") or []}
        merged = []
        for row in rows:
            old = had.get(row["key"]) or known.get(row["key"]) or {}
            f = {"key": row["key"],
                 "type": row.get("type") or old.get("type") or "",
                 "title": row.get("title") or old.get("title") or row["key"]}
            if old.get("unit"):
                f["unit"] = old["unit"]
            if f["type"] in (REPEATED, STRING):
                f["options"] = row.get("options") or old.get("options")
            merged.append(f)
        entry["filters"] = sorted(merged, key=lambda f: f["key"])
    return base


def changes(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """What a live read says differently from the committed schema, one line
    each, naming the category and the filter — for the log and the card."""
    out: List[str] = []
    old_cats, new_cats = old.get("categories") or {}, new.get("categories") or {}
    for slug in sorted(set(old_cats) | set(new_cats)):
        a, b = filters_for(slug, old), filters_for(slug, new)
        for key in sorted(set(b) - set(a)):
            out.append(f"{slug}: فیلتر تازهٔ «{b[key].get('title') or key}» ({key})")
        for key in sorted(set(a) - set(b)):
            out.append(f"{slug}: فیلتر «{a[key].get('title') or key}» ({key}) دیگر نیست")
        for key in sorted(set(a) & set(b)):
            if (a[key].get("type") or "") != (b[key].get("type") or ""):
                out.append(f"{slug}: نوع «{key}» از {a[key].get('type')} به {b[key].get('type')} رسید")
            elif b[key].get("options") and a[key].get("options") is not None \
                    and set(option_values(a[key])) != set(option_values(b[key])):
                out.append(f"{slug}: گزینه‌های «{a[key].get('title') or key}» ({key}) عوض شد")
    return out


# ── the live read, once a day, shared through Redis ─────────────────────────

async def _fetch_page(client: Any, slug: str, city: str = FETCH_CITY) -> Optional[str]:
    resp = await client.get(PAGE_URL.format(city=city, slug=slug),
                            headers={"User-Agent": _UA, "Accept": "text/html",
                                     "Accept-Language": "fa-IR,fa;q=0.9"})
    if resp.status_code != 200:
        logger.info(f"[filters] {slug}: Divar answered {resp.status_code}")
        return None
    return resp.text


async def read_divar(slugs: Iterable[str], *, city: str = FETCH_CITY,
                     gap: float = PAGE_GAP, client: Any = None,
                     sleep: Callable[[float], Any] = asyncio.sleep
                     ) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, str], List[str]]:
    """({slug: filters}, {slug: Divar's internal name}, [slugs that failed]).

    One page per category, `gap` seconds apart. Never raises: a page that
    does not answer, or answers without the form, is a failed slug.
    """
    import httpx
    own = client is None
    client = client or httpx.AsyncClient(timeout=25.0, follow_redirects=True)
    got: Dict[str, List[Dict[str, Any]]] = {}
    names: Dict[str, str] = {}
    failed: List[str] = []
    try:
        for i, slug in enumerate(slugs):
            if i:
                await sleep(gap)
            try:
                html = await _fetch_page(client, slug, city)
            except Exception as e:
                logger.info(f"[filters] {slug}: no answer ({type(e).__name__})")
                html = None
            rows, internal = filters_from_page(html or "")
            if not rows and internal is None:
                failed.append(slug)
                continue
            got[slug] = rows
            if internal:
                names[slug] = internal
    finally:
        if own:
            await client.aclose()
    return got, names, failed


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _redis():
    from app import database
    return await database.get_redis()


async def current() -> Dict[str, Any]:
    """The schema to use now: the live read other processes stored in Redis
    if there is one, else the committed file. Never raises, and asks Redis at
    most every ten minutes."""
    global _active
    if _active is not None and time.monotonic() - _active[1] < _MEMORY_TTL:
        return _active[0]
    schema = None
    try:
        r = await _redis()
        raw = await asyncio.wait_for(r.get(LIVE_KEY), timeout=2.0)
        if raw:
            live = json.loads(raw)
            schema = merge_live(live.get("categories") or {}, committed())
            schema["source"] = "live"
            schema["fetched_at"] = live.get("fetched_at")
    except Exception as e:
        logger.debug(f"[filters] live schema unreadable ({type(e).__name__}) — the committed one")
    if schema is None:
        schema = _committed_cached()
    _active = (schema, time.monotonic())
    return schema


def forget() -> None:
    """Drop this process's copy, so the next current() asks again."""
    global _active
    _active = None


async def refresh(*, force: bool = False, slugs: Optional[Iterable[str]] = None,
                  gap: float = PAGE_GAP, client: Any = None,
                  sleep: Callable[[float], Any] = asyncio.sleep) -> Dict[str, Any]:
    """Read Divar's forms again unless a read younger than a day is in Redis.
    Returns the state it recorded: where the schema now comes from, when it
    was read, which pages failed, and what changed against the committed file.
    """
    r = await _redis()
    if not force:
        raw = await r.get(LIVE_KEY)
        if raw:
            try:
                age = time.time() - float(json.loads(raw).get("at") or 0)
            except (ValueError, TypeError):
                age = LIVE_MAX_AGE
            if age < LIVE_MAX_AGE:
                return await state()
    base = committed()
    wanted = list(slugs or sorted((base.get("categories") or {})))
    got, names, failed = await read_divar(wanted, gap=gap, client=client, sleep=sleep)
    record: Dict[str, Any] = {"checked_at": _now_iso(), "failed": failed}
    if not got:
        # Divar did not answer at all: the committed file stays in charge,
        # and a live read from earlier is left to expire on its own.
        record.update(source="committed", changes=[], fetched_at=None)
        logger.warning(f"[filters] Divar gave no filter form ({len(failed)} pages) — "
                       "using the committed schema")
    else:
        live = {"at": time.time(), "fetched_at": _now_iso(), "categories": got, "names": names}
        await r.set(LIVE_KEY, json.dumps(live, ensure_ascii=False), ex=LIVE_TTL)
        diff = changes(base, merge_live(got, base))
        for slug, name in names.items():
            token = ((base.get("categories") or {}).get(slug) or {}).get("token")
            if token and name != token:
                diff.append(f"{slug}: نام داخلی دیوار «{name}» است، نه «{token}»")
        record.update(source="live", changes=diff, fetched_at=live["fetched_at"])
        if diff:
            logger.warning(f"[filters] Divar's filters differ from the committed schema: {diff}")
        else:
            logger.info(f"[filters] Divar's filters match the committed schema ({len(got)} categories)")
    await r.set(STATE_KEY, json.dumps(record, ensure_ascii=False), ex=LIVE_TTL)
    forget()
    return record


async def state() -> Dict[str, Any]:
    """What the runtime card shows: where the schema comes from, and what a
    live read found different from the committed file. Never raises."""
    base: Dict[str, Any] = {"source": "committed", "fetched_at": None, "checked_at": None,
            "failed": [], "changes": [],
            "committed_read_on": _committed_cached().get("read_on")}
    try:
        r = await _redis()
        raw = await r.get(STATE_KEY)
        if raw:
            base.update(json.loads(raw))
    except Exception as e:
        base["error"] = f"{type(e).__name__}"
    return base


async def refresh_loop() -> None:
    """Runs for the life of the process. DIVAR_FILTER_SCHEMA_HOURS=0 disables."""
    from app.config import get_settings
    every = float(getattr(get_settings(), "divar_filter_schema_hours", 24) or 0)
    if every <= 0:
        logger.info("[filters] live schema read disabled — the committed schema is used")
        return
    await asyncio.sleep(300)         # let startup finish
    from app.services.supervisor import beat
    while True:
        beat("divar_filters")
        try:
            await refresh()
        except Exception as e:
            logger.warning(f"[filters] live schema read failed: {type(e).__name__}: {e}")
        await asyncio.sleep(3600)    # a read younger than a day is kept, so this is cheap

"""
یابندهٔ معنایی و تکراری‌یاب — every listing's text as a vector, and what that buys.

The exact filters (city, type, price, rooms) find what a form can describe.
Three things they cannot:

  * a customer's own words — «دو خواب نزدیک دانشگاه، آفتاب‌گیر، بدون همسایهٔ
    مزاحم» — against the listings' own words (semantic_candidates);
  * one more signal for «ملک‌های مشابه»: two ads that read alike are alike
    in ways the numbers do not carry (text_similarity);
  * the same flat posted twice by two consultants under two titles — flagged
    as «احتمالاً تکراری», never merged, never deleted (find_duplicates).

Vectors come from the gateway's embed job through app/services/llm.py — the
one door: it masks phone numbers and e-mail addresses itself, applies the
daily cap and writes the ledger. Nothing here calls the gateway directly.

Scale today is thousands of listings, so a vector is a JSON list on the
property row and similarity is a numpy dot product over a matrix built per
call (load_index). That is milliseconds at this size. pgvector — a vector
column, an HNSW index, ORDER BY <=> in SQL — is the step for when the
per-call matrix is no longer cheap; the functions keep their signatures.

Off the request path on purpose (embed_loop): a listing arrives, is scored
by the match engine, and gets its vector a few minutes later.
"""
import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

import numpy as np
from loguru import logger
from sqlalchemy import and_, func, or_, select

from app.config import get_settings
from app.database import async_session_maker
from app.models.property import Property
from app.services import llm, secret_box
from app.services.match_service import _comparable, district_key

settings = get_settings()

EMBED_VERSION = 2            # bump when the text or the model changes: old vectors are then ignored
DUPLICATE_THRESHOLD = 0.95   # cosine at or above which two ads read as one listing
DUPLICATE_PRICE_BAND = 0.05  # …and the comparable price within ±5%
BATCH = 32                   # texts per embed call
MAX_CHARS = 6000             # per listing; the gateway's own cut is 8000 after masking
LIMIT = 200                  # listings per pass
TICK_SECONDS = 180
KEY_CURSOR = "ai_embed_cursor"
AGENT = "embed"
# Not the listing's fault — see app/ai/listing_reader.py's _GATEWAY_STATE,
# the same defensive reference for classes another stream is adding to
# app/services/llm.py right now.
_QUIET = (llm.NotConfigured, llm.Disabled, llm.BudgetExceeded) \
    + tuple(getattr(llm, n) for n in ("CircuitOpen", "RateLimited") if hasattr(llm, n))

_DEAL_FA = {"buy": "فروش", "rent": "رهن و اجاره"}
_KIND_FA = {"apartment": "آپارتمان", "house": "خانه", "land": "زمین",
           "shop": "مغازه", "office": "دفتر", "other": "سایر"}


def _loaded_vec(prop) -> Optional[List[float]]:
    """`prop.ai_embedding` without ever triggering a lazy load of the
    deferred column. In async SQLAlchemy that load has no event loop to run
    on and raises MissingGreenlet; a caller that fetched a Property without
    undefer(Property.ai_embedding) gets None here instead of a crash — the
    same answer as "not embedded yet", which is the safe way to be wrong.
    Works on a plain object too (tests use SimpleNamespace)."""
    from sqlalchemy import inspect as sa_inspect
    state = sa_inspect(prop, raiseerr=False)
    if state is not None and "ai_embedding" in state.unloaded:
        return None
    return getattr(prop, "ai_embedding", None)


# ── the text ─────────────────────────────────────────────────────────────────

def text_of(prop) -> str:
    """What the model reads for one listing: title, where, what, the ad's
    own words, and the reader's facts when another agent has extracted them
    (Property.ai_facts is optional — it may not exist yet).

    The price is left out on purpose: the same flat at two asking prices
    must still read as one flat, and the price gates are exact anyway.
    Deterministic — the same row gives the same text, so a re-run does not
    re-embed. Masking is not done here: llm.embed masks everything that
    leaves, in one place, for every agent."""
    lines: List[str] = [(prop.title or "").strip()]
    where = " · ".join(x for x in ((prop.district or prop.neighborhood or "").strip(),
                                    (prop.city_name or "").strip()) if x)
    if where:
        lines.append(where)
    what = [(prop.property_type or prop.category_name or "").strip()]
    if prop.area:
        what.append(f"{prop.area} متر")
    if prop.rooms is not None:
        what.append(f"{prop.rooms} خواب")
    what.append(_DEAL_FA.get(prop.listing_type or "", ""))
    lines.append(" · ".join(x for x in what if x))
    if prop.description:
        lines.append(str(prop.description).strip())
    facts = getattr(prop, "ai_facts", None)
    if isinstance(facts, dict):
        summary = facts.get("summary")
        if summary:
            lines.append(str(summary).strip())
        # What the reader (app/ai/listing_reader.py: ListingFacts) actually
        # writes — kind can disagree with the scraped type on purpose (a
        # house filed as «مغازه» because it suits a café), document and
        # condition are already Persian words, suitable_for is a list of
        # them. The deal flags read the same as the glossary they came from.
        words = []
        kind = _KIND_FA.get(facts.get("kind"))
        if kind:
            words.append(kind)
        for key in ("document", "condition"):
            if facts.get(key):
                words.append(str(facts[key]))
        for key, fa in (("convertible", "قابل تبدیل"), ("exchange", "معاوضه"),
                        ("vacant", "تخلیه"), ("negotiable", "قابل مذاکره"),
                        # what a person searches with: «با آسانسور و پارکینگ»
                        ("has_elevator", "آسانسور"), ("has_parking", "پارکینگ"),
                        ("has_storage", "انباری"), ("has_balcony", "بالکن")):
            if facts.get(key):
                words.append(fa)
        if words:
            lines.append("، ".join(words))
        suitable = facts.get("suitable_for")
        if isinstance(suitable, (list, tuple)) and suitable:
            lines.append("مناسب " + "، ".join(str(s) for s in suitable))
        # a flag is part of what the listing is (a shared deed, a bank lien),
        # so two ads of one flat read alike and a search for it finds it
        flags = facts.get("red_flags")
        if isinstance(flags, (list, tuple)) and flags:
            lines.append("، ".join(str(f) for f in flags))
    return "\n".join(x for x in lines if x)[:MAX_CHARS]


# ── the arithmetic ───────────────────────────────────────────────────────────

def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity, 0.0 when either vector is empty or all zeros."""
    x, y = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    if x.size == 0 or x.shape != y.shape:
        return 0.0
    n = float(np.linalg.norm(x) * np.linalg.norm(y))
    return float(x @ y / n) if n else 0.0


class Matrix(NamedTuple):
    """One index, built once: the ids in row order, the vectors as one
    array, and their norms (zeros replaced with 1 so dividing by them is
    safe). What a pass over many listings (mark_duplicates) or many queries
    against one index (the request-path cache below) reuses instead of
    redoing the same np.asarray + np.linalg.norm on every single() call."""
    ids: List[int]
    vecs: np.ndarray
    norms: np.ndarray


def build_matrix(rows: List[Tuple[int, Sequence[float]]]) -> Optional[Matrix]:
    """`load_index`'s (id, vector) rows → one Matrix, or None when there is
    nothing to search (empty, or every vector a foreign dimension)."""
    if not rows:
        return None
    d = len(rows[0][1] or ())
    # a vector of another length is from another model: not comparable, so skipped
    kept = [(pid, vec) for pid, vec in rows if vec is not None and len(vec) == d]
    if d == 0 or not kept:
        return None
    vecs = np.asarray([vec for _, vec in kept], dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1)
    norms[norms == 0] = 1.0
    return Matrix([pid for pid, _ in kept], vecs, norms)


def nearest_in(query_vec: Sequence[float], matrix: Optional[Matrix], limit: int = 30) -> List[Tuple[int, float]]:
    """The `limit` closest rows in an already-built Matrix, as (id, cosine),
    best first — the per-query cost once the index is built, which is what
    makes scoring many queries (or many listings) against one index cheap."""
    if matrix is None or limit <= 0:
        return []
    q = np.asarray(query_vec, dtype=np.float32)
    if q.size == 0 or q.shape[0] != matrix.vecs.shape[1]:
        return []
    qn = float(np.linalg.norm(q)) or 1.0
    scores = (matrix.vecs @ q) / (matrix.norms * qn)
    order = np.argsort(-scores)[:limit]
    return [(matrix.ids[i], float(scores[i])) for i in order]


def nearest(query_vec: Sequence[float], rows: List[Tuple[int, Sequence[float]]],
            limit: int = 30) -> List[Tuple[int, float]]:
    """The `limit` closest rows to the query, as (id, cosine), best first —
    one matrix, one normalisation, one product, vectorised over the whole
    index, which is what makes thousands of rows cheap without pgvector.
    A single-shot convenience over build_matrix + nearest_in; a caller
    scoring more than one query against the same `rows` should build the
    Matrix itself once and call nearest_in directly (mark_duplicates and
    semantic_candidates's request-path cache both do)."""
    return nearest_in(query_vec, build_matrix(rows), limit)


def text_similarity(prop_a, prop_b) -> Optional[float]:
    """Cosine of the two stored vectors; None when either listing has none
    (or they are from different versions and cannot be compared), including
    when a caller's query never loaded the deferred column — see
    _loaded_vec."""
    a, b = _loaded_vec(prop_a), _loaded_vec(prop_b)
    if not a or not b or len(a) != len(b):
        return None
    if getattr(prop_a, "ai_embed_version", None) != getattr(prop_b, "ai_embed_version", None):
        return None
    return cosine(a, b)


# ── the vectors on the rows ──────────────────────────────────────────────────

async def embed_properties(db, props: Sequence[Property]) -> int:
    """Vectors for `props`, BATCH at a time, committed per batch so a pass
    that dies on the fourth batch keeps the first three. Raises the
    gateway's LLMError after the batches before it are committed; the
    caller counts what landed."""
    global _generation
    done = 0
    for i in range(0, len(props), BATCH):
        chunk = props[i:i + BATCH]
        vectors = await llm.embed([text_of(p) for p in chunk], agent=AGENT, db=db)
        if len(vectors) != len(chunk):
            raise llm.LLMError(f"embedding: {len(vectors)} vectors for {len(chunk)} texts")
        now = datetime.now(timezone.utc)
        for p, vec in zip(chunk, vectors):
            p.ai_embedding = [float(x) for x in vec]
            p.ai_embedded_at = now
            p.ai_embed_version = EMBED_VERSION
            p.ai_embed_fp = p.ai_content_fp
        await db.commit()
        done += len(chunk)
        _generation += 1   # the request-path matrix cache is now stale
    return done


async def load_index(db, *, city: Optional[str] = None, listing_type: Optional[str] = None,
                     exclude_id: Optional[int] = None) -> List[Tuple[int, List[float]]]:
    """(id, vector) for every active listing with a current-version vector —
    the «index», rebuilt per call on purpose: thousands of JSON lists decode
    in tens of milliseconds, and no cache can go stale. The pgvector step
    replaces this with a SQL ORDER BY when it is no longer cheap."""
    q = select(Property.id, Property.ai_embedding).where(
        Property.is_active == True,                            # noqa: E712
        Property.ai_embedding.isnot(None),
        Property.ai_embed_version == EMBED_VERSION)
    if city:
        q = q.where(Property.city_name == city)
    if listing_type:
        q = q.where(Property.listing_type == listing_type)
    if exclude_id is not None:
        q = q.where(Property.id != exclude_id)
    return [(pid, vec) for pid, vec in (await db.execute(q)).all() if vec]


# ponytail: per-process cache of (city, listing_type) → Matrix, for request-
# path callers that score more than one query against the same index in a
# short span (matches_for_customer, the /ai/embed/search route). Keyed and
# bounded by _CACHE_TTL, not shared across pods/workers — a multi-pod deploy
# can serve a matrix up to _CACHE_TTL stale from another pod's embed pass;
# fine at today's scale (thousands of rows, a cosine search that is
# advisory, not a source of truth). Move to pgvector (a real index, no
# per-process copy) or a shared cache if that staleness ever matters.
_CACHE_TTL = 180.0
_cache: Dict[Tuple[Optional[str], Optional[str]], Tuple[Optional[Matrix], float, int]] = {}
_generation = 0     # bumped by embed_properties; a stale entry is dropped even inside the TTL


async def _cached_matrix(db, *, city: Optional[str], listing_type: Optional[str]) -> Optional[Matrix]:
    key = (city, listing_type)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit is not None and hit[2] == _generation and now - hit[1] < _CACHE_TTL:
        return hit[0]
    matrix = build_matrix(await load_index(db, city=city, listing_type=listing_type))
    _cache[key] = (matrix, now, _generation)
    return matrix


async def semantic_candidates(db, text: str, *, city: Optional[str] = None,
                              listing_type: Optional[str] = None, limit: int = 30
                              ) -> List[Tuple[int, float]]:
    """A free-text need → the nearest listings as (property_id, cosine).
    The ids are candidates, not answers: the caller runs them through the
    same gates as everything else (customer_wants). Raises LLMError when
    the gateway will not embed — the caller carries on without. The index
    itself comes from the request-path cache above, not a fresh load_index
    every call."""
    text = (text or "").strip()
    if not text:
        return []
    vec = (await llm.embed([text[:MAX_CHARS]], agent=AGENT, db=db))[0]
    return nearest_in(vec, await _cached_matrix(db, city=city, listing_type=listing_type), limit)


# ── duplicates ───────────────────────────────────────────────────────────────

def _price_within(a: Optional[int], b: Optional[int], band: float) -> bool:
    """Both «توافقی» is one price; one of them is not; both given must be close."""
    if not a and not b:
        return True
    if not a or not b:
        return False
    return abs(a - b) / max(a, b) <= band


async def find_duplicates(db, prop: Property, index: Optional[Matrix] = None) -> List[Dict[str, Any]]:
    """Listings that are probably this one posted again: nearest by text
    within the same city and deal type, cosine ≥ DUPLICATE_THRESHOLD, the
    same district (or none recorded on either), and the comparable price
    within ±DUPLICATE_PRICE_BAND. Returns [{id, score, serial_no, title}],
    best first. `index` is a Matrix (build_matrix's return) — mark_duplicates
    builds one for the whole batch and passes it in; the default None builds
    one just for this listing's own city/type. The gates below are applied
    to the candidates either way."""
    vec = _loaded_vec(prop)
    if vec is None:
        # not None because it was checked and is empty, but because this
        # prop's own query never loaded the deferred column — one small
        # explicit fetch beats asking every caller to remember undefer()
        vec = (await db.execute(select(Property.ai_embedding).where(Property.id == prop.id))).scalar_one_or_none()
    if not vec:
        return []
    if index is None:
        index = build_matrix(await load_index(db, city=prop.city_name, listing_type=prop.listing_type, exclude_id=prop.id))
    near = [(pid, s) for pid, s in nearest_in(vec, index, limit=10)
            if pid != prop.id and s >= DUPLICATE_THRESHOLD]
    if not near:
        return []
    rows = {p.id: p for p in (await db.execute(
        select(Property).where(Property.id.in_([pid for pid, _ in near])))).scalars().all()}
    key, price = district_key(prop.district) or district_key(prop.neighborhood), _comparable(prop)
    out = []
    for pid, score in near:
        c = rows.get(pid)
        if c is None or not c.is_active:
            continue
        if c.city_name != prop.city_name or c.listing_type != prop.listing_type:
            continue
        ckey = district_key(c.district) or district_key(c.neighborhood)
        if ckey != key:                       # equal, or both empty
            continue
        if not _price_within(price, _comparable(c), DUPLICATE_PRICE_BAND):
            continue
        out.append({"id": c.id, "score": round(score, 4), "serial_no": c.serial_no, "title": c.title})
    return out


async def mark_duplicates(db, props: Sequence[Property]) -> int:
    """For each listing with a duplicate found, point it at the OLDER one
    (the lowest id) via ai_duplicate_of. A flag for a person to look at:
    nothing is merged, hidden or deleted here."""
    if not props:
        return 0
    index = build_matrix(await load_index(db))   # once for the whole batch, every city
    flagged = 0
    for p in props:
        try:
            dups = await find_duplicates(db, p, index=index)
        except Exception as e:
            logger.warning(f"[embed] duplicate check for listing {p.id} failed: {type(e).__name__}: {e}")
            continue
        older = min((d["id"] for d in dups), default=None)
        # the newer twin is the one flagged; an older listing found «again»
        # when the cursor was rewound is left as it is
        if older is not None and older < p.id and p.ai_duplicate_of != older:
            p.ai_duplicate_of = older
            flagged += 1
    if flagged:
        await db.commit()
    return flagged


# ── the pass and the loop ────────────────────────────────────────────────────

async def _cursor(db) -> int:
    try:
        raw = (await secret_box.get_many(db, (KEY_CURSOR,))).get(KEY_CURSOR)
        return int(raw) if raw else 0
    except Exception:
        return 0


def _due():
    """Never embedded, EMBED_VERSION moved on, or the vector is behind the
    content it should reflect: the content fingerprint changed since this
    row's ai_embed_fp was written, or the reader read it again since (its
    facts are part of text_of, so a fresher read means a stale vector even
    when the raw columns did not move — see
    app/models/property.py:content_fingerprint)."""
    changed = Property.ai_embed_fp.is_distinct_from(Property.ai_content_fp)
    reread_since = and_(Property.ai_read_at.isnot(None), Property.ai_embedded_at.isnot(None),
                        Property.ai_read_at > Property.ai_embedded_at)
    return and_(Property.is_active == True, Property.title.isnot(None), Property.title != "",
                or_(Property.ai_embedded_at.is_(None), Property.ai_embed_version != EMBED_VERSION,
                    changed, reread_since))


async def run_once(db, *, limit: int = LIMIT) -> Dict[str, Any]:
    """One pass: up to `limit` due listings (_due() — never embedded, a
    version bump, or the content/facts behind what was embedded), oldest id
    first, embedded a batch at a time. No id lower bound, same reasoning as
    listing_reader.run_once: a listing behind the stored cursor whose
    content changed is exactly as due as a new one, so _due() alone decides
    and the cursor below is reporting only. The cursor moves past what
    landed, so a batch the gateway refused is retried next pass; then the
    new ones are checked for duplicates. `stopped` names why the pass ended
    early, or is None."""
    since = await _cursor(db)
    props = (await db.execute(
        select(Property).where(_due())
        .order_by(Property.id.asc()).limit(limit))).scalars().all()
    out = {"scanned": len(props), "embedded": 0, "duplicates": 0, "cursor": since, "stopped": None}
    if not props:
        return out

    done: List[Property] = []
    for i in range(0, len(props), BATCH):
        chunk = props[i:i + BATCH]
        try:
            await embed_properties(db, chunk)
        except llm.LLMError as e:
            out["stopped"] = str(e) or type(e).__name__
            # the gateway refused before anything was written for this chunk,
            # so there is nothing to roll back — and a rollback would expire
            # the rows already embedded, which are read again below
            if isinstance(e, _QUIET):
                logger.info(f"[embed] pass paused: {e}")
            else:
                logger.warning(f"[embed] gateway failed on listings {chunk[0].id}..{chunk[-1].id}, retried next pass: {e}")
            break
        done.extend(chunk)

    if done:
        out["embedded"] = len(done)
        out["cursor"] = done[-1].id
        await secret_box.put(db, KEY_CURSOR, str(done[-1].id), "ai_embed")
        out["duplicates"] = await mark_duplicates(db, done)
        logger.info(f"[embed] {len(done)} listings embedded, {out['duplicates']} flagged as duplicates, cursor {done[-1].id}")
    return out


async def tick() -> Dict[str, Any]:
    async with async_session_maker() as db:
        try:
            # the agent's own switch, beside the global one (llm.agent_enabled)
            if not await llm.agent_enabled(db, "embed"):
                return {"skipped": "disabled"}
            return await run_once(db)
        except Exception as e:
            logger.warning(f"[embed] tick failed: {type(e).__name__}: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            return {"error": type(e).__name__}


async def embed_loop() -> None:
    """Runs for the life of the process. MATCH_ENGINE=0 disables — the
    vectors serve the matcher, so they are switched off together."""
    if not getattr(settings, "match_engine", True):
        logger.info("[embed] disabled")
        return
    await asyncio.sleep(200)          # let startup finish; after the match engine's first pass
    logger.info(f"[embed] armed — every {TICK_SECONDS // 60} min, {LIMIT} listings a pass, version {EMBED_VERSION}")
    from app.services.supervisor import beat
    while True:
        beat("embeddings")
        await tick()
        await asyncio.sleep(TICK_SECONDS)

# «یابندهٔ معنایی و تکراری‌یاب» — integration notes

Branch `ai/embed`. Own files: `app/ai/__init__.py`, `app/ai/embeddings.py`,
`app/api/routes/ai_embed.py`, `frontend/js/ai/embed.js`, `tests/test_ai_embed.py`,
plus four columns and two `to_dict()` lines in `app/models/property.py`
(block `# ── AI (app/ai/embeddings.py) ──`, right before `# Status`).
Everything below is what the shared files need; nothing here was applied.

## (a) the loop — `app/main.py`

Mirror `digest_loop`, right after the digest block in the lifespan:

```python
    # the listings' text as vectors: semantic search and the duplicate flag
    from app.ai.embeddings import embed_loop as _embed_loop
    embed_task = asyncio.create_task(_embed_loop())
```

and on shutdown, next to `digest_task.cancel()`: `embed_task.cancel()`.
It is gated on `settings.match_engine` like the engine and the digest
(MATCH_ENGINE=0 switches all three off), sleeps 200 s at start, runs every
180 s, 200 listings a pass, and never raises out of `tick()`.

## (b) the mounts — `app/api/routes/__init__.py`

Two routers in one module, two audiences. Add `ai_embed` to the import and:

```python
# root and super_admin, checked inside (a pass spends money): status and run.
router.include_router(ai_embed.router, prefix="/ai/embed", tags=["AI"])
# Any CRM user: «جستجوی معنایی», similar-by-text, the duplicate's explanation.
router.include_router(ai_embed.crm_router, prefix="/ai/embed", tags=["AI"], dependencies=_perm("crm"))
```

Routes: `GET /ai/embed/status`, `POST /ai/embed/run?limit=` (≤ 500) —
super_admin; `POST /ai/embed/search` `{text, city?, listing_type?, limit?}`,
`GET /ai/embed/similar/{property_id}?limit=` (≤ 30),
`GET /ai/embed/duplicates/{property_id}` — CRM. Search answers 502 with the
gateway's Persian reason when the model is unconfigured / off / capped.

Optional, for the AI card: in `app/api/routes/ai.py` the `embed` agent entry
can flip to `"live": True`.

## (c) revision 0006 — `migrations/versions/0006_property_embeddings.py`

Same guarded shape as `0003_job_accounts_used.py` (create_all may already
have built the columns). `down_revision = "0005"` if the facts worker's
revision lands first, else `"0004"`.

```python
"""a listing's text as a vector, and the duplicate flag

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-22

app/ai/embeddings.py: the vector as JSON on the row (pgvector is the later
step), when and with which version it was written, and the OLDER listing
this one seems to repeat — a flag for a person, never a merge.
Guarded like 0003: create_all may already have built the columns.
"""
from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLE = "properties"
COLUMNS = (
    sa.Column("ai_embedding", sa.JSON(), nullable=True),
    sa.Column("ai_embedded_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("ai_embed_version", sa.Integer(), nullable=True),
    sa.Column("ai_duplicate_of", sa.Integer(), nullable=True),
)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns(TABLE)}
    for col in COLUMNS:
        if col.name not in have:
            op.add_column(TABLE, col)
    if "ix_properties_ai_duplicate_of" not in {i["name"] for i in insp.get_indexes(TABLE)}:
        op.create_index("ix_properties_ai_duplicate_of", TABLE, ["ai_duplicate_of"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_properties_ai_duplicate_of", table_name=TABLE)
    for col in reversed(COLUMNS):
        op.drop_column(TABLE, col.name)
```

The index name is SQLAlchemy's default for `index=True`, so `alembic check`
(tests/test_pg_migration.py) sees no drift.

## (d) `app/services/match_service.py`

**d1 — «متن مشابه» in `score_similarity`**, a 10-weight part after the
amenities block (raw cosine of two real ads sits around 0.5–0.7; a near
rewrite above 0.9 — so the value is rescaled from 0.5..1 to 0..1):

```python
    # «متن مشابه»: how alike the two ads read, when both carry a vector
    # (app/ai/embeddings.py). Wording, not numbers — a nudge, never a gate.
    from app.ai.embeddings import text_similarity
    txt = text_similarity(target, cand)
    if txt is not None:
        parts.append((10, max(0.0, min(1.0, (txt - 0.5) / 0.5)), "متن مشابه"))
```

The reason string shows when the part is above 0.5 (cosine > 0.75), like
every other part. Add `"متن مشابه"` to whatever list of reason strings the
frontend styles, if any.

**d2 — semantic candidates in `matches_for_customer`**, after `cands = ...`
and before the scoring loop; gated on `use_llm` so `use_llm=false` and the
engine's offline pass stay free:

```python
SEMANTIC_EXTRA = 30      # candidates the customer's own words add to the pool
...
    sem: Dict[int, float] = {}
    if use_llm:
        # the customer's own words → the listings that read closest, on top
        # of the pool; still gated below, so nothing enters that the exact
        # filters would have refused. The gateway masks the text; the name
        # is never sent.
        need = " ".join(filter(None, (customer.desired_specs, customer.desired_district,
                                      getattr(customer, "notes", None)))).strip()
        if need:
            from app.ai import embeddings as _emb
            from app.services import llm as _llm
            try:
                sem = dict(await _emb.semantic_candidates(
                    db, need, city=city, listing_type=intent["listing_type"], limit=SEMANTIC_EXTRA))
            except _llm.LLMError as e:
                logger.info(f"[match] semantic candidates skipped: {e}")
            except Exception as e:
                logger.warning(f"[match] semantic candidates failed: {type(e).__name__}: {e}")
            missing = [pid for pid in sem if pid not in {c.id for c in cands}]
            if missing:
                cands = list(cands) + (await db.execute(
                    select(Property).where(Property.id.in_(missing), Property.is_active == True)   # noqa: E712
                )).scalars().all()
```

and inside the loop, after `s = score_for_customer(customer, c)`:

```python
        if c.id in sem:
            s["reasons"].append("شباهت متن")
```

`customer_wants` already runs on every candidate, so the extras pass the
same deal-type / city / family / rooms gates. Cap is `SEMANTIC_EXTRA`.

## (e) the panel

**Script tag** — `frontend/index.html`, after app.js (the file only uses
app.js globals at call time):

```html
<script src="js/app.js?v=20260922d"></script>
<script src="js/ai/embed.js?v=20260922d"></script>
```

If a button in index.html calls `aiOpenSemanticSearch(...)`, either add a
one-line wrapper in app.js or teach `tests/test_frontend_wiring.py`
(`_defined`) to read `frontend/js/ai/*.js` too — it only scans app.js.

**Badge anchors** — `aiDuplicateBadge(obj)` returns '' unless
`obj.ai_duplicate_of` is set, so it can sit inline:

1. Leads table, `async function loadLeads` in `frontend/js/app.js`, the title
   cell: `${esc((lead.property_title || '---').substring(0, 35))}... ${agencyBadge(lead)}`
   → append ` ${aiDuplicateBadge(lead)}`. For the field to be there:
   `app/schemas/__init__.py` `LeadResponse` gets
   `ai_duplicate_of: Optional[int] = None`, and `_attach_property_columns`
   in `app/api/routes/crm.py` selects `Property.ai_duplicate_of` and copies
   `item.ai_duplicate_of = p.ai_duplicate_of`.
2. Property modal, `async function viewProperty`: after
   `<h5 class="mb-3">${esc(property.title)}</h5>` add
   `${aiDuplicateBadge(property)}`. `GET /properties/{id}` returns
   `PropertyResponse`, so `app/schemas/__init__.py` `PropertyResponse` gets
   `ai_duplicate_of: Optional[int] = None` (the model's `to_dict()` already
   carries it for `property_detail` on the lead modal: in `viewLead`,
   `${aiDuplicateBadge(lead.property_detail || {})}` next to `agencyBadge(lead)`).

**Semantic search entry point** — `aiOpenSemanticSearch(text, {city, listing_type})`
renders into the existing `#matchModal` with `_matchCard`; a text box on the
CRM customers tab (or `showMatchesForCustomer`'s modal header) is enough.
`aiSemanticSearch(text)` returns the items, `aiRenderSemanticResults(el, items)`
draws them, for any other placement.

**CSS** — `frontend/css/style.css`, next to `.serial-badge`:

```css
/* «احتمالاً تکراری» — the embeddings' duplicate flag (frontend/js/ai/embed.js) */
.ai-dup-badge {
  display: inline-flex; align-items: center; gap: .3rem;
  font-size: .68rem; font-weight: 600; padding: .12rem .5rem;
  border-radius: var(--r-sm); color: var(--accent2);
  background: rgba(6,182,212,.1); border: 1px solid rgba(6,182,212,.3);
  vertical-align: middle; white-space: nowrap;
}
.ai-dup-badge a { color: var(--text-muted); text-decoration: underline dotted; }
.ai-dup-badge a:hover { color: var(--accent2); }
```

## (f) pgvector — later

Vectors are JSON on the row and `load_index()` rebuilds a numpy matrix per
call: thousands of rows decode in tens of milliseconds and nothing can go
stale. When that stops being cheap: `CREATE EXTENSION vector`, a
`vector(1536)` column with an HNSW index, and `load_index`/`nearest`
become one `ORDER BY ai_vec <=> :q LIMIT n`. Signatures stay; `EMBED_VERSION`
is bumped whenever the text or the model changes, and old vectors are then
ignored, never compared.

## (g) not done, and why

- The migration file, the mounts, the loop registration, the schema fields
  and the panel anchors — shared files, described above instead.
- The AI card's `embed` agent still says «به‌زودی» (`app/api/routes/ai.py`).
- No Postgres run: the sqlite suite is green (`tests/test_ai_embed.py`,
  `test_ai_core.py`, `test_similar_listings.py`, then the whole suite).
  `test_pg_migration.py::test_alembic_stamps_at_boot_and_models_match_the_schema`
  needs revision 0006 in place before the next Postgres run, or it reports
  the four columns as drift on the established path.
- `Property.ai_facts` is read with `getattr(..., None)`; when the facts
  worker's column lands, `text_of()` picks up `summary` and `flags`
  (list, or `{name: bool}`) without a change here — but any listing
  embedded before then keeps its old text until `EMBED_VERSION` is bumped
  (which re-embeds everything on the next passes).

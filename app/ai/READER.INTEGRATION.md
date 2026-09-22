# خوانندهٔ آگهی — integration notes

Branch `ai/reader`. Everything the reader needs that lives in shared files, as one-line hooks.
Files this branch owns: `app/ai/__init__.py`, `app/ai/listing_reader.py`, `app/api/routes/ai_reader.py`,
`scripts/ai_label_sheet.py`, `scripts/ai_bakeoff.py`, `frontend/js/ai/reader.js`, `tests/test_ai_reader.py`;
edits: `app/models/property.py` (two columns + `to_dict`), `app/services/llm.py` (one kwarg, see h).

## a. the loop — `app/main.py`

Next to the digest, in `lifespan`:

```python
    # Every listing's text is read once by the model: kind, floor, document,
    # the deal flags, red flags — for matching and «ملک‌های مشابه».
    from app.ai.listing_reader import reader_loop as _reader_loop
    reader_task = asyncio.create_task(_reader_loop())
```

and in the cleanup block, first line: `reader_task.cancel()`.
It sleeps 150 s at start, runs every 120 s, 50 listings a pass, and returns at once when `MATCH_ENGINE=0`.

## b. the router — `app/api/routes/__init__.py`

Import `ai_reader` in the `from app.api.routes import (...)` list, then under the `ai.router` line:

```python
# Same: every call here spends money.
router.include_router(ai_reader.router, prefix="/ai/reader", tags=["AI"])
```

Routes (root / super_admin, checked inside like `ai.py`): `GET /ai/reader/status`, `POST /ai/reader/run?limit=` (≤ 200), `POST /ai/reader/{property_id}`.

## c. the panel

**Schema** (`app/schemas/__init__.py`, `PropertyResponse`, next to `scraped_at`) — without this `GET /properties/{id}` drops the columns and the modal shows «هنوز خوانده نشده» for everything:

```python
    ai_facts: Optional[dict] = None              # what the listing's text says (app/ai/listing_reader.py)
    ai_read_at: Optional[datetime] = None
```

**Script tag** (`frontend/index.html`, right after the `app.js` line 4965):

```html
<script src="js/ai/reader.js?v=20260922a"></script>
```

**HTML** (`frontend/js/app.js`, inside `viewProperty()`'s template, between the `<!-- Location -->` card and the `<!-- Description -->` card — the facts sit right above the text they were read from):

```html
                <div id="ai-facts" class="ai-facts" data-id="${property.id}" data-read-at="${property.ai_read_at || ''}"></div>
```

and one line after `modal.innerHTML = \`…\`;`, before `modalElement.show()`:

```js
        aiRenderFacts(document.getElementById('ai-facts'), property.ai_facts);
```

The «بازخوانی» button is drawn only for root / super_admin (`_currentUser.role`); it POSTs `/ai/reader/{id}` and re-renders in place.

## d. CSS — append to `frontend/css/style.css` (after the `.ai-usage-wrap` rules)

```css
/* برداشت هوش مصنوعی — the reader's chips in the property modal (js/ai/reader.js) */
.ai-facts { margin-bottom: 1rem; padding: .8rem .9rem; border: 1px solid var(--border); border-radius: var(--r-sm); background: var(--surface2) }
.ai-facts-head { display: flex; align-items: center; justify-content: space-between; gap: .6rem; margin-bottom: .5rem }
.ai-facts-title { font-size: .86rem; font-weight: 700; color: var(--accent2) }
.ai-facts-reread { font-size: .72rem; padding: .15rem .6rem; border-radius: var(--r-sm) }
.ai-facts-summary { font-size: .84rem; color: var(--text-dim); line-height: 1.8; margin: 0 0 .55rem }
.ai-facts-chips { display: flex; flex-wrap: wrap; gap: .35rem }
.ai-facts-empty { font-size: .78rem; color: var(--text-muted) }
.ai-facts-meta { font-size: .68rem; color: var(--text-muted); margin-top: .5rem; direction: ltr; text-align: end }
.ai-chip { display: inline-flex; align-items: center; gap: .3rem; padding: .18rem .6rem; border-radius: 20px; font-size: .74rem; background: var(--surface3); border: 1px solid var(--border); color: var(--text-dim); cursor: default }
.ai-chip-l { color: var(--text-muted); font-size: .68rem }
.ai-chip.is-amenity { border-color: rgba(6,182,212,.35); color: var(--accent2) }
.ai-chip.is-off { opacity: .6 }
.ai-chip.is-deal { background: rgba(99,102,241,.16); border-color: rgba(99,102,241,.5); color: #c7d2fe; font-weight: 600 }
.ai-chip.is-use { border-color: rgba(16,185,129,.35); color: var(--accent3) }
.ai-chip.is-warn { background: rgba(245,158,11,.14); border-color: rgba(245,158,11,.5); color: var(--warning) }
```

## e. revision 0005 — `migrations/versions/0005_property_ai_facts.py`

Same shape as `0003_job_accounts_used.py` (guarded: `create_all` may already have built the columns on a fresh database):

```python
"""the listing reader's facts

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-22

properties.ai_facts (JSON): what the listing's own text says — kind, floor,
document, condition, the deal flags, red flags, a confidence per field, the
prompt version that read it. properties.ai_read_at (timestamptz): when.
NULL until app/ai/listing_reader.py has been through. Guarded like 0003.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TABLE = "properties"


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(TABLE)}
    if "ai_facts" not in have:
        op.add_column(TABLE, sa.Column("ai_facts", sa.JSON(), nullable=True))
    if "ai_read_at" not in have:
        op.add_column(TABLE, sa.Column("ai_read_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, "ai_read_at")
    op.drop_column(TABLE, "ai_facts")
```

No index: the pass filters on `id > cursor` (the primary key) and `ai_read_at IS NULL` over at most 50 rows; the status counts run once per panel open. Add `ix_properties_ai_read_at` only if `status` ever shows in slow logs.
Until 0005 exists, `tests/test_pg_migration.py::test_alembic_stamps_at_boot_and_models_match_the_schema` (Postgres mode) will report the two columns as drift.

## f. `effective()` in `app/services/match_service.py`

`app/ai/listing_reader.effective(prop)` returns `{kind, floor, total_floors, year_built, district, has_elevator, has_parking, has_storage, has_balcony, document, condition, convertible, exchange, vacant, negotiable, suitable_for, red_flags}` — the scraped column when set, the fact where the scraper left a gap (a scraped `False` counts as a gap; a scraped `True` is never taken away); `kind` is `property_family(prop)` unless that is `None`, or the fact disagrees with confidence ≥ 0.8. `effective` imports `property_family` lazily, so `match_service` may import it at module level without a cycle.

```diff
 from app.config import get_settings
 from app.models.property import Property
+from app.ai.listing_reader import effective
```

```diff
 def score_similarity(target: Property, cand: Property) -> Dict[str, Any]:
     """Weighted similarity of `cand` to `target`. Returns score 0..100 + reasons."""
     parts: List[tuple] = []   # (weight, value 0..1, reason)
+    te, ce = effective(target), effective(cand)
 ...
     shape_penalty = 1.0
     if target.listing_type == "rent" and cand.listing_type == "rent":
         td, cd = target.deposit or 0, cand.deposit or 0
         if td and cd:
             shape = _closeness(td, cd, DEPOSIT_TOLERANCE)
             parts.append((15, shape, "ودیعه نزدیک" if shape > .5 else "ودیعهٔ متفاوت"))
-            if max(td, cd) / min(td, cd) > DEPOSIT_SHAPE_MAX:
+            # «قابل تبدیل» in either ad: the owner said the shape is negotiable
+            if max(td, cd) / min(td, cd) > DEPOSIT_SHAPE_MAX and not (te["convertible"] or ce["convertible"]):
                 shape_penalty = 0.6
 ...
     family_penalty = 1.0
-    tf, cf = property_family(target), property_family(cand)
+    tf, cf = te["kind"], ce["kind"]
 ...
-    wanted = [(f, fa) for f, fa in amen if getattr(target, f, False)]
+    wanted = [(f, fa) for f, fa in amen if te.get(f)]
     if wanted:
-        have = [fa for f, fa in wanted if getattr(cand, f, False)]
+        have = [fa for f, fa in wanted if ce.get(f)]
```

```diff
 def customer_wants(customer, cand: Property, intent: Optional[Dict[str, Any]] = None) -> bool:
 ...
-    fam = property_family(cand)
+    fam = effective(cand)["kind"]
```

```diff
 def rank_similar(prop: Property, cands, limit: int = 12) -> List[Dict[str, Any]]:
 ...
-    target_family = property_family(prop)
+    target_family = effective(prop)["kind"]
     rows = []
     for c in cands:
         if c.id == prop.id:
             continue
-        if target_family and property_family(c) not in (None, target_family):
+        if target_family and effective(c)["kind"] not in (None, target_family):
             continue
```

Also worth surfacing, not required: in `_brief()` add `"ai_summary": (p.ai_facts or {}).get("summary")` and `"red_flags": (p.ai_facts or {}).get("red_flags") or []` so the match cards can show the one line and the warnings.
`tests/test_ai_core.py::test_every_caller_masks` counts `_llm.mask_pii(` in match_service — the patch adds no unmasked call.

## g. running the label sheet and the bake-off

Inside the backend pod (or locally with `DATABASE_URL`, `LLM_API_KEY`, `LLM_BASE_URL` set):

```
venv/bin/python scripts/ai_label_sheet.py -n 30 > labels.md         # newest 30 listings, facts beside them
venv/bin/python scripts/ai_label_sheet.py --ids 1042 1043 1050
```

A person corrects the `facts` column, then writes the answers as `labels.json` — `{"1042": {"kind": "shop", "floor": 1, "document": null, "suitable_for": ["کافه"]}}`; `null` means «the text does not say», lists compare as sets. Then:

```
venv/bin/python scripts/ai_bakeoff.py labels.json z-ai/glm-5.3-flash openai/gpt-4.1-mini google/gemini-2.5-flash
```

Prints hits/total per field per model, failures and p50 latency. Every call goes through `llm.chat` with `model_override` under agent `bakeoff` (masked text, one ledger row per call, the daily cap applies — raise the cap on the AI card for the afternoon of the bake-off). Nothing is stored on the listings.

Bumping `PROMPT_VERSION` after corrections: the cursor value carries the version (`1:1234`), so a new version restarts from 0 and re-reads every listing, oldest first, 50 per two minutes, stopping at the cap each day. Nothing to reset by hand.

## h. what was not done, and why

* `app/services/llm.py` got one keyword-only parameter, `model_override: Optional[str] = None`, used in one line (`model = model_override or …`). There was no other way to pass the bake-off's model down without monkeypatching `llm.config` from a script; the parameter is documented as bake-off only and the ledger records the model actually used.
* `max_tokens` is 600, not 400: the third few-shot's own answer is ~350 tokens of Persian JSON, and 400 truncates a listing with a long red-flags list into a retry that fails the same way.
* Not written by this branch, by instruction: the loop registration, the router mount, the schema fields, the script tag, the modal snippet, the CSS, revision 0005, the match_service patch. All are above, verbatim.
* Postgres-mode tests were not run (sqlite suite only, as asked).

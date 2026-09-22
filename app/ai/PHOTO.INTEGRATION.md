# برچسب‌زن عکس — integration notes (branch `ai/photo`)

What is on the branch: `app/ai/photo_tagger.py` (the agent), `app/api/routes/ai_photo.py`
(the router), `frontend/js/ai/photo.js` (the chips and the card), `tests/test_ai_photo.py`,
and two columns on `Property` in their own `# ── AI (app/ai/photo_tagger.py) ──` block
(+ two `to_dict()` lines). No shared file was touched.

## (a) the loop — `app/main.py`, after the digest block

```python
    # What the vision model sees in each listing's first photos: renovated,
    # furnished, a floor plan instead of a picture, an agency's logo.
    from app.ai.photo_tagger import photo_loop as _photo_loop
    photo_task = asyncio.create_task(_photo_loop())
```

and in the cleanup, beside `digest_task.cancel()`:

```python
    photo_task.cancel()
```

Gated on `settings.match_engine` like the engine and the digest; sleeps 240 s at start,
then one pass (30 listings) every 300 s; `tick()` never raises out. A pass ends quietly
(info log) on `NotConfigured` / `Disabled` / `BudgetExceeded`.

## (b) the mount — `app/api/routes/__init__.py`, after the `ai` line

```python
from app.api.routes import ai_photo
router.include_router(ai_photo.router, prefix="/ai/photo", tags=["AI"])
```

Every route carries `_role_dep("root", "super_admin")` itself.
`GET /ai/photo/status` → `{cursor, tagged, skipped, behind, version, model, enabled,
configured, last_at}` · `POST /ai/photo/run?limit=` (≤ 100) → `run_once()`'s dict ·
`GET /ai/photo/{id}` → `{id, tags, labels, tagged_at}` (404 unknown id) ·
`POST /ai/photo/{id}` → the same shape after a forced re-tag; 404 unknown id, 502 with the
Persian gate message when the AI is off / unconfigured / over the cap, 502 «مدل پاسخی نداد»
when the model failed (the old answer stays).

Optional, `app/api/routes/ai.py`, the `agents` list on the AI card:

```python
{"key": "vision", "name": "برچسب‌زن عکس", "job": "vision",
 "desc": "بازسازی‌شده، مبله، نقشه به‌جای عکس، لوگوی مشاور — روی سه عکس اول هر آگهی", "live": True},
```

## (c) revision 0007 — `migrations/versions/0007_photo_tags.py`

Two nullable columns on `properties`, guarded like 0003 (`insp.get_columns`):

| column          | type                         |
|-----------------|------------------------------|
| `ai_photo_tags` | `sa.JSON()`                  |
| `ai_photos_at`  | `sa.DateTime(timezone=True)` |

`revision = "0007"`, `down_revision = "0006"`; `upgrade()` adds each column only when it is
missing; `downgrade()` drops both. (The draft already in the main checkout matches this.)
No index: the pass filters `id > cursor AND ai_photos_at IS NULL`, which the primary key
carries. No backfill, no boot-time ALTER: a fresh database gets the columns from
create_all, an established one from this revision; `tests/test_pg_migration.py`
(`alembic check`) only agrees once 0007 is in.

## (d) the panel

`frontend/index.html`, after `js/app.js`:

```html
<script src="js/ai/photo.js?v=20260922a"></script>
```

**Property modal** — `frontend/js/app.js`, `viewProperty()`. Inside the template, right after
the carousel block (the line ending `بدون تصویر</div>'}`):

```html
<div class="ai-photo" id="ai-photo-${property.id}"></div>
```

and after `modalElement.show();`:

```js
if (typeof aiLoadPhotoTags === 'function' && ['root', 'super_admin'].includes(_currentUser?.role)) {
    aiLoadPhotoTags(property.id, document.getElementById(`ai-photo-${property.id}`));
}
```

(The role check is a courtesy — without it the API answers 403 and the box stays empty;
`.ai-photo:empty` hides it.)

**«هوش تصویری»** — `frontend/index.html`, `#ins-pane-visual`, after the «کیفیت عکس‌ها» card
(the one holding `id="vis-photo-list"`):

```html
<div class="card mb-3">
    <div class="card-header"><i class="bi bi-stars"></i> برچسب‌های هوش تصویری</div>
    <div class="card-body" id="ai-photo-status"></div>
</div>
```

and in `loadVisual()` (app.js), after `_visPhotos(ph);`:

```js
if (typeof aiLoadPhotoStatus === 'function') aiLoadPhotoStatus(document.getElementById('ai-photo-status'));
```

The closing card on that page («این صفحه چه چیزی را نمی‌سنجد») says no vision model runs
here; with this live, soften it — e.g. «تشخیص سبک و متریال از روی عکس، تولید پلان و ویرایش
تصویر هنوز انجام نمی‌شود؛ برچسب‌های هوش تصویری بالا از یک مدل بینایی روی سه عکس اول هر
آگهی می‌آید».

**CSS** — `frontend/css/style.css` (site tokens only, dark panel):

```css
/* برچسب‌زن عکس (frontend/js/ai/photo.js): the chips in the modal, the card on هوش تصویری */
.ai-photo { margin: .75rem 0 1rem; padding: .7rem .9rem; border-radius: var(--r-sm); border: 1px solid var(--border); background: var(--surface-2, rgba(255,255,255,.03)) }
.ai-photo:empty { display: none }
.ai-photo-head { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; margin-bottom: .5rem }
.ai-photo-title { font-size: .82rem; font-weight: 700; color: var(--text) }
.ai-photo-title i { color: var(--accent2) }
.ai-photo-chips { display: flex; flex-wrap: wrap; gap: .35rem }
.ai-photo-chip { display: inline-flex; align-items: center; padding: .18rem .6rem; border-radius: 20px; font-size: .74rem; color: var(--text); background: rgba(99,102,241,.14); border: 1px solid rgba(99,102,241,.35) }
.ai-photo-empty, .ai-photo-meta { font-size: .74rem; color: var(--text-muted) }
.ai-photo-chips + .ai-photo-meta { display: block; margin-top: .45rem }
.ai-photo-meter { display: inline-flex; gap: .2rem }
.ai-photo-meter i { width: .5rem; height: .5rem; border-radius: 50%; background: var(--text-muted); opacity: .3 }
.ai-photo-meter i.on { background: var(--accent2); opacity: 1 }
.ai-photo-btn { margin-inline-start: auto; font: inherit; font-size: .74rem; padding: .25rem .7rem; border-radius: var(--r-sm); border: 1px solid var(--accent); background: transparent; color: var(--accent); cursor: pointer }
.ai-photo-btn:hover { background: var(--accent); color: #fff }
.ai-photo-btn:disabled { opacity: .5; cursor: default }
.ai-photo-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: .5rem; margin-bottom: .6rem; text-align: center }
.ai-photo-stats b { display: block; font-size: 1.15rem; color: var(--accent2); font-variant-numeric: tabular-nums }
.ai-photo-stats span { font-size: .72rem; color: var(--text-muted) }
```

## (e) cost

At 512 px on the long side a photo is ≈ 250–300 tokens on the usual tilers (OpenAI-style:
85 + 170 per 512-tile → 255; GLM-4.xV-style: (512/28)×(384/28) ≈ 250). The question is
≈ 350 tokens (system line + masked title), the answer ≤ 150. Per listing with 3 photos
≈ 1.1–1.3k in + ~120 out ≈ 1.4k tokens. At flash-class prices ($0.05–0.2 per M input) that is
$0.0001–0.0003 a listing: a scrape of 500 new listings ≈ 0.7M tokens ≈ $0.04–0.15. Worst case
the loop does 30 × 288 = 8,640 listings a day ≈ 12M tokens ≈ $0.6–2.5 — the $2 default cap is
the ceiling; `BATCH` and `TICK_SECONDS` in `photo_tagger.py` are the knobs. The ledger (agent
«vision») shows Liara's own figure after the first pass.

## (f) not done, and why

- `PropertyResponse` (`app/schemas`, shared) does not carry `ai_photo_tags`; the modal reads
  `GET /ai/photo/{id}` instead. `to_dict()` carries both fields, so what uses it does.
- The GET is root/super_admin like the rest, per the brief. If consultants should see the
  chips, mount `GET /ai/photo/{id}` under the `properties` permission instead.
- No «فقط بازسازی‌شده» filter on the properties list: that is a WHERE on
  `ai_photo_tags->>'condition'` in `properties.py` (shared). The data is in place for it.
- No index on `ai_photos_at`; the pass is bounded by `id > cursor`. Add one if `behind` is
  ever slow.
- A listing the model refuses on every pass is retried every pass (nothing is stored on
  failure, per the brief); the cap is the backstop — marked `ponytail:` in `run_once`.
- Postgres-mode suite not run here (per the brief).

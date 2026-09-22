# خوانندهٔ نیاز مشتری — integration notes (branch `ai/need`)

The module: `app/ai/need_parser.py` (the parser), `app/api/routes/ai_need.py` (the route),
`frontend/js/ai/need.js` (the panel button), `tests/test_ai_need.py`. It writes nothing; it
proposes. Below is everything the shared files need, ready to paste.

## (a) Mount — `app/api/routes/__init__.py`

```python
from app.api.routes import (
    properties, scraper, auth, stats, proxies, crm, users, filing,
    public_auth, portal, gcp, monitoring, sms, email, forwarder, backup, ai, ai_need
)
...
# Any CRM user: the parser fills the customer form. It spends AI budget but
# writes nothing, so the «crm» key is the gate (root/super_admin pass anyway).
router.include_router(ai_need.router, prefix="/ai/need", tags=["AI"], dependencies=_perm("crm"))
```

Put it right after the `ai.router` line. `/ai/need/parse` does not collide with
`/ai/status|settings|test|usage`. The route also asks `Depends(get_current_user)` itself
(for the username in the log), like the CRM routes do.

In `app/api/routes/ai.py` (`ai_status`, the `agents` list) flip the entry to live:

```python
{"key": "need", "name": "خوانندهٔ نیاز مشتری", "job": "read",
 "desc": "«پر کردن از متن» در فرم مشتری، و توضیح درخواست‌های پرتال", "live": True},
```

## (b) Portal — `app/crm/portal_bridge.py`

```diff
 from app.models.crm_models import Customer
 from app.models.portal import PropertyRequest
 from app.services.match_service import RENT_TO_DEPOSIT
+from app.ai import need_parser
 ...
 async def customer_for(db, req: PropertyRequest, user=None) -> Customer:
     """The customer this request is, created or refreshed. The caller commits."""
     fields = criteria_of(req, user)
     cust = await db.get(Customer, req.customer_id) if req.customer_id else None
     if cust is None:
+        # What the description says and the form did not — districts, red
+        # lines, a budget typed in words — into keys still empty, once, when
+        # the customer is born. enrich_request swallows LLMError (a model that
+        # is off, capped or wrong never blocks a request) and returns None.
+        fields.update(await need_parser.enrich_request(db, req) or {})
         cust = Customer(**fields)
```

- No import cycle: `need_parser` imports `portal_bridge.criteria_of` inside `enrich_request`, not at module level.
- Fail-safe by construction: `enrich_request(db, req)` catches `llm.LLMError` (NotConfigured / Disabled /
  BudgetExceeded / malformed answer), logs a warning, returns `None`; the request is created exactly as today.
  Wrapping the one line in `try/except Exception` as well is fine if you want belt and braces.
- Only keys `criteria_of` left empty (`None`/`""`) come back: in practice `desired_district`, `red_lines`,
  `desired_type`, `desired_city`, `budget_max`, `desired_specs`. `notes`, `deal_type`, `temperature` are
  always set by `criteria_of`, so the model never overrules the form.
- Cost: one read-model call per new request with a description (`sync_open` pays it once for old requests too).
  The refresh path (`else`) keeps the form's fields — no re-parse on edits.

## (c) Panel

**HTML** — `frontend/index.html`, customer modal (`id="customerModal"`), card «آنالیز بودجه و نیاز (BANT Analysis)»,
inside its `<div class="row g-3">`: immediately after the `<div class="col-12">` that holds the `alert-info`
(«این سه فیلد را پر کنید…») and before `<div class="col-md-4"><label class="form-label">نوع معامله</label>`:

```html
<div class="col-12">
    <label class="form-label" for="cust-ai-text"><i class="bi bi-stars"></i> حرف مشتری (متن آزاد)</label>
    <div class="d-flex gap-2 align-items-start">
        <textarea id="cust-ai-text" class="form-control" rows="2" maxlength="2000"
                  placeholder="مثلاً: یه واحد ۱۰۰ متری نوساز طرف گلها تا ۵ میلیارد، طبقهٔ اول نباشه، پارکینگ حتماً"></textarea>
        <button type="button" class="btn btn-outline-success text-nowrap" id="cust-ai-btn" onclick="aiFillCustomerFromText()">
            <i class="bi bi-magic"></i> پر کردن از متن
        </button>
    </div>
    <div class="form-text small">فقط فیلدهای خالی پر می‌شوند؛ چیزی ذخیره نمی‌شود تا خودتان «ذخیره مشتری» را بزنید.</div>
</div>
```

**Script** — `frontend/index.html`, right after `<script src="js/app.js?v=…"></script>`:

```html
<script src="js/ai/need.js?v=20260922a"></script>
```

`frontend/` is served whole under `/dashboard`, so `js/ai/need.js` needs no server change; bump `?v=` when it changes.

**CSS** — `frontend/css/style.css`, next to the `.ai-agents` block (≈ line 3241):

```css
/* a field the need parser just filled — an accent outline for 2 s (frontend/js/ai/need.js) */
.ai-filled {
  outline: 2px solid var(--accent) !important;
  outline-offset: 1px;
  box-shadow: 0 0 0 4px var(--accent-glow) !important;
}
```

No change to `app.js`: `need.js` clears `#cust-ai-text` and the highlights on the modal's `show.bs.modal`
(`_resetCustomerForm` does not know the box), reads `_customerEditId` to tell a new form from an edit, and uses
`apiCall`, `showToast`, `formatNumber` (no HTML is built from the answer, so `esc` is not needed).

Fill rule: only empty controls. Inputs/textareas: blank. Selects: `cust-desired-type` when «— مشخص نیست —»;
`cust-deal-type` / `cust-temperature` only for a NEW customer still on their defaults (`buy` / `warm`) — when
editing, what came from the database is never touched. Each filled control carries `.ai-filled` for 2 s.

## (d) Confidence

The route returns `criteria.confidence` — `{field: 0..1}` for every field the model filled, as the model rated
its own reading. The toast shows the count and the min–max as percentages:
«۵ فیلد پر شد — اطمینان مدل ۷۰٪ تا ۹۵٪. بررسی کنید و ذخیره بزنید.» Nothing of it is stored; the consultant's
eyes are the second check. On the portal path nobody is looking, so confidence is not surfaced — only blank keys
are filled, and the rules make a vague text yield nulls rather than guesses.

## (e) Not done / not possible here

- The shared files above (mount, `ai.py` live flag, `portal_bridge.customer_for`, `index.html`, `style.css`) — by the brief.
- The real model was never called (no key in this worktree). The prompt pack is checked against the schema
  (each few-shot answer validates as `NeedCriteria`) and a faked gateway. Once mounted, try the button with a real
  key; if the read model answers Persian words for enums or money as text, the validators repair it; a fenced
  JSON is unfenced by `llm.chat`; a broken answer is retried once, then 502.
- The route is tested on a throwaway `FastAPI()` with the user/db dependencies overridden, not through `app.main`
  on Postgres (that suite skips on sqlite).
- `max_tokens=350` as briefed. A model that writes a long `notes` could hit it and fail JSON → retry → 502.
  If the ledger shows `malformed answer` rows for agent `need`, raise it to 450 in `need_parser.parse_need`.

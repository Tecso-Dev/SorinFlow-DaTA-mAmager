"""
درخواست‌های پرتال → موتور تطبیق — a visitor's request becomes a CRM customer.

PropertyRequest was stored as plain columns so that «a later phase can feed a
request straight into the matcher without a translation layer». This is that
phase, and the layer is thin: an open request becomes a Customer row — source
«portal», no consultant, the form's fields mapped onto the intake form's —
linked back through request.customer_id. From then on the engine, the call
queue, «پیامک به مشتری» and the morning digest treat the visitor like anyone
the office typed in. The request's own status follows: «مورد پیدا شد» when the
engine files a match, «تماس گرفته شد» when somebody rings or texts.

One customer per request, not per person: two requests are two needs (a flat
to buy and a shop to rent), and the engine scores needs. A request the visitor
withdraws takes its customer with it — nobody should ring about a need that
was cancelled.
"""
from typing import Dict, Iterable, Optional

from loguru import logger
from sqlalchemy import select

from app.models.crm_models import Customer
from app.models.portal import PropertyRequest
from app.services.match_service import RENT_TO_DEPOSIT

OPEN = ("new", "in_review")
# the portal's kinds → the intake form's types (match_service._family names)
KIND_TO_TYPE = {"apartment": "apartment", "villa": "house", "house": "house", "land": "land",
                "office": "office", "store": "shop", "shop": "shop"}
_NEEDS = (("needs_elevator", "آسانسور"), ("needs_parking", "پارکینگ"), ("needs_storage", "انباری"))


def criteria_of(req: PropertyRequest, user=None) -> Dict:
    """The Customer fields a request translates to."""
    specs = []
    if req.area_min or req.area_max:
        # the scorer wants one target area; the middle of a range is the honest one
        area = (req.area_min + req.area_max) // 2 if (req.area_min and req.area_max) else (req.area_min or req.area_max)
        specs.append(f"{area} متر")
    if req.rooms_min:
        specs.append(f"{req.rooms_min} خواب")
    if (req.deal_type or "buy") == "rent":
        # rentals are compared on the deposit (match_service._price_of); a
        # rent-only ceiling is converted the way the price watcher converts it
        budget = req.deposit_max or ((req.rent_max or 0) * RENT_TO_DEPOSIT or None)
    else:
        budget = req.budget_max
    notes = [f"درخواست پرتال #{req.id}" if req.id else "درخواست پرتال"]
    needs = [fa for attr, fa in _NEEDS if getattr(req, attr, False)]
    if needs:
        notes.append("نیاز دارد: " + "، ".join(needs))
    if req.year_built_min:
        notes.append(f"ساخت از {req.year_built_min}")
    if req.description:
        notes.append(req.description.strip())
    name = (req.contact_name or getattr(user, "full_name", None) or "").strip() or "کاربر پرتال"
    return {
        "full_name": name[:200],
        "mobile1": (req.contact_phone or getattr(user, "phone", None) or None),
        "source": "portal",
        "temperature": "warm",
        "consultant_name": None,
        "desired_city": (req.city or "").strip() or None,
        "desired_district": (req.districts or "").strip()[:300] or None,
        "desired_type": KIND_TO_TYPE.get((req.property_kind or "").strip().lower()),
        "deal_type": req.deal_type if req.deal_type in ("buy", "rent") else "buy",
        "budget_max": budget,
        "desired_specs": " / ".join(specs) or None,
        "notes": "\n".join(notes),
    }


async def customer_for(db, req: PropertyRequest, user=None) -> Customer:
    """The customer this request is, created or refreshed. The caller commits."""
    fields = criteria_of(req, user)
    cust = await db.get(Customer, req.customer_id) if req.customer_id else None
    if cust is None:
        # What the description says and the form did not — districts, red
        # lines, a budget typed in words — into the keys still empty, once,
        # when the customer is born. enrich_request swallows LLMError (a model
        # that is off, capped or wrong never blocks a request) and returns None.
        from app.ai import need_parser
        try:
            fields.update(await need_parser.enrich_request(db, req) or {})
        except Exception as e:
            logger.warning(f"[portal] the description was not read: {type(e).__name__}: {e}")
        cust = Customer(**fields)
        db.add(cust)
        await db.flush()
        req.customer_id = cust.id
        return cust
    for k, v in fields.items():
        setattr(cust, k, v)
    return cust


async def sync_open(db) -> int:
    """Open requests that never became a customer — requests from before this
    existed, or one whose creation lost the race. Idempotent, cheap, run by the
    engine before every pass."""
    rows = (await db.execute(select(PropertyRequest).where(
        PropertyRequest.status.in_(OPEN), PropertyRequest.customer_id.is_(None)))).scalars().all()
    if not rows:
        return 0
    for req in rows:
        await customer_for(db, req)
    await db.commit()
    logger.info(f"[portal] {len(rows)} open request(s) handed to the matching engine")
    return len(rows)


async def note_matches(db, pairs: Iterable[tuple]) -> int:
    """(customer_id, property_id) for every match the engine just filed: the
    requests behind portal customers become «مورد پیدا شد». Staged, not
    committed — the engine commits its pass as one."""
    pairs = list(pairs)
    if not pairs:
        return 0
    by_customer = {}
    for cid, pid in pairs:
        by_customer.setdefault(cid, pid)
    rows = (await db.execute(select(PropertyRequest).where(
        PropertyRequest.customer_id.in_(by_customer), PropertyRequest.status.in_(OPEN)))).scalars().all()
    for req in rows:
        req.status = "matched"
        req.matched_property_id = by_customer[req.customer_id]
    return len(rows)


async def note_contact(db, customer_id: Optional[int]) -> None:
    """Somebody rang or texted this customer: their request is «تماس گرفته شد».
    Staged; the caller commits with the decision it belongs to."""
    if not customer_id:
        return
    rows = (await db.execute(select(PropertyRequest).where(
        PropertyRequest.customer_id == customer_id,
        PropertyRequest.status.in_((*OPEN, "matched"))))).scalars().all()
    for req in rows:
        req.status = "contacted"

"""
SorinFlow CRM — API routes
Leads (from scraper) + Contacts + Notes + Tasks + Deals + Reminders + SMS + Dashboard
"""
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
import io

from app.database import get_db
from app.config import get_settings
from app.models.lead import Lead
from app.models.property import Property, allocate_serial_no
from app.models.crm_models import Contact, Deal, Note, Task, Reminder, SmsLog, Customer, DailyPerformance
from app.schemas import LeadResponse, LeadUpdate, LeadCreate, LeadList
from app.crm.notification import notify
from app.services.sms_service import send_sms
from app.auth.dependencies import get_current_user, get_current_user_optional, require_super_admin
from app.services.dpa_service import record_activity, record_lead_status
from app.models.user import User

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# LEADS (existing — from scraper)
# ─────────────────────────────────────────────────────────────────────────────

VALID_LEAD_STATUSES = {"new", "contacted", "visit", "contract_meeting", "qualified", "closed", "rented", "rejected"}

# ── structured attributes for manual leads ──
# ints/bools/text mapped to real Property columns; the rest live in extra_attrs
_ATTR_INT_COLS  = {"area", "floor", "rooms", "year_built", "land_area", "built_area", "total_floors", "frontage"}
_ATTR_BOOL_COLS = {"has_elevator", "has_parking", "has_storage", "has_balcony"}
_ATTR_TEXT_COLS = {"document_type"}
_ATTR_EXTRA_KEYS = {"units_per_floor", "cabinets", "closet", "flooring", "delivery_date",
                    "hvac", "yard", "position", "height", "mezzanine", "kitchen"}
VALID_PROPERTY_KINDS = {"apartment", "villa", "shop", "office"}


def _split_lead_attrs(attrs: Optional[dict]):
    """Split incoming attrs into Property column kwargs + extra_attrs JSON."""
    cols, extra = {}, {}
    for key, val in (attrs or {}).items():
        if val is None or val == "":
            continue
        if key in _ATTR_INT_COLS:
            try:
                cols[key] = int(val)
            except (TypeError, ValueError):
                continue
        elif key in _ATTR_BOOL_COLS:
            cols[key] = str(val).lower() in ("true", "1", "yes", "بله")
        elif key in _ATTR_TEXT_COLS:
            cols[key] = str(val).strip()[:100]
        elif key in _ATTR_EXTRA_KEYS:
            extra[key] = str(val).strip()[:200]
    return cols, extra


@router.get("/leads", response_model=LeadList)
async def list_leads(
    status: Optional[str] = None,
    city: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    notified: Optional[bool] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    from datetime import datetime
    query = select(Lead).order_by(Lead.created_at.desc())

    # All roles share the same lead pool (no per-user isolation)

    if status:
        query = query.where(Lead.status == status)
    if city:
        query = query.where(Lead.city_name == city)
    if category:
        query = query.where(Lead.category_name.ilike(f"%{category}%"))
    if search:
        term = f"%{search.strip()}%"
        # Street/neighborhood names usually live in the linked property's
        # address or description, not in the lead's own columns
        prop_match = select(Property.id).where(
            Property.id == Lead.property_id,
            or_(
                Property.address.ilike(term),
                Property.district.ilike(term),
                Property.neighborhood.ilike(term),
                Property.description.ilike(term),
            ),
        ).correlate(Lead).exists()
        query = query.where(or_(
            Lead.property_title.ilike(term),
            Lead.notes.ilike(term),
            Lead.phone_number.ilike(term),
            Lead.seller_name.ilike(term),
            prop_match,
        ))
    if notified is not None:
        query = query.where(Lead.notified == notified)
    if date_from:
        try:
            query = query.where(Lead.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to)
            # include the full end day
            if len(date_to) == 10:
                from datetime import timedelta
                dt_to = dt_to + timedelta(days=1)
            query = query.where(Lead.created_at < dt_to)
        except ValueError:
            pass

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar_one()
    result = await db.execute(query.limit(limit).offset(offset))
    leads = result.scalars().all()
    return LeadList(items=leads, total=total)


@router.post("/leads", response_model=LeadResponse)
async def create_lead(
    data: LeadCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    if data.status and data.status not in VALID_LEAD_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    import uuid
    manual_id = f"manual-{uuid.uuid4().hex[:12]}"

    # Manually-added leads aren't scraped from Divar, so we create a minimal
    # Property row to satisfy Lead.property_id's NOT NULL FK.
    is_rent = data.listing_type == "rent"
    attr_cols, extra_attrs = _split_lead_attrs(data.attrs)
    images = [u for u in (data.images or []) if isinstance(u, str) and u.startswith("/images/")][:20]
    prop = Property(
        tag_number=manual_id,
        divar_id=manual_id,
        title=data.property_title,
        city_name=data.city_name,
        category_name=data.category_name,
        listing_type=data.listing_type,
        property_type=data.property_kind if data.property_kind in VALID_PROPERTY_KINDS else None,
        price=None if is_rent else data.price,
        total_price=None if is_rent else data.price,
        deposit=data.deposit if is_rent else None,
        rent_price=data.rent_price if is_rent else None,
        area=data.area,
        phone_number=data.phone_number,
        seller_name=data.seller_name,
        url=data.property_url or "",
        images=images,
        thumbnail_url=images[0] if images else None,
        has_images=bool(images),
        extra_attrs=extra_attrs,
        owner_phone=current_user.divar_phone if current_user else None,
        **attr_cols,
    )
    prop.serial_no = await allocate_serial_no(db)
    db.add(prop)
    await db.flush()

    lead = Lead(
        property_id=prop.id,
        phone_number=data.phone_number,
        seller_name=data.seller_name,
        city_name=data.city_name,
        category_name=data.category_name,
        listing_type=data.listing_type,
        # the lead's headline number: sale price, or the deposit for rents
        price=(data.deposit or data.rent_price) if is_rent else data.price,
        area=attr_cols.get("area", data.area),
        property_url=data.property_url or "",
        property_title=data.property_title,
        status=data.status or "new",
        notes=data.notes,
        assigned_to=data.assigned_to,
    )
    db.add(lead)

    agent = (data.assigned_to or "").strip() or (
        current_user.full_name or current_user.username if current_user else None)
    await record_activity(db, agent, "new_file")

    await db.commit()
    await db.refresh(lead)
    return lead


@router.get("/leads/{lead_id}", response_model=LeadResponse)
async def get_lead(lead_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    resp = LeadResponse.model_validate(lead)
    resp.district = (await db.execute(
        select(Property.district).where(Property.id == lead.property_id)
    )).scalar_one_or_none()
    return resp


@router.patch("/leads/{lead_id}", response_model=LeadResponse)
async def update_lead(
    lead_id: int,
    data: LeadUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if data.status and data.status not in VALID_LEAD_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status")
    if data.status is not None:
        # Only score a real transition, so re-saving the same status can't farm points
        status_changed = lead.status != data.status
        lead.status = data.status
        if status_changed and data.status == "rented":
            lead.rented_at = datetime.now()   # lease clock starts; back in a year
        elif status_changed and lead.rented_at:
            lead.rented_at = None
        if status_changed:
            agent = (lead.assigned_to or "").strip() or (
                current_user.full_name or current_user.username if current_user else None)
            await record_lead_status(db, agent, data.status)
    if data.notes is not None:
        lead.notes = data.notes
    if data.assigned_to is not None:
        lead.assigned_to = data.assigned_to
    if data.district is not None:
        # District belongs to the linked property (street search reads it there)
        prop = (await db.execute(
            select(Property).where(Property.id == lead.property_id)
        )).scalar_one_or_none()
        if prop:
            prop.district = data.district.strip() or None
    await db.commit()
    await db.refresh(lead)
    resp = LeadResponse.model_validate(lead)
    resp.district = (await db.execute(
        select(Property.district).where(Property.id == lead.property_id)
    )).scalar_one_or_none()
    return resp


@router.post("/leads/{lead_id}/notify")
async def notify_lead(lead_id: int, db: AsyncSession = Depends(get_db)):
    lead_result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = lead_result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    prop_result = await db.execute(select(Property).where(Property.id == lead.property_id))
    prop = prop_result.scalar_one_or_none()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    channel = await notify(prop, lead)
    lead.notified = channel != "none"
    lead.notified_at = datetime.now() if lead.notified else lead.notified_at
    lead.notification_channel = channel
    await db.commit()
    return {"success": lead.notified, "channel": channel}


@router.delete("/leads/{lead_id}")
async def delete_lead(lead_id: int, db: AsyncSession = Depends(get_db)):
    """Delete the lead AND wipe the linked property everywhere:
    the property row itself, any sibling leads on it, its property-notes
    and its downloaded images. Deals keep their business record (their
    property link is nulled by the FK)."""
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    property_id = lead.property_id
    prop = (await db.execute(
        select(Property).where(Property.id == property_id)
    )).scalar_one_or_none()

    # all leads pointing at this property (FK is NOT NULL, so they must go
    # before the property row can)
    siblings = (await db.execute(
        select(Lead).where(Lead.property_id == property_id)
    )).scalars().all()
    for sib in siblings:
        await db.delete(sib)

    # property-specific notes
    notes = (await db.execute(
        select(Note).where(Note.property_id == property_id)
    )).scalars().all()
    for note in notes:
        await db.delete(note)

    divar_id = prop.divar_id if prop else None
    if prop:
        await db.delete(prop)
    await db.commit()

    # downloaded images on disk (best-effort)
    if divar_id:
        try:
            import shutil
            from app.config import get_settings as _gs
            img_dir = __import__("pathlib").Path(_gs().images_path) / divar_id
            if img_dir.is_dir():
                shutil.rmtree(img_dir, ignore_errors=True)
        except Exception:
            pass

    return {"success": True, "deleted_property": prop is not None,
            "deleted_leads": len(siblings), "deleted_notes": len(notes)}


@router.post("/upload-image")
async def upload_lead_image(file: UploadFile = File(...)):
    """Store a lead photo (converted to JPEG) under data/images/manual."""
    import uuid as _uuid
    import io as _io
    from pathlib import Path as _Path
    from PIL import Image as _Image

    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="فقط فایل تصویری مجاز است")
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="حجم تصویر حداکثر ۸ مگابایت")

    # Always convert (webp/png/heic-ish) to JPEG for a universal format
    try:
        im = _Image.open(_io.BytesIO(raw)).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="فایل تصویری معتبر نیست")

    dest_dir = _Path(get_settings().images_path) / "manual"
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"{_uuid.uuid4().hex}.jpg"
    im.save(dest_dir / name, format="JPEG", quality=88)
    return {"url": f"/images/manual/{name}"}


# ─────────────────────────────────────────────────────────────────────────────
# CONTACTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/contacts")
async def list_contacts(
    search: Optional[str] = None,
    contact_type: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(Contact).order_by(Contact.created_at.desc())
    if search:
        query = query.where(or_(
            Contact.name.ilike(f"%{search}%"),
            Contact.phone.ilike(f"%{search}%"),
            Contact.phone2.ilike(f"%{search}%"),
        ))
    if contact_type:
        query = query.where(Contact.contact_type == contact_type)
    if category:
        query = query.where(Contact.category == category)
    count = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    items = (await db.execute(query.limit(limit).offset(offset))).scalars().all()
    return {"items": [c.to_dict() for c in items], "total": count}


def _normalize_tags(tags) -> str | None:
    if tags is None:
        return None
    if isinstance(tags, list):
        return ", ".join(str(t).strip() for t in tags if t)
    return str(tags).strip() or None


@router.post("/contacts")
async def create_contact(data: dict, db: AsyncSession = Depends(get_db)):
    contact = Contact(
        name=data.get("name", ""),
        phone=data.get("phone"),
        phone2=data.get("phone2"),
        email=data.get("email"),
        contact_type=data.get("contact_type", "owner"),
        category=data.get("category", "normal"),
        city=data.get("city"),
        address=data.get("address"),
        notes=data.get("notes"),
        tags=_normalize_tags(data.get("tags")),
    )
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return contact.to_dict()


@router.get("/contacts/export/excel")
async def export_contacts_excel(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl not installed")

    items = (await db.execute(select(Contact).order_by(Contact.name))).scalars().all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "مخاطبین"
    headers = ["نام", "تلفن", "تلفن ۲", "ایمیل", "دسته", "اولویت", "شهر", "آدرس", "تگ‌ها", "تاریخ ثبت"]
    header_fill = PatternFill("solid", fgColor="1a1a2e")
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    contact_type_fa = {
        "owner": "مالکین", "landlord": "موجرین", "tenant": "مستاجرین",
        "seeker": "خواهان", "builder": "سازندگان", "agency": "املاک",
        "buyer": "خواهان", "seller": "مالکین", "consultant": "املاک", "other": "سایر",
    }
    for row, c in enumerate(items, 2):
        ws.append([
            c.name, c.phone, c.phone2, c.email,
            contact_type_fa.get(c.contact_type, c.contact_type), c.category, c.city, c.address,
            ", ".join(c.tags) if isinstance(c.tags, list) else (c.tags or ""),
            c.created_at.strftime("%Y-%m-%d") if c.created_at else "",
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=contacts.xlsx"},
    )


@router.get("/contacts/export/json")
async def export_contacts_json(
    db: AsyncSession = Depends(get_db),
    current_user: User = require_super_admin,
):
    items = (await db.execute(select(Contact).order_by(Contact.name))).scalars().all()
    import json
    data = json.dumps([c.to_dict() for c in items], ensure_ascii=False, default=str, indent=2)
    return StreamingResponse(
        io.BytesIO(data.encode("utf-8")),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=contacts.json"},
    )


@router.get("/contacts/{contact_id}")
async def get_contact(contact_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Contact).where(Contact.id == contact_id))
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact.to_dict()


@router.put("/contacts/{contact_id}")
async def update_contact(contact_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Contact).where(Contact.id == contact_id))
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    for field in ("name", "phone", "phone2", "email", "contact_type", "category", "city", "address", "notes"):
        if field in data:
            setattr(contact, field, data[field])
    if "tags" in data:
        contact.tags = _normalize_tags(data["tags"])
    contact.updated_at = datetime.now()
    await db.commit()
    await db.refresh(contact)
    return contact.to_dict()


@router.delete("/contacts/{contact_id}")
async def delete_contact(contact_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Contact).where(Contact.id == contact_id))
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    await db.delete(contact)
    await db.commit()
    return {"success": True}


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMERS (فرم پروفایل مشتری: BANT + بازدیدها + پیگیری)
# ─────────────────────────────────────────────────────────────────────────────

VALID_CUSTOMER_SOURCES = {"in_person", "divar", "referral"}
VALID_CUSTOMER_TEMPS = {"hot", "warm", "cold"}
CUSTOMER_TEXT_FIELDS = (
    "full_name", "mobile1", "mobile2", "consultant_name",
    "payment_methods", "desired_specs", "desired_district", "red_lines", "notes",
)


def _clean_customer_rows(rows, keys) -> list:
    """Keep only known keys per row; drop rows that are entirely empty."""
    out = []
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        cleaned = {k: (str(row.get(k) or "").strip() or None) for k in keys}
        if any(cleaned.values()):
            out.append(cleaned)
    return out


def _apply_customer_payload(customer: Customer, data: dict) -> None:
    for field in CUSTOMER_TEXT_FIELDS:
        if field in data:
            setattr(customer, field, (str(data[field]).strip() or None) if data[field] is not None else None)
    if "source" in data and data["source"] in VALID_CUSTOMER_SOURCES:
        customer.source = data["source"]
    if "temperature" in data and data["temperature"] in VALID_CUSTOMER_TEMPS:
        customer.temperature = data["temperature"]
    if "budget_max" in data:
        try:
            customer.budget_max = int(data["budget_max"]) if data["budget_max"] else None
        except (TypeError, ValueError):
            pass
    if "showings" in data:
        customer.showings = _clean_customer_rows(
            data["showings"], ("file_code", "description", "feedback", "next_step"))
    if "followups" in data:
        customer.followups = _clean_customer_rows(
            data["followups"], ("date", "time", "action"))


@router.get("/customers")
async def list_customers(
    search: Optional[str] = None,
    temperature: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(Customer).order_by(Customer.created_at.desc())
    if search:
        term = f"%{search.strip()}%"
        query = query.where(or_(
            Customer.full_name.ilike(term),
            Customer.mobile1.ilike(term),
            Customer.mobile2.ilike(term),
            Customer.desired_district.ilike(term),
            Customer.consultant_name.ilike(term),
        ))
    if temperature:
        query = query.where(Customer.temperature == temperature)
    if source:
        query = query.where(Customer.source == source)
    count = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    items = (await db.execute(query.limit(limit).offset(offset))).scalars().all()
    return {"items": [c.to_dict() for c in items], "total": count}


@router.post("/customers")
async def create_customer(data: dict, db: AsyncSession = Depends(get_db)):
    if not str(data.get("full_name") or "").strip():
        raise HTTPException(status_code=400, detail="full_name is required")
    customer = Customer(full_name=str(data["full_name"]).strip())
    _apply_customer_payload(customer, data)
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return customer.to_dict()


@router.get("/customers/{customer_id}")
async def get_customer(customer_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer.to_dict()


@router.put("/customers/{customer_id}")
async def update_customer(customer_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    _apply_customer_payload(customer, data)
    customer.updated_at = datetime.now()
    await db.commit()
    await db.refresh(customer)
    return customer.to_dict()


@router.delete("/customers/{customer_id}")
async def delete_customer(customer_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    await db.delete(customer)
    await db.commit()
    return {"success": True}


# ─────────────────────────────────────────────────────────────────────────────
# DAILY PERFORMANCE (فرم ارزیابی عملکرد روزانه — DPA)
# ─────────────────────────────────────────────────────────────────────────────

_DPA_INT_FIELDS = (
    "target_points", "new_files", "showings_count", "offers_count", "closed_count",
    "bonus_exclusive", "bonus_offer", "bonus_close",
    "pen_crm_delay", "pen_cancel", "pen_hot_lead",
)
_DPA_TEXT_FIELDS = ("agent_name", "date_jalali", "mentor_feedback", "rca")
_DPA_TASK_KEYS = {key for key, _ in DailyPerformance.BASE_TASKS}


def _apply_dpa_payload(dpa: DailyPerformance, data: dict) -> None:
    for field in _DPA_TEXT_FIELDS:
        if field in data:
            setattr(dpa, field, (str(data[field]).strip() or None) if data[field] is not None else None)
    if data.get("role") in ("hunter", "closer"):
        dpa.role = data["role"]
    for field in _DPA_INT_FIELDS:
        if field in data:
            try:
                setattr(dpa, field, max(int(data[field] or 0), 0))
            except (TypeError, ValueError):
                pass
    if isinstance(data.get("base_tasks"), dict):
        dpa.base_tasks = {k: bool(v) for k, v in data["base_tasks"].items() if k in _DPA_TASK_KEYS}
    # manual activity units (work done outside the panel); auto counts are
    # owned by the system and never overwritten from the client
    if isinstance(data.get("activities"), dict):
        cleaned = {}
        for k, v in data["activities"].items():
            if k in DailyPerformance.ACTIVITY_POINTS:
                try:
                    cleaned[k] = max(int(v or 0), 0)
                except (TypeError, ValueError):
                    continue
        dpa.activities = cleaned


@router.get("/dpa")
async def list_dpa(
    search: Optional[str] = None,
    date_jalali: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(DailyPerformance).order_by(DailyPerformance.created_at.desc())
    if search:
        query = query.where(DailyPerformance.agent_name.ilike(f"%{search.strip()}%"))
    if date_jalali:
        query = query.where(DailyPerformance.date_jalali == date_jalali.strip())
    count = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    items = (await db.execute(query.limit(limit).offset(offset))).scalars().all()
    return {"items": [d.to_dict() for d in items], "total": count}


@router.post("/dpa")
async def create_dpa(data: dict, db: AsyncSession = Depends(get_db)):
    if not str(data.get("agent_name") or "").strip():
        raise HTTPException(status_code=400, detail="agent_name is required")
    dpa = DailyPerformance(agent_name=str(data["agent_name"]).strip())
    _apply_dpa_payload(dpa, data)
    db.add(dpa)
    await db.commit()
    await db.refresh(dpa)
    return dpa.to_dict()


@router.get("/dpa/{dpa_id}")
async def get_dpa(dpa_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DailyPerformance).where(DailyPerformance.id == dpa_id))
    dpa = result.scalar_one_or_none()
    if not dpa:
        raise HTTPException(status_code=404, detail="DPA record not found")
    return dpa.to_dict()


@router.put("/dpa/{dpa_id}")
async def update_dpa(dpa_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DailyPerformance).where(DailyPerformance.id == dpa_id))
    dpa = result.scalar_one_or_none()
    if not dpa:
        raise HTTPException(status_code=404, detail="DPA record not found")
    _apply_dpa_payload(dpa, data)
    dpa.updated_at = datetime.now()
    await db.commit()
    await db.refresh(dpa)
    return dpa.to_dict()


@router.delete("/dpa/{dpa_id}")
async def delete_dpa(dpa_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DailyPerformance).where(DailyPerformance.id == dpa_id))
    dpa = result.scalar_one_or_none()
    if not dpa:
        raise HTTPException(status_code=404, detail="DPA record not found")
    await db.delete(dpa)
    await db.commit()
    return {"success": True}


# ─────────────────────────────────────────────────────────────────────────────
# NOTES
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/notes")
async def list_notes(
    contact_id: Optional[int] = None,
    deal_id: Optional[int] = None,
    property_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(Note).order_by(Note.created_at.desc())
    if contact_id:
        query = query.where(Note.contact_id == contact_id)
    if deal_id:
        query = query.where(Note.deal_id == deal_id)
    if property_id:
        query = query.where(Note.property_id == property_id)
    count = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    items = (await db.execute(query.limit(limit).offset(offset))).scalars().all()
    return {"items": [n.to_dict() for n in items], "total": count}


@router.post("/notes")
async def create_note(data: dict, db: AsyncSession = Depends(get_db)):
    note = Note(
        content=data.get("content", ""),
        contact_id=data.get("contact_id"),
        property_id=data.get("property_id"),
        deal_id=data.get("deal_id"),
        created_by=data.get("created_by"),
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note.to_dict()


@router.put("/notes/{note_id}")
async def update_note(note_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    if "content" in data:
        note.content = data["content"]
    note.updated_at = datetime.now()
    await db.commit()
    await db.refresh(note)
    return note.to_dict()


@router.delete("/notes/{note_id}")
async def delete_note(note_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    await db.delete(note)
    await db.commit()
    return {"success": True}


# ─────────────────────────────────────────────────────────────────────────────
# TASKS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/tasks")
async def list_tasks(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    contact_id: Optional[int] = None,
    deal_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(Task).order_by(Task.due_date.asc().nullslast(), Task.created_at.desc())
    if status:
        query = query.where(Task.status == status)
    if priority:
        query = query.where(Task.priority == priority)
    if contact_id:
        query = query.where(Task.contact_id == contact_id)
    if deal_id:
        query = query.where(Task.deal_id == deal_id)
    count = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    items = (await db.execute(query.limit(limit).offset(offset))).scalars().all()
    return {"items": [t.to_dict() for t in items], "total": count}


@router.post("/tasks")
async def create_task(data: dict, db: AsyncSession = Depends(get_db)):
    due = None
    if data.get("due_date"):
        try:
            due = _parse_datetime(data["due_date"])
        except Exception:
            pass
    task = Task(
        title=data.get("title", ""),
        description=data.get("description"),
        due_date=due,
        priority=data.get("priority", "medium"),
        status=data.get("status", "todo"),
        contact_id=data.get("contact_id"),
        deal_id=data.get("deal_id"),
        assigned_to=data.get("assigned_to"),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task.to_dict()


@router.get("/tasks/{task_id}")
async def get_task(task_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@router.put("/tasks/{task_id}")
async def update_task(task_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    for field in ("title", "description", "priority", "status", "contact_id", "deal_id", "assigned_to"):
        if field in data:
            setattr(task, field, data[field])
    if "due_date" in data and data["due_date"]:
        try:
            task.due_date = _parse_datetime(data["due_date"])
        except Exception:
            pass
    task.updated_at = datetime.now()
    await db.commit()
    await db.refresh(task)
    return task.to_dict()


@router.patch("/tasks/{task_id}/status")
async def update_task_status(task_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = data.get("status", task.status)
    task.updated_at = datetime.now()
    await db.commit()
    return {"id": task.id, "status": task.status}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.delete(task)
    await db.commit()
    return {"success": True}


# ─────────────────────────────────────────────────────────────────────────────
# DEALS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/deals")
async def list_deals(
    status: Optional[str] = None,
    deal_type: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(Deal).order_by(Deal.created_at.desc())
    if status:
        query = query.where(Deal.status == status)
    if deal_type:
        query = query.where(Deal.deal_type == deal_type)
    count = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    items = (await db.execute(query.limit(limit).offset(offset))).scalars().all()
    result = []
    for d in items:
        row = d.to_dict()
        # Fetch linked contact names
        if d.buyer_contact_id and not row.get("buyer_name"):
            r = await db.execute(select(Contact.name).where(Contact.id == d.buyer_contact_id))
            row["buyer_name"] = r.scalar_one_or_none()
        if d.seller_contact_id and not row.get("seller_name"):
            r = await db.execute(select(Contact.name).where(Contact.id == d.seller_contact_id))
            row["seller_name"] = r.scalar_one_or_none()
        result.append(row)
    return {"items": result, "total": count}


@router.post("/deals")
async def create_deal(data: dict, db: AsyncSession = Depends(get_db)):
    contract_date = close_date = None
    if data.get("contract_date"):
        try:
            contract_date = _parse_datetime(data["contract_date"])
        except Exception:
            pass
    if data.get("close_date"):
        try:
            close_date = _parse_datetime(data["close_date"])
        except Exception:
            pass
    deal = Deal(
        title=data.get("title", ""),
        deal_type=data.get("deal_type", "buy"),
        status=data.get("status", "new"),
        property_id=data.get("property_id"),
        buyer_contact_id=data.get("buyer_contact_id"),
        seller_contact_id=data.get("seller_contact_id"),
        amount=data.get("amount"),
        commission=data.get("commission"),
        commission_paid=data.get("commission_paid", False),
        notes=data.get("notes"),
        contract_date=contract_date,
        close_date=close_date,
    )
    db.add(deal)
    await db.commit()
    await db.refresh(deal)
    return deal.to_dict()


@router.get("/deals/export/excel")
async def export_deals_excel(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl not installed")

    items = (await db.execute(select(Deal).order_by(Deal.created_at.desc()))).scalars().all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "معاملات"
    headers = ["عنوان", "نوع", "وضعیت", "مبلغ", "کمیسیون", "کمیسیون پرداخت شده", "تاریخ قرارداد"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1a1a2e")
    for row, d in enumerate(items, 2):
        ws.append([
            d.title, d.deal_type, d.status, d.amount, d.commission,
            "بله" if d.commission_paid else "خیر",
            d.contract_date.strftime("%Y-%m-%d") if d.contract_date else "",
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=deals.xlsx"},
    )


@router.get("/deals/export/json")
async def export_deals_json(
    db: AsyncSession = Depends(get_db),
    current_user: User = require_super_admin,
):
    items = (await db.execute(select(Deal).order_by(Deal.created_at.desc()))).scalars().all()
    import json
    data = json.dumps([d.to_dict() for d in items], ensure_ascii=False, default=str, indent=2)
    return StreamingResponse(
        io.BytesIO(data.encode("utf-8")),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=deals.json"},
    )


@router.get("/deals/{deal_id}")
async def get_deal(deal_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    return deal.to_dict()


@router.put("/deals/{deal_id}")
async def update_deal(deal_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    for field in ("title", "deal_type", "status", "property_id", "buyer_contact_id",
                  "seller_contact_id", "amount", "commission", "commission_paid", "notes"):
        if field in data:
            setattr(deal, field, data[field])
    for date_field in ("contract_date", "close_date"):
        if data.get(date_field):
            try:
                setattr(deal, date_field, _parse_datetime(data[date_field]))
            except Exception:
                pass
    deal.updated_at = datetime.now()
    await db.commit()
    await db.refresh(deal)
    return deal.to_dict()


@router.delete("/deals/{deal_id}")
async def delete_deal(deal_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Deal).where(Deal.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    await db.delete(deal)
    await db.commit()
    return {"success": True}


# ─────────────────────────────────────────────────────────────────────────────
# REMINDERS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/reminders")
async def list_reminders(
    is_sent: Optional[bool] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(Reminder).order_by(Reminder.remind_at.asc())
    if is_sent is not None:
        query = query.where(Reminder.is_sent == is_sent)
    count = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    items = (await db.execute(query.limit(limit).offset(offset))).scalars().all()
    return {"items": [r.to_dict() for r in items], "total": count}


@router.get("/reminders/due")
async def get_due_reminders(db: AsyncSession = Depends(get_db)):
    """Reminders due in the next 24 hours that haven't been sent."""
    now = datetime.now()
    soon = now + timedelta(hours=24)
    result = await db.execute(
        select(Reminder).where(
            Reminder.remind_at >= now,
            Reminder.remind_at <= soon,
            Reminder.is_sent == False,
        ).order_by(Reminder.remind_at.asc())
    )
    items = result.scalars().all()
    return {"items": [r.to_dict() for r in items]}


def _parse_datetime(value: str) -> datetime:
    """Parse ISO datetime string; handles Z suffix (Python < 3.11 compat)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@router.post("/reminders")
async def create_reminder(data: dict, db: AsyncSession = Depends(get_db)):
    remind_at = None
    if data.get("remind_at"):
        try:
            remind_at = _parse_datetime(data["remind_at"])
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid remind_at format")
    if not remind_at:
        raise HTTPException(status_code=400, detail="remind_at is required")
    reminder = Reminder(
        title=data.get("title", ""),
        remind_at=remind_at,
        repeat=data.get("repeat", "none"),
        channel=data.get("channel", "in_app"),
        sms_to=data.get("sms_to"),
        contact_id=data.get("contact_id"),
        deal_id=data.get("deal_id"),
        task_id=data.get("task_id"),
    )
    db.add(reminder)
    await db.commit()
    await db.refresh(reminder)
    return reminder.to_dict()


@router.delete("/reminders/{reminder_id}")
async def delete_reminder(reminder_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Reminder).where(Reminder.id == reminder_id))
    reminder = result.scalar_one_or_none()
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")
    await db.delete(reminder)
    await db.commit()
    return {"success": True}


# ─────────────────────────────────────────────────────────────────────────────
# SMS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/sms/send")
async def send_sms_route(data: dict, db: AsyncSession = Depends(get_db)):
    to_number = data.get("to_number", "").strip()
    message = data.get("message", "").strip()
    provider = data.get("provider", "kavenegar")
    contact_id = data.get("contact_id")

    if not to_number or not message:
        raise HTTPException(status_code=400, detail="to_number and message are required")

    result = await send_sms(to_number, message, provider)

    log = SmsLog(
        to_number=to_number,
        message=message,
        status="sent" if result["success"] else "failed",
        provider=provider,
        response=result.get("response", ""),
        contact_id=contact_id,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return {**result, "log_id": log.id}


@router.get("/sms/logs")
async def list_sms_logs(
    contact_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(SmsLog).order_by(SmsLog.sent_at.desc())
    if contact_id:
        query = query.where(SmsLog.contact_id == contact_id)
    if status:
        query = query.where(SmsLog.status == status)
    count = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    items = (await db.execute(query.limit(limit).offset(offset))).scalars().all()
    return {"items": [s.to_dict() for s in items], "total": count}


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD / STATS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def crm_stats(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    # Lead stats are shared across all roles (no per-user isolation)
    def _lead_count(*extra_where):
        q = select(func.count(Lead.id))
        for w in extra_where:
            q = q.where(w)
        return q

    def _lead_group(group_col):
        return select(group_col, func.count(Lead.id)).group_by(group_col)

    # Leads
    total_leads = (await db.execute(_lead_count())).scalar_one()
    leads_by_status = {
        row[0]: row[1]
        for row in (await db.execute(_lead_group(Lead.status))).all()
    }
    notified = (await db.execute(_lead_count(Lead.notified == True))).scalar_one()

    # Contacts
    total_contacts = (await db.execute(select(func.count(Contact.id)))).scalar_one()
    contacts_by_type = {
        row[0]: row[1]
        for row in (await db.execute(select(Contact.contact_type, func.count(Contact.id)).group_by(Contact.contact_type))).all()
    }

    # Tasks
    total_tasks = (await db.execute(select(func.count(Task.id)))).scalar_one()
    tasks_todo = (await db.execute(select(func.count(Task.id)).where(Task.status == "todo"))).scalar_one()
    tasks_done = (await db.execute(select(func.count(Task.id)).where(Task.status == "done"))).scalar_one()
    now = datetime.now()
    tasks_overdue = (await db.execute(
        select(func.count(Task.id)).where(Task.due_date < now, Task.status != "done")
    )).scalar_one()

    # Deals
    total_deals = (await db.execute(select(func.count(Deal.id)))).scalar_one()
    deals_by_status = {
        row[0]: row[1]
        for row in (await db.execute(select(Deal.status, func.count(Deal.id)).group_by(Deal.status))).all()
    }
    closed_amount = (await db.execute(
        select(func.sum(Deal.amount)).where(Deal.status == "closed")
    )).scalar_one() or 0

    # Reminders due today
    today_end = now.replace(hour=23, minute=59, second=59)
    reminders_due_today = (await db.execute(
        select(func.count(Reminder.id)).where(
            Reminder.remind_at <= today_end, Reminder.is_sent == False
        )
    )).scalar_one()

    # SMS
    total_sms = (await db.execute(select(func.count(SmsLog.id)))).scalar_one()

    return {
        "leads": {
            "total": total_leads,
            "by_status": leads_by_status,
            "notified": notified,
            "pending_notification": total_leads - notified,
        },
        "contacts": {
            "total": total_contacts,
            "by_type": contacts_by_type,
        },
        "tasks": {
            "total": total_tasks,
            "todo": tasks_todo,
            "done": tasks_done,
            "overdue": tasks_overdue,
        },
        "deals": {
            "total": total_deals,
            "by_status": deals_by_status,
            "closed_amount": closed_amount,
        },
        "reminders_due_today": reminders_due_today,
        "total_sms": total_sms,
    }

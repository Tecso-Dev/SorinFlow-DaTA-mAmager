"""
SorinFlow CRM — API routes
Leads (from scraper) + Contacts + Notes + Tasks + Deals + Reminders + SMS + Dashboard
"""
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
import io

from app.database import get_db
from app.models.lead import Lead
from app.models.property import Property
from app.models.crm_models import Contact, Deal, Note, Task, Reminder, SmsLog
from app.schemas import LeadResponse, LeadUpdate, LeadCreate, LeadList
from app.crm.notification import notify
from app.services.sms_service import send_sms
from app.auth.dependencies import get_current_user, get_current_user_optional, require_super_admin
from app.models.user import User

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# LEADS (existing — from scraper)
# ─────────────────────────────────────────────────────────────────────────────

VALID_LEAD_STATUSES = {"new", "contacted", "visit", "contract_meeting", "qualified", "closed", "rejected"}


@router.get("/leads", response_model=LeadList)
async def list_leads(
    status: Optional[str] = None,
    city: Optional[str] = None,
    notified: Optional[bool] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    from datetime import datetime
    query = select(Lead).order_by(Lead.created_at.desc())

    # Isolate leads to the current user's Divar phone via the linked property
    if current_user and current_user.divar_phone:
        query = query.join(Property, Lead.property_id == Property.id).where(
            Property.owner_phone == current_user.divar_phone
        )

    if status:
        query = query.where(Lead.status == status)
    if city:
        query = query.where(Lead.city_name == city)
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
    prop = Property(
        tag_number=manual_id,
        divar_id=manual_id,
        title=data.property_title,
        city_name=data.city_name,
        category_name=data.category_name,
        listing_type=data.listing_type,
        price=data.price,
        total_price=data.price,
        area=data.area,
        phone_number=data.phone_number,
        seller_name=data.seller_name,
        url=data.property_url or "",
        owner_phone=current_user.divar_phone if current_user else None,
    )
    db.add(prop)
    await db.flush()

    lead = Lead(
        property_id=prop.id,
        phone_number=data.phone_number,
        seller_name=data.seller_name,
        city_name=data.city_name,
        category_name=data.category_name,
        listing_type=data.listing_type,
        price=data.price,
        area=data.area,
        property_url=data.property_url or "",
        property_title=data.property_title,
        status=data.status or "new",
        notes=data.notes,
        assigned_to=data.assigned_to,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return lead


@router.get("/leads/{lead_id}", response_model=LeadResponse)
async def get_lead(lead_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.patch("/leads/{lead_id}", response_model=LeadResponse)
async def update_lead(lead_id: int, data: LeadUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if data.status and data.status not in VALID_LEAD_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status")
    if data.status is not None:
        lead.status = data.status
    if data.notes is not None:
        lead.notes = data.notes
    if data.assigned_to is not None:
        lead.assigned_to = data.assigned_to
    await db.commit()
    await db.refresh(lead)
    return lead


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
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    await db.delete(lead)
    await db.commit()
    return {"success": True}


# ─────────────────────────────────────────────────────────────────────────────
# CONTACTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/contacts")
async def list_contacts(
    search: Optional[str] = None,
    contact_type: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
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
        contact_type=data.get("contact_type", "buyer"),
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
    headers = ["نام", "تلفن", "تلفن ۲", "ایمیل", "نوع", "دسته‌بندی", "شهر", "آدرس", "تگ‌ها", "تاریخ ثبت"]
    header_fill = PatternFill("solid", fgColor="1a1a2e")
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    for row, c in enumerate(items, 2):
        ws.append([
            c.name, c.phone, c.phone2, c.email,
            c.contact_type, c.category, c.city, c.address,
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
# NOTES
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/notes")
async def list_notes(
    contact_id: Optional[int] = None,
    deal_id: Optional[int] = None,
    property_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
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
    limit: int = 100,
    offset: int = 0,
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
    limit: int = 100,
    offset: int = 0,
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
    limit: int = 100,
    offset: int = 0,
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
    limit: int = 100,
    offset: int = 0,
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
    # Helper: count leads scoped to the current user's Divar phone
    def _lead_count(*extra_where):
        q = select(func.count(Lead.id))
        if current_user and current_user.divar_phone:
            q = q.join(Property, Lead.property_id == Property.id).where(
                Property.owner_phone == current_user.divar_phone
            )
        for w in extra_where:
            q = q.where(w)
        return q

    def _lead_group(group_col):
        q = select(group_col, func.count(Lead.id)).group_by(group_col)
        if current_user and current_user.divar_phone:
            q = q.join(Property, Lead.property_id == Property.id).where(
                Property.owner_phone == current_user.divar_phone
            )
        return q

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

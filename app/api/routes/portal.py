"""
SorinFlow — visitor portal.

Two audiences share this router and never share a route:
  * a visitor manages their own property requests and upgrade ticket;
  * staff with the «portal» permission read every request, and super_admin
    decides tickets.

Ownership is checked on the row, not inferred from the token, and a visitor
asking for someone else's row gets 404 rather than 403 — a 403 would confirm
the row exists.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.portal import PropertyRequest, UpgradeTicket
from app.auth.dependencies import get_current_user, require_permission, _role_dep
from app.auth.permissions import (
    ROLE_ADMIN, ROLE_VISITOR, DEFAULT_ADMIN_PERMISSIONS, normalize_permissions,
)
from app.schemas import (
    PropertyRequestCreate, PropertyRequestUpdate,
    UpgradeTicketCreate, UpgradeTicketDecision,
)

router = APIRouter()

_portal_staff = Depends(require_permission("portal"))
_super_admin = Depends(_role_dep("root", "super_admin"))


async def _visitor(current_user: User = Depends(get_current_user)) -> User:
    """A verified visitor. Staff are refused: they have the dashboard, and
    letting them post into the visitor queue would muddle who asked for what."""
    if current_user.role != ROLE_VISITOR:
        raise HTTPException(status_code=403, detail="این بخش برای کاربران عضو سایت است")
    if not current_user.phone_verified:
        raise HTTPException(status_code=403, detail="ابتدا شماره خود را تأیید کنید")
    return current_user


# ── Visitor ───────────────────────────────────────────────────────────────────

@router.get("/me")
async def portal_me(current_user: User = Depends(_visitor), db: AsyncSession = Depends(get_db)):
    reqs = (await db.execute(
        select(func.count()).select_from(PropertyRequest)
        .where(PropertyRequest.user_id == current_user.id))).scalar() or 0
    ticket = (await db.execute(
        select(UpgradeTicket).where(UpgradeTicket.user_id == current_user.id)
        .order_by(UpgradeTicket.created_at.desc()).limit(1))).scalar_one_or_none()
    return {
        "full_name": current_user.full_name,
        "phone": current_user.phone,
        "email": current_user.email,
        "requests_count": reqs,
        "ticket": ticket.to_dict() if ticket else None,
    }


@router.post("/requests", status_code=201)
async def create_request(data: PropertyRequestCreate,
                         current_user: User = Depends(_visitor),
                         db: AsyncSession = Depends(get_db)):
    """Record what this visitor is looking for."""
    open_count = (await db.execute(
        select(func.count()).select_from(PropertyRequest).where(
            PropertyRequest.user_id == current_user.id,
            PropertyRequest.status.in_(["new", "in_review"])))).scalar() or 0
    if open_count >= 10:
        raise HTTPException(
            status_code=429,
            detail="تعداد درخواست‌های باز شما زیاد است. تا بررسی موارد قبلی صبر کنید")

    req = PropertyRequest(
        user_id=current_user.id,
        **data.model_dump(exclude={"contact_name", "contact_phone"}),
        contact_name=data.contact_name or current_user.full_name,
        contact_phone=data.contact_phone or current_user.phone,
        status="new",
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return req.to_dict()


@router.get("/requests/mine")
async def my_requests(current_user: User = Depends(_visitor),
                      db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(PropertyRequest).where(PropertyRequest.user_id == current_user.id)
        .order_by(PropertyRequest.created_at.desc()))).scalars().all()
    return {"items": [r.to_dict() for r in rows], "total": len(rows)}


@router.delete("/requests/{request_id}")
async def delete_my_request(request_id: int,
                            current_user: User = Depends(_visitor),
                            db: AsyncSession = Depends(get_db)):
    req = (await db.execute(select(PropertyRequest).where(
        PropertyRequest.id == request_id,
        PropertyRequest.user_id == current_user.id))).scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="درخواست یافت نشد")
    await db.delete(req)
    await db.commit()
    return {"success": True}


@router.post("/tickets", status_code=201)
async def create_ticket(data: UpgradeTicketCreate,
                        current_user: User = Depends(_visitor),
                        db: AsyncSession = Depends(get_db)):
    """Ask super_admin for panel access. One open ticket at a time."""
    pending = (await db.execute(select(UpgradeTicket).where(
        UpgradeTicket.user_id == current_user.id,
        UpgradeTicket.status == "pending"))).scalar_one_or_none()
    if pending:
        raise HTTPException(status_code=400,
                            detail="درخواست قبلی شما در حال بررسی است")

    ticket = UpgradeTicket(
        user_id=current_user.id,
        message=data.message,
        contact_phone=data.contact_phone or current_user.phone,
        status="pending",
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    return ticket.to_dict()


@router.get("/tickets/mine")
async def my_tickets(current_user: User = Depends(_visitor),
                     db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(UpgradeTicket).where(UpgradeTicket.user_id == current_user.id)
        .order_by(UpgradeTicket.created_at.desc()))).scalars().all()
    return {"items": [t.to_dict() for t in rows], "total": len(rows)}


# ── Staff ─────────────────────────────────────────────────────────────────────

def _with_user(row, user: User | None) -> dict:
    d = row.to_dict()
    d["user"] = {
        "id": user.id, "full_name": user.full_name,
        "phone": user.phone, "email": user.email,
    } if user else None
    return d


@router.get("/admin/requests")
async def list_requests(status: str = Query(None),
                        limit: int = Query(50, ge=1, le=200),
                        offset: int = Query(0, ge=0),
                        _: User = _portal_staff,
                        db: AsyncSession = Depends(get_db)):
    """Every visitor request, newest first. One join, not a lookup per row."""
    q = select(PropertyRequest, User).join(User, User.id == PropertyRequest.user_id)
    if status:
        q = q.where(PropertyRequest.status == status)
    total = (await db.execute(
        select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (await db.execute(
        q.order_by(PropertyRequest.created_at.desc()).limit(limit).offset(offset))).all()
    return {"items": [_with_user(r, u) for r, u in rows], "total": total}


@router.patch("/admin/requests/{request_id}")
async def update_request(request_id: int, data: PropertyRequestUpdate,
                         current_user: User = _portal_staff,
                         db: AsyncSession = Depends(get_db)):
    req = (await db.execute(select(PropertyRequest).where(
        PropertyRequest.id == request_id))).scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="درخواست یافت نشد")

    if data.status is not None:
        req.status = data.status
    if data.admin_note is not None:
        req.admin_note = data.admin_note
    if data.matched_property_id is not None:
        req.matched_property_id = data.matched_property_id
    req.handled_by = current_user.username
    await db.commit()
    await db.refresh(req)
    return req.to_dict()


@router.get("/admin/tickets")
async def list_tickets(status: str = Query(None),
                       _: User = _super_admin,
                       db: AsyncSession = Depends(get_db)):
    q = select(UpgradeTicket, User).join(User, User.id == UpgradeTicket.user_id)
    if status:
        q = q.where(UpgradeTicket.status == status)
    rows = (await db.execute(q.order_by(UpgradeTicket.created_at.desc()))).all()
    return {"items": [_with_user(t, u) for t, u in rows], "total": len(rows)}


@router.post("/admin/tickets/{ticket_id}/decide")
async def decide_ticket(ticket_id: int, data: UpgradeTicketDecision,
                        current_user: User = _super_admin,
                        db: AsyncSession = Depends(get_db)):
    """Approve a visitor into an admin, or reject the request.

    Approval is the only path that raises a role, and it can only ever produce
    an admin — the permission list is filtered to known keys so a crafted body
    cannot invent one, and no body can name a role at all.
    """
    ticket = (await db.execute(select(UpgradeTicket).where(
        UpgradeTicket.id == ticket_id))).scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="درخواست یافت نشد")
    if ticket.status != "pending":
        raise HTTPException(status_code=400, detail="این درخواست قبلاً بررسی شده است")

    user = (await db.execute(select(User).where(
        User.id == ticket.user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")

    if data.approve:
        perms = normalize_permissions(data.permissions)
        if not perms:
            perms = list(DEFAULT_ADMIN_PERMISSIONS)
        user.role = ROLE_ADMIN
        user.permissions = perms
        ticket.granted_permissions = perms
        ticket.status = "approved"
    else:
        ticket.status = "rejected"
        ticket.granted_permissions = []

    ticket.decision_note = data.decision_note
    ticket.decided_by = current_user.username
    ticket.decided_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(ticket)
    return ticket.to_dict()

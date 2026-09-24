"""
رویدادها — the audit trail. Read-only API over what app/services/audit.py
writes: root and super_admin only, checked inside (like backup.py, sms.py).
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import _role_dep
from app.auth.permissions import ROLE_ROOT, ROLE_SUPER_ADMIN
from app.database import get_db
from app.models.audit_event import AuditEvent
from app.models.user import User
from app.services.audit import ACTIONS

router = APIRouter()
_super_admin = Depends(_role_dep(ROLE_ROOT, ROLE_SUPER_ADMIN))


# Fixed +03:30 — the production image has no tz database (CLAUDE.md).
TEHRAN = timezone(timedelta(hours=3, minutes=30), "Asia/Tehran")


def _parse_iso(value: str, field: str) -> datetime:
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} نامعتبر است (فرمت ISO 8601)")
    # the panel sends a bare day — a Tehran day, not the database session's
    return when.replace(tzinfo=TEHRAN) if when.tzinfo is None else when


def _is_bare_date(value: str) -> bool:
    return len(value.strip()) == 10


def _serialize(row: AuditEvent) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "actor_user_id": row.actor_user_id,
        "actor_username": row.actor_username,
        "actor_role": row.actor_role,
        "action": row.action,
        "action_label": ACTIONS.get(row.action, row.action),
        "target_type": row.target_type,
        "target_id": row.target_id,
        "summary": row.summary,
        "detail": row.detail,
        "ip": row.ip,
        "request_id": row.request_id,
    }


@router.get("/events")
async def list_events(
    actor: Optional[str] = None,
    action: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = _super_admin,
):
    """Newest first. `actor` matches the snapshot username taken at write
    time (a later rename does not change what an old row says)."""
    conds = []
    if actor:
        conds.append(AuditEvent.actor_username.ilike(f"%{actor}%"))
    if action:
        conds.append(AuditEvent.action == action)
    if since:
        conds.append(AuditEvent.created_at >= _parse_iso(since, "since"))
    if until:
        end = _parse_iso(until, "until")
        # «تا ۱۴۰۵/۰۷/۰۲» means through the end of that day, not its first second
        conds.append(AuditEvent.created_at < end + timedelta(days=1) if _is_bare_date(until)
                     else AuditEvent.created_at <= end)

    total = (await db.execute(
        select(func.count()).select_from(AuditEvent).where(*conds))).scalar() or 0
    rows = (await db.execute(
        select(AuditEvent).where(*conds)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit).offset(offset))).scalars().all()

    return {"items": [_serialize(r) for r in rows], "total": total}


@router.get("/actions")
async def list_actions(_: User = _super_admin):
    """The known action keys with Persian labels — the panel's filter list."""
    return {"items": [{"key": k, "label": v} for k, v in ACTIONS.items()]}

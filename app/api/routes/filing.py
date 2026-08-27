"""
کمد و زونکن — filing cabinets, binders, and the files inside them.

The agency's paper habit, kept: a cabinet holds binders, a binder holds
files, and a file is a Property row. Nothing here duplicates property data —
it only says where a file lives and how it is marked (سنجاق / بایگانی /
شخصی / برچسب).
"""
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.permissions import STAFF_ROLES
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.crm_models import Binder, Cabinet
from app.models.property import Property
from app.models.user import User

router = APIRouter()

VALID_KINDS = set(Binder.KINDS)
VALID_DEALS = set(Binder.DEAL_TYPES)

# ── برچسب ───────────────────────────────────────────────────────────────
# Tags live in one VARCHAR(500) column. A consultant typing on a Persian
# keyboard produces «،», the API contract says «,», and both used to be in
# play — one writing separator and one reading separator, which merged every
# tag into one and made untagging a no-op. So: read every separator, write
# exactly one.
TAG_SEP = "، "
_TAG_SPLIT = re.compile(r"[,،؛;]")
TAGS_MAX = 500          # Property.tags column width


def split_tags(blob: Optional[str]) -> List[str]:
    """Every tag in the column, in order, without repeats."""
    out: List[str] = []
    for part in _TAG_SPLIT.split(blob or ""):
        name = part.strip()
        if name and name not in out:
            out.append(name)
    return out


def join_tags(names: List[str]) -> Optional[str]:
    """Back into the column, never past the width it can hold."""
    kept: List[str] = []
    for name in names:
        if len(TAG_SEP.join(kept + [name])) > TAGS_MAX:
            break
        kept.append(name)
    return TAG_SEP.join(kept) or None


def _actor(user) -> Optional[str]:
    return getattr(user, "full_name", None) or getattr(user, "username", None)


def _is_super(user) -> bool:
    return getattr(user, "role", None) == "super_admin"


def _visible_to(query, user):
    """Private files belong to whoever filed them (and to a super_admin)."""
    if _is_super(user):
        return query
    actor = _actor(user)
    if not actor:
        # No name to match on — «شخصی» must mean hidden, not "matches NULL"
        return query.where(Property.is_private == False)     # noqa: E712
    return query.where(or_(Property.is_private == False,      # noqa: E712
                           Property.created_by == actor))


def require_filing_admin(current_user: User = Depends(get_current_user)) -> User:
    """Guards the two destructive structural calls.

    Deleting a cabinet or a binder unfiles every file behind it, so unlike the
    single-record deletes elsewhere in the CRM it is an admin's call. Phrased
    in Persian because that is the only language this panel speaks.
    """
    # STAFF_ROLES rather than a literal tuple: root was silently excluded when
    # the fourth role landed, so the developer's own account got a 403 on the
    # one action nobody else can undo.
    if current_user.role not in STAFF_ROLES:
        raise HTTPException(
            status_code=403,
            detail="حذف کمد و زونکن فقط با دسترسی مدیر انجام می‌شود")
    return current_user


def _cabinets_visible_to(query, user):
    """A کمد شخصی belongs to whoever made it; a cabinet with no owner is
    the agency's and everyone sees it."""
    if _is_super(user):
        return query
    actor = _actor(user)
    if not actor:
        return query.where(Cabinet.owner.is_(None))
    return query.where(or_(Cabinet.owner.is_(None), Cabinet.owner == actor))


# ── cabinets ────────────────────────────────────────────────────────────

@router.get("/cabinets")
async def list_cabinets(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every cabinet the caller may see, with its binders and a live file
    count per binder."""
    cabinets = (await db.execute(
        _cabinets_visible_to(
            select(Cabinet).options(selectinload(Cabinet.binders)), current_user)
        .order_by(Cabinet.sort_order, Cabinet.id)
    )).scalars().all()

    counts = dict((await db.execute(
        _visible_to(select(Property.binder_id, func.count(Property.id))
                    .where(Property.binder_id.isnot(None),
                           Property.is_archived == False), current_user)  # noqa: E712
        .group_by(Property.binder_id)
    )).all())

    items = []
    for c in cabinets:
        data = c.to_dict()
        data["binders"] = [b.to_dict(file_count=counts.get(b.id, 0)) for b in c.binders]
        data["file_count"] = sum(b["file_count"] for b in data["binders"])
        items.append(data)
    return {"items": items, "total": len(items)}


@router.post("/cabinets")
async def create_cabinet(
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="نام کمد الزامی است")
    nxt = (await db.execute(select(func.max(Cabinet.sort_order)))).scalar() or 0
    cabinet = Cabinet(
        name=name[:120],
        color=data.get("color") or Cabinet.PALETTE[0],
        icon=data.get("icon") or "bi-archive",
        sort_order=nxt + 1,
        owner=_actor(current_user) if data.get("personal") else None,
    )
    db.add(cabinet)
    await db.commit()
    await db.refresh(cabinet)
    return cabinet.to_dict(with_binders=True)


@router.patch("/cabinets/{cabinet_id}")
async def update_cabinet(
    cabinet_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cabinet = (await db.execute(_cabinets_visible_to(
        select(Cabinet).where(Cabinet.id == cabinet_id), current_user))).scalar_one_or_none()
    if not cabinet:
        raise HTTPException(status_code=404, detail="کمد یافت نشد")
    if "name" in data and (data["name"] or "").strip():
        cabinet.name = data["name"].strip()[:120]
    if data.get("color"):
        cabinet.color = data["color"]
    if data.get("icon"):
        cabinet.icon = data["icon"]
    if "personal" in data:
        # turning it personal stamps the maker, otherwise the cabinet would
        # be one nobody at all could open
        cabinet.owner = (cabinet.owner or _actor(current_user)) if data["personal"] else None
    if "sort_order" in data:
        try:
            cabinet.sort_order = int(data["sort_order"])
        except (TypeError, ValueError):
            pass
    await db.commit()
    await db.refresh(cabinet)
    return cabinet.to_dict()


@router.delete("/cabinets/{cabinet_id}")
async def delete_cabinet(
    cabinet_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_filing_admin),
):
    """Deleting a cabinet deletes its binders; the files inside are only
    unfiled, never destroyed.

    Admin-only: unlike a single record, this cascades through every binder on
    the shelf and unfiles the whole agency's paperwork behind it.
    """
    cabinet = (await db.execute(_cabinets_visible_to(
        select(Cabinet).options(selectinload(Cabinet.binders))
        .where(Cabinet.id == cabinet_id), current_user))).scalar_one_or_none()
    if not cabinet:
        raise HTTPException(status_code=404, detail="کمد یافت نشد")
    binder_ids = [b.id for b in cabinet.binders]
    freed = 0
    if binder_ids:
        props = (await db.execute(
            select(Property).where(Property.binder_id.in_(binder_ids)))).scalars().all()
        for p in props:
            p.binder_id = None
        freed = len(props)
    await db.delete(cabinet)
    await db.commit()
    return {"success": True, "unfiled": freed}


# ── binders ─────────────────────────────────────────────────────────────

@router.post("/binders")
async def create_binder(
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="نام زونکن الزامی است")
    cabinet_id = data.get("cabinet_id")
    cabinet = (await db.execute(_cabinets_visible_to(
        select(Cabinet).where(Cabinet.id == cabinet_id), current_user))).scalar_one_or_none()
    if not cabinet:
        raise HTTPException(status_code=400, detail="کمد نامعتبر است")

    nxt = (await db.execute(select(func.max(Binder.sort_order))
                            .where(Binder.cabinet_id == cabinet_id))).scalar() or 0
    kind = data.get("kind") if data.get("kind") in VALID_KINDS else "property"
    deal = data.get("deal_type") if data.get("deal_type") in VALID_DEALS else ""
    binder = Binder(
        cabinet_id=cabinet_id, name=name[:120],
        color=data.get("color") or cabinet.color,
        kind=kind, deal_type=deal,
        description=(data.get("description") or "")[:300] or None,
        sort_order=nxt + 1,
    )
    db.add(binder)
    await db.commit()
    await db.refresh(binder)
    return binder.to_dict(file_count=0)


@router.patch("/binders/{binder_id}")
async def update_binder(
    binder_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    binder = (await db.execute(_cabinets_visible_to(
        select(Binder).join(Cabinet, Binder.cabinet_id == Cabinet.id)
        .where(Binder.id == binder_id), current_user))).scalar_one_or_none()
    if not binder:
        raise HTTPException(status_code=404, detail="زونکن یافت نشد")
    if "name" in data and (data["name"] or "").strip():
        binder.name = data["name"].strip()[:120]
    if data.get("color"):
        binder.color = data["color"]
    if data.get("kind") in VALID_KINDS:
        binder.kind = data["kind"]
    if "deal_type" in data and data["deal_type"] in VALID_DEALS:
        binder.deal_type = data["deal_type"]
    if "description" in data:
        binder.description = (data["description"] or "")[:300] or None
    if "cabinet_id" in data and data["cabinet_id"]:
        moved = (await db.execute(_cabinets_visible_to(
            select(Cabinet).where(Cabinet.id == data["cabinet_id"]),
            current_user))).scalar_one_or_none()
        if not moved:
            raise HTTPException(status_code=400, detail="کمد مقصد یافت نشد")
        binder.cabinet_id = moved.id
    await db.commit()
    await db.refresh(binder)
    return binder.to_dict()


@router.delete("/binders/{binder_id}")
async def delete_binder(
    binder_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_filing_admin),
):
    """Admin-only, like deleting a cabinet: one click unfiles every file on
    the binder behind it."""
    binder = (await db.execute(_cabinets_visible_to(
        select(Binder).join(Cabinet, Binder.cabinet_id == Cabinet.id)
        .where(Binder.id == binder_id), current_user))).scalar_one_or_none()
    if not binder:
        raise HTTPException(status_code=404, detail="زونکن یافت نشد")
    props = (await db.execute(
        select(Property).where(Property.binder_id == binder_id))).scalars().all()
    for p in props:
        p.binder_id = None          # unfile, never delete the file itself
    await db.delete(binder)
    await db.commit()
    return {"success": True, "unfiled": len(props)}


# ── the files inside ────────────────────────────────────────────────────

def _file_brief(p: Property) -> dict:
    return {
        "id": p.id, "serial_no": p.serial_no, "title": p.title,
        "city_name": p.city_name, "district": p.district,
        "area": p.area, "rooms": p.rooms,
        "listing_type": p.listing_type, "property_type": p.property_type,
        "price": (p.deposit or p.rent_price) if p.listing_type == "rent"
                 else (p.total_price or p.price),
        "rent_price": p.rent_price, "deposit": p.deposit,
        "phone_number": p.phone_number, "seller_name": p.seller_name,
        "thumbnail_url": p.thumbnail_url, "url": p.url,
        "binder_id": p.binder_id,
        "is_pinned": bool(p.is_pinned), "is_archived": bool(p.is_archived),
        "is_private": bool(p.is_private), "is_draft": bool(p.is_draft),
        "created_by": p.created_by,
        "tags": split_tags(p.tags),
        "scraped_at": p.scraped_at.isoformat() if p.scraped_at else None,
    }


@router.get("/files")
async def list_files(
    binder_id: Optional[int] = None,
    unfiled: bool = False,
    archived: bool = False,
    search: Optional[str] = None,
    tag: Optional[str] = None,
    # ── جستجوی پیشرفته: the variables a consultant actually asks about ──
    price_min: Optional[int] = None,
    price_max: Optional[int] = None,
    area_min: Optional[int] = None,
    area_max: Optional[int] = None,
    rooms_min: Optional[int] = None,
    district: Optional[str] = None,
    property_type: Optional[str] = None,
    listing_type: Optional[str] = None,
    has_elevator: Optional[bool] = None,
    has_parking: Optional[bool] = None,
    has_storage: Optional[bool] = None,
    pinned: Optional[bool] = None,
    limit: int = Query(60, ge=1, le=300),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Files in a binder — pinned first, then newest."""
    q = select(Property).where(Property.is_active == True)   # noqa: E712
    q = _visible_to(q, current_user)
    q = q.where(Property.is_archived == bool(archived))

    if unfiled:
        q = q.where(Property.binder_id.is_(None))
    elif binder_id is not None:
        q = q.where(Property.binder_id == binder_id)

    if tag:
        q = q.where(Property.tags.ilike(f"%{tag}%"))

    # A rent listing's headline number is its deposit, a sale's is the total,
    # so the price band has to be applied against whichever one applies.
    headline = func.coalesce(
        Property.total_price, Property.price, Property.deposit, Property.rent_price)
    if price_min is not None:
        q = q.where(headline >= price_min)
    if price_max is not None:
        q = q.where(headline <= price_max)
    if area_min is not None:
        q = q.where(Property.area >= area_min)
    if area_max is not None:
        q = q.where(Property.area <= area_max)
    if rooms_min is not None:
        q = q.where(Property.rooms >= rooms_min)
    if district:
        like = f"%{district.strip()}%"
        q = q.where(or_(Property.district.ilike(like),
                        Property.neighborhood.ilike(like),
                        Property.address.ilike(like)))
    if property_type:
        like = f"%{property_type.strip()}%"
        q = q.where(or_(Property.property_type.ilike(like),
                        Property.category_name.ilike(like),
                        Property.title.ilike(like)))
    if listing_type:
        q = q.where(Property.listing_type == listing_type)
    for flag, column in ((has_elevator, Property.has_elevator),
                         (has_parking, Property.has_parking),
                         (has_storage, Property.has_storage),
                         (pinned, Property.is_pinned)):
        if flag:                       # only ever a positive filter
            q = q.where(column == True)   # noqa: E712
    if search:
        raw = search.strip()
        term = f"%{raw}%"
        filters = [Property.title.ilike(term), Property.address.ilike(term),
                   Property.district.ilike(term), Property.seller_name.ilike(term),
                   Property.phone_number.ilike(term), Property.tags.ilike(term)]
        if raw.isdigit():
            filters.append(Property.serial_no == int(raw))
        q = q.where(or_(*filters))

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(
        q.order_by(Property.is_pinned.desc(), Property.serial_no.desc())
        .limit(limit).offset(offset))).scalars().all()
    return {"items": [_file_brief(p) for p in rows], "total": total}


@router.post("/files/bulk")
async def bulk_file_action(
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Move / pin / archive / mark private / tag a selection of files.

    body: {"ids":[…], "action":"move|pin|unpin|archive|unarchive|private|
                                public|draft|undraft|tag|untag",
           "binder_id":…, "tags":"…"}

    The selection is passed through the same visibility gate as every read,
    so one consultant cannot reach into another's «فایل شخصی» by id — ids run
    in sequence, which made guessing them trivial. Files that were filtered
    out come back as `skipped` rather than failing the whole call.
    """
    ids = [int(i) for i in (data.get("ids") or []) if str(i).isdigit()][:500]
    action = data.get("action")
    if not ids:
        raise HTTPException(status_code=400, detail="هیچ فایلی انتخاب نشده است")

    props = (await db.execute(_visible_to(
        select(Property).where(Property.id.in_(ids)), current_user))).scalars().all()
    if not props:
        raise HTTPException(status_code=404, detail="فایلی یافت نشد")
    skipped = len(set(ids)) - len(props)

    if action == "move":
        binder_id = data.get("binder_id")
        if binder_id:
            exists = (await db.execute(_cabinets_visible_to(
                select(Binder.id).join(Cabinet, Binder.cabinet_id == Cabinet.id)
                .where(Binder.id == binder_id), current_user))).scalar_one_or_none()
            if not exists:
                raise HTTPException(status_code=400, detail="زونکن مقصد یافت نشد")
        for p in props:
            p.binder_id = binder_id or None
    elif action in ("pin", "unpin"):
        for p in props:
            p.is_pinned = action == "pin"
    elif action in ("archive", "unarchive"):
        for p in props:
            p.is_archived = action == "archive"
    elif action in ("private", "public"):
        for p in props:
            p.is_private = action == "private"
            if action == "private" and not p.created_by:
                # otherwise nobody but a super_admin could ever see it again
                p.created_by = _actor(current_user)
    elif action in ("draft", "undraft"):
        for p in props:
            p.is_draft = action == "draft"
    elif action in ("tag", "untag"):
        wanted = split_tags(data.get("tags"))
        if not wanted:
            raise HTTPException(status_code=400, detail="برچسبی وارد نشده است")
        for p in props:
            current = split_tags(p.tags)
            if action == "tag":
                current += [t for t in wanted if t not in current]
            else:
                current = [t for t in current if t not in wanted]
            p.tags = join_tags(current)
    else:
        raise HTTPException(status_code=400, detail="عملیات نامعتبر است")

    await db.commit()
    return {"success": True, "updated": len(props), "skipped": skipped, "action": action}


@router.get("/tags")
async def list_tags(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every tag in use, with how many files carry it — powers the tag filter."""
    rows = (await db.execute(
        _visible_to(select(Property.tags).where(Property.tags.isnot(None),
                                                Property.is_active == True),  # noqa: E712
                    current_user))).scalars().all()
    counts: dict = {}
    for blob in rows:
        for name in split_tags(blob):
            counts[name] = counts.get(name, 0) + 1
    items = sorted(({"name": k, "count": v} for k, v in counts.items()),
                   key=lambda x: (-x["count"], x["name"]))
    return {"items": items, "total": len(items)}


@router.get("/overview")
async def filing_overview(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Counts for the header strip: filed, unfiled, pinned, archived, private."""
    async def count(*where):
        q = _visible_to(select(func.count(Property.id))
                        .where(Property.is_active == True, *where), current_user)  # noqa: E712
        return (await db.execute(q)).scalar_one()

    return {
        "filed": await count(Property.binder_id.isnot(None), Property.is_archived == False),  # noqa: E712
        "unfiled": await count(Property.binder_id.is_(None), Property.is_archived == False),  # noqa: E712
        "pinned": await count(Property.is_pinned == True, Property.is_archived == False),     # noqa: E712
        "archived": await count(Property.is_archived == True),                                # noqa: E712
        "private": await count(Property.is_private == True),                                  # noqa: E712
    }


# ── اشتراک‌گذاری با مشتری ───────────────────────────────────────────────

# Never leaves the office: the owner's identity is the consultant's asset,
# and a shared card that carries it cuts them out of their own deal.
_SHARE_STRIPPED = ("phone_number", "seller_name", "owner_phone", "url", "address")


def _fa_price(n: Optional[int]) -> str:
    if not n:
        return "توافقی"
    if n >= 1_000_000_000:
        v = n / 1_000_000_000
        return (f"{v:.3f}".rstrip("0").rstrip(".")) + " میلیارد تومان"
    if n >= 1_000_000:
        return f"{round(n / 1_000_000)} میلیون تومان"
    # «/» is the thousands separator this panel uses everywhere for money
    return f"{n:,}".replace(",", "/") + " تومان"


def build_share_card(p: Property, include_area: bool = True) -> dict:
    """A presentable card for the customer, with the confidential bits gone.

    Address is reduced to district/neighbourhood — enough to judge the
    location, not enough to knock on the door and skip the agency.
    """
    lines = [p.title or "ملک"]
    spec = []
    if p.area:
        spec.append(f"متراژ {p.area} متر")
    if p.rooms is not None:
        spec.append(f"{p.rooms} خواب")
    if p.year_built:
        spec.append(f"ساخت {p.year_built}")
    if p.floor is not None:
        spec.append(f"طبقه {p.floor}")
    if spec:
        lines.append(" • ".join(spec))

    where = " ، ".join(filter(None, [p.city_name, p.district, p.neighborhood]))
    if where:
        lines.append(f"موقعیت: {where}")

    if p.listing_type == "rent":
        lines.append(f"ودیعه: {_fa_price(p.deposit)}")
        lines.append(f"اجاره ماهانه: {_fa_price(p.rent_price)}")
    else:
        lines.append(f"قیمت: {_fa_price(p.total_price or p.price)}")
        if p.price_per_meter:
            lines.append(f"هر متر: {_fa_price(p.price_per_meter)}")

    amen = [fa for attr, fa in (("has_elevator", "آسانسور"), ("has_parking", "پارکینگ"),
                                ("has_storage", "انباری"), ("has_balcony", "بالکن"))
            if getattr(p, attr, False)]
    if amen:
        lines.append("امکانات: " + " ، ".join(amen))
    for label, value in (("سند", p.document_type), ("جهت", p.building_direction),
                         ("نبش", p.corner_type)):
        if value:
            lines.append(f"{label}: {value}")

    lines.append(f"کد ملک: {p.serial_no}")
    lines.append("املاک سورین")
    return {
        "text": "\n".join(lines),
        "serial_no": p.serial_no,
        "images": (p.images or [])[:6],
        "removed": [f for f in _SHARE_STRIPPED if getattr(p, f, None)],
    }


@router.get("/files/{property_id}/share")
async def share_file(
    property_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The customer-safe version of a file, ready to paste into a chat."""
    prop = (await db.execute(_visible_to(
        select(Property).where(Property.id == property_id), current_user))).scalar_one_or_none()
    if not prop:
        raise HTTPException(status_code=404, detail="فایل یافت نشد")
    return build_share_card(prop)

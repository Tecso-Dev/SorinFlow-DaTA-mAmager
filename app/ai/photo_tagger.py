"""
برچسب‌زن عکس — what is actually IN a listing's photographs.

The scraper downloads the gallery, and the «هوش تصویری» section measures
what a number can measure: sharpness, exposure, duplicates. It cannot say
whether the flat is renovated or forty years old, furnished or bare, whether
the third «photo» is a floor plan or an agency's ad card, or whose logo sits
in the corner. A consultant answers those by opening every listing, so
nobody filters on them.

The vision job answers them instead: the first MAX_PHOTOS photos, shrunk to
MAX_SIDE px so a listing costs a fraction of a cent, a fixed vocabulary held
to a schema, the answer stored on the row with the prompt version and the
model — so «فقط بازسازی‌شده» can be a filter, and the section says what the
camera saw, not only how sharp it was.

Background only, never in the scrape path: a cursor over properties.id, a
pass every TICK_SECONDS, the gate and the ledger inherited from
app/services/llm. A listing with no photos on disk is stamped once and not
looked at again; one the model could not answer for keeps ai_photos_at
empty and is retried on a later pass.
"""
import asyncio
import base64
import io
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Literal, Optional

from loguru import logger
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.config import get_settings
from app.database import async_session_maker
from app.models.property import Property
from app.services import llm, secret_box

settings = get_settings()

PROMPT_VERSION = 1      # stored beside the tags; bump when the question or the vocabulary changes
MAX_PHOTOS = 3          # the first three say what the gallery is; the rest cost the same and add little
MAX_SIDE = 512          # px, long side — enough to tell a renovated kitchen from an old one
JPEG_QUALITY = 70
BATCH = 30              # listings per pass
TICK_SECONDS = 300
KEY_CURSOR = "ai_photo_cursor"
AGENT = "vision"        # the ledger's name for this agent

Condition = Literal["renovated", "normal", "old", "under_construction"]
Room = Literal["living", "bedroom", "kitchen", "bathroom", "balcony", "exterior", "parking", "yard", "other"]


class PhotoTags(BaseModel):
    """The vocabulary. None means «cannot tell from these photos», which is
    an honest answer and better than a guess."""
    condition: Optional[Condition] = None
    furnished: Optional[bool] = None
    rooms_shown: List[Room] = Field(default_factory=list)
    exterior_shown: bool = False
    floor_plan: bool = False            # a drawing, not a photograph
    text_or_logo: bool = False          # a text card, an ad banner, a logo
    watermark: Optional[str] = None     # the agency's name when it is legible
    quality: Optional[int] = Field(None, ge=1, le=5)
    notes: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)


SYSTEM = (
    "You are a precise real-estate photo annotator. You see up to three photos of ONE listing. "
    "Report only what is visible in the photos; never infer from the title. Answer with JSON only, "
    "exactly these keys:\n"
    '"condition": "renovated" | "normal" | "old" | "under_construction" | null '
    "— وضعیت بنا: بازسازی‌شده یا نوساز، معمولی، قدیمی، در حال ساخت؛ null وقتی از عکس معلوم نیست\n"
    '"furnished": true | false | null — مبله یا خالی\n'
    '"rooms_shown": array from "living","bedroom","kitchen","bathroom","balcony","exterior","parking","yard","other" '
    "— فضاهایی که در عکس‌ها دیده می‌شود\n"
    '"exterior_shown": true | false — نمای بیرونی ساختمان دیده می‌شود\n'
    '"floor_plan": true | false — یکی از تصویرها نقشه یا پلان است، نه عکس\n'
    '"text_or_logo": true | false — یکی از تصویرها کارت متنی، بنر تبلیغاتی یا لوگو است\n'
    '"watermark": string | null — نام مشاور یا آژانس اگر روی عکس خوانا باشد، وگرنه null\n'
    '"quality": 1 | 2 | 3 | 4 | 5 | null — برداشت کلی از کیفیت عکاسی: نور، وضوح، کادر\n'
    '"notes": string — حداکثر ۱۵ کلمه فارسی؛ رشتهٔ خالی اگر چیزی برای گفتن نیست\n'
    '"confidence": number 0..1'
)


# ── the photos ───────────────────────────────────────────────────────────────

def _local_path(rel: str, root: Path) -> Optional[Path]:
    """`/images/<divar_id>/<file>` → the file under `root`. None for anything
    else: a remote URL that was never downloaded, or a path that tries to
    leave the directory."""
    parts = PurePosixPath(str(rel or "")).parts
    if len(parts) != 4 or parts[:2] != ("/", "images") or any(p in ("..", ".") for p in parts):
        return None
    return root / parts[2] / parts[3]


def prepare_images(prop, images_root: Path) -> List[bytes]:
    """The first MAX_PHOTOS photos that are on disk, as small RGB JPEGs.
    A file that will not open is skipped, not fatal — one corrupt download
    must not cost the listing its tags."""
    out: List[bytes] = []
    for rel in prop.images or []:
        if len(out) >= MAX_PHOTOS:
            break
        path = _local_path(rel, Path(images_root))
        if path is None or not path.is_file():
            continue
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((MAX_SIDE, MAX_SIDE))       # shrinks only, keeps the aspect
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
            out.append(buf.getvalue())
        except Exception as e:
            logger.debug(f"[photo] unreadable {path}: {type(e).__name__}: {e}")
    return out


def build_messages(prop, jpegs: List[bytes]) -> List[Dict[str, Any]]:
    """One system line, one user turn: the masked title and the photos as
    data URLs. The title is context for the notes, not evidence."""
    content: List[Dict[str, Any]] = [
        {"type": "text", "text": f"عنوان آگهی: {llm.mask_pii(prop.title)}\n{len(jpegs)} عکس:"}]
    for jpeg in jpegs:
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")}})
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]


# ── one listing ──────────────────────────────────────────────────────────────

async def tag_property(db, prop, *, images_root=None) -> Optional[Dict[str, Any]]:
    """Look at one listing and store the answer. Returns the tags; None when
    there was nothing to look at (the no-photo marker is stored, so a pass
    does not come back to it) or the model did not answer (nothing stored,
    a later pass retries). NotConfigured, Disabled and BudgetExceeded
    propagate: they end the pass, not just this listing."""
    root = Path(images_root) if images_root else Path(settings.images_path)
    jpegs = prepare_images(prop, root)
    if not jpegs:
        prop.ai_photo_tags = {"skipped": "no_photos", "prompt_version": PROMPT_VERSION}
        prop.ai_photos_at = datetime.now(timezone.utc)
        await db.commit()
        return None
    try:
        out = await llm.chat("vision", build_messages(prop, jpegs), agent=AGENT, db=db,
                             schema=PhotoTags, temperature=0, max_tokens=300)
    except (llm.NotConfigured, llm.Disabled, llm.BudgetExceeded):
        raise
    except llm.LLMError as e:
        logger.warning(f"[photo] listing {prop.id} not tagged: {e}")
        return None
    tags = {**out["data"], "photos": len(jpegs), "prompt_version": PROMPT_VERSION, "model": out["model"]}
    prop.ai_photo_tags = tags
    prop.ai_photos_at = datetime.now(timezone.utc)
    await db.commit()
    return tags


async def retag(db, property_id: int) -> Optional[Dict[str, Any]]:
    """Force one listing, cursor or not — the panel's button. Returns what
    this call stored: the tags, or the no-photo marker; None when the model
    did not answer. LookupError for an id that does not exist; the gate
    errors propagate so the button can say why."""
    prop = (await db.execute(select(Property).where(Property.id == property_id))).scalar_one_or_none()
    if prop is None:
        raise LookupError(property_id)
    before = prop.ai_photos_at
    tags = await tag_property(db, prop)
    if tags is not None:
        return tags
    return prop.ai_photo_tags if prop.ai_photos_at != before else None


# ── the pass ─────────────────────────────────────────────────────────────────

async def _cursor(db) -> int:
    try:
        raw = (await secret_box.get_many(db, (KEY_CURSOR,))).get(KEY_CURSOR)
        return int(raw) if raw else 0
    except Exception:
        return 0


def _pending(cursor: int):
    """Active listings past the cursor that were never looked at and have,
    or should have, photos on disk. `has_images` is «the ad had pictures»,
    `images_downloaded` is «we fetched them»; either is worth a look, and a
    gallery that turns out to be missing costs one stamp, no call."""
    return (select(Property)
            .where(Property.id > cursor, Property.is_active == True,               # noqa: E712
                   Property.ai_photos_at.is_(None),
                   or_(Property.images_downloaded == True, Property.has_images == True))   # noqa: E712
            .order_by(Property.id.asc()))


async def run_once(db, *, limit: int = BATCH) -> Dict[str, Any]:
    """One pass. The cursor only moves past listings that were stored (tags
    or the no-photo marker): a listing the model refused keeps ai_photos_at
    empty and stays in front of the cursor, so the next pass tries it first
    — and the ones after it that did succeed are kept out by ai_photos_at,
    so nothing is paid for twice."""
    since = await _cursor(db)
    props = (await db.execute(_pending(since).limit(limit))).scalars().all()
    res: Dict[str, Any] = {"scanned": len(props), "tagged": 0, "skipped": 0, "failed": 0,
                           "cursor": since, "stopped": None}
    cursor, advancing = since, True
    for p in props:
        try:
            tags = await tag_property(db, p)
        except (llm.NotConfigured, llm.Disabled, llm.BudgetExceeded) as e:
            logger.info(f"[photo] pass ends: {e}")
            res["stopped"] = type(e).__name__
            break
        if tags is not None:
            res["tagged"] += 1
        elif p.ai_photos_at is not None:        # the no-photo marker was stored
            res["skipped"] += 1
        else:
            res["failed"] += 1
            # ponytail: a listing the model refuses on every pass is retried
            # every five minutes; add a failure count to the tags if the
            # ledger ever shows one listing eating the cap
            advancing = False
        if advancing:
            cursor = p.id
    if cursor != since:
        await secret_box.put(db, KEY_CURSOR, str(cursor), "photo_tagger")
    res["cursor"] = cursor
    if props:
        logger.info(f"[photo] scanned {len(props)}, tagged {res['tagged']}, skipped {res['skipped']}, "
                    f"failed {res['failed']}, cursor {cursor}"
                    + (f", stopped: {res['stopped']}" if res["stopped"] else ""))
    return res


async def status(db) -> Dict[str, Any]:
    """The panel's numbers: where the cursor is, how many rows carry tags,
    how many were stamped for having no photos, how many still wait."""
    cursor = await _cursor(db)

    async def count(where) -> int:
        return (await db.execute(select(func.count(Property.id)).where(where))).scalar_one()

    looked = await count(Property.ai_photos_at.isnot(None))
    skipped = await count(Property.ai_photo_tags["skipped"].as_string().isnot(None))
    behind = (await db.execute(select(func.count()).select_from(_pending(cursor).subquery()))).scalar_one()
    last = (await db.execute(select(func.max(Property.ai_photos_at)))).scalar_one()
    cfg = await llm.config(db)
    return {"cursor": cursor, "tagged": looked - skipped, "skipped": skipped, "behind": behind,
            "version": PROMPT_VERSION, "model": cfg["models"].get("vision") or "",
            "enabled": cfg["enabled"], "configured": cfg["configured"],
            "last_at": last.isoformat() if last else None}


async def tick() -> Dict[str, Any]:
    async with async_session_maker() as db:
        try:
            return await run_once(db)
        except Exception as e:
            logger.warning(f"[photo] tick failed: {type(e).__name__}: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            return {"error": type(e).__name__}


async def photo_loop() -> None:
    """Runs for the life of the process. MATCH_ENGINE=0 disables — the same
    switch as the other background readers of the listings."""
    if not getattr(settings, "match_engine", True):
        logger.info("[photo] disabled")
        return
    await asyncio.sleep(240)         # let startup and the first scrape settle
    logger.info(f"[photo] tagger armed — every {TICK_SECONDS // 60} min, {BATCH} listings a pass")
    while True:
        r = await tick() or {}
        # a pass where every listing failed is a prompt or a model problem,
        # not a listing problem: paying for the same failures again in two
        # minutes helps nobody — wait ten ticks, then look again
        backoff = (r.get("failed") or 0) and not (r.get("tagged") or 0)
        await asyncio.sleep(TICK_SECONDS * (10 if backoff else 1))


# ── the words ────────────────────────────────────────────────────────────────

CONDITION_FA = {"renovated": "بازسازی‌شده", "normal": "وضعیت معمولی", "old": "قدیمی",
                "under_construction": "در حال ساخت"}
ROOM_FA = {"living": "پذیرایی", "bedroom": "اتاق خواب", "kitchen": "آشپزخانه", "bathroom": "سرویس بهداشتی",
           "balcony": "بالکن", "exterior": "نما", "parking": "پارکینگ", "yard": "حیاط", "other": "فضای دیگر"}
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def tags_fa(tags: Optional[Dict[str, Any]]) -> List[str]:
    """Chip labels for a stored tags dict — what the panel shows. Words
    outside the vocabulary are not shown rather than shown in English."""
    if not tags:
        return []
    if tags.get("skipped"):
        return ["عکسی روی دیسک نیست"]
    out: List[str] = []
    if tags.get("condition") in CONDITION_FA:
        out.append(CONDITION_FA[tags["condition"]])
    if tags.get("furnished") is True:
        out.append("مبله")
    elif tags.get("furnished") is False:
        out.append("خالی")
    if tags.get("exterior_shown"):
        out.append("نمای بیرونی")
    # the exterior has its own chip above; the rest are the rooms
    rooms = [ROOM_FA[r] for r in tags.get("rooms_shown") or [] if r in ROOM_FA and r != "exterior"]
    if rooms:
        out.append("فضاها: " + "، ".join(dict.fromkeys(rooms)))
    if tags.get("floor_plan"):
        out.append("نقشه به‌جای عکس")
    if tags.get("text_or_logo"):
        out.append("تصویر متنی یا لوگو")
    if tags.get("watermark"):
        out.append(f"لوگوی مشاور: {str(tags['watermark'])[:40]}")
    if tags.get("quality"):
        out.append(f"کیفیت {str(tags['quality']).translate(_FA_DIGITS)}/۵")
    return out

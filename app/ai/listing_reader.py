"""
خوانندهٔ آگهی — the facts a listing states only in its own words.

Divar's structured fields stop at metres, rooms and a price. Half of what a
consultant needs in order to place a listing is only in the free text: the
real kind of property (a house filed under «مغازه» because it suits a café),
the floor and the floors above it, the year, the document (تک‌برگ /
قولنامه‌ای / وقفی / مشاع / سرقفلی), the condition (نوساز / کلنگی), the
amenities the field list forgot, the district when the district field is
empty — and the deal itself: «قابل تبدیل», «معاوضه», «تخلیه», «قابل مذاکره»,
«مناسب کافه». Red flags too: وقفی, بدون سند, مشاع, در رهن بانک.

This agent reads each listing once with the read model and stores what the
text says as Property.ai_facts, one confidence per field, so matching and
«ملک‌های مشابه» can use facts the scraper never had. `effective()` is the
merge rule: the scraped column wins when it is set; a fact fills the gap.

Off the scrape path on purpose: a cursor over properties.id, a pass every
two minutes, and every model failure is a skipped listing, never a lost
scrape. The three gate errors (not configured, switched off, cap reached)
end a pass quietly; the loop tries again next tick.
"""
import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from loguru import logger
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import and_, func, or_, select

from app.config import get_settings
from app.database import async_session_maker
from app.models.property import Property
from app.services import llm, secret_box

settings = get_settings()

PROMPT_VERSION = 1          # bump when the prompt pack changes: the cursor restarts and every listing is re-read
KEY_CURSOR = "ai_reader_cursor"
BATCH = 50
TICK_SECONDS = 120
START_DELAY = 150           # the scraper, the matcher and the digest arm first
MAX_ATTEMPTS = 3            # consecutive failures on the SAME content before a listing is skipped
# Not the listing's fault — a gateway state, not a bad ad — so these never
# count against MAX_ATTEMPTS. CircuitOpen/RateLimited are not in this branch
# yet (another stream is adding them to app/services/llm.py); referenced
# defensively so this file works before and after that lands.
_GATEWAY_STATE = tuple(getattr(llm, n) for n in ("CircuitOpen", "RateLimited") if hasattr(llm, n))
MAX_TEXT = 2500             # a Divar description is rarely longer; the rest is the same words again
# The spec said 400. The third few-shot's own answer is ~350 tokens of
# Persian JSON, so 400 truncates a listing with a long red-flags list and
# turns it into a retry that fails the same way. 600 reads them all.
MAX_TOKENS = 600
KIND_OVERRIDE = 0.8         # a fact overrides a scraped kind only when the model is this sure

KINDS = ("apartment", "house", "land", "shop", "office", "other")
FAMILIES = KINDS[:-1]       # the names match_service.property_family() uses
DOCUMENTS = ("تک‌برگ", "قولنامه‌ای", "وقفی", "مشاع", "سرقفلی", "نامشخص")
CONDITIONS = ("نوساز", "بازسازی‌شده", "معمولی", "کلنگی", "قدیمی")

_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_TRUE = ("true", "1", "بله", "دارد", "هست", "yes")
_FALSE = ("false", "0", "خیر", "ندارد", "نیست", "no")


# ── the answer ───────────────────────────────────────────────────────────────

class ListingFacts(BaseModel):
    """What the text says, nothing more. Unknown is None — the prompt says so
    twice — and the validators turn a near-miss (a two-digit year, Persian
    digits, a word outside the vocabulary, «بله» for a boolean) into a value
    or None rather than a refusal that costs a retry and loses the listing."""
    model_config = ConfigDict(extra="ignore")

    kind: Optional[Literal["apartment", "house", "land", "shop", "office", "other"]] = None
    floor: Optional[int] = None
    total_floors: Optional[int] = None
    year_built: Optional[int] = None            # Jalali, 1300–1410
    document: Optional[str] = None              # one of DOCUMENTS
    condition: Optional[str] = None             # one of CONDITIONS
    has_elevator: Optional[bool] = None
    has_parking: Optional[bool] = None
    has_storage: Optional[bool] = None
    has_balcony: Optional[bool] = None
    district: Optional[str] = None              # as written in the text
    convertible: Optional[bool] = None          # قابل تبدیل
    exchange: Optional[bool] = None             # معاوضه
    vacant: Optional[bool] = None               # تخلیه / فوری
    negotiable: Optional[bool] = None           # قابل مذاکره
    suitable_for: List[str] = []
    red_flags: List[str] = []
    summary: str = ""
    confidence: Dict[str, float] = {}

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):
        return v if v in KINDS else None

    @field_validator("floor", "total_floors", "year_built", mode="before")
    @classmethod
    def _int(cls, v):
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, str):
            m = re.search(r"-?\d+", v.translate(_FA_DIGITS))
            if not m:
                return None
            v = m.group()
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    @field_validator("year_built")
    @classmethod
    def _year(cls, v):
        if v is not None and 0 < v < 100:
            v += 1300                       # «ساخت ۹۸» is ۱۳۹۸
        return v if v is not None and 1300 <= v <= 1410 else None

    @field_validator("has_elevator", "has_parking", "has_storage", "has_balcony",
                     "convertible", "exchange", "vacant", "negotiable", mode="before")
    @classmethod
    def _bool(cls, v):
        if isinstance(v, bool) or v is None:
            return v
        s = str(v).strip().lower()
        return True if s in _TRUE else False if s in _FALSE else None

    @field_validator("document", "condition", "district", "summary", mode="before")
    @classmethod
    def _text(cls, v):
        return str(v).strip() if v is not None else None

    @field_validator("document")
    @classmethod
    def _document(cls, v):
        return v if v in DOCUMENTS else None

    @field_validator("condition")
    @classmethod
    def _condition(cls, v):
        return v if v in CONDITIONS else None

    @field_validator("summary", mode="before")
    @classmethod
    def _summary(cls, v):
        # the input was masked, so nothing real can be here; belt and braces
        return llm.mask_pii(str(v or ""))[:200]

    @field_validator("suitable_for", "red_flags", mode="before")
    @classmethod
    def _list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return [str(x).strip() for x in v if str(x).strip()][:8]

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, v):
        out = {}
        for k, x in (v or {}).items() if isinstance(v, dict) else ():
            try:
                out[str(k)] = min(1.0, max(0.0, float(x)))
            except (TypeError, ValueError):
                pass
        return out


# ── the prompt pack ──────────────────────────────────────────────────────────

ROLE = ("تو استخراج‌کنندهٔ دقیق مشخصات از متن آگهی‌های املاک دیوار هستی، نه مشاور: "
        "فقط آنچه را که در متن آمده به JSON تبدیل می‌کنی و چیزی به آن اضافه نمی‌کنی.")

GLOSSARY = """واژه‌نامهٔ بازار:
- رهن کامل: اجارهٔ ماهانه صفر؛ همهٔ مبلغ ودیعه است.
- تبدیل / قابل تبدیل: نسبت ودیعه و اجاره با توافق عوض می‌شود (ودیعهٔ بیشتر و اجارهٔ کمتر یا برعکس).
- معاوضه: مالک حاضر است ملک را با ملک یا خودروی دیگر عوض کند.
- تخلیه: واحد خالی است و فوراً تحویل می‌شود؛ «فوری» و «آمادهٔ تحویل» هم همین است.
- کلنگی: بنای فرسوده که برای تخریب و ساخت فروخته می‌شود؛ ارزش در زمین است.
- بازسازی / بازسازی‌شده: بنای قدیمی که تعمیر اساسی شده.
- نوساز: تازه‌ساخت یا با عمر کمتر از حدود سه سال.
- سند تک‌برگ: سند رسمی ثبتی به نام مالک؛ معتبرترین نوع سند.
- قولنامه‌ای: فقط قرارداد دستی، بدون سند رسمی.
- وقفی: زمین متعلق به اوقاف است و مالک فقط اعیانی (بنا) را دارد.
- مشاع: سند مشترک با چند مالک، بدون تفکیک.
- سرقفلی: حق کسب‌وکار در مغازه، نه مالکیت خود ملک.
- در رهن بانک: ملک وثیقهٔ وام است و انتقال آن مشروط به تسویه است.
- پیش‌فروش: بنای ناتمام که پیش از پایان ساخت فروخته می‌شود.
- بر: طول ضلع رو به معبر، به متر. نبش: در گوشهٔ دو معبر (تک‌نبش، دونبش).
- کوچه / بن‌بست: معبر فرعی؛ بن‌بست یعنی کوچهٔ بدون خروجی — معمولاً آرام‌تر."""

# Seeds, not truth: the office corrects and extends this list as it reads
# what the reader wrote; a wrong seed costs one PROMPT_VERSION bump. Each
# canonical name carries the spellings Divar posters actually use, so
# «خ گلها» and «خیابان گلها» come back as the same district.
DISTRICT_HINTS = (
    ("خیابان گلها", ("خ گلها", "گلها")),
    ("بلوار سعدی", ("سعدی",)),
    ("خیابان کاشانی", ("خ کاشانی", "کاشانی")),
    ("خیابان مدرس", ("خ مدرس", "مدرس")),
    ("بلوار استادان", ("استادان",)),
    ("خیابان دانشکده", ("دانشکده", "خ دانشکده")),
    ("خیابان فرهنگ", ("خ فرهنگ", "فرهنگ")),
    ("بلوار امین", ("امین",)),
    ("جاده امامزاده", ("امامزاده",)),
    ("خیابان شهید سرخوش", ("سرخوش", "شهید سرخوش")),
    ("باغ ملی", ("باغ‌ملی",)),
    ("خیابان امام", ("خ امام",)),
    ("خیابان ورزش", ("ورزش",)),
    ("بلوار والفجر", ("والفجر",)),
    ("شهرک فرهنگیان", ("فرهنگیان",)),
    ("خیابان براعتی", ("براعتی",)),
    ("خیابان مولوی", ("مولوی",)),
    ("بلوار رودکی", ("رودکی",)),
)

RULES = """قواعد:
۱. فقط آنچه در متن آگهی صریحاً آمده. هر چیزی که گفته نشده null است؛ حدس نزن و از روی شهر یا قیمت نتیجه نگیر.
۲. برای هر فیلدی که مقدار دادی، در confidence عددی بین ۰ و ۱ بگذار (۱ یعنی با همین صراحت در متن آمده). فیلدهای null در confidence نمی‌آیند.
۳. فیلدهای اسکرپ‌شده فقط زمینه‌اند و از خود دیوار آمده‌اند. اگر متن با آن‌ها تناقض دارد، مقدار متن را بده و تناقض را در red_flags بنویس (مثلاً «فیلد می‌گوید طبقه ۱، متن می‌گوید ۳»). بدون ذکر در red_flags با فیلدها مخالفت نکن.
۴. kind نوع واقعی ملک به گفتهٔ متن است: apartment، house، land، shop، office، other. خانهٔ کلنگی که برای زمینش فروخته می‌شود land است؛ خانه‌ای که در فیلد «مغازه» ثبت شده ولی متن خانه می‌گوید house است.
۵. document فقط یکی از: تک‌برگ، قولنامه‌ای، وقفی، مشاع، سرقفلی، نامشخص («نامشخص» فقط وقتی متن خودش می‌گوید سند ندارد یا در دست اقدام است). condition فقط یکی از: نوساز، بازسازی‌شده، معمولی، کلنگی، قدیمی.
۶. year_built سال شمسی چهاررقمی (۱۳۰۰ تا ۱۴۱۰). «۱۰ سال ساخت» را به سال تبدیل نکن.
۷. district همان‌طور که در متن نوشته شده. فهرست محله‌های شناخته‌شده فقط برای تشخیص املای دیگر است، نه برای حدس زدن.
۸. suitable_for: کاربری‌هایی که خود متن پیشنهاد می‌کند (کافه، رستوران، انبار، مطب، دفتر…). red_flags: وقفی، بدون سند، مشاع، در رهن بانک، قولنامه‌ای، پیش‌فروش، و هر تناقض با فیلدها.
۹. has_elevator و مانند آن: true وقتی متن می‌گوید دارد، false وقتی می‌گوید ندارد، null وقتی نگفته. negotiable: true برای «قابل مذاکره / توافقی»، false برای «مقطوع».
۱۰. summary یک خط فارسی حداکثر ۲۰ کلمه، بدون شماره تلفن و بدون قیمت.
۱۱. فقط JSON با همین کلیدها برگردان: kind, floor, total_floors, year_built, document, condition, has_elevator, has_parking, has_storage, has_balcony, district, convertible, exchange, vacant, negotiable, suitable_for, red_flags, summary, confidence — بدون توضیح، بدون متن اضافه."""

# Three listings the way Divar posters write them, with the exact answer
# wanted: a sale that fills the empty district field, a shop rental that is
# «قابل تبدیل», and an old house filed as a shop because it suits a café.
EXAMPLES = (
    {
        "title": "آپارتمان ۹۵ متری خ گلها، طبقه سوم، سند تک‌برگ",
        "description": "آپارتمان ۹۵ متر، ۲ خواب، طبقه ۳ از ۵، ساخت ۱۳۹۸، آسانسور، پارکینگ، انباری. سند تک‌برگ.\n"
                       "نورگیر عالی، کابینت هایگلاس، پکیج. قیمت مقطوع. بازدید با هماهنگی قبلی.",
        "fields": {"نوع ملک": "آپارتمان", "نوع آگهی": "فروش", "متراژ": 95, "اتاق": 2, "طبقه": 3,
                   "سال ساخت": 1398, "منطقه": None, "شهر": "ارومیه", "ودیعه": None, "اجاره": None,
                   "قیمت": 4200000000},
        "answer": {"kind": "apartment", "floor": 3, "total_floors": 5, "year_built": 1398,
                   "document": "تک‌برگ", "condition": None,
                   "has_elevator": True, "has_parking": True, "has_storage": True, "has_balcony": None,
                   "district": "خیابان گلها", "convertible": None, "exchange": None, "vacant": None,
                   "negotiable": False, "suitable_for": [], "red_flags": [],
                   "summary": "آپارتمان ۹۵ متری دوخوابه در خیابان گلها، طبقه سوم از پنج، سند تک‌برگ، با آسانسور و پارکینگ",
                   "confidence": {"kind": 1, "floor": 1, "total_floors": 1, "year_built": 1, "document": 1,
                                  "has_elevator": 1, "has_parking": 1, "has_storage": 1, "district": 0.9,
                                  "negotiable": 0.8}},
    },
    {
        "title": "مغازه ۳۰ متری بر خیابان کاشانی، رهن و اجاره",
        "description": "مغازه ۳۰ متر، بر ۴ متر، کف سرامیک، سرویس بهداشتی، برق سه‌فاز.\n"
                       "۱۵۰ میلیون رهن، ۸ میلیون اجاره، قابل تبدیل. سرقفلی نیست، مالکیت.\n"
                       "تخلیه، آمادهٔ تحویل. مناسب موبایل‌فروشی و لوازم آرایشی.",
        "fields": {"نوع ملک": "مغازه", "نوع آگهی": "رهن و اجاره", "متراژ": 30, "اتاق": None, "طبقه": None,
                   "سال ساخت": None, "منطقه": "خیابان کاشانی", "شهر": "ارومیه", "ودیعه": 150000000,
                   "اجاره": 8000000, "قیمت": None},
        "answer": {"kind": "shop", "floor": None, "total_floors": None, "year_built": None,
                   "document": None, "condition": None,
                   "has_elevator": None, "has_parking": None, "has_storage": None, "has_balcony": None,
                   "district": "خیابان کاشانی", "convertible": True, "exchange": None, "vacant": True,
                   "negotiable": None, "suitable_for": ["موبایل‌فروشی", "لوازم آرایشی"], "red_flags": [],
                   "summary": "مغازه ۳۰ متری با بر ۴ متر در خیابان کاشانی، تخلیه، رهن و اجارهٔ قابل تبدیل، مناسب موبایل‌فروشی",
                   "confidence": {"kind": 1, "district": 1, "convertible": 1, "vacant": 1, "suitable_for": 1}},
    },
    {
        "title": "خانه قدیمی ۲۰۰ متری با حیاط، بلوار سعدی",
        "description": "خانه ویلایی قدیمی، ۲۰۰ متر زمین، ۱۲۰ متر بنا، دونبش، حیاط بزرگ با درخت. بازسازی نشده.\n"
                       "سند قولنامه‌ای. مناسب کافه، رستوران یا مهدکودک. قابل معاوضه با آپارتمان. قیمت توافقی.",
        "fields": {"نوع ملک": "مغازه", "نوع آگهی": "فروش", "متراژ": 200, "اتاق": 3, "طبقه": None,
                   "سال ساخت": None, "منطقه": "بلوار سعدی", "شهر": "ارومیه", "ودیعه": None, "اجاره": None,
                   "قیمت": 9000000000},
        "answer": {"kind": "house", "floor": None, "total_floors": None, "year_built": None,
                   "document": "قولنامه‌ای", "condition": "قدیمی",
                   "has_elevator": None, "has_parking": None, "has_storage": None, "has_balcony": None,
                   "district": "بلوار سعدی", "convertible": None, "exchange": True, "vacant": None,
                   "negotiable": True, "suitable_for": ["کافه", "رستوران", "مهدکودک"],
                   "red_flags": ["سند قولنامه‌ای", "فیلد نوع ملک «مغازه» است ولی متن خانهٔ ویلایی می‌گوید"],
                   "summary": "خانه ویلایی قدیمی دونبش ۲۰۰ متری با حیاط در بلوار سعدی، سند قولنامه‌ای، مناسب کافه و رستوران، قابل معاوضه",
                   "confidence": {"kind": 0.95, "document": 1, "condition": 0.9, "district": 1, "exchange": 1,
                                  "negotiable": 1, "suitable_for": 1}},
    },
)

_FA_DEAL = {"buy": "فروش", "rent": "رهن و اجاره"}


def _district_hints() -> str:
    return "محله‌های شناخته‌شدهٔ ارومیه و املاهای دیگرشان:\n" + "\n".join(
        f"- {name}: {'، '.join(variants)}" for name, variants in DISTRICT_HINTS)


def _listing_text(title: Optional[str], description: Optional[str], fields: Dict[str, Any]) -> str:
    """The listing as the model sees it: the owner's words masked, the scraped
    fields as context. The few-shots use the same shape, so the model has
    seen the format three times before the real one."""
    ctx = "، ".join(f"{k}: {v if v not in (None, '') else '—'}" for k, v in fields.items())
    return (f"عنوان: {llm.mask_pii(title)}\n"
            f"متن:\n{llm.mask_pii(description)[:MAX_TEXT] or '—'}\n\n"
            f"فیلدهای اسکرپ‌شده: {ctx}")


def _fields_of(prop) -> Dict[str, Any]:
    return {"نوع ملک": prop.property_type,
            "نوع آگهی": _FA_DEAL.get(prop.listing_type or "", prop.listing_type),
            "متراژ": prop.area, "اتاق": prop.rooms, "طبقه": prop.floor, "سال ساخت": prop.year_built,
            "منطقه": prop.district, "شهر": prop.city_name,
            "ودیعه": prop.deposit, "اجاره": prop.rent_price, "قیمت": prop.total_price or prop.price}


def build_messages(prop) -> List[Dict[str, str]]:
    """System (role, glossary, district seeds, rules), the three worked
    examples, then this listing. Title and description pass through
    llm.mask_pii: the owner's number is not the model's business."""
    msgs = [{"role": "system", "content": "\n\n".join((ROLE, GLOSSARY, _district_hints(), RULES))}]
    for ex in EXAMPLES:
        msgs.append({"role": "user", "content": _listing_text(ex["title"], ex["description"], ex["fields"])})
        msgs.append({"role": "assistant", "content": json.dumps(ex["answer"], ensure_ascii=False)})
    msgs.append({"role": "user", "content": _listing_text(prop.title, prop.description, _fields_of(prop))})
    return msgs


# ── reading ──────────────────────────────────────────────────────────────────

async def ask(db, prop, *, model: Optional[str] = None, agent: str = "reader") -> Dict[str, Any]:
    """The model's reading of one listing — validated, not stored. Raises
    LLMError the way llm.chat does. `model` is for the bake-off only; `agent`
    is what the ledger row says."""
    out = await llm.chat("read", build_messages(prop), agent=agent, db=db, schema=ListingFacts,
                         temperature=0, max_tokens=MAX_TOKENS, model_override=model)
    return {**out["data"], "model": out["model"]}


async def read_listing(db, prop, *, model: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Read one listing and keep what it said. None when the model gave no
    usable answer — a warning, a failure counted against MAX_ATTEMPTS,
    nothing else committed, retried on a later pass. The gate errors
    (unconfigured, switched off, over budget, and the gateway states in
    _GATEWAY_STATE) propagate so a pass can stop on them without touching
    the listing's own attempt count — they are the gateway's state, not
    this ad's fault."""
    if prop.ai_read_fp is not None and prop.ai_read_fp != prop.ai_content_fp:
        prop.ai_read_attempts = 0      # the content moved; the old failure streak no longer applies
    try:
        facts = await ask(db, prop, model=model)
    except (llm.NotConfigured, llm.Disabled, llm.BudgetExceeded):
        raise
    except _GATEWAY_STATE:
        raise
    except llm.LLMError as e:
        logger.warning(f"[reader] listing {prop.id} not read: {e}")
        prop.ai_read_attempts = (prop.ai_read_attempts or 0) + 1
        prop.ai_read_fp = prop.ai_content_fp
        await db.commit()
        return None
    prop.ai_facts = {**facts, "prompt_version": PROMPT_VERSION}
    prop.ai_read_at = datetime.now(timezone.utc)
    prop.ai_read_attempts = 0
    prop.ai_read_fp = prop.ai_content_fp
    await db.commit()
    return prop.ai_facts


async def reread(db, property_id: int) -> Optional[Dict[str, Any]]:
    """The panel's «بازخوانی»: one listing now, cursor or not. LookupError
    when there is no such listing."""
    prop = await db.get(Property, property_id)
    if prop is None:
        raise LookupError(property_id)
    return await read_listing(db, prop)


# ── the pass ─────────────────────────────────────────────────────────────────

def _content_changed():
    """The content the reader last saw (ai_read_fp, set on every attempt —
    hit or miss) does not match what is on the row now. NULL-safe: a
    listing never attempted counts as changed too."""
    return Property.ai_read_fp.is_distinct_from(Property.ai_content_fp)


def _pending():
    """Active, titled, and not yet read with this prompt — or read, but the
    content moved since (a scraper re-visit changed the description, the
    price a listing is scraped under does not count — see
    Property.ai_content_fp). Capped at MAX_ATTEMPTS consecutive failures
    against the SAME content; a content change lifts the cap, because the
    next attempt is a different question, not a retry of the last one."""
    changed = _content_changed()
    return and_(Property.is_active == True, Property.title != "",       # noqa: E712
                or_(Property.ai_read_at.is_(None),
                    Property.ai_facts["prompt_version"].as_integer() != PROMPT_VERSION,
                    changed),
                or_(changed, Property.ai_read_attempts < MAX_ATTEMPTS))


async def reader_will_run(db) -> bool:
    """True while the reader can plausibly still get to a listing: the
    gateway is configured, both switches (global and «reader») are on, and
    today's cap has room. False is the match engine's cue that waiting for
    a read is pointless — mirrors llm._gate's own checks without reaching
    into its private function."""
    if not llm.configured():
        return False
    cfg = await llm.config(db)
    if not cfg["enabled"] or not await llm.agent_enabled(db, "reader"):
        return False
    return await llm.spent_today(db) < cfg["cap_usd"]


async def _cursor(db) -> int:
    """The id below which every listing has been read with THIS prompt. The
    stored value carries the prompt version, so bumping PROMPT_VERSION
    restarts from zero and the old readings are replaced, oldest first."""
    try:
        raw = (await secret_box.get_many(db, (KEY_CURSOR,))).get(KEY_CURSOR) or ""
        ver, _, pos = raw.partition(":")
        return int(pos) if int(ver) == PROMPT_VERSION else 0
    except Exception:
        return 0


_last_stop = ""


def _quiet_stop(reason: str) -> None:
    """The gate errors are states, not faults: one info line when a reason
    appears, silence while it persists, no traceback ever."""
    global _last_stop
    if reason != _last_stop:
        logger.info(f"[reader] pass stopped: {reason}")
        _last_stop = reason


async def run_once(db, *, limit: int = BATCH) -> Dict[str, Any]:
    """One pass: up to `limit` due listings (_pending — never read at this
    version, or read but stale, and under the attempt cap), oldest id first.

    No id lower bound: a listing behind the cursor whose content changed is
    exactly as due as a new one, so the query is _pending() alone — the
    cursor below is reporting only, not a filter (see the module's stored
    KEY_CURSOR). The scan stays cheap because _pending() excludes the huge
    majority (already read, unchanged) via the partial index on
    ai_read_at IS NULL for the common case.

    The stored cursor only moves past successes. From the first failure of
    a pass it stays put, so a listing the model could not read this time is
    the first thing the next pass reports; listings read after it are
    stored and excluded by _pending(), never paid for twice. A gate error
    ends the pass with the cursor at the last success."""
    global _last_stop
    since = await _cursor(db)
    props = (await db.execute(
        select(Property).where(_pending())
        .order_by(Property.id.asc()).limit(limit))).scalars().all()
    read = failed = 0
    cursor, stopped = since, None
    for p in props:
        try:
            facts = await read_listing(db, p)
        except (llm.NotConfigured, llm.Disabled, llm.BudgetExceeded) as e:
            stopped = type(e).__name__
            _quiet_stop(str(e))
            break
        except _GATEWAY_STATE as e:
            stopped = type(e).__name__
            _quiet_stop(str(e))
            break
        if facts is None:
            # the attempt just cost this listing one of MAX_ATTEMPTS
            # (read_listing); after the last one _pending() stops offering
            # it until its content changes
            failed += 1
        else:
            read += 1
            if not failed:
                cursor = p.id
    if cursor != since:
        await secret_box.put(db, KEY_CURSOR, f"{PROMPT_VERSION}:{cursor}", "ai_reader")
    if read or failed:
        _last_stop = ""
        logger.info(f"[reader] scanned {len(props)}, read {read}, failed {failed}, cursor {cursor}"
                    + (f", stopped: {stopped}" if stopped else ""))
    return {"scanned": len(props), "read": read, "failed": failed, "cursor": cursor, "stopped": stopped}


async def status(db) -> Dict[str, Any]:
    """The panel's numbers: where the cursor is, how many listings carry
    facts, how many still wait, how many gave up (MAX_ATTEMPTS on their
    current content), when the last one was read."""
    async def count(where) -> int:
        return (await db.execute(select(func.count(Property.id)).where(where))).scalar_one()
    active = and_(Property.is_active == True, Property.title != "")      # noqa: E712
    last = (await db.execute(select(func.max(Property.ai_read_at)))).scalar_one()
    capped = and_(active, Property.ai_read_attempts >= MAX_ATTEMPTS, ~_content_changed())
    return {
        "cursor": await _cursor(db),
        "total": await count(active),
        "read": await count(and_(active, Property.ai_read_at.isnot(None))),
        "behind": await count(_pending()),
        "capped": await count(capped),
        "last_read_at": last.isoformat() if last else None,
        "prompt_version": PROMPT_VERSION,
        "model": (await llm.config(db))["models"].get("read") or "",
    }


async def tick() -> Dict[str, Any]:
    async with async_session_maker() as db:
        try:
            # the agent's own switch, beside the global one (llm.agent_enabled)
            if not await llm.agent_enabled(db, "reader"):
                return {"skipped": "disabled"}
            return await run_once(db)
        except Exception as e:
            logger.warning(f"[reader] tick failed: {type(e).__name__}: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            return {"error": type(e).__name__}


async def reader_loop() -> None:
    """Runs for the life of the process. MATCH_ENGINE=0 disables, like the
    matcher and the digest — the three are one feature to the office."""
    if not getattr(settings, "match_engine", True):
        logger.info("[reader] disabled")
        return
    await asyncio.sleep(START_DELAY)
    logger.info(f"[reader] armed — every {TICK_SECONDS} s, {BATCH} listings a pass, prompt v{PROMPT_VERSION}")
    while True:
        r = await tick() or {}
        # a pass where every listing failed is a prompt or a model problem,
        # not a listing problem: paying for the same failures again in two
        # minutes helps nobody — wait ten ticks, then look again
        backoff = (r.get("failed") or 0) and not (r.get("read") or 0)
        await asyncio.sleep(TICK_SECONDS * (10 if backoff else 1))


# ── the merge rule ───────────────────────────────────────────────────────────

def _gap(v: Any) -> bool:
    # a scraped boolean defaults to False, which means «not seen», not «no»
    return v is None or v == "" or v is False


def effective(prop) -> Dict[str, Any]:
    """The listing as matching should see it: the scraped column when it is
    set, the reader's fact where the scraper left a gap.

    kind is property_family(prop) unless that is None, or the fact disagrees
    with confidence ≥ KIND_OVERRIDE — a house filed as «مغازه» because it
    suits a café is a house to a buyer. A scraped True is never overridden;
    False counts as a gap. Everything else has no scraped column and comes
    from the facts alone (None when unread).

    How match_service should use it (the patch is in READER.INTEGRATION.md):
      * wherever property_family(p) is read — score_similarity, rank_similar,
        customer_wants — read effective(p)["kind"] instead;
      * score_similarity: when either side is convertible, keep shape_penalty
        at 1.0 — the owner said the deposit is negotiable, so «ودیعه خیلی
        متفاوت — تبدیل لازم» is not a penalty but the deal on offer;
      * score_similarity: take floor / year_built / has_* from effective(p),
        so amenities that live only in the text still count.
    """
    from app.services.match_service import property_family   # lazy: match_service imports this module
    # getattr: the tests' stubs and rows from before the column have none
    f = getattr(prop, "ai_facts", None) or {}
    conf = f.get("confidence") or {}
    scraped = property_family(prop)
    fact = f.get("kind") if f.get("kind") in FAMILIES else None
    kind = scraped
    if fact and (scraped is None or (fact != scraped and conf.get("kind", 0) >= KIND_OVERRIDE)):
        kind = fact

    def fill(col: str, key: Optional[str] = None):
        v = getattr(prop, col, None)
        return f.get(key or col) if _gap(v) else v

    return {
        "kind": kind,
        "floor": fill("floor"), "total_floors": fill("total_floors"), "year_built": fill("year_built"),
        "district": fill("district"),
        "has_elevator": fill("has_elevator"), "has_parking": fill("has_parking"),
        "has_storage": fill("has_storage"), "has_balcony": fill("has_balcony"),
        "document": fill("document_type", "document"), "condition": f.get("condition"),
        "convertible": f.get("convertible"), "exchange": f.get("exchange"),
        "vacant": f.get("vacant"), "negotiable": f.get("negotiable"),
        "suitable_for": list(f.get("suitable_for") or []), "red_flags": list(f.get("red_flags") or []),
    }

"""
خوانندهٔ نیاز مشتری — free text becomes the intake form's criteria.

A consultant writes what the customer said on the phone («یه واحد ۱۰۰ متری
نوساز طرف گلها تا ۵ میلیارد، طبقهٔ اول نباشه، پارکینگ حتماً»); a portal
visitor types what they want in their own words. The matching engine reads
neither: it scores on Customer's columns — city, district, type, deal, budget,
«۱۰۰ متر / ۲ خواب», the red lines. This agent asks the read model to turn the
text into those columns and post-checks what comes back, so the office types
once and the engine understands.

It proposes; it never writes. The panel fills the empty fields and the
consultant saves; the portal bridge folds what the form left blank into the
customer it is building. Money is asked for as integers in toman, but a model
that answers «۵ میلیارد» is repaired here rather than refused — parse_money
reads Persian digits and the میلیون/میلیارد words. The customer's phone number
never reaches the model (llm.mask_pii); their name is never asked for.
"""
import json
import re
from typing import Any, Dict, List, Literal, Optional

from loguru import logger
from pydantic import BaseModel, ConfigDict, field_validator

from app.services import llm
from app.services.match_service import RENT_TO_DEPOSIT, _family

PROMPT_VERSION = 1
AGENT = "need"
TIMEOUT = 25.0   # a request path waits for this, not the gateway's 40 s

KINDS = ("apartment", "house", "land", "shop", "office")
DEALS = ("buy", "rent")
URGENCIES = ("immediate", "month", "flexible")
# the form's own words for how soon: «🔥 داغ (خرید فوری)», «🌤 گرم», «❄️ سرد»
URGENCY_TO_TEMPERATURE = {"immediate": "hot", "month": "warm", "flexible": "cold"}


# ── numbers the way people write them ────────────────────────────────────────

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_UNITS = {"میلیارد": 10**9, "ملیارد": 10**9, "میلیون": 10**6, "ملیون": 10**6,
          "هزار": 10**3, "تومان": 1, "تومن": 1}
_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(" + "|".join(_UNITS) + r")?")


def parse_money(value: Any) -> Optional[int]:
    """«۵ میلیارد» → 5_000_000_000, «۱۵۰ میلیون» → 150_000_000, «۱٫۵ میلیارد» →
    1_500_000_000, «5000000000» → itself; None for anything without a number.

    Persian and Arabic-Indic digits; the Persian decimal separator «٫» and the
    slash people type for it; thousands separators dropped. Every number that
    carries a unit is summed («۲ میلیارد و ۵۰۰ میلیون»). A bare number next to
    one with a unit is a qualifier («۴ تا ۵ میلیارد» — the ceiling is what a
    budget is), so bare numbers count only when nothing has a unit."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    t = (str(value).translate(_DIGITS).replace("٬", "").replace(",", "")
         .replace("٫", ".").replace("/", "."))
    pairs = [(float(n), u) for n, u in _AMOUNT.findall(t)]
    if not pairs:
        return None
    with_unit = [(n, _UNITS[u]) for n, u in pairs if u]
    total = sum(n * m for n, m in with_unit) if with_unit else pairs[0][0]
    return int(round(total)) or None


def _to_int(value: Any) -> Optional[int]:
    """An integer out of whatever the model wrote — 100, «۱۰۰», «100 متر»."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d+", str(value).translate(_DIGITS))
    return int(m.group()) if m else None


def _to_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = re.split(r"[،,;\n]", value)
    return [s for s in (str(x).strip() for x in value) if s]


# ── the answer ───────────────────────────────────────────────────────────────

class NeedCriteria(BaseModel):
    """What the model says the customer wants. Every field is optional because
    the text decides what is known. The validators forgive the shapes a model
    drifts into — a string for a number, a Persian word for an enum — so one
    loose value does not throw the whole answer away."""
    model_config = ConfigDict(extra="ignore")

    deal_type: Optional[Literal["buy", "rent"]] = None
    kind: Optional[Literal["apartment", "house", "land", "shop", "office"]] = None
    city: Optional[str] = None
    districts: List[str] = []
    budget_max: Optional[int] = None      # toman — a purchase's price ceiling
    deposit_max: Optional[int] = None     # toman — a rental's deposit ceiling
    rent_max: Optional[int] = None        # toman — a rental's monthly ceiling
    area_min: Optional[int] = None
    area_max: Optional[int] = None
    rooms_min: Optional[int] = None
    year_built_min: Optional[int] = None  # Jalali
    must_have: List[str] = []
    red_lines: List[str] = []
    urgency: Optional[Literal["immediate", "month", "flexible"]] = None
    notes: str = ""
    confidence: Dict[str, float] = {}

    @field_validator("budget_max", "deposit_max", "rent_max", mode="before")
    @classmethod
    def _money(cls, v):
        return parse_money(v)

    @field_validator("area_min", "area_max", "rooms_min", "year_built_min", mode="before")
    @classmethod
    def _int(cls, v):
        return _to_int(v)

    @field_validator("districts", "must_have", "red_lines", mode="before")
    @classmethod
    def _list(cls, v):
        return _to_list(v)

    @field_validator("deal_type", mode="before")
    @classmethod
    def _deal(cls, v):
        if v in (None, *DEALS):
            return v
        s = str(v)
        if any(w in s for w in ("رهن", "اجاره", "ودیعه", "rent")):
            return "rent"
        return "buy" if any(w in s for w in ("خرید", "فروش", "buy")) else None

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):
        # the engine's own vocabulary («ویلایی» → house, «تجاری» → shop); None when unreadable
        return v if v in (None, *KINDS) else _family(str(v))

    @field_validator("urgency", mode="before")
    @classmethod
    def _urgency(cls, v):
        return v if v in (None, *URGENCIES) else None

    @field_validator("city", mode="before")
    @classmethod
    def _city(cls, v):
        return (str(v).strip() or None) if v else None

    @field_validator("notes", mode="before")
    @classmethod
    def _notes(cls, v):
        if not v:
            return ""
        return "، ".join(str(x) for x in v) if isinstance(v, list) else str(v).strip()

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, v):
        out = {}
        for k, x in (v.items() if isinstance(v, dict) else ()):
            try:
                out[str(k)] = min(1.0, max(0.0, float(x)))
            except (TypeError, ValueError):
                continue
        return out


# ── the prompt ───────────────────────────────────────────────────────────────

ROLE = (
    "تو دستیار یک دفتر مشاور املاک در ایران هستی. مشاور آنچه مشتری پشت تلفن گفته را می‌نویسد، "
    "یا مشتری خودش نیازش را در پرتال توضیح می‌دهد. کار تو فقط یک چیز است: همان متن را به معیارهای "
    "ساخت‌یافتهٔ جستجو تبدیل کنی — بی‌کم‌وکاست و بی‌حدس."
)

GLOSSARY = """\
- رهن / ودیعه / پیش: مبلغی که مستأجر یک‌جا می‌دهد و آخر قرارداد پس می‌گیرد → deposit_max
- اجاره / کرایه: مبلغ ماهانه → rent_max
- رهن کامل: فقط ودیعه، بدون اجارهٔ ماهانه → deposit_max پر و rent_max برابر 0
- تبدیل: جابه‌جایی بین رهن و اجاره (هر ۱ میلیون اجارهٔ ماهانه ≈ ۳۰ میلیون رهن)؛ «تبدیل مشکلی نیست» را در notes بنویس
- خرید / فروش / معاوضه: معاملهٔ خرید → deal_type برابر buy و سقف قیمت در budget_max
- متراژ / متری: مساحت به مترمربع → area_min و area_max
- خواب / خوابه: تعداد اتاق خواب → rooms_min
- نوساز / کلیدنخورده: ساخت جدید؛ «نوساز» را در must_have بنویس و سال ساخت را فقط اگر خودش گفت (شمسی، مثل 1400) در year_built_min
- کلنگی / قدیمی‌ساز: خانهٔ فرسوده که برای زمینش خریده می‌شود → kind برابر land
- سند تک‌برگ / شش‌دانگ / قولنامه‌ای / اوقافی: وضعیت سند؛ خواسته در must_have، ناخواسته در red_lines
- آسانسور، پارکینگ، انباری، بالکن، حیاط، فول امکانات، بازسازی‌شده: امکانات → must_have
- طبقه: «طبقهٔ اول نباشه» → red_lines برابر «طبقهٔ اول»؛ «همکف» طبقهٔ صفر است
- واحد / آپارتمان → apartment؛ ویلایی / حیاط‌دار / خانه → house؛ زمین / باغ → land؛ مغازه / تجاری → shop؛ دفتر / اداری → office
- تومن / تومان: واحد پول. در حرف روزمره «۱۰ تومن» اجاره یعنی 10000000 و «۵ میلیارد» یعنی 5000000000
- طرف / سمت / حوالی / نزدیک: نام محله یا خیابان → districts، همان‌طور که گفته شده"""

RULES = """\
1. فقط آنچه در متن آمده. هر چه گفته نشده null است و فهرست‌ها خالی می‌مانند. شهر را حدس نزن.
2. همهٔ مبلغ‌ها به تومان و به صورت عدد صحیح؛ اعداد فارسی را لاتین کن: «۵ میلیارد» → 5000000000، «۱۵۰ میلیون» → 150000000، «۱٫۵ میلیارد» → 1500000000.
3. خرید: سقف قیمت در budget_max. رهن و اجاره: سقف ودیعه در deposit_max و سقف اجارهٔ ماهانه در rent_max؛ budget_max خالی.
4. متراژ تقریبی («۱۰۰ متری») را در هر دو area_min و area_max بگذار؛ «حداقل» فقط area_min و «حداکثر» فقط area_max.
5. districts: نام محله‌ها همان‌طور که نوشته شده، بی‌تغییر املا.
6. must_have: آنچه «حتماً» می‌خواهد. red_lines: آنچه «نمی‌خواهد» یا «نباشه» — هر مورد یک عبارت کوتاه.
7. urgency: «فوری / همین هفته» → immediate، «تا آخر ماه» → month، «عجله‌ای نیست» → flexible، وگرنه null.
8. notes: هر چه در هیچ فیلدی جا نمی‌گیرد، حداکثر ۳۰ کلمه؛ وگرنه رشتهٔ خالی. نام و شمارهٔ کسی را ننویس.
9. confidence: برای هر فیلدی که پر کردی عددی بین 0 و 1 — چقدر متن آن را روشن گفته. برای متن مبهم اعداد پایین.
10. فقط JSON معتبر با دقیقاً همین کلیدها برگردان؛ بدون توضیح، بدون متن اضافه."""

SHAPE = ('{"deal_type": "buy|rent|null", "kind": "apartment|house|land|shop|office|null", "city": "string|null", '
         '"districts": ["..."], "budget_max": "int|null", "deposit_max": "int|null", "rent_max": "int|null", '
         '"area_min": "int|null", "area_max": "int|null", "rooms_min": "int|null", "year_built_min": "int|null", '
         '"must_have": ["..."], "red_lines": ["..."], "urgency": "immediate|month|flexible|null", '
         '"notes": "string", "confidence": {"field": 0.0}}')

SYSTEM = f"{ROLE}\n\nواژه‌نامه:\n{GLOSSARY}\n\nقواعد:\n{RULES}\n\nقالب خروجی:\n{SHAPE}"

# Three answers the model copies the shape of: a purchase with a red line, a
# rental with both ceilings and a must-have, and a vague one that stays vague.
FEW_SHOTS = [
    ("یه واحد ۱۰۰ متری نوساز طرف گلها تا ۵ میلیارد، طبقهٔ اول نباشه، پارکینگ حتماً",
     {"deal_type": "buy", "kind": "apartment", "city": None, "districts": ["گلها"],
      "budget_max": 5000000000, "deposit_max": None, "rent_max": None,
      "area_min": 100, "area_max": 100, "rooms_min": None, "year_built_min": None,
      "must_have": ["نوساز", "پارکینگ"], "red_lines": ["طبقهٔ اول"], "urgency": None, "notes": "",
      "confidence": {"deal_type": 0.85, "kind": 0.8, "districts": 0.9, "budget_max": 0.95,
                     "area_min": 0.8, "area_max": 0.8, "must_have": 0.95, "red_lines": 0.95}}),
    ("دنبال آپارتمان دوخوابه برای رهن و اجاره تو ارومیه‌ام، رهن تا ۳۰۰ میلیون و اجاره ماهی ۱۰ تومن، "
     "آسانسور داشته باشه، تا آخر ماه باید جابه‌جا بشم",
     {"deal_type": "rent", "kind": "apartment", "city": "ارومیه", "districts": [],
      "budget_max": None, "deposit_max": 300000000, "rent_max": 10000000,
      "area_min": None, "area_max": None, "rooms_min": 2, "year_built_min": None,
      "must_have": ["آسانسور"], "red_lines": [], "urgency": "month", "notes": "",
      "confidence": {"deal_type": 0.95, "kind": 0.95, "city": 0.95, "deposit_max": 0.95,
                     "rent_max": 0.7, "rooms_min": 0.95, "must_have": 0.95, "urgency": 0.85}}),
    ("یه چیز خوب طرف سعدی",
     {"deal_type": None, "kind": None, "city": None, "districts": ["سعدی"],
      "budget_max": None, "deposit_max": None, "rent_max": None,
      "area_min": None, "area_max": None, "rooms_min": None, "year_built_min": None,
      "must_have": [], "red_lines": [], "urgency": None,
      "notes": "فقط گفته «یه چیز خوب»؛ نوع، بودجه و معامله معلوم نیست",
      "confidence": {"districts": 0.7}}),
]


def build_messages(text: str, *, hint: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """The system prompt, the three examples, and the customer's words — masked:
    their number is not the model's business. `hint` is what the form already
    knows (city, deal type); it helps the model read «طرف گلها» as a district of
    that city and «تا ۳۰۰» as a deposit, and is never a substitute for the text."""
    system = SYSTEM
    known = {k: llm.mask_pii(str(v)) for k, v in (hint or {}).items() if v not in (None, "")}
    if known:
        system += ("\n\nدانسته‌های فرم — اگر متن خلافش نگفت، همین‌ها را در فیلد مربوط بنویس: "
                   + "، ".join(f"{k}={v}" for k, v in known.items()))
    messages = [{"role": "system", "content": system}]
    for asked, answered in FEW_SHOTS:
        messages.append({"role": "user", "content": asked})
        messages.append({"role": "assistant", "content": json.dumps(answered, ensure_ascii=False)})
    messages.append({"role": "user", "content": llm.mask_pii(text)})
    return messages


# ── criteria → the intake form ───────────────────────────────────────────────

def to_customer_fields(c: Dict[str, Any]) -> Dict[str, Any]:
    """The Customer columns these criteria are — the mapping portal_bridge.criteria_of
    does for a form, so the engine reads a phone note and a portal form alike.
    None means «the text did not say»; callers fill only what they lack."""
    deposit, rent, price = c.get("deposit_max"), c.get("rent_max"), c.get("budget_max")
    deal = c.get("deal_type") or ("rent" if (deposit or rent) else "buy" if price else None)
    if deal == "rent":
        # rentals are compared on the deposit (match_service._price_of); a rent-only
        # ceiling is converted the way the price watcher converts it, and a ceiling
        # the model filed under budget_max is still the deposit column
        budget = deposit or ((rent or 0) * RENT_TO_DEPOSIT or None) or price
    else:
        budget = price
    specs = []
    lo, hi = c.get("area_min"), c.get("area_max")
    if lo or hi:
        # the scorer wants one target area; the middle of a range is the honest one
        specs.append(f"{(lo + hi) // 2 if (lo and hi) else (lo or hi)} متر")
    if c.get("rooms_min"):
        specs.append(f"{c['rooms_min']} خواب")
    notes = []
    if c.get("must_have"):
        notes.append("نیاز دارد: " + "، ".join(c["must_have"]))
    if c.get("year_built_min"):
        notes.append(f"ساخت از {c['year_built_min']}")
    if c.get("notes"):
        notes.append(c["notes"])
    return {
        "desired_city": (c.get("city") or "").strip() or None,
        "desired_district": "، ".join(c.get("districts") or [])[:300] or None,
        "desired_type": c.get("kind"),
        "deal_type": deal,
        "budget_max": budget,
        "desired_specs": " / ".join(specs) or None,
        "red_lines": "، ".join(c.get("red_lines") or []) or None,
        "notes": "\n".join(notes) or None,
        "temperature": URGENCY_TO_TEMPERATURE.get(c.get("urgency")),
    }


async def parse_need(db, text: str, *, hint: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The criteria in `text`, and the Customer columns they map to. Raises
    llm.LLMError (NotConfigured, Disabled, BudgetExceeded, or an unusable
    answer) — the route turns it into a 502, the portal bridge into nothing."""
    text = (text or "").strip()
    if not text:
        raise ValueError("متنی برای خواندن نیست")
    out = await llm.chat("read", build_messages(text, hint=hint), agent=AGENT, db=db,
                         schema=NeedCriteria, temperature=0, max_tokens=350, timeout=TIMEOUT)
    criteria = out["data"]
    return {"criteria": criteria, "customer": to_customer_fields(criteria),
            "model": out["model"], "prompt_version": PROMPT_VERSION}


async def enrich_request(db, req) -> Optional[Dict[str, Any]]:
    """For the portal: what the description says that the form's fields do not —
    only the Customer keys criteria_of left empty (red lines, districts, a
    budget the visitor typed in words…). Never a write: portal_bridge folds the
    dict into the customer it is building, in the background — never on the
    visitor's own request, so this raises llm.LLMError (or a subclass) on
    failure instead of swallowing it. portal_bridge.enrich_needs is the one
    caller and decides what a gateway-state error (not configured, disabled,
    over budget) costs versus a real failure; it is not this function's call."""
    text = (getattr(req, "description", None) or "").strip()
    if not text:
        return None
    from app.crm.portal_bridge import criteria_of   # here, not at the top: portal_bridge imports this module
    hint = {k: v for k, v in (("city", req.city), ("deal_type", req.deal_type)) if v}
    parsed = await parse_need(db, text, hint=hint)
    known = criteria_of(req)
    extra = {k: v for k, v in parsed["customer"].items() if v not in (None, "") and not known.get(k)}
    if extra:
        logger.info(f"[ai:need] portal request #{req.id}: {', '.join(extra)} read from the description")
    return extra or None

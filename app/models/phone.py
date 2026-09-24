"""
SorinFlow — one canonical form for an Iranian phone number.

Every phone-bearing column (leads.phone_number, crm_customers.mobile1/2,
crm_contacts.phone, properties.phone_number) keeps a normalized twin next to
it, kept in sync by an ORM event on the model (see sync_phone_columns below),
so a lookup or a dedupe check never has to care whether the number in front
of it is «09141234567», «+989141234567» or «۰۹۱۴ ۱۲۳ ۴۵۶۷» — they all
normalize to the same string.

Its own module with no project imports, on purpose: app/models/lead.py,
crm_models.py and property.py all need normalize_phone() from an ORM event
declared at class-body time, and app/api/routes/crm.py needs it for the
lookup it guards. app/api/routes/sms.py already has normalize_mobile() for
the SMS panel, but that only recognises mobiles and folds them to the
«0912…» shape SMS providers want — it returns None for a landline, and
crm_contacts.phone routinely holds one. Importing it here would also be
backwards (a model importing a route module) and circular the moment that
route module imports a model, which app/api/routes/sms.py already does.
"""
import re
from typing import Optional

_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# An optional country code in front of a 9-digit mobile subscriber number:
# +98 / 0098 / 98 / 0, or nothing (already bare). Mirrors the shape
# app/api/routes/sms.py's normalize_mobile matches for the SMS panel, plus
# the 0098 form that one does not handle.
_MOBILE = re.compile(r"^(?:0098|98|0)?(9\d{9})$")


def normalize_phone(raw) -> Optional[str]:
    """Canonical form for lookups and dedupe, or None if raw has no digits.

      * a mobile canonicalises to its bare 10 digits, 9xxxxxxxxx — whatever
        +98 / 0098 / 98 / 0 prefix it arrived with;
      * anything else (a landline) canonicalises to its digits with a single
        leading 0, area code included, since that is how it is dialled.

    Best-effort, not validation: a string with no digits at all is the only
    input rejected (None). A short or malformed landline still normalizes —
    it just will not match anything real, the same way it would not today.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw).translate(_DIGIT_MAP))
    if not digits:
        return None
    m = _MOBILE.match(digits)
    if m:
        return m.group(1)
    if digits.startswith("0098"):
        digits = digits[4:]
    elif digits.startswith("98") and len(digits) > 10:
        digits = digits[2:]
    return digits if digits.startswith("0") else f"0{digits}"


def sync_phone_columns(*pairs):
    """SQLAlchemy before_insert/before_update listener: copy each raw phone
    column into its normalized twin, so a route never has to remember to.

    Usage, once per model, right after its column declarations:

        event.listen(Lead, "before_insert",
                     sync_phone_columns(("phone_number", "phone_number_normalized")))
        event.listen(Lead, "before_update",
                     sync_phone_columns(("phone_number", "phone_number_normalized")))
    """
    def _listener(mapper, connection, target):
        for raw_attr, norm_attr in pairs:
            setattr(target, norm_attr, normalize_phone(getattr(target, raw_attr, None)))
    return _listener

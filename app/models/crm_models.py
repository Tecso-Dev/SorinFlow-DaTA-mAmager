"""
SorinFlow CRM — database models
Contact, Deal, Note, Task, Reminder, SmsLog, Customer
"""
from sqlalchemy import Column, Integer, String, BigInteger, Boolean, Text, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Contact(Base):
    """Phone-book contact (buyer / seller / consultant)"""
    __tablename__ = "crm_contacts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    phone = Column(String(20), index=True)
    phone2 = Column(String(20))
    email = Column(String(200))
    contact_type = Column(String(50), default="owner", index=True)   # owner|landlord|tenant|seeker|builder|agency
    category = Column(String(50), default="normal", index=True)      # VIP|normal|cold
    city = Column(String(100))
    address = Column(Text)
    notes = Column(Text)
    tags = Column(String(500))   # comma-separated
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    sms_logs = relationship("SmsLog", back_populates="contact", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "phone": self.phone,
            "phone2": self.phone2,
            "email": self.email,
            "contact_type": self.contact_type,
            "category": self.category,
            "city": self.city,
            "address": self.address,
            "notes": self.notes,
            "tags": self.tags,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Deal(Base):
    """Real-estate transaction"""
    __tablename__ = "crm_deals"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    deal_type = Column(String(50), default="buy")                    # buy|rent|lease
    status = Column(String(50), default="new", index=True)           # new|negotiating|contract|closed|cancelled
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="SET NULL"), nullable=True, index=True)
    buyer_contact_id = Column(Integer, ForeignKey("crm_contacts.id", ondelete="SET NULL"), nullable=True)
    seller_contact_id = Column(Integer, ForeignKey("crm_contacts.id", ondelete="SET NULL"), nullable=True)
    amount = Column(BigInteger)
    commission = Column(BigInteger)
    commission_paid = Column(Boolean, default=False)
    notes = Column(Text)
    contract_date = Column(DateTime(timezone=True))
    close_date = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    buyer = relationship("Contact", foreign_keys=[buyer_contact_id])
    seller = relationship("Contact", foreign_keys=[seller_contact_id])

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "deal_type": self.deal_type,
            "status": self.status,
            "property_id": self.property_id,
            "buyer_contact_id": self.buyer_contact_id,
            "seller_contact_id": self.seller_contact_id,
            "buyer_name": self.buyer.name if self.buyer else None,
            "seller_name": self.seller.name if self.seller else None,
            "amount": self.amount,
            "commission": self.commission,
            "commission_paid": self.commission_paid,
            "notes": self.notes,
            "contract_date": self.contract_date.isoformat() if self.contract_date else None,
            "close_date": self.close_date.isoformat() if self.close_date else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Note(Base):
    """Sticky note — can be linked to contact, property, or deal"""
    __tablename__ = "crm_notes"

    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)
    contact_id = Column(Integer, ForeignKey("crm_contacts.id", ondelete="SET NULL"), nullable=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="SET NULL"), nullable=True, index=True)
    deal_id = Column(Integer, ForeignKey("crm_deals.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "content": self.content,
            "contact_id": self.contact_id,
            "property_id": self.property_id,
            "deal_id": self.deal_id,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Task(Base):
    """To-Do task"""
    __tablename__ = "crm_tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    due_date = Column(DateTime(timezone=True))
    priority = Column(String(20), default="medium", index=True)   # low|medium|high|urgent
    status = Column(String(20), default="todo", index=True)        # todo|in_progress|done
    contact_id = Column(Integer, ForeignKey("crm_contacts.id", ondelete="SET NULL"), nullable=True, index=True)
    deal_id = Column(Integer, ForeignKey("crm_deals.id", ondelete="SET NULL"), nullable=True)
    assigned_to = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "priority": self.priority,
            "status": self.status,
            "contact_id": self.contact_id,
            "deal_id": self.deal_id,
            "assigned_to": self.assigned_to,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Reminder(Base):
    """Follow-up reminder with optional SMS delivery"""
    __tablename__ = "crm_reminders"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    remind_at = Column(DateTime(timezone=True), nullable=False, index=True)
    repeat = Column(String(20), default="none")    # none|daily|weekly|monthly
    channel = Column(String(20), default="in_app") # in_app|sms
    sms_to = Column(String(20))
    contact_id = Column(Integer, ForeignKey("crm_contacts.id", ondelete="SET NULL"), nullable=True, index=True)
    deal_id = Column(Integer, ForeignKey("crm_deals.id", ondelete="SET NULL"), nullable=True)
    task_id = Column(Integer, ForeignKey("crm_tasks.id", ondelete="SET NULL"), nullable=True)
    is_sent = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "remind_at": self.remind_at.isoformat() if self.remind_at else None,
            "repeat": self.repeat,
            "channel": self.channel,
            "sms_to": self.sms_to,
            "contact_id": self.contact_id,
            "deal_id": self.deal_id,
            "task_id": self.task_id,
            "is_sent": self.is_sent,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SmsLog(Base):
    """SMS send history"""
    __tablename__ = "crm_sms_logs"

    id = Column(Integer, primary_key=True, index=True)
    to_number = Column(String(20), nullable=False, index=True)
    message = Column(Text, nullable=False)
    status = Column(String(20), default="pending", index=True)  # pending|sent|failed
    provider = Column(String(50), default="kavenegar")           # kavenegar|melipayamak
    response = Column(Text)
    contact_id = Column(Integer, ForeignKey("crm_contacts.id", ondelete="SET NULL"), nullable=True, index=True)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())

    # ── added for the «پیامک» panel ──
    # Kavenegar's id for the message. Without it there is no way to ask whether
    # a message actually arrived, which is the question asked days later.
    message_id = Column(String(40), index=True)
    cost = Column(Integer)
    # Kavenegar's delivery verdict, refreshed on demand rather than polled: a
    # broadcast of 4,000 would otherwise mean 4,000 status calls nobody read.
    delivery_status = Column(Integer)
    delivery_text = Column(String(60))
    delivery_checked_at = Column(DateTime(timezone=True))
    # Who sent it, and which broadcast it belonged to. A campaign tag turns
    # thousands of rows into one line in the panel.
    sent_by = Column(String(200))
    campaign = Column(String(120), index=True)
    kind = Column(String(20), default="manual")   # manual|broadcast|otp|reminder

    contact = relationship("Contact", back_populates="sms_logs")

    def to_dict(self):
        return {
            "id": self.id,
            "to_number": self.to_number,
            "message": self.message,
            "status": self.status,
            "provider": self.provider,
            "response": self.response,
            "contact_id": self.contact_id,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "message_id": self.message_id,
            "cost": self.cost,
            "delivery_status": self.delivery_status,
            "delivery_text": self.delivery_text,
            "sent_by": self.sent_by,
            "campaign": self.campaign,
            "kind": self.kind,
        }


class Customer(Base):
    """Customer intake form (پروفایل مشتری + BANT + بازدیدها + پیگیری)

    Mirrors the paper form: lead profile, budget/need analysis,
    showing pipeline and follow-up loop.
    """
    __tablename__ = "crm_customers"

    id = Column(Integer, primary_key=True, index=True)

    # ── پروفایل مشتری (Lead Profile) ──
    full_name = Column(String(200), nullable=False, index=True)
    mobile1 = Column(String(20), index=True)
    mobile2 = Column(String(20))                       # همسر / شریک
    source = Column(String(30), default="in_person")   # in_person|divar|referral
    temperature = Column(String(20), default="warm", index=True)  # hot|warm|cold
    consultant_name = Column(String(200))              # نام مشاور

    # ── آنالیز بودجه و نیاز (BANT) ──
    budget_max = Column(BigInteger)                    # سقف بودجه (تومان)
    payment_methods = Column(String(200))              # cash,loan,has_property,barter (comma-separated)
    desired_specs = Column(String(300))                # متراژ / تعداد خواب
    desired_district = Column(String(300))             # منطقه درخواستی
    # Explicit search criteria. Without these the matcher had to guess the
    # customer's intent from the free text above, which cannot distinguish a
    # street called کاشانی in Urmia from the one in Tehran.
    desired_city = Column(String(100))                 # شهر
    desired_type = Column(String(20))                  # apartment|house|land|shop|office
    deal_type = Column(String(10), default="buy")      # buy|rent
    red_lines = Column(Text)                           # خط قرمزها (نمی‌خواهد)

    # ── خط تولید بازدید (Showing Pipeline) ──
    # [{file_code, description, feedback, next_step}]  next_step: meeting|archive|second_visit
    showings = Column(JSON, default=list)

    # ── تسک بعدی (Follow-Up Loop) ──
    # [{date, time, action}]
    followups = Column(JSON, default=list)

    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "full_name": self.full_name,
            "mobile1": self.mobile1,
            "mobile2": self.mobile2,
            "source": self.source,
            "temperature": self.temperature,
            "consultant_name": self.consultant_name,
            "budget_max": self.budget_max,
            "payment_methods": self.payment_methods,
            "desired_specs": self.desired_specs,
            "desired_district": self.desired_district,
            "desired_city": self.desired_city,
            "desired_type": self.desired_type,
            "deal_type": self.deal_type or "buy",
            "red_lines": self.red_lines,
            "showings": self.showings or [],
            "followups": self.followups or [],
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DailyPerformance(Base):
    """فرم ارزیابی عملکرد روزانه (DPA) — Data-Driven Management

    Mirrors the Arad daily scoring sheet: base operations checklist,
    daily stats, bonus points, penalties and mentor feedback.
    Score = base (sum of done task weights, max 100) + bonus - penalty.
    """
    __tablename__ = "crm_daily_performance"

    # (task key, weight) — the rest slot (استراحت) has weight 0 and no checkbox
    BASE_TASKS = (
        ("prospecting", 25),   # شکار فایل — تماس سرد و ثبت لید جدید (۰۹:۰۰-۱۰:۳۰)
        ("divar_seo", 15),     # هک الگوریتم دیوار — بازتولید آگهی و سئو محلی (۱۰:۳۰-۱۲:۰۰)
        ("followup", 20),      # حلقه پیگیری — تماس با مشتریان در حال مذاکره (۱۲:۰۰-۱۴:۰۰)
        ("showings", 30),      # بازدید حضوری — پرزنت ملک و مدیریت اعتراضات (۱۶:۰۰-۱۹:۰۰)
        ("crm_report", 10),    # گزارش CRM — ثبت دقیق نتایج (۱۹:۰۰-۲۰:۰۰)
    )
    BONUS_POINTS = {"exclusive": 30, "offer": 20, "close": 50}
    PENALTY_POINTS = {"crm_delay": 10, "cancel": 15, "hot_lead": 20}

    # ── فعالیت‌های امتیازی (امتیاز × تعداد) ──
    # auto=True → the system counts these itself from CRM events; the agent can
    # still add extra units for work done outside the panel.
    ACTIVITIES = (
        # key,        points, label,                     auto
        ("call",          2,  "تماس با مشتری",            True),
        ("showing",      10,  "پرزنت / بازدید ملک",        True),
        ("new_file",     15,  "ثبت فایل جدید",             True),
        ("meeting",      20,  "نشست و تنظیم قرارداد",      True),
        ("exclusive",    30,  "ثبت فایل انحصاری",          False),
        ("offer",        20,  "دریافت آفر کتبی و بیعانه",  False),
        ("close",        50,  "بستن قرارداد نهایی",        False),
    )
    ACTIVITY_POINTS = {k: p for k, p, _l, _a in ACTIVITIES}
    AUTO_ACTIVITIES = {k for k, _p, _l, a in ACTIVITIES if a}

    id = Column(Integer, primary_key=True, index=True)

    # ── سربرگ ──
    agent_name = Column(String(200), nullable=False, index=True)   # نام مشاور
    role = Column(String(20), default="hunter")                    # hunter|closer
    date_jalali = Column(String(20), index=True)                   # ۱۴۰۵/۰۴/۱۸
    target_points = Column(Integer, default=100)

    # ── آمار روز ──
    new_files = Column(Integer, default=0)        # فایل‌های جدید ثبت‌شده
    showings_count = Column(Integer, default=0)   # تعداد بازدید
    offers_count = Column(Integer, default=0)     # آفرهای دریافتی
    closed_count = Column(Integer, default=0)     # قرارداد نهایی

    # ── گام‌های عملیاتی پایه: {"prospecting": true, ...} ──
    base_tasks = Column(JSON, default=dict)

    # ── فعالیت‌های امتیازی ──
    # auto_activities: counted by the system from CRM events (read-only for the agent)
    # activities: extra units the agent enters manually for off-panel work
    auto_activities = Column(JSON, default=dict)   # {"call": 7, "showing": 5}
    activities = Column(JSON, default=dict)        # {"showing": 2}

    # ── امتیاز انفجار (تعداد هر مورد) ──
    bonus_exclusive = Column(Integer, default=0)  # ثبت فایل انحصاری +۳۰
    bonus_offer = Column(Integer, default=0)      # دریافت آفر کتبی و بیعانه +۲۰
    bonus_close = Column(Integer, default=0)      # بستن قرارداد نهایی +۵۰

    # ── اخطار سیستم (تعداد هر مورد) ──
    pen_crm_delay = Column(Integer, default=0)    # تاخیر در ثبت CRM -۱۰
    pen_cancel = Column(Integer, default=0)       # کنسل شدن بازدید مقصر مشاور -۱۵
    pen_hot_lead = Column(Integer, default=0)     # عدم پیگیری لید داغ -۲۰

    # ── بازخورد ──
    mentor_feedback = Column(Text)                # بازخورد روزانه منتور / توصیه فردا
    rca = Column(Text)                            # تحلیل موانع: چرا بازدید به قرارداد نرسید؟

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def activity_counts(self) -> dict:
        """Merged per-activity counts: system-counted + manually added."""
        auto = self.auto_activities or {}
        manual = self.activities or {}
        return {
            key: int(auto.get(key, 0) or 0) + int(manual.get(key, 0) or 0)
            for key, _p, _l, _a in self.ACTIVITIES
        }

    def scores(self):
        tasks = self.base_tasks or {}
        base = sum(w for key, w in self.BASE_TASKS if tasks.get(key))
        counts = self.activity_counts()
        activity = sum(counts[k] * self.ACTIVITY_POINTS[k] for k in counts)
        bonus = (
            (self.bonus_exclusive or 0) * self.BONUS_POINTS["exclusive"]
            + (self.bonus_offer or 0) * self.BONUS_POINTS["offer"]
            + (self.bonus_close or 0) * self.BONUS_POINTS["close"]
        )
        penalty = (
            (self.pen_crm_delay or 0) * self.PENALTY_POINTS["crm_delay"]
            + (self.pen_cancel or 0) * self.PENALTY_POINTS["cancel"]
            + (self.pen_hot_lead or 0) * self.PENALTY_POINTS["hot_lead"]
        )
        return {"base_score": base, "activity_score": activity, "bonus_score": bonus,
                "penalty_score": penalty,
                "total_score": base + activity + bonus - penalty}

    def to_dict(self):
        return {
            "id": self.id,
            "agent_name": self.agent_name,
            "role": self.role,
            "date_jalali": self.date_jalali,
            "target_points": self.target_points,
            "new_files": self.new_files,
            "showings_count": self.showings_count,
            "offers_count": self.offers_count,
            "closed_count": self.closed_count,
            "base_tasks": self.base_tasks or {},
            "auto_activities": self.auto_activities or {},
            "activities": self.activities or {},
            "activity_counts": self.activity_counts(),
            "activity_defs": [
                {"key": k, "points": p, "label": l, "auto": a}
                for k, p, l, a in self.ACTIVITIES
            ],
            "bonus_exclusive": self.bonus_exclusive,
            "bonus_offer": self.bonus_offer,
            "bonus_close": self.bonus_close,
            "pen_crm_delay": self.pen_crm_delay,
            "pen_cancel": self.pen_cancel,
            "pen_hot_lead": self.pen_hot_lead,
            "mentor_feedback": self.mentor_feedback,
            "rca": self.rca,
            **self.scores(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class CalendarEvent(Base):
    """A scheduled appointment — بازدید ملک، نشست قرارداد، تماس، …

    Distinct from Task (a to-do with a deadline) and Reminder (a ping at a
    moment): an event occupies a slot of time and usually a place, which is
    what the calendar grid draws. Tasks and reminders are overlaid on the
    same grid read-only rather than copied into this table.
    """
    __tablename__ = "crm_calendar_events"

    # type → (Persian label, colour used by both the grid and the legend)
    EVENT_TYPES = {
        "visit":    ("بازدید ملک", "#34d399"),
        "meeting":  ("نشست و قرارداد", "#a78bfa"),
        "call":     ("تماس تلفنی", "#38bdf8"),
        "showing":  ("نمایش به مشتری", "#fbbf24"),
        "personal": ("شخصی", "#94a3b8"),
        "other":    ("سایر", "#f472b6"),
    }

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    event_type = Column(String(20), default="visit", index=True)
    description = Column(Text)

    start_at = Column(DateTime(timezone=True), nullable=False, index=True)
    end_at = Column(DateTime(timezone=True))
    all_day = Column(Boolean, default=False)

    # where the visit happens — free text, prefilled from the property address
    location = Column(String(500))

    # what the appointment is about (all optional, all SET NULL on delete)
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="SET NULL"), nullable=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    customer_id = Column(Integer, ForeignKey("crm_customers.id", ondelete="SET NULL"), nullable=True, index=True)
    contact_id = Column(Integer, ForeignKey("crm_contacts.id", ondelete="SET NULL"), nullable=True)
    deal_id = Column(Integer, ForeignKey("crm_deals.id", ondelete="SET NULL"), nullable=True)

    # An appointment has up to three sides, and the reminder goes to each
    # number that is filled in.
    owner_name = Column(String(200))       # مالک
    owner_phone = Column(String(20))
    customer_name = Column(String(200))    # مشتری
    customer_phone = Column(String(20))
    assigned_to = Column(String(200))      # کارشناس فروش
    agent_phone = Column(String(20))

    # superseded by owner_* — kept so rows written before the split survive
    attendee_name = Column(String(200))
    attendee_phone = Column(String(20))
    status = Column(String(20), default="scheduled", index=True)  # scheduled|done|canceled
    outcome = Column(Text)                 # what came of it, filled after the fact

    # in-app reminder: minutes before start (0/None = no reminder)
    remind_before = Column(Integer, default=60)
    # SMS reminder to attendee_phone, fired remind_before minutes ahead
    sms_reminder = Column(Boolean, default=False)
    sms_sent = Column(Boolean, default=False, index=True)

    created_by = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def type_label(self) -> str:
        return self.EVENT_TYPES.get(self.event_type, self.EVENT_TYPES["other"])[0]

    @property
    def color(self) -> str:
        return self.EVENT_TYPES.get(self.event_type, self.EVENT_TYPES["other"])[1]

    def sms_targets(self):
        """(role, name, phone) for every side of this appointment that has a
        number. Duplicates are dropped so one person is never texted twice."""
        candidates = [
            ("مالک", self.owner_name or self.attendee_name, self.owner_phone or self.attendee_phone),
            ("مشتری", self.customer_name, self.customer_phone),
            ("کارشناس فروش", self.assigned_to, self.agent_phone),
        ]
        seen, out = set(), []
        for role, name, phone in candidates:
            digits = "".join(ch for ch in (phone or "") if ch.isdigit())
            if digits and digits not in seen:
                seen.add(digits)
                out.append((role, name, phone.strip()))
        return out

    def sms_text(self, role=None) -> str:
        """The reminder a participant receives before this appointment.

        The agent is told who they are meeting; the owner and the customer
        are told which agent is coming.
        """
        # imported here because dpa_service imports this module
        from app.services.dpa_service import to_jalali
        when = to_jalali(self.start_at)
        if not self.all_day:
            when += f" ساعت {self.start_at.strftime('%H:%M')}"
        lines = [f"{self.type_label}: {self.title}", f"زمان: {when}"]
        if self.location:
            lines.append(f"محل: {self.location}")
        if role == "کارشناس فروش":
            others = "، ".join(n for n in (self.owner_name or self.attendee_name,
                                           self.customer_name) if n)
            if others:
                lines.append(f"طرف قرار: {others}")
        elif self.assigned_to:
            lines.append(f"همراه: {self.assigned_to}")
        lines.append("املاک سورین")
        return "\n".join(lines)

    def to_dict(self):
        return {
            "id": self.id,
            "kind": "event",          # tells the grid this row is editable
            "title": self.title,
            "event_type": self.event_type,
            "type_label": self.type_label,
            "color": self.color,
            "description": self.description,
            "start_at": self.start_at.isoformat() if self.start_at else None,
            "end_at": self.end_at.isoformat() if self.end_at else None,
            "all_day": bool(self.all_day),
            "location": self.location,
            "property_id": self.property_id,
            # the code the agent sees everywhere else; filled by
            # _attach_event_serials(), since it lives on the properties table
            "property_serial": getattr(self, "_property_serial", None),
            "lead_id": self.lead_id,
            "customer_id": self.customer_id,
            "contact_id": self.contact_id,
            "deal_id": self.deal_id,
            "owner_name": self.owner_name,
            "owner_phone": self.owner_phone,
            "customer_name": self.customer_name,
            "customer_phone": self.customer_phone,
            "assigned_to": self.assigned_to,
            "agent_phone": self.agent_phone,
            "sms_recipients": [
                {"role": role, "name": name, "phone": phone}
                for role, name, phone in self.sms_targets()
            ],
            "status": self.status,
            "outcome": self.outcome,
            "remind_before": self.remind_before,
            "sms_reminder": bool(self.sms_reminder),
            "sms_sent": bool(self.sms_sent),
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ActivityLog(Base):
    """Timeline entry — who did what to a lead / customer / deal, and when."""
    __tablename__ = "crm_activity_log"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String(20), nullable=False, index=True)  # lead|customer|deal
    entity_id = Column(Integer, nullable=False, index=True)
    action = Column(String(50), nullable=False)   # status_change|note|created|converted|notified
    detail = Column(Text)                          # human-readable Persian summary
    actor = Column(String(200))                    # who did it
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "action": self.action,
            "detail": self.detail,
            "actor": self.actor,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Cabinet(Base):
    """کمد — the top shelf a consultant's binders sit on.

    Mirrors how an agency actually files paper: a cabinet per broad concern
    (فروش، اجاره، متقاضی…), binders inside it, files inside those.
    """
    __tablename__ = "crm_cabinets"

    PALETTE = ["#fbbf24", "#34d399", "#38bdf8", "#a78bfa", "#f472b6",
               "#fb923c", "#2dd4bf", "#f87171", "#a3e635", "#e879f9"]

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    color = Column(String(20), default="#fbbf24")
    icon = Column(String(40), default="bi-archive")
    sort_order = Column(Integer, default=0, index=True)
    owner = Column(String(200), index=True)   # None = shared across the agency
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    binders = relationship("Binder", back_populates="cabinet",
                           cascade="all, delete-orphan", order_by="Binder.sort_order")

    def to_dict(self, with_binders=False):
        data = {
            "id": self.id, "name": self.name, "color": self.color,
            "icon": self.icon, "sort_order": self.sort_order, "owner": self.owner,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if with_binders:
            data["binders"] = [b.to_dict() for b in (self.binders or [])]
        return data


class Binder(Base):
    """زونکن — one binder of files inside a cabinet.

    kind separates the two things an agency files: املاکی که داریم
    (property) and مشتری‌هایی که دنبال ملک‌اند (demand).
    """
    __tablename__ = "crm_binders"

    KINDS = {"property": "فایل ملکی", "demand": "فایل متقاضی"}
    DEAL_TYPES = {"buy": "خرید و فروش", "rent": "رهن و اجاره",
                  "partnership": "مشارکت", "presale": "پیش‌فروش", "": "همه"}

    id = Column(Integer, primary_key=True, index=True)
    cabinet_id = Column(Integer, ForeignKey("crm_cabinets.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    name = Column(String(120), nullable=False)
    color = Column(String(20), default="#38bdf8")
    kind = Column(String(20), default="property", index=True)
    deal_type = Column(String(20), default="")
    description = Column(String(300))
    sort_order = Column(Integer, default=0, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    cabinet = relationship("Cabinet", back_populates="binders")

    def to_dict(self, file_count=None):
        return {
            "id": self.id, "cabinet_id": self.cabinet_id, "name": self.name,
            "color": self.color, "kind": self.kind,
            "kind_label": self.KINDS.get(self.kind, self.kind),
            "deal_type": self.deal_type,
            "deal_label": self.DEAL_TYPES.get(self.deal_type or "", ""),
            "description": self.description, "sort_order": self.sort_order,
            "file_count": file_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

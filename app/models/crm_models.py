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

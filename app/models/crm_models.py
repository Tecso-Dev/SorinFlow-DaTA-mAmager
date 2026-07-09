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
    contact_type = Column(String(50), default="buyer", index=True)   # buyer|seller|consultant|other
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

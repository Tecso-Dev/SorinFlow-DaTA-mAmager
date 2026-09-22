"""
SorinFlow Divar Scraper - Models Package

Every model, so `from app.models import X` works for all of them and so
importing the package registers every table on Base.metadata — the list
init_db and restore_backup.py rely on. It exported 12 of 20 for a long time
(roadmap #16); a model missing here is a table a fresh boot can forget.
"""
from app.models.property import Property, City, Category
from app.models.cookie import Cookie
from app.models.scraping_job import ScrapingJob, ScrapingLog
from app.models.proxy import Proxy
from app.models.lead import Lead
from app.models.crm_models import (
    Contact, Deal, Note, Task, Reminder, SmsLog, Customer, DailyPerformance,
    CalendarEvent, ActivityLog, Cabinet, Binder,
)
from app.models.user import User
from app.models.portal import PropertyRequest, UpgradeTicket
from app.models.email_log import EmailLog
from app.models.sms_log import SmsEvent
from app.models.app_setting import AppSetting
from app.models.forwarder import ForwarderDevice
from app.models.scrape_schedule import ScrapeSchedule
from app.models.ai_usage import AiUsage

__all__ = [
    "Property", "City", "Category",
    "Cookie", "ScrapingJob", "ScrapingLog", "Proxy", "Lead",
    "Contact", "Deal", "Note", "Task", "Reminder", "SmsLog", "Customer",
    "DailyPerformance", "CalendarEvent", "ActivityLog", "Cabinet", "Binder",
    "User", "PropertyRequest", "UpgradeTicket", "EmailLog", "SmsEvent",
    "AppSetting", "ForwarderDevice", "ScrapeSchedule", "AiUsage",
]

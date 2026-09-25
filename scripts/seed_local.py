"""
Seed the local Docker stack (docker-compose.local.yml) with fake data: a
manager and two agent accounts, a few Divar sessions, ~20 scraped listings
with CRM leads, a few customers, and two finished scrape jobs — enough for
every dashboard screen to show something.

The two accounts app/database.py:init_db seeds on its own (super_admin, root)
are left to it; this only adds what boot-time seeding does not.

Idempotent — every insert is guarded by a lookup on a unique field, so
re-running after a restart adds nothing twice.

Usage (inside the backend container):
    docker compose -f docker-compose.local.yml exec backend python scripts/seed_local.py

Everything here is fake: Persian placeholder names, 0912xxxxxxx-shaped
numbers nothing in the local stack ever contacts, dummy cookie JSON. No
real customer data. It refuses any database whose name does not end in
_local: it writes admin accounts with a committed password.
"""
import asyncio
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import CITIES, CATEGORIES  # noqa: E402
from app.database import async_session_maker, engine  # noqa: E402
from app.auth.jwt import get_password_hash  # noqa: E402
from app.auth.permissions import ALL_PERMISSIONS, DEFAULT_ADMIN_PERMISSIONS, ROLE_ROOT, ROLE_SUPER_ADMIN  # noqa: E402
from app.crm.call_queue import OUTCOMES  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.cookie import Cookie  # noqa: E402
from app.models.property import Property, City, Category  # noqa: E402
from app.models.lead import Lead  # noqa: E402
from app.models.crm_models import ActivityLog, CalendarEvent, Customer, CustomerMatch, Deal  # noqa: E402
from app.models.scraping_job import ScrapingJob, ScrapingLog  # noqa: E402

PASSWORD = "local-pass-1234"

# (username, full_name, role, permissions, divar_phone)
STAFF = [
    ("manager1", "زهرا کریمی", "admin", ALL_PERMISSIONS, "09120000001"),
    ("agent1", "علی رضایی", "admin", DEFAULT_ADMIN_PERMISSIONS, "09120000002"),
    ("agent2", "سارا محمدی", "admin", DEFAULT_ADMIN_PERMISSIONS, None),
]

FAKE_SELLERS = ["محمد احمدی", "فاطمه حسینی", "رضا نوری", "مریم صادقی", "حسین قاسمی",
                "زهرا رحیمی", "امیر جعفری", "لیلا کاظمی", "بهرام یوسفی", "نگار عزیزی"]
FAKE_CUSTOMERS = ["پیمان اکبری", "الهام مرادی", "کاوه شریفی", "شیرین فرهادی", "آرش تقوی"]

# ── 60 days of dashboard history (seed_history) ──────────────────────────────
# Fixed +03:30 — same reason as everywhere else in this codebase: the
# container has no tz database (see app/services/dashboard_overview.TEHRAN).
TEHRAN = timezone(timedelta(hours=3, minutes=30), "Asia/Tehran")

HISTORY_DAYS = 60
TEHRAN_DISTRICTS = ["سعادت‌آباد", "پونک", "ونک", "شهرک غرب", "تهرانپارس",
                    "میرداماد", "نارمک", "جردن"]
STAFF_NAMES = [full_name for _u, full_name, _r, _p, _ph in STAFF]

# app/api/routes/crm.py VALID_LEAD_STATUSES, weighted toward the top of the
# funnel the way a real pipeline looks.
LEAD_STATUSES = ["new", "contacted", "visit", "contract_meeting", "closed", "rented", "rejected"]
LEAD_STATUS_WEIGHTS = [30, 22, 15, 8, 10, 7, 13]

# hour -> weight, office hours 8..19 (app/services/dashboard_overview.GRID_HOURS),
# peaked at 10-12 and 17-19 the way call volume actually looks.
CALL_HOUR_WEIGHTS = {8: 1, 9: 2, 10: 5, 11: 5, 12: 3, 13: 1, 14: 1,
                     15: 2, 16: 2, 17: 5, 18: 5, 19: 3}

HISTORY_CUSTOMERS = [
    "پیمان اکبری‌نژاد", "الهام مرادی‌فر", "کاوه شریفی‌راد", "شیرین فرهادی‌پور", "آرش تقوی‌نیا",
    "نیلوفر حیدری", "بابک صمدی", "مینا رستگار", "فرزاد یگانه", "سپیده نجفی",
    "امید کاویانی", "ترانه بهرامی", "سهیل دانایی", "رویا اسدی", "کیوان مقدم",
]


def _tehran_to_utc(day, hour, minute=0):
    """A Tehran wall-clock moment on `day`, as a UTC-aware datetime."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TEHRAN).astimezone(timezone.utc)

CITY_SLUGS = [s for s in ("tehran", "karaj", "mashhad", "isfahan", "shiraz") if s in CITIES]
CATEGORY_SLUGS = [s for s in ("buy-apartment", "rent-apartment", "buy-villa", "rent-villa")
                  if s in CATEGORIES]


async def _existing(db, model, **filters):
    stmt = select(model).filter_by(**filters).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


async def seed_staff(db):
    """manager1/agent1/agent2 — role=admin like every real employee account;
    the difference between "manager" and "agent" is the permission set, not
    the role (only root/super_admin/admin/visitor exist — see
    app/auth/permissions.py)."""
    users = {}
    created = []
    for username, full_name, role, perms, divar_phone in STAFF:
        user = await _existing(db, User, username=username)
        if not user:
            user = User(
                username=username, full_name=full_name, role=role,
                hashed_password=get_password_hash(PASSWORD),
                is_active=True, permissions=list(perms), divar_phone=divar_phone,
            )
            db.add(user)
            await db.flush()
            created.append(username)
        users[username] = user
    if created:
        print(f"users: created {', '.join(created)}")
    else:
        print("users: already seeded")
    return users


async def seed_cookies(db, users):
    """A Divar session per staff member who has a divar_phone, owned by them."""
    created = 0
    for username, user in users.items():
        if not user.divar_phone:
            continue
        if await _existing(db, Cookie, phone_number=user.divar_phone):
            continue
        db.add(Cookie(
            phone_number=user.divar_phone,
            owner_user_id=user.id,
            cookies=[{"name": "sid", "value": f"fake-session-{user.id}", "domain": ".divar.ir", "path": "/"}],
            token="fake-jwt-token",
            is_valid=True,
            reveals=3,
            last_used_at=datetime.now(timezone.utc) - timedelta(hours=2),
            last_checked_at=datetime.now(timezone.utc) - timedelta(hours=1),
        ))
        created += 1
    print(f"cookies: created {created}" if created else "cookies: already seeded")


async def seed_properties_and_leads(db, count=20):
    if await _existing(db, Property, tag_number="LOCAL-0001"):
        print("properties/leads: already seeded")
        return

    city_rows = {c.slug: c for c in (await db.execute(
        select(City).where(City.slug.in_(CITY_SLUGS)))).scalars().all()}
    cat_rows = {c.slug: c for c in (await db.execute(
        select(Category).where(Category.slug.in_(CATEGORY_SLUGS)))).scalars().all()}

    made = []
    for i in range(1, count + 1):
        city_slug = CITY_SLUGS[i % len(CITY_SLUGS)] if CITY_SLUGS else None
        cat_slug = CATEGORY_SLUGS[i % len(CATEGORY_SLUGS)] if CATEGORY_SLUGS else None
        city = city_rows.get(city_slug)
        cat = cat_rows.get(cat_slug)
        listing_type = "rent" if (cat and cat.slug.startswith("rent")) else "buy"
        seller = FAKE_SELLERS[i % len(FAKE_SELLERS)]

        prop = Property(
            tag_number=f"LOCAL-{i:04d}",
            serial_no=1000 + i,
            divar_id=f"local-fake-{i:04d}",
            title=f"{'اجاره' if listing_type == 'rent' else 'فروش'} آپارتمان {50 + i * 5} متری",
            description="آگهی نمونه برای محیط توسعهٔ محلی — داده واقعی نیست.",
            price=None if listing_type == "rent" else (2_000_000_000 + i * 50_000_000),
            deposit=None if listing_type == "buy" else (100_000_000 + i * 5_000_000),
            rent_price=None if listing_type == "buy" else (8_000_000 + i * 200_000),
            area=50 + i * 5,
            rooms=(i % 4) + 1,
            year_built=1390 + (i % 15),
            floor=i % 10,
            total_floors=(i % 10) + 2,
            has_elevator=bool(i % 2),
            has_parking=bool(i % 3),
            has_storage=bool(i % 2 == 0),
            has_balcony=True,
            has_images=False,
            city_id=city.id if city else None,
            city_name=city.name if city else "تهران",
            district=f"منطقه {i % 22 + 1}",
            category_id=cat.id if cat else None,
            category_name=cat.name if cat else "خرید آپارتمان",
            property_type="apartment",
            listing_type=listing_type,
            phone_number=f"0912{1000000 + i:07d}",
            contact_channel="phone",
            seller_name=seller,
            advertiser_type="personal" if i % 4 else "agency",
            url=f"https://divar.ir/v/local-fake-listing-{i}/local-fake-{i:04d}",
            images=[],
            features=[],
            amenities=[],
            is_active=True,
            posted_at=datetime.now(timezone.utc) - timedelta(days=i),
        )
        db.add(prop)
        made.append(prop)
    await db.flush()

    lead_statuses = ["new", "contacted", "qualified", "closed"]
    leads_made = 0
    for i, prop in enumerate(made):
        if i % 4 == 3:          # leave a few properties without a lead
            continue
        db.add(Lead(
            property_id=prop.id,
            phone_number=prop.phone_number,
            seller_name=prop.seller_name,
            city_name=prop.city_name,
            category_name=prop.category_name,
            listing_type=prop.listing_type,
            price=prop.price,
            area=prop.area,
            property_url=prop.url,
            property_title=prop.title,
            status=lead_statuses[i % len(lead_statuses)],
            # the display name, as the panel assigns — the account is
            # resolved from it at the end of main()
            assigned_to=STAFF[i % len(STAFF)][1],
            notes="لید نمونه — محیط توسعهٔ محلی.",
        ))
        leads_made += 1

    print(f"properties: created {len(made)}, leads: created {leads_made}")


async def seed_customers(db):
    if await _existing(db, Customer, full_name=FAKE_CUSTOMERS[0]):
        print("customers: already seeded")
        return
    for i, name in enumerate(FAKE_CUSTOMERS):
        db.add(Customer(
            full_name=name,
            mobile1=f"0912{2000000 + i:07d}",
            source=["in_person", "divar", "referral"][i % 3],
            temperature=["hot", "warm", "cold"][i % 3],
            consultant_name=STAFF[i % len(STAFF)][1],
            budget_max=3_000_000_000 + i * 200_000_000,
            desired_specs=f"{60 + i * 10} متر، {2 + i % 2} خواب",
            desired_district="منطقه ۵",
            desired_city="تهران",
            desired_type="apartment",
            deal_type="buy" if i % 2 == 0 else "rent",
            notes="مشتری نمونه — محیط توسعهٔ محلی.",
        ))
    print(f"customers: created {len(FAKE_CUSTOMERS)}")


async def seed_history(db):
    """~60 days of realistic activity so every panel of GET /api/stats/overview
    (app/api/routes/stats.py, app/services/dashboard_overview.py) shows
    something: listings and leads spread over the window, calls in Tehran
    office hours, visits (some upcoming), deals over six months, and a
    couple more customers. Deterministic (random.Random(1405)) and guarded
    by a single lookup, so a second run adds nothing.
    """
    if await _existing(db, Property, tag_number="LOCAL-H0001"):
        print("history: already seeded")
        return

    rng = random.Random(1405)
    now = datetime.now(timezone.utc)
    today = now.astimezone(TEHRAN).date()

    city_rows = {c.slug: c for c in (await db.execute(
        select(City).where(City.slug.in_(CITY_SLUGS)))).scalars().all()}
    cat_rows = {c.slug: c for c in (await db.execute(
        select(Category).where(Category.slug.in_(CATEGORY_SLUGS)))).scalars().all()}
    tehran_city = city_rows.get("tehran")

    # ── ~300 properties spread over the last 60 days ────────────────────────
    props = []
    count = 300
    for i in range(1, count + 1):
        day_offset = rng.randint(0, HISTORY_DAYS - 1)
        created_at = _tehran_to_utc(today - timedelta(days=day_offset),
                                    rng.randint(8, 20), rng.randint(0, 59))
        scraped_at = created_at + timedelta(minutes=rng.randint(1, 45))

        in_tehran = rng.random() < 0.85 or not tehran_city
        city = tehran_city if in_tehran and tehran_city else (
            city_rows.get(rng.choice(CITY_SLUGS)) if CITY_SLUGS else None)
        district = rng.choice(TEHRAN_DISTRICTS) if (city is tehran_city and tehran_city) \
            else f"منطقه {rng.randint(1, 22)}"

        cat = cat_rows.get(rng.choice(CATEGORY_SLUGS)) if CATEGORY_SLUGS else None
        listing_type = "rent" if (cat and cat.slug.startswith("rent")) else "buy"
        area = rng.randint(45, 220)
        price_per_meter = rng.randint(80, 220) * 1_000_000
        seller = rng.choice(FAKE_SELLERS)

        prop = Property(
            tag_number=f"LOCAL-H{i:04d}",
            serial_no=5000 + i,
            divar_id=f"local-hist-{i:04d}",
            title=f"{'اجاره' if listing_type == 'rent' else 'فروش'} آپارتمان {area} متری "
                  f"{district}",
            description="آگهی نمونه برای تاریخچهٔ داشبورد — محیط توسعهٔ محلی، داده واقعی نیست.",
            price=price_per_meter * area if listing_type == "buy" else None,
            price_per_meter=price_per_meter,
            deposit=None if listing_type == "buy" else rng.randint(50, 600) * 1_000_000,
            rent_price=None if listing_type == "buy" else rng.randint(5, 80) * 1_000_000,
            area=area,
            rooms=rng.randint(1, 4),
            year_built=1390 + rng.randint(0, 15),
            floor=rng.randint(0, 12),
            total_floors=rng.randint(2, 16),
            has_elevator=rng.random() < 0.6,
            has_parking=rng.random() < 0.7,
            has_storage=rng.random() < 0.5,
            has_balcony=rng.random() < 0.8,
            has_images=False,
            city_id=city.id if city else None,
            city_name=city.name if city else "تهران",
            district=district,
            category_id=cat.id if cat else None,
            category_name=cat.name if cat else "خرید آپارتمان",
            property_type="apartment",
            listing_type=listing_type,
            phone_number=f"0912{9000000 + i:07d}",
            contact_channel="phone",
            seller_name=seller,
            advertiser_type="personal" if rng.random() < 0.75 else "agency",
            url=f"https://divar.ir/v/local-hist-listing-{i}/local-hist-{i:04d}",
            images=[],
            features=[],
            amenities=[],
            is_active=True,
            posted_at=created_at - timedelta(hours=rng.randint(0, 48)),
            created_at=created_at,
            scraped_at=scraped_at,
        )
        db.add(prop)
        props.append(prop)
    await db.flush()

    # ── ~40% of them get a lead ──────────────────────────────────────────────
    leads = []
    for prop in props:
        if rng.random() >= 0.40:
            continue
        lead_created = min(now, prop.created_at + timedelta(hours=rng.uniform(0, 72)))
        status = rng.choices(LEAD_STATUSES, weights=LEAD_STATUS_WEIGHTS, k=1)[0]
        lead = Lead(
            property_id=prop.id,
            phone_number=prop.phone_number,
            seller_name=prop.seller_name,
            city_name=prop.city_name,
            category_name=prop.category_name,
            listing_type=prop.listing_type,
            price=prop.price,
            area=prop.area,
            property_url=prop.url,
            property_title=prop.title,
            status=status,
            assigned_to=rng.choice(STAFF_NAMES),
            notes="لید نمونه — تاریخچهٔ داشبورد محیط توسعهٔ محلی.",
            created_at=lead_created,
            updated_at=lead_created,
        )
        db.add(lead)
        leads.append(lead)
    await db.flush()

    # ── calls: office hours, weekdays busier than Friday ────────────────────
    call_hours = list(CALL_HOUR_WEIGHTS.keys())
    call_hour_weights = list(CALL_HOUR_WEIGHTS.values())
    outcome_labels = list(OUTCOMES.values())
    calls_made = 0
    for day_offset in range(HISTORY_DAYS):
        day = today - timedelta(days=day_offset)
        is_friday = (day.weekday() + 2) % 7 == 6   # Saturday=0 … Friday=6
        n_calls = rng.randint(2, 8) if is_friday else rng.randint(15, 30)
        for _ in range(n_calls):
            hour = rng.choices(call_hours, weights=call_hour_weights, k=1)[0]
            when = _tehran_to_utc(day, hour, rng.randint(0, 59))
            if when > now:
                continue
            lead = rng.choice(leads) if leads else None
            db.add(ActivityLog(
                entity_type="lead",
                entity_id=lead.id if lead else 0,
                action="call",
                detail=rng.choice(outcome_labels),
                actor=rng.choice(STAFF_NAMES),
                created_at=when,
            ))
            calls_made += 1

    # ── a few status_change rows for the leads that closed or got rented ────
    won_leads = [lead for lead in leads if lead.status in ("closed", "rented")]
    for lead in won_leads:
        changed_at = min(now, lead.created_at + timedelta(hours=rng.uniform(2, 240)))
        db.add(ActivityLog(
            entity_type="lead",
            entity_id=lead.id,
            action="status_change",
            detail=f"وضعیت به «{lead.status}» تغییر کرد",
            actor=lead.assigned_to,
            created_at=changed_at,
        ))

    # ── calendar: visits (some upcoming), plus a couple of meetings ─────────
    events_made = 0
    for _ in range(55):
        day_offset = rng.randint(-HISTORY_DAYS, 7)
        day = today + timedelta(days=day_offset)
        hour = rng.randint(9, 18)
        start_at = _tehran_to_utc(day, hour, rng.choice((0, 30)))
        prop = rng.choice(props)
        status = "scheduled" if start_at > now else rng.choice(
            ("done", "done", "done", "canceled"))
        assignee = rng.choice(STAFF_NAMES)
        db.add(CalendarEvent(
            title=f"بازدید ملک — {prop.district or prop.city_name}",
            event_type="visit",
            description="بازدید نمونه — تاریخچهٔ داشبورد محیط توسعهٔ محلی.",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            location=f"{prop.city_name}، {prop.district or ''}".strip("، "),
            property_id=prop.id,
            owner_name=prop.seller_name,
            owner_phone=prop.phone_number,
            assigned_to=assignee,
            status=status,
            created_by=assignee,
        ))
        events_made += 1
    for _ in range(4):
        day_offset = rng.randint(-30, 5)
        day = today + timedelta(days=day_offset)
        start_at = _tehran_to_utc(day, rng.randint(10, 17), 0)
        assignee = rng.choice(STAFF_NAMES)
        db.add(CalendarEvent(
            title="نشست و تنظیم قرارداد",
            event_type="meeting",
            description="جلسهٔ قرارداد نمونه — تاریخچهٔ داشبورد محیط توسعهٔ محلی.",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            assigned_to=assignee,
            status="scheduled" if start_at > now else "done",
            created_by=assignee,
        ))
        events_made += 1

    # ── deals over the last 6 months ─────────────────────────────────────────
    deal_types = ["buy", "rent", "lease"]
    deal_type_weights = [45, 40, 15]
    deal_statuses = ["closed", "contract", "negotiating"]
    deal_status_weights = [55, 25, 20]
    deals_made = 0
    for _ in range(60):
        day_offset = rng.randint(0, 185)
        contract_date = _tehran_to_utc(today - timedelta(days=day_offset), rng.randint(10, 18))
        deal_type = rng.choices(deal_types, weights=deal_type_weights, k=1)[0]
        status = rng.choices(deal_statuses, weights=deal_status_weights, k=1)[0]
        district = rng.choice(TEHRAN_DISTRICTS)
        deal_type_fa = {"buy": "خرید و فروش", "rent": "رهن", "lease": "اجاره"}[deal_type]
        amount = rng.randint(2, 40) * 1_000_000_000 if deal_type == "buy" \
            else rng.randint(100, 900) * 1_000_000
        commission = rng.randint(30, 400) * 1_000_000
        db.add(Deal(
            title=f"معاملهٔ {deal_type_fa} — {district}",
            deal_type=deal_type,
            status=status,
            amount=amount,
            commission=commission,
            commission_paid=status == "closed" and rng.random() < 0.8,
            notes="معاملهٔ نمونه — تاریخچهٔ داشبورد محیط توسعهٔ محلی.",
            contract_date=contract_date,
            close_date=contract_date + timedelta(days=rng.randint(0, 10))
            if status == "closed" else None,
            created_at=contract_date,
        ))
        deals_made += 1

    # ── ~15 more customers ────────────────────────────────────────────────
    # app/api/routes/crm.py VALID_CUSTOMER_SOURCES has no «portal» — kept to
    # the sources the panel actually validates.
    sources = ["in_person", "divar", "referral"]
    temperatures = ["hot", "warm", "cold"]
    customers = []
    for i, name in enumerate(HISTORY_CUSTOMERS):
        customer = Customer(
            full_name=name,
            mobile1=f"0912{9500000 + i:07d}",
            source=sources[i % len(sources)],
            temperature=temperatures[i % len(temperatures)],
            consultant_name=rng.choice(STAFF_NAMES),
            budget_max=rng.randint(2, 20) * 1_000_000_000,
            desired_specs=f"{rng.randint(60, 200)} متر، {rng.randint(1, 4)} خواب",
            desired_district=rng.choice(TEHRAN_DISTRICTS),
            desired_city="تهران",
            desired_type="apartment",
            deal_type="buy" if i % 2 == 0 else "rent",
            notes="مشتری نمونه — تاریخچهٔ داشبورد محیط توسعهٔ محلی.",
        )
        db.add(customer)
        customers.append(customer)
    await db.flush()

    # ── a handful of customer matches ────────────────────────────────────────
    for customer in customers[:10]:
        prop = rng.choice(props)
        db.add(CustomerMatch(
            property_id=prop.id,
            customer_id=customer.id,
            score=rng.randint(50, 99),
            reasons=["منطقهٔ درخواستی", "بودجه در محدوده"],
            consultant=customer.consultant_name,
            status=rng.choice(("new", "new", "contacted")),
        ))

    print(f"history: created {len(props)} properties, {len(leads)} leads, "
          f"{calls_made} calls, {len(won_leads)} status changes, "
          f"{events_made} calendar events, {deals_made} deals, "
          f"{len(customers)} customers, {len(customers[:10])} matches")


async def seed_jobs(db, users):
    if await _existing(db, ScrapingJob, divar_phone="09120000001", status="completed"):
        print("scrape jobs: already seeded")
        return

    city = (await db.execute(select(City).where(City.slug == "tehran"))).scalar_one_or_none()
    cat = (await db.execute(select(Category).where(Category.slug == "buy-apartment"))).scalar_one_or_none()

    for n, phone in enumerate(("09120000001", "09120000002"), start=1):
        job_id = uuid.uuid4()
        started = datetime.now(timezone.utc) - timedelta(days=n, hours=1)
        finished = started + timedelta(minutes=25)
        job = ScrapingJob(
            job_id=job_id,
            city_id=city.id if city else None,
            category_id=cat.id if cat else None,
            status="completed",
            total_pages=5, scraped_pages=5,
            total_items=40, scraped_items=40, new_items=10 * n, updated_items=5,
            divar_phone=phone,
            accounts_used=[phone],
            finish_reason="پایان یافت — تمام آگهی‌های واجد شرایط بررسی شد",
            divar_count=40,
            started_at=started,
            completed_at=finished,
        )
        db.add(job)
        await db.flush()
        db.add(ScrapingLog(job_id=job_id, level="info", message="اسکرپ آغاز شد",
                           created_at=started))
        db.add(ScrapingLog(job_id=job_id, level="info",
                           message=f"{40} آگهی بررسی شد، {10 * n} مورد جدید",
                           created_at=finished))
    print("scrape jobs: created 2")


def _refuse_unless_local():
    # DATABASE_URL is read from the environment or ./.env; pointed at a real
    # database, the staff accounts (one an admin with every permission, all
    # with the password in this file) would commit before anything else ran.
    name = engine.url.database or ""
    if not name.endswith("_local"):
        sys.exit(f"refusing to seed {name!r}: only a database whose name ends "
                 "in _local (docker-compose.local.yml's divar_scraper_local)")


async def main():
    _refuse_unless_local()
    async with async_session_maker() as db:
        users = await seed_staff(db)
        await db.commit()
        await seed_cookies(db, users)
        await seed_properties_and_leads(db)
        await seed_customers(db)
        await seed_history(db)
        await seed_jobs(db, users)
        await db.commit()
    # leads and customers above name their consultant; the boot step would
    # resolve the accounts on the next restart, this does it now
    from app.auth.visibility import backfill_owner_ids
    async with engine.begin() as conn:
        print(f"owner accounts: {await conn.run_sync(backfill_owner_ids)} row(s) resolved")
    async with async_session_maker() as db:
        super_admins = (await db.execute(
            select(User).where(User.role.in_([ROLE_ROOT, ROLE_SUPER_ADMIN])))).scalars().all()

    print("\n── seeded logins (throwaway, local only) ──")
    for u in super_admins:
        print(f"  {u.username:<10} role={u.role:<12} password=<from .env.local — "
              f"SUPER_ADMIN_PASSWORD/ROOT_PASSWORD>")
    for username, full_name, role, _perms, _phone in STAFF:
        print(f"  {username:<10} role={role:<12} password={PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())

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
from app.models.user import User  # noqa: E402
from app.models.cookie import Cookie  # noqa: E402
from app.models.property import Property, City, Category  # noqa: E402
from app.models.lead import Lead  # noqa: E402
from app.models.crm_models import Customer  # noqa: E402
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
            assigned_to=STAFF[i % len(STAFF)][0],
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
        await seed_jobs(db, users)
        await db.commit()

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

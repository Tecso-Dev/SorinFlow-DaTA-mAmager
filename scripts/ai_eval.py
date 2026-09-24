#!/usr/bin/env python3
"""
ارزیابی هوش مصنوعی — a yardstick for app/ai/* and app/services/llm.py.

Phase 2 touches masking, embedding, matching and the assistant's privacy at
the same time, from several worktrees. This script is how "did it get
better or worse" stays a number instead of a feeling: run it before a
change and after it, on the same cases, and diff the two tables.

Sections (see the module docstring of each function below):
    masking            offline  app.services.llm.mask_pii
    embed_text         offline  app.ai.embeddings.text_of
    matching           offline  app.services.match_service.customers_for_property
    assistant_privacy  offline  app.ai.assistant.answer (a scripted fake model)
    reader             live     app.ai.listing_reader.ask
    need               live     app.ai.need_parser.parse_need
    retrieval          live     app.ai.embeddings.nearest + app.services.llm.embed

Offline sections need no network and always run. Live sections call the
real gateway through the app's own app.services.llm module (its ledger, its
cap) and only run with --live plus LLM_API_KEY/LLM_BASE_URL in the
environment — otherwise they are reported as "skipped: no key".

The whole thing runs against its own throwaway sqlite database in a temp
directory. DATABASE_URL and REDIS_URL are never read from the caller's
environment — this script sets its own before the first `app.*` import, so
running it can never touch a real database. LLM_API_KEY / LLM_BASE_URL /
LLM_MODEL, on the other hand, ARE read from the environment on purpose:
that is how --live is configured, and neither the key nor the base URL is
ever printed.

Usage:
    python scripts/ai_eval.py [--live] [--only masking,embed_text] \\
        [--json out.json] [--markdown out.md]

Exit code is always 0 unless the harness itself fails to start (it is a
measurement, not a gate) — a section that raises is caught and reported as
that section's own error, not a crash.
"""
import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CASES_DIR = Path(__file__).resolve().parent / "ai_eval"
SECTIONS = ("masking", "embed_text", "matching", "assistant_privacy", "reader", "need", "retrieval")
LIVE_SECTIONS = {"reader", "need", "retrieval"}

_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_SEPARATORS = re.compile(r"[\s\-.\(\)‌]")


def _norm_digits(s: str) -> str:
    return (s or "").translate(_FA_DIGITS)


def _load_case(name: str):
    return json.loads((CASES_DIR / name).read_text(encoding="utf-8"))


# ── environment / database ──────────────────────────────────────────────────

def _prepare_env(tmp_dir: str) -> None:
    """Its own sqlite database, its own fake secret — never the caller's.

    DATABASE_URL and REDIS_URL are overwritten even if the caller's shell
    already has real ones set, which is the whole point: this harness must
    never be one env var away from touching production data."""
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_dir}/ai_eval.db"
    os.environ["REDIS_URL"] = "redis://ai-eval-unused.invalid:6379/0"
    os.environ["SECRET_KEY"] = "ai-eval-harness-fake-secret-0123456789"
    os.environ["LOGS_PATH"] = tmp_dir
    os.environ["IMAGES_PATH"] = tmp_dir
    os.environ["SUPER_ADMIN_PASSWORD"] = "ai-eval-harness-fake-password-only"


async def _init_db() -> None:
    """create_all with every model imported — the same shape tests/*.py
    builds its own sqlite databases in. scraping_jobs/scraping_logs/
    skipped_listings carry a Postgres-only UUID column that sqlite's DDL
    compiler cannot render (see tests/test_ai_embed.py's `maker` fixture for
    the same exclusion); nothing this harness calls touches those tables."""
    import app.models  # noqa: F401  registers every table on Base.metadata
    from app.database import Base, engine

    skip = {"scraping_jobs", "scraping_logs", "skipped_listings"}
    tables = [t for t in Base.metadata.sorted_tables if t.name not in skip]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))


# ── 1. masking (offline) ────────────────────────────────────────────────────

def _phone_survives(masked_text: str, digits: str) -> bool:
    """True when some 7+ digit run of `digits` can still be read out of the
    masked text — checked as sliding 7-digit windows so a partial mask
    (the area code redacted, the subscriber number intact) still counts as
    a leak."""
    stripped = _SEPARATORS.sub("", _norm_digits(masked_text))
    digits = _norm_digits(digits)
    for i in range(max(0, len(digits) - 6)):
        if digits[i:i + 7] in stripped:
            return True
    return False


async def _section_masking():
    """app.services.llm.mask_pii — recall on ~40 phone formats, precision on
    ~20 prices/codes/dates that must survive untouched. Pure function, no DB."""
    from app.services.llm import mask_pii

    data = _load_case("masking.json")
    positives, negatives = data["positives"], data["negatives"]
    failures = []

    hits = 0
    for case in positives:
        masked = mask_pii(case["text"])
        survived = [p for p in case["digits"] if _phone_survives(masked, p)]
        if survived:
            failures.append({"id": case["id"], "reason": f"phone survived masking: {survived}"})
        else:
            hits += 1
    recall = hits / len(positives) if positives else 1.0

    fp = 0
    for case in negatives:
        masked = mask_pii(case["text"])
        if case["protected"] not in masked:
            fp += 1
            failures.append({"id": case["id"], "reason": f"protected text was altered: {case['protected']!r}"})
    precision_ok = 1 - fp / len(negatives) if negatives else 1.0

    return {
        "metrics": {"recall": round(recall, 3), "false_positives": fp, "precision_ok": round(precision_ok, 3)},
        "cases": {"recall": len(positives), "false_positives": len(negatives), "precision_ok": len(negatives)},
        "failures": failures,
    }


# ── 2. embed_text (offline) ─────────────────────────────────────────────────

async def _section_embed_text():
    """app.ai.embeddings.text_of on transient Property objects — no DB row is
    ever written. Each case's ai_facts is shaped like listing_reader's
    ListingFacts.model_dump(); expect_in_text is what a good embedding text
    built from those facts must contain."""
    from app.ai.embeddings import text_of
    from app.models.property import Property

    cases = _load_case("embed_text.json")
    found = total = 0
    failures = []
    for c in cases:
        prop = Property(title=c["title"], district=c.get("district"), city_name=c.get("city_name"),
                        property_type=c.get("property_type"), area=c.get("area"), rooms=c.get("rooms"),
                        listing_type=c.get("listing_type", "buy"), description=c.get("description"))
        prop.ai_facts = c["ai_facts"]
        text = text_of(prop)
        missing = [w for w in c["expect_in_text"] if w not in text]
        total += len(c["expect_in_text"])
        found += len(c["expect_in_text"]) - len(missing)
        if missing:
            failures.append({"id": c["key"], "reason": f"missing from text_of: {', '.join(missing)}"})

    coverage = found / total if total else 1.0
    return {"metrics": {"coverage": round(coverage, 3)}, "cases": {"coverage": total}, "failures": failures}


# ── 3. matching (offline, DB) ───────────────────────────────────────────────

async def _section_matching():
    """app.services.match_service.customers_for_property, scored the way
    app.crm.match_engine does (MIN_SCORE, use_llm=False). One "old" customer
    is created before ≥320 filler customers to probe the CANDIDATE_POOL=300
    window in customers_for_property's `order by id desc limit 300` query."""
    from app.crm.match_engine import MIN_SCORE
    from app.database import async_session_maker
    from app.models.crm_models import Customer
    from app.models.property import Property
    from app.services.match_service import customers_for_property

    data = _load_case("matching.json")

    async with async_session_maker() as db:
        listings = {}
        for i, row in enumerate(data["listings"]):
            p = Property(tag_number=f"m-{row['key']}", divar_id=f"m-{row['key']}",
                        url=f"https://divar.ir/v/m-{row['key']}", serial_no=2000 + i,
                        title=row["title"], listing_type=row["listing_type"], property_type=row["property_type"],
                        district=row["district"], city_name=row.get("city_name", "ارومیه"),
                        area=row.get("area"), rooms=row.get("rooms"), total_price=row.get("total_price"),
                        deposit=row.get("deposit"), rent_price=row.get("rent_price"), is_active=True)
            db.add(p)
            listings[row["key"]] = p
        await db.flush()

        # (1) the old customer, first — lowest id in the table.
        old = data["old_customer"]
        old_c = Customer(full_name=old["full_name"], deal_type=old["deal_type"], desired_type=old["desired_type"],
                         desired_city=old["desired_city"], desired_district=old["desired_district"],
                         budget_max=old["budget_max"], desired_specs=old["desired_specs"])
        db.add(old_c)
        await db.flush()

        # (2) ≥320 fillers, after it, fitting nothing (wrong city).
        for i in range(320):
            db.add(Customer(full_name=f"مشتری پرکننده {i}", deal_type="buy", desired_type="apartment",
                            desired_city="تهران", budget_max=9000000000))
        await db.flush()

        # (3) the labelled customers, last — always inside the newest-300 window.
        customers = {}
        for row in data["customers"]:
            c = Customer(full_name=row["full_name"], deal_type=row["deal_type"], desired_type=row["desired_type"],
                        desired_city=row["desired_city"], desired_district=row.get("desired_district"),
                        budget_max=row.get("budget_max"), desired_specs=row.get("desired_specs"))
            db.add(c)
            customers[row["key"]] = c
        await db.commit()

        id_to_key = {c.id: key for key, c in customers.items()}
        expected = {key: set() for key in listings}
        for row in data["customers"]:
            for lk in row.get("fits", []):
                expected[lk].add(row["key"])

        tp = fp = total_expected = 0
        old_found = False
        failures = []
        for lk, prop in listings.items():
            results = await customers_for_property(db, prop, limit=5, use_llm=False)
            got_ids = {r["id"] for r in results if r["score"] >= MIN_SCORE}
            if lk in old.get("fits", []) and old_c.id in got_ids:
                old_found = True
            got_keys = {id_to_key[i] for i in got_ids if i in id_to_key}
            exp = expected.get(lk, set())
            total_expected += len(exp)
            tp += len(got_keys & exp)
            fp += len(got_keys - exp)
            for missed in exp - got_keys:
                failures.append({"id": f"{lk}:{missed}", "reason": "expected customer was not matched"})
            for extra in got_keys - exp:
                failures.append({"id": f"{lk}:{extra}", "reason": "unexpected customer was matched"})
        if not old_found:
            failures.append({"id": "old_customer", "reason": "the oldest customer fell outside the newest-300 window"})

    recall = tp / total_expected if total_expected else 1.0
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    return {
        "metrics": {"recall": round(recall, 3), "precision": round(precision, 3), "old_customer_found": int(old_found)},
        "cases": {"recall": total_expected, "precision": tp + fp, "old_customer_found": 1},
        "failures": failures,
    }


# ── 4. assistant_privacy (offline, DB, fake model) ──────────────────────────

async def _section_assistant_privacy():
    """app.ai.assistant.answer, called as agent1 (an admin whose consultant
    name is «مینا کاظمی»), against a scripted fake app.services.llm.chat that
    asks for everything: every tool in assistant.TOOLS, including agent2's
    customers, a private listing, a draft listing and a serial with a phone
    number in its description. Whatever the fake model actually receives is
    the real privacy boundary — not what the code is supposed to do."""
    import inspect
    from sqlalchemy import select
    from app.ai import assistant
    from app.database import async_session_maker
    from app.models.crm_models import Customer
    from app.models.property import Property
    from app.models.user import User
    from app.services import llm

    # Names of their own — scripts/ai_eval/matching.json seeds ~350 customers
    # in the same throwaway database, and a name shared between the two
    # sections would make the by-name lookup below ambiguous.
    PRIVATE_TOKEN, DRAFT_TOKEN = "زمردنشان", "یاقوت‌کاشی"
    LISTING_PHONE = "09140009999"
    CONSULTANTS = {
        "مینا کاظمی": ["وحید عباسی", "رویا کیانی", "پیمان صالحی"],
        "رضا نادری": ["آرش مرادی", "ندا فتحی", "بابک سلطانی"],
    }
    NO_CONSULTANT = ["یاسمن عزیزی", "کامران رحیمی"]

    async with async_session_maker() as db:
        db.add_all([
            User(username="ai_eval_root", hashed_password="x", role="root", full_name="ریشه"),
            User(username="agent1", hashed_password="x", role="admin", full_name="مینا کاظمی",
                permissions=["properties", "crm"]),
            User(username="agent2", hashed_password="x", role="admin", full_name="رضا نادری",
                permissions=["properties", "crm"]),
        ])

        # updated_at is set explicitly (not the server default) so these rows
        # are unambiguously "most recent" for tool_customers' `ORDER BY
        # updated_at DESC` even when matching.json's ~350 rows landed in the
        # same wall-clock second — sqlite's CURRENT_TIMESTAMP only has
        # one-second resolution, Python's datetime.now() does not.
        now = datetime.now(timezone.utc)
        seeded_phones, n = [LISTING_PHONE], 0
        for consultant, names in CONSULTANTS.items():
            for name in names:
                n += 1
                mobile = f"0914000{n:04d}"
                seeded_phones.append(mobile)
                db.add(Customer(full_name=name, consultant_name=consultant, mobile1=mobile,
                               updated_at=now + timedelta(microseconds=n)))
        for name in NO_CONSULTANT:
            n += 1
            mobile = f"0914000{n:04d}"
            seeded_phones.append(mobile)
            db.add(Customer(full_name=name, mobile1=mobile, updated_at=now + timedelta(microseconds=n)))

        def _listing(i, title, **kw):
            return Property(tag_number=f"priv-{i}", divar_id=f"priv-{i}", url=f"https://divar.ir/v/priv-{i}",
                            serial_no=9000 + i, title=title, city_name="ارومیه", is_active=True, **kw)

        db.add(_listing(1, "آپارتمان ۸۰ متری خیابان گلها"))
        db.add(_listing(2, "آپارتمان ۹۰ متری والفجر"))
        db.add(_listing(3, "مغازه ۳۰ متری خیابان کاشانی"))
        private_listing = _listing(4, f"آپارتمان لوکس {PRIVATE_TOKEN} در شهرک امامت",
                                   is_private=True, created_by="رضا نادری")
        draft_listing = _listing(5, f"ویلای پیش‌نویس {DRAFT_TOKEN} در بلوار استادان",
                                 is_draft=True, created_by="رضا نادری")
        phone_listing = _listing(6, "آپارتمان ۷۰ متری خیابان دانشکده",
                                 description=f"تماس مستقیم با مالک: {LISTING_PHONE}")
        db.add_all([private_listing, draft_listing, phone_listing])
        await db.commit()
        private_serial, draft_serial = private_listing.serial_no, draft_listing.serial_no

        seen_messages, call_n = [], [0]

        async def fake_chat(job, messages, *, agent, db=None, schema=None, json_mode=False,
                            max_tokens=400, temperature=0.2, timeout=40.0, cap=True,
                            model_override=None, tools=None):
            call_n[0] += 1
            seen_messages.append(json.loads(json.dumps(messages, ensure_ascii=False)))
            if call_n[0] == 1:
                wanted = [
                    ("customers", {"limit": 15}), ("count_listings", {}),
                    ("search_listings", {"query": PRIVATE_TOKEN}),
                    ("property", {"serial_no": private_serial}), ("property", {"serial_no": draft_serial}),
                    ("queue_status", {}), ("today_digest", {}),
                ]
                tool_calls = [{"id": f"c{i}", "type": "function",
                              "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
                             for i, (name, args) in enumerate(wanted)]
                message = {"role": "assistant", "content": None, "tool_calls": tool_calls}
                return {"content": "", "data": None, "model": "ai-eval/fake", "usage": {}, "cost_usd": 0.0,
                       "cost_toman": 0.0, "ms": 1, "message": message, "tool_calls": tool_calls}
            message = {"role": "assistant", "content": "پایان"}
            return {"content": "پایان", "data": None, "model": "ai-eval/fake", "usage": {}, "cost_usd": 0.0,
                   "cost_toman": 0.0, "ms": 1, "message": message, "tool_calls": []}

        async def fake_embed(texts, *, agent, db=None, timeout=40.0):
            # no network call attempted — the assistant must fall back to text search
            raise llm.NotConfigured("ai_eval: embedding is off for the privacy probe")

        real_chat, real_embed = llm.chat, llm.embed
        llm.chat, llm.embed = fake_chat, fake_embed
        try:
            sig = inspect.signature(assistant.answer)
            question = "همه مشتری‌ها و آگهی‌ها رو با جزئیات کامل بگو"
            if "user" in sig.parameters:
                agent1 = (await db.execute(select(User).where(User.username == "agent1"))).scalars().one()
                result = await assistant.answer(db, question, user=agent1, chat_id="1")
            else:
                result = await assistant.answer(db, question, who="agent1", chat_id="1")
        finally:
            llm.chat, llm.embed = real_chat, real_embed

        blob = json.dumps(seen_messages, ensure_ascii=False)
        norm_blob = _norm_digits(blob)
        customers = (await db.execute(select(Customer))).scalars().all()
        by_name = {c.full_name: c for c in customers}

        def present(name: str) -> bool:
            c = by_name.get(name)
            token = f"مشتری-{c.id}" if c else None
            return name in blob or bool(token and token in blob)

        all_names = [n for names in CONSULTANTS.values() for n in names] + NO_CONSULTANT
        customer_names_sent = sum(1 for n in all_names if present(n))
        other_consultant_customers = sum(1 for n in CONSULTANTS["رضا نادری"] if present(n))
        own_customers_visible = sum(1 for n in CONSULTANTS["مینا کاظمی"] if present(n))
        phones_sent = sum(1 for ph in seeded_phones if _norm_digits(ph) in norm_blob)
        names_in_reply = sum(1 for n in CONSULTANTS["مینا کاظمی"] if n in result["text"])

    failures = []
    if other_consultant_customers:
        failures.append({"id": "other_consultant_customers",
                         "reason": f"{other_consultant_customers}/3 of رضا نادری's customers reached the model"})
    if private_listing_leaked := int(PRIVATE_TOKEN in blob):
        failures.append({"id": "private_listing", "reason": "the private listing's token reached the model"})
    if draft_listing_leaked := int(DRAFT_TOKEN in blob):
        failures.append({"id": "draft_listing", "reason": "the draft listing's token reached the model"})
    if phones_sent:
        failures.append({"id": "phones_sent", "reason": f"{phones_sent} seeded phone number(s) reached the model"})

    return {
        "metrics": {
            "customer_names_sent": customer_names_sent,
            "other_consultant_customers": other_consultant_customers,
            "private_listing_leaked": private_listing_leaked,
            "draft_listing_leaked": draft_listing_leaked,
            "phones_sent": phones_sent,
            "own_customers_visible": own_customers_visible,
            "names_in_reply": names_in_reply,
        },
        "cases": {"customer_names_sent": len(all_names), "other_consultant_customers": 3,
                  "private_listing_leaked": 1, "draft_listing_leaked": 1, "phones_sent": len(seeded_phones),
                  "own_customers_visible": 3, "names_in_reply": 3},
        "failures": failures,
    }


# ── 5. reader (live) ─────────────────────────────────────────────────────────

def _match_reader_field(expected, got):
    if isinstance(expected, list):
        got_list = [str(x) for x in (got or [])]
        return all(any(e in g or g in e for g in got_list) for e in expected)
    return got == expected


async def _section_reader():
    """app.ai.listing_reader.ask, the way the reader's own pass calls it, on
    transient Property objects (never persisted)."""
    from app.ai import listing_reader as reader
    from app.database import async_session_maker
    from app.models.property import Property

    cases = _load_case("reader.json")
    sem = asyncio.Semaphore(3)
    hits, totals, failures = {}, {}, []

    async def one(case):
        prop = Property(title=case["title"], description=case.get("description"),
                        property_type=case.get("property_type"), listing_type=case.get("listing_type"),
                        area=case.get("area"), rooms=case.get("rooms"), floor=case.get("floor"),
                        total_floors=case.get("total_floors"), year_built=case.get("year_built"),
                        district=case.get("district"), city_name="ارومیه", deposit=case.get("deposit"),
                        rent_price=case.get("rent_price"), total_price=case.get("total_price"))
        async with sem:
            async with async_session_maker() as db:
                try:
                    facts = await reader.ask(db, prop, agent="ai_eval_reader")
                except Exception as e:
                    for field in case["expected"]:
                        totals[field] = totals.get(field, 0) + 1
                    failures.append({"id": case["id"], "reason": f"{type(e).__name__}: {e}"})
                    return
        for field, expected in case["expected"].items():
            totals[field] = totals.get(field, 0) + 1
            if _match_reader_field(expected, facts.get(field)):
                hits[field] = hits.get(field, 0) + 1
            else:
                failures.append({"id": f"{case['id']}:{field}",
                                 "reason": f"expected {expected!r}, got {facts.get(field)!r}"})

    await asyncio.gather(*(one(c) for c in cases))
    metrics = {f"{f}_accuracy": round(hits.get(f, 0) / n, 3) for f, n in totals.items()}
    total_hits, total_n = sum(hits.values()), sum(totals.values())
    metrics["overall"] = round(total_hits / total_n, 3) if total_n else 0.0
    cases_out = {f"{f}_accuracy": n for f, n in totals.items()}
    cases_out["overall"] = total_n
    return {"metrics": metrics, "cases": cases_out, "failures": failures}


# ── 6. need (live) ───────────────────────────────────────────────────────────

def _match_need_field(field, expected, criteria):
    if field == "district_contains":
        return expected in " ".join(criteria.get("districts") or [])
    if field in ("budget_max", "deposit_max", "rent_max"):
        got = criteria.get(field)
        return got is not None and abs(got - expected) <= 0.15 * expected
    if field in ("must_have", "red_lines"):
        got = [str(x) for x in (criteria.get(field) or [])]
        return all(any(e in g or g in e for g in got) for e in expected)
    return criteria.get(field) == expected


async def _section_need():
    """app.ai.need_parser.parse_need, on free-form colloquial need texts."""
    from app.ai import need_parser
    from app.database import async_session_maker

    cases = _load_case("need.json")
    sem = asyncio.Semaphore(3)
    hits, totals, failures = {}, {}, []

    async def one(case):
        async with sem:
            async with async_session_maker() as db:
                try:
                    out = await need_parser.parse_need(db, case["text"])
                except Exception as e:
                    for field in case["expected"]:
                        totals[field] = totals.get(field, 0) + 1
                    failures.append({"id": case["id"], "reason": f"{type(e).__name__}: {e}"})
                    return
        criteria = out["criteria"]
        for field, expected in case["expected"].items():
            totals[field] = totals.get(field, 0) + 1
            if _match_need_field(field, expected, criteria):
                hits[field] = hits.get(field, 0) + 1
            else:
                failures.append({"id": f"{case['id']}:{field}", "reason": f"expected {expected!r}"})

    await asyncio.gather(*(one(c) for c in cases))
    metrics = {f"{f}_accuracy": round(hits.get(f, 0) / n, 3) for f, n in totals.items()}
    total_hits, total_n = sum(hits.values()), sum(totals.values())
    metrics["overall"] = round(total_hits / total_n, 3) if total_n else 0.0
    cases_out = {f"{f}_accuracy": n for f, n in totals.items()}
    cases_out["overall"] = total_n
    return {"metrics": metrics, "cases": cases_out, "failures": failures}


# ── 7. retrieval (live) ─────────────────────────────────────────────────────

async def _section_retrieval():
    """20 listings embedded through app.services.llm.embed (one batched
    call), 8 queries embedded in a second call, ranked with
    app.ai.embeddings.nearest. Metric: recall@3."""
    from app.ai.embeddings import nearest, text_of
    from app.database import async_session_maker
    from app.models.property import Property
    from app.services import llm

    data = _load_case("retrieval.json")
    props = []
    for row in data["listings"]:
        p = Property(title=row["title"], district=row.get("district"), city_name=row.get("city_name"),
                    property_type=row.get("property_type"), listing_type=row.get("listing_type"),
                    area=row.get("area"), rooms=row.get("rooms"), floor=row.get("floor"),
                    description=row.get("description"))
        p.ai_facts = row.get("ai_facts")
        props.append((row["key"], p))

    async with async_session_maker() as db:
        vectors = await llm.embed([text_of(p) for _, p in props], agent="ai_eval_retrieval", db=db)
        index = list(enumerate(vectors))
        key_by_pos = {i: key for i, (key, _) in enumerate(props)}

        queries = data["queries"]
        qvecs = await llm.embed([q["text"] for q in queries], agent="ai_eval_retrieval", db=db)

    hits, failures = 0, []
    for q, qvec in zip(queries, qvecs):
        top = [key_by_pos[i] for i, _ in nearest(qvec, index, limit=3)]
        if q["expected"] in top:
            hits += 1
        else:
            failures.append({"id": q["id"], "reason": f"expected {q['expected']} not in top-3 {top}"})

    recall_at_3 = hits / len(queries) if queries else 1.0
    return {"metrics": {"recall_at_3": round(recall_at_3, 3)}, "cases": {"recall_at_3": len(queries)},
           "failures": failures}


# ── orchestration ────────────────────────────────────────────────────────────

SECTION_FUNCS = {
    "masking": _section_masking, "embed_text": _section_embed_text, "matching": _section_matching,
    "assistant_privacy": _section_assistant_privacy, "reader": _section_reader,
    "need": _section_need, "retrieval": _section_retrieval,
}


async def _run(live: bool, only) -> dict:
    await _init_db()
    can_live = live and bool(os.environ.get("LLM_API_KEY")) and bool(os.environ.get("LLM_BASE_URL"))
    out = {}
    for name in SECTIONS:
        if only and name not in only:
            continue
        if name in LIVE_SECTIONS and not can_live:
            out[name] = {"skipped": "no key"}
            continue
        try:
            out[name] = await SECTION_FUNCS[name]()
        except Exception as e:
            # a section's own bug is a result, not a harness crash
            out[name] = {"error": f"{type(e).__name__}: {e}"}
    return out


def _print_table(results: dict) -> None:
    rows = []
    for section, res in results.items():
        if "skipped" in res:
            rows.append((section, "-", f"skipped: {res['skipped']}", "-"))
        elif "metrics" not in res:
            rows.append((section, "-", f"error: {res.get('error', '?')}", "-"))
        else:
            metrics, cases = res["metrics"], res.get("cases", {})
            for metric, value in metrics.items():
                rows.append((section, metric, value, cases.get(metric, "-")))
    w1 = max([len("section")] + [len(r[0]) for r in rows])
    w2 = max([len("metric")] + [len(str(r[1])) for r in rows])
    w3 = max([len("value")] + [len(str(r[2])) for r in rows])
    print(f"{'section':<{w1}}  {'metric':<{w2}}  {'value':>{w3}}  cases")
    for section, metric, value, cases in rows:
        print(f"{section:<{w1}}  {str(metric):<{w2}}  {str(value):>{w3}}  {cases}")


def _write_json(path: str, results: dict, live: bool) -> None:
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "live": live, "sections": results}
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: str, results: dict) -> None:
    lines = ["| section | metric | value | cases |", "|---|---|---|---|"]
    for section, res in results.items():
        if "skipped" in res:
            lines.append(f"| {section} | - | skipped: {res['skipped']} | - |")
        elif "metrics" not in res:
            lines.append(f"| {section} | - | error: {res.get('error', '?')} | - |")
        else:
            metrics, cases = res["metrics"], res.get("cases", {})
            for metric, value in metrics.items():
                lines.append(f"| {section} | {metric} | {value} | {cases.get(metric, '-')} |")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="کیفیت ایجنت‌های هوش مصنوعی، قبل و بعد از هر تغییر")
    ap.add_argument("--live", action="store_true", help="بخش‌های زنده (reader, need, retrieval) هم اجرا شوند")
    ap.add_argument("--only", default="", help="فقط این بخش‌ها، جدا شده با کاما")
    ap.add_argument("--json", default="", help="مسیر فایل خروجی JSON")
    ap.add_argument("--markdown", default="", help="مسیر فایل خروجی Markdown")
    return ap.parse_args()


def main() -> int:
    args = _parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()} or None
    with tempfile.TemporaryDirectory(prefix="sorinflow_ai_eval_") as tmp:
        _prepare_env(tmp)
        results = asyncio.run(_run(args.live, only))
    _print_table(results)
    if args.json:
        _write_json(args.json, results, args.live)
    if args.markdown:
        _write_markdown(args.markdown, results)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
The audit call sites wired after the audit trail landed: the AI screen's
knobs, a CRM delete and export, and «سورین»'s Telegram unlink. Each is the
route function itself, called with its session, and what it leaves in
audit_events — not a grep for the call.
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_audit_wiring.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

NS = f"wire{uuid.uuid4().hex[:6]}"      # the shared table holds other files' rows too


async def _setup():
    from app.database import async_session_maker, engine
    from app.models.app_setting import AppSetting
    from app.models.audit_event import AuditEvent
    from app.models.crm_models import Contact, Customer
    from app.models.telegram_link import TelegramLink
    from app.models.user import User
    async with engine.begin() as conn:
        for t in (User, AuditEvent, AppSetting, Contact, Customer, TelegramLink):
            await conn.run_sync(t.__table__.create, checkfirst=True)
    async with async_session_maker() as db:
        root = User(username=f"{NS}_root", hashed_password="x", role="root", is_active=True)
        agent = User(username=f"{NS}_agent", hashed_password="x", role="admin", is_active=True,
                     permissions=["crm"])
        customer = Customer(full_name="مشتری آزمایشی ممیزی", mobile1="09140009911")
        db.add_all([root, agent, customer])
        await db.commit()
        db.add(TelegramLink(user_id=agent.id, telegram_user_id=900_000_000 + uuid.uuid4().int % 99_999_999))
        await db.commit()
        return root, agent, customer.id


async def _events(actor):
    from app.database import async_session_maker
    from app.models.audit_event import AuditEvent
    async with async_session_maker() as db:
        rows = (await db.execute(select(AuditEvent).where(AuditEvent.actor_username == actor)
                                 .order_by(AuditEvent.id))).scalars().all()
        return [(r.action, r.target_type, r.target_id) for r in rows]


def test_the_ai_knobs_a_crm_delete_an_export_and_an_unlink_leave_a_row():
    from app.api.routes import ai as ai_routes, crm, telegram_link
    from app.database import async_session_maker

    async def _go():
        root, agent, customer_id = await _setup()
        async with async_session_maker() as db:
            await ai_routes.ai_agent_cap("reader", ai_routes.AgentCapIn(cap_usd=0.4), db=db, user=root)
            await ai_routes.ai_agent_switch("vision", ai_routes.AgentSwitchIn(enabled=False), db=db, user=root)
            await crm.delete_customer(customer_id, db=db, current_user=agent)
            await crm.export_contacts_json(db=db, current_user=agent)
            await telegram_link.my_telegram_unlink(db=db, user=agent)
        return await _events(root.username), await _events(agent.username), customer_id

    by_root, by_agent, customer_id = asyncio.run(_go())
    assert ("ai_agent_cap_set", "ai_agent", "reader") in by_root
    assert ("ai_agent_toggle", "ai_agent", "vision") in by_root
    assert ("crm_delete", "customer", str(customer_id)) in by_agent
    assert ("crm_export", "contacts", None) in by_agent
    assert ("telegram_unlink", "telegram", None) in by_agent


def test_every_wired_action_has_a_persian_label():
    """The «رویدادها» filter lists actions from this catalog; an action
    without a label would show as its raw key."""
    from app.services.audit import ACTIONS
    for key in ("ai_settings_save", "ai_agent_toggle", "ai_agent_cap_set", "telegram_link",
                "telegram_unlink", "crm_export", "crm_delete", "leads_bulk", "portal_ticket_decide"):
        assert ACTIONS.get(key), key

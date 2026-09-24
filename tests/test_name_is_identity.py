"""
A display name another account already goes by is refused — in the
profile, in the owner's user editor, and when a visitor is approved into
staff. Ownership is by account (app/auth/visibility.py), but a colleague is
named in a form and resolved to the one account that goes by the name: two
accounts sharing one would leave every row typed with it nobody's.
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_name_identity.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

async def _users():
    NS = uuid.uuid4().hex[:6]           # each test its own people in the shared table
    from app.database import async_session_maker, engine
    from app.models.audit_event import AuditEvent
    from app.models.user import User
    async with engine.begin() as conn:
        for t in (User, AuditEvent):
            await conn.run_sync(t.__table__.create, checkfirst=True)
    async with async_session_maker() as db:
        mina = User(username=f"mina{NS}", full_name=f"مینا کاظمی {NS}", hashed_password="x",
                    role="admin", is_active=True)
        reza = User(username=f"reza{NS}", full_name=f"رضا نادری {NS}", hashed_password="x",
                    role="admin", is_active=True)
        boss = User(username=f"boss{NS}", full_name=None, hashed_password="x",
                    role="super_admin", is_active=True)
        db.add_all([mina, reza, boss])
        await db.commit()
        return mina.id, reza.id, boss.id, NS


def _run(coro):
    return asyncio.run(coro)


def test_the_profile_refuses_a_colleagues_name_and_keeps_ones_own():
    from app.api.routes.users import update_me
    from app.database import async_session_maker
    from app.models.user import User
    from app.schemas import ProfileUpdate
    mina_id, reza_id, boss_id, NS = _run(_users())

    async def _as(uid, name):
        async with async_session_maker() as db:
            me = await db.get(User, uid)
            return await update_me(ProfileUpdate(full_name=name), current_user=me, db=db)

    with pytest.raises(HTTPException) as e:
        _run(_as(reza_id, f"مینا کاظمی {NS}"))
    assert e.value.status_code == 409
    with pytest.raises(HTTPException):
        _run(_as(reza_id, f"  مینا کاظمی {NS} "))            # spacing does not get around it
    with pytest.raises(HTTPException):
        _run(_as(reza_id, f"boss{NS}"))                     # nor another account's username
    _run(_as(reza_id, f"رضا نادری {NS}"))                   # one's own name is fine
    _run(_as(reza_id, f"رضا نادری بخش فروش {NS}"))          # and so is a new one


def test_the_owners_editor_refuses_it_too():
    from app.api.routes.users import update_user
    from app.database import async_session_maker
    from app.models.user import User
    from app.schemas import UserUpdate
    mina_id, reza_id, boss_id, NS = _run(_users())

    async def _go():
        async with async_session_maker() as db:
            boss = await db.get(User, boss_id)
            return await update_user(reza_id, UserUpdate(full_name=f"مینا کاظمی {NS}"), actor=boss, db=db)

    with pytest.raises(HTTPException) as e:
        _run(_go())
    assert e.value.status_code == 409


def test_a_username_cannot_become_a_colleagues_full_name():
    """Someone without a full name is known by their username, so renaming
    the username to a colleague's (Latin) full name was the side door."""
    from app.api.routes.users import update_me
    from app.database import async_session_maker
    from app.models.user import User
    from app.schemas import ProfileUpdate

    async def _go():
        from app.database import engine
        from app.models.audit_event import AuditEvent
        async with engine.begin() as conn:
            for tbl in (User, AuditEvent):
                await conn.run_sync(tbl.__table__.create, checkfirst=True)
        ns = uuid.uuid4().hex[:6]
        async with async_session_maker() as db:
            sara = User(username=f"s{ns}", full_name=f"sara.k{ns}", hashed_password="x", role="admin", is_active=True)
            bob = User(username=f"b{ns}", full_name=None, hashed_password="x", role="admin", is_active=True)
            db.add_all([sara, bob])
            await db.commit()
            return bob.id, ns

    bob_id, ns = _run(_go())

    async def _rename():
        async with async_session_maker() as db:
            me = await db.get(User, bob_id)
            return await update_me(ProfileUpdate(username=f"sara.k{ns}"), current_user=me, db=db)

    with pytest.raises(HTTPException) as e:
        _run(_rename())
    assert e.value.status_code == 409


def test_making_a_visitor_staff_checks_the_name_they_signed_up_with():
    from app.api.routes.users import update_user
    from app.database import async_session_maker
    from app.models.user import User
    from app.schemas import UserUpdate
    mina_id, reza_id, boss_id, NS = _run(_users())

    async def _go():
        async with async_session_maker() as db:
            visitor = User(username=f"v{NS}", full_name=f"مینا کاظمی {NS}", hashed_password="x",
                           role="visitor", is_active=True)
            db.add(visitor)
            await db.commit()
            boss = await db.get(User, boss_id)
            return await update_user(visitor.id, UserUpdate(role="admin"), actor=boss, db=db)

    with pytest.raises(HTTPException) as e:
        _run(_go())
    assert e.value.status_code == 409

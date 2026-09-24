"""
«سورین» answers a person, not a chat — and only with that person's rights.

It used to answer anyone in the backup's Telegram chats, read the whole
office for them, and send the model every customer's full name. Now a panel
user links their own Telegram account with a one-time code from their
profile; the bot answers only linked users and only in a private chat;
every tool reads with the asker's panel rights (app/auth/visibility.py);
and a customer reaches the model only as «مشتری-<id>».

A sqlite database of its own and a fake Redis, so this runs the same in the
sqlite suite and the Postgres one. The model and Telegram are fakes.
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

import fakeredis
import fakeredis.aioredis
import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_assistant_per_user.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.ai import assistant, embeddings  # noqa: E402
from app.database import Base  # noqa: E402
from app.models.ai_chat import AiChat  # noqa: E402
from app.models.app_setting import AppSetting  # noqa: E402
from app.models.crm_models import Binder, Cabinet, Customer, CustomerMatch, PriceAlert  # noqa: E402
from app.models.lead import Lead  # noqa: E402
from app.models.property import Category, City, Property  # noqa: E402
from app.models.telegram_link import TelegramLink  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import llm  # noqa: E402

ZWNJ = chr(0x200C)
PLACE = "کوچهٔ آزمون"          # every listing here is on it, so a search finds exactly these
TABLES = [m.__table__ for m in (User, TelegramLink, AiChat, AppSetting, City, Category, Cabinet, Binder,
                                Property, Lead, Customer, CustomerMatch, PriceAlert)]
BAD_CODE = "این کد درست نیست یا منقضی شده است؛ از پروفایل پنل کد تازه بگیرید."


def fa(n) -> str:
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _prop(serial, **kw):
    return Property(tag_number=f"t-{serial}", divar_id=f"d-{serial}", url=f"https://divar.ir/v/{serial}",
                    title=f"آپارتمان {PLACE} {serial}", district=PLACE, city_name="ارومیه", area=100, rooms=2,
                    listing_type="buy", total_price=4_000_000_000, serial_no=serial, is_active=True,
                    created_at=datetime.now(timezone.utc), **kw)


async def _seed(s):
    """One office: root, two consultants with their customers and their own
    private and draft files, and accounts short of a permission or of a
    staff role."""
    people = {
        "root": User(username="root", full_name="روت", role="root"),
        "mina": User(username="mina", full_name="مینا رضایی", role="admin", permissions=["properties", "crm", "filing"]),
        "reza": User(username="reza", full_name="رضا کریمی", role="admin", permissions=["properties", "crm", "filing"]),
        "nocrm": User(username="nocrm", full_name="نیما", role="admin", permissions=["properties"]),
        "noprops": User(username="noprops", full_name="نرگس", role="admin", permissions=["crm"]),
        "visitor": User(username="visitor", full_name="بازدیدکننده", role="visitor"),
        "gone": User(username="gone", full_name="رفته", role="admin", permissions=["properties", "crm"], is_active=False),
    }
    for u in people.values():
        u.hashed_password = "x"
        u.is_active = u.is_active is not False
    props = {
        "public": _prop(7001),
        "mina_private": _prop(7002, is_private=True, created_by="مینا رضایی"),
        "reza_private": _prop(7003, is_private=True, created_by="رضا کریمی"),
        "reza_draft": _prop(7004, is_draft=True, created_by="رضا کریمی"),
        "nobodys_private": _prop(7005, is_private=True),
    }
    s.add_all([*people.values(), *props.values()])
    await s.flush()
    pub = props["public"].id
    sara = Customer(full_name="سارا امیری", mobile1="09121110000", temperature="hot", consultant_name="مینا رضایی",
                    desired_district="گلها", budget_max=5_000_000_000, notes="همسرش بهرام کاظمی، 09125556677")
    bahram = Customer(full_name="بهرام کاظمی", mobile1="09122220000", temperature="warm", consultant_name="رضا کریمی")
    negar = Customer(full_name="نگار صادقی", mobile1="09123330000", temperature="cold")
    s.add_all([sara, bahram, negar])
    await s.flush()
    now = datetime.now(timezone.utc)
    s.add_all([
        Lead(property_id=pub, phone_number="09120000001", status="new"),
        Lead(property_id=pub, phone_number="09120000002", status="new", assigned_to="مینا رضایی"),
        Lead(property_id=pub, phone_number="09120000003", status="new", assigned_to="رضا کریمی"),
        CustomerMatch(property_id=pub, customer_id=sara.id, consultant="مینا رضایی", status="new", score=70),
        CustomerMatch(property_id=pub, customer_id=bahram.id, consultant="رضا کریمی", status="new", score=70),
        CustomerMatch(property_id=pub, customer_id=negar.id, consultant=None, status="new", score=70),
        PriceAlert(property_id=pub, from_amount=5, to_amount=4, delta_pct=-20, moved_at=now, status="new"),
        PriceAlert(property_id=props["reza_private"].id, from_amount=5, to_amount=4, delta_pct=-20,
                   moved_at=now, status="new"),
    ])
    await s.commit()
    return people, {"props": {k: p.id for k, p in props.items()},
                    "sara": sara.id, "bahram": bahram.id, "negar": negar.id}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The office in a database of its own, a fake Redis, a fake Telegram —
    every message the bot sends lands in world["sent"] — and the API as the
    panel calls it."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.database as database
    from app.api.routes import router as api_router
    from app.auth.jwt import access_claims, create_access_token
    from app.services import backup_service as bk

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/sorin.db", poolclass=NullPool)
    maker = async_sessionmaker(eng, expire_on_commit=False)

    async def build():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=TABLES))
        async with maker() as s:
            return await _seed(s)
    users, ids = asyncio.run(build())

    # one fake Redis server; a client per call, so no client outlives its event loop
    server = fakeredis.FakeServer()

    async def get_redis():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(database, "get_redis", get_redis)

    sent, updates = [], []

    async def tg_request(token, method, route=None, **kw):
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "sorin_test_bot"}}), "direct"
        if method == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": list(updates)}), "direct"
        if method == "sendMessage":
            sent.append(kw["json"])
        return httpx.Response(200, json={"ok": True}), "direct"

    async def resolve_telegram(db=None):
        # the bot's token, and no backup chat at all: the assistant needs none
        return {"token": "t-test", "chat_id": "", "source": "panel"}

    async def resolve_route(db=None):
        return {}
    monkeypatch.setattr(bk, "tg_request", tg_request)
    monkeypatch.setattr(bk, "resolve_telegram", resolve_telegram)
    monkeypatch.setattr(bk, "resolve_route", resolve_route)
    monkeypatch.setattr(assistant, "_bot", {"token": "", "name": "", "retry_at": 0.0})

    async def no_model(*a, **kw):
        raise AssertionError("the model was called")
    monkeypatch.setattr(llm, "chat", no_model)

    api = FastAPI()
    api.include_router(api_router, prefix="/api")

    async def session():
        async with maker() as s:
            yield s
    api.dependency_overrides[database.get_db] = session
    client = TestClient(api)

    def call(method, path, who=None, **kw):
        headers = {"Authorization": f"Bearer {create_access_token(access_claims(users[who]))}"} if who else {}
        return client.request(method, f"/api{path}", headers=headers, **kw)

    yield {"maker": maker, "users": users, "ids": ids, "sent": sent, "updates": updates,
           "server": server, "api": call}
    asyncio.run(eng.dispose())


def _tg(world, text, *, tid, chat=None, kind="private", username=None):
    """One Telegram message through the bot; what it answered, or None."""
    sender = {"id": tid, "is_bot": False, "first_name": "x", **({"username": username} if username else {})}
    msg = {"message_id": 1, "chat": {"id": chat or tid, "type": kind}, "from": sender, "text": text}

    async def go():
        async with world["maker"]() as db:
            return await assistant.handle_update(db, {"update_id": 1, "message": msg}, token="t-test", route={})
    return asyncio.run(go())


def _code(world, who):
    r = world["api"]("POST", "/users/me/telegram/link-code", who)
    assert r.status_code == 200, r.text
    return r.json()["code"]


def _link(world, who, tid):
    """Linked the way a person does it: a code from the profile, sent in private."""
    assert _tg(world, f"/start {_code(world, who)}", tid=tid) == f"حساب شما به {world['users'][who].full_name} وصل شد"


def _links(world):
    """{username: telegram id} for every link there is."""
    async def go():
        async with world["maker"]() as db:
            rows = (await db.execute(select(User.username, TelegramLink.telegram_user_id)
                                     .join(TelegramLink, TelegramLink.user_id == User.id))).all()
            return dict(rows)
    return asyncio.run(go())


def _tool(world, who, name, **args):
    async def go():
        async with world["maker"]() as db:
            return await assistant.run_tool(db, world["users"][who], name, json.dumps(args))
    return asyncio.run(go())


def _script(monkeypatch, *turns):
    """llm.chat as a script: each call answers with the next turn — a
    (tool, arguments) pair or the final text — and keeps everything the
    model was sent, as the gateway would have received it."""
    seen = []

    async def chat(job, messages, *, agent, db=None, tools=None, **kw):
        seen.append(json.dumps(messages, ensure_ascii=False))
        turn = turns[len(seen) - 1]
        if isinstance(turn, str):
            return {"content": turn, "tool_calls": [], "message": {"role": "assistant", "content": turn}}
        name, args = turn
        call = {"id": f"c{len(seen)}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
        return {"content": "", "tool_calls": [call],
                "message": {"role": "assistant", "content": None, "tool_calls": [call]}}
    monkeypatch.setattr(llm, "chat", chat)
    return seen


# ── linking ──────────────────────────────────────────────────────────────────

class TestLinking:

    def test_a_code_is_eight_plain_characters_that_live_ten_minutes_and_are_stored_hashed(self, world):
        r = world["api"]("POST", "/users/me/telegram/link-code", "mina")
        assert r.status_code == 200, r.text
        d = r.json()
        assert re.fullmatch(r"[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}", d["code"]), "no 0/O or 1/I/L to misread"
        assert d["expires_in"] == 600 and d["command"] == f"/start {d['code']}"
        assert d["deep_link"] == f"https://t.me/sorin_test_bot?start={d['code']}"

        async def redis_state():
            r = fakeredis.aioredis.FakeRedis(server=world["server"], decode_responses=True)
            keys = await r.keys("*")
            return await r.ttl(assistant._code_key(d["code"])), keys, [await r.get(k) for k in keys]
        ttl, keys, values = asyncio.run(redis_state())
        assert 590 <= ttl <= 600
        assert not any(d["code"] in x for x in keys + values), "a Redis dump must not hold a live code"

    def test_a_new_code_replaces_the_old_one(self, world):
        first, second = _code(world, "mina"), _code(world, "mina")
        assert _tg(world, f"/start {first}", tid=111) == BAD_CODE
        assert _tg(world, f"/start {second}", tid=111) == "حساب شما به مینا رضایی وصل شد"
        assert _links(world) == {"mina": 111}

    def test_start_in_private_links_the_sender_once(self, world):
        code = _code(world, "mina")
        assert _tg(world, f"/start {code}", tid=111, username="mina_tg") == "حساب شما به مینا رضایی وصل شد"
        me = world["api"]("GET", "/users/me/telegram", "mina").json()
        assert me["linked"] and me["telegram_username"] == "mina_tg" and me["linked_at"]
        assert _tg(world, f"/start {code}", tid=222) == BAD_CODE, "a code is spent by its first use"
        assert _links(world) == {"mina": 111}
        # typed by hand, without /start and in lower case, a code works as well
        assert _tg(world, _code(world, "reza").lower(), tid=333) == "حساب شما به رضا کریمی وصل شد"

    def test_a_group_refuses_the_code_and_burns_it(self, world):
        code = _code(world, "mina")
        assert _tg(world, f"/start {code}", tid=111, chat=-100500, kind="supergroup") is None
        assert _links(world) == {} and world["sent"] == []
        # everyone in that group saw it: it is nobody's now
        assert _tg(world, f"/start {code}", tid=111) == BAD_CODE

    def test_relinking_moves_the_telegram_account_and_replaces_the_old_link(self, world):
        _link(world, "mina", 111)
        _link(world, "reza", 111)
        assert _links(world) == {"reza": 111}, "one Telegram account is one panel user"
        _link(world, "reza", 222)
        assert _links(world) == {"reza": 222}, "one panel user is one Telegram account"

    def test_unlinking_silences_the_bot(self, world):
        _link(world, "mina", 111)
        world["sent"].clear()
        assert world["api"]("DELETE", "/users/me/telegram", "mina").json() == {"linked": False}
        assert world["api"]("GET", "/users/me/telegram", "mina").json() == {"linked": False}
        assert _tg(world, "صف تماس چطوره؟", tid=111) is None and world["sent"] == []

    def test_wrong_codes_are_answered_five_times_then_not_at_all(self, world):
        for _ in range(assistant.BAD_CODES):
            assert _tg(world, "/start AAAAAAAA", tid=111) == BAD_CODE
        assert _tg(world, "/start AAAAAAAA", tid=111) is None
        good = _code(world, "mina")
        assert _tg(world, f"/start {good}", tid=111) is None, "not even tried once the guesses are spent"
        assert _links(world) == {}
        assert _tg(world, f"/start {good}", tid=333) == "حساب شما به مینا رضایی وصل شد", "the limit is per account"

    def test_only_staff_may_link(self, world):
        for method, path in (("GET", "/users/me/telegram"), ("POST", "/users/me/telegram/link-code"),
                             ("DELETE", "/users/me/telegram")):
            assert world["api"](method, path, "visitor").status_code == 403, path
            assert world["api"](method, path).status_code == 401, path


# ── who may ask ──────────────────────────────────────────────────────────────

class TestWhoMayAsk:

    def test_an_unlinked_private_chat_hears_nothing(self, world):
        assert _tg(world, "صف تماس چطوره؟", tid=999) is None
        assert _tg(world, "/start", tid=999) is None
        assert world["sent"] == [], "an unknown chat does not learn that a bot listens"

        async def seen():
            async with world["maker"]() as db:
                return [c["id"] for c in await assistant.seen_chats(db)]
        assert "999" in asyncio.run(seen()), "still remembered for the backup card's «پیدا کن»"

    def test_a_linked_user_in_a_group_is_sent_to_private_once_an_hour(self, world):
        _link(world, "mina", 111)
        world["sent"].clear()
        assert _tg(world, "صف تماس چطوره؟", tid=111, chat=-100500, kind="group") == assistant.PRIVATE_ONLY
        assert world["sent"] == [{"chat_id": -100500, "text": "لطفاً در چت خصوصی با من بپرسید",
                                  "disable_web_page_preview": True}]
        assert _tg(world, "مشتری‌های من؟", tid=111, chat=-100500, kind="group") is None, "not under every message"
        assert _tg(world, "صف تماس چطوره؟", tid=999, chat=-100500, kind="group") is None
        assert len(world["sent"]) == 1

    def test_an_inactive_or_non_staff_account_is_refused(self, world):
        async def link_directly(who, tid):
            async with world["maker"]() as db:
                db.add(TelegramLink(user_id=world["users"][who].id, telegram_user_id=tid))
                await db.commit()
        asyncio.run(link_directly("gone", 222))
        asyncio.run(link_directly("visitor", 333))
        assert _tg(world, "صف تماس چطوره؟", tid=222) is None
        assert _tg(world, "صف تماس چطوره؟", tid=333) is None
        assert world["sent"] == []

        async def ask():
            async with world["maker"]() as db:
                return await assistant.answer(db, "صف تماس؟", user=world["users"]["gone"])
        assert asyncio.run(ask())["text"] == assistant.NO_ACCESS, "and no model call (it would raise)"

    def test_the_bot_listens_with_no_backup_chat_configured(self, world):
        _link(world, "mina", 111)
        world["updates"].append({"update_id": 5, "message": {
            "chat": {"id": 111, "type": "private"}, "from": {"id": 111}, "text": "/help"}})

        async def poll():
            async with world["maker"]() as db:
                return await assistant.poll_once(db)
        assert asyncio.run(poll()) == {"updates": 1, "answered": 1, "offset": 6}
        assert world["sent"][-1]["text"] == assistant.HELP


# ── what each person may read ────────────────────────────────────────────────

def _serials(result):
    return {row["serial_no"] for row in result.get("examples", result.get("results", []))}


class TestScope:

    def test_a_consultant_sees_their_own_customers_and_the_unassigned_ones(self, world):
        ids = world["ids"]
        token = assistant.customer_token

        def tokens(who, **args):
            return {c["customer"] for c in _tool(world, who, "customers", **args)["customers"]}
        assert tokens("mina") == {token(ids["sara"]), token(ids["negar"])}
        assert tokens("reza") == {token(ids["bahram"]), token(ids["negar"])}
        assert tokens("root") == {token(ids["sara"]), token(ids["bahram"]), token(ids["negar"])}
        assert tokens("mina", customer_id=ids["bahram"]) == set(), "not by id either"
        assert tokens("mina", consultant="کریمی") == set(), "nor by the colleague's name"

    def test_others_private_and_draft_listings_are_invisible_everywhere(self, world, monkeypatch):
        for who, visible in (("mina", {7001, 7002}), ("reza", {7001, 7003, 7004}),
                             ("root", {7001, 7002, 7003, 7004, 7005})):
            counted = _tool(world, who, "count_listings", district=PLACE)
            assert counted["count"] == len(visible) and _serials(counted) == visible, who
            assert _serials(_tool(world, who, "search_listings", query=PLACE)) == visible, who
        # a listing mina may not see answers exactly like one that does not exist
        for serial in (7003, 7004, 7005):
            assert _tool(world, "mina", "property", serial_no=serial) == _tool(world, "mina", "property", serial_no=99999)
        assert _tool(world, "mina", "property", serial_no=7002)["found"] is True

        async def nearest_everything(db, text, **kw):
            return [(pid, 0.9) for pid in world["ids"]["props"].values()]
        monkeypatch.setattr(embeddings, "semantic_candidates", nearest_everything)
        found = _tool(world, "mina", "search_listings", query="هر چیزی")
        assert found["how"] == "semantic" and _serials(found) == {7001, 7002}, "semantic hits are filtered too"

    def test_the_queue_is_the_askers_own_and_the_dashboard_keeps_the_office(self, world):
        def queue(who):
            q = _tool(world, who, "queue_status")
            return q["calls_due"], q["matches_waiting"], q["price_drops_new"], q["listings_today"], q["listings_active"]
        # calls: unassigned + theirs; matches: theirs + nobody's; drops and
        # listings: only on listings they may see
        assert queue("mina") == (2, 2, 1, 2, 2)
        assert queue("reza") == (2, 2, 2, 3, 3)
        assert queue("root") == (1, 3, 2, 5, 5), "root's own calls; everything else"

        async def office():
            async with world["maker"]() as db:
                return await assistant.tool_queue_status(db)
        assert asyncio.run(office()) == {"calls_due": 3, "matches_waiting": 3, "price_drops_new": 2,
                                         "listings_today": 5, "listings_active": 5}, "/stats/today is unchanged"

    def test_each_tool_needs_the_permission_the_panel_would_ask_for(self, world):
        refused = {"error": "دسترسی ندارید"}
        for tool, args in (("count_listings", {}), ("search_listings", {"query": PLACE}), ("property", {"serial_no": 7001})):
            assert _tool(world, "noprops", tool, **args) == refused, tool
            assert "error" not in _tool(world, "nocrm", tool, **args), tool
        for tool in ("customers", "queue_status"):
            assert _tool(world, "nocrm", tool) == refused, tool
            assert "error" not in _tool(world, "noprops", tool), tool
        for who in ("visitor", "gone"):
            for tool in assistant._TOOL_FUNCS:
                assert _tool(world, who, tool) == refused, (who, tool)

    def test_the_digest_is_root_and_super_admin_only(self, world, monkeypatch):
        from app.crm import digest

        async def build(db, **kw):
            return {"text": "خلاصهٔ کل دفتر"}
        monkeypatch.setattr(digest, "build", build)
        assert _tool(world, "mina", "today_digest") == {"error": "دسترسی ندارید"}, "every permission, still not the office's day"
        assert _tool(world, "root", "today_digest") == {"text": "خلاصهٔ کل دفتر"}


# ── names ────────────────────────────────────────────────────────────────────

class TestNamesStayHome:

    def test_the_model_reads_tokens_and_the_telegram_reply_reads_names(self, world, monkeypatch):
        ids = world["ids"]
        sara, negar, bahram = ids["sara"], ids["negar"], ids["bahram"]
        _link(world, "mina", 111)
        seen = _script(monkeypatch, ("customers", {}),
                       f"مشتری-{sara} داغ است. مشتری {fa(sara)} و مشتری #{fa(sara)} و مشتری{ZWNJ}{sara} یکی‌اند؛ "
                       f"مشتری-{negar} مشاور ندارد و مشتری-{bahram} مال شما نیست.")
        reply = _tg(world, "سارا امیری چی می‌خواد؟ شمارهٔ من 09129998877 است", tid=111)

        to_model = "\n".join(seen)
        for secret in ("سارا امیری", "نگار صادقی", "بهرام کاظمی",
                       "09121110000", "09123330000", "09125556677", "09129998877"):
            assert secret not in to_model, secret
        assert f"مشتری-{sara} چی می‌خواد" in seen[0], "the name in the question went out as its token"
        tool_result = json.loads(seen[1])[-1]
        assert tool_result["role"] == "tool"
        assert f'"customer": "مشتری-{sara}"' in tool_result["content"] and f'"customer": "مشتری-{negar}"' in tool_result["content"]
        assert reply.count("سارا امیری") == 4 and "نگار صادقی" in reply
        assert f"مشتری-{bahram}" in reply and "بهرام کاظمی" not in reply, "a customer she may not see stays a token"
        assert world["sent"][-1]["text"] == reply, "Telegram got the names"

        async def logged():
            async with world["maker"]() as db:
                return (await db.execute(select(AiChat))).scalars().one()
        row = asyncio.run(logged())
        assert row.who == "mina" and row.chat_id == "111" and row.tools == ["customers"]

    def test_names_are_swapped_as_whole_words_and_come_back_only_for_the_visible(self):
        out = assistant._hide_names(f"سارا  امیری، سارا امیری{ZWNJ}ها، علیرضا و علي", {12: "سارا امیری", 5: "علی"})
        assert out == f"مشتری-12، مشتری-12{ZWNJ}ها، علیرضا و مشتری-5"
        back = assistant._show_names(f"مشتری-12، مشتری ۱۲، مشتری #۱۲، مشتري١٢، مشتری-7، مشتری{ZWNJ}ها ۱۲ نفر، مشتری-123",
                                     {12: "سارا امیری"})
        assert back == f"سارا امیری، سارا امیری، سارا امیری، سارا امیری، مشتری-7، مشتری{ZWNJ}ها ۱۲ نفر، مشتری-123"

    def test_a_search_the_model_writes_with_a_token_embeds_the_token(self, world, monkeypatch):
        """search_listings sends its query to the embedding model — another
        model: a token the model put in it must not turn back into the name."""
        sara = world["ids"]["sara"]
        embedded = []

        async def nearest(db, text, **kw):
            embedded.append(text)
            return []
        monkeypatch.setattr(embeddings, "semantic_candidates", nearest)
        _link(world, "mina", 111)
        _script(monkeypatch, ("search_listings", {"query": f"خانهٔ حیاط‌دار برای مشتری-{sara}"}), "چیزی نیست")
        _tg(world, "یک خانهٔ حیاط‌دار برای سارا امیری پیدا کن", tid=111)
        assert embedded == [f"خانهٔ حیاط‌دار برای مشتری-{sara}"]


# ── the panel did not change ─────────────────────────────────────────────────

class TestThePanelIsUnchanged:

    def test_the_customer_list_still_shows_everyone_to_anyone_with_crm(self, world):
        r = world["api"]("GET", "/crm/customers", "mina")
        assert r.status_code == 200, r.text
        assert {c["full_name"] for c in r.json()["items"]} == {"سارا امیری", "بهرام کاظمی", "نگار صادقی"}

    def test_filing_and_matches_keep_their_own_rules(self, world):
        """The helpers moved to app/auth/visibility.py; the panel still runs
        its own rules through them — a colleague's draft stays visible in the
        filing screen, where only «شخصی» hides a file."""
        r = world["api"]("GET", "/filing/files", "mina", params={"search": PLACE})
        assert r.status_code == 200, r.text
        assert {f["serial_no"] for f in r.json()["items"]} == {7001, 7002, 7004}
        r = world["api"]("GET", "/crm/matches", "mina")
        assert r.status_code == 200, r.text
        assert {m["customer"]["full_name"] for m in r.json()["items"]} == {"سارا امیری", "نگار صادقی"}

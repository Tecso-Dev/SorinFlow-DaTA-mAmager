"""
The AI gateway says the credit is gone — the door keeps the reason, and pauses (#35).

The scheduler log showed «[photo] listing not tagged: HTTP …» and «[reader]
listing not read: HTTP …» 64 times each, and the explainer's «Workspace …»,
and nothing said why: the door kept the first 160 characters of the body. Two
things were wrong. The cause was thrown away. And if the cause was an account
that is out of credit, every listing of every pass asked again and was refused
again — an attempt counted against each listing for something that was never
the listing's fault.

What these tests hold app/services/llm.py to:

  * a refusal's body is kept — scrubbed of numbers, addresses and the API key,
    at most 1000 characters in the error and the log, 300 in the ledger (its
    column) — so the next occurrence explains itself;
  * a refusal that says the credit, the quota or the balance is used up (402;
    or 403/429 whose body says so — a plain rate limit does not) pauses every
    agent until the next Tehran midnight or until the AI card's test is
    answered, whichever comes first — one pause, in app_settings, so every pod
    honours it — and is logged once, not once per call.

The agents that must stop asking are test_ai_quota_agents.py; what the panel
is told is test_ai_quota_card.py. The fakes are in _ai_quota_kit.py.
"""
import json
import os
import sys
from datetime import datetime, timedelta

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_ai_quota_pause.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool                                         # noqa: E402

from _ai_quota_kit import (                                                  # noqa: E402
    GATEWAY_WORDS, OUT_OF_CREDIT, UTC, _answer, _chat, _configure, _forget_what_was_told, _freeze, _gateway,
    _ledger, _pause_now, _refuse, _setting, capture_logs, open_world, reset_process,
)
from app.database import Base                                                # noqa: E402
from app.models.ai_usage import AiUsage                                      # noqa: E402
from app.models.app_setting import AppSetting                                # noqa: E402
from app.services import llm, secret_box                                     # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_process():
    reset_process()


@pytest.fixture
def logs():
    with capture_logs() as lines:
        yield lines


@pytest.fixture
async def world(monkeypatch, tmp_path):
    async with open_world(monkeypatch, tmp_path) as maker:
        yield maker


# ── what the gateway said ────────────────────────────────────────────────────

class TestWhatTheGatewaySaid:

    async def test_the_whole_reason_is_kept_not_the_first_160_characters(self, world, monkeypatch):
        reason = "پردازش رد شد. " * 30 + "علت اصلی: TAIL-OF-THE-MESSAGE"
        _gateway(monkeypatch, lambda r: _refuse(400, {"error": {"message": reason}}))
        with pytest.raises(llm.LLMError) as e:
            await _chat()
        text = str(e.value)
        assert text.startswith("HTTP 400: ")
        assert "TAIL-OF-THE-MESSAGE" in text, "the cause sat past character 160 and was cut off"

    async def test_it_is_bounded_at_1000_characters(self, world, monkeypatch):
        _gateway(monkeypatch, lambda r: _refuse(400, "x" * 5000))
        with pytest.raises(llm.LLMError) as e:
            await _chat()
        assert llm.ERROR_BODY_MAX == 1000
        assert len(str(e.value)) <= len("HTTP 400: ") + llm.ERROR_BODY_MAX
        assert len(str(e.value)) > 900, "bounded, not truncated to nothing"

    async def test_the_ledger_keeps_what_its_column_can_hold(self, world, monkeypatch):
        """ai_usage.error is a String(300): more would fail the insert and the
        failure would vanish from the ledger altogether."""
        _gateway(monkeypatch, lambda r: _refuse(400, {"error": {"message": "علت. " + "y" * 4000}}))
        with pytest.raises(llm.LLMError):
            await _chat()
        [row] = await _ledger(world)
        assert row.ok is False and row.agent == "vision"
        assert row.error.startswith('HTTP 400: {"error":{"message":"علت.')
        assert len(row.error) == 300

    async def test_numbers_and_addresses_in_a_body_are_masked_everywhere_it_goes(self, world, monkeypatch, logs):
        body = {"error": {"message": "contact 09143495300 or ali@example.ir about ۰۹۱۲۳۴۵۶۷۸۹"}}
        _gateway(monkeypatch, lambda r: _refuse(400, body))
        with pytest.raises(llm.LLMError) as e:
            await _chat()
        [row] = await _ledger(world)
        for where in (str(e.value), row.error, " ".join(logs)):
            for secret in ("09143495300", "ali@example.ir", "۰۹۱۲۳۴۵۶۷۸۹"):
                assert secret not in where
        assert "contact" in str(e.value), "the rest of the sentence survives the masking"

    async def test_the_api_key_is_never_stored_or_logged_even_when_the_gateway_echoes_it(self, world, monkeypatch, logs):
        _configure(monkeypatch, key="sk-live-0123456789abcdef")
        _gateway(monkeypatch, lambda r: _refuse(401, {"error": {"message": "Incorrect API key provided: sk-live-0123456789abcdef"}}))
        with pytest.raises(llm.LLMError) as e:
            await _chat()
        [row] = await _ledger(world)
        for where in (str(e.value), row.error, " ".join(logs)):
            assert "sk-live-0123456789abcdef" not in where
        assert "Incorrect API key provided" in str(e.value), "the rest of the sentence is what tells the reader what to fix"

    async def test_a_body_that_is_not_json_is_kept_too(self, world, monkeypatch):
        _gateway(monkeypatch, lambda r: _refuse(502, "<html><body><h1>Bad Gateway</h1>\n  upstream   timed out</body></html>"))
        with pytest.raises(llm.LLMError) as e:
            await _chat()
        assert "Bad Gateway" in str(e.value) and "upstream timed out" in str(e.value), "whitespace is collapsed"
        _gateway(monkeypatch, lambda r: _refuse(500, b""))
        with pytest.raises(llm.LLMError) as e:
            await _chat()
        assert str(e.value).startswith("HTTP 500")

    async def test_embed_keeps_the_reason_too(self, world, monkeypatch):
        _gateway(monkeypatch, lambda r: _refuse(400, {"error": {"message": "q" * 300 + " EMBED-TAIL"}}))
        with pytest.raises(llm.LLMError) as e:
            await llm.embed(["متن"], agent="embed")
        assert "EMBED-TAIL" in str(e.value)


# ── which refusals are about credit ──────────────────────────────────────────

CREDIT = [
    ("a 402 says it whatever the body", 402, "", False),
    ("a 402 with a plain sentence", 402, "Payment Required", False),
    ("the OpenAI-compatible quota answer", 429,
     '{"error":{"message":"You exceeded your current quota, please check your plan and billing details.","type":"insufficient_quota","code":"insufficient_quota"}}', False),
    ("workspace with insufficient credit", 403, '{"error":{"message":"Workspace has insufficient credit"}}', False),
    ("workspace daily token limit, plain text", 403, "Workspace daily token limit exceeded", False),
    ("free tokens finished", 429, '{"message":"Workspace free tokens are exhausted for today"}', False),
    ("balance not enough", 403, '{"detail":"Your balance is not enough. Please top up your account."}', False),
    ("quota exceeded for the workspace", 429, '{"error":"Quota exceeded for this workspace"}', False),
    ("workspace budget", 429, "Workspace budget exceeded", False),
    ("workspace spending limit", 403, '{"error":{"message":"Workspace has reached its spending limit."}}', False),
    ("insufficient balance by code", 429, '{"error":{"message":"Insufficient balance","type":"insufficient_balance"}}', False),
    ("out of credit", 403, "Your workspace is out of credit.", False),
    ("no remaining tokens", 429, "No remaining tokens for this workspace", False),
    ("does not have enough credit", 403, "The workspace does not have enough credit to run this request", False),
    ("the credit is used up", 429, "Workspace credit used up", False),
    ("a daily request limit", 429, "Daily request limit exceeded", False),
    ("a daily message limit", 403, '{"error":{"message":"You have reached your daily message limit."}}', False),
    ("Persian: the credit is finished", 403, '{"message":"اعتبار حساب شما به پایان رسیده است"}', False),
    ("Persian: the balance is not enough", 403, "موجودی کیف پول کافی نیست", False),
    ("Persian: the daily cap is over", 429, "سقف مصرف روزانهٔ فضای کاری به پایان رسیده است", False),
    ("a Retry-After does not hide a body that is plainly about credit", 429,
     "You exceeded your current quota, please check your plan and billing details.", True),
]

RATE_LIMITS_AND_OTHER_REFUSALS = [
    ("a plain rate limit", 429, '{"error":{"message":"Rate limit reached for requests","type":"rate_limit_exceeded"}}', False),
    ("a tokens-per-minute rate limit", 429,
     "Rate limit reached for gpt-4 in organization org-abc on tokens per min (TPM): Limit 10000, Used 9800, Requested 500. Please try again in 1.8s.", False),
    ("too many requests", 429, "Too many requests", False),
    ("a Retry-After with a body that only says limit", 429, '{"error":"limit reached, slow down"}', True),
    ("a Retry-After with nothing else", 429, "", True),
    ("the workspace's own rate limit", 429, "Workspace rate limit exceeded, retry in 5 seconds", False),
    ("concurrent requests", 429, "Too many concurrent requests for this workspace", False),
    ("a per-minute quota is a rate", 429, "Quota exceeded for quota metric 'requests per minute'", False),
    ("a bare 429", 429, "", False),
    ("a bare 403", 403, "", False),
    ("forbidden", 403, '{"error":{"message":"Forbidden"}}', False),
    ("an invalid key", 403, "Invalid API key", False),
    ("a model this key may not use", 403, "You do not have access to this model", False),
    ("insufficient permissions is not insufficient credit", 403, "Insufficient permissions to perform this action", False),
    ("no token provided is not about tokens left", 403, "No token provided", False),
    ("being rate limited", 429, "You are being rate limited", False),
    ("an Azure-style token rate limit", 429,
     "Requests to the ChatCompletions_Create Operation have exceeded token rate limit of your current S0 pricing tier. Please retry after 20 seconds.", False),
    ("an overloaded model", 429, '{"error":{"message":"The model is overloaded. Please try again later."}}', False),
    ("a region that is not supported", 403, "Country, region, or territory not supported", False),
    ("a safety refusal", 403, '{"error":{"message":"Your request was blocked by the safety system"}}', False),
    ("a workspace that does not exist", 403, "Workspace not found", False),
    ("a workspace member without the right", 403, "Workspace member has insufficient permissions to do this", False),
    ("a workspace payload that is too big", 403, "Workspace request payload exceeded the allowed size", False),
    ("a 401 is the key, whatever it says", 401, "insufficient credit", False),
    ("a 400 is the request", 400, "balance", False),
    ("a 500 is the road", 500, "quota database timeout, insufficient credit backend", False),
    ("a 503 is the road", 503, "credit service unavailable", False),
]


class TestWhichRefusalsAreAboutCredit:

    @pytest.mark.parametrize("why,status,body,retry_after", CREDIT, ids=[c[0] for c in CREDIT])
    def test_credit_refusals(self, why, status, body, retry_after):
        assert llm.quota_exhausted(status, body, retry_after=retry_after) is True, why

    @pytest.mark.parametrize("why,status,body,retry_after", RATE_LIMITS_AND_OTHER_REFUSALS,
                             ids=[c[0] for c in RATE_LIMITS_AND_OTHER_REFUSALS])
    def test_everything_else_stays_what_it_was(self, why, status, body, retry_after):
        assert llm.quota_exhausted(status, body, retry_after=retry_after) is False, why

    async def test_escaped_persian_in_a_json_body_is_read_as_persian(self, world, monkeypatch):
        """json.dumps escapes Persian by default (\\u0627\\u0639…): the check
        must run on the words, not on the escapes."""
        raw = json.dumps({"message": "اعتبار حساب شما تمام شده است"}).encode("ascii")
        assert b"\\u0627" in raw
        _gateway(monkeypatch, lambda r: httpx.Response(403, content=raw))
        with pytest.raises(llm.QuotaExhausted):
            await _chat()

    async def test_a_plain_429_with_retry_after_is_still_a_rate_limit(self, world, monkeypatch):
        _gateway(monkeypatch, lambda r: _refuse(429, {"error": {"message": "Rate limit reached"}}, {"Retry-After": "7"}))
        with pytest.raises(llm.RateLimited) as e:
            await _chat()
        assert e.value.retry_after == 7 and not isinstance(e.value, llm.QuotaExhausted)
        assert await _setting(world, llm.KEY_PAUSE) is None, "a throttle is not a pause until tomorrow"

    async def test_a_429_without_retry_after_that_is_a_rate_limit_is_not_a_pause(self, world, monkeypatch):
        _gateway(monkeypatch, lambda r: _refuse(429, {"error": {"message": "Rate limit reached for requests"}}))
        with pytest.raises(llm.LLMError) as e:
            await _chat()
        assert not isinstance(e.value, llm.QuotaExhausted)
        assert llm._breaker.fails == 1, "the breaker counts it as it always did"
        assert await _setting(world, llm.KEY_PAUSE) is None


# ── the pause ────────────────────────────────────────────────────────────────

class TestTheDayAPauseLasts:

    def test_it_ends_at_the_next_midnight_in_tehran(self):
        # 20:30 UTC is 00:00 in Tehran (+03:30, fixed)
        assert llm._next_tehran_midnight(datetime(2026, 9, 28, 12, 0, tzinfo=UTC)) == datetime(2026, 9, 28, 20, 30, tzinfo=UTC)
        assert llm._next_tehran_midnight(datetime(2026, 9, 28, 20, 29, 59, tzinfo=UTC)) == datetime(2026, 9, 28, 20, 30, tzinfo=UTC)
        assert llm._next_tehran_midnight(datetime(2026, 9, 28, 20, 30, tzinfo=UTC)) == datetime(2026, 9, 29, 20, 30, tzinfo=UTC)
        assert llm._next_tehran_midnight(datetime(2026, 9, 28, 21, 0, tzinfo=UTC)) == datetime(2026, 9, 29, 20, 30, tzinfo=UTC)

    def test_it_is_the_same_day_the_daily_cap_counts(self):
        now = datetime(2026, 9, 28, 9, 15, tzinfo=UTC)
        assert llm._next_tehran_midnight(now) == llm._day_start_utc(now) + timedelta(days=1)


class TestThePause:

    async def test_a_402_pauses_every_agent_until_the_next_tehran_midnight(self, world, monkeypatch):
        _freeze(monkeypatch, "2026-09-28T12:00:00")
        asked = _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        with pytest.raises(llm.QuotaExhausted) as first:
            await _chat("vision", "vision")
        assert len(asked) == 1
        stored = json.loads(await _setting(world, llm.KEY_PAUSE))
        assert stored["status"] == 402 and stored["message"] == GATEWAY_WORDS
        assert stored["until"] == "2026-09-28T20:30:00+00:00" and stored["agent"] == "vision"
        assert first.value.gateway_message == GATEWAY_WORDS and first.value.status == 402

        # every other agent, every job, the embedder: refused before anything goes out
        for agent, job in (("reader", "read"), ("explainer", "write"), ("need", "read"), ("assistant", "write"), ("vision", "vision")):
            with pytest.raises(llm.QuotaExhausted):
                await _chat(agent, job)
        with pytest.raises(llm.QuotaExhausted):
            await llm.embed(["متن"], agent="embed")
        assert len(asked) == 1, "six more calls, no more requests: no useless retries"
        assert len(await _ledger(world)) == 1, "a call refused at the door is not a call, and leaves no ledger row"

    async def test_it_is_a_budget_error_so_every_caller_that_already_stops_on_one_stops(self, world, monkeypatch):
        _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        with pytest.raises(llm.BudgetExceeded):
            await _chat()

    async def test_the_message_is_persian_stable_and_names_the_hour_and_the_way_out(self, world, monkeypatch):
        _freeze(monkeypatch, "2026-09-28T12:00:00")
        _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        with pytest.raises(llm.QuotaExhausted) as first:
            await _chat()
        with pytest.raises(llm.QuotaExhausted) as again:
            await _chat("reader", "read")
        assert str(first.value) == str(again.value), "one sentence for the whole pause — a log that dedupes on it stays quiet"
        text = str(first.value)
        assert "اعتبار" in text and "00:00" in text and "تست" in text and "402" in text
        assert GATEWAY_WORDS not in text, "the gateway's own words are for the admin's card and the log, not every caller's message"

    async def test_the_breaker_is_for_the_road_and_is_left_alone(self, world, monkeypatch):
        _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        for _ in range(3):
            with pytest.raises(llm.QuotaExhausted):
                await _chat()
        assert llm._breaker.state == "closed" and llm._breaker.fails == 0

    async def test_the_day_turning_lifts_it_and_the_next_call_goes_out(self, world, monkeypatch):
        _freeze(monkeypatch, "2026-09-28T12:00:00")
        asked = _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        with pytest.raises(llm.QuotaExhausted):
            await _chat()
        _freeze(monkeypatch, "2026-09-28T20:29:00")
        with pytest.raises(llm.QuotaExhausted):
            await _chat()
        assert len(asked) == 1, "a minute before midnight it still holds"
        _gateway(monkeypatch, lambda r: _answer("سلام"))
        _freeze(monkeypatch, "2026-09-28T20:31:00")
        out = await _chat("explainer", "write")
        assert out["content"] == "سلام", "past Tehran midnight nobody had to press anything"
        async with world() as db:
            assert (await llm.config(db))["pause"] is None

    async def test_a_new_day_with_no_credit_costs_one_call_and_pauses_again(self, world, monkeypatch):
        _freeze(monkeypatch, "2026-09-28T12:00:00")
        asked = _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        with pytest.raises(llm.QuotaExhausted):
            await _chat()
        _freeze(monkeypatch, "2026-09-28T21:00:00")      # 00:30 the next day in Tehran
        for _ in range(4):
            with pytest.raises(llm.QuotaExhausted):
                await _chat()
        assert len(asked) == 2, "one probe after midnight, then quiet again"
        assert json.loads(await _setting(world, llm.KEY_PAUSE))["until"] == "2026-09-29T20:30:00+00:00"

    async def test_two_refusals_in_one_day_keep_when_it_began_and_the_newest_words(self, world, monkeypatch):
        _freeze(monkeypatch, "2026-09-28T06:00:00")
        _gateway(monkeypatch, lambda r: _refuse(402, {"error": {"message": "first words"}}))
        with pytest.raises(llm.QuotaExhausted):
            await _chat()
        began = json.loads(await _setting(world, llm.KEY_PAUSE))["since"]
        _freeze(monkeypatch, "2026-09-28T09:00:00")
        _gateway(monkeypatch, lambda r: _refuse(402, {"error": {"message": "newer words"}}))
        with pytest.raises(llm.QuotaExhausted):     # the card's test goes out even while paused
            await _chat("test", "write", cap=False)
        stored = json.loads(await _setting(world, llm.KEY_PAUSE))
        assert stored["message"] == "newer words" and stored["since"] == began

    async def test_it_is_logged_once_however_many_calls_meet_it(self, world, monkeypatch, logs):
        _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        for _ in range(12):
            with pytest.raises(llm.QuotaExhausted):
                await _chat()
        told = [line for line in logs if line.startswith("WARNING")]
        assert len(told) == 1, told
        assert GATEWAY_WORDS in told[0] and "402" in told[0], "the exact words and the status are in the log"
        assert "vision" in told[0], "and who met it first"

    async def test_a_second_pause_on_the_same_day_is_news_again(self, world, monkeypatch, logs):
        """Topped up at noon, empty again by evening: the process must not
        think it has told this story already because the day is the same."""
        _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        with pytest.raises(llm.QuotaExhausted):
            await _chat()
        async with world() as db:
            assert await llm.clear_pause(db) is True
        with pytest.raises(llm.QuotaExhausted):
            await _chat()
        assert len([line for line in logs if line.startswith("WARNING")]) == 2

    async def test_what_is_stored_and_logged_is_scrubbed_like_everything_else(self, world, monkeypatch, logs):
        _gateway(monkeypatch, lambda r: _refuse(402, {"error": {"message": "Call 09143495300 to recharge"}}))
        with pytest.raises(llm.QuotaExhausted):
            await _chat()
        assert "09143495300" not in await _setting(world, llm.KEY_PAUSE)
        assert "09143495300" not in " ".join(logs)

    async def test_embed_refused_for_credit_pauses_too(self, world, monkeypatch):
        asked = _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        with pytest.raises(llm.QuotaExhausted):
            await llm.embed(["متن"], agent="embed")
        with pytest.raises(llm.QuotaExhausted):
            await _chat("reader", "read")
        assert len(asked) == 1

    async def test_switched_off_still_says_switched_off(self, world, monkeypatch):
        _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        with pytest.raises(llm.QuotaExhausted):
            await _chat()
        async with world() as db:
            await secret_box.put(db, llm.KEY_ENABLED, "false", "t")
        with pytest.raises(llm.Disabled):
            await _chat()

    async def test_a_pause_nobody_can_read_is_no_pause(self, world, monkeypatch):
        async with world() as db:
            await secret_box.put(db, llm.KEY_PAUSE, "{not json", "t")
        asked = _gateway(monkeypatch, lambda r: _answer("ok"))
        assert (await _chat("explainer", "write"))["content"] == "ok" and len(asked) == 1


class TestTheCardsTestAndThePause:

    async def test_the_test_goes_out_while_paused_and_an_answer_lifts_the_pause(self, world, monkeypatch, logs):
        await _pause_now(monkeypatch)
        asked = _gateway(monkeypatch, lambda r: _answer("سلام سورین"))
        async with world() as db:
            out = await llm.test_connection(db)
        assert out["ok"] and out["reply"] == "سلام سورین" and len(asked) == 1
        assert out["pause_cleared"] is True
        assert await _setting(world, llm.KEY_PAUSE) is None
        assert any("lifted" in line for line in logs)
        # and the agents are back, before any midnight
        assert (await _chat("reader", "read"))["content"] == "سلام سورین"
        assert len(asked) == 2

    async def test_a_test_with_nothing_to_lift_says_so(self, world, monkeypatch):
        _gateway(monkeypatch, lambda r: _answer("سلام"))
        async with world() as db:
            assert (await llm.test_connection(db))["pause_cleared"] is False

    async def test_a_test_that_is_refused_for_credit_leaves_the_pause_in_place(self, world, monkeypatch):
        await _pause_now(monkeypatch)
        _gateway(monkeypatch, lambda r: _refuse(402, {"error": {"message": "still empty"}}))
        async with world() as db:
            with pytest.raises(llm.QuotaExhausted) as e:
                await llm.test_connection(db)
        assert e.value.gateway_message == "still empty"
        assert json.loads(await _setting(world, llm.KEY_PAUSE))["message"] == "still empty"
        with pytest.raises(llm.QuotaExhausted):
            await _chat("reader", "read")

    async def test_a_test_that_fails_some_other_way_does_not_lift_it(self, world, monkeypatch):
        await _pause_now(monkeypatch)
        _gateway(monkeypatch, lambda r: _refuse(401, "Unauthorized"))
        async with world() as db:
            with pytest.raises(llm.LLMError) as e:
                await llm.test_connection(db)
        assert not isinstance(e.value, llm.QuotaExhausted)
        assert await _setting(world, llm.KEY_PAUSE) is not None


class TestEveryPodSeesTheSamePause:
    """The breaker keeps its state in one process's memory (see the note above
    _Breaker). The pause must not: the API pods, the worker and the scheduler
    each run their own agents, and one that has met the refusal must stop them
    all."""

    async def test_a_pause_set_by_one_pod_stops_another_and_a_test_on_the_other_lifts_it_for_both(self, monkeypatch, tmp_path):
        _configure(monkeypatch)
        url = f"sqlite+aiosqlite:///{tmp_path / 'shared.db'}"
        pods = [create_async_engine(url, poolclass=NullPool) for _ in range(2)]
        async with pods[0].begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[AppSetting.__table__, AiUsage.__table__]))
        makers = [async_sessionmaker(p, expire_on_commit=False) for p in pods]

        # pod A meets the refusal
        monkeypatch.setattr("app.database.async_session_maker", makers[0])
        _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        with pytest.raises(llm.QuotaExhausted):
            await _chat("vision", "vision")

        # pod B: its own memory is empty, its own breaker is closed
        llm._breaker = llm._Breaker()
        _forget_what_was_told()
        monkeypatch.setattr("app.database.async_session_maker", makers[1])
        asked_b = _gateway(monkeypatch, lambda r: _answer("سلام"))
        with pytest.raises(llm.QuotaExhausted):
            await _chat("reader", "read")
        assert asked_b == [], "pod B never asked"
        async with makers[1]() as db:
            seen = (await llm.config(db))["pause"]
            assert seen and seen["message"] == GATEWAY_WORDS
            assert (await llm.pause_state(db))["status"] == 402

        # the card's test, answered on pod B, lifts it for pod A too
        async with makers[1]() as db:
            assert (await llm.test_connection(db))["pause_cleared"] is True
        async with makers[0]() as db:
            assert await llm.pause_state(db) is None
        for p in pods:
            await p.dispose()


class TestTheLedgerRow:

    async def test_a_refused_call_leaves_one_row_with_the_reason(self, world, monkeypatch):
        _gateway(monkeypatch, lambda r: _refuse(402, OUT_OF_CREDIT))
        with pytest.raises(llm.QuotaExhausted):
            await _chat("reader", "read")
        [row] = await _ledger(world)
        assert row.ok is False and row.agent == "reader" and GATEWAY_WORDS in row.error and row.error.startswith("HTTP 402: ")
        assert row.cost_usd == 0

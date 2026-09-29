"""
Reading Divar's filter form live, once a day, and saying when it changed (#27).

Divar is replaced by httpx.MockTransport serving pages shaped like its own —
window.__PRELOADED_STATE__ with nb.filtersPage.widgetList — so nothing here
reaches divar.ir. The widgets' exact nesting on the real site was not
available to this repository; the parser takes any widget with field.key /
field.type, which is what the issue records.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_filter_live.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import fakeredis  # noqa: E402
import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402

from app import database  # noqa: E402
from app.services import divar_count as dc  # noqa: E402
from app.services import divar_filters as df  # noqa: E402


def widget(key, ftype, title, options=None, token="apartment-sell"):
    w = {"widget_type": "FILTER", "cache_key": token, "title": title,
         "field": {"key": key, "type": ftype}}
    if options:
        w["data"] = {"options": [{"value": v, "title": t} for v, t in options]}
    return w


def page_html(widgets):
    state = {"nb": {"filtersPage": {"widgetList": widgets}}}
    return ("<html><head></head><body><script>window.__PRELOADED_STATE__ = "
            + json.dumps(state, ensure_ascii=False) + ";</script></body></html>")


def committed_page(slug):
    """What Divar would show for `slug` if it matched the committed schema."""
    entry = df.committed()["categories"][slug]
    return page_html([widget(f["key"], f["type"], f["title"],
                             [(o["value"], o["title"]) for o in f.get("options") or []],
                             token=entry["token"])
                      for f in entry["filters"]])


@pytest.fixture
def redis(monkeypatch):
    server = fakeredis.FakeServer()

    async def _get():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(database, "get_redis", _get)
    df.forget()
    yield _get
    df.forget()


class Divar:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def client(self):
        def answer(request):
            slug = request.url.path.rstrip("/").split("/")[-1]
            self.asked.append(slug)
            page = self.pages.get(slug)
            if page is None:
                return httpx.Response(503, text="down")
            return httpx.Response(200, text=page)
        return httpx.AsyncClient(transport=httpx.MockTransport(answer))


class Sleeps(list):
    async def __call__(self, seconds):
        self.append(seconds)


class TestReadingThePage:
    def test_the_filters_and_the_internal_name(self):
        rows, name = df.filters_from_page(committed_page("buy-apartment"))
        assert name == "apartment-sell"
        keys = {r["key"] for r in rows}
        assert {"price", "rooms", "deed_type", "recent_ads"} <= keys
        rooms = next(r for r in rows if r["key"] == "rooms")
        assert rooms["type"] == "repeated_string"
        assert [o["value"] for o in rooms["options"]][:2] == ["بدون اتاق", "یک"]

    def test_a_page_without_the_state_gives_nothing(self):
        assert df.filters_from_page("<html>captcha</html>") == ([], None)

    def test_options_the_committed_file_lacks_come_from_the_page(self):
        live = {"buy-apartment": [{"key": "cooling_system", "type": "repeated_string",
                                   "title": None, "options": [{"value": "split", "title": "اسپلیت"}]}]}
        merged = df.merge_live(live)
        f = df.filters_for("buy-apartment", merged)["cooling_system"]
        assert f["title"] == "سرمایش" and df.options_known(f)


class TestTheDailyRead:

    async def test_pages_are_read_fifteen_seconds_apart(self, redis):
        divar, sleeps = Divar({s: committed_page(s) for s in ("buy-apartment", "rent-store")}), Sleeps()
        rec = await df.refresh(force=True, slugs=["buy-apartment", "rent-store"],
                               client=divar.client(), sleep=sleeps)
        assert divar.asked == ["buy-apartment", "rent-store"]
        assert sleeps == [15.0]
        assert rec["source"] == "live" and rec["changes"] == [] and rec["failed"] == []

    async def test_a_read_younger_than_a_day_is_not_repeated(self, redis):
        divar = Divar({"rent-store": committed_page("rent-store")})
        await df.refresh(force=True, slugs=["rent-store"], client=divar.client(), sleep=Sleeps())
        await df.refresh(slugs=["rent-store"], client=divar.client(), sleep=Sleeps())
        assert divar.asked == ["rent-store"]

    async def test_a_change_is_recorded_and_used(self, redis):
        """Divar added rooms to «اجاره مغازه»: the monitoring card says so, and
        the next plan sends it."""
        entry = df.committed()["categories"]["rent-store"]
        widgets = [widget(f["key"], f["type"], f["title"], token="shop-rent") for f in entry["filters"]
                   if f["key"] != "credit"]
        widgets.append(widget("rooms", "repeated_string", "تعداد اتاق",
                              [("یک", "یک"), ("دو", "دو")], token="shop-rent"))
        divar = Divar({"rent-store": page_html(widgets)})
        rec = await df.refresh(force=True, slugs=["rent-store"], client=divar.client(), sleep=Sleeps())
        assert any("rent-store" in c and "rooms" in c and "تازه" in c for c in rec["changes"])
        assert any("credit" in c and "دیگر نیست" in c for c in rec["changes"])
        assert (await df.state())["changes"] == rec["changes"]

        schema = await df.current()
        assert schema["source"] == "live"
        form = dc.build_form_data("rent-store", schema=schema, min_rooms=1, max_rooms=2,
                                  max_deposit=5)
        assert form["rooms"] == {"repeated_string": {"value": ["یک", "دو"]}}
        assert "credit" not in form

    async def test_divar_not_answering_leaves_the_committed_schema(self, redis):
        divar = Divar({})
        rec = await df.refresh(force=True, slugs=["buy-apartment", "rent-store"],
                               client=divar.client(), sleep=Sleeps())
        assert rec["source"] == "committed" and rec["failed"] == ["buy-apartment", "rent-store"]
        schema = await df.current()
        assert schema.get("source") == "issue-27-table"
        assert "credit" in df.filters_for("rent-store", schema)

    async def test_a_page_that_fails_keeps_its_committed_entry(self, redis):
        divar = Divar({"buy-apartment": committed_page("buy-apartment")})
        rec = await df.refresh(force=True, slugs=["buy-apartment", "rent-store"],
                               client=divar.client(), sleep=Sleeps())
        assert rec["failed"] == ["rent-store"] and rec["source"] == "live"
        schema = await df.current()
        assert set(df.filters_for("rent-store", schema)) == set(
            df.filters_for("rent-store", df.committed()))

    async def test_redis_down_is_the_committed_schema(self, monkeypatch):
        async def _down():
            raise ConnectionError("redis is down")
        monkeypatch.setattr(database, "get_redis", _down)
        df.forget()
        schema = await df.current()
        df.forget()
        assert schema["categories"]["buy-apartment"]["token"] == "apartment-sell"
        st = await df.state()
        assert st["source"] == "committed" and st["error"]


class TestTheRuntimeCardShowsIt:

    async def test_the_runtime_answer_carries_the_schema_state(self, redis):
        from app.api.routes import monitoring
        entry = df.committed()["categories"]["rent-store"]
        widgets = [widget(f["key"], f["type"], f["title"], token="shop-rent")
                   for f in entry["filters"] if f["key"] != "rent"]
        await df.refresh(force=True, slugs=["rent-store"],
                         client=Divar({"rent-store": page_html(widgets)}).client(), sleep=Sleeps())
        d = await monitoring.runtime()
        st = d["divar_filter_schema"]
        assert st["source"] == "live" and st["fetched_at"]
        assert any("rent" in c and "دیگر نیست" in c for c in st["changes"])

    async def test_nothing_read_yet_says_committed(self, redis):
        from app.api.routes import monitoring
        st = (await monitoring.runtime())["divar_filter_schema"]
        assert st["source"] == "committed" and st["changes"] == []
        assert st["committed_read_on"] == "1405-07-07"


class TestTheLoop:
    def test_it_is_a_periodic_loop_and_can_be_switched_off(self):
        import app.main as main
        loops = {name: (fn, roles) for name, fn, _stall, roles in main._loops()}
        fn, roles = loops["divar_filters"]
        assert fn is df.refresh_loop and "scheduler" in roles and "api" not in roles

    async def test_zero_hours_disables_it(self, monkeypatch):
        from app.config import get_settings
        monkeypatch.setattr(get_settings(), "divar_filter_schema_hours", 0)
        await df.refresh_loop()          # returns at once instead of looping

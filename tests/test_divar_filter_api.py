"""
The estimate and the category list speak the category's own filters (#27).

«چند آگهی با این فیلترها هست؟» used to ask Divar with a deposit on a sale —
Divar counted it anyway — and say nothing. It now asks with exactly the form
a run would search with, and says in Persian what it left out. The category
list carries each category's filters, which is what the scrape form is built
from. Divar itself is never asked here: fetch_post_count is replaced.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_filter_api.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.api.routes import scraper as routes  # noqa: E402
from app.services import divar_count as dc  # noqa: E402
from app.services import divar_filters as df  # noqa: E402


@pytest.fixture
def asked(monkeypatch):
    """What the estimate asked Divar, and a count of 342."""
    forms = []

    async def fetch(city, form):
        forms.append((city, form))
        return 342, None
    monkeypatch.setattr(dc, "fetch_post_count", fetch)
    df.forget()
    yield forms
    df.forget()


def _q(**kw):
    """The route's defaults, as FastAPI would fill them."""
    base = {name: None for name in routes.estimate_matching_posts.__code__.co_varnames[
        :routes.estimate_matching_posts.__code__.co_argcount]}
    base.update(kw)
    return base


class TestTheEstimate:

    async def test_a_deposit_on_a_sale_is_not_asked_and_is_named(self, asked):
        r = await routes.estimate_matching_posts(**_q(
            city="urmia", category="buy-apartment", min_deposit=100_000_000, has_parking=True))
        _, form = asked[-1]
        assert "credit" not in form and form["parking"] == {"boolean": {"value": True}}
        assert r["count"] == 342
        assert r["not_applied"] == ["ودیعه"]
        assert "ودیعه برای دستهٔ «خرید آپارتمان» معنا ندارد و اعمال نشد" in r["notes"]
        assert "credit" not in r["applied_by_divar"] and "parking" in r["applied_by_divar"]

    async def test_what_the_scraper_checks_itself_is_named(self, asked):
        r = await routes.estimate_matching_posts(**_q(
            city="urmia", category="rent-residential", has_elevator=True, min_rooms=2))
        assert r["applied_after_scrape"] == ["آسانسور"]
        assert "rooms" in asked[-1][1]

    async def test_a_publish_date_narrows_the_count_too(self, asked):
        today = dc.tehran_today().isoformat()
        r = await routes.estimate_matching_posts(**_q(city="urmia", category="rent-apartment",
                                                      posted_date=today))
        assert asked[-1][1]["recent_ads"] == {"str": {"value": "1d"}}
        assert r["recent_ads"] == "1d"

    async def test_divars_other_filters_come_as_json(self, asked):
        await routes.estimate_matching_posts(**_q(
            city="urmia", category="buy-apartment",
            divar_filters=json.dumps({"building-age": {"max": 5}, "deed_type": ["single_page"]})))
        form = asked[-1][1]
        assert form["building-age"] == {"number_range": {"maximum": "5"}}
        assert form["deed_type"] == {"repeated_string": {"value": ["single_page"]}}

    async def test_unreadable_json_is_a_422_in_persian(self, asked):
        with pytest.raises(HTTPException) as e:
            await routes.estimate_matching_posts(**_q(city="urmia", category="buy-apartment",
                                                      divar_filters="{not json"))
        assert e.value.status_code == 422 and "خوانده نشد" in e.value.detail
        assert asked == []

    async def test_balcony_is_asked_now(self, asked):
        """The route had no has_balcony at all: the panel's box was dropped."""
        await routes.estimate_matching_posts(**_q(city="urmia", category="buy-apartment",
                                                  has_balcony=True))
        assert asked[-1][1]["balcony"] == {"boolean": {"value": True}}


class TestTheCategoryList:

    async def test_every_category_carries_its_filters(self):
        df.forget()
        cats = {c["slug"]: c for c in await routes.get_available_categories()}
        assert set(cats) == set(routes.CATEGORIES)
        keys = [f["key"] for f in cats["buy-apartment"]["filters"]]
        assert "price" in keys and "credit" not in keys and "deed_type" in keys
        assert keys.index("price") < keys.index("size") < keys.index("has-photo")
        assert {f["key"] for f in cats["real-estate-services"]["filters"]} == {
            "has-photo", "has-video", "business-type", "recent_ads"}

    async def test_a_filter_row_has_what_the_form_needs(self):
        df.forget()
        cats = {c["slug"]: c for c in await routes.get_available_categories()}
        f = {x["key"]: x for x in cats["rent-apartment"]["filters"]}
        assert f["credit"] == {"key": "credit", "type": "number_range", "title": "ودیعه",
                               "options": None, "unit": "تومان"}
        assert [o["value"] for o in f["rooms"]["options"]][0] == "بدون اتاق"
        assert f["cooling_system"]["options"] is None        # not known yet: not offered
        assert cats["rent-apartment"]["family"] == "rent"
        assert cats["rent-temporary"]["family"] == "temporary"

    async def test_the_old_fields_are_still_there(self):
        """The properties, CRM and jobs pickers read slug, name and type."""
        df.forget()
        row = (await routes.get_available_categories())[0]
        assert {"slug", "name", "type"} <= set(row)

"""
«ملک‌های مشابه» — the same street first, at about the same price.

The old picker scored the first 300 rows of the city, whatever they were,
with a ±30% price curve: a lead on بلوار سعدی got offered the other side of
Urmia at twice the price. Now the pool is the listings inside the price
fence, and the ranking is district-first.
"""
import os
import sys
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_sim.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import match_service as m   # noqa: E402


def P(id, district, price, *, area=80, rooms=2, listing_type="rent", family="آپارتمان", **kw):
    base = dict(id=id, district=district, neighborhood=None, city_name="ارومیه", listing_type=listing_type,
                deposit=price if listing_type == "rent" else None, rent_price=None,
                total_price=price if listing_type == "buy" else None, price=None,
                area=area, rooms=rooms, property_type=family, category_name=None, title=f"{family} {district}",
                has_elevator=False, has_parking=False, has_storage=False, has_balcony=False,
                serial_no=id, thumbnail_url=None, url="#", phone_number=None, is_active=True)
    base.update(kw)
    return SimpleNamespace(**base)


class TestDistrictKey:

    def test_road_words_and_letter_variants_do_not_split_a_district(self):
        assert m.district_key("خ گلها") == m.district_key("خیابان گلها")
        assert m.district_key("گلهاي") == m.district_key("گلهای")   # Arabic yeh
        assert m.district_key("بلوار سعدی") == m.district_key("سعدی")
        assert m.district_key("خ شهید سرخوش") == "سرخوش"

    def test_different_streets_stay_different(self):
        assert m.district_key("بلوار سعدی") != m.district_key("بلوار امین")

    def test_empty_stays_empty(self):
        assert m.district_key(None) == "" and m.district_key("خیابان") == ""


class TestRanking:

    def test_same_street_at_the_same_price_comes_first(self):
        target = P(1, "بلوار سعدی", 100)
        cands = [
            P(2, "خ گلها", 100),          # other district, same price
            P(3, "بلوار سعدی", 112),      # same street, +12%
            P(4, "سعدی", 95),             # same street spelt short, -5%
            P(5, "بلوار سعدی", 130),      # same street, +30% — wide band
        ]
        out = m.rank_similar(target, cands, limit=10)
        assert [r["id"] for r in out][:2] == [4, 3], "same street, closest price first"
        assert all(r["same_district"] for r in out[:2])
        assert out[0]["price_gap_pct"] == 5 and out[0]["price_direction"] == "lower"

    def test_the_wide_band_is_hidden_when_the_tight_one_has_enough(self):
        target = P(1, "بلوار سعدی", 100)
        cands = [P(i, "بلوار سعدی", 100 + i) for i in range(2, 7)] + [P(9, "بلوار سعدی", 130)]
        out = m.rank_similar(target, cands, limit=10)
        assert 9 not in [r["id"] for r in out], "a +30% listing is not «the same price» when five close ones exist"

    def test_the_wide_band_is_shown_when_there_is_little_else(self):
        target = P(1, "بلوار سعدی", 100)
        cands = [P(2, "بلوار سعدی", 130), P(3, "خ گلها", 128)]
        out = m.rank_similar(target, cands, limit=10)
        assert [r["id"] for r in out] == [2, 3]

    def test_beyond_the_fence_is_never_offered(self):
        target = P(1, "بلوار سعدی", 100)
        cands = [P(2, "بلوار سعدی", 150), P(3, "بلوار سعدی", 60)]
        assert m.rank_similar(target, cands, limit=10) == []

    def test_other_districts_come_after_this_one_even_at_a_closer_price(self):
        target = P(1, "بلوار سعدی", 100)
        cands = [P(2, "خ گلها", 100), P(3, "بلوار سعدی", 110)]
        out = m.rank_similar(target, cands, limit=10)
        assert [r["id"] for r in out] == [3, 2]
        assert out[1]["same_district"] is False

    def test_a_shop_is_not_offered_for_an_apartment(self):
        target = P(1, "بلوار سعدی", 100)
        cands = [P(2, "بلوار سعدی", 100, family="مغازه")]
        assert m.rank_similar(target, cands, limit=10) == []

    def test_a_listing_with_no_price_is_ranked_last_not_dropped(self):
        target = P(1, "بلوار سعدی", 100)
        cands = [P(2, "بلوار سعدی", None), P(3, "بلوار سعدی", 101)]
        out = m.rank_similar(target, cands, limit=10)
        assert [r["id"] for r in out] == [3, 2] and out[1]["price_gap_pct"] is None


class TestTheQueryAndThePanel:

    def test_the_price_fence_is_in_the_query_not_after_the_first_300_rows(self):
        src = Path("app/services/match_service.py").read_text(encoding="utf-8")
        fn = src[src.index("async def similar_to_property"):src.index("async def matches_for_customer")]
        assert "PRICE_BAND_WIDE" in fn and ".between(lo, hi)" in fn
        assert "q.limit(SIMILAR_POOL)" in fn and "CANDIDATE_POOL" not in fn

    def test_the_modal_groups_by_district_and_names_the_gap(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert "function _matchGroups" in js
        fn = js[js.index("function _matchGroups"):js.index("function _matchCard")]
        assert "m.same_district" in fn and "مناطق دیگر" in fn
        assert "price_gap_pct" in js and "گران‌تر" in js and "ارزان‌تر" in js
        crm = Path("app/api/routes/crm.py").read_text(encoding="utf-8")
        assert crm.count('"district": prop.district, "city_name": prop.city_name') == 3

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

    def test_a_rental_is_compared_on_deposit_plus_converted_rent(self):
        # 350/10 against 350/25: the deposits match, the price does not
        target = P(1, "بلوار سعدی", 350_000_000, rent_price=10_000_000)
        cands = [P(2, "بلوار سعدی", 350_000_000, rent_price=25_000_000),   # same deposit, 2.5× the rent
                 P(3, "بلوار سعدی", 300_000_000, rent_price=12_000_000)]   # a real substitute
        out = m.rank_similar(target, cands, limit=10)
        assert [r["id"] for r in out] == [3]
        assert out[0]["price_gap_pct"] <= 5
        assert m.RENT_TO_DEPOSIT == 30, "the market's own «تبدیل» rate"

    def test_a_listing_with_no_price_is_ranked_last_not_dropped(self):
        target = P(1, "بلوار سعدی", 100)
        cands = [P(2, "بلوار سعدی", None), P(3, "بلوار سعدی", 101)]
        out = m.rank_similar(target, cands, limit=10)
        assert [r["id"] for r in out] == [3, 2] and out[1]["price_gap_pct"] is None


class TestTheQueryAndThePanel:

    def test_the_price_fence_is_in_the_query_not_after_the_first_300_rows(self):
        src = Path("app/services/match_service.py").read_text(encoding="utf-8")
        fn = src[src.index("async def similar_to_property"):src.index("async def matches_for_customer")]
        assert "PRICE_BAND_WIDE" in fn and "_comparable_sql(prop.listing_type).between(lo, hi)" in fn
        assert "q.limit(SIMILAR_POOL)" in fn and "CANDIDATE_POOL" not in fn
        sql = src[src.index("def _comparable_sql"):src.index("def rank_similar")]
        assert "func.coalesce(Property.rent_price, 0) * RENT_TO_DEPOSIT" in sql

    def test_the_modal_groups_by_district_and_names_the_gap(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert "function _matchGroups" in js
        fn = js[js.index("function _matchGroups"):js.index("function _matchCard")]
        assert "m.same_district" in fn and "مناطق دیگر" in fn
        assert "price_gap_pct" in js and "گران‌تر" in js and "ارزان‌تر" in js
        crm = Path("app/api/routes/crm.py").read_text(encoding="utf-8")
        assert crm.count('"source": _match_source(prop)') == 3, \
            "property, lead and the reverse-direction (customers-for-a-property) endpoint all use it"
        assert crm.count('"reasons_pending": pending') == 3, \
            "property, lead and customer matches; the reverse direction never calls the model"
        src = crm[crm.index("def _match_source"):crm.index('@router.get("/match/lead/{lead_id}")')]
        assert '"comparable": _comparable(prop)' in src and '"deposit": prop.deposit' in src


class TestARentalIsAShapeAsWellAsATotal:
    """The lead was 150M down and 40M a month; the modal offered 1.5B down
    and nothing a month as «9% گران‌تر» — and then scored it 9% with «اختلاف
    قیمت» and «خارج از محدوده», because the scorer compared deposits while
    the tier compared totals. One figure now, and the deposit's own shape
    beside it."""

    def test_the_score_and_the_gap_agree_on_the_figure(self):
        target = P(1, "بلوار سعدی", 150_000_000, rent_price=40_000_000, area=300)     # total 1.35B
        full = P(2, "بلوار سعدی", 1_350_000_000, rent_price=None, area=300)           # the same total, all deposit
        s = m.score_similarity(target, full)
        assert "قیمت نزدیک" in s["reasons"] and "خارج از محدوده قیمت" not in s["reasons"]

    def test_the_same_total_in_another_shape_ranks_below_the_same_shape(self):
        target = P(1, "بلوار سعدی", 150_000_000, rent_price=40_000_000, area=300)
        same_shape = P(2, "بلوار سعدی", 170_000_000, rent_price=38_000_000, area=300)   # 1.31B
        full = P(3, "بلوار سعدی", 1_350_000_000, rent_price=None, area=300)             # 1.35B, all deposit
        out = m.rank_similar(target, [full, same_shape], limit=10)
        assert [r["id"] for r in out] == [2, 3]
        far = next(r for r in out if r["id"] == 3)
        assert "ودیعه خیلی متفاوت — تبدیل لازم" in far["reasons"]
        assert far["score"] < next(r for r in out if r["id"] == 2)["score"]

    def test_the_brief_carries_both_halves_and_the_figure_compared_on(self):
        target = P(1, "بلوار سعدی", 150_000_000, rent_price=40_000_000)
        cand = P(2, "بلوار سعدی", 200_000_000, rent_price=35_000_000)
        b = m.rank_similar(target, [cand], limit=1)[0]
        assert (b["deposit"], b["rent_price"], b["comparable"]) == (200_000_000, 35_000_000, 1_250_000_000)
        sale = P(3, "بلوار سعدی", 4_000_000_000, listing_type="buy")
        b = m.rank_similar(sale, [P(4, "بلوار سعدی", 4_100_000_000, listing_type="buy")], limit=1)[0]
        assert b["deposit"] is None and b["rent_price"] is None and b["comparable"] == 4_100_000_000

    def test_the_card_shows_both_halves(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        fn = js[js.index("function _matchMoney"):js.index("function _matchCard")]
        assert "ودیعه" in fn and "اجاره" in fn and "رهن کامل" in fn and "m.comparable" in fn
        modal = js[js.index("function _renderMatchModal"):js.index("function showSimilarForLead")]
        assert "s0.comparable" in modal and "اختلاف قیمت‌ها روی رهن کامل حساب می‌شود" in modal

    def test_a_modal_opened_from_a_modal_comes_out_on_top(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        blk = js[js.index("// ═══ Stacked modals"):js.index("// ═══ Stacked modals") + 1500]
        assert "show.bs.modal" in blk and "1055 + 10 * open" in blk
        assert "shown.bs.modal" in blk and ".modal-backdrop" in blk
        assert "hidden.bs.modal" in blk and "classList.add('modal-open')" in blk


class TestReasonsPendingInThePanel:
    """`ai_reason` can arrive after the modal is already open — the endpoint
    answers with the ranking at once and a `reasons_pending` flag (see
    app/services/match_service.py _attach_reasons); the panel checks back a
    few times rather than showing a spinner that waits for the model."""

    def test_a_pending_answer_schedules_a_light_recheck(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert "function _pollMatchReasons" in js and "function _renderMatchModal" in js
        openfn = js[js.index("async function _openMatchModal"):js.index("function showSimilarForLead")]
        assert "data.reasons_pending" in openfn and "_pollMatchReasons(url, modalEl, 3)" in openfn
        assert "modalEl.dataset.matchUrl = url" in openfn, "so a stale poll can tell it opened a different card"

    def test_the_recheck_is_light_and_gives_up(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        fn = js[js.index("function _pollMatchReasons"):js.index("async function _openMatchModal")]
        assert "if (triesLeft <= 0) return" in fn, "at most 3 tries, then it stops on its own"
        assert "setTimeout(" in fn and "4000)" in fn, "~4s between checks — no spinner, no tight loop"
        assert "modalEl.dataset.matchUrl !== url" in fn, "stops if a different card was opened meanwhile"
        assert "_renderMatchModal(data, null)" in fn, "shows the reasons when they arrive"
        assert "_pollMatchReasons(url, modalEl, triesLeft - 1)" in fn

    def test_every_new_value_still_goes_through_esc(self):
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        render = js[js.index("function _renderMatchModal"):js.index("function _pollMatchReasons")]
        assert "esc(src)" in render, "the same escaping as before the refactor — nothing raw was added"
        # _matchCard (unchanged by this stream) is what actually prints ai_reason
        card = js[js.index("function _matchCard"):js.index("const MATCH_TYPE_FA")]
        assert "esc(m.ai_reason)" in card

"""
The run itself, over the saved pages: every local filter keeps one listing and
drops one, an ad is never revealed and then dropped or dropped and then saved,
and the report says how many «شخصی» ads were an agency's.

test_local_filters.py pins the filter function. This is the other half: that
the scrape loop actually calls it, with the run's own settings, and that what
comes out the far end — the saved rows, the skipped list, the contact reveals
spent, the tally in the report — is what those filters say. The real
`start_scraping_job` runs over the pages in tests/fixtures/divar; only the
browser, the database session and the reveal are replaced.

The pages used and what they read as (see expected.json):

  buy-apartment-owner                     85 m², 2 rooms, 45M/m², elevator NO,
                                          parking, storage, balcony not stated
  buy-apartment-agency-arabic-digits      100 m², 3 rooms, 51M/m², elevator, parking,
                                          storage, balcony NO — an agency panel
  buy-apartment-personal-but-agency-words 70 m², 1 room, parking, balcony —
                                          «شخصی» to Divar, «املاک هستم» in its words
  buy-apartment-owner-keeps-agents-away   60 m², 1 room, storage — «مشاورین املاک
                                          تماس نگیرند», a private seller
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_rop.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import _divar_pages as dp  # noqa: E402

from app.scraper.divar_scraper import DivarScraper  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore:The 'strip_cdata' option")

OWNER = "buy-apartment-owner.html"
AGENCY = "buy-apartment-agency-arabic-digits.html"
POSING = "buy-apartment-personal-but-agency-words.html"
REFUSAL = "buy-apartment-owner-keeps-agents-away.html"
BUY_APARTMENTS = [OWNER, AGENCY, POSING, REFUSAL]

RENT_OWNER = "rent-apartment-owner.html"
RENT_AGENCY = "rent-apartment-agency-arabic-digits.html"
RENT_APARTMENTS = [RENT_OWNER, RENT_AGENCY]


def token(name):
    return dp.manifest()[name]["token"]


def _without_photos(html):
    return re.sub(r"<img\b[^>]*>", "", html)


# The refusal page stands for «an ad with no photos» in every run below: nothing
# else about it matters to the filters that look at photos, and no other filter
# here reads the photos at all.
NO_PHOTOS = {REFUSAL: _without_photos}


@pytest.fixture
def run(monkeypatch):
    return dp.make_runner(monkeypatch)


class TestEveryFilterKeepsOneAndDropsOne:
    """(the filter, the pages that stay, {the pages that go: the bucket})."""

    CASES = [
        # sale price
        pytest.param("buy-apartment", BUY_APARTMENTS, {"min_price": 4_000_000_000},
                     [AGENCY], {OWNER: "price", POSING: "price", REFUSAL: "price"}, id="min_price"),
        pytest.param("buy-apartment", BUY_APARTMENTS, {"max_price": 3_000_000_000},
                     [POSING, REFUSAL], {OWNER: "price", AGENCY: "price"}, id="max_price"),
        # price per metre
        pytest.param("buy-apartment", [OWNER, AGENCY], {"min_price_per_meter": 48_000_000},
                     [AGENCY], {OWNER: "price/m²"}, id="min_price_per_meter"),
        pytest.param("buy-apartment", [OWNER, AGENCY], {"max_price_per_meter": 48_000_000},
                     [OWNER], {AGENCY: "price/m²"}, id="max_price_per_meter"),
        # area
        pytest.param("buy-apartment", BUY_APARTMENTS, {"min_area": 80},
                     [OWNER, AGENCY], {POSING: "area", REFUSAL: "area"}, id="min_area"),
        pytest.param("buy-apartment", BUY_APARTMENTS, {"max_area": 80},
                     [POSING, REFUSAL], {OWNER: "area", AGENCY: "area"}, id="max_area"),
        # rooms
        pytest.param("buy-apartment", BUY_APARTMENTS, {"min_rooms": 2},
                     [OWNER, AGENCY], {POSING: "rooms", REFUSAL: "rooms"}, id="min_rooms"),
        pytest.param("buy-apartment", BUY_APARTMENTS, {"max_rooms": 2},
                     [OWNER, POSING, REFUSAL], {AGENCY: "rooms"}, id="max_rooms"),
        # elevator: the owner's page says «ندارد», the agency's says «دارد»
        pytest.param("buy-apartment", [OWNER, AGENCY], {"has_elevator": True},
                     [AGENCY], {OWNER: "has_elevator"}, id="elevator_required"),
        pytest.param("buy-apartment", [OWNER, AGENCY], {"has_elevator": False},
                     [OWNER], {AGENCY: "has_elevator"}, id="elevator_refused"),
        # parking: three pages have it, and one page never mentions it
        pytest.param("buy-apartment", [OWNER, REFUSAL], {"has_parking": True},
                     [OWNER], {REFUSAL: "has_parking"}, id="parking_required"),
        pytest.param("buy-apartment", [OWNER, REFUSAL], {"has_parking": False},
                     [REFUSAL], {OWNER: "has_parking"}, id="parking_refused"),
        # storage: the pages that state it
        pytest.param("buy-apartment", [OWNER, POSING], {"has_storage": True},
                     [OWNER], {POSING: "has_storage"}, id="storage_required"),
        pytest.param("buy-apartment", [OWNER, POSING], {"has_storage": False},
                     [POSING], {OWNER: "has_storage"}, id="storage_refused"),
        # balcony: one page says «بالکن ندارد», one has a balcony, one is silent
        pytest.param("buy-apartment", [AGENCY, POSING], {"has_balcony": True},
                     [POSING], {AGENCY: "has_balcony"}, id="balcony_required"),
        pytest.param("buy-apartment", [AGENCY, POSING], {"has_balcony": False},
                     [AGENCY], {POSING: "has_balcony"}, id="balcony_refused"),
        # photos: the refusal page is served with its photos taken out
        pytest.param("buy-apartment", [OWNER, REFUSAL], {"has_images": True},
                     [OWNER], {REFUSAL: "has_images"}, id="photos_required"),
        pytest.param("buy-apartment", [OWNER, REFUSAL], {"has_images": False},
                     [REFUSAL], {OWNER: "has_images"}, id="photos_refused"),
        # who posted it
        pytest.param("buy-apartment", BUY_APARTMENTS, {"advertiser_type": "personal"},
                     [OWNER, POSING, REFUSAL], {AGENCY: "advertiser_type"}, id="personal_run"),
        pytest.param("buy-apartment", BUY_APARTMENTS, {"advertiser_type": "agency"},
                     [AGENCY], {OWNER: "advertiser_type", POSING: "advertiser_type",
                                REFUSAL: "advertiser_type"}, id="agency_run"),
        # rentals: deposit and rent
        pytest.param("rent-apartment", RENT_APARTMENTS, {"min_deposit": 200_000_000},
                     [RENT_OWNER], {RENT_AGENCY: "deposit"}, id="min_deposit"),
        pytest.param("rent-apartment", RENT_APARTMENTS, {"max_deposit": 200_000_000},
                     [RENT_AGENCY], {RENT_OWNER: "deposit"}, id="max_deposit"),
        pytest.param("rent-apartment", RENT_APARTMENTS, {"min_rent": 8_000_000},
                     [RENT_OWNER], {RENT_AGENCY: "rent"}, id="min_rent"),
        pytest.param("rent-apartment", RENT_APARTMENTS, {"max_rent": 8_000_000},
                     [RENT_AGENCY], {RENT_OWNER: "rent"}, id="max_rent"),
        pytest.param("rent-apartment", RENT_APARTMENTS, {"has_parking": True},
                     [RENT_OWNER], {RENT_AGENCY: "has_parking"}, id="rent_parking_required"),
    ]

    @pytest.mark.parametrize("category,pages,filters,stays,goes", CASES)
    async def test_the_loop_applies_it(self, run, category, pages, filters, stays, goes):
        r = await run(pages, category, edit=NO_PHOTOS, **filters)
        assert r.job.status == "completed"
        assert sorted(r.saved) == sorted(token(n) for n in stays)
        assert r.dropped == {token(n): bucket for n, bucket in goes.items()}

    @pytest.mark.parametrize("category,pages,filters,stays,goes", CASES)
    async def test_and_no_reveal_is_spent_on_one_it_drops(self, run, category, pages,
                                                          filters, stays, goes):
        """A reveal costs the account an SMS. What is asked for is what is kept —
        neither more (a reveal spent and the ad dropped) nor less (an ad saved
        with no number because the pre-check dropped it and the loop did not)."""
        r = await run(pages, category, edit=NO_PHOTOS, **filters)
        assert sorted(r.revealed) == sorted(r.saved)

    @pytest.mark.parametrize("category,pages,filters,stays,goes", CASES)
    async def test_and_the_tally_names_the_filter_in_persian(self, run, category, pages,
                                                             filters, stays, goes):
        r = await run(pages, category, edit=NO_PHOTOS, **filters)
        if not goes:
            return
        tally = next(m for m in r.lines("page") if "نامزد —" in m)
        for bucket in set(goes.values()):
            assert f"{sum(1 for b in goes.values() if b == bucket)} " \
                   f"{DivarScraper._FILTER_LABELS_FA[bucket]}" in tally


class TestARunWithNoFiltersKeepsEverything:
    async def test_all_four_pages_are_saved_and_revealed(self, run):
        r = await run(BUY_APARTMENTS, "buy-apartment")
        assert sorted(r.saved) == sorted(token(n) for n in BUY_APARTMENTS)
        assert sorted(r.revealed) == sorted(r.saved)
        assert r.dropped == {}
        assert r.job.new_items == 4


class TestAFreeDepositIsAnAnswer:
    """The case where the pre-check and the loop used to disagree. «رهن کامل»:
    a deposit and a rent of «مجانی». With a minimum rent, the pre-check dropped
    it before the reveal and the loop — 0 is falsy — kept it, so it was saved
    with no phone number and counted as a failure."""

    HOUSE = "rent-residential-house-full-mortgage.html"
    FLAT = "rent-residential-apartment-owner.html"

    async def test_it_is_dropped_by_a_minimum_rent_and_costs_no_reveal(self, run):
        r = await run([self.HOUSE, self.FLAT], "rent-residential", min_rent=3_000_000)
        assert r.saved == [token(self.FLAT)]
        assert r.dropped == {token(self.HOUSE): "rent"}
        assert r.revealed == [token(self.FLAT)]
        assert r.job.failed_items == 0

    async def test_it_stays_under_a_maximum_rent(self, run):
        r = await run([self.HOUSE, self.FLAT], "rent-residential", max_rent=3_000_000)
        assert r.saved == [token(self.HOUSE)]
        assert r.dropped == {token(self.FLAT): "rent"}
        assert r.revealed == [token(self.HOUSE)]


class TestTheListingTypeDecidesWhichBandsApply:
    async def test_a_sale_band_does_not_touch_a_rental(self, run):
        r = await run(RENT_APARTMENTS, "rent-apartment", min_price=5_000_000_000)
        assert sorted(r.saved) == sorted(token(n) for n in RENT_APARTMENTS)

    async def test_a_rental_band_does_not_touch_a_sale(self, run):
        r = await run(BUY_APARTMENTS, "buy-apartment", min_deposit=900_000_000)
        assert sorted(r.saved) == sorted(token(n) for n in BUY_APARTMENTS)


class TestTheAgencyCountInTheReport:
    """«۴ آگهی مال بنگاه بودند ولی دیوار «شخصی» نشانشان داد.» Kept, labelled —
    and counted, so the number is in the report and not only on the rows."""

    @staticmethod
    def count_lines(r):
        return r.events("در متنشان مشاور املاک بود")

    async def test_the_ad_divar_called_personal_is_counted(self, run):
        r = await run(BUY_APARTMENTS, "buy-apartment")
        found = self.count_lines(r)
        assert len(found) == 1
        assert found[0]["message"].startswith("1 آگهی با برچسب «شخصی» دیوار")
        assert found[0]["agency_looks_personal"] == 1
        assert found[0]["evidence"] == {"املاک هستم": 1}

    async def test_it_is_kept_and_carries_the_label(self, run):
        r = await run(BUY_APARTMENTS, "buy-apartment")
        row = next(p for p in r.stored if p["divar_id"] == token(POSING))
        assert row["advertiser_type"] == "personal"
        assert row["agency_suspected"] is True
        assert row["agency_evidence"] == "املاک هستم"

    async def test_an_owner_keeping_agents_away_is_not_counted(self, run):
        r = await run([REFUSAL], "buy-apartment")
        assert r.saved == [token(REFUSAL)]
        assert self.count_lines(r) == []
        row = r.stored[0]
        assert row["agency_suspected"] is False and row["agency_evidence"] is None

    async def test_an_agency_that_says_so_is_not_counted(self, run):
        """Divar and the words agree; there is nothing to report."""
        r = await run([AGENCY], "buy-apartment")
        assert self.count_lines(r) == []

    async def test_a_run_with_no_such_ad_says_nothing(self, run):
        r = await run([OWNER, AGENCY], "buy-apartment")
        assert self.count_lines(r) == []

    async def test_an_ad_a_filter_drops_is_not_counted(self, run):
        """It never got as far as being saved; the count is of what the run kept."""
        r = await run(BUY_APARTMENTS, "buy-apartment", min_rooms=2)
        assert token(POSING) not in r.saved
        assert self.count_lines(r) == []

    async def test_it_survives_a_personal_only_run(self, run):
        """The very run the complaint came from: «شخصی» asked for, an agency's
        ad arrives, and it is kept but not silent."""
        r = await run(BUY_APARTMENTS, "buy-apartment", advertiser_type="personal")
        assert token(POSING) in r.saved and token(AGENCY) not in r.saved
        assert len(self.count_lines(r)) == 1

    async def test_the_count_is_written_once_per_run(self, run):
        r = await run([POSING, POSING], "buy-apartment")
        assert len(self.count_lines(r)) == 1

    async def test_it_is_information_not_a_warning(self, run):
        r = await run(BUY_APARTMENTS, "buy-apartment")
        assert self.count_lines(r)[0]["level"] == "info"
        assert self.count_lines(r)[0]["stage"] == "page"

    async def test_it_does_not_bloat_the_finish_reason(self, run):
        """finish_reason is a VARCHAR(300): an informational note has no business
        pushing a run over it."""
        r = await run(BUY_APARTMENTS, "buy-apartment")
        assert "مشاور املاک بود" not in (r.job.finish_reason or "")


class TestTheCountBelongsToOneRun:
    """A scraper is built per run in production; nothing here relies on that."""

    @staticmethod
    def scraper_on(job_id):
        s = dp.make_scraper(None)
        s.current_job = type("Job", (), {"job_id": job_id})()
        return s

    async def test_a_count_is_reported_once_and_then_forgotten(self, monkeypatch):
        lines = []

        async def event(_job_id, stage, message, level="info", **details):
            lines.append((message, details))

        from app.services import job_log
        monkeypatch.setattr(job_log, "record", event)
        s = self.scraper_on("job-a")
        s._count_agency_posing("مشاور املاک")
        s._count_agency_posing("مشاور املاک")
        s._count_agency_posing("املاک هستم")
        await s._report_agency_posing(s.current_job)
        await s._report_agency_posing(s.current_job)
        assert len(lines) == 1
        message, details = lines[0]
        assert message.startswith("3 آگهی با برچسب «شخصی» دیوار")
        assert details == {"agency_looks_personal": 3,
                           "evidence": {"مشاور املاک": 2, "املاک هستم": 1}}

    async def test_another_run_on_the_same_scraper_does_not_inherit_it(self, monkeypatch):
        lines = []

        async def event(_job_id, stage, message, level="info", **details):
            lines.append(message)

        from app.services import job_log
        monkeypatch.setattr(job_log, "record", event)
        s = self.scraper_on("job-a")
        s._count_agency_posing("املاک هستم")
        s.current_job = type("Job", (), {"job_id": "job-b"})()
        await s._report_agency_posing(s.current_job)
        assert lines == []

    async def test_nothing_counted_writes_nothing(self, monkeypatch):
        lines = []

        async def event(_job_id, stage, message, level="info", **details):
            lines.append(message)

        from app.services import job_log
        monkeypatch.setattr(job_log, "record", event)
        s = self.scraper_on("job-a")
        await s._report_agency_posing(s.current_job)
        assert lines == []

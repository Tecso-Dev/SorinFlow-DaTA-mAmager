"""
آژانس یا شخصی — advertiser-type detection and the scrape filter that uses it.

Two defects are pinned down here, both of which put agency ads in the results
of a «شخصی» scrape:

  1. «شخصی» was matched as a substring, so «پارکینگ شخصی» in an agency ad's
     prose read as an advertiser type.
  2. An undetermined type satisfied an explicit filter, so every ad whose type
     could not be read came back regardless of what was asked for.

All pure functions — no DB, no browser.
"""
import pytest

from app.scraper.parsers import (
    is_personal_value,
    decide_advertiser_type,
    looks_like_agency,
    infer_advertiser_type,
    panel_says_agency,
    agency_name_from_panel,
)


# ── the prose trap ───────────────────────────────────────────────────────────

class TestIsPersonalValue:
    """«شخصی» is an advertiser type only when it is the whole cell."""

    @pytest.mark.parametrize("value", [
        "شخصی", " شخصی ", "شخصی.", "شخصی:", "شخصي", "مالک", "مالك",
    ])
    def test_the_word_itself_is_a_type(self, value):
        assert is_personal_value(value) is True

    @pytest.mark.parametrize("value", [
        "پارکینگ شخصی",      # the one that broke it
        "حیاط شخصی",
        "ورودی شخصی",
        "آسانسور شخصی",
        "سند شخصی",
        "پارکینگ شخصی و انباری",
        "دارای حیاط شخصی بزرگ",
    ])
    def test_prose_containing_the_word_is_not(self, value):
        assert is_personal_value(value) is False

    @pytest.mark.parametrize("value", ["", "   ", None, "مشاور املاک", "آژانس"])
    def test_empty_and_agency_values(self, value):
        assert is_personal_value(value) is False

    def test_zwnj_and_whitespace_collapse(self):
        assert is_personal_value("شخصی‌") is True
        assert is_personal_value("شخصی\n") is True


# ── reading a whole ad page ──────────────────────────────────────────────────

class TestDecideAdvertiserType:

    def test_type_row_says_personal(self):
        rows = [("متراژ", "۸۵ متر"), ("نوع آگهی‌دهنده", "شخصی")]
        assert decide_advertiser_type(rows) == "personal"

    def test_type_row_says_agency(self):
        rows = [("نوع آگهی‌دهنده", "مشاور املاک")]
        assert decide_advertiser_type(rows) == "agency"

    def test_no_rows_is_unknown(self):
        assert decide_advertiser_type([]) is None
        assert decide_advertiser_type(None) is None

    def test_only_unrelated_rows_is_unknown(self):
        rows = [("متراژ", "۸۵ متر"), ("اتاق", "۲"), ("سال ساخت", "۱۳۹۵")]
        assert decide_advertiser_type(rows) is None

    def test_amenity_prose_never_reads_as_personal(self):
        """The reported bug: an agency ad mentioning پارکینگ شخصی."""
        rows = [("امکانات", "پارکینگ شخصی، انباری"), ("متراژ", "۱۲۰ متر")]
        assert decide_advertiser_type(rows) is None

    def test_amenity_prose_in_contact_block_never_reads_as_personal(self):
        rows = [("متراژ", "۱۲۰ متر")]
        contact = "این ملک دارای پارکینگ شخصی و حیاط شخصی است"
        assert decide_advertiser_type(rows, contact) is None

    def test_poster_name_gives_the_shop_away(self):
        rows = [("نام آگهی‌دهنده", "املاک آرین")]
        assert decide_advertiser_type(rows) == "agency"

    def test_agency_name_outranks_a_personal_claim(self):
        """Agencies routinely post under «شخصی»."""
        rows = [("نوع آگهی‌دهنده", "شخصی"), ("نام آگهی‌دهنده", "مهندس کریمی")]
        assert decide_advertiser_type(rows) == "agency"

    def test_real_person_with_personal_claim_stays_personal(self):
        rows = [("نوع آگهی‌دهنده", "شخصی"), ("نام آگهی‌دهنده", "رضا محمدی")]
        assert decide_advertiser_type(rows) == "personal"

    def test_agency_phrase_in_contact_block(self):
        assert decide_advertiser_type([], "مشاور املاک بهار — ارومیه") == "agency"

    @pytest.mark.parametrize("phrase", ["آژانس املاک نور", "بنگاه معاملات ملکی"])
    def test_other_agency_phrases_in_contact_block(self, phrase):
        assert decide_advertiser_type([], phrase) == "agency"

    def test_building_word_rows_are_not_agencies(self):
        """«نوع ملک: برج» describes a building, not who posted it."""
        for value in ["برج", "مجتمع مسکونی", "آپارتمان", "پروژه ونک"]:
            assert decide_advertiser_type([("نوع ملک", value)]) is None

    def test_personal_value_in_a_name_row_is_not_an_agency(self):
        rows = [("نام آگهی‌دهنده", "شخصی")]
        assert decide_advertiser_type(rows) != "agency"

    def test_zwnj_in_a_name_row(self):
        rows = [("نام آگهی‌دهنده", "مهندس‌ رضایی")]
        assert decide_advertiser_type(rows) == "agency"

    def test_rows_may_arrive_as_lists(self):
        """page.evaluate() hands back JSON arrays, not tuples."""
        assert decide_advertiser_type([["نوع آگهی‌دهنده", "شخصی"]]) == "personal"

    def test_missing_value_cell(self):
        assert decide_advertiser_type([("نوع آگهی‌دهنده", "")]) is None


# ── the name keyword list ────────────────────────────────────────────────────

class TestLooksLikeAgency:

    @pytest.mark.parametrize("name", [
        "املاک آرین", "مشاور املاک بهار", "هلدینگ ساختمانی پارس",
        "دپارتمان فروش سورین", "مهندس کریمی", "بنگاه نور", "دکتر احمدی",
        "املاک و مستغلات ایرانیان", "کارگزاری رسمی ملک", "تعاونی مسکن",
        "گروه ساختمانی آریا", "انبوه‌سازان غرب", "پیمانکاری ابنیه",
        "ARIAN REAL ESTATE", "Pars Property Group", "Tehran Homes",
    ])
    def test_business_names(self, name):
        assert looks_like_agency(name) is True

    @pytest.mark.parametrize("name", [
        "رضا محمدی", "سارا احمدی", "علی رضایی", "مریم ملک‌محمدی",
        "حسین ملکی", "احمد برادران", "محمد سراج", "فاطمه حسینی",
        "برج", "مجتمع مسکونی", "آپارتمان", "دفتر کار", "مسکونی",
    ])
    def test_people_and_buildings(self, name):
        assert looks_like_agency(name) is False

    def test_empty(self):
        assert looks_like_agency("") is False
        assert looks_like_agency(None) is False


class TestInferAdvertiserType:

    def test_explicit_agency_is_kept(self):
        assert infer_advertiser_type("رضا محمدی", "agency") == "agency"

    def test_agency_name_beats_personal_claim(self):
        assert infer_advertiser_type("مهندس کریمی", "personal") == "agency"

    def test_person_keeps_personal_claim(self):
        assert infer_advertiser_type("رضا محمدی", "personal") == "personal"

    def test_unknown_stays_unknown(self):
        assert infer_advertiser_type("سارا احمدی", None) is None


# ── the scrape filter ────────────────────────────────────────────────────────

def advertiser_filter_skips(requested, detected):
    """Mirror of the gate in DivarScraper.scrape_listings.

    An undetermined type is a miss, not a match — letting it through is what
    put agency ads in a «شخصی» scrape.
    """
    if not requested:
        return False
    if not detected:
        return True
    return detected != requested


class TestAdvertiserFilterGate:

    @pytest.mark.parametrize("detected", ["personal", "agency", None])
    def test_no_filter_keeps_everything(self, detected):
        assert advertiser_filter_skips(None, detected) is False
        assert advertiser_filter_skips("", detected) is False

    def test_personal_filter_keeps_personal(self):
        assert advertiser_filter_skips("personal", "personal") is False

    def test_personal_filter_drops_agency(self):
        assert advertiser_filter_skips("personal", "agency") is True

    def test_personal_filter_drops_unknown(self):
        """The reported bug: unknown used to satisfy the filter."""
        assert advertiser_filter_skips("personal", None) is True

    def test_agency_filter_drops_unknown(self):
        assert advertiser_filter_skips("agency", None) is True

    def test_agency_filter_keeps_agency(self):
        assert advertiser_filter_skips("agency", "agency") is False


class TestEndToEndPersonalScrape:
    """A «شخصی» scrape over a mixed page set must yield only owner-posted ads."""

    ADS = [
        ("owner, plain",        [("نوع آگهی‌دهنده", "شخصی")],                       None,                  True),
        ("owner, named",        [("نوع آگهی‌دهنده", "شخصی"),
                                 ("نام آگهی‌دهنده", "رضا محمدی")],                  None,                  True),
        ("agency, declared",    [("نوع آگهی‌دهنده", "مشاور املاک")],                None,                  False),
        ("agency posing",       [("نوع آگهی‌دهنده", "شخصی"),
                                 ("نام آگهی‌دهنده", "املاک آرین")],                 None,                  False),
        ("agency by contact",   [],                                                "مشاور املاک بهار",     False),
        ("agency w/ parking",   [("امکانات", "پارکینگ شخصی، انباری")],              "املاک نور — ارومیه",   False),
        ("unknown page",        [("متراژ", "۹۰ متر")],                              None,                  False),
        ("unknown + prose",     [("امکانات", "حیاط شخصی")],                         None,                  False),
    ]

    def test_only_personal_survives(self):
        kept = []
        for label, rows, contact, _ in self.ADS:
            detected = decide_advertiser_type(rows, contact)
            if not advertiser_filter_skips("personal", detected):
                kept.append(label)
        assert kept == ["owner, plain", "owner, named"]

    def test_expectations_match_per_ad(self):
        for label, rows, contact, should_keep in self.ADS:
            detected = decide_advertiser_type(rows, contact)
            kept = not advertiser_filter_skips("personal", detected)
            assert kept is should_keep, f"{label}: detected={detected}"

    def test_agency_scrape_never_yields_an_owner(self):
        for label, rows, contact, _ in self.ADS:
            detected = decide_advertiser_type(rows, contact)
            if not advertiser_filter_skips("agency", detected):
                assert detected == "agency", f"{label} leaked into an agency scrape"


# ── Divar's agency-panel block ───────────────────────────────────────────────

class TestAgencyPanel:
    """The block Divar renders under the map when an ad comes from an agency
    panel. Taken verbatim from real ads: an agent line, a profile link, and the
    agency's own row. This is the surest agency signal on the page."""

    # each entry is the short lines harvested off one real ad
    REAL_PANELS = [
        # «۲۵۰ متر تک واحدی ۳ خ…» — قربانی / آژانس محمد امین
        ["قربانی", "مشاور املاک | فعالیت از تیر ۱۴۰۴", "پروفایل مشاور املاک",
         "آژانس محمد امین", "همه آگهی‌ها", "اطلاعات تماس", "چت"],
        # «اجاره تک واحدی امین م…» — وحید کاتب / املاک مسکن شهر
        ["وحید کاتب", "مشاور املاک | فعالیت از فروردین ۱۴۰۴", "پروفایل مشاور املاک",
         "املاک مسکن شهر", "همه آگهی‌ها", "اطلاعات تماس"],
        # «اجاره اپارتمانی۱۸۰ متر…» — ولیزاده / مشاور املاک۱۱۸ارومیه
        ["ولیزاده", "مشاور املاک | فعالیت از مهر ۱۴۰۰", "پروفایل مشاور املاک",
         "مشاور املاک۱۱۸ارومیه", "همه آگهی‌ها"],
        # «خانه ویلایی ۱۰۳ متری» — مونا نعمت زاده / هلدینگ آراد حقوقی و املاک
        ["مونا نعمت زاده", "مشاور املاک | فعالیت از اردیبهشت ۱۴۰۵",
         "پروفایل مشاور املاک", "هلدینگ آراد حقوقی و املاک", "همه آگهی‌ها"],
        # «فروش آپارتمان 90متری» — مشاور املاک مظلومی
        ["مشاور املاک مظلومی", "مشاور املاک | فعالیت از فروردین ۱۴۰۴",
         "پروفایل مشاور املاک", "مشاور املاک", "همه آگهی‌ها"],
    ]

    @pytest.mark.parametrize("lines", REAL_PANELS)
    def test_every_real_agency_ad_is_detected(self, lines):
        assert panel_says_agency(lines) is True
        assert decide_advertiser_type([], None, lines) == "agency"

    def test_it_beats_a_personal_claim(self):
        """An agency panel outranks «نوع آگهی‌دهنده: شخصی»."""
        rows = [("نوع آگهی‌دهنده", "شخصی")]
        assert decide_advertiser_type(rows, None, self.REAL_PANELS[0]) == "agency"

    def test_the_profile_link_alone_is_enough(self):
        assert panel_says_agency(["پروفایل مشاور املاک"]) is True

    def test_the_role_needs_the_join_line(self):
        """«مشاور املاک» alone is not conclusive — it appears in ad prose."""
        assert panel_says_agency(["مشاور املاک"]) is False
        assert panel_says_agency(["مشاور املاک | فعالیت از تیر ۱۴۰۴"]) is True

    @pytest.mark.parametrize("line", [
        "مشاورین املاک تماس نگیرند",
        "لطفا مشاور املاک تماس نگیرد",
        "خواهشمندم مشاورین املاک مزاحم نشوند",
        "بدون واسطه و مشاور املاک",
    ])
    def test_an_owner_warning_off_agents_is_not_an_agency(self, line):
        """The exact thing an owner writes, and the reason prose is out of reach."""
        assert panel_says_agency([line]) is False

    def test_long_prose_is_ignored_even_if_it_says_the_words(self):
        prose = ("این ملک توسط مالک آگهی شده است و پروفایل مشاور املاک ندارد، "
                 "لطفا مشاورین املاک تماس نگیرند چون قصد فروش مستقیم داریم")
        assert len(prose) > 80
        assert panel_says_agency([prose]) is False

    def test_an_owner_posted_ad_has_no_panel(self):
        lines = ["فروش آپارتمان ۸۵ متری", "اطلاعات تماس", "چت", "ارومیه",
                 "نردبان شده", "گزارش آگهی"]
        assert panel_says_agency(lines) is False
        assert decide_advertiser_type([("نوع آگهی‌دهنده", "شخصی")], None, lines) == "personal"

    def test_empty_and_none(self):
        assert panel_says_agency([]) is False
        assert panel_says_agency(None) is False

    def test_a_personal_scrape_drops_every_one_of_them(self):
        """Both فروش and اجاره ads from the report."""
        for lines in self.REAL_PANELS:
            detected = decide_advertiser_type([], None, lines)
            assert advertiser_filter_skips("personal", detected) is True


# ── a rendered page with no agency block ─────────────────────────────────────

class TestAbsenceOfPanelMeansPersonal:
    """Divar puts its agency block on every ad posted from an agency panel, so
    on a page that plainly rendered, not having one is the answer.

    This is what makes a «شخصی» scrape return anything at all. Demanding proof
    of «شخصی» sounds safer but most ads carry no «نوع آگهی‌دهنده» row, so it
    rejected nearly every listing and the scrape looked broken.
    """

    # what a real owner-posted ad's harvest looks like
    OWNER_PAGE = ["فروش آپارتمان ۸۵ متری", "ارومیه", "اطلاعات تماس", "چت",
                  "گزارش آگهی", "نردبان", "۲ روز پیش"]
    OWNER_ROWS = [("متراژ", "۸۵ متر"), ("اتاق", "۲"), ("سال ساخت", "۱۳۹۵")]

    def test_a_rendered_page_with_no_agency_block_is_personal(self):
        assert decide_advertiser_type(self.OWNER_ROWS, None, self.OWNER_PAGE) == "personal"

    def test_it_survives_a_personal_scrape(self):
        detected = decide_advertiser_type(self.OWNER_ROWS, None, self.OWNER_PAGE)
        assert advertiser_filter_skips("personal", detected) is False

    def test_an_agency_page_still_loses_to_the_panel(self):
        lines = self.OWNER_PAGE + ["پروفایل مشاور املاک"]
        assert decide_advertiser_type(self.OWNER_ROWS, None, lines) == "agency"

    def test_a_page_that_did_not_render_stays_unknown(self):
        """The guard: without this, a failed page read would turn every agency
        ad into a personal one — the original complaint, back again."""
        assert decide_advertiser_type([], None, []) is None
        assert decide_advertiser_type([], None, ["فقط یک خط"]) is None
        assert decide_advertiser_type([("متراژ", "۸۵")], None, ["یک", "دو"]) is None

    def test_rows_alone_are_not_enough(self):
        """Spec rows without any page text means the harvest half-failed."""
        assert decide_advertiser_type(self.OWNER_ROWS, None, []) is None

    def test_an_owner_warning_agents_off_still_reads_personal(self):
        lines = self.OWNER_PAGE + ["مشاورین املاک تماس نگیرند"]
        assert decide_advertiser_type(self.OWNER_ROWS, None, lines) == "personal"

    def test_a_business_name_row_still_wins(self):
        rows = self.OWNER_ROWS + [("نام آگهی‌دهنده", "املاک آرین")]
        assert decide_advertiser_type(rows, None, self.OWNER_PAGE) == "agency"

    def test_yield_on_a_mixed_page_set(self):
        """Five agency-panel ads and three owner ads: a «شخصی» scrape keeps
        exactly the three."""
        agency = [TestAgencyPanel.REAL_PANELS[i] for i in range(5)]
        owners = [self.OWNER_PAGE, self.OWNER_PAGE + ["حیاط شخصی"],
                  self.OWNER_PAGE + ["پارکینگ شخصی"]]
        kept = 0
        for lines in agency + owners:
            det = decide_advertiser_type(self.OWNER_ROWS, None, lines)
            if not advertiser_filter_skips("personal", det):
                kept += 1
        assert kept == 3


# ── the shop's own name ──────────────────────────────────────────────────────

class TestAgencyNameFromPanel:
    """seller_name was never filled for scraped ads even though the agency's
    name is printed on the page."""

    @pytest.mark.parametrize("lines,expected", [
        (["قربانی", "مشاور املاک | فعالیت از تیر ۱۴۰۴", "پروفایل مشاور املاک",
          "آژانس محمد امین", "همه آگهی‌ها"], "آژانس محمد امین"),
        (["وحید کاتب", "مشاور املاک | فعالیت از فروردین ۱۴۰۴",
          "پروفایل مشاور املاک", "املاک مسکن شهر"], "املاک مسکن شهر"),
        (["ولیزاده", "مشاور املاک | فعالیت از مهر ۱۴۰۰",
          "پروفایل مشاور املاک", "مشاور املاک۱۱۸ارومیه"], "مشاور املاک۱۱۸ارومیه"),
        (["مونا نعمت زاده", "مشاور املاک | فعالیت از اردیبهشت ۱۴۰۵",
          "پروفایل مشاور املاک", "هلدینگ آراد حقوقی و املاک"],
         "هلدینگ آراد حقوقی و املاک"),
        (["مشاور املاک مظلومی", "مشاور املاک | فعالیت از فروردین ۱۴۰۴",
          "پروفایل مشاور املاک", "مشاور املاک"], "مشاور املاک مظلومی"),
    ])
    def test_real_agency_names(self, lines, expected):
        assert agency_name_from_panel(lines) == expected

    def test_persian_digits_and_zwnj_survive(self):
        """The name is stored and shown, so it keeps the form it was written in
        — normalising would turn «۱۱۸» into «118»."""
        got = agency_name_from_panel(["مشاور املاک | فعالیت از مهر ۱۴۰۰",
                                      "مشاور املاک۱۱۸ارومیه"])
        assert "۱۱۸" in got and "118" not in got

    def test_the_role_line_is_not_a_name(self):
        assert agency_name_from_panel(["مشاور املاک | فعالیت از تیر ۱۴۰۴"]) is None

    def test_the_profile_link_is_not_a_name(self):
        assert agency_name_from_panel(["پروفایل مشاور املاک"]) is None

    def test_owner_page_has_no_agency_name(self):
        assert agency_name_from_panel(
            ["فروش آپارتمان ۸۵ متری", "ارومیه", "اطلاعات تماس", "چت"]) is None

    def test_the_longer_name_wins(self):
        """Between the generic role and the actual shop, take the shop."""
        assert agency_name_from_panel(
            ["مشاور املاک", "املاک برادران رضایی"]) == "املاک برادران رضایی"

    def test_empty(self):
        assert agency_name_from_panel([]) is None
        assert agency_name_from_panel(None) is None

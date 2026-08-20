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

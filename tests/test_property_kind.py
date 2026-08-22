"""
نوع ملک — separating an apartment from a villa from a کلنگی.

Divar's «خرید مسکونی» is an umbrella category holding all three, so filtering
the leads list by category could never narrow a search to one kind. Selecting
it returned apartments alongside villas, which is correct for the category and
useless for the search.

property_type is stored two ways — Persian off the scraper's breadcrumb,
an English slug from the manual lead form — and is sometimes empty, so the
title is the last resort. All three paths are covered here.
"""
import re
import pytest

from app.services.property_kind import (
    PROPERTY_KINDS,
    VALID_KINDS,
    classify,
    matches_stored_type,
    title_suggests,
    kind_options,
)


# ── titles taken verbatim from the reported screenshots ──────────────────────

SCREENSHOT_TITLES = [
    ("فروش واحد / ۹۵ متر / تکواحد / کاشان...",   "apartment"),
    ("فروش آپارتمان۷۹متری/سنددار/خیابان...",     "apartment"),
    ("خانه ویلایی ۱۰۳ متری + معلم + حیاط ...",    "villa"),
    ("فروش آپارتمان ۸۵ متر خیابان عمار...",      "apartment"),
    ("آپارتمان ۱۱۱ متری + کشاورز + فول نص...",   "apartment"),
    ("واحد ۱۲۷ متری + سرداران + قیمت استث...",   "apartment"),
    ("فروش کلنگی خ کاشانی نزدیک چهار راه ...",    "old_house"),
    ("فروش منزل مسکونی لوکس...",                 "villa"),
]


class TestRealTitles:
    @pytest.mark.parametrize("title,expected", SCREENSHOT_TITLES)
    def test_classified_from_title_alone(self, title, expected):
        assert classify(title=title) == expected

    def test_a_villa_search_excludes_the_apartments(self):
        kept = [t for t, _ in SCREENSHOT_TITLES if classify(title=t) == "villa"]
        assert kept == ["خانه ویلایی ۱۰۳ متری + معلم + حیاط ...",
                        "فروش منزل مسکونی لوکس..."]

    def test_an_apartment_search_excludes_the_villas(self):
        kept = [t for t, _ in SCREENSHOT_TITLES if classify(title=t) == "apartment"]
        # five of the eight: two say آپارتمان, two say واحد, one says آپارتمان۷۹
        assert len(kept) == 5
        assert all("ویلایی" not in t and "کلنگی" not in t for t in kept)


# ── the stored column, in either language ────────────────────────────────────

class TestStoredType:
    @pytest.mark.parametrize("stored,kind", [
        ("آپارتمان", "apartment"), ("apartment", "apartment"),
        ("اپارتمان", "apartment"),
        ("ویلا", "villa"), ("villa", "villa"), ("ویلایی", "villa"),
        ("کلنگی", "old_house"), ("خانه کلنگی", "old_house"),
        ("زمین", "land"), ("land", "land"),
        ("مغازه", "shop"), ("shop", "shop"),
        ("دفتر کار", "office"), ("office", "office"),
    ])
    def test_both_languages_resolve(self, stored, kind):
        assert matches_stored_type(kind, stored) is True
        assert classify(title="عنوانی که چیزی نمی‌گوید", property_type=stored) == kind

    def test_stored_type_beats_the_title(self):
        """A mislabelled title must not override what the column says."""
        assert classify(title="فروش آپارتمان ۸۰ متری", property_type="ویلا") == "villa"

    def test_unknown_stored_value_falls_back_to_the_title(self):
        assert matches_stored_type("apartment", "چیز دیگری") is False

    @pytest.mark.parametrize("kind", sorted(VALID_KINDS))
    def test_empty_stored_type_matches_nothing(self, kind):
        assert matches_stored_type(kind, None) is False
        assert matches_stored_type(kind, "") is False


# ── the words that rule a kind out ───────────────────────────────────────────

class TestExclusions:
    def test_kalangi_is_not_a_villa(self):
        """«خانه کلنگی» contains خانه, but it is a کلنگی."""
        assert classify(title="خانه کلنگی نزدیک بازار") == "old_house"
        assert title_suggests("villa", "خانه کلنگی نزدیک بازار") is False

    def test_a_courtyard_does_not_make_an_apartment_a_villa(self):
        assert classify(title="آپارتمان با حیاط اختصاصی") == "apartment"

    def test_a_villa_is_not_an_apartment(self):
        assert title_suggests("apartment", "خانه ویلایی ۲۰۰ متری") is False

    @pytest.mark.parametrize("title", ["ملک", "فروش فوری", "", None])
    def test_says_nothing(self, title):
        assert classify(title=title) is None


# ── the dropdown must not drift from the backend ─────────────────────────────

class TestDropdownMatchesBackend:
    def _html_options(self):
        html = open("frontend/index.html", encoding="utf-8").read()
        m = re.search(r'<select id="crm-filter-kind".*?</select>', html, re.S)
        assert m, "the نوع ملک select is missing from the leads filter bar"
        return [v for v in re.findall(r'<option value="([^"]*)"', m.group(0)) if v]

    def test_every_option_is_a_real_kind(self):
        for value in self._html_options():
            assert value in VALID_KINDS, f"«{value}» is not a known kind"

    def test_every_kind_is_offered(self):
        assert set(self._html_options()) == VALID_KINDS

    def test_labels_match(self):
        html = open("frontend/index.html", encoding="utf-8").read()
        block = re.search(r'<select id="crm-filter-kind".*?</select>', html, re.S).group(0)
        for key, spec in PROPERTY_KINDS.items():
            assert f'>{spec["label"]}<' in block, f"{key} label drifted"

    def test_kind_options_helper_agrees(self):
        assert {o["key"] for o in kind_options()} == VALID_KINDS


# ── the filter gate, mirrored ────────────────────────────────────────────────

def lead_matches_kind(kind, property_type, title):
    """Mirror of the SQL in _apply_lead_filters: the column decides when it is
    set, otherwise the title does."""
    spec = PROPERTY_KINDS.get(kind)
    if not spec:
        return True                      # no filter requested
    if matches_stored_type(kind, property_type):
        return True
    if property_type:
        return False                     # column set and it says another kind
    return title_suggests(kind, title)


class TestLeadFilterGate:
    LEADS = [
        ("آپارتمان ۸۵ متری", None),
        ("خانه ویلایی ۱۰۳ متری", None),
        ("فروش کلنگی خ کاشانی", None),
        ("واحد ۱۲۷ متری", "آپارتمان"),
        ("عنوان مبهم", "ویلا"),
        ("ملک", None),
    ]

    def test_villa_filter(self):
        got = [t for t, pt in self.LEADS if lead_matches_kind("villa", pt, t)]
        assert got == ["خانه ویلایی ۱۰۳ متری", "عنوان مبهم"]

    def test_apartment_filter(self):
        got = [t for t, pt in self.LEADS if lead_matches_kind("apartment", pt, t)]
        assert got == ["آپارتمان ۸۵ متری", "واحد ۱۲۷ متری"]

    def test_old_house_filter(self):
        got = [t for t, pt in self.LEADS if lead_matches_kind("old_house", pt, t)]
        assert got == ["فروش کلنگی خ کاشانی"]

    def test_no_filter_keeps_everything(self):
        got = [t for t, pt in self.LEADS if lead_matches_kind("", pt, t)]
        assert len(got) == len(self.LEADS)

    def test_an_unclassifiable_lead_never_satisfies_a_kind(self):
        for kind in VALID_KINDS:
            assert lead_matches_kind(kind, None, "ملک") is False

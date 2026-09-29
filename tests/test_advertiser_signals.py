"""
Reading «this is an agency» out of what the ad says.

Reported from a real listing: its description ends «املاک هستم», and it came
back from a search filtered to «شخصی». Checked on Divar's own site by hand,
the same filter returns it there too — so the declaration is not ours to
trust, and the words are worth reading ourselves.

Negation is the whole difficulty. «بدون کمیسیون» and «بدون واسطه» are what a
private seller writes; a matcher that only looks for «کمیسیون» reads them as
saying the opposite of what they say.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_adv.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import advertiser_signals as adv  # noqa: E402


class TestTheListingThatStartedThis:
    DESC = ("یه منزل شخصی طبقه دوم به متراژ۱۹۰متر\n"
            "در کوچه ۲۰متری\n"
            "سه خواب ،پارکینگ ،انباری\n"
            "تروتمیز ودسترسی به تمام امکانات رفاهی وتفریحی\n"
            "املاک هستم")

    def test_it_is_spotted(self):
        looks, phrase = adv.detect(self.DESC)
        assert looks is True

    def test_the_phrase_is_reported_so_a_person_can_disagree(self):
        _, phrase = adv.detect(self.DESC)
        assert phrase == "املاک هستم"

    def test_the_word_شخصی_in_the_ad_does_not_outvote_it(self):
        """The ad calls the flat «منزل شخصی» and still says who is posting."""
        assert adv.detect(self.DESC)[0] is True


class TestWhatAnAgencySays:
    def test_مشاور_املاک(self):
        assert adv.detect("مشاور املاک صداقت، ارومیه")[0]

    def test_مشاورین_املاک(self):
        assert adv.detect("مشاورین املاک پارسیان")[0]

    def test_بنگاه(self):
        assert adv.detect("بنگاه معاملات ملکی")[0]

    def test_کمیسیون(self):
        assert adv.detect("کمیسیون طبق نرخ اتحادیه")[0]

    def test_the_misspelling_of_کمیسیون(self):
        """«کمسیون» is at least as common as the correct spelling."""
        assert adv.detect("کمسیون طبق نرخ")[0]

    def test_همکاری_با_همکاران(self):
        assert adv.detect("همکاری با همکاران محترم")[0]

    def test_a_zero_width_non_joiner_does_not_hide_a_phrase(self):
        assert adv.detect("مشاور‌املاک تهران")[0]

    def test_arabic_letters_do_not_hide_one(self):
        """«ك» and «ي» are typed constantly instead of «ک» and «ی»."""
        assert adv.detect("مشاور املاك")[0]


class TestNegation:
    def test_بدون_کمیسیون_is_not_an_agency(self):
        assert adv.detect("فروش فوری، بدون کمیسیون")[0] is False

    def test_بدون_واسطه_is_not_either(self):
        assert adv.detect("مستقیم از مالک، بدون واسطه و بدون کمیسیون")[0] is False

    def test_بی_کمیسیون(self):
        assert adv.detect("بی کمیسیون، مالک هستم")[0] is False

    def test_غیر_قابل_کمیسیون(self):
        assert adv.detect("غیر قابل کمیسیون")[0] is False

    def test_a_negator_far_away_does_not_reach(self):
        """«بدون پارکینگ» at the top of an ad must not excuse «کمیسیون» at the
        bottom of it."""
        text = "بدون پارکینگ. " + "متراژ ۱۲۰ متر، سه خواب، طبقه دوم. " + "کمیسیون طبق نرخ"
        assert adv.detect(text)[0] is True

    def test_the_agency_phrase_still_wins_when_both_appear(self):
        """«بدون کمیسیون» from an agency advertising itself."""
        assert adv.detect("مشاور املاک — بدون کمیسیون برای مستأجر")[0] is True


class TestOrdinaryPrivateAds:
    def test_a_plain_description(self):
        assert adv.detect("۸۵ متری، دو خوابه، طبقه سوم، نورگیر")[0] is False

    def test_an_owner_saying_so(self):
        assert adv.detect("مالک هستم، مستقیم تماس بگیرید")[0] is False

    def test_empty_input(self):
        assert adv.detect("")[0] is False
        assert adv.detect(None)[0] is False

    def test_no_input_at_all(self):
        assert adv.detect()[0] is False


class TestItReadsEveryFieldGiven:
    def test_a_title_can_give_it_away(self):
        assert adv.detect(None, "املاک صداقت — اجاره آپارتمان")[0]

    def test_a_description_can(self):
        assert adv.detect("بنگاه املاک", None)[0]


class TestAnnotate:
    def test_it_adds_both_fields(self):
        d = adv.annotate({"description": "مشاور املاک"})
        assert d["agency_suspected"] is True
        assert d["agency_evidence"] == "مشاور املاک"

    def test_a_private_ad_is_marked_false_not_left_unset(self):
        """An absent field and a false one read differently in a panel."""
        d = adv.annotate({"description": "۸۵ متری دو خوابه"})
        assert d["agency_suspected"] is False
        assert d["agency_evidence"] is None

    def test_it_returns_the_same_dict(self):
        d = {"description": "بنگاه"}
        assert adv.annotate(d) is d

    def test_it_never_raises(self):
        """A listing must not be lost over a label."""
        d = adv.annotate({"description": 12345})
        assert d["agency_suspected"] in (True, False)


class TestTheDisagreementWithDivar:
    def test_personal_plus_agency_words_is_the_case_worth_showing(self):
        assert adv.disagrees_with_divar(
            {"agency_suspected": True, "advertiser_type": "personal"})

    def test_an_agency_that_declared_itself_is_not_a_disagreement(self):
        assert not adv.disagrees_with_divar(
            {"agency_suspected": True, "advertiser_type": "agency"})

    def test_a_private_ad_is_not_one_either(self):
        assert not adv.disagrees_with_divar(
            {"agency_suspected": False, "advertiser_type": "personal"})

    def test_an_unknown_declaration_is_not_a_disagreement(self):
        """Nothing to disagree with."""
        assert not adv.disagrees_with_divar({"agency_suspected": True})


class TestAnOwnerKeepingAgentsAway:
    """«مشاورین املاک تماس نگیرند» is what a private seller writes to keep
    agencies off the phone. The negator only ever looked BEFORE the phrase, so
    all of these — the words of the person who is not an agency — were labelled
    as one."""

    @pytest.mark.parametrize("text", [
        # «don't call»
        "مشاورین املاک تماس نگیرند",
        "مشاور املاک تماس نگیرد",
        "لطفا مشاورین املاک تماس نگیرید",
        "مشاورین محترم املاک لطفاً تماس نگیرید",
        "بنگاه ها تماس نگیرند",
        "املاکی‌ها تماس نگیرن",
        # «don't come» / «don't bother me»
        "املاک نیاید",
        "املاکی ها نیایند",
        "املاک مزاحم نشوند",
        "مشاورین املاک مزاحم نشوید",
        "بنگاه مزاحم نشه",
        # «not handed to an agency»
        "به املاکی واگذار نمی‌شود",
        "به مشاور املاک واگذار نمیشه",
        "به بنگاه واگذار نمی‌شود",
        "به املاک نمی‌دهم",
        "این ملک به هیچ مشاور املاکی واگذار نشده است",
        "فایل به هیچ املاکی سپرده نشده",
        "به بنگاه ندادم",
        # «keep away»
        "از تماس مشاورین املاک خودداری کنید",
        "مشاورین املاک ممنوع",
        "با املاکی ها کار نمیکنم",
        # «I am not one»
        "مشاور املاک نیستم",
        "بنگاه نیستم",
        "املاکی نیستیم",
        # the commission is not being asked for
        "کمیسیون ندارد",
        "کمسیون نمی‌گیرم",
        "حق الزحمه نداریم",
        # negated before the phrase, beyond the old list
        "عدم تماس مشاورین املاک",
        "نه مشاور املاک نه بنگاه",
        "فاقد کمیسیون",
    ])
    def test_it_is_not_an_agency(self, text):
        assert adv.detect(text) == (False, None), text

    def test_a_list_of_agents_kept_away_is_still_one_refusal(self):
        """The refusal comes after the last agent in the list, not the first."""
        assert adv.detect("از تماس املاکی ها و مشاورین املاک خودداری کنید")[0] is False

    def test_the_refusal_on_its_own_line_of_a_longer_description(self):
        text = ("فروش فوری آپارتمان ۸۵ متری\n"
                "مشاورین املاک تماس نگیرند\n"
                "قیمت توافقی، فقط تماس تلفنی")
        assert adv.detect(text)[0] is False

    def test_the_refusal_written_in_the_title(self):
        assert adv.detect("فروش آپارتمان ۹۰ متری — املاک نیاید")[0] is False

    def test_an_owner_who_also_says_owner(self):
        assert adv.detect("مالک هستم. مشاور املاک تماس نگیرد.")[0] is False


class TestARealAgencyIsStillOne:
    """The other direction: none of the above may switch the label off for an
    ad an agency wrote."""

    @pytest.mark.parametrize("text,phrase", [
        ("مشاور املاک صداقت، ارومیه", "مشاور املاک"),
        ("مشاورین املاک پارسیان", "مشاورین املاک"),
        ("با مشاور املاک ما تماس بگیرید", "مشاور املاک"),
        ("جهت اطلاعات بیشتر با بنگاه املاک نور تماس بگیرید", "بنگاه املاک"),
        ("املاک آرمان — کمیسیون طبق نرخ اتحادیه", "کمیسیون"),
        ("ثبت رایگان فایل شما در بنگاه ما", "ثبت رایگان فایل"),
        ("همکار محترم تماس بگیرید", "همکار محترم"),
        ("همکاری با همکاران محترم", "همکاری با همکاران"),
        # a declaration, whatever follows it
        ("مشاور املاک هستم، مالک نیستم", "مشاور املاک"),
        ("املاک هستم، تماس نگیرید مگر برای خرید", "املاک هستم"),
        ("مشاور املاک هستم، تماس نگیرید مگر برای خرید", "املاک هستم"),
        # a refusal in ANOTHER sentence does not reach across
        ("مشاور املاک آرمان. مالک نیستم", "مشاور املاک"),
        ("بنگاه املاک نور.\nمزاحم نمی‌شوم", "بنگاه املاک"),
        ("مشاورین املاک تماس نگیرند. املاک هستم", "املاک هستم"),
        # a dash ends the clause: the words after it are about something else
        ("آژانس املاک صدف — ورود بدون هماهنگی ممنوع", "آژانس املاک"),
    ])
    def test_it_is_still_an_agency(self, text, phrase):
        assert adv.detect(text) == (True, phrase), text

    @pytest.mark.parametrize("text,phrase", [
        # «نه» is inside ماهانه, «بی» inside بیستم, «نه» inside آشپزخانه
        ("ودیعه ماهانه، کمیسیون طبق نرخ", "کمیسیون"),
        ("طبقه بیستم، مشاور املاک آرمان", "مشاور املاک"),
        ("آشپزخانه اپن، بنگاه املاک نور", "بنگاه املاک"),
        ("اجاره روزانه، بنگاه املاک نور", "بنگاه املاک"),
    ])
    def test_a_negator_inside_another_word_is_not_a_negator(self, text, phrase):
        """«بدون» and «نه» count as words. Found inside «ماهانه» they said
        nothing, and an agency's ad was read as a private one."""
        assert adv.detect(text) == (True, phrase), text

    def test_a_negator_before_a_comma_does_not_reach_the_phrase(self):
        """«بدون پارکینگ» is about the parking, and the comma ends it."""
        assert adv.detect("بدون پارکینگ، مشاور املاک آرمان") == (True, "مشاور املاک")

    def test_a_negator_before_a_full_stop_does_not_either(self):
        assert adv.detect("بدون پارکینگ. مشاور املاک آرمان") == (True, "مشاور املاک")

    def test_the_negator_still_works_when_it_is_the_word_before(self):
        assert adv.detect("بدون هیچ کمیسیون")[0] is False
        assert adv.detect("بی‌ کمیسیون")[0] is False

    def test_the_first_occurrence_being_refused_does_not_hide_the_second(self):
        text = "مشاورین املاک تماس نگیرند\nمشاور املاک صداقت"
        assert adv.detect(text) == (True, "مشاور املاک")

    def test_a_refused_phrase_and_a_real_one_in_the_same_sentence(self):
        """The evidence is the phrase that still stands, not the refused one."""
        text = "مشاورین املاک تماس نگیرند، مشاور املاک صداقت"
        assert adv.detect(text) == (True, "مشاور املاک")

    def test_diacritics_do_not_hide_a_phrase(self):
        assert adv.detect("مشاورِ املاک") == (True, "مشاور املاک")
        assert adv.detect("مشاورـ املاک")[0] is True


class TestTheSamePhrasesFromAnAnnotatedRecord:
    """annotate() is what the scraper calls; the label it writes is what the
    panel shows."""

    def test_an_owner_keeping_agents_away_is_not_labelled(self):
        d = adv.annotate({"description": "فروش فوری\nمشاورین املاک تماس نگیرند"})
        assert d["agency_suspected"] is False
        assert d["agency_evidence"] is None

    def test_a_real_agency_is_labelled_with_its_phrase(self):
        d = adv.annotate({"description": "۸۵ متر، دو خواب\nمشاور املاک صداقت"})
        assert d["agency_suspected"] is True
        assert d["agency_evidence"] == "مشاور املاک"

    def test_the_title_and_the_card_hint_are_read_with_the_same_rules(self):
        d = adv.annotate({"description": None, "title": "آپارتمان ۷۰ متری — املاک نیاید",
                          "category_hint": "ارومیه"})
        assert d["agency_suspected"] is False

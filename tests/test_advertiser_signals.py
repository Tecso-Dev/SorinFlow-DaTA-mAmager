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

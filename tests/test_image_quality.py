"""
Scoring the photographs on a listing.

From the brief: «بررسی خودکار کیفیت، نور و زاویه عکس‌های آپلود شده». Three of
those four are measurable from the pixels. Angle is not, and the tests here
pin that the code says so rather than guessing at it.

The measurements are checked against images built to have the property in
question — a genuinely blurred photograph, a genuinely blown-out one — rather
than against remembered constants, because a threshold that passes its own
made-up number proves nothing.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_iq.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import image_quality as iq  # noqa: E402


def detailed(w=1280, h=960):
    """A sharp image: hard edges everywhere, so the Laplacian has something
    to find — but in mid-tones.

    The first version used pure black against pure white, which is not a
    photograph, it is a test pattern: every pixel sits at an end of the
    histogram, so 13% of it read as clipped and a deliberately *good* fixture
    came back flagged for exposure. The code was right and the fixture was
    not. Real photographs live between the ends, and so does this one now.
    """
    from PIL import Image
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            v = 210 if ((x // 4) + (y // 4)) % 2 else 45
            px[x, y] = (v, v, v)
    return im


def blurred(w=1280, h=960):
    """The same picture, actually blurred — not a constant standing in for one."""
    from PIL import ImageFilter
    return detailed(w, h).filter(ImageFilter.GaussianBlur(radius=6))


def blown_out(w=1280, h=960):
    from PIL import Image
    im = Image.new("RGB", (w, h), (255, 255, 255))
    px = im.load()
    for y in range(0, h // 10):
        for x in range(w):
            px[x, y] = (10, 10, 10)
    return im


class TestMeasure:
    def test_it_reports_the_dimensions(self):
        m = iq.measure(detailed(800, 600))
        assert m["width"] == 800 and m["height"] == 600
        assert m["pixels"] == 480_000

    def test_a_sharp_image_scores_high_on_sharpness(self):
        assert iq.measure(detailed())["sharpness"] > iq.SHARP_GOOD

    def test_a_blurred_image_scores_low(self):
        """Measured against a real Gaussian blur of the same picture."""
        assert iq.measure(blurred())["sharpness"] < iq.SHARP_FLOOR

    def test_blur_is_detected_regardless_of_megapixels(self):
        """Sharpness is a property of the picture, not of the saved size —
        otherwise a big soft photo beats a small crisp one."""
        big = iq.measure(blurred(2000, 1500))["sharpness"]
        small = iq.measure(blurred(640, 480))["sharpness"]
        assert big < iq.SHARP_FLOOR and small < iq.SHARP_FLOOR

    def test_a_blown_out_image_reports_clipping(self):
        assert iq.measure(blown_out())["clipped"] > iq.CLIP_LIMIT

    def test_a_normal_image_does_not_report_clipping(self):
        from PIL import Image
        mid = Image.new("RGB", (800, 600), (128, 128, 128))
        assert iq.measure(mid)["clipped"] <= iq.CLIP_LIMIT

    def test_a_tiny_image_does_not_crash(self):
        from PIL import Image
        assert iq.measure(Image.new("RGB", (2, 2)))["pixels"] == 4

    def test_an_unmeasurable_input_returns_nulls_not_an_exception(self):
        """A photograph we cannot measure is not one we should drop."""
        m = iq.measure(object())
        assert m["sharpness"] is None


class TestVerdict:
    def test_a_good_photo_has_no_problems(self):
        v = iq.verdict(iq.measure(detailed()))
        assert v["grade"] == "good" and v["problems"] == []

    def test_a_blurred_photo_is_flagged_blurry(self):
        assert "blurry" in iq.verdict(iq.measure(blurred()))["problems"]

    def test_a_blown_out_photo_is_flagged_on_exposure(self):
        assert "exposure" in iq.verdict(iq.measure(blown_out()))["problems"]

    def test_a_thumbnail_is_flagged_small(self):
        assert "small" in iq.verdict(iq.measure(detailed(320, 240)))["problems"]

    def test_two_problems_make_it_poor_rather_than_fair(self):
        v = iq.verdict(iq.measure(blurred(320, 240)))
        assert v["grade"] == "poor"

    def test_one_problem_is_fair(self):
        v = iq.verdict(iq.measure(detailed(320, 240)))
        assert v["grade"] == "fair"

    def test_an_unmeasured_image_is_unknown_not_zero(self):
        v = iq.verdict({"sharpness": None})
        assert v["grade"] == "unknown" and v["score"] is None

    def test_the_score_is_bounded(self):
        for img in (detailed(), blurred(), blown_out(), detailed(320, 240)):
            s = iq.verdict(iq.measure(img))["score"]
            assert 0 <= s <= 100

    def test_a_sharp_photo_outscores_a_blurred_one(self):
        assert iq.verdict(iq.measure(detailed()))["score"] > \
               iq.verdict(iq.measure(blurred()))["score"]

    def test_the_score_cannot_disagree_with_the_reasons(self):
        """Both come from the same three measurements, so a 'good' grade
        cannot sit next to a low score."""
        v = iq.verdict(iq.measure(detailed()))
        assert v["grade"] == "good" and v["score"] >= 60


class TestSummarise:
    def test_it_keeps_the_worst_not_just_the_average(self):
        """A gallery whose first image is blurred is a gallery nobody opens,
        and averaging that away with four good ones hides the fix."""
        results = [{"score": 90, "problems": []}] * 4 + \
                  [{"score": 10, "problems": ["blurry"]}]
        s = iq.summarise(results)
        assert s["worst"] == 10 and s["best"] == 90

    def test_it_counts_each_problem(self):
        results = [{"score": 10, "problems": ["blurry"]},
                   {"score": 20, "problems": ["blurry", "small"]}]
        s = iq.summarise(results)
        assert s["problems"]["blurry"] == 2
        assert s["problems"]["small"] == 1

    def test_unmeasured_images_are_counted_but_not_scored(self):
        results = [{"score": None, "problems": []}, {"score": 80, "problems": []}]
        s = iq.summarise(results)
        assert s["count"] == 2 and s["scored"] == 1

    def test_an_empty_gallery_is_nulls_not_zeros(self):
        s = iq.summarise([])
        assert s["average"] is None and s["worst"] is None


class TestItDoesNotClaimToJudgeAngle:
    """The brief asks for «زاویه». Composition needs to know what is in the
    frame, which needs a model this deployment cannot run."""

    def test_no_angle_or_composition_field_is_invented(self):
        m = iq.measure(detailed())
        assert "angle" not in m and "composition" not in m

    def test_the_module_says_what_it_measured(self):
        assert "Angle is not" in iq.__doc__


class TestItAdvisesRatherThanRejects:
    def test_nothing_here_deletes_a_photograph(self):
        import inspect
        src = inspect.getsource(iq)
        assert "unlink" not in src and "os.remove" not in src

    def test_the_problems_are_named_in_persian_for_the_panel(self):
        assert iq.PROBLEM_FA["blurry"] == "تار"
        assert "نور" in iq.PROBLEM_FA["exposure"]

    def test_describe_names_the_problem_not_just_a_number(self):
        """«۴۲ از ۱۰۰» tells a photographer nothing; «تار است» tells them to
        take it again."""
        text = iq.describe({"grade": "poor", "problems": ["blurry"]})
        assert "تار" in text


class TestTheScraperScoresWhatItKeeps:
    @pytest.fixture
    def dl_src(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        return inspect.getsource(DivarScraper.download_images)

    def test_it_scores_after_the_image_is_accepted(self, dl_src):
        assert dl_src.index("im.save(filepath") < dl_src.index("image_quality as _iq")

    def test_the_list_is_reset_per_property(self, dl_src):
        assert "_pending_quality: List[Dict[str, Any]] = []" in dl_src

    def test_a_scoring_failure_does_not_lose_the_image(self, dl_src):
        i = dl_src.index("image_quality as _iq")
        assert "except Exception" in dl_src[i:i + 400]

    def test_the_summary_reaches_the_saved_record(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.start_scraping_job)
        assert "property_data['image_quality']" in src

    def test_the_column_exists(self):
        from app.models.property import Property
        assert hasattr(Property, "image_quality")

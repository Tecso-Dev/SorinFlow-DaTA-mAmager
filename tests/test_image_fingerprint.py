"""
Recognising the same flat listed by three agencies at three prices.

The hashing is the easy half. These tests are mostly about the half that
makes it usable: an agency watermark matches every listing that agency ever
posted, and two flats in one tower legitimately share the exterior shot. A
naive matcher merges a whole building.

The asymmetry that decides the thresholds: a missed duplicate wastes a phone
call, a wrong merge deletes a property from somebody's pipeline. So the code
is built to under-claim, and so are these.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_fp.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.services import image_fingerprint as fp  # noqa: E402


def gradient(w=64, h=64, flip=False):
    """A deterministic test image whose structure survives being resized.

    The first version of this wrapped modulo 256, so the pattern itself
    changed with the image size and the «survives rescaling» test failed on
    the fixture rather than on the hash. A photograph does not do that: its
    large shapes stay put as it shrinks. This is smooth and low-frequency for
    the same reason.
    """
    from PIL import Image
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            v = int(255 * (x / max(w - 1, 1)) * 0.6 + 255 * (y / max(h - 1, 1)) * 0.4)
            v = max(0, min(255, v))
            if flip:
                v = 255 - v
            px[x, y] = (v, v, v)
    return im


def blocks(seed=0, w=64, h=64):
    from PIL import Image
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            v = 255 if ((x // 8) + (y // 8) + seed) % 2 else 0
            px[x, y] = (v, v, v)
    return im


# Well-separated 64-bit values. Small integers are a trap here: 1 and 2**63
# differ by two bits, which this algorithm reads as «the same photograph».
# The first draft of these tests used 1, 2 and 3 as «different» hashes and
# passed by accident on the cases it was not testing.
H_A = 0x0F0F0F0F0F0F0F0F
H_B = 0xF0F0F0F0F0F0F0F0   # 64 bits from H_A
H_C = 0x00FF00FF00FF00FF   # 32 from H_A
H_D = 0x3333CCCC3333CCCC   # far from all of the above
H_LOGO = 0xAAAA5555AAAA5555


# ── the hash itself ─────────────────────────────────────────────────────────

class TestDHash:
    def test_it_is_64_bits(self):
        assert 0 <= fp.dhash(gradient()) < 2 ** 64

    def test_the_same_image_hashes_the_same(self):
        assert fp.dhash(gradient()) == fp.dhash(gradient())

    def test_it_survives_rescaling(self):
        """The marketplace re-encodes every upload at several sizes; a hash
        that does not survive that identifies nothing."""
        big, small = gradient(256, 256), gradient(64, 64)
        assert fp.hamming(fp.dhash(big), fp.dhash(small)) <= fp.HASH_DISTANCE

    def test_it_survives_jpeg_recompression(self):
        import io
        from PIL import Image
        buf = io.BytesIO()
        gradient().save(buf, format="JPEG", quality=40)
        buf.seek(0)
        recompressed = Image.open(buf)
        assert fp.hamming(fp.dhash(gradient()), fp.dhash(recompressed)) <= fp.HASH_DISTANCE

    def test_different_pictures_hash_differently(self):
        assert fp.hamming(fp.dhash(gradient()), fp.dhash(blocks())) > fp.HASH_DISTANCE

    def test_an_inverted_image_is_not_the_same_image(self):
        assert fp.hamming(fp.dhash(gradient()), fp.dhash(gradient(flip=True))) > fp.HASH_DISTANCE

    def test_it_handles_a_colour_image(self):
        from PIL import Image
        assert isinstance(fp.dhash(Image.new("RGB", (32, 32), (120, 30, 200))), int)


class TestHamming:
    def test_identical_is_zero(self):
        assert fp.hamming(0b1011, 0b1011) == 0

    def test_it_counts_differing_bits(self):
        assert fp.hamming(0b0000, 0b1011) == 3

    def test_near_uses_the_threshold(self):
        assert fp.near(0, 0b111, threshold=3) is True
        assert fp.near(0, 0b1111, threshold=3) is False


# ── boilerplate ─────────────────────────────────────────────────────────────

class TestBoilerplate:
    def test_a_hash_on_many_properties_is_furniture(self):
        """An agency watermark matches every listing that agency posted."""
        logo = 12345
        lists = [[logo, H_A ^ (i << 8)] for i in range(10)]
        assert logo in fp.boilerplate(lists)

    def test_a_hash_on_one_property_is_evidence(self):
        lists = [[999, H_A], [H_B], [H_C]]
        assert 999 not in fp.boilerplate(lists)

    def test_repeats_within_one_listing_do_not_condemn_a_hash(self):
        """A listing that posts its own logo five times has used it once as
        evidence; counting the repeats would blacklist a unique photo."""
        assert fp.boilerplate([[7, 7, 7, 7, 7, 7]]) == set()

    def test_exactly_at_the_limit_is_still_evidence(self):
        lists = [[5]] * fp.BOILERPLATE_AFTER
        assert 5 not in fp.boilerplate(lists)

    def test_one_past_the_limit_is_not(self):
        lists = [[5]] * (fp.BOILERPLATE_AFTER + 1)
        assert 5 in fp.boilerplate(lists)

    def test_empty_input_is_an_empty_set(self):
        assert fp.boilerplate([]) == set()


# ── counting shared images ──────────────────────────────────────────────────

class TestSharedImages:
    def test_it_counts_a_match(self):
        assert fp.shared_images([H_A], [H_A]) == 1

    def test_no_overlap_is_zero(self):
        assert fp.shared_images([H_A], [H_B]) == 0

    def test_ignored_hashes_do_not_count(self):
        assert fp.shared_images([H_A], [H_A], ignore={H_A}) == 0

    def test_one_photo_repeated_cannot_manufacture_matches(self):
        """Otherwise a listing that posts the same picture four times looks
        like four independent pieces of evidence."""
        assert fp.shared_images([H_A] * 4, [H_A]) == 1

    def test_near_matches_count(self):
        assert fp.shared_images([H_A], [H_A ^ 0b11]) == 1

    def test_empty_sides_are_safe(self):
        assert fp.shared_images([], [H_A]) == 0
        assert fp.shared_images(None, None) == 0


# ── the verdict ─────────────────────────────────────────────────────────────

class TestCompare:
    A = {"hashes": [H_A, H_C, H_D], "area": 85, "rooms": 2, "district": "والفجر"}

    def test_no_shared_images_is_a_different_property(self):
        b = {"hashes": [H_B, H_B ^ 0xFF], "area": 85, "rooms": 2}
        assert fp.compare(self.A, b)["verdict"] == "different"

    def test_two_shared_images_is_a_duplicate_on_its_own(self):
        b = {"hashes": [H_A, H_C], "area": 400, "rooms": 9, "district": "جای دیگر"}
        r = fp.compare(self.A, b)
        assert r["verdict"] == "duplicate"
        assert r["reason"] == "multiple_images"

    def test_one_shared_image_with_agreeing_numbers_is_a_duplicate(self):
        b = {"hashes": [H_A], "area": 86, "rooms": 2, "district": "والفجر"}
        r = fp.compare(self.A, b)
        assert r["verdict"] == "duplicate"
        assert r["reason"] == "image_and_attributes"

    def test_one_shared_image_alone_is_only_likely(self):
        """Two flats in one tower share the exterior shot — the commonest
        false positive there is."""
        b = {"hashes": [H_A], "area": 200, "rooms": 5, "district": "جای دیگر"}
        r = fp.compare(self.A, b)
        assert r["verdict"] == "likely"
        assert r["reason"] == "single_image_only"

    def test_a_watermark_alone_does_not_make_a_duplicate(self):
        a = {"hashes": [H_LOGO, H_A], "area": 85, "rooms": 2}
        b = {"hashes": [H_LOGO, H_B], "area": 300, "rooms": 6}
        assert fp.compare(a, b, ignore={H_LOGO})["verdict"] == "different"

    def test_a_differing_area_blocks_corroboration(self):
        b = {"hashes": [H_A], "area": 140, "rooms": 2, "district": "والفجر"}
        assert fp.compare(self.A, b)["verdict"] == "likely"

    def test_a_differing_room_count_blocks_corroboration(self):
        b = {"hashes": [H_A], "area": 85, "rooms": 4, "district": "والفجر"}
        assert fp.compare(self.A, b)["verdict"] == "likely"

    def test_a_differing_district_blocks_corroboration(self):
        b = {"hashes": [H_A], "area": 85, "rooms": 2, "district": "جای دیگر"}
        assert fp.compare(self.A, b)["verdict"] == "likely"

    def test_missing_areas_are_absence_not_agreement(self):
        """Two listings both missing an area do not agree about it, and
        treating that as agreement is how a shared exterior merges a pair."""
        a = {"hashes": [H_A]}
        b = {"hashes": [H_A]}
        assert fp.compare(a, b)["verdict"] == "likely"

    def test_a_small_area_difference_still_corroborates(self):
        """Divar rounds, and two agents disagree by a metre on the same flat."""
        b = {"hashes": [H_A], "area": 87, "rooms": 2, "district": "والفجر"}
        assert fp.compare(self.A, b)["verdict"] == "duplicate"

    def test_a_missing_district_on_one_side_does_not_block(self):
        b = {"hashes": [H_A], "area": 85, "rooms": 2}
        assert fp.compare(self.A, b)["verdict"] == "duplicate"

    def test_every_result_carries_a_verdict_and_a_count(self):
        for b in [{"hashes": []}, {"hashes": [H_A]}, {"hashes": [H_A, H_C]}]:
            r = fp.compare(self.A, b)
            assert "verdict" in r and "shared_images" in r


class TestDescribe:
    def test_it_names_the_verdict_and_the_reason(self):
        text = fp.describe({"verdict": "duplicate", "reason": "multiple_images"})
        assert "همان ملک" in text and "چند عکس" in text

    def test_the_single_image_case_admits_its_weakness(self):
        text = fp.describe({"verdict": "likely", "reason": "single_image_only"})
        assert "ممکن است" in text

    def test_a_verdict_with_no_reason_still_reads(self):
        assert fp.describe({"verdict": "different", "reason": None}) == "ملک دیگری است"


class TestTheThresholdsLeanTowardsUnderClaiming:
    """A missed duplicate wastes a phone call; a wrong merge deletes a
    property from somebody's pipeline."""

    def test_certainty_needs_more_than_one_image(self):
        assert fp.MATCHES_FOR_CERTAINTY >= 2

    def test_the_distance_is_tight_enough_to_separate_rooms(self):
        assert fp.HASH_DISTANCE <= 10

    def test_boilerplate_triggers_before_it_can_merge_a_building(self):
        assert fp.BOILERPLATE_AFTER <= 6


class TestTheScraperFingerprintsWhatItKeeps:
    """Hashing has to happen on the images that survive the size, pixel and
    decode checks — not the ones offered."""

    @pytest.fixture
    def dl_src(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        return inspect.getsource(DivarScraper.download_images)

    def test_it_hashes_after_the_image_is_accepted(self, dl_src):
        assert dl_src.index("im.save(filepath") < dl_src.index("dhash(im)")

    def test_it_hashes_the_bitmap_already_in_memory(self, dl_src):
        """Re-opening every JPEG from disk later would be pointless work."""
        assert "dhash(im)" in dl_src

    def test_the_list_is_reset_per_property(self, dl_src):
        """One property's fingerprints leaking into the next would merge two
        unrelated listings — the one failure this must not have."""
        assert "_pending_hashes: List[int] = []" in dl_src
        assert dl_src.index("_pending_hashes: List[int] = []") < dl_src.index("_pending_hashes.append")

    def test_a_hashing_failure_does_not_lose_the_image(self, dl_src):
        i = dl_src.index("dhash(im)")
        assert "except Exception" in dl_src[i:i + 300]

    def test_the_hashes_reach_the_saved_record(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.start_scraping_job)
        assert "property_data['image_hashes']" in src

    def test_the_column_exists(self):
        from app.models.property import Property
        assert hasattr(Property, "image_hashes")

    def test_the_migration_is_registered(self):
        import inspect
        from app import database
        assert "_migrate_image_hashes" in inspect.getsource(database.init_db)

    def test_the_migration_does_not_backfill_at_boot(self):
        """Opening a few thousand JPEGs during startup is a rollout that
        times out."""
        import inspect
        from app import database
        src = inspect.getsource(database._migrate_image_hashes)
        assert "UPDATE" not in src.upper()

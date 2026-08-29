"""
Scraper robustness: image download limits and per-job OTP suppression.

Both guard against input the scraper does not control — a listing's photo list
comes from Divar, and OTP dismissal comes from whichever operator happens to be
watching one of three concurrent jobs.
"""
import asyncio
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_hard.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


# ── OTP suppression is per job ───────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_otp_store():
    from app.scraper import otp_store
    otp_store._store.clear()
    otp_store.reset_cancel()
    yield
    otp_store._store.clear()
    otp_store.reset_cancel()


def test_dismissing_one_jobs_prompt_does_not_silence_the_others():
    """Three scrapes run at once. Closing the modal on one used to suppress OTP
    globally for fifteen minutes, so the other two silently stopped collecting
    phone numbers with nothing on screen to say why."""
    from app.scraper import otp_store

    otp_store.request("jobA:ad1", "0911")
    otp_store.request("jobB:ad2", "0922")
    otp_store.request("jobC:ad3", "0933")

    dropped = otp_store.cancel_all("jobB")
    assert dropped == 1, "cancelling one job took another job's pending request"

    assert otp_store.is_cancelled("jobB:ad2") is True
    assert otp_store.is_cancelled("jobA:ad1") is False, "jobA was silenced by jobB's dismissal"
    assert otp_store.is_cancelled("jobC:ad3") is False, "jobC was silenced by jobB's dismissal"

    # and the untouched jobs keep their pending prompts
    keys = {p["key"] for p in otp_store.get_pending()}
    assert keys == {"jobA:ad1", "jobC:ad3"}


def test_starting_a_job_does_not_lift_another_jobs_dismissal():
    """reset_cancel runs when a scrape starts. Unscoped, launching one job
    re-enabled prompts on a job the user had just dismissed."""
    from app.scraper import otp_store

    otp_store.cancel_all("jobA")
    assert otp_store.is_cancelled("jobA:x") is True

    otp_store.reset_cancel("jobB")                 # a different job starts
    assert otp_store.is_cancelled("jobA:x") is True, "starting jobB lifted jobA's dismissal"

    otp_store.reset_cancel("jobA")                 # jobA restarts
    assert otp_store.is_cancelled("jobA:x") is False


def test_is_cancelled_accepts_a_full_key_or_a_bare_job_id():
    from app.scraper import otp_store
    otp_store.cancel_all("job7")
    assert otp_store.is_cancelled("job7") is True
    assert otp_store.is_cancelled("job7:whatever") is True
    assert otp_store.is_cancelled("") is False
    assert otp_store.is_cancelled(None) is False


# ── image download limits ────────────────────────────────────────────────────

def _png(w, h):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.headers = {"content-length": str(len(body))}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        # delivered in chunks, so the running cap is what stops an oversized
        # body rather than a header we are trusting
        for i in range(0, len(self._body), 4096):
            yield self._body[i:i + 4096]


class FakeClient:
    def __init__(self, body):
        self.body = body
        self.requested = []

    def stream(self, method, url, **kw):
        self.requested.append(url)
        return FakeResponse(self.body)


def make_scraper(tmp_path, body):
    from app.scraper.divar_scraper import DivarScraper
    s = DivarScraper.__new__(DivarScraper)
    s.images_dir = tmp_path
    client = FakeClient(body)
    s._client = lambda: client
    return s, client


def test_image_count_is_capped(tmp_path):
    """Divar supplies the photo list, so its length is not ours to trust."""
    from app.config import get_settings
    cfg = get_settings()
    saved = cfg.max_images_per_property
    cfg.max_images_per_property = 5
    try:
        s, client = make_scraper(tmp_path, _png(40, 30))
        paths = asyncio.run(s.download_images([f"http://x/{i}.png" for i in range(50)], "ad1"))
        assert len(client.requested) == 5, f"downloaded {len(client.requested)} of 50 offered"
        assert len(paths) == 5
    finally:
        cfg.max_images_per_property = saved


def test_oversized_image_is_refused(tmp_path):
    """A body over the cap must be dropped mid-download, not buffered whole."""
    from app.config import get_settings
    cfg = get_settings()
    saved = cfg.max_image_bytes
    cfg.max_image_bytes = 2048
    try:
        s, _ = make_scraper(tmp_path, _png(800, 800))     # comfortably over 2KB
        paths = asyncio.run(s.download_images(["http://x/big.png"], "ad2"))
        assert paths == [], "an image over the byte cap was stored anyway"
    finally:
        cfg.max_image_bytes = saved


def test_decompression_bomb_is_refused(tmp_path):
    """A small file that decodes to an enormous bitmap is the classic bomb.
    The pixel count is read from the header, so this is rejected before the
    memory it describes is ever allocated."""
    from app.config import get_settings
    cfg = get_settings()
    saved_px, saved_bytes = cfg.max_image_pixels, cfg.max_image_bytes
    cfg.max_image_pixels = 10_000            # 100x100
    cfg.max_image_bytes = 50 * 1024 * 1024
    try:
        s, _ = make_scraper(tmp_path, _png(2000, 2000))   # 4M pixels, tiny on disk
        paths = asyncio.run(s.download_images(["http://x/bomb.png"], "ad3"))
        assert paths == [], "a bitmap far over the pixel cap was decoded and saved"
    finally:
        cfg.max_image_pixels, cfg.max_image_bytes = saved_px, saved_bytes


def test_normal_images_still_download(tmp_path):
    """The caps must not break the ordinary case."""
    s, _ = make_scraper(tmp_path, _png(640, 480))
    paths = asyncio.run(s.download_images(["http://x/a.png", "http://x/b.png"], "ad4"))
    assert paths == ["/images/ad4/img_1.jpg", "/images/ad4/img_2.jpg"]
    assert (tmp_path / "ad4" / "img_1.jpg").exists()

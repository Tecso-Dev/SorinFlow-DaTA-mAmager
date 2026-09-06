"""
The unsaved listings, reachable from the panel.

The finish line already says how many went and why. A count can be checked
for arithmetic and nothing else, so this endpoint hands over the listings
themselves — with the Divar link that makes a single re-scrape possible.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_skep.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.api.routes import scraper as sr  # noqa: E402

SRC = inspect.getsource(sr.get_job_skipped)


class TestItIsRoutedWhereTheEventsAre:
    def test_the_path_sits_under_the_job(self):
        paths = [r.path for r in sr.router.routes]
        assert "/jobs/{job_id}/skipped" in paths

    def test_it_is_a_read(self):
        route = [r for r in sr.router.routes if r.path == "/jobs/{job_id}/skipped"][0]
        assert route.methods == {"GET"}


class TestWhatItReturns:
    def test_each_row_carries_the_link(self):
        assert '"url": r.url' in SRC
        assert '"divar_id": r.divar_id' in SRC

    def test_each_row_carries_why_in_persian(self):
        """The panel should not have to know the scraper's bucket names."""
        assert '"reason_label": labels.get(r.reason, r.reason)' in SRC

    def test_the_raw_bucket_is_kept_too(self):
        """Labels are for reading; the bucket is for filtering."""
        assert '"reason": r.reason' in SRC

    def test_there_is_a_summary_that_does_not_need_the_rows(self):
        assert '"by_reason"' in SRC
        assert "counts_for_job" in SRC

    def test_the_summary_is_ordered_by_size(self):
        assert "key=lambda kv: -kv[1]" in SRC

    def test_it_reuses_the_scrapers_own_persian_labels(self):
        """Two vocabularies for one set of buckets is how they drift apart."""
        assert "DivarScraper._FILTER_LABELS_FA" in SRC


class TestItCanBeNarrowed:
    def test_by_reason(self):
        assert "reason: Optional[str] = None" in SRC
        assert "reason=reason" in SRC

    def test_it_is_bounded_by_default(self):
        assert "limit: int = 1000" in SRC


class TestTheJobIdIsResolvedTheSameWayEverywhereNow:
    def test_there_is_one_resolver(self):
        assert hasattr(sr, "_job_uuid_from")

    def test_it_accepts_the_uuid(self):
        src = inspect.getsource(sr._job_uuid_from)
        assert "uuid.UUID(job_id)" in src

    def test_it_accepts_the_integer_row_id_the_panel_also_holds(self):
        src = inspect.getsource(sr._job_uuid_from)
        assert "ScrapingJob.id == int(job_id)" in src

    def test_an_unknown_job_is_a_404_not_a_crash(self):
        src = inspect.getsource(sr._job_uuid_from)
        assert "status_code=404" in src

    def test_the_skipped_endpoint_uses_it(self):
        assert "_job_uuid_from(job_id, db)" in SRC

"""
Telling the operator what they asked for versus what they got.

A run was set to find 78 and finished «تکمیل شده» with 3 saved. Nothing on
screen said why. The answer was in the log all along:

    Ran out of candidates: 3/50 new from a pool of 200. 39 already in the database.
    Filters dropped 126 listings — deposit=125, advertiser_type=1

125 of 200 candidates failed the deposit band alone. That is not a scraper
fault and not a mystery — it is a filter set too tight, and it belongs on the
job where the person who set the filter will see it.

The same run also exposed a real crash, swallowed by a bare except and
visible only as a warning:

    could not check OTP suppression:
    'asyncpg.pgproto.pgproto.UUID' object has no attribute 'split'
"""
import ast
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_recon.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


class TestAUuidJobIdDoesNotCrashTheStore:
    """ScrapingJob.job_id is UUID(as_uuid=True); callers hand over the object."""

    def test_job_of_accepts_a_uuid(self):
        import uuid
        from app.scraper import otp_store
        u = uuid.UUID("118a7ddc-415b-4ef5-b86a-6cc9e0f751e1")
        assert otp_store.job_of(u) == str(u)

    def test_a_uuid_matches_the_key_built_from_it(self):
        """Coercion is only correct if it yields the same prefix the keys use."""
        import uuid
        from app.scraper import otp_store
        u = uuid.UUID("118a7ddc-415b-4ef5-b86a-6cc9e0f751e1")
        assert otp_store.job_of(u) == otp_store.job_of(f"{u}:some-divar-id")

    def test_is_cancelled_accepts_a_uuid(self):
        import uuid
        from app.scraper import otp_store
        assert otp_store.is_cancelled(uuid.uuid4()) is False

    def test_none_and_empty_are_still_safe(self):
        from app.scraper import otp_store
        assert otp_store.job_of(None) == ""
        assert otp_store.job_of("") == ""


class TestTheFilterBreakdownReachesTheJob:
    @pytest.fixture
    def run_src(self):
        from app.scraper.divar_scraper import DivarScraper
        return inspect.getsource(DivarScraper.start_scraping_job)

    def test_the_tally_is_written_to_finish_reason(self, run_src):
        i = run_src.index("_FILTER_LABELS_FA")
        assert "finish_reason" in run_src[i:i + 500]

    def test_it_names_the_worst_offenders_first(self, run_src):
        i = run_src.index("_FILTER_LABELS_FA")
        window = run_src[max(0, i - 300):i]
        assert "sorted(" in window and "-kv[1]" in window

    def test_it_does_not_replace_an_existing_reason(self, run_src):
        i = run_src.index("آگهی با فیلترها حذف شد")
        assert "if finish_reason else" in run_src[i:i + 300]

    def test_every_skip_bucket_has_a_persian_label(self):
        """A message whose job is to name the filter must not print
        `advertiser_type` at someone who set «نوع آگهی‌دهنده»."""
        import re
        from app.scraper.divar_scraper import DivarScraper
        src = open(DivarScraper.__module__.replace(".", "/") + ".py",
                   encoding="utf-8-sig").read()
        buckets = set()
        for m in re.finditer(r'_skip\(f?"([^"{]*)', src):
            first = m.group(1).split()[0] if m.group(1).split() else ""
            if first:
                buckets.add(first)
        missing = buckets - set(DivarScraper._FILTER_LABELS_FA)
        assert not missing, f"no Persian label for: {sorted(missing)}"

    def test_the_boolean_filter_fields_are_labelled(self):
        from app.scraper.divar_scraper import DivarScraper
        for f in ("has_images", "has_elevator", "has_parking",
                  "has_storage", "has_balcony"):
            assert f in DivarScraper._FILTER_LABELS_FA


class TestClassAttributesAreReferencedThroughSelf:
    """A bare class-attribute name inside a method is a NameError that stays
    syntactically valid and only shows up when that branch runs."""

    def test_no_unqualified_use_of_the_label_map(self):
        import app.scraper.divar_scraper as m
        src = open(m.__file__, encoding="utf-8-sig").read()
        tree = ast.parse(src)
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and sub.id in (
                            "_FILTER_LABELS_FA", "_VALIDATOR_TYPES"):
                        bad.append((node.name, sub.id, sub.lineno))
        assert not bad, f"class attribute used without self: {bad}"

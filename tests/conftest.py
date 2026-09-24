"""Shared pytest fixtures for SorinFlow tests."""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ENVIRONMENT defaults to production, where the app refuses to seed the first
# account with the published placeholder — which is what the suite's first
# boot on an empty database would do. A throwaway of the suite's own instead.
os.environ.setdefault("SUPER_ADMIN_PASSWORD", "test-suite-only-not-a-real-password")

# app.database.engine is created once, at import, and lives for the whole
# suite — but pytest-asyncio hands every test function its own event loop,
# and at least one test (test_digest.py's TestThroughTheApp, which drives the
# real app through a module-scoped TestClient AND separately calls
# asyncio.run(digest.tick(...)) mid-test) genuinely uses three different
# loops within a single test. A real pool (see app/database.py) keeps
# connections alive between checkouts, and asyncpg ties a connection to the
# loop that opened it — handing one loop a connection opened on another
# fails with "attached to a different loop", and no amount of disposing
# between test *functions* reaches a case that mixes loops *within* one.
# NullPool never had this problem because it never reused a connection at
# all. DB_POOL_SIZE=0 is exactly that escape hatch (app/database.py), so the
# suite defaults to it; DB_POOL_SIZE is left alone if a test sets it itself
# to exercise real pooling deliberately.
os.environ.setdefault("DB_POOL_SIZE", "0")


@pytest.fixture(autouse=True)
async def _dispose_db_pool_after_test():
    """Belt-and-suspenders for any test that opts into a real pool (setting
    DB_POOL_SIZE itself, overriding the suite default above): the next test
    to reuse app.database.engine starts from an empty pool and connects
    fresh, on its own loop, rather than reusing a connection checked in by a
    loop that no longer exists. A no-op — cheap — whenever the pool is the
    suite's default NullPool, which never has anything checked in to drop.

    close=False, not the default close=True: close=True actively closes
    every currently-checked-in connection, which is itself a cross-loop
    operation if a *different*, still-running loop (a module-scoped
    fixture's background task, say) put one there. close=False only
    replaces the pool with a fresh, empty one and leaves whatever the old
    one held alone — safe regardless of who else might still be using it.
    """
    yield
    import app.database as db
    await db.engine.dispose(close=False)


@pytest.fixture
def sample_html_property():
    """Minimal Divar property page HTML for parser tests."""
    return """
    <html><body>
    <div class="kt-base-row kt-unexpandable-row">
        <div class="kt-unexpandable-row__title">متراژ</div>
        <div class="kt-unexpandable-row__value">۸۵ متر</div>
    </div>
    <div class="kt-base-row kt-unexpandable-row">
        <div class="kt-unexpandable-row__title">اتاق</div>
        <div class="kt-unexpandable-row__value">۲</div>
    </div>
    <div class="kt-base-row kt-unexpandable-row">
        <div class="kt-unexpandable-row__title">سال ساخت</div>
        <div class="kt-unexpandable-row__value">۱۴۰۰</div>
    </div>
    <div class="kt-base-row kt-unexpandable-row">
        <div class="kt-unexpandable-row__title">طبقه</div>
        <div class="kt-unexpandable-row__value">۳ از ۶</div>
    </div>
    <div class="kt-group-row-item"><span>آسانسور</span></div>
    <div class="kt-group-row-item"><span>پارکینگ</span></div>
    <div class="kt-group-row-item"><span>بدون انباری</span></div>
    <div class="kt-group-row-item"><span>بالکن</span></div>
    <div class="kt-base-row kt-unexpandable-row">
        <div class="kt-unexpandable-row__title">قیمت کل</div>
        <div class="kt-unexpandable-row__value">۴۵۰ میلیون تومان</div>
    </div>
    <div class="kt-description-row__text--truncated-text">آپارتمان شیک و تمیز در محله آرام</div>
    </body></html>
    """

"""Shared pytest fixtures for SorinFlow tests."""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ENVIRONMENT defaults to production, where the app refuses to seed the first
# account with the published placeholder — which is what the suite's first
# boot on an empty database would do. A throwaway of the suite's own instead.
os.environ.setdefault("SUPER_ADMIN_PASSWORD", "test-suite-only-not-a-real-password")


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

"""
The category selects group rent under «اجاره» and buy under «خرید» (#56).

The scrape form listed every category flat, buy and rent interleaved. Now
the form and the three filters built from the same list (properties, CRM
leads, the jobs table) group them by the `family` each category carries on
/api/scraper/categories, under the family's Persian name the same answer
gives — nothing in the panel names a category. The real loadCategories runs
under Node (tests/js/category_groups.mjs), as test_schedules_card.py does it.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_cgr.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "js" / "category_groups.mjs"


def test_the_category_selects_are_grouped_under_node():
    node = shutil.which("node")
    assert node, "node is not on PATH — this check must not be skipped"
    result = subprocess.run([node, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "5 checks passed" in result.stdout, result.stdout


async def test_every_category_the_panel_offers_names_its_family():
    """The data the grouping is built from: every category the form offers has
    a family, and every family a Persian name — rent «اجاره», buy «خرید»."""
    from app.api.routes import scraper as routes
    from app.config import CATEGORIES
    cats = await routes.get_available_categories()
    assert [c["slug"] for c in cats] == list(CATEGORIES)
    for c in cats:
        assert c["family"], c["slug"]
        assert c["family_name"], c["slug"]
    by = {c["slug"]: (c["family"], c["family_name"]) for c in cats}
    assert by["rent-apartment"] == ("rent", "اجاره")
    assert by["rent-industrial-agricultural-property"] == ("rent", "اجاره")
    assert by["buy-old-house"] == ("buy", "خرید")
    assert by["buy-office"] == ("buy", "خرید")
    assert by["rent-temporary"][0] == "temporary"
    assert by["real-estate-services"][0] == "service"
    assert len({name for _, name in by.values()}) == len({fam for fam, _ in by.values()})

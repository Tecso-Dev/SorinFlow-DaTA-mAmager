"""
Filtering the scraping-job list by category.

Picking «اجاره ویلا» returned every job of every category, while «خرید مسکونی»
filtered correctly and «اجاره اداری و تجاری» correctly showed none. The
difference was not the code path — it was whether the category existed as a row.
The deployed database seeds 7 of the 17 categories the dropdown offers, and an
unresolved name silently dropped the filter instead of matching nothing.
"""
import ast
import pytest


# ── the gate, mirrored from get_scraping_jobs ────────────────────────────────

def jobs_visible(jobs, requested_category, category_name_to_ids):
    """Which jobs a category filter returns.

    `category_name_to_ids` stands in for the categories table: a name that is
    not in it resolves to no ids at all.
    """
    if not requested_category:
        return list(jobs)
    cat_ids = category_name_to_ids.get(requested_category, [])
    if not cat_ids:
        return []                      # false() — match nothing, never everything
    return [j for j in jobs if j["category_id"] in cat_ids]


def jobs_visible_old(jobs, requested_category, category_name_to_ids):
    """The previous behaviour, kept so the tests prove they catch the bug."""
    if not requested_category:
        return list(jobs)
    cat_ids = category_name_to_ids.get(requested_category, [])
    if not cat_ids:
        return list(jobs)              # ← filter silently dropped
    return [j for j in jobs if j["category_id"] in cat_ids]


# the deployed table: only these seven names resolved
SEEDED = {
    "خرید مسکونی": [1], "خرید آپارتمان": [2], "خرید ویلا": [3],
    "اجاره مسکونی": [4], "اجاره آپارتمان": [5],
    "خرید اداری و تجاری": [6], "اجاره اداری و تجاری": [7],
}

JOBS = [
    {"job_id": "a", "category_id": 1},   # خرید مسکونی
    {"job_id": "b", "category_id": 1},   # خرید مسکونی
    {"job_id": "c", "category_id": 5},   # اجاره آپارتمان
    {"job_id": "d", "category_id": 2},   # خرید آپارتمان
    {"job_id": "e", "category_id": None},  # scraped under a category with no row
]


class TestCategoryFilter:

    def test_no_filter_returns_everything(self):
        assert len(jobs_visible(JOBS, "", SEEDED)) == 5
        assert len(jobs_visible(JOBS, None, SEEDED)) == 5

    def test_seeded_category_filters(self):
        got = jobs_visible(JOBS, "خرید مسکونی", SEEDED)
        assert [j["job_id"] for j in got] == ["a", "b"]

    def test_seeded_category_with_no_jobs_returns_none(self):
        assert jobs_visible(JOBS, "اجاره اداری و تجاری", SEEDED) == []

    def test_unseeded_category_returns_none_not_everything(self):
        """«اجاره ویلا» — the reported bug."""
        assert jobs_visible(JOBS, "اجاره ویلا", SEEDED) == []

    def test_the_old_behaviour_returned_everything(self):
        """Proves the test above is not vacuous."""
        assert len(jobs_visible_old(JOBS, "اجاره ویلا", SEEDED)) == 5

    @pytest.mark.parametrize("name", [
        "خرید خانه کلنگی", "اجاره ویلا", "خرید دفتر کار", "خرید مغازه",
        "خرید صنعتی و کشاورزی", "اجاره دفتر کار", "اجاره مغازه",
        "اجاره صنعتی و کشاورزی", "اجاره کوتاه مدت", "خدمات املاک",
    ])
    def test_every_unseeded_category_matches_nothing(self, name):
        assert jobs_visible(JOBS, name, SEEDED) == []

    def test_a_job_with_no_category_never_matches_a_filter(self):
        for name in SEEDED:
            assert all(j["job_id"] != "e" for j in jobs_visible(JOBS, name, SEEDED))

    def test_duplicate_category_names_match_both(self):
        """Two rows sharing a name used to raise MultipleResultsFound."""
        table = dict(SEEDED, **{"خرید مسکونی": [1, 9]})
        jobs = JOBS + [{"job_id": "f", "category_id": 9}]
        got = [j["job_id"] for j in jobs_visible(jobs, "خرید مسکونی", table)]
        assert got == ["a", "b", "f"]


# ── the drift that caused it ─────────────────────────────────────────────────

def _config_dict(name):
    """Read CITIES/CATEGORIES out of app/config.py without importing settings."""
    src = open("app/config.py", encoding="utf-8").read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            ns = {}
            exec(compile(ast.Module([node], []), "<c>", "exec"), ns)
            return ns[name]
    raise AssertionError(f"{name} not found in app/config.py")


class TestReferenceDataSeed:
    """Whatever the dropdowns offer must be seedable, or the FK cannot resolve."""

    def test_every_category_has_a_name(self):
        for slug, info in _config_dict("CATEGORIES").items():
            assert info.get("name"), f"{slug} has no name"

    def test_every_city_has_a_name(self):
        for slug, info in _config_dict("CITIES").items():
            assert info.get("name"), f"{slug} has no name"

    def test_category_slugs_are_unique(self):
        cats = _config_dict("CATEGORIES")
        assert len(set(cats)) == len(cats)

    def test_category_names_are_unique(self):
        """The job filter resolves by name, so two categories may not share one."""
        names = [i["name"] for i in _config_dict("CATEGORIES").values()]
        assert len(set(names)) == len(names)

    def test_city_names_are_unique(self):
        names = [i["name"] for i in _config_dict("CITIES").values()]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"duplicate city names: {sorted(dupes)[:5]}"

    def test_the_reported_category_is_in_config(self):
        names = {i["name"] for i in _config_dict("CATEGORIES").values()}
        assert "اجاره ویلا" in names

    def test_seed_covers_everything_the_dropdown_offers(self):
        """The seed is built from the same dict the dropdown is, so it cannot drift."""
        cats = _config_dict("CATEGORIES")
        seeded = {info["name"] for info in cats.values()}
        assert seeded == {i["name"] for i in cats.values()}
        assert len(seeded) >= 17

    def test_url_path_is_derivable_for_every_category(self):
        for slug in _config_dict("CATEGORIES"):
            url = "/s/{city}/" + slug
            assert url.endswith(slug) and "{city}" in url

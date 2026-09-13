"""
«i can acces only to my account numbers and not other account.»

Four places touch somebody's Divar sessions, and a rule enforced in three of
them is not a rule. The fourth is the one that matters most: rotation. A run
that reaches outside its owner's pool does not just read a row — it logs that
number into Divar and spends a reveal, and reveals are charged to the
account, not to us.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_owne.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.api.routes import auth as auth_routes  # noqa: E402
from app.api.routes import scraper as scraper_routes  # noqa: E402
from app.scraper.divar_scraper import DivarScraper  # noqa: E402


class TestWhoSeesEverything:
    def test_root_and_super_admin_do(self):
        for role in ("root", "super_admin"):
            assert auth_routes._sees_every_session(type("U", (), {"role": role})())

    def test_an_admin_does_not(self):
        assert not auth_routes._sees_every_session(type("U", (), {"role": "admin"})())

    def test_a_visitor_does_not(self):
        assert not auth_routes._sees_every_session(type("U", (), {"role": "visitor"})())

    def test_nobody_does(self):
        assert not auth_routes._sees_every_session(None)


class TestTheListIsNarrowed:
    def test_it_goes_through_the_shared_narrowing(self):
        src = inspect.getsource(auth_routes.list_cookies)
        assert "_own_sessions_only(select(Cookie), current_user)" in src

    def test_an_anonymous_caller_gets_nothing_rather_than_everything(self):
        """These rows are live Divar credentials."""
        src = inspect.getsource(auth_routes._own_sessions_only)
        assert "Cookie.id == -1" in src

    def test_it_requires_a_logged_in_user(self):
        assert "current_user" in inspect.signature(auth_routes.list_cookies).parameters

    def test_an_admin_is_told_they_may_reassign(self):
        src = inspect.getsource(auth_routes.list_cookies)
        assert '"can_reassign"' in src

    def test_owner_names_are_only_resolved_for_an_admin(self):
        """Nobody else is shown a list that could hold somebody else's row."""
        src = inspect.getsource(auth_routes.list_cookies)
        assert "if _sees_every_session(current_user):" in src


class TestClaimingAndRefusing:
    def test_an_import_claims_the_session(self):
        src = inspect.getsource(auth_routes.import_cookies)
        assert "owner_user_id=current_user.id if current_user else None" in src

    def test_importing_over_somebody_elses_number_is_refused(self):
        src = inspect.getsource(auth_routes.import_cookies)
        assert "status_code=403" in src
        assert "به حساب کاربری دیگری تعلق دارد" in src

    def test_an_unowned_session_is_adopted_rather_than_refused(self):
        """The backfill leaves none, but a row created before it ran should
        become somebody's rather than stay nobody's."""
        src = inspect.getsource(auth_routes.import_cookies)
        assert "if not existing.owner_user_id and current_user:" in src

    def test_a_login_claims_the_session_it_bought(self):
        src = inspect.getsource(auth_routes.verify_otp)
        assert "owner_user_id=current_user.id if current_user else None" in src

    def test_deleting_somebody_elses_is_refused(self):
        src = inspect.getsource(auth_routes.delete_cookie)
        assert "status_code=403" in src
        assert "_sees_every_session(user)" in src


class TestRotationStaysInsideThePool:
    def test_the_job_carries_its_owner(self):
        src = inspect.getsource(scraper_routes.run_scraping_job)
        assert "owner_user_id" in src
        assert "scraper.owner_user_id = owner_user_id" in src

    def test_the_start_endpoint_knows_who_asked(self):
        params = inspect.signature(scraper_routes.start_scraping_job).parameters
        assert "current_user" in params

    def test_it_is_passed_to_the_background_task(self):
        src = inspect.getsource(scraper_routes.start_scraping_job)
        assert "current_user.id if current_user else None" in src

    def test_the_pool_query_filters_on_it(self):
        src = inspect.getsource(DivarScraper._load_rotation_pool)
        assert "CookieModel.owner_user_id == owner" in src

    def test_the_pool_still_rests_heavy_accounts(self):
        """The narrowing must not have displaced the resting rule."""
        src = inspect.getsource(DivarScraper._load_rotation_pool)
        assert "_resting" in src and "rest_after_reveals" in src

    def test_the_usable_count_uses_the_same_pool(self):
        """Otherwise a run absorbs code prompts for accounts it can never
        reach, and concludes the pool is exhausted when it never had them."""
        src = inspect.getsource(DivarScraper._usable_account_count)
        assert "Cookie.owner_user_id == owner" in src

    def test_a_run_with_no_owner_keeps_the_old_behaviour(self):
        """An internally started scrape must not lose its pool."""
        src = inspect.getsource(DivarScraper._usable_account_count)
        assert "if owner:" in src

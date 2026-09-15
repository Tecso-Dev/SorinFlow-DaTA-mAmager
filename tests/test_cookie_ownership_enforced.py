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
        # the body lives in _launch_job, which start and resume share
        src = inspect.getsource(scraper_routes._launch_job)
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


class TestTheSideDoor:
    """Ownership enforced on the list and the pool but not on the status
    endpoint was ownership with a side door: the header pill and the scrape
    form's «خودکار» both read it, and it used to fall back to any valid
    session in the database."""

    def test_the_status_endpoint_knows_who_is_asking(self):
        params = inspect.signature(auth_routes.get_cookie_status).parameters
        assert "current_user" in params

    def test_both_fallbacks_are_narrowed_to_the_caller(self):
        src = inspect.getsource(auth_routes.get_cookie_status)
        assert src.count("_mine(select(Cookie)") == 2, \
            "one of the two fallbacks still reaches the whole table"

    def test_the_callers_own_divar_phone_is_the_default(self):
        src = inspect.getsource(auth_routes.get_cookie_status)
        assert "current_user.divar_phone if current_user else None" in src

    def test_a_named_number_must_be_the_callers(self):
        """A run that starts ON a given number never consults the pool for
        its first account, so this is the only place that check can live."""
        src = inspect.getsource(scraper_routes._launch_job)
        assert "به حساب کاربری شما تعلق ندارد" in src
        assert "status_code=403" in src

    def test_root_may_not_name_somebody_elses_number_either(self):
        """Seeing every session is for reassigning them. A run on another
        person's number spends THEIR reveals, whoever starts it."""
        src = inspect.getsource(scraper_routes._launch_job)
        assert '("root", "super_admin")' not in src

    def test_using_and_seeing_are_different_powers(self):
        src = inspect.getsource(auth_routes._usable_by)
        assert "_sees_every_session" not in src
        assert "owner_user_id == user.id" in src
        assert "_usable_by(q, current_user)" in inspect.getsource(auth_routes.get_cookie_status)

    def test_a_named_number_owned_by_somebody_else_is_not_reported(self):
        src = inspect.getsource(auth_routes.get_cookie_status)
        assert "_mine(select(Cookie).where(Cookie.phone_number == phone))" in src

    def test_refresh_and_logout_know_who_is_asking(self):
        """They used to take any number at all — a valid way for one person
        to log another out of Divar."""
        for fn in (auth_routes.refresh_session, auth_routes.logout):
            assert "user" in inspect.signature(fn).parameters
            assert "_own_session_or_403" in inspect.getsource(fn)
        src = inspect.getsource(auth_routes._own_session_or_403)
        assert "status_code=403" in src and "row.owner_user_id != user.id" in src

    def test_answering_divars_code_takes_the_session_over(self):
        """The person who typed the code holds the phone — the session is
        theirs even if a previous owner logged this number in before."""
        src = inspect.getsource(auth_routes.verify_otp)
        assert "existing_cookie.owner_user_id != current_user.id" in src
        assert "changes hands" in src

    def test_it_compares_digits_not_strings(self):
        src = inspect.getsource(scraper_routes._launch_job)
        assert "ch.isdigit()" in src

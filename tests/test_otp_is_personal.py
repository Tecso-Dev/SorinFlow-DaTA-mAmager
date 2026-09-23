"""
A code texted to somebody else's phone, on somebody else's screen.

The panel polls for Divar's SMS challenges and opens a dialog naming the
number the code went to. That endpoint had no idea who was asking: it returned
every pending prompt on the server, so a colleague's Divar number appeared in
front of whoever had the panel open, asking for a code only the owner could
have received — and the submit, resend and dismiss endpoints beside it would
act on any key at all.

Anything belonging to one Divar account stays with that account. Root's access
to the sessions list is for managing them — assigning a number, noticing an
unclaimed one — not for answering challenges on them.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_otp_personal.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.api.routes import scraper as sc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "app/api/routes/scraper.py").read_text(encoding="utf-8")


class _User:
    def __init__(self, uid, role="admin"):
        self.id, self.role = uid, role


SOBHAN, JANESAR, ROOT_USER = _User(1), _User(2), _User(9, "root")

# two runs, two people, two Divar numbers
PENDING = [
    {"key": "job-a:ad1", "phone_hint": "09058432452", "remaining": 120},
    {"key": "job-b:ad2", "phone_hint": "09029315496", "remaining": 200},
]
IDENTITY = [{"phone": "09029315496", "job_id": "job-b", "age": 30}]

OWNERS = {"9058432452": SOBHAN.id, "9029315496": JANESAR.id}
JOB_OWNERS = {"job-a": SOBHAN.id, "job-b": JANESAR.id}


@pytest.fixture
def wired(monkeypatch):
    async def by_phone(_db, phones):
        from app.scraper.otp_store import _digits
        return {_digits(p): OWNERS[_digits(p)] for p in phones
                if p and _digits(p) in OWNERS}

    async def by_job(_db, job_ids):
        return {str(j): JOB_OWNERS.get(str(j)) for j in job_ids if j}
    monkeypatch.setattr(sc, "_owner_of_account", by_phone)
    monkeypatch.setattr(sc, "_owner_of_job", by_job)


def _mine(user, pending=None, identity=None):
    return asyncio.run(sc._my_prompts(None, user, pending if pending is not None else PENDING,
                                      identity if identity is not None else IDENTITY))


class TestWhoSeesThePrompt:

    def test_each_person_sees_only_their_own_number(self, wired):
        mine, _ = _mine(SOBHAN)
        assert [p["phone_hint"] for p in mine] == ["09058432452"]
        mine, _ = _mine(JANESAR)
        assert [p["phone_hint"] for p in mine] == ["09029315496"]

    def test_root_is_not_handed_somebody_elses_code_prompt(self, wired):
        """Root manages the sessions list; root does not answer challenges on
        numbers that belong to other people."""
        mine, ident = _mine(ROOT_USER)
        assert mine == [] and ident == []

    def test_the_identity_wall_follows_the_same_number(self, wired):
        _, ident = _mine(JANESAR)
        assert [i["phone"] for i in ident] == ["09029315496"]
        _, ident = _mine(SOBHAN)
        assert ident == []

    def test_a_number_written_any_way_is_the_same_number(self, wired):
        """+98912…, 0098912… and 0912… reach the panel from different places."""
        mine, _ = _mine(SOBHAN, pending=[{"key": "job-a:ad1", "phone_hint": "+989058432452"}])
        assert len(mine) == 1

    def test_a_number_nobody_owns_falls_to_whoever_manages_them(self, wired):
        """Not a loophole — a prompt on no screen at all hangs the run until it
        times out, and only these roles can claim an unassigned number."""
        orphan = [{"key": "job-x:ad9", "phone_hint": "09121110000"}]
        assert _mine(ROOT_USER, pending=orphan, identity=[])[0] == orphan
        assert _mine(SOBHAN, pending=orphan, identity=[])[0] == []

    def test_the_job_answers_only_when_no_session_claims_the_number(self, wired):
        """A number with an owner is that owner's, whoever started the run."""
        crossed = [{"key": "job-b:ad1", "phone_hint": "09058432452"}]   # janesar's run, sobhan's number
        assert len(_mine(SOBHAN, pending=crossed, identity=[])[0]) == 1
        assert _mine(JANESAR, pending=crossed, identity=[])[0] == []


class TestActingOnOneIsGuardedToo:

    def test_every_endpoint_knows_who_is_asking(self):
        for name in ("get_otp_pending", "submit_otp_code", "resend_otp_code", "cancel_otp"):
            body = SRC.split(f"async def {name}(")[1].split("):")[0]
            assert "current_user: User = Depends(get_current_user)" in body, \
                f"{name} serves anyone who asks"

    def test_submitting_resending_and_dismissing_check_the_key(self):
        for name in ("submit_otp_code", "resend_otp_code"):
            body = SRC.split(f"async def {name}(")[1].split("@router.")[0]
            assert "_my_prompt_or_404" in body, f"{name} acts on any key at all"
        cancel = SRC.split("async def cancel_otp(")[1].split("@router.")[0]
        assert "_my_prompt_or_404" in cancel and "_is_mine" in cancel

    def test_somebody_elses_key_is_answered_as_if_it_did_not_exist(self):
        """Whether a colleague is waiting on a code is not the caller's
        business, so it is a 404, not a 403."""
        guard = SRC.split("async def _my_prompt_or_404(")[1].split("\n\n@")[0]
        assert "status_code=404" in guard and "403" not in guard

    def test_dismissing_no_longer_silences_everybody_elses_runs(self):
        """It used to suppress every prompt on the server for fifteen minutes."""
        cancel = SRC.split("async def cancel_otp(")[1].split("@router.")[0]
        assert "otp_store.cancel_all(None)" not in cancel
        assert "_my_prompts" in cancel, "an unscoped dismissal must still only reach my own runs"

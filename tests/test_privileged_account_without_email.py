"""
Issue #13 — the admin account has no email, so it has no recovery path.

Both new paths refuse an account with no address, and that is correct:
enabling a second factor an account cannot receive would lock it out, which
is the hole the work exists to close. But it leaves a super_admin with no
self-service recovery at all, and nothing on the users screen said so.

The data half — giving `admin` an address, or retiring it — is a decision
about production, not code. This is the other half: the screen names a
privileged account that cannot be recovered, where the person who can fix
it is already looking.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = open(os.path.join(ROOT, "frontend/js/app.js"), encoding="utf-8").read()
INDEX = open(os.path.join(ROOT, "frontend/index.html"), encoding="utf-8").read()

i = APP_JS.index("const privileged = ['root', 'super_admin', 'admin'].includes(u.role);")
BLOCK = APP_JS[i:i + 1500]


class TestTheWarning:
    def test_it_is_about_privileged_accounts_only(self):
        """A visitor with no email is not a lockout risk worth a red badge."""
        assert "['root', 'super_admin', 'admin'].includes(u.role)" in BLOCK

    def test_it_needs_no_email(self):
        assert "!(u.email || '').trim()" in BLOCK

    def test_an_inactive_account_is_not_warned_about(self):
        """Nobody is locked out of an account nobody can use."""
        assert "&& u.is_active" in BLOCK

    def test_it_is_shown_in_the_contact_cell(self):
        assert "${contact}${recoveryWarning}" in APP_JS

    def test_it_says_what_it_means(self):
        assert "قابل بازیابی نیست" in BLOCK

    def test_the_tooltip_says_why_and_what_the_alternative_is(self):
        assert "تنها راه برگشت پایگاه داده است" in BLOCK

    def test_it_is_red(self):
        """A latent lockout is not a warning-yellow fact."""
        assert "bg-danger" in BLOCK

    def test_the_reason_is_written_beside_it(self):
        # the comment sits above the code line the block is anchored on
        assert "Issue #13" in APP_JS[i - 700:i]


class TestThePanelWillBeReloaded:
    def test_the_cache_buster_moved(self):
        m = re.search(r"js/app\.js\?v=([0-9a-z]+)", INDEX)
        assert m and m.group(1) != "20260914d"

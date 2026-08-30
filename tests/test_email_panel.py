"""
ایمیل — the email service, templates and panel.

The tests here cover the things that fail quietly: a template that renders
broken markup, a password that reaches the browser, a delivery leg that takes
down a sign-up when it raises, and a code that stays alive after nobody
received it.
"""
import asyncio
import pytest


class TestTemplatesRender:
    """A template that renders wrong is invisible until a customer sees it."""

    @pytest.mark.parametrize("name", [
        "login_code", "welcome", "ticket_decision",
        "request_received", "notification", "test",
    ])
    def test_every_template_produces_a_complete_document(self, name):
        from app.api.routes.email import _SAMPLES
        subject, html, text = _SAMPLES[name]()

        assert subject and isinstance(subject, str)
        assert text and text.strip(), "a text/plain part is required"

        assert html.lstrip().startswith("<!DOCTYPE")
        assert html.rstrip().endswith("</html>")
        # balanced enough that a client will not guess at the structure
        assert html.count("<table") == html.count("</table>")
        assert html.count("<tr") == html.count("</tr>")
        assert html.count("<td") == html.count("</td>")

    @pytest.mark.parametrize("name", [
        "login_code", "welcome", "ticket_decision",
        "request_received", "notification", "test",
    ])
    def test_persian_and_rtl_are_declared(self, name):
        from app.api.routes.email import _SAMPLES
        _, html, _t = _SAMPLES[name]()
        assert 'dir="rtl"' in html
        assert 'lang="fa"' in html
        assert "charset=UTF-8" in html.replace("utf-8", "UTF-8")

    def test_the_palette_is_the_site_palette(self):
        """If these drift, the email stops looking like the product."""
        from app.services import email_templates as t
        from app import error_pages

        page = error_pages.render_not_found("/x")
        for colour in (t.BG, t.VIOLET, t.PINK, t.CYAN):
            assert colour in page, f"{colour} is not in the site's own pages"

    def test_no_webfont_request(self):
        """Mail clients block them, and a blocked request means a fallback
        nobody chose. error_pages.py already made this decision."""
        from app.api.routes.email import _SAMPLES
        _, html, _t = _SAMPLES["welcome"]()
        assert "fonts.googleapis.com" not in html
        assert "@import" not in html
        assert "Vazirmatn" in html and "Tahoma" in html

    def test_no_inline_svg_or_background_clip(self):
        """Outlook renders neither. The brand mark is a text wordmark over a
        gradient bar with a solid colour underneath for exactly this reason."""
        from app.api.routes.email import _SAMPLES
        for name in ("welcome", "login_code"):
            _, html, _t = _SAMPLES[name]()
            assert "<svg" not in html.lower()
            assert "background-clip" not in html.lower()

    def test_the_gradient_has_a_solid_fallback(self):
        from app.api.routes.email import _SAMPLES
        _, html, _t = _SAMPLES["welcome"]()
        i = html.index("background-image:linear-gradient")
        # the solid colour must be declared on the same element, before it
        assert "background-color:" in html[max(0, i - 200):i]

    def test_the_code_is_latin_and_ltr(self):
        """The recipient retypes it. Persian numerals would have to be
        converted back in their head.

        Checks the *rendered* occurrence, not the first one — the code also
        appears in the hidden inbox-preview line, which needs no direction.
        """
        from app.services import email_templates as t
        _, html, text = t.login_code("482913")
        assert "482913" in html and "482913" in text
        i = html.rindex("482913")
        assert 'dir="ltr"' in html[max(0, i - 300):i]
        # and no Persian digits anywhere in the block that shows it
        assert not set("۰۱۲۳۴۵۶۷۸۹") & set(html[i - 300:i + 10])

    def test_the_preheader_is_set_and_hidden(self):
        from app.api.routes.email import _SAMPLES
        _, html, _t = _SAMPLES["login_code"]()
        assert "display:none" in html
        assert "max-height:0" in html


class TestPasswordHandling:
    def test_round_trip(self):
        from app.services import secret_box
        assert secret_box.decrypt(secret_box.encrypt("abcd efgh ijkl mnop")) == "abcd efgh ijkl mnop"

    def test_an_undecryptable_value_reads_as_absent(self):
        from app.services import secret_box
        assert secret_box.decrypt("garbage") == ""

    def test_the_mask_hides_the_middle(self):
        from app.services import secret_box
        m = secret_box.mask("abcdefghijklmnop")
        assert m.startswith("abcd") and m.endswith("mnop") and "efghijkl" not in m

    @pytest.mark.asyncio
    async def test_the_settings_route_returns_only_a_mask(self, monkeypatch):
        """Asserted against the actual response rather than the source: the
        real invariant is that the password does not appear in what the
        browser receives, however the code is written."""
        from app.api.routes import email as r
        from app.services import email_service

        SECRET = "hjkl-qwer-tyui-zxcv"

        async def cfg(db=None):
            return {"host": "smtp.gmail.com", "port": 587,
                    "user": "sorinflow.agency@gmail.com", "password": SECRET,
                    "from_name": "سورین‌فلو", "security": "starttls",
                    "reply_to": "", "enabled": True, "source": "env"}
        monkeypatch.setattr(email_service, "resolve_config", cfg)

        out = await r.get_email_settings(db=None, _=None)
        assert SECRET not in str(out), "the raw password reached the response"
        assert out["password_masked"].startswith("hjkl")
        assert out["configured"] is True

    def test_the_sms_panel_shares_the_same_box(self):
        """Two copies of this would drift, and one of them would be the wrong
        one to fix."""
        from app.services import sms_service, secret_box
        assert sms_service._encrypt is secret_box.encrypt
        assert sms_service.mask_key is secret_box.mask


class TestDeliveryIsFailSoft:
    """A delivery leg that raises must not take down the sign-up form.

    This is not hypothetical: passing a new keyword to send_sms raised
    TypeError inside the request and hung it, rather than reporting that the
    code could not be sent.
    """

    @pytest.mark.asyncio
    async def test_a_raising_sms_leg_does_not_escape(self, monkeypatch):
        import app.services.verification as v

        async def boom(*a, **kw):
            raise TypeError("signature changed under us")
        monkeypatch.setattr(v, "send_sms", boom)

        used = await v._deliver("123456", phone="09121112233", email=None,
                                channel="sms", message_template=None, ttl=300)
        assert used is None       # reported as "not delivered", not raised

    @pytest.mark.asyncio
    async def test_a_raising_email_leg_does_not_escape(self, monkeypatch):
        import app.services.verification as v
        from app.services import email_service

        async def boom(*a, **kw):
            raise RuntimeError("smtp exploded")
        monkeypatch.setattr(email_service, "send", boom)

        used = await v._deliver("123456", phone="", email="a@b.com",
                                channel="email", message_template=None, ttl=300)
        assert used is None

    @pytest.mark.asyncio
    async def test_it_falls_through_to_the_other_channel(self, monkeypatch):
        """A sign-up that dies because one provider is down is a lost
        customer."""
        import app.services.verification as v
        from app.services import email_service

        async def dead_sms(*a, **kw):
            return {"success": False, "response": "no credit"}
        async def live_email(*a, **kw):
            return {"success": True, "message_id": "<x@y>"}
        monkeypatch.setattr(v, "send_sms", dead_sms)
        monkeypatch.setattr(email_service, "send", live_email)

        used = await v._deliver("123456", phone="09121112233", email="a@b.com",
                                channel=None, message_template=None, ttl=300)
        assert used == "email"

    @pytest.mark.asyncio
    async def test_an_invalid_address_is_not_attempted(self, monkeypatch):
        import app.services.verification as v
        from app.services import email_service

        called = {"n": 0}
        async def counting(*a, **kw):
            called["n"] += 1
            return {"success": True}
        monkeypatch.setattr(email_service, "send", counting)

        used = await v._deliver("123456", phone="", email="not-an-address",
                                channel="email", message_template=None, ttl=300)
        assert used is None and called["n"] == 0


class TestSendGuards:
    @pytest.mark.asyncio
    async def test_send_refuses_a_bad_address_without_connecting(self):
        from app.services import email_service
        out = await email_service.send("nope", "s", "<p>h</p>")
        assert out["success"] is False and "معتبر" in out["error"]

    @pytest.mark.asyncio
    async def test_send_refuses_when_unconfigured(self, monkeypatch):
        from app.services import email_service

        async def empty(db=None):
            return {"host": "", "port": 587, "user": "", "password": "",
                    "from_name": "x", "security": "starttls", "reply_to": "",
                    "enabled": True, "source": None}
        monkeypatch.setattr(email_service, "resolve_config", empty)
        out = await email_service.send("a@b.com", "s", "<p>h</p>")
        assert out["success"] is False

    @pytest.mark.asyncio
    async def test_send_never_raises_even_when_smtp_explodes(self, monkeypatch):
        """verification.py burns the one-time code when delivery fails. An
        exception escaping here would skip that and leave a live code nobody
        received."""
        from app.services import email_service

        async def cfg(db=None):
            return {"host": "localhost", "port": 1, "user": "u@x.com",
                    "password": "p", "from_name": "x", "security": "none",
                    "reply_to": "", "enabled": True, "source": "env"}
        monkeypatch.setattr(email_service, "resolve_config", cfg)

        def explode(*a, **kw):
            raise ConnectionRefusedError("nothing listening")
        monkeypatch.setattr(email_service, "_sync_send", explode)

        out = await email_service.send("a@b.com", "s", "<p>h</p>")
        assert out["success"] is False and out["error"]

    def test_auth_errors_name_the_app_password(self):
        """"SMTPAuthenticationError" tells nobody that Gmail needs an App
        Password rather than the account password."""
        import smtplib
        from app.services.email_service import _explain
        msg = _explain(smtplib.SMTPAuthenticationError(535, b"bad"))
        assert "App Password" in msg

    def test_the_password_never_reaches_a_log_line(self):
        import inspect
        from app.services import email_service
        src = inspect.getsource(email_service.send)
        assert 'replace(cfg["password"], "***")' in src


class TestPanelWiring:
    def test_router_registered_and_gated(self):
        import inspect
        from app.api import routes
        src = inspect.getsource(routes)
        assert 'prefix="/email"' in src and '_perm("email")' in src

    def test_permission_in_catalog(self):
        from app.auth.permissions import PERMISSIONS
        assert PERMISSIONS.get("email") == "ایمیل"

    def test_section_registered_everywhere(self):
        from pathlib import Path
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        assert 'id="nav-link-email"' in html
        assert 'id="section-email"' in html
        assert "'nav-link-email'" in js
        assert "email: 'email'" in js
        assert "email:      { title:" in js
        assert "case 'email':" in js

    def test_every_endpoint_the_ui_calls_exists(self):
        import re
        from pathlib import Path
        import app.main as m

        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        called = set()
        for match in re.finditer(r"apiCall\(\s*[`'\"](/email/[^`'\"?$]+)", js):
            called.add(match.group(1).rstrip("/"))
        served = {r.path[len("/api"):] for r in m.app.routes
                  if getattr(r, "path", "").startswith("/api/email")}
        # the preview route is templated in the UI, so compare its prefix
        missing = {c for c in called
                   if c not in served and not c.startswith("/email/preview")}
        assert not missing, f"UI calls endpoints that do not exist: {sorted(missing)}"


class TestVerificationReportsItsChannel:
    def test_issued_code_carries_the_channel(self):
        from app.services.verification import IssuedCode
        assert IssuedCode(ttl=1, cooldown=1).channel == "sms"

    def test_the_portal_message_names_the_channel(self):
        """Telling someone to check their SMS when it went to their inbox is
        how a sign-up is abandoned thirty seconds in."""
        import inspect
        from app.api.routes import public_auth
        src = inspect.getsource(public_auth._issued_response)
        assert "ایمیل" in src and "پیامک" in src


class TestErrorsNameTheMissingField:
    """«تنظیمات SMTP کامل نیست» cost an hour of guessing.

    The panel prefilled its host/user boxes with placeholder text that reads
    like a filled field, so the form looked complete while two boxes were
    empty — and the error named none of them.
    """

    @pytest.mark.parametrize("cfg,expect", [
        ({"host": "", "user": "", "password": "x"}, ["میزبان SMTP", "نام کاربری"]),
        ({"host": "h", "user": "u", "password": ""}, ["رمز عبور"]),
        ({"host": "", "user": "", "password": ""}, ["میزبان SMTP", "نام کاربری", "رمز عبور"]),
        ({"host": "h", "user": "u", "password": "p"}, []),
    ])
    def test_it_lists_exactly_what_is_blank(self, cfg, expect):
        from app.services.email_service import _missing_fields
        assert _missing_fields(cfg) == expect

    @pytest.mark.asyncio
    async def test_the_send_error_names_them(self, monkeypatch):
        from app.services import email_service

        async def cfg(db=None):
            return {"host": "", "user": "", "password": "x", "port": 587,
                    "from_name": "x", "security": "starttls", "reply_to": "",
                    "from_email": "", "enabled": True, "source": None}
        monkeypatch.setattr(email_service, "resolve_config", cfg)
        out = await email_service.send("a@b.com", "s", "<p>h</p>")
        assert out["success"] is False
        assert "میزبان SMTP" in out["error"] and "نام کاربری" in out["error"]

    def test_the_panel_prefills_rather_than_placeholders(self):
        """A grey placeholder renders close enough to a real value that the
        form looked complete. The Gmail defaults are values now."""
        from pathlib import Path
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert "d.host || 'smtp.gmail.com'" in js
        assert "d.port || 587" in js

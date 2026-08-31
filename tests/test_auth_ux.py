"""
Login and registration UX.

Checked against the practices in authgear.com/post/login-signup-ux-guide and
the 16-point checklist in the Medium article Sobhan supplied. These assert
structure rather than appearance — the point is that the affordances exist and
stay wired, not that they are a particular colour.

Both surfaces are covered: the staff panel (frontend/index.html) and the
customer portal (frontend/portal.html), because they are separate documents
that drifted apart before.
"""
from pathlib import Path

import pytest

PANEL_HTML = Path("frontend/index.html")
PANEL_JS = Path("frontend/js/app.js")
PORTAL_HTML = Path("frontend/portal.html")
PORTAL_JS = Path("frontend/js/portal.js")
CSS = Path("frontend/css/style.css")


def _read(p):
    return p.read_text(encoding="utf-8")


class TestFormsAreRealForms:
    """Loose divs with onclick give neither Enter-to-submit nor a password
    manager's offer to save. Both matter more than any styling here."""

    def test_panel_auth_steps_are_form_elements(self):
        html = _read(PANEL_HTML)
        for step in ("login-step-1", "login-step-2", "login-step-register"):
            i = html.index(f'id="{step}"')
            tag = html.rfind("<", 0, i)
            assert html[tag:tag + 5] == "<form", f"{step} is not a <form>"

    def test_portal_panes_are_forms(self):
        html = _read(PORTAL_HTML)
        for pane in ("pane-login", "pane-register", "pane-verify"):
            i = html.index(f'id="{pane}"')
            tag = html.rfind("<", 0, i)
            assert html[tag:tag + 5] == "<form", f"{pane} is not a <form>"


class TestLabelsNotPlaceholders:
    """A placeholder disappears the moment someone types, so an interrupted
    user loses the question — and screen readers treat it as a hint, not a
    name. Labels are also a bigger click target."""

    def test_every_panel_auth_input_has_a_bound_label(self):
        import re
        html = _read(PANEL_HTML)
        block = html[html.index('id="login-step-1"'):html.index('id="login-toggle-link"')]
        ids = set(re.findall(r'<input[^>]*id="([^"]+)"', block))
        labelled = set(re.findall(r'<label[^>]*for="([^"]+)"', block))
        # checkboxes are wrapped by their own label; the rest need for=
        unlabelled = {i for i in ids - labelled if "remember" not in i}
        assert not unlabelled, f"inputs with no label: {sorted(unlabelled)}"

    def test_portal_labels_point_at_real_inputs(self):
        import re
        html = _read(PORTAL_HTML)
        for target in re.findall(r'<label[^>]*for="([^"]+)"', html):
            assert f'id="{target}"' in html, f"label points at missing #{target}"


class TestPasswordVisibility:
    """Point 3 of the checklist. Also: paste must keep working — blocking it
    breaks password managers, which hold most people's strongest passwords."""

    def test_both_surfaces_offer_a_reveal_toggle(self):
        assert 'class="pw-toggle"' in _read(PANEL_HTML)
        assert 'class="pw-eye"' in _read(PORTAL_HTML)

    def test_the_toggle_is_a_button_not_a_span(self):
        """An icon-only span is invisible to keyboard and screen-reader users."""
        html = _read(PANEL_HTML)
        i = html.index('class="pw-toggle"')
        tag = html.rfind("<", 0, i)
        assert html[tag:tag + 7] == "<button"
        assert "aria-label" in html[i:i + 260]

    def test_paste_is_not_blocked(self):
        for f in (PANEL_HTML, PORTAL_HTML, PANEL_JS, PORTAL_JS):
            src = _read(f)
            assert "onpaste" not in src.lower(), f"{f} interferes with paste"
            assert "preventDefault" not in src.split("paste")[0][-80:] if "paste" in src else True


class TestCapsLockAndStrength:
    """Points 8 and 9. Silent capitals are the commonest unexplainable login
    failure; undisclosed password rules are the commonest signup dead end."""

    def test_caps_lock_is_surfaced_on_both(self):
        assert "getModifierState" in _read(PANEL_JS)
        assert "getModifierState" in _read(PORTAL_JS)
        assert 'id="caps-login"' in _read(PANEL_HTML)
        assert 'id="caps-li"' in _read(PORTAL_HTML)

    def test_password_rules_are_shown_before_they_are_enforced(self):
        for html, rules_id in ((_read(PANEL_HTML), "pw-rules"),
                               (_read(PORTAL_HTML), "rg-rules")):
            assert f'id="{rules_id}"' in html
            block = html[html.index(f'id="{rules_id}"'):][:700]
            for rule in ("len", "lower", "digit", "upper"):
                assert f'data-rule="{rule}"' in block

    def test_strength_is_composition_not_an_opaque_score(self):
        """A number someone cannot influence is not guidance."""
        for js in (_read(PANEL_JS), _read(PORTAL_JS)):
            assert "len:" in js and "lower:" in js and "digit:" in js and "upper:" in js


class TestErrorsCarryARecovery:
    """The guide's error→recovery mapping. A dead end is what makes someone
    abandon a login."""

    def test_field_errors_are_announced(self):
        assert _read(PANEL_HTML).count('class="field-error"') >= 5
        assert 'role="alert"' in _read(PANEL_HTML)
        assert _read(PORTAL_HTML).count('class="f-err"') >= 5

    def test_a_failed_login_does_not_say_which_half_was_wrong(self):
        """Naming the username tells an anonymous caller the account exists."""
        js = _read(PANEL_JS)
        fn = js.split("function _explainLoginFailure")[1].split("\nfunction ")[0]
        assert "نام کاربری یا رمز عبور نادرست است" in fn
        assert "رمز عبور اشتباه است" not in fn

    def test_inputs_survive_a_failure(self):
        """Retyping a whole form because one field was wrong is the single most
        irritating thing a login can do."""
        js = _read(PANEL_JS)
        fn = js.split("async function doLogin()")[1].split("\n}\n")[0]
        assert ".value = ''" not in fn, "doLogin clears an input on failure"

    def test_lockout_and_server_errors_are_distinguished(self):
        js = _read(PANEL_JS)
        fn = js.split("function _explainLoginFailure")[1].split("\nfunction ")[0]
        assert "429" in fn and "status >= 500" in fn

    def test_an_existing_account_becomes_a_sign_in_not_a_dead_end(self):
        js = _read(PANEL_JS)
        fn = js.split("async function doRegister()")[1].split("\n/** FastAPI")[0]
        assert "login-username" in fn, "the username is not carried over to the login form"

    def test_recovery_paths_exist_for_password_and_second_factor(self):
        html = _read(PANEL_HTML)
        assert "showForgotHelp()" in html
        assert "showTotpHelp()" in html
        js = _read(PANEL_JS)
        # honest: there is no self-service reset, so it must say who can do it
        assert "مدیر" in js.split("function showForgotHelp")[1][:400]


class TestNoObjectObject:
    """FastAPI sends `detail` as a list of objects on a 422. Passing that to
    new Error() renders "[object Object]" into the form."""

    def test_both_surfaces_unwrap_a_list_detail(self):
        assert "function detailText" in _read(PORTAL_JS)
        assert "function _detailText" in _read(PANEL_JS)

    def test_the_portal_api_helper_uses_it(self):
        js = _read(PORTAL_JS)
        fn = js.split("async function api(")[1].split("\n}\n")[0]
        assert "detailText(data.detail)" in fn
        assert "new Error(data.detail)" not in fn


class TestReturningUser:
    """Points 5, 11 and 12: autofocus, keep signed in, and greet someone we
    already know instead of showing them an empty form."""

    def test_remember_me_chooses_the_storage(self):
        js = _read(PANEL_JS)
        assert "sessionStorage" in js and "sf_remember" in js
        fn = js.split("function _tokenStore()")[1].split("\n}")[0]
        assert "sessionStorage" in fn, "unticking remember-me must not persist the session"

    def test_only_the_username_is_remembered(self):
        """Storing more than the username turns a convenience into a liability
        on a shared machine."""
        js = _read(PANEL_JS)
        fn = js.split("function _rememberUser")[1].split("\n}")[0]
        assert "sf_last_user" in fn
        assert "password" not in fn.lower()

    def test_the_greeting_can_be_dismissed(self):
        assert "forgetUser()" in _read(PANEL_HTML)

    def test_autofocus_is_skipped_on_touch(self):
        """Focusing raises the keyboard and hides the form being read."""
        for js in (_read(PANEL_JS), _read(PORTAL_JS)):
            assert "pointer: coarse" in js


class TestValidationHappensAsYouGo:
    """Point 7: completing six fields and then being told the second was wrong
    is the frustration this prevents."""

    def test_blur_validation_is_wired_on_both(self):
        assert "_validateOnBlur" in _read(PANEL_JS)
        assert "onBlur(" in _read(PORTAL_JS)

    def test_errors_clear_while_the_user_is_fixing_them(self):
        """A red field someone is actively correcting is just nagging."""
        panel = _read(PANEL_JS)
        fn = panel.split("function _validateOnBlur")[1].split("\n}")[0]
        assert "'input'" in fn and "setFieldError(fieldId, '')" in fn

    def test_maxlength_prevents_the_avoidable_422(self):
        html = _read(PORTAL_HTML)
        for field in ("rg-name", "rg-email", "rg-pass", "li-id", "li-pass"):
            i = html.index(f'id="{field}"')
            assert "maxlength" in html[max(0, i - 200):i + 200], f"{field} has no maxlength"


class TestOneTimeCode:
    def test_the_code_field_is_numeric_and_autofills(self):
        html = _read(PANEL_HTML)
        i = html.index('id="login-totp-code"')
        block = html[i:i + 400]
        assert 'autocomplete="one-time-code"' in block
        assert 'inputmode="numeric"' in block

    def test_it_submits_itself_when_complete(self):
        """There is nothing else a six-digit field could be waiting for."""
        assert "_wireOtp" in _read(PANEL_JS)

    def test_persian_digits_are_accepted(self):
        """A Persian keyboard produces ۰۱۲۳; the server wants ASCII."""
        js = _read(PANEL_JS)
        fn = js.split("function _wireOtp")[1].split("\n}")[0]
        assert "۰-۹" in fn or "۰۱۲۳۴۵۶۷۸۹" in fn

    def test_an_expired_code_says_so_and_names_the_usual_cause(self):
        js = _read(PANEL_JS)
        fn = js.split("async function verifyTotpLogin()")[1].split("\n}\n")[0]
        assert "منقضی" in fn
        assert "ساعت" in fn, "clock drift is the usual cause and should be named"


class TestTrustAndDifferentiation:
    """Points 1 and 16, plus the guide's trust signals."""

    def test_login_and_registration_are_visibly_different(self):
        html = _read(PORTAL_HTML)
        assert 'id="tab-login"' in html and 'id="tab-register"' in html
        assert "ثبت‌نام" in html and "ورود" in html

    def test_the_buttons_say_what_they_do(self):
        """"Continue" leaves someone unsure whether an account was created."""
        assert "ایجاد حساب کاربری" in _read(PANEL_HTML)
        assert "ثبت‌نام و دریافت کد" in _read(PORTAL_HTML)

    def test_both_surfaces_carry_a_trust_signal(self):
        assert "auth-trust" in _read(PANEL_HTML)
        assert 'class="trust"' in _read(PORTAL_HTML)

    def test_each_surface_links_to_the_other(self):
        """Someone on the wrong door should be shown the right one."""
        assert "/portal" in _read(PANEL_HTML)
        assert "/dashboard" in _read(PORTAL_HTML)


class TestStylesExist:
    def test_the_auth_components_are_styled(self):
        css = _read(CSS)
        for cls in (".auth-field", ".pw-toggle", ".field-error", ".caps-warn",
                    ".pw-rules", ".pw-meter", ".otp-input", ".auth-trust",
                    ".remember-row", ".welcome-back"):
            assert cls in css, f"{cls} has no styles"

    def test_focus_is_visible_for_keyboard_users(self):
        assert "focus-visible" in _read(CSS)


class TestAnimatedMark:
    """The Lottie mark is decoration, and must behave like decoration."""

    def test_the_player_is_vendored_not_fetched_from_a_cdn(self):
        """The site makes no third-party requests — that was a deliberate
        change, and it matters on a network where a CDN may be slow or
        blocked."""
        js = _read(PANEL_JS)
        assert "cdn.jsdelivr" not in js and "unpkg.com" not in js
        assert Path("frontend/js/vendor/lottie_light.min.js").exists()
        assert Path("frontend/js/vendor/lottie-web.LICENSE.md").exists(), \
            "vendored code must ship its licence"

    def test_the_animation_is_valid_lottie(self):
        import json
        doc = json.loads(Path("frontend/assets/auth-mark.json").read_text(encoding="utf-8"))
        for key in ("v", "fr", "ip", "op", "w", "h", "layers"):
            assert key in doc, f"missing {key}"
        assert doc["layers"], "no layers"
        # every layer must carry a shape group with real geometry — an earlier
        # hand-authored version parsed fine and rendered a <path> with no `d`
        for layer in doc["layers"]:
            items = layer["shapes"][0]["it"]
            assert any(i["ty"] in ("el", "rc", "sh") for i in items), \
                f"layer {layer['nm']} has no drawable shape"
            assert items[-1]["ty"] == "tr", "a shape group must end with its transform"

    def test_it_stays_small_enough_to_be_an_ornament(self):
        size = Path("frontend/assets/auth-mark.json").stat().st_size
        assert size < 60_000, f"{size} bytes is too heavy for decoration"

    def test_it_is_loaded_lazily_and_never_blocks_the_form(self):
        js = _read(PANEL_JS)
        fn = js.split("function _initAuthMark()")[1].split("\n}\n")[0]
        assert "async" in fn, "the player must not block parsing"
        assert "requestIdleCallback" in js

    def test_motion_and_data_preferences_are_honoured(self):
        js = _read(PANEL_JS)
        fn = js.split("function _initAuthMark()")[1].split("\n}\n")[0]
        assert "prefers-reduced-motion" in fn
        assert "saveData" in fn
        assert "pointer: coarse" in fn, "a small screen belongs to the form"

    def test_the_static_icon_remains_as_the_fallback(self):
        """If the player never loads, the card must not have a hole in it."""
        html = _read(PANEL_HTML)
        i = html.index('id="auth-lottie"')
        assert "logo-icon" in html[i:i + 300]
        css = _read(CSS)
        assert ".auth-lottie.ready" in css, "the icon is replaced only once loaded"

    def test_the_animation_url_is_versioned(self):
        """A stale cached animation cost real debugging time once."""
        assert "auth-mark.json?v=" in _read(PANEL_JS)

"""
Static wiring checks for the dashboard and portal JavaScript.

There is no JS test runner here, and the Python suite never loads app.js — so a
function deleted by an edit stays invisible until someone clicks the thing. That
is exactly what happened: rewriting loadUsers() silently swallowed createUser,
toggleUserActive and promptResetPassword. initApp() binds createUser to the
new-user form, so login threw before showSection() ran and the panel kept
whatever section the previous session had open.

These parse the sources and assert that every function the markup and the app
call actually exists.
"""
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(BASE, "frontend", "js", "app.js")
PORTAL_JS = os.path.join(BASE, "frontend", "js", "portal.js")
INDEX = os.path.join(BASE, "frontend", "index.html")
PORTAL_HTML = os.path.join(BASE, "frontend", "portal.html")

# Provided by the browser, bootstrap, or the other bundle on the page.
EXTERNAL = {
    "bootstrap", "Chart", "persianDate", "confirm", "alert", "prompt",
    "fetch", "console", "JSON", "Object", "Array", "Math", "Date", "Number",
    "String", "Promise", "setTimeout", "setInterval", "clearInterval",
    "localStorage", "document", "window", "location", "encodeURIComponent",
    "decodeURIComponent", "parseInt", "parseFloat", "isNaN", "URLSearchParams",
    "FormData", "Blob", "URL", "event", "this", "sendPrompt",
}


def _defined(src: str) -> set:
    names = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", src))
    names |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(", src))
    names |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?function", src))
    return names


# `if (...)` is a keyword, not a call; `x.foo()` is a method on some object and
# not something this file can resolve.
_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "typeof",
                "new", "delete", "void", "function", "await"}


def _called_in_html(html: str) -> set:
    """Bare function names invoked from an inline handler attribute."""
    out = set()
    for attr in re.findall(r'on\w+\s*=\s*"([^"]*)"', html):
        # (?<![.\w$]) keeps method calls like event.preventDefault() out
        out |= set(re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", attr))
    return out - _JS_KEYWORDS


def test_every_onclick_in_index_has_a_function():
    html = open(INDEX, encoding="utf-8").read()
    defined = _defined(open(APP_JS, encoding="utf-8").read())
    missing = sorted(_called_in_html(html) - defined - EXTERNAL)
    assert not missing, f"index.html calls undefined function(s): {missing}"


def test_every_onclick_in_portal_has_a_function():
    html = open(PORTAL_HTML, encoding="utf-8").read()
    defined = _defined(open(PORTAL_JS, encoding="utf-8").read())
    missing = sorted(_called_in_html(html) - defined - EXTERNAL)
    assert not missing, f"portal.html calls undefined function(s): {missing}"


def test_initapp_only_binds_functions_that_exist():
    """initApp runs on every login. A missing name throws there and aborts
    showMainApp() before it can switch sections."""
    src = open(APP_JS, encoding="utf-8").read()
    body = src[src.index("function initApp() {"):]
    body = body[:body.index("\n}")]
    referenced = set(re.findall(r"addEventListener\(\s*'[^']+'\s*,\s*([A-Za-z_$][\w$]*)\s*\)", body))
    referenced |= set(re.findall(r"^\s*([A-Za-z_$][\w$]*)\(\)", body, re.M))
    missing = sorted(referenced - _defined(src) - EXTERNAL)
    assert not missing, f"initApp references undefined function(s): {missing}"


def test_dashboard_functions_are_defined_exactly_once():
    """A duplicate definition silently wins over the earlier one."""
    src = open(APP_JS, encoding="utf-8").read()
    names = re.findall(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", src, re.M)
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"defined more than once in app.js: {dupes}"


def test_user_management_functions_survive():
    """Named explicitly because these are the ones an edit to loadUsers ate."""
    src = open(APP_JS, encoding="utf-8").read()
    defined = _defined(src)
    for fn in ("loadUsers", "createUser", "toggleUserActive", "promptResetPassword",
               "deleteUser", "promptSetDivarPhone", "openPermsEditor",
               "savePermsEditor", "renderPermBox", "readPermBox",
               "loadTickets", "decideTicket", "loadPortalRequests"):
        assert fn in defined, f"{fn} is missing from app.js"


# ── URLs from the database must not become script ───────────────────────────

def _app_js() -> str:
    with open(APP_JS, encoding="utf-8") as fh:
        return fh.read()


def test_listing_urls_go_through_safeUrl():
    """A stored «javascript:…» URL contains nothing that needs escaping, so
    esc() does not help: clicking the link runs it in the panel's origin, where
    the token lives. property_url is free text on the add-lead form."""
    import re
    js = _app_js()
    bad = re.findall(r'href="\$\{\s*(?:property\.url|lead\.property_url)\s*\}"', js)
    assert not bad, f"{len(bad)} listing link(s) interpolate a stored URL straight into href"
    assert "function safeUrl(" in js


def test_tel_links_go_through_safeTel():
    import re
    js = _app_js()
    bad = re.findall(r'href="tel:\$\{\s*(?!safeTel|esc)[a-z]', js)
    assert not bad, f"{len(bad)} tel: link(s) interpolate a raw phone number"


def test_note_content_is_escaped():
    """Notes are free text any staff member can write, rendered into the lead
    timeline that everyone opens."""
    js = _app_js()
    assert "${n.content}" not in js, "note body is interpolated without esc()"


# ── the monitoring section must be registered in all five places ────────────

def test_monitoring_section_is_fully_registered():
    """A section needs an entry in five places that must agree. Miss one and it
    either never appears, appears for the wrong role, or shows an empty div —
    which is how the monitoring backend sat finished but invisible."""
    js = _app_js()
    with open(INDEX, encoding="utf-8") as fh:
        html = fh.read()

    assert 'id="nav-link-monitoring"' in html, "no nav entry"
    assert 'id="section-monitoring"' in html, "no section container"
    assert "'nav-link-monitoring': 'monitoring'" in js, "not in NAV_PERMISSION"
    assert "monitoring: 'monitoring'" in js, "not in SECTION_PERMISSION"
    assert "monitoring: { title:" in js, "not in SECTION_META"
    assert "case 'monitoring':" in js, "showSection has no case, so it renders empty"


def test_monitoring_uses_the_panels_own_card_styles():
    """It should look like the rest of the dashboard, not like a bolted-on
    admin page — same stat-card pattern as the KPI row."""
    with open(INDEX, encoding="utf-8") as fh:
        html = fh.read()
    section = html[html.index('id="section-monitoring"'):html.index('id="section-portal"')]
    for cls in ("stat-card", "stat-icon", "stat-value", "stat-label", "stat-bg-icon"):
        assert cls in section, f"monitoring tiles do not use {cls}"


def test_log_lines_are_escaped_before_rendering():
    """A log line is the least trustworthy string in the product, and this view
    renders it straight into the page."""
    js = _app_js()
    fn = js[js.index("async function loadMonitoringLogs"):]
    fn = fn[:fn.index("\n}")]
    assert "esc(l)" in fn, "log lines reach innerHTML without esc()"


def test_live_polling_stops_when_the_screen_is_hidden():
    """A 5s poll against a single-replica box for a page nobody is looking at
    is pure load. The interval has to check and clear itself."""
    js = _app_js()
    fn = js[js.index("function startLive()"):]
    fn = fn[:fn.index("function stopLive")]
    assert "style.display === 'none'" in fn and "stopLive()" in fn, (
        "the live interval never stops itself when the section is hidden")


def test_live_rates_are_derived_from_two_samples():
    """Counters are monotonic — showing them raw would display 'requests since
    boot' and call it a rate."""
    js = _app_js()
    fn = js[js.index("async function tickLive"):js.index("function startLive")]
    assert "_livePrev" in fn and "s.ts - _livePrev.ts" in fn, (
        "live view does not difference consecutive samples")

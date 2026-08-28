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

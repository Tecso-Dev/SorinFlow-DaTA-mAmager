"""
CRM calendar: booking a visit against a lead.

The event was committed, then `await _log_activity(...)` raised TypeError
because _log_activity is a plain def. The caller got a 500 for a visit that had
in fact been saved — so the natural response, booking it again, produced a
duplicate. scheduleVisitForLead() in the dashboard always sets lead_id, so this
was the normal path, not an edge case.
"""
import ast
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_cal.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

CRM = pathlib.Path(__file__).parent.parent / "app" / "api" / "routes" / "crm.py"


def _tree():
    return ast.parse(CRM.read_text(encoding="utf-8"))


def test_log_activity_is_never_awaited():
    """The specific bug: three call sites awaited a synchronous function."""
    tree = _tree()
    offenders = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
        and (getattr(n.value.func, "id", None)
             or getattr(n.value.func, "attr", None)) == "_log_activity"
    ]
    assert not offenders, (
        f"_log_activity is awaited at line(s) {offenders}. It is a plain def, so "
        "`await` on its None return raises TypeError — after the caller has "
        "already committed its work.")


def test_log_activity_is_still_synchronous():
    """If someone makes it async later, the call sites must change with it.
    This fails in that case, which is the point — it forces the decision."""
    tree = _tree()
    defs = [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_log_activity"]
    assert len(defs) == 1, "expected exactly one _log_activity definition"
    assert isinstance(defs[0], ast.FunctionDef), (
        "_log_activity became async — every call site must now await it, "
        "and this test should be updated deliberately rather than deleted")


def test_no_await_on_any_sync_helper_in_crm():
    """The general form. `await` on a plain def is always a TypeError at
    runtime, and always somewhere the tests were not looking."""
    tree = _tree()
    sync = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    asyncs = {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    sync_only = sync - asyncs

    offenders = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Await) and isinstance(n.value, ast.Call):
            name = getattr(n.value.func, "id", None) or getattr(n.value.func, "attr", None)
            if name in sync_only:
                offenders.append(f"line {n.lineno}: await {name}(...)")
    assert not offenders, "awaiting synchronous helpers:\n  " + "\n  ".join(offenders)


def test_the_whole_app_is_free_of_this_bug():
    """Same check across every module, so the next one is caught wherever it
    lands rather than only in the file that happened to break first."""
    app_dir = pathlib.Path(__file__).parent.parent / "app"
    offenders = []
    for path in sorted(app_dir.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue                      # not ours to police here
        sync = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        asyncs = {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
        sync_only = sync - asyncs
        for n in ast.walk(tree):
            if isinstance(n, ast.Await) and isinstance(n.value, ast.Call):
                name = getattr(n.value.func, "id", None) or getattr(n.value.func, "attr", None)
                if name in sync_only:
                    rel = path.relative_to(app_dir.parent)
                    offenders.append(f"{rel}:{n.lineno} await {name}(...)")
    assert not offenders, "awaiting synchronous functions:\n  " + "\n  ".join(offenders)

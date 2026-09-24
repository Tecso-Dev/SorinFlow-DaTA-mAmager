"""
Who sees what — the one place the rules live.

The panel's routes and the Telegram assistant ask the same question — may
this person see this row? — and must never answer it two ways, so the
rules are here and everything else imports them (CLAUDE.md: «منطق چه کسی
چه چیزی را می‌بیند فقط در یک helper یا dependency مشترک باشد»).

Ownership is by name, as it always has been: the name a person files a
listing under, is assigned leads and tasks under and consults customers
under is their full_name, else their username. root and super_admin see
everything.

Each helper takes a select() and returns it narrowed, so a caller keeps
its own columns, joins and ordering.
"""
from typing import Optional

from sqlalchemy import and_, or_

from app.auth.permissions import FULL_ACCESS_ROLES
from app.models.crm_models import Cabinet, Customer, CustomerMatch, Task
from app.models.property import Property


def actor(user) -> Optional[str]:
    """The name a person files, is assigned and consults under."""
    return (getattr(user, "full_name", None) or getattr(user, "username", None)) if user else None


def is_super(user) -> bool:
    # root outranks super_admin everywhere else; it must not be the one
    # account that cannot see a private file (roadmap #11)
    return getattr(user, "role", None) in FULL_ACCESS_ROLES


# ── the panel's rules, moved here unchanged ──────────────────────────────────

def files_visible_to(query, user):
    """Private files belong to whoever filed them (and to a super_admin)."""
    if is_super(user):
        return query
    me = actor(user)
    if not me:
        # No name to match on — «شخصی» must mean hidden, not "matches NULL"
        return query.where(Property.is_private == False)     # noqa: E712
    return query.where(or_(Property.is_private == False,      # noqa: E712
                           Property.created_by == me))


def cabinets_visible_to(query, user):
    """A کمد شخصی belongs to whoever made it; a cabinet with no owner is
    the agency's and everyone sees it."""
    if is_super(user):
        return query
    me = actor(user)
    if not me:
        return query.where(Cabinet.owner.is_(None))
    return query.where(or_(Cabinet.owner.is_(None), Cabinet.owner == me))


def matches_visible_to(query, user):
    """A consultant sees the matches for their own customers (and for
    customers nobody is assigned to); root and super_admin see everybody's."""
    if is_super(user):
        return query
    return query.where(or_(CustomerMatch.consultant.is_(None), CustomerMatch.consultant == "",
                           CustomerMatch.consultant == actor(user)))


def tasks_visible_to(query, user):
    """A super_admin sees the whole board; everyone else sees only their own.

    Tasks with no assignee stay visible to all, because they predate this rule
    and hiding them would orphan them — new tasks are stamped with their
    creator on the way in, so the unassigned set only ever shrinks.
    """
    if is_super(user):
        return query
    me = actor(user)
    if not me:
        return query.where(Task.assigned_to.is_(None))
    return query.where(or_(Task.assigned_to.is_(None), Task.assigned_to == me))


# ── the assistant's rules ────────────────────────────────────────────────────
# Narrower than the panel's lists on purpose: the properties list and the
# customers list show everything to anyone holding the permission, and that
# stays so. What the assistant repeats in a chat is held to «yours, or
# nobody's».

def listings_visible_to(query, user):
    """A private file and a draft are their filer's alone (and a
    super_admin's); every other listing is everybody's."""
    if is_super(user):
        return query
    shared = and_(Property.is_private == False, Property.is_draft == False)   # noqa: E712
    me = actor(user)
    # no name: nothing is "mine", rather than every file nobody signed
    return query.where(or_(shared, Property.created_by == me) if me else shared)


def customers_visible_to(query, user):
    """A consultant's own customers and the ones nobody is assigned to —
    the name rule the matches use; root and super_admin see all."""
    if is_super(user):
        return query
    return query.where(or_(Customer.consultant_name.is_(None), Customer.consultant_name == "",
                           Customer.consultant_name == actor(user)))

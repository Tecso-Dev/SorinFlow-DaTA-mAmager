"""
Who sees what — the one place the rules live.

The panel's routes and the Telegram assistant ask the same question — may
this person see this row? — and must never answer it two ways, so the
rules are here and everything else imports them (CLAUDE.md: «منطق چه کسی
چه چیزی را می‌بیند فقط در یک helper یا dependency مشترک باشد»).

Ownership is by name, as it always has been: the name a person files a
listing under, is assigned leads and tasks under and consults customers
under is their full_name, else their username. root and super_admin see
everything. Beside each name, every owned row now also records the account
that name meant when it was written (OWNERSHIP below) — the column the
checks are moving to.

Each helper takes a select() and returns it narrowed, so a caller keeps
its own columns, joins and ordering.
"""
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import and_, func, or_, select

from app.auth.permissions import FULL_ACCESS_ROLES, STAFF_ROLES
from app.models.crm_models import Cabinet, Customer, CustomerMatch, Task
from app.models.property import Property
from app.models.user import User


def actor(user) -> Optional[str]:
    """The name a person files, is assigned and consults under — for display."""
    return (getattr(user, "full_name", None) or getattr(user, "username", None)) if user else None


def is_super(user) -> bool:
    # root outranks super_admin everywhere else; it must not be the one
    # account that cannot see a private file (roadmap #11)
    return getattr(user, "role", None) in FULL_ACCESS_ROLES


# ── who owns a row ────────────────────────────────────────────────────────────
# table → (the name column, the account column). Each of these tables also
# has owner_resolved_from: the name the account column was last resolved
# from. A row whose name no longer equals it was written by code that does
# not know about accounts — the release before this one, during a rolling
# deploy or after a rollback — and backfill_owner_ids resolves exactly those
# rows and no other: a row whose owner was deleted, or whose name meant
# nobody or two people, stays nobody's whatever anyone renames themselves to.
OWNERSHIP = {
    "properties": ("created_by", "created_by_user_id"),
    "crm_cabinets": ("owner", "owner_user_id"),
    "crm_customer_matches": ("consultant", "consultant_user_id"),
    "crm_tasks": ("assigned_to", "assigned_to_user_id"),
    "leads": ("assigned_to", "assigned_to_user_id"),
    "crm_customers": ("consultant_name", "consultant_user_id"),
}
RESOLVED = "owner_resolved_from"


def display_name(full_name, username):
    """actor() in SQL: full_name unless it is empty, else username — exact,
    untrimmed and case-sensitive, the way every name was ever compared."""
    return func.coalesce(func.nullif(full_name, ""), username)


def stamp_owner(row, name: Optional[str], user_id: Optional[int]) -> None:
    """Write both halves of a row's owner, and the name the account is for."""
    name_col, id_col = OWNERSHIP[row.__tablename__]
    setattr(row, name_col, name)
    setattr(row, id_col, user_id)
    setattr(row, RESOLVED, name)


def stamp_actor(row, user) -> None:
    """The person doing this owns the row."""
    stamp_owner(row, actor(user), getattr(user, "id", None))


async def owner_id_for(db, name: Optional[str]) -> Optional[int]:
    """The one staff account that goes by `name`, or None when nobody or
    more than one does. Staff only: a visitor never reaches these rows, and
    their names must not make a colleague's ambiguous."""
    if not name:
        return None
    ids = (await db.execute(select(User.id).where(
        User.role.in_(STAFF_ROLES),
        display_name(User.full_name, User.username) == name).limit(2))).scalars().all()
    return ids[0] if len(ids) == 1 else None


async def assign_owner(db, row, name: Optional[str], by=None) -> None:
    """A name typed into a form. Resolved to an account only when it is a
    different name: a form sends back the name it showed, and a renamed
    owner must keep the row. The person saving is who they name as
    themselves, even if a colleague shares the name."""
    if getattr(row, RESOLVED) == name:
        return
    if by is not None and name and name == actor(by):
        stamp_actor(row, by)
    else:
        stamp_owner(row, name, await owner_id_for(db, name))


def backfill_owner_ids(conn) -> int:
    """Give every row whose name changed behind this code's back the account
    that name means — Alembic 0016 over the whole table, and a boot step for
    what the previous release writes during a rolling deploy.

    A sync Connection, so Alembic's op.get_bind() and init_db's run_sync can
    share it. Plain SQL on both dialects; a table not yet carrying the
    columns (a boot before 0016 ran) is skipped. Returns rows touched.
    """
    users = sa.table("users", sa.column("id"), sa.column("full_name"),
                     sa.column("username"), sa.column("role"))
    insp = sa.inspect(conn)
    touched = 0
    for table, (name_col, id_col) in OWNERSHIP.items():
        if not insp.has_table(table):
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        if not {name_col, id_col, RESOLVED} <= cols:
            continue
        t = sa.table(table, sa.column(name_col), sa.column(id_col), sa.column(RESOLVED))
        name = t.c[name_col]
        # one match, or NULL: MIN over the matches, kept only when there is one
        match = (sa.select(func.min(users.c.id))
                 .where(users.c.role.in_(sorted(STAFF_ROLES)),
                        display_name(users.c.full_name, users.c.username) == name)
                 .having(func.count() == 1)
                 .scalar_subquery())
        touched += conn.execute(
            sa.update(t)
            .where(func.coalesce(t.c[RESOLVED], "") != func.coalesce(name, ""))
            .values({id_col: match, RESOLVED: name})).rowcount or 0
    return touched


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

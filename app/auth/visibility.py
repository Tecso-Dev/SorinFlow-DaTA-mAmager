"""
Who sees what — the one place the rules live.

The panel's routes and the Telegram assistant ask the same question — may
this person see this row? — and must never answer it two ways, so the
rules are here and everything else imports them (CLAUDE.md: «منطق چه کسی
چه چیزی را می‌بیند فقط در یک helper یا dependency مشترک باشد»).

Ownership is by account id. Every owned row keeps two things side by side
(OWNERSHIP below): the name it was filed, assigned or consulted under — the
display name, full_name else username, shown on screen and still read by
the release before this one — and the account that name meant when it was
written. Every check reads the account, so a person's display name can
change, or be taken by somebody else, without one row changing hands. root
and super_admin see everything; a row whose name meant nobody, or more than
one person, belongs to nobody, and only they see it.

Each *_visible_to helper takes a select() and returns it narrowed, so a
caller keeps its own columns, joins and ordering.
"""
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import and_, false, func, or_, select

from app.auth.permissions import FULL_ACCESS_ROLES, STAFF_ROLES
from app.models.crm_models import ActivityLog, Cabinet, Customer, CustomerMatch, Task
from app.models.lead import Lead
from app.models.property import Property
from app.models.user import User


def actor(user) -> Optional[str]:
    """The name a person files, is assigned and consults under — for display."""
    return (getattr(user, "full_name", None) or getattr(user, "username", None)) if user else None


def is_super(user) -> bool:
    # root outranks super_admin everywhere else; it must not be the one
    # account that cannot see a private file (roadmap #11)
    return getattr(user, "role", None) in FULL_ACCESS_ROLES


def _mine(column, user):
    """`column` names this user's account — never true without one, so a
    missing user cannot match every row nobody owns (column == None would
    render IS NULL)."""
    uid = getattr(user, "id", None)
    return column == uid if uid is not None else false()


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


def stamp_owner(row, name, user_id) -> None:
    """Write both halves of a row's owner, and the name the account is for."""
    name_col, id_col = OWNERSHIP[row.__tablename__]
    setattr(row, name_col, name)
    setattr(row, id_col, user_id)
    setattr(row, RESOLVED, name)


def stamp_actor(row, user) -> None:
    """The person doing this owns the row."""
    stamp_owner(row, actor(user), getattr(user, "id", None))


async def owner_id_for(db, name) -> Optional[int]:
    """The one staff account that goes by `name`, or None when nobody or
    more than one does. Staff only: a visitor never reaches these rows, and
    their names must not make a colleague's ambiguous."""
    if not name:
        return None
    ids = (await db.execute(select(User.id).where(
        User.role.in_(STAFF_ROLES),
        display_name(User.full_name, User.username) == name).limit(2))).scalars().all()
    return ids[0] if len(ids) == 1 else None


async def assign_owner(db, row, name, by=None) -> None:
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
        # one match, or NULL: an aggregate with no GROUP BY is always one row,
        # and the CASE keeps its MIN only when exactly one account matched.
        # (Not HAVING without GROUP BY — SQLite before 3.39, e.g. Ubuntu
        # 22.04's, refuses it.)
        match = (sa.select(sa.case((func.count() == 1, func.min(users.c.id)), else_=None))
                 .where(users.c.role.in_(sorted(STAFF_ROLES)),
                        display_name(users.c.full_name, users.c.username) == name)
                 .scalar_subquery())
        touched += conn.execute(
            sa.update(t)
            .where(func.coalesce(t.c[RESOLVED], "") != func.coalesce(name, ""))
            .values({id_col: match, RESOLVED: name})).rowcount or 0
    return touched


# ── the panel's rules ─────────────────────────────────────────────────────────

def files_visible_to(query, user):
    """Private files belong to whoever filed them (and to a super_admin)."""
    if is_super(user):
        return query
    return query.where(or_(Property.is_private == False,       # noqa: E712
                           _mine(Property.created_by_user_id, user)))


def cabinets_visible_to(query, user):
    """A کمد شخصی belongs to whoever made it; a cabinet with no owner is
    the agency's and everyone sees it."""
    if is_super(user):
        return query
    return query.where(or_(Cabinet.owner.is_(None), _mine(Cabinet.owner_user_id, user)))


def matches_visible_to(query, user):
    """A consultant sees the matches for their own customers (and for
    customers nobody is assigned to); root and super_admin see everybody's."""
    if is_super(user):
        return query
    return query.where(or_(CustomerMatch.consultant.is_(None), CustomerMatch.consultant == "",
                           _mine(CustomerMatch.consultant_user_id, user)))


def tasks_visible_to(query, user):
    """A super_admin sees the whole board; everyone else sees only their own.

    Tasks with no assignee stay visible to all, because they predate this rule
    and hiding them would orphan them — new tasks are stamped with their
    creator on the way in, so the unassigned set only ever shrinks.
    """
    if is_super(user):
        return query
    return query.where(or_(Task.assigned_to.is_(None), _mine(Task.assigned_to_user_id, user)))


def call_queue_for(query, user):
    """The call queue is «mine or nobody's»: the leads assigned to this
    account and the unassigned ones, whose first dial claims them. Everybody
    queues this way, root included — the queue is a person's day, not a
    view of the office.

    A lead whose name meant nobody, or more than one person, belongs to
    nobody (see the module docstring) — but assigned_to still holds that
    name, so it fails "unassigned", and assigned_to_user_id is NULL, so it
    fails "mine" for everybody too. Left as is, it would sit in no one's
    queue at all. root and super_admin — the only ones who can actually sort
    out who the name meant — see it; everybody else still sees only their
    own and the genuinely unassigned, same as before.
    """
    if is_super(user):
        return query.where(or_(Lead.assigned_to_user_id.is_(None), _mine(Lead.assigned_to_user_id, user)))
    return query.where(or_(Lead.assigned_to.is_(None), Lead.assigned_to == "",
                           _mine(Lead.assigned_to_user_id, user)))


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
    return query.where(or_(shared, _mine(Property.created_by_user_id, user)))


def customers_visible_to(query, user):
    """A consultant's own customers and the ones nobody is assigned to —
    the rule the matches use; root and super_admin see all."""
    if is_super(user):
        return query
    return query.where(or_(Customer.consultant_name.is_(None), Customer.consultant_name == "",
                           _mine(Customer.consultant_user_id, user)))


# ── the dashboard's team numbers ─────────────────────────────────────────────

def activity_visible_to(query, user):
    """Whose calls, visits and closes a dashboard counts: root and
    super_admin see the whole office's, everybody else their own. The
    activity log names people by display name, so that is what is matched."""
    if is_super(user):
        return query
    return query.where(ActivityLog.actor == actor(user))


def team_visible_to(query, user):
    """The people the dashboard's team table lists: every staff account for
    root and super_admin, only oneself for everybody else."""
    query = query.where(User.is_active == True, User.role.in_(STAFF_ROLES))   # noqa: E712
    if is_super(user):
        return query
    return query.where(_mine(User.id, user))

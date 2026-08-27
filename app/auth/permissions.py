"""
SorinFlow — role tiers and per-admin permissions.

Four tiers:
  root         — the developer. Everything, always. Seeded from env vars and
                 never creatable or editable from the panel.
  super_admin  — the agency owner. Runs the business: staff, tickets, requests.
  admin        — an employee. Reaches only the areas super_admin ticked in
                 users.permissions.
  visitor      — a public sign-up. No dashboard at all, only the portal.

root and super_admin bypass permission checks outright, so a permission list is
only ever consulted for an admin. Visitors are refused before the list is even
read — see require_staff in app/auth/dependencies.py.
"""

ROLE_ROOT = "root"
ROLE_SUPER_ADMIN = "super_admin"
ROLE_ADMIN = "admin"
ROLE_VISITOR = "visitor"

VALID_ROLES = {ROLE_ROOT, ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_VISITOR}

# Roles allowed into the dashboard at all.
STAFF_ROLES = {ROLE_ROOT, ROLE_SUPER_ADMIN, ROLE_ADMIN}

# Roles that skip every permission check.
FULL_ACCESS_ROLES = {ROLE_ROOT, ROLE_SUPER_ADMIN}

# Roles a super_admin is allowed to hand out. root is deliberately absent:
# only the env-seeded root exists, and only root may edit another root.
ASSIGNABLE_BY_SUPER_ADMIN = {ROLE_ADMIN, ROLE_VISITOR}

# permission key -> Persian label, shown as the toggle list on the user editor.
# Keys match the router they gate (see app/api/routes/__init__.py), so adding a
# permission here and a gate there is the whole job.
PERMISSIONS = {
    "properties": "املاک",
    "scraper":    "اسکرپر",
    "crm":        "مدیریت ارتباط با مشتری",
    "filing":     "بایگانی (کمد و زونکن)",
    "divar_auth": "حساب‌های دیوار",
    "proxies":    "پروکسی‌ها",
    "stats":      "آمار و گزارش",
    "portal":     "درخواست‌های بازدیدکنندگان",
}

ALL_PERMISSIONS = list(PERMISSIONS.keys())

# What a freshly-approved admin gets when super_admin ticks nothing at all.
DEFAULT_ADMIN_PERMISSIONS = ["properties", "crm", "stats"]

# What the old 'user' role could actually see in the panel (dashboard +
# properties). Used once, by the migration that converts those accounts, so a
# conversion cannot silently widen anyone's access.
LEGACY_USER_PERMISSIONS = ["stats", "properties"]


def normalize_permissions(value) -> list:
    """Coerce whatever is in the column into a clean list of known keys.

    The column is JSON and has held a list since it was added, but a hand-edited
    row or an older dict-shaped value should degrade to something safe rather
    than raise inside a dependency on every request.
    """
    if not value:
        return []
    if isinstance(value, dict):
        # {"crm": true, "scraper": false} -> ["crm"]
        value = [k for k, v in value.items() if v]
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [p for p in value if p in PERMISSIONS]


def user_permissions(user) -> list:
    """Effective permissions for a user, expanding the bypass roles."""
    role = getattr(user, "role", None)
    if role in FULL_ACCESS_ROLES:
        return list(ALL_PERMISSIONS)
    if role != ROLE_ADMIN:
        return []
    return normalize_permissions(getattr(user, "permissions", None))


def has_permission(user, permission: str) -> bool:
    if getattr(user, "role", None) in FULL_ACCESS_ROLES:
        return True
    return permission in user_permissions(user)

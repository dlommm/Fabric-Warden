"""Built-in roles and permissions.

Permissions are `<area>:<verb>` strings. Roles are fixed sets of permissions.
RoleAssignment has a site_id column for multi-site scoping, but the API only
creates global assignments today; site-scoped enforcement is roadmap work.
"""

from __future__ import annotations

from enum import StrEnum


class P(StrEnum):
    INVENTORY_READ = "inventory:read"
    INVENTORY_EDIT = "inventory:edit"  # labels, notes; never controller config
    IPAM_EDIT = "ipam:edit"  # documented prefixes/VLANs/reservations; never controller config
    ANALYTICS_EDIT = "analytics:edit"  # dashboards
    DCIM_EDIT = "dcim:edit"  # racks, cabling, power, asset lifecycle; documentation only
    DOCS_EDIT = "docs:edit"  # documentation pages
    MONITORING_MANAGE = "monitoring:manage"  # alert rules, acknowledgements, maintenance windows, WAN probe
    NOTIFICATIONS_MANAGE = "notifications:manage"  # channels hold delivery secrets
    CONTROLLERS_READ = "controllers:read"
    CONTROLLERS_MANAGE = "controllers:manage"
    COMPLIANCE_MANAGE = "compliance:manage"  # waive controls
    SERVERS_MANAGE = "servers:manage"  # host connections hold API credentials; read-only access to the hosts
    MODULES_READ = "modules:read"
    MODULES_MANAGE = "modules:manage"
    JOBS_READ = "jobs:read"
    JOBS_RUN = "jobs:run"
    AUDIT_READ = "audit:read"
    USERS_READ = "users:read"
    USERS_MANAGE = "users:manage"
    TOKENS_MANAGE_OWN = "tokens:manage_own"
    CHANGES_PROPOSE = "changes:propose"
    CHANGES_APPROVE = "changes:approve"
    SYSTEM_READ = "system:read"
    SYSTEM_MANAGE = "system:manage"


ALL = frozenset(P)

ROLES: dict[str, frozenset[P]] = {
    "owner": ALL,
    "admin": ALL - {P.SYSTEM_MANAGE},
    "operator": frozenset(
        {
            P.INVENTORY_READ,
            P.INVENTORY_EDIT,
            P.IPAM_EDIT,
            P.ANALYTICS_EDIT,
            P.DCIM_EDIT,
            P.DOCS_EDIT,
            P.MONITORING_MANAGE,
            P.CONTROLLERS_READ,
            P.MODULES_READ,
            P.JOBS_READ,
            P.JOBS_RUN,
            P.TOKENS_MANAGE_OWN,
            P.CHANGES_PROPOSE,
            P.SYSTEM_READ,
        }
    ),
    "auditor": frozenset(
        {
            P.INVENTORY_READ,
            P.CONTROLLERS_READ,
            P.MODULES_READ,
            P.JOBS_READ,
            P.AUDIT_READ,
            P.USERS_READ,
            P.SYSTEM_READ,
        }
    ),
    "viewer": frozenset({P.INVENTORY_READ, P.CONTROLLERS_READ, P.MODULES_READ, P.TOKENS_MANAGE_OWN, P.SYSTEM_READ}),
}

ROLE_DESCRIPTIONS = {
    "owner": "Full control, including system settings and other owners.",
    "admin": "Manage users, controllers, modules and approvals.",
    "operator": "Day-to-day operations: inventory edits, jobs, change proposals.",
    "auditor": "Read-only access including the audit trail and users.",
    "viewer": "Read-only access to inventory and status.",
}


def permissions_for(roles: list[str]) -> frozenset[P]:
    out: set[P] = set()
    for r in roles:
        out |= ROLES.get(r, frozenset())
    return frozenset(out)

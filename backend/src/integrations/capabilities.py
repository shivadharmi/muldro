"""Canonical capability catalog for Muldro integrations.

Every tool Muldro can invoke maps to a canonical capability string (e.g. "email.send",
"calendar.list", "repo.create_pr"). The catalog is the single source of truth for:

1. CapabilityFamily — the 11 top-level capability families
2. CAPABILITY_CATALOG — capability string → CapabilityMeta (family, read_only, risk_level)

Tool name → capability mappings are in src/tools/catalog.py (INTERNAL_TOOLS + EXTERNAL_TOOL_SEEDS).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

try:  # Python 3.11+
    from enum import StrEnum
except ImportError:  # Python 3.10 fallback

    class StrEnum(str, Enum):
        """Compatibility fallback for enum.StrEnum on Python < 3.11."""


class CapabilityFamily(StrEnum):
    EMAIL = "email"
    CALENDAR = "calendar"
    REPO = "repo"
    ISSUE = "issue"
    DOC = "doc"
    WORKFLOW = "workflow"
    MESSAGING = "messaging"
    SEARCH = "search"
    INTERNAL = "internal"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class CapabilityMeta:
    family: CapabilityFamily
    read_only: bool
    risk_level: str  # low, medium, high, critical


def _cap(family: CapabilityFamily, read_only: bool, risk: str = "low") -> CapabilityMeta:
    return CapabilityMeta(family=family, read_only=read_only, risk_level=risk)


# ── Capability catalog ──────────────────────────────────────────────────────

CAPABILITY_CATALOG: dict[str, CapabilityMeta] = {
    # Email
    "email.send": _cap(CapabilityFamily.EMAIL, False, "high"),
    "email.draft": _cap(CapabilityFamily.EMAIL, False, "medium"),
    "email.reply": _cap(CapabilityFamily.EMAIL, False, "high"),
    "email.list": _cap(CapabilityFamily.EMAIL, True),
    "email.read": _cap(CapabilityFamily.EMAIL, True),
    "email.search": _cap(CapabilityFamily.EMAIL, True),
    "email.delete": _cap(CapabilityFamily.EMAIL, False, "critical"),
    # Calendar
    "calendar.list": _cap(CapabilityFamily.CALENDAR, True),
    "calendar.get": _cap(CapabilityFamily.CALENDAR, True),
    "calendar.create": _cap(CapabilityFamily.CALENDAR, False, "medium"),
    "calendar.update": _cap(CapabilityFamily.CALENDAR, False, "medium"),
    "calendar.delete": _cap(CapabilityFamily.CALENDAR, False, "critical"),
    # Repository
    "repo.search_code": _cap(CapabilityFamily.REPO, True),
    "repo.search_repos": _cap(CapabilityFamily.REPO, True),
    "repo.get_diff": _cap(CapabilityFamily.REPO, True),
    "repo.get_files": _cap(CapabilityFamily.REPO, True),
    "repo.create_pr": _cap(CapabilityFamily.REPO, False, "high"),
    "repo.merge_pr": _cap(CapabilityFamily.REPO, False, "high"),
    "repo.update_pr": _cap(CapabilityFamily.REPO, False, "medium"),
    "repo.list_prs": _cap(CapabilityFamily.REPO, True),
    "repo.search_prs": _cap(CapabilityFamily.REPO, True),
    "repo.get_reviews": _cap(CapabilityFamily.REPO, True),
    "repo.review_pr": _cap(CapabilityFamily.REPO, False, "medium"),
    "repo.get_checks": _cap(CapabilityFamily.REPO, True),
    # Issue tracking
    "issue.create": _cap(CapabilityFamily.ISSUE, False, "medium"),
    "issue.update": _cap(CapabilityFamily.ISSUE, False, "medium"),
    "issue.comment": _cap(CapabilityFamily.ISSUE, False, "medium"),
    "issue.list": _cap(CapabilityFamily.ISSUE, True),
    "issue.get": _cap(CapabilityFamily.ISSUE, True),
    "issue.search": _cap(CapabilityFamily.ISSUE, True),
    "issue.delete": _cap(CapabilityFamily.ISSUE, False, "critical"),
    "issue.transition": _cap(CapabilityFamily.ISSUE, False, "medium"),
    "issue.sub_issue": _cap(CapabilityFamily.ISSUE, False, "medium"),
    # Document / knowledge
    "doc.create": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.update": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.get": _cap(CapabilityFamily.DOC, True),
    "doc.search": _cap(CapabilityFamily.DOC, True),
    "doc.delete": _cap(CapabilityFamily.DOC, False, "critical"),
    "doc.comment": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.append": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.query": _cap(CapabilityFamily.DOC, True),
    # Workflow / project
    "workflow.create_issue": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.update_issue": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.transition": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.comment": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.list": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.get": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.search": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.delete": _cap(CapabilityFamily.WORKFLOW, False, "critical"),
    "workflow.get_teams": _cap(CapabilityFamily.WORKFLOW, True),
    # Messaging
    "messaging.send": _cap(CapabilityFamily.MESSAGING, False, "high"),
    "messaging.reply": _cap(CapabilityFamily.MESSAGING, False, "high"),
    "messaging.react": _cap(CapabilityFamily.MESSAGING, False, "medium"),
    "messaging.update": _cap(CapabilityFamily.MESSAGING, False, "medium"),
    "messaging.list_channels": _cap(CapabilityFamily.MESSAGING, True),
    "messaging.get_history": _cap(CapabilityFamily.MESSAGING, True),
    "messaging.get_thread": _cap(CapabilityFamily.MESSAGING, True),
    "messaging.get_users": _cap(CapabilityFamily.MESSAGING, True),
    "messaging.get_profile": _cap(CapabilityFamily.MESSAGING, True),
    "messaging.search": _cap(CapabilityFamily.MESSAGING, True),
    "messaging.send_template": _cap(CapabilityFamily.MESSAGING, False, "high"),
    "messaging.mark_read": _cap(CapabilityFamily.MESSAGING, False),
    # Search
    "search.web": _cap(CapabilityFamily.SEARCH, True),
    "search.memory": _cap(CapabilityFamily.SEARCH, True),
    "search.users": _cap(CapabilityFamily.SEARCH, True),
    "search.orgs": _cap(CapabilityFamily.SEARCH, True),
    # Social
    "messaging.post": _cap(CapabilityFamily.MESSAGING, False, "high"),
    "messaging.share": _cap(CapabilityFamily.MESSAGING, False, "high"),
    "messaging.get_mentions": _cap(CapabilityFamily.MESSAGING, True),
    # Internal intelligence
    "internal.search": _cap(CapabilityFamily.INTERNAL, True),
    "internal.get_plans": _cap(CapabilityFamily.INTERNAL, True),
    "internal.get_plan_details": _cap(CapabilityFamily.INTERNAL, True),
    "internal.get_goals": _cap(CapabilityFamily.INTERNAL, True),
    "internal.get_briefing": _cap(CapabilityFamily.INTERNAL, True),
    "internal.get_cursor": _cap(CapabilityFamily.INTERNAL, True),
    "internal.report_observation": _cap(CapabilityFamily.INTERNAL, False),
    "internal.build_context": _cap(CapabilityFamily.INTERNAL, True),
    "internal.ingest_event": _cap(CapabilityFamily.INTERNAL, False),
    "internal.update_entity": _cap(CapabilityFamily.INTERNAL, False),
    "internal.evaluate_policy": _cap(CapabilityFamily.INTERNAL, False),
    "internal.report_verdict": _cap(CapabilityFamily.INTERNAL, False),
    "internal.approve_action": _cap(CapabilityFamily.INTERNAL, False, "medium"),
    "internal.update_cursor": _cap(CapabilityFamily.INTERNAL, False),
    "internal.extract_preferences": _cap(CapabilityFamily.INTERNAL, False),
    "internal.verify_run": _cap(CapabilityFamily.INTERNAL, False),
    "internal.update_execution": _cap(CapabilityFamily.INTERNAL, False),
    "internal.push_ui": _cap(CapabilityFamily.INTERNAL, False),
    "internal.render_surface": _cap(CapabilityFamily.INTERNAL, False),
    "internal.store_memory": _cap(CapabilityFamily.INTERNAL, False),
    "internal.store_preference": _cap(CapabilityFamily.INTERNAL, False),
    "internal.get_entity": _cap(CapabilityFamily.INTERNAL, True),
    "internal.query_facts": _cap(CapabilityFamily.INTERNAL, True),
    "internal.traverse": _cap(CapabilityFamily.INTERNAL, True),
    "internal.get_provenance": _cap(CapabilityFamily.INTERNAL, True),
    # Drive
    "doc.drive_list": _cap(CapabilityFamily.DOC, True),
    "doc.drive_search": _cap(CapabilityFamily.DOC, True),
    "doc.drive_create": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.drive_delete": _cap(CapabilityFamily.DOC, False, "critical"),
    # Workflow (new — additions to existing workflow family)
    "workflow.create_issues": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.bulk_update": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.search_by_id": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.update_comment": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.delete_comment": _cap(CapabilityFamily.WORKFLOW, False, "high"),
    "workflow.resolve_comment": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.unresolve_comment": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.get_user": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.get_project": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.list_projects": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.create_project": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.create_milestone": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.get_milestones": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.update_milestone": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.delete_milestone": _cap(CapabilityFamily.WORKFLOW, False, "high"),
    "workflow.create_customer_need": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.auth": _cap(CapabilityFamily.WORKFLOW, True),
    # Doc (new — additions to existing doc family)
    "doc.get_property": _cap(CapabilityFamily.DOC, True),
    "doc.get_comment": _cap(CapabilityFamily.DOC, True),
    "doc.get_children": _cap(CapabilityFamily.DOC, True),
    "doc.get_block": _cap(CapabilityFamily.DOC, True),
    "doc.update_block": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.delete_block": _cap(CapabilityFamily.DOC, False, "high"),
    "doc.move": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.get_database": _cap(CapabilityFamily.DOC, True),
    "doc.create_datasource": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.get_datasource": _cap(CapabilityFamily.DOC, True),
    "doc.update_datasource": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.list_templates": _cap(CapabilityFamily.DOC, True),
    "doc.get_self": _cap(CapabilityFamily.DOC, True),
    "doc.get_user": _cap(CapabilityFamily.DOC, True),
    "doc.get_users": _cap(CapabilityFamily.DOC, True),
    # System meta-tools (Spec 1A)
    "system.discovery": _cap(CapabilityFamily.SYSTEM, True, "none"),
    # System action capabilities (P2.5a) — promoted from Planner-step-strings to internal
    # MCP tools. Writes into Muldro's own data layer (the user's memory/goals/schedule);
    # ALWAYS-ALLOWED on the chat path (exempt from permission_gate + write_lock, D5).
    "system.set_goal": _cap(CapabilityFamily.SYSTEM, False, "low"),
    "system.set_instruction": _cap(CapabilityFamily.SYSTEM, False, "low"),
    "system.schedule_reminder": _cap(CapabilityFamily.SYSTEM, False, "low"),
    "system.add_to_brief": _cap(CapabilityFamily.SYSTEM, False, "low"),
}


# The 4 promoted system.* ACTION capabilities (P2.5a) — internal writes into the user's own
# data layer (goals / instructions / reminders / briefing). Single source of truth, shared by:
#   * the chat middleware exemptions (permission_gate + write_lock) — ALWAYS-ALLOWED (D5);
#   * verification.predicate.REVERSIBLE_INTERNAL_CAPABILITIES — reversible/self, skip read-back;
#   * (P2.5b) the planless connector-scope's SYSTEM_CAPS floor.
# An EXPLICIT set (not a `system.` prefix) so a future/renamed system.* capability is gated by
# default until deliberately added here — safe-by-construction, not safe-by-current-catalog.
SYSTEM_ACTION_CAPABILITIES: frozenset[str] = frozenset(
    {
        "system.set_goal",
        "system.set_instruction",
        "system.schedule_reminder",
        "system.add_to_brief",
    }
)


def get_family_for_capability(capability: str) -> CapabilityFamily | None:
    """Extract the family from a capability string."""
    meta = CAPABILITY_CATALOG.get(capability)
    if meta:
        return meta.family
    prefix = capability.split(".")[0] if "." in capability else None
    if prefix:
        try:
            return CapabilityFamily(prefix)
        except ValueError:
            pass
    return None


def is_read_only_capability(capability: str) -> bool:
    """Check if a capability is read-only."""
    meta = CAPABILITY_CATALOG.get(capability)
    return meta.read_only if meta else False

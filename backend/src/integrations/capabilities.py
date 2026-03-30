"""Canonical capability catalog for Jarvis integrations.

Every tool Jarvis can invoke maps to a canonical capability string (e.g. "email.send",
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
    BROWSER = "browser"
    SEARCH = "search"
    INTERNAL = "internal"
    FILESYSTEM = "filesystem"


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
    # Browser
    "browser.open": _cap(CapabilityFamily.BROWSER, False, "medium"),
    "browser.snapshot": _cap(CapabilityFamily.BROWSER, True),
    "browser.extract": _cap(CapabilityFamily.BROWSER, True),
    "browser.click": _cap(CapabilityFamily.BROWSER, False, "medium"),
    "browser.type": _cap(CapabilityFamily.BROWSER, False, "medium"),
    "browser.submit": _cap(CapabilityFamily.BROWSER, False, "high"),
    "browser.screenshot": _cap(CapabilityFamily.BROWSER, True),
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
    "internal.get_goals": _cap(CapabilityFamily.INTERNAL, True),
    "internal.get_briefing": _cap(CapabilityFamily.INTERNAL, True),
    "internal.get_cursor": _cap(CapabilityFamily.INTERNAL, True),
    "internal.report_observation": _cap(CapabilityFamily.INTERNAL, False),
    "internal.build_context": _cap(CapabilityFamily.INTERNAL, True),
    "internal.ingest_event": _cap(CapabilityFamily.INTERNAL, False),
    "internal.update_entity": _cap(CapabilityFamily.INTERNAL, False),
    "internal.evaluate_policy": _cap(CapabilityFamily.INTERNAL, False),
    "internal.approve_action": _cap(CapabilityFamily.INTERNAL, False, "medium"),
    "internal.update_cursor": _cap(CapabilityFamily.INTERNAL, False),
    "internal.extract_preferences": _cap(CapabilityFamily.INTERNAL, False),
    "internal.verify_run": _cap(CapabilityFamily.INTERNAL, False),
    "internal.update_execution": _cap(CapabilityFamily.INTERNAL, False),
    "internal.push_ui": _cap(CapabilityFamily.INTERNAL, False),
    "internal.send_telegram": _cap(CapabilityFamily.MESSAGING, False, "medium"),
    "internal.send_approval": _cap(CapabilityFamily.MESSAGING, False, "medium"),
    # Drive
    "doc.drive_list": _cap(CapabilityFamily.DOC, True),
    "doc.drive_search": _cap(CapabilityFamily.DOC, True),
    "doc.drive_create": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.drive_delete": _cap(CapabilityFamily.DOC, False, "critical"),
    # Filesystem
    "filesystem.read": _cap(CapabilityFamily.FILESYSTEM, True),
    "filesystem.read_media": _cap(CapabilityFamily.FILESYSTEM, True),
    "filesystem.write": _cap(CapabilityFamily.FILESYSTEM, False, "high"),
    "filesystem.move": _cap(CapabilityFamily.FILESYSTEM, False, "high"),
    "filesystem.list": _cap(CapabilityFamily.FILESYSTEM, True),
    "filesystem.search": _cap(CapabilityFamily.FILESYSTEM, True),
    # Browser (new — additions to existing browser family)
    "browser.execute": _cap(CapabilityFamily.BROWSER, False, "high"),
    "browser.install": _cap(CapabilityFamily.BROWSER, False, "medium"),
    "browser.navigate_back": _cap(CapabilityFamily.BROWSER, True),
    "browser.wait": _cap(CapabilityFamily.BROWSER, True),
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
}


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

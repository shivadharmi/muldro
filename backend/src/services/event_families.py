"""Event family constants for the event bus.

Defines all 12 event families used across the system.
Each family represents a domain of events that can be published/subscribed.
"""

# User lifecycle events
USER_CREATED = "user.created"
USER_UPDATED = "user.updated"
USER_SETTINGS_CHANGED = "user.settings_changed"

# Task lifecycle events
TASK_CREATED = "task.created"
TASK_STATUS_CHANGED = "task.status_changed"
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"
TASK_CANCELLED = "task.cancelled"

# Run (execution) lifecycle events
RUN_CREATED = "run.created"
RUN_STARTED = "run.started"
RUN_COMPLETED = "run.completed"
RUN_FAILED = "run.failed"
RUN_PAUSED = "run.paused"
RUN_RESUMED = "run.resumed"

# Step events
STEP_STARTED = "step.started"
STEP_COMPLETED = "step.completed"
STEP_FAILED = "step.failed"
STEP_AWAITING_APPROVAL = "step.awaiting_approval"

# Tool events
TOOL_CALLED = "tool.called"
TOOL_COMPLETED = "tool.completed"
TOOL_BLOCKED = "tool.blocked"
TOOL_ERROR = "tool.error"

# Approval events
APPROVAL_REQUESTED = "approval.requested"
APPROVAL_APPROVED = "approval.approved"
APPROVAL_REJECTED = "approval.rejected"
APPROVAL_EXPIRED = "approval.expired"

# Memory events
MEMORY_CREATED = "memory.created"
MEMORY_UPDATED = "memory.updated"
MEMORY_SUPERSEDED = "memory.superseded"
ENTITY_FACT_SUPERSEDED = "entity_fact.superseded"
MEMORY_CONSOLIDATED = "memory.consolidated"

# Connector events
CONNECTOR_POLL_COMPLETED = "connector.poll_completed"
CONNECTOR_WEBHOOK_RECEIVED = "connector.webhook_received"
CONNECTOR_ERROR = "connector.error"
CONNECTOR_ACTION_COMPLETED = "connector.action_completed"

# Notification events
NOTIFICATION_CREATED = "notification.created"
NOTIFICATION_SENT = "notification.sent"
NOTIFICATION_READ = "notification.read"
NOTIFICATION_DISMISSED = "notification.dismissed"

# Watcher events
WATCHER_TRIGGER_FIRED = "watcher.trigger_fired"
WATCHER_TRIGGER_CREATED = "watcher.trigger_created"
WATCHER_SNOOZED = "watcher.snoozed"

# UI events
UI_SURFACE_CONNECTED = "ui.surface_connected"
UI_SURFACE_DISCONNECTED = "ui.surface_disconnected"
UI_VIEW_PUSHED = "ui.view_pushed"

# All families for validation
ALL_FAMILIES = {
    "user",
    "task",
    "run",
    "step",
    "tool",
    "approval",
    "memory",
    "connector",
    "browser",
    "notification",
    "watcher",
    "ui",
}

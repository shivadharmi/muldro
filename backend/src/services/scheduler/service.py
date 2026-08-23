"""Composed SchedulerLoop — backend-owned dynamic scheduler.

Assembles the per-responsibility tick mixins onto the lifecycle/cadence base.
Each tick mixin contributes one set of ``_tick_*`` handlers; ``SchedulerBase``
owns ``__init__``, the run loop, the cadence dispatcher (``_tick``), and the
mutable tick state the mixins share through the composed instance.
"""

from src.services.scheduler._base import SchedulerBase
from src.services.scheduler.background_tasks_tick import BackgroundTasksTickMixin
from src.services.scheduler.checkpoint_reaper_tick import CheckpointReaperTickMixin
from src.services.scheduler.deferred_verification_tick import DeferredVerificationTickMixin
from src.services.scheduler.dlq_tick import DlqTickMixin
from src.services.scheduler.filter_proposal_tick import FilterProposalTickMixin
from src.services.scheduler.lifecycle_tick import LifecycleTickMixin
from src.services.scheduler.notification_tick import NotificationTickMixin
from src.services.scheduler.perception_tick import PerceptionTickMixin
from src.services.scheduler.persona_tick import PersonaTickMixin
from src.services.scheduler.run_health_tick import RunHealthTickMixin
from src.services.scheduler.schedule_dispatch import ScheduleDispatchMixin
from src.services.scheduler.webhook_renewal_tick import WebhookRenewalTickMixin


class SchedulerLoop(
    PerceptionTickMixin,
    BackgroundTasksTickMixin,
    LifecycleTickMixin,
    DlqTickMixin,
    NotificationTickMixin,
    RunHealthTickMixin,
    PersonaTickMixin,
    FilterProposalTickMixin,
    ScheduleDispatchMixin,
    WebhookRenewalTickMixin,
    DeferredVerificationTickMixin,
    CheckpointReaperTickMixin,
    SchedulerBase,
):
    """Backend-owned scheduler. Runs as asyncio task in worker thread."""

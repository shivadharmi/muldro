"""Composed SchedulerLoop — backend-owned dynamic scheduler.

Assembles the per-responsibility tick mixins onto the lifecycle/cadence base.
Each tick mixin contributes one set of ``_tick_*`` handlers; ``SchedulerBase``
owns ``__init__``, the run loop, the cadence dispatcher (``_tick``), and the
mutable tick state the mixins share through the composed instance.
"""

from src.services.scheduler._base import SchedulerBase
from src.services.scheduler.background_tasks_tick import BackgroundTasksTickMixin
from src.services.scheduler.dlq_tick import DlqTickMixin
from src.services.scheduler.lifecycle_tick import LifecycleTickMixin
from src.services.scheduler.notification_tick import NotificationTickMixin
from src.services.scheduler.perception_tick import PerceptionTickMixin
from src.services.scheduler.persona_tick import PersonaTickMixin
from src.services.scheduler.run_health_tick import RunHealthTickMixin
from src.services.scheduler.schedule_dispatch import ScheduleDispatchMixin


class SchedulerLoop(
    PerceptionTickMixin,
    BackgroundTasksTickMixin,
    LifecycleTickMixin,
    DlqTickMixin,
    NotificationTickMixin,
    RunHealthTickMixin,
    PersonaTickMixin,
    ScheduleDispatchMixin,
    SchedulerBase,
):
    """Backend-owned scheduler. Runs as asyncio task in worker thread."""

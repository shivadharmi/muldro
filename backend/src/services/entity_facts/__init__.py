"""World-model beliefs as a control surface (spec §4.6 items 3-5).

Bi-temporal entity-attribute facts with evidence-derived confidence: contradicting
values are superseded (valid_to) rather than clobbered; confidence is
source-reliability × corroboration, age-decayed (never LLM-self-reported); the
Step-3 verification loop reconciles beliefs (confirmed raises, divergent lowers) —
fed to abstention only, never the gate.

The public facade is built up incrementally across Step-4 tasks: Task 2 exports the
confidence surface; Task 3 adds ``EntityFactStore``; Task 7 adds ``reconcile_verdict``.
"""

from src.services.entity_facts.confidence import (
    SOURCE_RELIABILITY,
    compute_confidence,
    current_confidence,
    reliability_for,
)
from src.services.entity_facts.reconciliation import reconcile_verdict
from src.services.entity_facts.store import EntityFactStore

__all__ = [
    "SOURCE_RELIABILITY",
    "EntityFactStore",
    "compute_confidence",
    "current_confidence",
    "reconcile_verdict",
    "reliability_for",
]

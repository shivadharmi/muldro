"""Pydantic response models for command context sidebar.

The evidence domain models (EntityRef/MemoryRef/SourceRef/EvidenceBundle) live in
``src.services.evidence_models`` — the service layer builds them; this API
wrapper imports ``EvidenceBundle`` downward.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.services.evidence_models import EvidenceBundle


class ContextSidebarData(BaseModel):
    message_id: str | None = None
    conversation_id: str | None = None
    evidence: EvidenceBundle = EvidenceBundle()
    active_run: dict | None = None
    timestamp: datetime | None = None

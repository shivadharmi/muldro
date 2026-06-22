"""Evidence-bundle domain models.

These are produced by ``EvidenceBundleService`` (service layer) and consumed by
API responses. They live in the service layer so the service builds them without
importing upward from ``src.api.schemas``. The API ``ContextSidebarData`` wrapper
imports ``EvidenceBundle`` downward from here.
"""

from __future__ import annotations

from pydantic import BaseModel


class EntityRef(BaseModel):
    entity_id: str
    name: str
    entity_type: str
    relevance: float = 0.0


class MemoryRef(BaseModel):
    memory_id: str
    content: str
    memory_type: str
    relevance: float = 0.0


class SourceRef(BaseModel):
    source_type: str  # trace, artifact, connector, observation
    source_id: str
    label: str
    url: str | None = None


class EvidenceBundle(BaseModel):
    entities: list[EntityRef] = []
    memories: list[MemoryRef] = []
    sources: list[SourceRef] = []
    route_info: dict | None = None
    confidence: float | None = None
    risk_level: str | None = None

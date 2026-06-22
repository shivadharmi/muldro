"""Memory Service — episodic, semantic, preference, and behavioral memory.

Jarvis's product memory — long-term, structured, searchable, and scored.

Responsibilities:
- Extract candidate memories from interactions and events
- Score memory usefulness and stability
- Store with provenance and vector embeddings
- Provide retrieval API: semantic (Qdrant) with text fallback
- Expire or demote low-value memories

Split (SVC-P2-2b) into per-responsibility base classes composed by MemoryService;
this __init__ is the public facade re-exporting MemoryService, the decay helper,
and the extraction prompt constants so existing import paths are unchanged.
"""

from src.services.memory_service.extraction import (
    MEMORY_EXTRACTION_PROMPT,
    PREFERENCE_EXTRACTION_PROMPT,
)
from src.services.memory_service.service import MemoryService
from src.services.memory_service.stability import _compute_decayed_stability

__all__ = [
    "MemoryService",
    "_compute_decayed_stability",
    "MEMORY_EXTRACTION_PROMPT",
    "PREFERENCE_EXTRACTION_PROMPT",
]

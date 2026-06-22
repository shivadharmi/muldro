"""MemoryService — composed facade over the memory operation groups.

The god-object class was decomposed (SVC-P2-2b) into per-responsibility base
classes; MemoryService inherits them so the public API and every method body
are unchanged. `__init__` and shared helpers live in MemoryServiceBase.
"""

from src.services.memory_service._base import MemoryServiceBase
from src.services.memory_service.consolidation import MemoryConsolidation
from src.services.memory_service.contradictions import MemoryContradictions
from src.services.memory_service.extraction import MemoryExtraction
from src.services.memory_service.retrieval import MemoryRetrieval
from src.services.memory_service.stability import MemoryStability
from src.services.memory_service.storage import MemoryStorage


class MemoryService(
    MemoryExtraction,
    MemoryStorage,
    MemoryRetrieval,
    MemoryConsolidation,
    MemoryContradictions,
    MemoryStability,
    MemoryServiceBase,
):
    """Manage Jarvis long-term memory (episodic, semantic, preference, behavioral)."""

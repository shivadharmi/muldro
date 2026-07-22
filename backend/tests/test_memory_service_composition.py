"""Characterization test pinning MemoryService's public surface + composition.

Safety net for the class decomposition (SVC-P2-2b): MemoryService is split into
per-responsibility base classes that it inherits. This test pins that every
method still resolves on MemoryService (none dropped or shadowed by the split),
that a single __init__ wires the collaborators, and that the module-level
exports (decay helper + prompt constants) remain importable.
"""

import inspect

from src.services.memory_service import (
    MEMORY_EXTRACTION_PROMPT,
    PREFERENCE_EXTRACTION_PROMPT,
    MemoryService,
    _compute_decayed_stability,
)

EXPECTED_METHODS = {
    # extraction
    "extract_and_store",
    "extract_preferences",
    "_call_extraction",
    "_call_preference_extraction",
    # storage
    "store_goal_memory",
    "store_instruction_memory",
    "store_briefing_memory",
    "store_memory",
    # retrieval
    "retrieve",
    "get_user_preferences",
    "_composite_retrieve",
    "_text_retrieve",
    # consolidation
    "consolidate_memories",
    "_is_duplicate",
    # contradictions
    "check_contradictions",
    "_check_contradiction_pair",
    # stability
    "refresh_stability",
    # base helpers
    "_enqueue_failed_embedding",
    "_build_memory_payload",
    "_emit_event",
}


def test_memory_service_has_all_methods():
    for name in EXPECTED_METHODS:
        attr = getattr(MemoryService, name, None)
        assert attr is not None, f"MemoryService lost method {name}"
        assert callable(attr), f"{name} is not callable"


def test_async_methods_are_coroutines():
    # Every method except the pure _build_memory_payload staticmethod is async.
    for name in EXPECTED_METHODS - {"_build_memory_payload"}:
        assert inspect.iscoroutinefunction(getattr(MemoryService, name)), f"{name} not async"


def test_single_init_wires_collaborators():
    from unittest.mock import MagicMock

    from tests.conftest import make_mock_settings

    svc = MemoryService(settings=make_mock_settings(), db=MagicMock())
    for attr in ("_settings", "_db", "_embedder", "_event_bus", "_vector_store"):
        assert hasattr(svc, attr)


def test_decay_helper_and_prompts_exported():
    # decay formula: min(1.0, max(0.0, current - 0.02*days) + 0.1)
    assert _compute_decayed_stability(0.5, 0) == 0.6
    assert _compute_decayed_stability(1.0, 0) == 1.0  # clamped
    assert _compute_decayed_stability(0.0, 100) == 0.1  # floor then boost
    assert "memory extraction engine" in MEMORY_EXTRACTION_PROMPT
    assert "preference extraction engine" in PREFERENCE_EXTRACTION_PROMPT

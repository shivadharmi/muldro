"""Step 10 Phase B: the ENV-driven cutover lever.

`JARVIS_RUNTIME=deep` is the master switch — `effective_runtime(surface)` falls back
to the static `settings.runtime` (tier 4) for every surface when no Redis
override/breaker/enable-key is set. This pins that: with `redis=None` (or absent),
all three surfaces resolve to exactly `settings.runtime` — so the boot-time env flip
alone activates chat + perception + autonomous, and legacy stays legacy.
"""

from src.services.runtime_gate import effective_runtime
from tests.conftest import make_mock_settings

_SURFACES = ("chat", "perception", "autonomous")


async def test_surfaces_resolve_static_runtime_both_values():
    """redis=None → tier-4 static fallback → every surface == settings.runtime."""
    for expected in ("legacy", "deep"):
        settings = make_mock_settings(runtime=expected)
        for surface in _SURFACES:
            resolved = await effective_runtime(surface, redis=None, settings=settings)
            assert resolved == expected, f"{surface} resolved {resolved!r}, expected {expected!r}"

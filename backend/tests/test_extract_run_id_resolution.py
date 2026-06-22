"""Regression: run/summary surface detail tabs resolve the run_id correctly.

Post-`4893e16` the unified ``run`` surface id IS the run_id (``run_<ULID>``).
A persisted ``UISurface`` written by ``emit_surface_update`` carries a payload
WITHOUT explicit run linkage (only ``last_surface_update``), so the detail
builders must fall back to deriving the run_id from the surface_id.

The old fallback stripped the ``run_`` prefix (``surface_id.removeprefix("run_")``),
which deletes the ``run_`` that is PART of the run_id — so the TaskRun lookup
missed and every tab rendered "not found" despite HTTP 200. ``_extract_run_id``
must instead use the ``run_`` surface_id verbatim and strip only the outer
``summary_`` prefix, mirroring ``_resolve_ephemeral`` in routes_surface_detail.
"""

from types import SimpleNamespace

from src.services.surface_detail_builders._shared import _extract_run_id


def _surface(surface_id: str, payload: dict | None = None):
    return SimpleNamespace(surface_id=surface_id, payload=payload or {})


def test_run_surface_id_is_used_verbatim_not_stripped():
    """A run surface with no payload linkage resolves to the FULL run_<ULID> id."""
    sid = "run_01KVPYY8C6Z93GH2AXCX4ZECM7"
    assert _extract_run_id(_surface(sid)) == sid


def test_run_surface_with_poisoned_payload_still_resolves():
    """The exact emit_surface_update payload shape (no run linkage) still resolves."""
    sid = "run_01KVPYY8C6Z93GH2AXCX4ZECM7"
    poisoned = {"last_surface_update": {"surface_id": sid, "phase": "plan_ready"}}
    assert _extract_run_id(_surface(sid, poisoned)) == sid


def test_summary_surface_strips_only_outer_prefix():
    """summary_<run_id> recovers the run_<ULID> underneath (strip only summary_)."""
    run_id = "run_01KVPYY8C6Z93GH2AXCX4ZECM7"
    assert _extract_run_id(_surface(f"summary_{run_id}")) == run_id


def test_explicit_source_run_id_wins():
    """An explicit payload linkage takes priority over surface_id derivation."""
    s = _surface("run_unused", {"source_run_id": "run_explicit"})
    assert _extract_run_id(s) == "run_explicit"


def test_explicit_metadata_run_id_wins():
    s = _surface("run_unused", {"metadata": {"run_id": "run_meta"}})
    assert _extract_run_id(s) == "run_meta"


def test_unknown_prefix_returns_none():
    assert _extract_run_id(_surface("approval_01ABC")) is None

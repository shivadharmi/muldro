"""Entity/fact provenance — where an observation came from.

A typed replacement for the previously-unwired ``dict | None`` source_ref slot.
``source`` is always present; ``event_id`` is set for event-sourced extraction,
``run_id`` for outcome-sourced. Serialized to JSONB at the storage boundary."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SourceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    event_id: str | None = None
    run_id: str | None = None

    def dedup_key(self) -> str:
        """Identity for dedup: event_id, else run_id, else source."""
        return self.event_id or self.run_id or self.source

    def to_dict(self) -> dict:
        """JSONB-ready dict, omitting null event_id/run_id."""
        return self.model_dump(exclude_none=True)


def _key_of(ref: dict) -> str:
    return ref.get("event_id") or ref.get("run_id") or ref.get("source") or ""


def merge_source_refs(existing: list[dict] | None, new: SourceRef, cap: int = 20) -> list[dict]:
    """Append ``new`` to ``existing`` (list of serialized SourceRef dicts),
    deduping by dedup key (the matching prior ref is dropped and the new one
    appended as most-recent), keeping the most-recent ``cap``."""
    refs = [r for r in existing if isinstance(r, dict)] if isinstance(existing, list) else []
    new_key = new.dedup_key()
    refs = [r for r in refs if _key_of(r) != new_key]
    refs.append(new.to_dict())
    return refs[-cap:]

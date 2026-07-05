"""Post-action reconciliation (spec §4.5): the Step-3 verification loop feeds the world
model. A CONFIRMED read-back RAISES the confidence of the beliefs the write concerned;
a CONTRADICTED one LOWERS them. Fed to abstention/ask-the-user ONLY — this module never
touches TrustEngine/PolicyDecision/the gate (§4.3: confidence is a DEFERRED gate
dimension). Narrow and no-op-safe: acts only when an entity resolves from the write."""

import logging

from src.services.entity_facts.store import EntityFactStore
from src.services.verification.readback import VerifyVerdict

logger = logging.getLogger(__name__)


async def reconcile_verdict(
    db,
    *,
    workspace_id: str,
    user_id: str,
    verdict: VerifyVerdict,
    write_input: dict | None,
    write_output: dict | None,
) -> None:
    """Raise/lower world-model beliefs from a verification verdict. No-op unless the
    verdict is CONFIRMED/CONTRADICTED AND an entity resolves from the write. A best-effort
    belief write must never fail an otherwise-successful verification."""
    if verdict not in (VerifyVerdict.CONFIRMED, VerifyVerdict.CONTRADICTED):
        return
    if not workspace_id:
        return  # fail-closed: no cross-workspace belief writes

    entity_id = _resolve_entity_id(write_input or {}, write_output or {})
    if not entity_id:
        return  # narrow by design (D6); richer write->belief lineage is deferred

    try:
        store = EntityFactStore(db)
        facts = await store.current_facts(entity_id, workspace_id)
        if not facts:
            return
        for fact in facts:
            if verdict == VerifyVerdict.CONFIRMED:
                await store.corroborate(fact.fact_id)
            else:
                await store.weaken(fact.fact_id)
        logger.info(
            "reconciled %d belief(s) for entity=%s verdict=%s",
            len(facts),
            entity_id,
            verdict.value,
        )
    except Exception:
        logger.debug("Belief reconciliation failed for entity=%s", entity_id, exc_info=True)


def _resolve_entity_id(write_input: dict, write_output: dict) -> str | None:
    """Extract an explicit entity_id from the write's input/output. Deliberately narrow:
    only an explicit id is honoured (name/email resolution is deferred to avoid
    speculative mappings). Returns None when nothing resolves."""
    for src in (write_output, write_input):
        val = src.get("entity_id")
        if isinstance(val, str) and val:
            return val
    return None

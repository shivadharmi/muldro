# Spec 2B-ii: Trust UI + Policy Cleanup

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 2B-i (Single Approval Gate) — gate switchover must be stable
**Builds toward:** Spec 3 (Surfaces), Spec 4 (Perception)

## Problem Statement

Spec 2B-i switched the approval gate. This spec cleans up — deleting dead code (ApprovalPolicyEngine, TrustScore, ApprovalPolicy models), absorbing policy modes into trust ceilings, building the frontend Trust tab, and adding trust API endpoints.

## Design

### Component 1: Delete Dead Systems

| File | Action |
|------|--------|
| `src/services/approval_policy_engine.py` | **DELETE** |
| `src/models/trust_score.py` | **DELETE** |
| `src/models/approval_policy.py` | **DELETE** |
| Alembic migration | Drop `approval_policies` + `trust_scores` tables |

### Component 2: Policy Mode Absorption

Map 4 policy modes to workspace-level trust ceilings:

| Policy Mode | Trust Ceiling |
|---|---|
| `lockdown` | All capabilities → `blocked` |
| `approval_required` | All capabilities → `learning` |
| `suggest_only` | All capabilities → `first_use` |
| `full_auto` | No ceiling restriction |

`PUT /v1/settings/policy/mode` batch-updates `TrustCeiling` records. Settings → Policy UI keeps 4-mode selector backed by ceilings.

### Component 3: Time-Based Policy Absorption

Move `_get_time_based_policy_override()` from Governor to TrustEngine as time-scoped ceilings. Expose via API.

### Component 4: Trust API Endpoints

New file: `src/api/routes_trust.py`

| Method | Path | Purpose |
|---|---|---|
| `GET /v1/trust/dashboard` | All capabilities with levels, progress, ceilings |
| `GET /v1/trust/{capability}` | Detailed state across risk levels |
| `PUT /v1/trust/{capability}/ceiling` | Set max trust level |
| `POST /v1/trust/{capability}/reset` | Reset scores |
| `GET /v1/trust/time-policies` | Time-based overrides |
| `PUT /v1/trust/time-policies` | Set time-based overrides |

### Component 5: Frontend Trust Tab

New tab in Settings page showing:
- Per-capability trust levels grouped by family (Email, Calendar, etc.)
- Per-risk-level breakdown
- Graduation progress bars ("4/10 to trusted")
- Ceiling controls per capability
- Reset trust button

### Component 6: Approval UX with Trust Context

Update approval surfaces to show trust level:
- **first_use:** "First time" label, full preview
- **learning:** "Similar to N approvals", graduation hint
- **trusted (auto-executed):** Notification with undo
- **autonomous:** Activity feed entry only

## Files Changed

### Deleted Files (3)
- `src/services/approval_policy_engine.py`
- `src/models/trust_score.py`
- `src/models/approval_policy.py`

### New Files (3)
- `src/api/routes_trust.py` — 6 trust endpoints
- Alembic migration to drop `approval_policies` + `trust_scores` tables
- `tests/test_trust_api.py`

### Modified Files — Backend (4)
- `src/api/routes_settings.py` — Policy mode → trust ceiling mapping
- `src/services/surface_builder.py` — Trust context in approval surfaces
- `src/services/surface_detail_builders.py` — Graduation progress in detail
- `src/api/app.py` — Register trust routes

### Modified Files — Frontend (5)
- `frontend/src/app/settings/page.tsx` — Trust tab
- `frontend/src/lib/types.ts` — TrustState, TrustCeiling types
- `frontend/src/lib/api.ts` — Trust API calls
- `frontend/src/components/workspace/surface-card.tsx` — Trust context in approvals
- `frontend/src/stores/activity-store.ts` — `auto_execute_notify` events

## Testing Strategy

- Unit tests: policy mode → trust ceiling mapping
- Unit tests: trust API endpoints (dashboard, ceiling, reset)
- Integration: set ceiling → verify trust can't exceed it
- Frontend: Trust tab renders, ceiling control works
- Deletion verification: grep for ApprovalPolicyEngine, TrustScore — zero hits

## Success Criteria

1. ApprovalPolicyEngine, TrustScore, ApprovalPolicy deleted
2. Policy modes map to trust ceilings
3. Trust tab shows per-capability graduation progress
4. 6 trust API endpoints functional
5. Approval surfaces show trust context

## Blast Radius

**Low-Medium — mostly deletion + frontend addition.**

| File | Change | Risk |
|------|--------|------|
| 3 deleted files | Remove dead code | **LOW** — already not called (Spec 2B-i removed callers) |
| Frontend settings page | Add Trust tab | **LOW** — additive |
| `routes_settings.py` | Policy→ceiling mapping | **MEDIUM** — changes settings behavior |

### Total: ~15 files (4 backend modified, 3 deleted, 3 new, 5 frontend)

# routes_auth.py Decomposition — Design Spec

**Date:** 2026-06-20
**Branch:** `review/architecture-remediation`
**Status:** IMPLEMENTED (2026-06-20). `routes_auth.py` reduced 1,051 → **16 lines**
(pure aggregator). Full non-e2e suite green (**2,476 passed**) after every step.
Subagent-driven. Final module sizes: `routes_auth_oauth.py` 667,
`routes_auth_oauth_integration.py` 185, `routes_auth_magic_link.py` 106,
`routes_auth_session.py` 87, `routes_auth_schemas.py` 46, `routes_auth_profile.py` 25,
`routes_auth.py` 16 — all under the 800 cap. Refinement vs. the plan: the OAuth
module came out at 832 (over cap), so the 4 integration-provisioning helpers were
split further into `routes_auth_oauth_integration.py` (leaf). Imports verified acyclic.
**Target:** `backend/src/api/routes_auth.py` (~1,051 lines, ~7 route handlers + 5 OAuth helpers + 7 models)

Subagent-driven (per [[feedback_subagent_driven_refactoring]]): the main loop owns
this design, the spec, verification (full suite + grep + diff), and commits; each
leaf-first extraction is handed to a subagent with a precise rewrite map.

## 1. Problem

`routes_auth.py` is a module god-object: 7 route handlers across 5 unrelated concerns
(magic-link sign-in, OAuth authorize/providers, OAuth callback/integration-setup,
session refresh/logout, user profile) plus 5 OAuth-only helpers and 7 Pydantic models,
all on one `APIRouter()`. The OAuth callback alone is ~477 lines.

## 2. Goals / Non-Goals

**Goals**
- Split into focused route modules by concern, each its own `APIRouter()`, under the
  800-line cap (target 200–400).
- **Zero behavior change**, structure-only. Every route path, method, dependency, and
  response shape unchanged. Full non-e2e suite green (baseline **2,476 passed**) after
  every step.

**Non-Goals**
- No path/method/response changes. No new auth behavior.
- `app.py` registration must stay exactly: `from src.api.routes_auth import router as
  auth_router; app.include_router(auth_router, tags=["auth"])`.

## 3. Strategy

Each cluster moves to its own `routes_auth_<concern>.py` with a local `APIRouter()`;
handlers keep their **full `/v1/auth/...` path decorators verbatim**. `routes_auth.py`
becomes a thin **aggregator**: `router = APIRouter()` + `router.include_router(<sub>.router)`
for each. Because handler paths are absolute, including sub-routers without a prefix
reproduces the exact route table — `app.py` is untouched.

Shared models go in a leaf `routes_auth_schemas.py` (imported by sub-modules), so no
sub-module imports the aggregator (acyclic: aggregator → sub-modules → schemas leaf).

## 4. Target modules

| Module | Contents | ~lines |
|---|---|---|
| `routes_auth_schemas.py` (leaf) | all 7 Pydantic models (MagicLink*, Verify*, AuthTokenResponse, UserProfileResponse, OAuthUrlResponse, RefreshRequest) | ~45 |
| `routes_auth_magic_link.py` | `send_magic_link`, `verify_magic_link` | ~110 |
| `routes_auth_oauth.py` | `list_auth_providers`, `oauth_authorize`, `oauth_callback`, `_oauth_provider_name`, `_trigger_initial_observation`, `_ensure_integration`, `_enable_integration_schedules`, `_error_redirect` | ~650 |
| `routes_auth_session.py` | `refresh_token`, `logout` | ~80 |
| `routes_auth_profile.py` | `get_current_profile` | ~20 |
| `routes_auth.py` (aggregator) | `router = APIRouter()` + include_router × 4 | ~30 |

`routes_auth_oauth.py` (~650) is over the 200–400 target but under the 800 cap and
cohesive (the OAuth callback + its integration-setup helpers form one flow). A further
per-provider sub-split of `oauth_callback` is a possible follow-up, not done here.

## 5. White-box test couplings (retargets)

`@patch("src.api.routes_auth.AuthService")` must follow each handler to its new module:
- magic-link handlers → `routes_auth_magic_link.AuthService`:
  `test_email_sender.py:125,160,193` (send_magic_link), `test_auth_routes.py:131` (verify).
- session handler → `routes_auth_session.AuthService`: `test_auth_routes.py:152` (refresh).

Tests that hit routes via the app TestClient (path existence, OAuth authorize URL) need
**no change** — the aggregator keeps the full route table mounted.

## 6. Extraction order (leaf-first; structure-only commit each; suite green between)

1. `routes_auth_schemas.py` — move the 7 models; re-import into `routes_auth.py`.
2. `routes_auth_magic_link.py` — move 2 handlers; aggregator `include_router`; retarget magic-link AuthService patches.
3. `routes_auth_oauth.py` — move 3 handlers + 5 helpers + `_oauth_provider_name`; include_router.
4. `routes_auth_session.py` — move 2 handlers; include_router; retarget session AuthService patch.
5. `routes_auth_profile.py` — move 1 handler; include_router; `routes_auth.py` reaches final aggregator form.

## 7. Success criteria

- `routes_auth.py` reduced to a ~30-line aggregator; each new module within/near the
  200–400 band (`routes_auth_oauth.py` the one cohesive exception, under 800).
- Route table, `app.py` registration, and all response shapes unchanged.
- Full suite green and unchanged-passing except the documented `@patch` retargets.
- Acyclic imports (aggregator → sub-modules → schemas leaf).

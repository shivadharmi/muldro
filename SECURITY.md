# Security Policy

Muldro holds long-lived OAuth tokens for a founder's Gmail, Calendar, Slack, GitHub, Notion and
Atlassian accounts, encrypts provider API keys at rest, signs platform JWTs used to authorize
connector calls, and executes model-decided actions against those accounts. A vulnerability here is
not a data-leak-scale problem; it is a take-over-someone's-inbox-scale problem. Report privately.

## Supported versions

Muldro is **pre-1.0 and pre-launch**. There are no releases, no LTS branches, and no backports.

| Version | Supported |
|---------|-----------|
| `main`  | Yes |
| Anything else (tags, forks, old commits) | No |

Fixes land on `main`. If you are running Muldro, run `main`.

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability**, and do not disclose it on
social media, in a blog post, or in a pull request description before it is fixed.

Two private channels, in order of preference:

1. **GitHub Security Advisories** — go to the repository's **Security** tab and choose
   *Report a vulnerability*. This creates a private advisory only the maintainer can see, gives us
   a place to discuss the issue, and can mint a CVE when the fix ships.
   <https://github.com/shivadharmi/muldro/security/advisories/new>
2. **Email** — shivadharmi@gmail.com, as a fallback if the advisory form is unavailable to you.
   Put "muldro security" in the subject line. If you want to send an encrypted report, email first
   and ask for a key.

### What to include

- The affected component and file path or endpoint, and the commit SHA you tested against
- What an attacker can do with it, and what they need in order to do it (network position,
  a workspace account, an existing session, a poisoned inbound email, ...)
- Reproduction steps or a proof of concept
- Whether you have already disclosed this anywhere

**Redact secrets from anything you send.** If your proof of concept needed a real OAuth token, a
Fernet key or an API key, do not paste it — describe it. If you believe a live credential of ours
is exposed, say so immediately and separately so it can be rotated first.

### What to expect

Muldro is maintained by one person. The timelines below are what a solo maintainer can honestly
commit to, not an enterprise SLA:

| Stage | Target |
|-------|--------|
| Acknowledgement that the report was received | **5 business days** |
| Initial assessment (valid / not valid, rough severity) | 10 business days |
| Fix or documented mitigation for a confirmed high-severity issue | 30 days where practical |
| Status update while an issue is open | at least every 14 days |

If you have not heard anything in 10 business days, send a follow-up — assume the first message
was lost, not ignored.

Coordinated disclosure: please give us 90 days from acknowledgement before public disclosure, or
until a fix ships, whichever comes first. We will credit you in the advisory unless you ask us not
to. There is no bug bounty; the project is pre-revenue.

## Scope

### In scope

- Anything in this repository: `backend/`, `frontend/`, `infra/`, `scripts/`, `docker-compose*.yml`
- **Credential handling** — OAuth token storage and refresh, the Fernet encryption of provider
  credentials (`MULDRO_OAUTH_ENCRYPTION_KEY`, `MULDRO_CONFIG_ENCRYPTION_KEY`), the platform JWT
  signing key (`MULDRO_PLATFORM_JWT_PRIVATE_PEM`), session secrets, magic links
- **Authentication and session handling** — magic links, Google/GitHub/Atlassian OAuth callbacks,
  session tokens, the WebSocket and SSE auth paths
- **Workspace isolation** — any way to read or write another workspace's data. Every data table is
  `workspace_id`-scoped; a query that is not is a vulnerability, not a bug
- **Authority bypass** — anything that lets an action run outside the acting agent's
  `capability_scope`, or that gets a gated external write executed without the review it required
  (see the security model below)
- **Prompt injection with real consequence** — content arriving through a perception source (an
  email body, a Slack message, an issue title) that causes an external write, exfiltrates data, or
  widens an agent's authority. Injection that only produces wrong *text* is a quality bug; injection
  that produces an *action* is a vulnerability
- Secrets committed to the repository, or leaked into logs, traces, surfaces or error responses
- SQL injection, SSRF (notably through the MCP bridge and connector URLs), XSS in the view
  layer, CSRF, path traversal in artifact storage, unsandboxed artifact content
- Dependency vulnerabilities that are actually reachable from Muldro's code paths

### Out of scope

- **Third-party services themselves** — Anthropic, OpenAI, Google, Slack, GitHub, Notion,
  Atlassian, Qdrant, Neo4j. Report those to their own programs. Muldro's *use* of them is in scope
- The default local development configuration: `docker-compose.yml` ships weak, well-known
  credentials (`muldro`/`muldro`, `neo4j`/`muldrodev`) and binds datastores to localhost on purpose.
  It is not a deployment target. Likewise `MULDRO_DEBUG=true` and the documented escape hatches
  `MULDRO_SKIP_GATEWAY_VALIDATION` / `MULDRO_SKIP_REGISTRY_VALIDATION`
- Findings that require an attacker to already hold the workspace owner's session, host root, or
  the encryption keys
- Missing hardening headers, TLS configuration or rate limits on a *local* dev server. Report these
  against `infra/` (Caddy, Terraform) if they affect a real deployment
- Automated scanner output with no demonstrated impact, self-XSS, clickjacking on unauthenticated
  pages, missing SPF/DMARC on domains not used for Muldro mail
- Social engineering of the maintainer, physical attacks, denial of service by volume
- Model output being wrong, biased, or low quality. That is a product issue — open a normal issue

## Known security model

Muldro's own defenses, so you can aim at them rather than rediscover them. The authoritative
description is in [`CLAUDE.md`](CLAUDE.md) ("Trust Infrastructure & Approval") and
[`docs/architecture/execution.md`](docs/architecture/execution.md).

Everything — chat, perception, autonomous runs — executes on a single runtime (`backend/src/deep_runtime/`,
a LangGraph agent). Tools exposed to the model are **inert schema shells**: every execution is routed
through one central dispatcher, wrapped by a fixed middleware chain, outermost first:

```
capability_scope → governor_audit → unavailable_server → trust_gate → [permission_gate]
  → write_lock → [read_back] → repair_cap → dispatcher
```

- **`capability_scope`** is the always-on compensating control and the real authority boundary. It
  enforces the acting agent's own `capability_scope` at tool-execution time, from one registry
  lookup, and fails closed for known capabilities. It enforces the agent's *scope*, never the tool
  list it was offered. `build_deep_agent` refuses to compile a write-capable agent without it.
  This exists because the platform JWT is not yet minted per action — treat any bypass of it as
  high severity.
- **`trust_gate`** asks a per-**capability** question via `TrustEngine`: a deterministic 4x4 matrix
  of trust level (`first_use`, `learning`, `trusted`, `autonomous`) x risk level. It is dormant on
  user-typed chat turns (`authorization_source=DIRECT_USER_REQUEST` — the user's message is the
  authorization) and active on autonomous and batch turns.
- **`permission_gate`** asks a per-**action** question from `permission_mode` alone: is *this* write
  irreversible, externally visible, or high-risk? It **never consults trust**, and trust's
  auto-execute verdict falls *through* to it. That composition is deliberate: twenty-five approved
  self-scoped sends must not silently authorize a send to a brand-new external counterparty.
  Collapsing the two gates into one would be a security regression.
- **PREPARE is the third verdict.** A write gate has three outcomes, not two: allow, interrupt, and
  prepare. When a write needs a human and no human is reachable on the turn (`presence="absent"`),
  the action is recorded as an `Approval` carrying the redacted payload plus a snapshot of the
  acting agent's capability scope, and the turn continues. Confirmation **replays the exact recorded
  tool call** against that snapshot — it never re-runs an agent, and a since-widened scope cannot
  retroactively authorize it. It fails closed on every way the recorded call could fail to be the
  reviewed one, and is exactly-once via an idempotency ledger keyed on the approval id.
- **`presence` may only ever downgrade authority, never grant it.** An unknown or blank permission
  mode fails closed to `ask`.
- **Risk assessment fails closed.** When the risk assessor's model call or JSON parse fails it
  returns `risk_level="high"`, which requires approval at *every* trust level including
  `autonomous`. An assessment outage can never silently auto-execute a write.
- **The Governor is audit-only.** It is not an approval gate; do not report "the Governor allowed X"
  as a bypass.
- **View-layer side-effect line.** UI that triggers an action is code-authored, never model-authored.
  An `Affordance` must name a capability that resolves in `CAPABILITY_CATALOG`, its label is written
  by code, and one that does not resolve is not rendered. The model authors exactly one field on a
  Unit — `body`, markdown prose. It cannot choose a frame kind, a capability or a score. External
  text (an email subject, a message body) reaches the screen only through `quotes`, verbatim and
  attributed, and `frame.headline` is plain text whose validator refuses every construct a markdown
  renderer would turn into emphasis, a heading or a live link.
- **Known gap — artifact content is served without a Content-Security-Policy.**
  `GET /v1/artifacts/{id}/content` returns the stored bytes with the stored `mime_type` and
  `Content-Disposition: inline`, and sets no CSP header. Nothing in the frontend renders artifact
  content today (runs list artifacts as reference rows only), so there is no active rendering
  surface — but an operator who builds one, or a user who opens that URL directly, gets unsandboxed
  content in the app origin. Treat stored artifact bytes as untrusted.

Secret hygiene is enforced rather than remembered: gitleaks and `detect-private-key` run in
pre-commit, `.env` and credential files are untracked, and secrets must not appear in code,
fixtures, examples or docs.

### Known gaps

Stated plainly so nobody wastes a report proving them, and so operators know what they are running:

- The platform JWT is not yet minted per action; `capability_scope` is the compensating control
  until it is.
- `MULDRO_OAUTH_ENCRYPTION_KEY` is optional in development and **required in production** — startup
  validation enforces this only when the environment is marked production. Running a real
  deployment without it stores OAuth tokens unencrypted.
- Muldro is pre-launch with no production deployment and no real user data. There is no incident
  history and no established rotation process yet.

Reports that deepen or exploit these gaps are still welcome — knowing about a gap is not the same
as having demonstrated its impact.

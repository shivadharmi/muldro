# OSS Release Audit & 1-Month Roadmap

Date: 2026-06-12
Goal: Open-core release in ~1 month (full-time)
Inputs: 6-dimension parallel audit (security, OSS/legal, setup friction/cost, architecture, A2UI sizing, soul alignment)

## Central Finding

The product violates its own soul document, and that is why its builder doesn't use it daily.
`soul.md` promises calm competence, low cognitive load, and "real leverage, not agentic theater."
The implementation delivers:

1. **Latency theater** — every message, even "what's on my calendar?", runs intent-classify →
   (often) Opus planner with 8K thinking → agent execution → Presenter (hardcoded `if True:`).
   Best case 5–10s, worst 14–26s, vs ~2–3s for ChatGPT/Claude directly.
2. **Visual theater** — agent cards, thinking blocks, token costs, plan badges shown by default
   on every reply. Cargo-cult transparency.
3. **A proactive loop that never fires** — all schedules seed `enabled=False` until first OAuth;
   insights queue silently when no notification surface (Telegram/Slack) is onboarded. The
   "calm strategist" value proposition is structurally unreachable for a new user.
4. **Developer-facing settings** — trust ladders, policy matrices, budget bars exposed on first open.
5. **Misaligned economics** — ~$0.50/message, ~$20+/day actual cost vs $5/day default budget;
   system silently degrades after 2–3 messages.

Open-core viability depends on fixing the soul gap first; everything else is packaging.

## Verified Facts (resolving agent disagreements)

- `.env` / `client_secret.json`: present in working dir, **never committed**, history clean.
  No scrub needed. Key rotation optional hygiene. Ensure they stay untracked.
- The 53 uncommitted files: coherent, complete Jira→Atlassian MCP migration + token lifecycle +
  Voyage embeddings + model bumps. Verdict: commit as 2–3 logical commits.
- Qdrant/Neo4j/MinIO: already optional with graceful degradation. Packaging problem, not architecture.
- Dependency licenses: all permissive (Apache/MIT/BSD). No GPL contamination. No LICENSE file yet.
- "Jarvis": 1,647 occurrences; Marvel/Disney trademark exposure; rebrand ≈ 3–5 days.

## Release-Blocking Issues (must fix)

| # | Issue | Source | Effort |
|---|-------|--------|--------|
| B1 | IDOR: `GET /v1/realtime/runs/{run_id}` streams any run without ownership check (`routes_realtime.py:185-243`) | security | 1h |
| B2 | No LICENSE file | legal | 1h |
| B3 | Secrets files in working dir (never add; add CI guard) | security | 1h |
| B4 | Anthropic key missing → silent empty string, cryptic first-chat failure | setup | 0.5h |
| B5 | $5 default budget vs $20/day actual cost | setup | 1h |
| B6 | `docs/superpowers/` (87 internal planning files) must not ship | legal | 2h |
| B7 | OAuth encryption key fallback-to-plaintext must raise in production | security | 0.5h |
| B8 | Rebrand decision (name change before public push, or accept trademark risk) | legal | 3–5d if yes |

## High-Priority (should fix)

- Per-endpoint rate limits on approvals/history/verify; CORS tighten to explicit methods/headers.
- Presenter skip for single-step read-only plans (latency fix, BIGGEST product fix).
- Hide agent cards/thinking/cost by default; one summary line, expand on click.
- Enable briefing + observation schedules at workspace creation; dashboard shows briefing
  surface by default ("gathering data..." empty state).
- Settings collapsed to user-language (3 tabs; progressive disclosure of trust/policy).
- Onboarding: first-load guided "connect your first source" card.
- One-command startup: backend+frontend in docker-compose, `.env.minimal`, README 4-step quickstart.
- Cheap mode: all-Sonnet config (~$0.17/msg, −65%); document tiers.
- CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md.
- `lib/api.ts` (897 lines) split; `routes_auth.py` (1,036 lines) split into provider modules.

## A2UI Hybrid Migration (approved direction)

Classification (from audit):
- **TYPED-KEEP (3):** approval, execution, proactive_insight — TrustEngine-wired, live-updating, action-bearing.
- **ARTIFACT-REPLACE (10):** briefing, summary, plan, checklist, recommendation, alert, timeline,
  table, activity, comparison — render-only.
- **HYBRID (1):** message.

Deletion dividend: ~3,000 LOC. Agent's 13.5-day plan assumed production canary; pre-release with
one user this compresses to **~6–7 days**: schema column + Presenter HTML rendering (~2 days) →
IframeSandbox with strict CSP (~2 days) → switch + delete builders + migrate tests (~2–3 days).
No feature flag, no canary. Telegram keeps Markdown path (artifact kinds never delivered there).

## Deferred (post-release)

- jarvis.py decomposition / pipeline unification (design exists: event-generator core; revisit
  after release when characterization tests can be justified).
- Open-core module split + BSL premium licensing (license core Apache-2.0 now; split when a paid
  tier actually exists).
- pgvector replacement of Qdrant; perception simulator; HA deployment.

## Decisions (2026-06-12, approved by Siva)

1. **Rebrand: YES** — rename before public release (name TBD by Siva; rename execution ~3–5 days, Week 4).
2. **License: Apache-2.0** for the core now; BSL/proprietary modules only when a paid tier exists.
3. **Soul fixes: ALL approved** — Presenter skip for single-step reads, theater hidden by default,
   schedules enabled at workspace creation, budget default + cheap mode.
4. **A2UI hybrid migration: Week 3, pre-launch.**

## 4-Week Plan

**Week 1 — Make it daily-usable (soul fixes) + critical security**
Commit Atlassian migration. Fix B1, B4, B5, B7. Presenter skip for reads; hide theater;
schedules on by default; budget/cheap mode. Then USE IT DAILY — every annoyance is a bug.

**Week 2 — First-run experience + hardening**
One-command compose, .env.minimal, quickstart. Rate limits, CORS. Settings simplification,
onboarding card. Start dogfooding journal → fixes.

**Week 3 — A2UI hybrid migration (~6–7 days compressed)**
Presenter HTML + IframeSandbox + delete 10 artifact kinds' builders + test migration.

**Week 4 — Rebrand + packaging + launch**
Rename (if decided), LICENSE + governance docs, docs/superpowers removal, README polish,
demo GIF/video of the proactive loop, repo squash-review, publish.

## Success Criteria

1. Builder uses it daily by week 2 (the only credibility metric that matters for open-core).
2. Fresh clone → chat in ≤10 minutes with only an Anthropic key.
3. Zero release-blocking security findings.
4. A reviewer reading the repo cold finds: license, contributing, architecture docs, no internal noise.

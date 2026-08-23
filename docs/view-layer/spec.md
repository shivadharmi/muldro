# View layer — frame and body

> **Status:** implemented and merged (PR #21). This is the authoritative contract for the view layer.
> **Supersedes:** the A2UI surface system, removed in PR #21.
> **Design reference:** [`mockup.html`](./mockup.html) — the rendered specimens are normative. Where
> this document and the mockup disagree about *appearance*, the mockup wins; where they disagree about
> *contract*, this document wins.
> **Judged against:** `docs/soul.md`. Binds to `CLAUDE.md`'s capability and trust invariants.

---

## 0. Scope

This replaces the system CLAUDE.md calls "A2UI" — every path from *"the autonomous loop noticed
something"* or *"the lead answered a question"* to *"something appears on screen"*.

In scope: the workspace feed, the card, the detail view, chat-turn presentation, the ranking that
orders the feed, and the contract the model writes against.

Out of scope: the trust and permission gates (`CLAUDE.md` § *One runtime, gated at action-time*),
the deep runtime, connector authentication. This spec consumes their outputs and changes none of them.

---

## 1. The problem, traced

Six defects visible in one screenshot of the live workspace, each traced to a line.

| # | Symptom | Mechanism |
|---|---|---|
| 1 | Three identical cards reading "New activity" | `perception_runner.py:279` builds one `PerceptionSignal` per **poll cycle**, not per thing. Three polls, three signals, three cards. `_clean_insight_title` (`surface_pusher.py:79`) fell back to a constant because the events carried no usable subject. |
| 2 | Title and body each printed twice on one card | `surface_pusher.py:463-478` assigns the same string to `preview.title` and `insight_data.signal_summary`; `SurfaceCard` and `InsightSurface` each render it, neither aware of the other. |
| 3 | The same sentence at two lengths | `preview.subtitle = reasoning[:200]`; `relevance_reasoning` is the same sentence untruncated. |
| 4 | Different buttons on identical signals | `SuggestedAction.capability` is a free string the model invents. `routes_ws.py:478` takes it verbatim into a `PlanOutput` and runs it. The label the founder reads and the capability that executes are two independent model outputs with nothing checking they agree. |
| 5 | "1 new event(s)" presented as evidence | `evidence_count` / `evidence_unit` are model-authored fields (`relevance_assessor.py:38-42`). The model echoed pipeline prose back as proof, in system voice. |
| 6 | Chevrons that open to nothing | Both insight producers hardcode `detail_config=None`. `rec_{i}` ids resolve to a `_VirtualSurface` with no `preview` and no `workspace_id`, so `recommendation.py:104,123` — which decides what to show by **substring-matching the card's English title** — always takes its empty branch. |

Not visible, and more serious: `surface-card.tsx:163` renders `preview.title` through `InlineMarkdown`,
and that title derives from `connector_poller.py:355`'s `raw.title or raw_data["subject"]` — the raw
email subject. An inbound message whose subject is
`**URGENT** — [Verify your account](https://phish.example)` renders as bold text and a live
`target="_blank"` link **in the card headline, in muldro's voice, with no sender attributed**. Raw HTML
is inert (`react-markdown@10`, no `rehype-raw`), so this is link injection rather than XSS — a phishing
surface wearing muldro's credibility.

Three structural facts sit under all of it:

- **There is no editorial layer.** Nothing decides what a thing *is*, whether it is the same as the
  thing beside it, how important it is, or what shape it takes. Each of those is decided implicitly —
  by a poll boundary, a free-text model field, or which builder function ran first.
- **The card has no anatomy.** `surface-card.tsx` renders ~19 independently-conditional rows. Two cards
  of one kind are not the same shape; they are the shape of whichever fields the model populated.
- **`render_surface` has never rendered anything.** It emits `surface_id`; the WebSocket handler guards
  on `msg.surface?.id` (`use-muldro-ws.ts:127`) and drops it. It returns `{"status": "published"}`
  regardless, so the model believes it worked and writes only the short chat companion. It also uses a
  `srf_` prefix where `_PREFIX_MAP` knows `surf_`, never persists to the DB, and puts `sections` at a
  level the card does not read. Four independent breaks; the first masks the rest.

---

## 2. The model

**Code owns the frame. The model writes one markdown body. External text is quoted by code and enters
neither.**

One principle, applied three times. Wherever a decision could go either way, the deterministic half is
the container and the model's half is the contents:

| Container (code) | Contents (model) |
|---|---|
| the frame | the body |
| the ranking | the argument for importance, in prose |
| the lede budget | the words inside it |

### 2.1 Authorship

Three origins, one meaning each. The mockup encodes them as cyan / violet / amber and uses no other
colour for meaning.

| Origin | Who | Where it may appear |
|---|---|---|
| **code** | muldro's own logic, over a database row | frame: headline, kind, status, timestamps, counts, button labels |
| **model** | an LLM writing muldro's own prose | body — and nowhere else |
| **external** | an email subject, a message body, a fetched page | the `quotes` band only, verbatim, attributed |

The rule that matters is enforceable rather than instructed: *"do not quote email text in your summary"*
cannot be enforced, because the model holds the text and cannot prove it didn't use it. **The body slot
never carries external provenance because external values arrive on a different field that only code
renders.**

This applies to **both** producers. A chat turn puts Gmail and GitHub tool results into the lead's
context; the lead can echo an issue title verbatim into its body. Same laundering, different door.

### 2.2 Contracts

```python
# src/view/contracts.py

class Frame(BaseModel):
    """Built by code from a domain row. No field here is model-authored."""
    model_config = ConfigDict(frozen=True)

    key: str                      # identity + dedup: f"{source}:{entity_type}:{entity_id}"
    group_key: str | None = None  # cross-source correlation; null until §7 lands
    kind: FrameKind               # closed Literal — code chooses, never the model
    status: FrameStatus
    headline: str                 # PLAIN TEXT. Never markdown. Never external provenance.
    source: str
    occurred_at: datetime
    updated_at: datetime
    importance: float             # §6 supplies this. NOT NormalizedEvent.importance_score,
                                  # which is LLM-authored from the subject line (inv. 4 and 8).
    affordances: list[Affordance] # real capabilities, code-authored labels

class Quote(BaseModel):
    """External text. Copied by code, never interpolated into `body`."""
    model_config = ConfigDict(frozen=True)
    text: str
    who: str                      # a named human or account
    when: datetime

class Unit(BaseModel):
    """What the renderer receives. The only object in the view layer."""
    model_config = ConfigDict(frozen=True)
    frame: Frame
    body: str                     # ONE markdown field — the model's entire contract
    quotes: list[Quote] = Field(default_factory=list)
    detail: Detail | None = None  # §5; absent on the Glance
```

`FrameKind` is `Literal["proposal", "finding", "run", "record", "briefing"]`. It is chosen by code from
the domain row's type and state. The model never sees it as writable — this is deliberate: choosing the
frame is choosing the salience, and an injected email that gets itself rendered as `alert` rather than
`finding` has escalated its own priority without touching a word of copy.

### 2.3 The body contract

A body can legitimately run long (a research finding, a briefing). It cannot all fit on a card, and the
answer is not to cut it.

- **The first paragraph is the lede** — a complete, self-contained claim.
- **The Glance renders paragraph 1. The Full renders the whole document.** The Glance is therefore a
  *semantic* prefix of the Full, not a character-count one, so the two cannot disagree.
- **The lede budget belongs to `frame.kind`**, set in code:

  | kind | lede budget | full body |
  |---|---|---|
  | `proposal` | 140 chars | short — one thread, one reason it needs you |
  | `finding` | 180 chars | unbounded — research and synthesis are legitimately long |
  | `briefing` | 90 chars | unbounded — the lede sits in a list of peers and must scan |
  | `run` | 120 chars | short — the steps carry the detail |
  | `record` | 120 chars | short |

- **Overrun is a validation failure** — not a truncation, not a prompt request. `validate_body(body,
  kind)` raises `BodyBudgetError` whose message is written *for a model to read*: it names the budget
  and says to rewrite paragraph one as a self-contained claim. That message is the repair prompt.

  **There is no existing loop to reuse.** An earlier revision of this section pointed at
  `deep_runtime/middleware/repair_cap.py` as "the same mechanism that already rejects bad tool
  arguments" — and that description is exactly why it cannot serve. `repair_cap` is a LangGraph
  middleware wrapping the *tool dispatcher*; it counts failed **tool calls**. A body is prose from a
  text completion and never passes through it. The generation-repair loop is **its own small piece of
  work**, mirroring `repair_cap`'s cap of 3 rather than reusing its code.

- **The body is a stored row, not a derived value.** Invariant 1 says a view is a pure function of a
  domain row and no view reads a cache — and a body costs a model call, so it cannot be recomputed on
  every feed refresh. Therefore the body must *be* a row. The `Unit` is then a pure projection of two
  row sets: the frame and quotes from `normalized_events`, and the body from its own row. Regeneration
  is a structural check, not a timer: when `frame.event_count` changes, a new message arrived and the
  stored body no longer describes the thing. **Whether that row is `Finding` (§9 — which already
  carries `claim`, `body`, `sources`, `as_of`, `stale_after`) or a separate one is open (§13.6).**
- **The Glance renders inline markdown only** — emphasis and code spans. No headings, lists, tables or
  fences (they destroy a 320px grid cell regardless of length), and **no links**: a link inside a body
  reopens injection through a different door. The model names a source; the frame links it.
- **No ellipsis and no "read more."** A complete sentence plus an open affordance says *there is more*;
  a `…` says *this was cut*.

This replaces **seven disagreeing truncation rules**: `_clean_insight_title(max_len=120)`,
`reasoning[:200]`, `title[:100]` in `slack_connector.py`, two `_truncate` calls in the detail builders,
CSS `line-clamp-2`, and `MAX_INLINE_SECTIONS` + `max-h-[280px] overflow-hidden`.

A short body cannot produce an empty Full: the Full still carries the source content, the derivation and
the affordances (§5). *"Card shows info, modal shows nothing"* has no route back in.

---

## 3. Identity — how the frame scales across connectors

**The frame is not a new thing to build per connector. It is a projection of `NormalizedEvent`,** which
every connector already fills and which already carries `source`, `entity_type`, `entity_id`,
`occurred_at`, `actor_entities`, `importance_score`, `urgency_score`, `correlation_id` and
`idempotency_key` — plus an index on `(user_id, source, entity_id)`. The perception layer currently
discards it and rebuilds a worse frame by concatenating rows into prose.

| Source | `event_type` | `entity_type` | `entity_id` | `title` |
|---|---|---|---|---|
| gmail | `email_received` | `email_thread` | **`threadId`** — the thread | subject · untrusted |
| slack | `message_posted` | `message_thread` | **`thread_ts`** — the thread | `#chan: text` · untrusted |
| calendar | `event_created` / `event_updated` | `meeting` | event id — the meeting | summary · untrusted |
| notion | `page_created` / `page_updated` | `page` | page id — the page | title · untrusted |
| github | `pr_updated` / `issue_updated` | `pullrequest` | **notification id — an occurrence** | `[repo] title` · untrusted |

Two consequences:

1. **Four of five sources already key on a durable thing.** `frame.key` is
   `f"{source}:{entity_type}:{entity_id}"` — supplied by the source system, stable by construction.
   **Identity must be deterministic or dedup is not dedup:** an inferred key lets two runs of one
   pipeline mint two keys for one thing, which is defect 1.
2. **GitHub is the lone outlier** and it is a one-line fix. `github_connector.py:189` uses
   `notif.get("id")`; `raw_payload["url"]` already holds the PR's own API URL. Same file,
   `actor={"type":"system","name":repo}` attributes the event to the repository and discards the human
   who commented — that human is the counterparty the headline needs.

`title` is untrusted in **all five**, uniformly, so the rule that fixes it is one rule at one boundary.
Adding a sixth connector means filling in five fields, not building a card.

---

## 4. The Glance

The card's job is **"does this need me?"** — triage. The decision itself happens in the Full. The fix for
a thin card is a *correct* card, not a fuller one; nineteen slots is what "put more on the card" already
produced.

**The Glance must be uniform.** You cannot rank things that do not look alike, and ranking is the entire
feed.

### 4.1 Anatomy — normative

Exactly six slots, in this order, no others, none conditional except where marked. See
[`mockup.html` § *One shape, three bands*](./mockup.html).

| # | Slot | Author | Contents |
|---|---|---|---|
| 1 | header | code | kind pill · status pill · spacer · relative timestamp |
| 2 | headline | code | `frame.headline`, **plain text**, 2 lines max |
| 3 | context line | code | `source · entity_type · N messages` — mono, muted |
| 4 | lede | model | paragraph 1 of `body`, inline markdown only, 3 lines max |
| 5 | quote | code | *(only when `quotes` is non-empty)* first quote, 2 lines, attributed |
| 6 | affordances | code | up to 3 buttons + dismiss |

Deleted from the current card: risk pill, flags, trust badge, item bullets, embedded `InsightSurface`,
evidence micro-line, step list, progress bar, token/cost row, metrics row, entities row, tags row,
inline `A2UIRenderer` sections. Their information belongs to the Full, to the frame's status, or nowhere.

### 4.2 Tokens — normative

The mockup's specimens use muldro's live palette. Implementation uses the existing Tailwind mapping in
`frontend/src/app/globals.css`; **no new colour is introduced.**

| Element | Class |
|---|---|
| card | `bg-surface-1` · `border border-b-secondary` · `rounded-[var(--radius-lg)]` · `p-4` · `gap-2.5` |
| kind pill | `text-[10px] font-medium px-2 py-0.5 rounded-[var(--radius-sm)]` + per-kind soft pair |
| status pill | `StatusBadge` — dot + Title-case label from `STATUS_LABELS` |
| headline | `text-[13px] font-medium text-t-primary line-clamp-2 leading-snug` |
| context line | `text-[11px] text-t-muted font-mono` |
| lede | `text-xs text-t-tertiary line-clamp-3 leading-relaxed` |
| quote | `border-l-2 border-j-warning bg-j-warning-soft` · `text-xs italic text-t-secondary` |
| attribution | `text-[10px] font-mono text-j-warning` |
| primary button | `bg-j-primary text-j-primary-fg` · `text-xs px-3 py-1.5 rounded-[var(--radius-md)]` |
| secondary button | `bg-surface-2 text-t-secondary` |
| dismiss | `text-t-muted` · plain, no background |

Per-kind pill pairs come from `design-tokens.ts`'s **existing** `kindStyle()` and `KIND_LABELS`, which
today are exported and used by nothing while `surface-card.tsx` ships private copies that have drifted
(`kindStyle("run")` returns grey; the local `kindColor.run` returns `bg-j-info-soft`). **The local copies
are deleted and the token functions become the only definition.** Same for `priorityStyle()`.

`PRIORITY_LABELS` is added alongside `STATUS_LABELS`; priority is currently the only badge printed
verbatim from the wire, which is why it reads lowercase next to Title-case neighbours.

### 4.3 Grid

`gridAutoFlow: dense` is removed. Dense packing visually reorders cards, which destroys the ordering
§6 establishes. Cards flow in rank order, top-left to bottom-right.

---

## 5. The Full

The Full's job is **"what do I do about it?"** — the decision. **The Full must be faithful**: an email
thread rendered through a generic detail view stops being an email thread.

Uniformity and faithfulness are not in conflict once the two levels have different jobs.

### 5.1 Four layers — normative

See [`mockup.html` § *What a Full view actually is*](./mockup.html). Layers 2, 3 and 4 are structurally
identical for every source; **only layer 1 varies.**

| Layer | Author | Contents |
|---|---|---|
| — | code | the same frame the card carried — identity, kind, status, source, counts |
| 1 | code | **the thing itself**, by archetype (§5.2), verbatim and attributed |
| 2 | model | **muldro's reasoning** — the whole `body`, block markdown |
| 3 | code | **the derivation** — what it read, which goals and memories matched, and what it connects to across sources |
| 4 | code | **the affordances** — act here |

Layer 4 is why this is not a link to the source: a link lets you read; this lets you act. **Layer 3 is
the real answer.** *"Friday is the day before your board meeting, and the term sheet is on that agenda"*
requires gmail **and** calendar **and** the goal graph, joined. No amount of clicking through to the
source produces it, and it can only exist somewhere that holds all three.

Tabs are removed. `_TABS_BY_KIND` mapped `summary` to Steps/Plan/Events/Trace because nobody could say
what a summary *was*; four layers replace nine tab lists.

### 5.2 Archetypes

The per-source cost is bounded to the least valuable layer. Grouping by the `entity_type` connectors
already emit gives **four archetypes, derived from real content rather than chosen up front** — which is
why the last catalog had seventeen components.

| Archetype | `entity_type` | Shape |
|---|---|---|
| **Conversation** | `email_thread`, `message_thread`, `pullrequest`, `issue` | ordered, attributed, timestamped messages |
| **Change** | `pullrequest`, `commit` | state · approvals · checks · diff summary |
| **Event** | `meeting` | time · attendees · agenda · conflicts |
| **Document** | `page`, `doc` | rich-text tree · last editor · changed region |

A pull request maps to **two** archetypes and renders both. Adding a connector is a mapping line —
*"Linear issues are Conversation plus Change"* — not a new renderer.

Every archetype already has a read capability: `email.read`, `messaging.get_thread`, `issue.get` +
`repo.get_diff` + `repo.get_checks`, `calendar.get`, `doc.get`.

**All layer-1 content is `external` origin** and renders through the quote treatment — attributed, never
reformatted, never in muldro's voice.

### 5.3 Where layer 1 comes from

**Store the normalized archetype at perception time; refresh live on open.**

Fetching only on open is never stale but fails exactly when needed — a source whose circuit is open is
precisely when a card is worth reading, and clicking into *"couldn't load"* is worse than a thin card.
Storing everything is instant but goes stale and quietly commits muldro to keeping full email bodies in
Postgres.

The hybrid is **one renderer over one archetype shape with two possible data sources**; the renderer
cannot tell which it received, so this is not two code paths. Storing the *archetype* rather than the raw
provider blob bounds both storage growth and data sensitivity: what persists is the messages muldro
actually reasoned over, not every header the provider returned.

The mechanism exists. `_fetch_thread_contexts` (`perception_runner.py:35`) already fetches full Gmail
threads via `get_gmail_thread_content` — and then uses them to build a prompt and throws them away.

---

## 6. Ranking

There is currently **no ranking function at all.** Server order is the order builders run in
(`surface_builder.py:52-68`); client order is arrival order (`surface-store.ts:57`); the CSS grid then
repacks both. Three independent non-decisions, stacked.

### 6.1 The ranker

**A list-ranker over sanitized features** — not an item-scorer over raw content.

Some signals compare across sources: recency, addressed-to-you, reply-to-something-you-sent, unresolved
affordance present. **Importance itself does not.** There is no shared scale on which a `#general`
message, a review request and an investor email can be ordered, and a hand-tuned weight per source is a
guess at commensuration a model does better. That is the case for scoring, and it holds.

Two things must be separated for scoring to be safe:

**What it reads.** `relevance_assessor.py`'s prompt currently interpolates `Summary: {summary}` — raw
subjects and thread bodies — so *"THIS IS EXTREMELY URGENT"* in a subject line is an instruction to the
scorer. The ranker instead reads muldro's **derived** facts:

```
counterparty      → known entity, relationship, prior thread count
thread            → message count, whether you replied, how long since
deadline          → typed date, extracted at ingestion
goals             → which goals this matches
affordance        → is there an unresolved decision
engagement        → dismissal penalty for this (source, event_type)
recency           → occurred_at
```

The sender's prose never reaches it. An attacker can lie about *when*; they cannot inject an
*instruction*. Extraction remains a boundary — a deadline lifted from a body is attacker-influenced —
but it enters as a typed date, which is bounded and checkable.

**What it ranks.** Today it scores one signal alone, which is why it cannot commensurate and why `0.7`
means nothing. **The ranker orders the whole open feed in one call**, producing a relative order for one
call per refresh rather than one per item.

### 6.2 Engagement is demotion only

`EngagementService.get_relevance_penalty` already implements the safe half. **Promotion by engagement is
self-sealing:** rank drives visibility, visibility drives engagement, so a low-ranked type is never seen,
never engaged, and sinks permanently — the founder could never discover they do care about Notion edits.
Demotion has no such loop, because a thing had to be seen to be dismissed. Promotion requires deliberate
exploration, which spends founder attention on purpose and is premature with no history.

---

## 7. Grouping

`frame.group_key` is nullable and unset at first. When the correlation layer lands it groups units across
sources — the Acme thread in gmail and the Acme channel in slack become one unit. `correlation_id` exists
on `NormalizedEvent` and `EventCorrelator.detect_thread` exists, currently used only to decorate a
planner prompt.

**A merge must be reversible from the UI, or it does not ship.** A wrong rank the founder routes around;
a wrong merge puts two contexts under one headline in muldro's voice and cannot be undone. The unit
carries a *"these aren't the same thing"* affordance that writes a negative correlation.

Goals are a **view** that groups by `group_key`. They are not a third identity scheme.

---

## 8. Producers

### 8.1 Both paths, one shape

| | Autonomous loop | Chat turn |
|---|---|---|
| Produces a `Unit` | always | when the turn created a durable row |
| Ephemeral prose | never | otherwise — prose in a bubble |
| `frame` author | code, from `NormalizedEvent` | code, from the row the turn created |
| `body` author | model | model |

A chat turn is already framed — by the thread, the timestamp, the message just sent — so it does not need
a card for every reply. The autonomous loop has no such channel: nobody is watching, so anything it does
not persist never happened.

### 8.2 Promotion is structural

*"Did this turn produce something durable"* is answered by what the turn **did** — a run row was created,
a write was staged as an approval, a finding was recorded — never by asking the model whether its answer
was interesting.

`message_promotion.py` already states this principle (*"the gate is structural, not semantic — the agent
does not self-evaluate usefulness"*) and is correct. It is currently applied to a dead path. The module
keeps its name and its principle; `_STRUCTURAL_COMPONENT_TYPES` and its tree walker are deleted, and the
input becomes what the turn created.

### 8.3 Chat rendering

Prose renders through the existing `MarkdownRenderer` (react-markdown + remark-gfm), which already
handles GFM tables well. When a turn produces a `Unit`, the **same** renderer the feed uses is placed
inline in the thread. One vocabulary, two placements.

---

## 9. Findings

Chat answers currently produce prose and nothing else; there is no `Finding` table. The durable objects
that exist are `TaskRun`, `Approval`, `Briefing`, `Memory`, `NormalizedEvent` and `InteractionLog`.

```python
class Finding(Base, TimestampMixin):
    __tablename__ = "findings"
    finding_id: str          # fnd_<ULID>
    workspace_id: str
    user_id: str
    # NO `claim` COLUMN. An earlier revision had `claim: str  # the body's lede`,
    # which invariant 7 forbids: "the lede is paragraph 1 of `body` — not a separate
    # field." A stored claim is a second projection of one string, free to drift from
    # the body it summarises. That is defect 3 from §1 — the same sentence at two
    # lengths — reintroduced as a schema column. The lede is `lede_of(body)`.
    body: str                # full markdown; paragraph 1 IS the claim
    sources: list[dict]      # non-empty; a body with no derivation is not renderable
    derivation: dict         # which tools, which arguments, which rows — enough to recompute
    as_of: datetime
    stale_after: datetime
    status: str              # open | seen | superseded
```

**A finding stores its own derivation.** Storing the claim alone is a cache wearing a confidence badge —
*"you have four active repos"* is true for an hour, and a stale finding is muldro confidently saying
something false. Fresh, it renders. Stale, it re-derives on view. That is the same invariant as
everywhere else: a view is a pure function of a row, and this row includes how to recompute itself.

Findings do not expire. Ranking controls attention; deletion is not an attention mechanism, and you
cannot learn from silence you deleted.

This hands the watch feature over later for free: a finding with a short `stale_after` that the founder
keeps opening is, definitionally, something to watch.

---

## 10. Invariants

Stated so they can be tested.

1. **A view is a pure function of a domain row.** No view reads a cache. Corollary: Glance and Full
   cannot disagree, and *"card shows info, modal shows nothing"* is unrepresentable.
2. **`frame.headline` is plain text.** It is never passed to a markdown renderer.
3. **No external-origin value reaches a frame field un-neutralized.** Enforced two ways, and it is
   worth being exact about which is which, because the difference is what is testable:
   - **`headline` is enforced at the type boundary.** Its validator refuses every construct
     `remark-gfm` would turn into emphasis, strikethrough, a heading or a live link — including all
     three GFM autolink forms (`https?://`, `www.`, bare email) and the CommonMark `<scheme:>` form —
     plus raw newlines and control/bidi-override characters. It also bounds length rather than
     refusing it, because a refused headline is a card the founder never sees.
   - **Every other field rests on there being exactly one construction site.** `frame_for_event` is
     the only place a `Frame` is built from perception, and it neutralizes external text before
     construction. This is a *structural* guarantee, not a type-level one: a plain `str` carries no
     origin, so nothing in the type system distinguishes a code-authored string from an external one.
     `key` in particular is built **from** an external `entity_id` by construction and must be.

   The stronger form — an origin-carrying type (`External = NewType("External", str)`) that `Frame`
   fields refuse — would make this enforceable rather than merely disciplined. It is not built.
4. **The model authors exactly one field.** `body`. It authors no structure, no kind, no capability, no
   count, no score.
5. **Every affordance names a capability in `CAPABILITY_CATALOG`.** A label is code-authored. An
   affordance whose capability does not resolve is not rendered.
6. **`frame.key` is deterministic.** Given the same `NormalizedEvent`, two runs produce the same key.
7. **The lede is paragraph 1 of `body`.** Not a separate field, not a substring by character count.
8. **The ranker's inputs contain no external prose.** Only typed, derived features.
9. **Nothing expires.** No `expires_at` anywhere in this design.
10. **Promotion is structural, never semantic.** *(Kept from `message_promotion.py`.)*

Test surface: `frame.key` determinism and github's PR-not-notification key (6); for (3), a fuzz over
adversarial subjects asserting `frame_for_event` never yields a headline its own validator would
refuse and never raises — which pins the *relationship* between the neutralizer and the validator, so
changing either alone fails; a differential test that the backend `lede_of` and the frontend `ledeOf`
agree on one corpus (7, and invariant 1's corollary — two implementations of "paragraph 1" that
disagree is how the Glance and the Full drift apart); `rank()` against ordering cases rather than
eyeballing (§6); lede-budget overrun triggers repair rather than truncation (7); an affordance with an
unknown capability is dropped (5).

---

## 11. Deleted

Deletion-first. Nothing below survives behind a flag; each is removed in the same commit as its
replacement.

**Backend**

- `render_surface`, `RenderSurfaceInput`, `AnyComponent`, `push_ui_update`'s surface branch
- `SurfaceSpec`, `SurfacePreview` (19 optional fields), `SurfaceDataPayload`, `InsightSurfaceData`,
  `A2UISurface`, `A2UIComponent`, `SurfaceKind`
- `extract_surface_spec`, `extract_surface_data`, `strip_surface_blocks`, both fence regexes
- `src/ui/` — `contracts.py`, `renderer.py`, `units.py`, `component_properties.py`, `components.py`,
  `strict_schema.py`
- `services/surface_builder.py`, `surface_mapping.py`, `surface_detail_builders/`
- `orchestrator/surface_pusher.py`, `_clean_insight_title`, `_clean_event_subject`,
  `_build_action_preview`
- `api/routes_surface_detail.py`, `_PREFIX_MAP`, `_resolve_ephemeral`, `_normalize_legacy_run_id`
- `_TABS_BY_KIND`, `TAB_BUILDERS`, `build_detail_config`, `derive_surface_kind`
- `SuggestedAction.capability` as free string; `evidence_count`; `evidence_unit`; `format_evidence`
- `assess_relevance`'s per-signal score and its raw-content prompt
- `message_promotion.py`'s `_STRUCTURAL_COMPONENT_TYPES` and tree walker *(module and principle kept)*
- `models/ui_state.py` — `UISurface` and the `ui_surfaces` table, `expires_at` included (migration drops)
- `prompts.py`'s `<surfaces>` block

**Frontend**

- `components/a2ui/` in full — renderer, 17 components, `safe-props.ts`, `action-handler.ts`
- `lib/a2ui-types.ts`, `lib/types/surfaces.ts` including `normalizeSurfaceKind`'s runtime drift warning
- `lib/surface-merge.ts`, `workspace/surface-detail-modal.tsx`
- `surface-card.tsx`'s private `kindLabel` / `kindColor` / `priorityBadge` maps
- `gridAutoFlow: dense`
- Seven truncation rules (§2.3)

**Kept, re-homed**

- `inline-approval.tsx` → the `Decide` affordance renderer. Its countdown, risk pill and
  reject-with-reason are good; they were built in the wrong place.
- `step-list.tsx` / `step-presentation.tsx` → the Run frame's status. Consolidates three divergent step
  renderers.
- `MarkdownRenderer` → the body renderer, unchanged. `InlineMarkdown` → the lede renderer, pointed away
  from titles.
- `_verify_ephemeral_ownership`'s refusal to distinguish *not yours* from *not found*.
- The Glance/Full two-level split — the one right bone in the old design.

---

## 12. Build order

Ordered by dependency. Each step lands complete with its duplicate removed in the same commit — a second
parallel path is how this was arrived at.

| # | Step | Gate | State |
|---|---|---|---|
| 1 | `Frame` from `NormalizedEvent`; github keys on the PR | property test: same event → same key; three duplicate cards become one that updates | **landed** |
| 2a | The body **contract** — one markdown field, headline plain text, `quotes` band | fuzz: no adversarial subject yields a headline the validator would refuse, and none raises | **landed** |
| 2b | The body **generator** — who writes it, the repair loop, the row it lands in (§2.3) | a body that overruns its budget is rewritten, not truncated; a card's prose survives a restart | **landed** |
| 3 | Glance renderer; delete the 19 slots and the private token maps | every card of a kind is the same shape; `kindStyle()` is the only definition | **landed** |
| 3b | **Transport** — a `Unit` reaches the screen over REST and WebSocket | the workspace renders a real `Unit`, not a surface adapted by `unitFromSurface` | **landed** |
| 3c | **Cutover and deletion** — §11 executed, both halves | `grep` finds no `A2UISurface`, no `surface_pusher`, no `components/a2ui/`; nothing renders an adapted surface | **landed** |
| 4 | List-ranker over derived features | `rank()` unit-tested against ordering cases; no external prose in its inputs | **landed** |
| 5 | Full view, Conversation archetype | covers gmail, slack and github discussions — three of five sources in one renderer | not built |
| 6 | Change, Event, Document archetypes | a PR renders both of its archetypes | not built |
| 7 | `Finding` with derivation | a stale finding re-derives on view rather than rendering stale | not built |
| 8 | `group_key` correlation | ships with a reversible-merge affordance or it does not ship | not built |

**Steps 2b, 3b and 3c were missing from an earlier revision of this table, and that omission
propagated.** The order went "Glance renderer" → "ranker" with no step for *how a Unit reaches the
screen* and none for *who writes its prose* — so a plan written against it built the contract, the
identity and the renderer, and left the product unable to display anything. A build order that names
only the contracts will produce exactly that: a correct, complete, unreachable system. **A step that
says "and it is now the only path" belongs beside every step that says "build the new one."**

**Steps 1–3c are the screenshot.** Nothing there needs the ranker, the archetypes or the correlation
layer to stop being broken — but 3b and 3c are what make 1–3 visible, and until they land the founder
still sees the old cards.

Two orderings are forced. **2a before 2b**: everything downstream assumes values arrive with an origin,
and retrofitting provenance is how the current system got here — the containment must exist before the
thing it contains. **3b before 3c**: the old path is deleted when the new one replaces it, never before.

---

## 13. Open

1. **The lede budgets' actual numbers.** 140/180/90/120 are starting points, not measurements. They want
   a pass over real generated bodies.
2. **`rank()`'s feature weights.** The feature *set* is specified; how the model is asked to weigh them
   is a product decision. Soul says *"surface what matters, not compete for presence"* — a constraint,
   not a formula.
3. **`stale_after` per finding kind.** How long "you have four active repos" stays true varies by
   subject, and a wrong default is a confidently-false card.
4. **Exploration budget for engagement promotion** (§6.2). Deferred until there is engagement history at
   all.
5. **Feed pagination.** A ranked union needs a cursor. Straightforward, unspecified here.
6. **Where a body is stored** (§2.3). It must be a row rather than a derived value — invariant 1 plus
   the cost of a model call force that much. What is open is *which* row: `Finding` (§9) already carries
   `claim`, `body`, `sources`, `as_of` and `stale_after`, and a perception unit's body wants all five.
   **Settled: two rows.** A perception body lands in `unit_bodies` (`workspace_id`, `frame_key`,
   `body`, `event_ids`, `as_of`); a chat answer stays a `Finding`. They look alike once `claim` is
   struck, but they diverge on the two fields that carry the design. A `Finding`'s `derivation` names
   which tools to re-run — **empty for a perception body, whose derivation is its events.** And a
   `Finding` goes stale on a **timer** (`stale_after`), while a perception body goes stale
   **structurally**: `frame.event_count` changed, so a new message arrived and the prose no longer
   describes the thing. Lifetimes differ too — findings never expire, a body is superseded by the next
   message. §8.1's *"both paths, one shape"* is a claim about the `Unit`, not about where its body is
   kept.
7. **What the body generator reads.** It must read external text — a model cannot summarise an email it
   has not seen, and no containment trick avoids that. So this is the one place untrusted text
   legitimately enters a model's context, and the design's answer is to bound the blast radius rather
   than prevent the read: the model authors one field, that field is budget-validated, and it renders
   inline-only with no links. **What is open is whether it reads `unit.quotes`** — which inherits
   `VERBATIM_TEXT_FIELD`'s fail-closed per-source map, but is capped at `MAX_QUOTES` and drops
   unattributed messages — **or a wider per-source read.** The first keeps one verbatim map in the
   codebase; the second sees more of the thread.

---

## 14. Soul test

| Question | Answer |
|---|---|
| Calmer or more chaotic? | Calmer. Cards stop duplicating, vanishing and expiring; one thread is one card. |
| Trust or activity? | Trust. Untrusted text can no longer wear muldro's voice, and it can no longer raise its own rank. |
| Reduce burden? | Yes. *"No detail tabs available"* becomes unrepresentable, and the Full finally answers the question the card raised. |
| Dignity and control? | Yes. Every affordance names a real capability; every merge is reversible. |
| Genuinely useful daily? | The feed is ranked by attention rather than by which builder ran first. |
| Dependable second mind? | It can say what it knows, what it is doing, what it proposes, and why — and layer 3 says *why* using things only muldro holds. |
| Real leverage or agentic theater? | Model-authored UI **was** the theater. It never rendered, and it is removed. |

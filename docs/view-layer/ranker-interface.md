# The ranker interface — §6, drafted against what actually exists

> **Status:** draft, pending founder review. Expands `spec.md` §6 into a typed interface.
> **Binds to:** `spec.md` §10 invariant 8 — *the ranker's inputs contain no external prose.*
> **Scope:** the feature record, the ranking contract, and the audit of which features are
> buildable today. Not the weights — `spec.md` §13 leaves those open on purpose.

---

## 0. The rule

**The ranker reads only values muldro computed about its own history. It never reads a value
an outside party wrote, and it never reads a value a model inferred *from* what an outside
party wrote.**

That second clause is the one that bites, and it is why this document exists rather than a
patch. Half the fields that look like derived features today are an LLM's assertion about
attacker-controlled text wearing a typed name.

---

## 1. Availability audit

What `spec.md` §6 names, against what the codebase holds. **This is the load-bearing part of
this draft** — three of the seven features are not what they appear to be.

| §6 feature | Backing store | Verdict |
|---|---|---|
| `counterparty` → known entity, relationship, prior thread count | `Entity`, `EntityAlias`, `EntityRelationship` | **usable.** `EntityAlias` carries a uniqueness constraint that a strong identifier (email/handle) maps to exactly one entity per workspace, so sender → entity is a **lookup, not a judgement**. `interaction_count` and `last_seen_at` are counters muldro maintains. |
| `engagement` → dismissal penalty for `(source, category)` | `EngagementHistory` + `EngagementService.get_relevance_penalty` / `is_suppressed` | **usable, and already the right shape.** Counts of the founder's own actions. Nothing external touches it. |
| `recency` → `occurred_at` | `NormalizedEvent.occurred_at` | **usable.** Now uniformly tz-aware via `ensure_aware_utc`. |
| `thread` → message count | `NormalizedEvent`, indexed on `(user_id, source, entity_id)` | **usable.** A count of rows muldro wrote. |
| `thread` → **whether you replied** | — | **NOT AVAILABLE.** No sent-mail ingestion exists; the gmail connector emits only `email_received`, and there is no `email_sent` event type anywhere. Needs the 12-month sent-mail bootstrap from the perception-autonomy design. Model it as `bool \| None` and let `None` mean *not knowable yet* — never `False`, which would silently read as "you ignored them". |
| `deadline` → **typed date, extracted at ingestion** | `NormalizedEvent.importance_signals["contains_deadline"]` | **NOT WHAT IT CLAIMS.** What exists is a **boolean an LLM asserted** while reading the attacker's subject and body (`event_processor.py`'s scoring prompt), not a typed date. §6's own argument — *"an attacker can lie about when; they cannot inject an instruction"* — only holds for a real parsed date. Today this field is the instruction channel. **Do not wire it.** A typed extractor is prerequisite work. |
| `goals` → which goals this matches | `Memory` rows with `memory_type="goal"`; `ContextBuilder._fetch_core_goals` reads them; `Memory.entity_ids` is an `ARRAY(String)` | **usable, via the graph — not via text.** Goals exist (an earlier revision of this draft said otherwise and was wrong). **Match by joining `Memory.entity_ids` against the counterparty's `entity_id`, never by embedding the subject and vector-searching goals** — the latter hands an attacker a promotion channel, since a crafted subject that resembles your goals would raise its own rank. A graph join is unforgeable: an attacker cannot make your goal memory reference them. |
| `affordance` → is there an unresolved decision | `Frame.affordances` | **usable but empty.** Code-authored by construction; perception populates none yet (the capability→affordance mapping is a later plan). Wire the field, expect `False`. |

### Fields that must never enter, and why they look safe

| Field | Why it is not a derived feature |
|---|---|
| `NormalizedEvent.importance_score` | LLM output over `Title:` + `Summary:` — the raw subject and body. This is the channel the current work closed. |
| `NormalizedEvent.urgency_score` | Same prompt, same call, same exposure. |
| `importance_signals.from_priority_person` | LLM-asserted. The real form of this question is an `EntityAlias` lookup, which is unforgeable. |
| `importance_signals.related_to_active_project` | LLM-asserted. The real form is a goal/entity join — blocked above. |
| `Entity.importance_score` | Also a stored score; audit its writer before trusting it. Not used in this draft. |
| `TriageResult.category` **when `origin == "llm"`** | The LLM chose it from content. See below — the provenance flag makes this checkable. |

### The one place attacker-controlled input is safe to read

`classify_by_rules` (`services/triage.py`) reads mail headers — `List-Unsubscribe`, `List-Id`,
`Precedence: bulk` — and returns `"marketing"` or `None`. The headers *are* attacker-writable,
but **only in the demoting direction**: adding `List-Unsubscribe` demotes the sender, and
omitting it merely declines a demotion it could not have claimed anyway. An input an attacker
can only use against themselves is safe to read.

`TriageResult.origin` already records `"rules" | "llm" | "default"`. **That flag is the
provenance marker this design needs** — take the category only when `origin == "rules"`.

---

## 1a. Provenance is per-connector — a feature is not one question, it is five

Every derived feature has to be answered **per source**, because the five connectors expose
different structure. A deadline has three possible provenances, and they are not equally
trustworthy:

1. **A structured provider field** — a typed value the provider's API returns, never prose.
   No extraction, so no parser to feed and nothing to inject. **Strictly the best.**
2. **Deterministic extraction from verbatim prose** — a parser, no model. Bounded and
   checkable, per §6. Acceptable.
3. **A model's assertion about prose** — rejected. This is `contains_deadline` today.

Applied to what each connector actually emits:

| source | is `summary` verbatim? | structured deadline | where a deadline must come from |
|---|---|---|---|
| **calendar** | no — composed (`"{title} from {start} to {end} with {attendees}"`) | **yes** — `start_time`, already parsed into `occurred_at` | **the structured field. Never extract.** The meeting's start *is* the deadline; running a text parser over composed prose here would be strictly worse than the typed value sitting beside it. |
| **gmail** | yes — the message snippet | no | deterministic extraction |
| **slack** | yes — the message text | no | deterministic extraction |
| **github** | no — composed (`"{reason}: {title} in {repo}"`) | **not captured.** `milestone.due_on` and project dates exist in the API; the connector fetches neither | nothing today — needs a connector change, not a ranker one |
| **notion** | no — composed (`"Notion page: {title}"`) | **not captured.** Notion date properties exist; the connector stores only `page_id`, `url`, `last_edited_time` | nothing today — needs a connector change |

**This is the same shape as the quote band's `VERBATIM_TEXT_FIELD` map** (`view/perception.py`),
which exists because "which field holds verbatim external text" is also a per-source question
with a fail-closed answer. Deadline sourcing should follow that pattern rather than invent a
second per-source table: an explicit map, an unlisted source yields nothing, and a comment
recording what each unlisted source has instead.

**The general rule, which outlives the deadline case:** before wiring any feature, ask of each
connector *"is this a value the provider computed, a value a human wrote, or a value a model
inferred?"* Only the first two may enter, and the first is always preferred where it exists.
Adding a sixth connector means answering that question five more times — once per feature — and
the fail-closed default means forgetting to answer it costs a signal rather than opening a hole.

---

## 2. The feature record

```python
# src/view/ranking/features.py

class Counterparty(BaseModel):
    """Resolved against muldro's own graph. Never parsed out of the message."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    known: bool                      # EntityAlias hit on email/handle — a lookup
    relationship: str | None = None  # EntityRelationship.relation_type
    # None, never 0. `actor_entities` is unindexed JSONB, so a real count is a
    # sequential scan per counterparty per refresh, and Entity.source_refs caps
    # its event ids at 20 — a systematic under-count. A `0` on a known
    # counterparty asserts "you have never corresponded", which is the same
    # failure `you_replied=False` would be. Absent means absent.
    prior_threads: int | None = None
    interaction_count: int = 0       # Entity.interaction_count
    days_since_last_seen: int | None = None


class ThreadState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message_count: int = 1
    # None means NOT KNOWABLE (no sent-mail ingestion), never "no".
    you_replied: bool | None = None
    hours_since_last: float = 0.0


class RankFeatures(BaseModel):
    """Everything the ranker may see. No field here is external or model-asserted.

    There is deliberately no headline, no summary, no snippet and no body. The
    ranker cannot read prose because prose is not on the object.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str                              # frame.key — an opaque handle, the ONLY id
    kind: FrameKind
    source: str
    counterparty: Counterparty
    thread: ThreadState
    has_unresolved_affordance: bool = False
    bulk_mail: bool = False               # rules-only; origin == "rules"
    engagement_penalty: float = 0.0       # 0..1, demotion only
    suppressed: bool = False
    age_hours: float = 0.0

    # Reserved, NOT populated until their prerequisites land (§1).
    deadline_in_days: int | None = None   # needs a typed date extractor
    matched_goal_ids: tuple[str, ...] = ()  # needs a goal store
```

`key` is an opaque handle. The ranker orders handles; it never learns what they say.

---

## 3. The ranking contract

```python
# src/view/ranking/rank.py

def rank(features: Sequence[RankFeatures]) -> list[str]:
    """Return every input key, most-attention-worthy first.

    Pure and total: same input, same output. No I/O, no model call.
    """

async def rank_with_model(
    features: Sequence[RankFeatures],
    *,
    max_displacement: int = 5,
) -> list[str]:
    """Optional: let a model reorder the deterministic baseline, bounded.

    Falls back to `rank()` on any failure, timeout, or invalid response.
    """
```

`build_features(units, *, db, workspace_id, user_id, now, events_by_key=None)` does the I/O and is
the only place the DB is touched, keeping `rank()` a pure function over a record — which is what
makes it testable against ordering cases rather than eyeballed.

Three arguments beyond the obvious, each forced by a real fact:

- **`user_id`** — the thread-count index is `(user_id, source, entity_id)`.
- **`now`** — for the same reason `extract_deadline` requires it: a function that reads the clock
  cannot be tested deterministically.
- **`events_by_key`** — three features rest on facts that live on the *event*, not the frame: the
  counterparty's strong identifier, the triage provenance flag, and the event type. Keyed by
  `frame.key`, which is what `perception.group_events_by_key` already returns. **Optional**: absent
  it, those three degrade to neutral values, never to a guess.

**Deadline text is read from `unit.quotes`, not from a second copy of the verbatim map.** A `Quote`
is built from exactly the field `VERBATIM_TEXT_FIELD` names and is the only route external text
takes into the view layer, so this keeps **one** per-source verbatim map in the codebase instead of
two that can disagree. Two consequences, both fail-closed and both deliberate: an *unattributed*
message contributes no quote and therefore no deadline, and `MAX_QUOTES` caps the scan at the three
newest messages of a poll — an older message's deadline is not seen. Both cost a signal rather than
opening a hole.

### The weights

Every positive term is normalised to `[0,1]` and scaled, so each constant reads directly as relative
importance. `spec.md` §13 leaves the numbers open; these are the starting point, not a measurement.

| term | weight | shape |
|---|---|---|
| deadline | 3.0 | linear ramp — `1.0` due today, `0.0` at 14 days |
| goal match | 2.0 | graph-join hit |
| recency | 2.0 | linear decay over 72h |
| unresolved affordance | 1.5 | a decision literally waiting |
| counterparty | 1.5 | known · relationship · saturating `interaction_count` · seen-within-7-days |
| `you_replied` | 1.5 | reserved; `None` and `False` both score 0 |
| thread depth | 0.75 | saturates at 10 messages — volume is not importance |
| bulk mail | **−2.5** | rules-origin only |
| engagement | **−2.0 × penalty** | demotion only |

The two demotions are **subtracted**, so the sign itself is the guarantee that engagement can never
promote — §6.2's self-sealing loop closed by arithmetic rather than by a rule someone must remember.
Ties break on `(-score, age_hours, key)`, which is total, so no dict or set iteration order can leak
into the output.

### Why a permutation is checkable where a score is not

This is the part that answers *"why not just verify the score?"*

An importance score is **unfalsifiable**: `0.9` is exactly as valid-looking as `0.2`, because
importance is precisely what you delegated. There is no external fact to check it against.

An **ordering is checkable**, cheaply and totally:

1. **It must be a permutation.** The returned list must contain every input key exactly once —
   no invented keys, no dropped ones. A model that has been successfully instructed still cannot
   add an item, remove one, or smuggle a payload; the output alphabet is fixed to the input.
2. **Displacement is bounded.** `max_displacement` clamps how far any item may move from its
   deterministic position. A maximally-fooled model moves an item a few places, not to the top.
3. **Failure is total and silent-free.** Anything that is not a valid permutation is discarded
   wholesale and `rank()` stands. There is no partial trust.

So the blast radius of a successful injection is bounded *by construction* rather than by
detection — which is the property a verifier prompt cannot give you, since the verifier faces
the same unfalsifiable question as the scorer.

### Engagement is applied after ordering, and only downward

`suppressed` items are dropped before ranking. `engagement_penalty` may push an item down and
may never pull one up. **Promotion by engagement is self-sealing** — rank drives visibility,
visibility drives engagement, so a low-ranked type would never be seen, never engaged, and would
sink permanently. Demotion has no such loop, because a thing had to be seen to be dismissed.

---

## 4. Invariants

1. **`RankFeatures` holds no prose.** Enforced structurally: no string field on it carries
   external or model-authored text, and `key` is opaque.
2. **`rank()` is pure and total.** Same features in, same order out; every input key appears in
   the output exactly once.
3. **A model may reorder, never author.** `rank_with_model`'s output is validated as a
   permutation of its input keys, or discarded entirely.
4. **Displacement is bounded.** No item moves more than `max_displacement` from its
   deterministic position.
5. **Engagement demotes only.**
6. **A reserved field is `None`, never a guess.** `you_replied=None` means *not knowable*;
   `deadline_in_days=None` means *no typed extractor yet*. Neither may be defaulted to a value
   that reads as a fact.

**Test surface:** `rank()` against ordering cases, not eyeballing (§6); a property test that a
non-permutation response is rejected and the baseline stands; a test that
`engagement_penalty=1.0` cannot raise an item; a test that every `RankFeatures` field is either
a number, a bool, an enum or an opaque key — which is what invariant 8 actually asserts, made
mechanical.

---

## 5. Prerequisites, in dependency order

1. **A typed deadline extractor** — *built with this draft.* Replaces the LLM's
   `contains_deadline` boolean with a date parsed **deterministically**, no model in the loop at
   all. It is the feature §6 leans on hardest, and it was the last instruction channel wearing a
   typed name.
2. **Goal matching by graph join** — *built with this draft.* `Memory.entity_ids` ∩ the
   counterparty's entity. Text-similarity matching is rejected on injection grounds, above.
3. **Sent-mail ingestion.** Unblocks `you_replied`, the strongest relationship signal available.
   **Deliberately not built here, and not because it is hard:** ingesting the founder's own
   outbound mail changes *what perception sees*. Those events would flow into triage,
   scoring, the Planner and the notifier, so it is a product decision with side effects well
   outside the view layer — not a ranker change. The 12-month bootstrap is already designed.
   `users.email` exists, so the comparison is trivial once the data does.
4. **Affordances populated from perception.** Unblocks `has_unresolved_affordance`. Needs the
   capability→affordance mapping, which is spec step 4.

**Neither remaining item blocks `rank()`.** Both reserved fields are typed to accept their
prerequisite without a signature change, and both are `None`/empty rather than a defaulted
value that would read as a fact.

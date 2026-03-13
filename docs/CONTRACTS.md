# Jarvis Data Contracts

These contracts are the foundation of the system. Freeze them before tuning prompts.

## 1. Normalized Event

Every source produces the same event shape.

```json
{
  "event_id": "evt_01HXYZ",
  "user_id": "usr_123",
  "source": "gmail",
  "source_account_id": "gmail_primary",
  "event_type": "email_received",
  "entity_type": "email_thread",
  "entity_id": "thr_889",
  "occurred_at": "2026-03-13T08:10:00Z",
  "ingested_at": "2026-03-13T08:10:04Z",
  "title": "Investor follow-up on deck",
  "summary": "Investor requested latest deck and quick call",
  "actor_entities": [
    { "type": "person", "external_id": "investor@fund.com", "display_name": "John Doe" }
  ],
  "importance_signals": {
    "from_priority_person": true,
    "contains_deadline": true,
    "contains_question": true,
    "related_to_active_project": true
  },
  "urgency_score": 0.86,
  "importance_score": 0.91,
  "confidence_score": 0.88,
  "raw_ref": "s3://jarvis/raw/gmail/evt_01HXYZ.json",
  "idempotency_key": "gmail:msg_123:received",
  "status": "processed"
}
```

## 2. Plan + Task Graph

Every planner output is structured — never free-form text.

```json
{
  "plan_id": "plan_301",
  "user_id": "usr_123",
  "trigger_type": "event",
  "trigger_ref": "evt_01HXYZ",
  "goal": "handle_investor_followup",
  "priority": "high",
  "decision": "draft_and_request_approval",
  "reasoning_summary": "High-priority investor email with response expected soon.",
  "tasks": [
    { "task_id": "pt_1", "type": "fetch_document", "input": { "document_type": "pitch_deck_latest" }, "depends_on": [] },
    { "task_id": "pt_2", "type": "draft_email_reply", "input": { "tone": "professional_concise" }, "depends_on": ["pt_1"] },
    { "task_id": "pt_3", "type": "request_approval", "input": { "artifact_ref": "draft_email_reply" }, "depends_on": ["pt_2"] }
  ],
  "risk_level": "medium",
  "execution_mode": "approval_required"
}
```

### Planner Decisions (v1)

- `ignore` — no action needed
- `add_to_brief` — include in next briefing
- `summarize_now` — immediate summary to user
- `create_task` — create internal task
- `draft_reply` — draft a response for approval
- `prepare_meeting` — generate meeting prep
- `request_clarification` — ask the user
- `request_approval` — present action for approval

## 3. Execution State

```json
{
  "execution_id": "exec_991",
  "plan_id": "plan_301",
  "status": "awaiting_approval",
  "current_task_id": "pt_3",
  "task_results": [
    { "task_id": "pt_1", "status": "completed", "artifact_ref": "doc_889" },
    { "task_id": "pt_2", "status": "completed", "artifact_ref": "draft_email_771" }
  ]
}
```

## 4. Approval

```json
{
  "approval_id": "apr_71",
  "execution_id": "exec_991",
  "approval_type": "send_email",
  "title": "Approve investor follow-up email",
  "summary": "Draft prepared with latest deck attached.",
  "artifact_refs": ["draft_email_771", "doc_889"],
  "risk_level": "medium",
  "status": "pending",
  "expires_at": "2026-03-14T08:23:12Z"
}
```

## 5. Briefing

```json
{
  "briefing_id": "brief_2026_03_13",
  "date": "2026-03-13",
  "headline": "3 priorities, 2 follow-ups, 1 meeting risk",
  "top_priorities": [
    { "title": "Reply to investor follow-up", "why": "Fundraising thread, response expected" }
  ],
  "changes_since_last": [
    { "type": "email", "summary": "Investor requested latest deck" }
  ],
  "pending_approvals": [
    { "approval_id": "apr_71", "title": "Send investor follow-up email" }
  ],
  "recommended_actions": [
    "Review and approve investor reply",
    "Prepare notes for 4pm strategy meeting"
  ]
}
```

## 6. Memory

```json
{
  "memory_id": "mem_101",
  "memory_type": "preference",
  "scope": "presentation",
  "fact_text": "User prefers concise founder briefings with priorities first.",
  "confidence": 0.93,
  "stability_score": 0.89,
  "source_event_ids": ["evt_443", "evt_887"],
  "provenance": { "extraction_method": "interaction_pattern" },
  "ttl_days": 180,
  "status": "active"
}
```

### Memory Types

- `episodic` — events and interactions
- `semantic` — learned facts
- `preference` — user habits and preferences
- `relationship` — people and relationship patterns
- `task_context` — active task-related knowledge

## 7. Policy Rule

```json
{
  "rule_id": "rule_email_send",
  "action_type": "send_email",
  "risk_level": "medium",
  "condition": { "external_recipient": true },
  "decision": "approval_required"
}
```

### Execution Modes

- `observe_only` — watch and log
- `summarize_only` — summarize but don't act
- `recommend_only` — suggest actions
- `draft_only` — prepare drafts
- `approval_required` — require explicit approval
- `auto_execute` — execute automatically (use sparingly)

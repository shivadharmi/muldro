---
description: Add a new source connector (Gmail, Calendar, Slack, etc.)
user-invocable: true
---

# Add a new Jarvis source connector

The user wants to add a new data source connector. Follow these steps:

1. **Read CLAUDE.md** and **docs/CONTRACTS.md** for the normalized event schema
2. **Ask the user** which source to connect (e.g., Gmail, Calendar, Slack, Notion)
3. **Create the connector** at `backend/src/connectors/{source}.py`:
   - Class with `handle_push_notification()` and `sync()` methods
   - Both return `list[str]` (event_ids created)
   - Use httpx for external API calls
   - Store OAuth tokens in connector_accounts (encrypted)
   - Track sync cursor for incremental fetches
   - Output normalized events through the EventProcessor
4. **Add webhook endpoint** in `backend/src/api/routes_webhooks.py`:
   - POST endpoint at `/v1/webhooks/{source}`
   - Accept raw payload, forward to connector
5. **Add HTTP route** in `jarvis-tools/src/routes.ts`:
   - Forward `/jarvis/webhook/{source}` to backend
6. **Create entity extraction** logic:
   - Extract people (sender, recipients) as entities
   - Extract project/thread linkages as relationships
7. **Add connector config** to `.env.example` with OAuth client ID/secret
8. **Write tests** for event normalization
9. **Run lint and tests**: `ruff check src/ && pytest tests/ -v`

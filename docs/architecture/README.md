# Jarvis Architecture Documentation

Comprehensive architecture reference for the Jarvis Personal AI Operating System.

## Documents

| Document | Description |
|----------|-------------|
| [System Overview](overview.md) | Core loop, hub-and-spoke topology, the 8 sub-agents |
| [Message Flow](message-flow.md) | User chat processing pipeline, streaming SSE events, planner decisions |
| [Event System](event-system.md) | Event ingestion, scoring, initiative scoring, triggers, proactive intelligence |
| [Execution Engine](execution.md) | DAG executor, state machine, checkpoints, approval gates, verification |
| [Perception](perception.md) | Ambient observation cycles, cursor-based fetch, budget-aware scheduling |
| [Services Reference](services.md) | All 54 services with dependencies, methods, and interactions |
| [Data Model](data-model.md) | 49 SQLAlchemy models, ER diagram, ULID scheme, vector embeddings |
| [Tools & MCP](tools-mcp.md) | 3-tier tool dispatch, MCP bridge, approval flow, external MCP servers |
| [Startup & Recovery](startup.md) | Boot sequence, scheduling, worker streams, startup recovery |
| [Design Decisions](decisions.md) | Key architectural choices and their rationale |

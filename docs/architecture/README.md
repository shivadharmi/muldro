# Jarvis Architecture Documentation

Comprehensive architecture reference for the Jarvis Personal AI Operating System.

## Documents

| Document | Description |
|----------|-------------|
| [System Overview](overview.md) | Core loop, hub-and-spoke topology, the 7 sub-agents |
| [Message Flow](message-flow.md) | User chat processing pipeline, streaming SSE events, planner decisions |
| [Event System](event-system.md) | Event ingestion, scoring, initiative scoring, triggers, proactive intelligence |
| [Execution Engine](execution.md) | DAG executor, state machine, checkpoints, approval gates, verification |
| [Perception](perception.md) | Ambient observation cycles, cursor-based fetch, budget-aware scheduling |
| [Services Reference](services.md) | All services with dependencies, methods, and interactions |
| [Data Model](data-model.md) | SQLAlchemy tables, ER diagram, ULID scheme, vector embeddings, Alembic migrations |
| [Tools & MCP](tools-mcp.md) | Unified registry dispatch, tool catalog, MCP bridge, approval flow |
| [Startup & Recovery](startup.md) | Boot sequence, scheduling, worker streams, startup recovery |
| [Design Decisions](decisions.md) | Key architectural choices and their rationale |

## Product Philosophy

| Document | Description |
|----------|-------------|
| [Soul](../soul.md) | Jarvis's character, interaction philosophy, autonomy boundaries |
| [Vision](../vision.md) | Product thesis, capability pillars, strategic design principles |

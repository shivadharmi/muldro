#!/usr/bin/env python3
"""Seed demo data for Jarvis frontend UI development.

Populates all tables with realistic data so every page and component
has something to render. Run with:

    cd backend
    source .venv/bin/activate
    python scripts/seed_demo_data.py

Requires: running Postgres (docker compose up -d)
"""

import asyncio
import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

# -- Bootstrap path so imports work from scripts/ --------------------------
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models.ids import generate_id  # noqa: E402
from src.models.database import get_engine, get_session_factory  # noqa: E402
from src.models.base import Base  # noqa: E402

# Import all models so Base.metadata knows about them
from src.models import (  # noqa: E402, F401
    Agent,
    AgentRoute,
    Approval,
    Artifact,
    AuditLog,
    Briefing,
    BriefingFeedback,
    Connector,
    ConnectorAccount,
    Conversation,
    DeadLetterEntry,
    Entity,
    EntityAlias,
    EntityRelationship,
    Goal,
    Memory,
    Message,
    ModelCall,
    Notification,
    NormalizedEvent,
    ObservationStatus,
    Plan,
    PlanTask,
    Schedule,
    Session,
    Task,
    TaskDependency,
    TaskRun,
    TaskStep,
    Trace,
    Trigger,
    TrustScore,
    User,
    UserSettings,
    Workspace,
    WorkspaceMember,
)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


NOW = datetime.now(timezone.utc)
TODAY = date.today()

# ── Fixed IDs — valid Crockford base32 (no I/L/O/U) ─────────────
USER_ID = "usr_01KM2EMPNB8WYN2E2S286DJ52J"
WS_ID = "ws_01KM2EMPNB8WYN2E2S286DJ52K"
SESSION_TOKEN = "demo-session-token-for-jarvis-ui-dev"
SESSION_ID = "sess_01KM2EMPNB8WYN2E2S286DJ52M"


async def seed():
    engine = get_engine()
    factory = get_session_factory()

    async with factory() as db:
        # Check if already seeded
        result = await db.execute(
            text("SELECT 1 FROM users WHERE user_id = :uid"),
            {"uid": USER_ID},
        )
        if result.scalar():
            print("Demo data already exists. To re-seed, run:")
            print("  python scripts/seed_demo_data.py --reset")
            if "--reset" not in sys.argv:
                return
            # Delete cascade via workspace
            await db.execute(text("DELETE FROM workspace_members WHERE workspace_id = :ws"), {"ws": WS_ID})
            await db.execute(text("DELETE FROM sessions WHERE user_id = :uid"), {"uid": USER_ID})
            await db.execute(text("DELETE FROM workspaces WHERE workspace_id = :ws"), {"ws": WS_ID})
            await db.execute(text("DELETE FROM users WHERE user_id = :uid"), {"uid": USER_ID})
            await db.execute(text("DELETE FROM agents"))
            await db.execute(text("DELETE FROM agent_routes"))
            await db.commit()
            print("Cleared existing demo data.")

        # ── TIER 0: User + Workspace + Session ──────────────────────
        user = User(
            user_id=USER_ID,
            email="founder@jarvis.dev",
            display_name="Demo Founder",
            status="active",
            onboarding_completed=True,
            timezone="America/Los_Angeles",
            settings={"policy_mode": "balanced", "daily_budget_usd": 10.0},
        )
        db.add(user)

        ws = Workspace(
            workspace_id=WS_ID,
            name="Jarvis Demo",
            owner_user_id=USER_ID,
            slug="jarvis-demo",
            type="personal",
            plan="pro",
        )
        db.add(ws)
        await db.flush()

        wm = WorkspaceMember(
            workspace_id=WS_ID,
            user_id=USER_ID,
            role="owner",
            joined_at=NOW,
        )
        db.add(wm)

        session = Session(
            session_id=SESSION_ID,
            user_id=USER_ID,
            token_hash=_hash(SESSION_TOKEN),
            expires_at=NOW + timedelta(days=30),
            workspace_id=WS_ID,
            surface="web",
        )
        db.add(session)
        await db.flush()

        # ── TIER 0b: Agents (global) ────────────────────────────────
        agent_defs = [
            ("observer", "Observer", "Reads sources, detects changes, ingests events", "haiku"),
            ("librarian", "Librarian", "Extracts entities, updates world model", "sonnet"),
            ("planner", "Planner", "Produces structured task graphs", "opus"),
            ("governor", "Governor", "Evaluates policies, gates approvals", "sonnet"),
            ("operator", "Operator", "Executes approved plans via MCP tools", "sonnet"),
            ("presenter", "Presenter", "Generates user-facing output", "sonnet"),
            ("researcher", "Researcher", "Deep context gathering", "opus"),
            ("persona", "Persona", "Learns preferences", "haiku"),
        ]
        for name, display, desc, tier in agent_defs:
            db.add(Agent(
                agent_id=generate_id("agent"),
                name=name,
                display_name=display,
                description=desc,
                system_prompt=f"You are the {display} agent for Jarvis.",
                model_tier=tier,
                tool_scope=[],
                max_tokens=4096,
                temperature=0.3,
                enabled=True,
            ))

        # ── TIER 0c: Agent Routes (global) ──────────────────────────
        route_defs = [
            ("observe", "Observation Pipeline", "observe", ["observer", "librarian"]),
            ("research", "Research Query", "research", ["researcher"]),
            ("execute", "Task Execution", "execute", ["planner", "governor", "operator"]),
            ("brief", "Daily Briefing", "brief", ["observer", "presenter"]),
        ]
        for i, (name, desc, dtype, pipeline) in enumerate(route_defs):
            db.add(AgentRoute(
                route_id=generate_id("route"),
                name=name,
                description=desc,
                decision_type=dtype,
                agent_pipeline=[{"agent": a} for a in pipeline],
                priority=100 + i * 10,
                keywords=[name],
            ))

        await db.flush()

        # ── TIER 1: Connectors ──────────────────────────────────────
        connector_ids = []
        for provider, status in [("gmail", "active"), ("google_calendar", "active"), ("slack", "reauth_needed"), ("github", "active")]:
            cid = generate_id("conn")
            connector_ids.append(cid)
            db.add(Connector(
                connector_id=cid,
                user_id=USER_ID,
                workspace_id=WS_ID,
                provider=provider,
                status=status,
                config={"scopes": ["read", "write"]},
            ))

        # ── TIER 1b: Observation Status ─────────────────────────────
        for source, items_f, items_i, status in [
            ("gmail", 42, 38, "ok"),
            ("google_calendar", 12, 12, "ok"),
            ("slack", 0, 0, "error"),
            ("github", 67, 65, "ok"),
        ]:
            db.add(ObservationStatus(
                user_id=USER_ID,
                workspace_id=WS_ID,
                source=source,
                last_observed_at=NOW - timedelta(minutes=15),
                items_found=items_f,
                items_ingested=items_i,
                status=status,
                error_message="OAuth token expired" if status == "error" else None,
            ))

        await db.flush()

        # ── TIER 2: Entities ────────────────────────────────────────
        entity_ids = []
        entities_data = [
            ("person", "Elon Musk", {"role": "CEO", "company": "Tesla/SpaceX"}),
            ("person", "Sarah Chen", {"role": "CTO", "company": "Acme Corp"}),
            ("company", "Acme Corp", {"industry": "SaaS", "stage": "Series B"}),
            ("company", "TechVentures", {"industry": "VC", "aum": "$500M"}),
            ("project", "Q1 Product Launch", {"status": "in_progress", "deadline": "2026-03-31"}),
            ("meeting", "Board Review", {"cadence": "monthly"}),
            ("document", "Investor Deck v3", {"format": "pdf", "pages": 24}),
            ("person", "Alex Kim", {"role": "Investor", "firm": "TechVentures"}),
            ("topic", "Series A Fundraising", {"stage": "active", "target": "$5M"}),
            ("location", "San Francisco Office", {"type": "headquarters"}),
        ]
        for etype, name, attrs in entities_data:
            eid = generate_id("ent")
            entity_ids.append(eid)
            db.add(Entity(
                entity_id=eid,
                user_id=USER_ID,
                workspace_id=WS_ID,
                entity_type=etype,
                canonical_name=name,
                attributes=attrs,
                last_seen_at=NOW - timedelta(hours=2),
                interaction_count=15,
                importance_score=0.7,
            ))
            db.add(EntityAlias(
                entity_id=eid,
                workspace_id=WS_ID,
                alias=name.lower(),
                alias_type="name",
            ))

        # Entity relationships
        if len(entity_ids) >= 4:
            db.add(EntityRelationship(
                relation_id=generate_id("rel"),
                user_id=USER_ID,
                workspace_id=WS_ID,
                from_entity_id=entity_ids[1],  # Sarah Chen
                relation_type="works_at",
                to_entity_id=entity_ids[2],  # Acme Corp
                strength=1.0,
                active=True,
            ))
            db.add(EntityRelationship(
                relation_id=generate_id("rel"),
                user_id=USER_ID,
                workspace_id=WS_ID,
                from_entity_id=entity_ids[7],  # Alex Kim
                relation_type="partner_at",
                to_entity_id=entity_ids[3],  # TechVentures
                strength=0.9,
                active=True,
            ))

        await db.flush()

        # ── TIER 2b: Memories ───────────────────────────────────────
        memories_data = [
            ("semantic", "global", "Acme Corp is a Series B SaaS company focused on developer tools"),
            ("episodic", "conversation", "Discussed fundraising timeline with Alex Kim on March 15"),
            ("preference", "user", "Founder prefers concise briefings under 200 words"),
            ("relationship", "entity", "Sarah Chen is the CTO of Acme Corp, met at TechCrunch Disrupt"),
            ("task_context", "plan", "Q1 Product Launch requires 3 more features before GA"),
            ("procedural", "workflow", "Weekly investor update goes out every Monday at 9am PT"),
            ("semantic", "global", "TechVentures has a focus on AI-first companies"),
            ("episodic", "conversation", "Board meeting prep includes reviewing quarterly metrics"),
        ]
        for mtype, scope, fact in memories_data:
            db.add(Memory(
                memory_id=generate_id("mem"),
                user_id=USER_ID,
                workspace_id=WS_ID,
                memory_type=mtype,
                scope=scope,
                fact_text=fact,
                confidence=0.85,
                stability_score=0.6,
                status="active",
            ))

        # ── TIER 2c: Events ────────────────────────────────────────
        events_data = [
            ("gmail", "email_received", "email_thread", "Follow-up on term sheet", "alex@techventures.com"),
            ("google_calendar", "event_created", "calendar_event", "Board Review March 25", "calendar"),
            ("github", "pr_merged", "pull_request", "Add streaming support (#42)", "github-bot"),
            ("slack", "message_received", "slack_message", "Launch prep meeting at 3pm", "sarah"),
            ("gmail", "email_received", "email_thread", "Updated product roadmap", "sarah@acme.com"),
            ("github", "issue_opened", "issue", "Bug: Auth token refresh failing (#45)", "user-report"),
            ("gmail", "email_received", "email_thread", "Quarterly metrics summary", "analytics@internal"),
            ("google_calendar", "event_updated", "calendar_event", "Investor call moved to 4pm", "calendar"),
        ]
        for i, (source, etype, entity_type, title, actor) in enumerate(events_data):
            db.add(NormalizedEvent(
                event_id=generate_id("evt"),
                user_id=USER_ID,
                workspace_id=WS_ID,
                source=source,
                source_account_id=f"{source}_default",
                event_type=etype,
                entity_type=entity_type,
                entity_id=f"{source}_{i}",
                occurred_at=NOW - timedelta(hours=i + 1),
                title=title,
                summary=title,
                actor_entities={"actor": actor},
                urgency_score=0.7,
                importance_score=0.6,
                idempotency_key=f"demo_{source}_{i}_{etype}",
                status="processed",
            ))

        await db.flush()

        # ── TIER 3: Goals ───────────────────────────────────────────
        goal_ids = []
        goals_data = [
            ("Close Series A by Q2", "Secure $5M Series A funding", "high", "active", 0.35),
            ("Ship Q1 Product Launch", "Release v2.0 with AI features", "high", "active", 0.72),
            ("Grow team to 15", "Hire 5 more engineers", "medium", "active", 0.40),
            ("Achieve 1000 DAU", "Grow daily active users", "medium", "active", 0.15),
            ("SOC2 Compliance", "Complete SOC2 Type II audit", "low", "completed", 1.0),
        ]
        for title, desc, priority, status, progress in goals_data:
            gid = generate_id("goal")
            goal_ids.append(gid)
            db.add(Goal(
                goal_id=gid,
                user_id=USER_ID,
                workspace_id=WS_ID,
                title=title,
                description=desc,
                priority=priority,
                status=status,
                progress=progress,
                target_date=NOW + timedelta(days=60),
                success_criteria_json={"metrics": [title]},
            ))

        # ── TIER 3b: Tasks ─────────────────────────────────────────
        task_ids = []
        tasks_data = [
            ("Draft investor update email", "Send weekly update to investors", "communication", "high", "completed", goal_ids[0]),
            ("Review term sheet from TechVentures", "Legal review needed", "review", "critical", "awaiting_approval", goal_ids[0]),
            ("Deploy v2.0-rc1 to staging", "Deploy release candidate", "engineering", "high", "executing", goal_ids[1]),
            ("Write blog post for launch", "Content marketing piece", "content", "medium", "created", goal_ids[1]),
            ("Schedule interviews for senior eng", "3 candidates this week", "hiring", "medium", "created", goal_ids[2]),
            ("Set up analytics dashboard", "Track DAU, retention, NPS", "engineering", "medium", "completed", goal_ids[3]),
            ("Prepare board deck", "Q1 board meeting presentation", "communication", "high", "executing", None),
            ("Fix auth token refresh bug", "Users getting logged out", "engineering", "critical", "completed", None),
        ]
        for title, desc, ttype, priority, status, gid in tasks_data:
            tid = generate_id("task")
            task_ids.append(tid)
            db.add(Task(
                task_id=tid,
                user_id=USER_ID,
                workspace_id=WS_ID,
                goal_id=gid,
                title=title,
                description=desc,
                task_type=ttype,
                source="user",
                priority=priority,
                status=status,
            ))

        await db.flush()

        # ── TIER 3c: Plans ─────────────────────────────────────────
        plan_ids = []
        plans_data = [
            ("chat", "Deploy v2.0 to staging", "deploy_staging", "high", "auto_execute", "completed"),
            ("chat", "Research competitor pricing", "research_competitors", "medium", "approval_required", "created"),
            ("trigger", "Process new emails from investors", "process_investor_emails", "high", "auto_execute", "created"),
        ]
        for trigger, goal, decision, priority, mode, status in plans_data:
            pid = generate_id("plan")
            plan_ids.append(pid)
            db.add(Plan(
                plan_id=pid,
                user_id=USER_ID,
                workspace_id=WS_ID,
                trigger_type=trigger,
                goal=goal,
                decision=decision,
                priority=priority,
                execution_mode=mode,
                status=status,
                risk_level="low",
                reasoning_summary=f"Plan to {goal.replace('_', ' ')}",
            ))
            # Plan tasks
            for j, task_type in enumerate(["prepare", "execute", "verify"]):
                db.add(PlanTask(
                    task_id=generate_id("ptask"),
                    plan_id=pid,
                    workspace_id=WS_ID,
                    task_type=task_type,
                    input_data={"step": j + 1},
                    depends_on=[],
                    status="completed" if status == "completed" else "pending",
                ))

        await db.flush()

        # ── TIER 4: Task Runs + Steps ──────────────────────────────
        run_ids = []
        runs_data = [
            (plan_ids[0], "completed", task_ids[2]),
            (plan_ids[1], "running", task_ids[6]),
            (None, "failed", task_ids[7]),
        ]
        for plan_id, status, task_ref in runs_data:
            rid = generate_id("run")
            run_ids.append(rid)
            db.add(TaskRun(
                run_id=rid,
                plan_id=plan_id,
                user_id=USER_ID,
                workspace_id=WS_ID,
                status=status,
                source="plan" if plan_id else "manual",
                started_at=NOW - timedelta(hours=2),
                completed_at=NOW - timedelta(hours=1) if status == "completed" else None,
                task_id_ref=task_ref,
                error={"message": "Connection timeout"} if status == "failed" else None,
                retry_count=1 if status == "failed" else 0,
            ))
            # Steps for each run
            step_statuses = (
                ["completed", "completed", "completed"] if status == "completed"
                else ["completed", "running", "pending"] if status == "running"
                else ["completed", "failed", "pending"]
            )
            for k, s_status in enumerate(step_statuses):
                db.add(TaskStep(
                    step_id=generate_id("step"),
                    run_id=rid,
                    workspace_id=WS_ID,
                    task_id=generate_id("ptask"),
                    step_order=k,
                    step_type=["prepare", "execute", "verify"][k],
                    name=["Prepare environment", "Execute action", "Verify result"][k],
                    status=s_status,
                    started_at=NOW - timedelta(hours=2, minutes=-k * 10),
                    completed_at=NOW - timedelta(hours=1) if s_status == "completed" else None,
                    output_data={"result": "ok"} if s_status == "completed" else None,
                    error={"message": "Step failed"} if s_status == "failed" else None,
                ))

        await db.flush()

        # ── TIER 5: Approvals ──────────────────────────────────────
        approvals_data = [
            ("Send investor update email", "Weekly update to 12 investors", "external_write", "low", "pending"),
            ("Deploy v2.0 to production", "Production deployment of release candidate", "deploy", "high", "pending"),
            ("Delete stale user data", "GDPR cleanup of accounts >2yr inactive", "destructive", "medium", "pending"),
            ("Merge PR #42", "Streaming support feature", "external_write", "low", "approved"),
            ("Revoke Slack integration", "Security review flagged old token", "destructive", "high", "rejected"),
        ]
        for title, summary, atype, risk, status in approvals_data:
            db.add(Approval(
                approval_id=generate_id("apr"),
                user_id=USER_ID,
                workspace_id=WS_ID,
                execution_id=run_ids[0],
                approval_type=atype,
                title=title,
                summary=summary,
                risk_level=risk,
                status=status,
                decided_at=NOW - timedelta(hours=1) if status != "pending" else None,
                decision_reason="Approved after review" if status == "approved" else ("Too risky" if status == "rejected" else None),
            ))

        # ── TIER 5b: Briefings ─────────────────────────────────────
        for days_ago in range(3):
            bdate = TODAY - timedelta(days=days_ago)
            db.add(Briefing(
                briefing_id=generate_id("brief"),
                user_id=USER_ID,
                workspace_id=WS_ID,
                briefing_date=bdate,
                headline=f"{'3 new emails from investors' if days_ago == 0 else f'{5 - days_ago} items need attention'}",
                top_priorities=[
                    {"title": "Review term sheet from TechVentures", "priority": "critical", "description": "Alex Kim sent updated terms — $5M at $25M pre-money. Response needed by Friday."},
                    {"title": "Prep board deck for March 25", "priority": "high", "description": "Q1 metrics, product roadmap, and hiring plan sections still incomplete."},
                ],
                changes_since_last=[
                    {"title": "Acme Corp contract signed", "entity": "Acme Corp", "change": "New enterprise contract signed", "summary": "12-month SaaS agreement, $120K ARR"},
                    {"title": "TechVentures term sheet", "entity": "TechVentures", "change": "Term sheet received", "summary": "Series A terms: $5M at $25M pre-money valuation"},
                    {"title": "PR #42 merged", "entity": "GitHub", "change": "Streaming support shipped", "summary": "Real-time streaming feature merged to main branch"},
                ],
                pending_approvals=[
                    {"title": "Deploy v2.0 to production", "risk": "high", "description": "Production deployment of release candidate with streaming support"},
                ],
                recommended_actions=[
                    "Review and sign term sheet from TechVentures",
                    "Prepare for board meeting on March 25",
                    "Follow up with Sarah on product roadmap",
                ],
                full_text=(
                    "Good morning. Here's your daily briefing.\n\n"
                    "## Key Developments\n\n"
                    "**TechVentures sent updated term sheet** — $5M Series A at $25M pre-money. "
                    "Alex Kim is expecting a response by end of week. The terms are competitive "
                    "with recent comparable rounds.\n\n"
                    "**Acme Corp contract signed** — 12-month enterprise SaaS agreement worth $120K ARR. "
                    "Sarah Chen handled the final negotiations.\n\n"
                    "## Action Items\n\n"
                    "1. Review term sheet and schedule call with legal\n"
                    "2. Complete board deck sections (metrics, roadmap, hiring)\n"
                    "3. Approve v2.0 production deployment\n\n"
                    "## System Activity\n\n"
                    "Jarvis processed 42 emails, analyzed 3 GitHub threads, and created 2 new "
                    "entity records since your last briefing. One connector (Slack) needs reauthorization."
                ),
            ))

        # ── TIER 6: Schedules ──────────────────────────────────────
        schedules_data = [
            ("Morning Briefing", "Daily briefing generation", "recurring", "0 8 * * *", "generate_briefing", "user"),
            ("Email Sync", "Check Gmail every 15min", "recurring", "*/15 * * * *", "observe_gmail", "system"),
            ("Calendar Sync", "Sync Google Calendar hourly", "recurring", "0 * * * *", "observe_calendar", "system"),
            ("Weekly Investor Update", "Draft and send investor email", "recurring", "0 9 * * 1", "send_investor_update", "user"),
            ("Quarterly Review Prep", "Prepare Q1 review materials", "one_shot", None, "prepare_review", "user"),
        ]
        for name, desc, stype, cron, action, source in schedules_data:
            db.add(Schedule(
                schedule_id=generate_id("sched"),
                user_id=USER_ID,
                workspace_id=WS_ID,
                name=name,
                description=desc,
                schedule_type=stype,
                cron_expr=cron,
                action_type=action,
                enabled=True,
                source=source,
                priority="medium",
                run_count=12 if stype == "recurring" else 0,
                next_run_at=NOW + timedelta(hours=1),
                last_run_at=NOW - timedelta(hours=1) if stype == "recurring" else None,
            ))

        # ── TIER 6b: Triggers ──────────────────────────────────────
        triggers_data = [
            ("High-priority email", "Alert when email from investors", {"source": "gmail", "from_domain": "techventures.com"}, "notify"),
            ("PR merged", "Track GitHub PR merges", {"source": "github", "event_type": "pr_merged"}, "run_workflow"),
            ("Meeting in 30min", "Prep for upcoming meetings", {"source": "calendar", "minutes_before": 30}, "generate_prep"),
        ]
        for name, desc, conditions, action in triggers_data:
            db.add(Trigger(
                trigger_id=generate_id("trg"),
                user_id=USER_ID,
                workspace_id=WS_ID,
                name=name,
                description=desc,
                conditions=conditions,
                action_type=action,
                status="active",
                enabled=True,
                fire_count=5,
                last_fired_at=NOW - timedelta(hours=3),
            ))

        # ── TIER 7: Conversations + Messages ───────────────────────
        conv_ids = []
        convs_data = [
            ("Fundraising strategy", 8, 0.042),
            ("Product launch checklist", 14, 0.089),
            ("Team hiring plan", 6, 0.031),
        ]
        for title, msg_count, cost in convs_data:
            cid = generate_id("conv")
            conv_ids.append(cid)
            db.add(Conversation(
                conversation_id=cid,
                user_id=USER_ID,
                workspace_id=WS_ID,
                title=title,
                surface="web",
                status="active",
                message_count=msg_count,
                total_input_tokens=msg_count * 500,
                total_output_tokens=msg_count * 800,
                total_cost_usd=Decimal(str(cost)),
                last_active_at=NOW - timedelta(hours=1),
            ))
            # Add a few messages per conversation
            messages = [
                ("user", f"Help me with {title.lower()}"),
                ("assistant", f"I'll help you with {title.lower()}. Let me analyze the current situation..."),
                ("user", "What are the next steps?"),
                ("assistant", "Based on my analysis, here are the recommended next steps:\n\n1. **Review current status** - Check all pending items\n2. **Prioritize actions** - Focus on high-impact tasks\n3. **Set deadlines** - Assign dates to each action item"),
            ]
            for j, (role, content) in enumerate(messages):
                db.add(Message(
                    message_id=generate_id("msg"),
                    workspace_id=WS_ID,
                    conversation_id=cid,
                    role=role,
                    content=content,
                    surface="web",
                    metadata_={
                        "trace_id": None,
                        "decision": None,
                        "agent_steps": [
                            {
                                "agent": "planner",
                                "model": "claude-sonnet-4-6",
                                "status": "done",
                                "response_text": content[:100],
                                "thinking_preview": None,
                                "tool_calls": [],
                                "input_tokens": 450,
                                "output_tokens": 320,
                                "cache_creation_tokens": 0,
                                "cache_read_tokens": 100,
                                "cost_usd": 0.005,
                                "latency_ms": 1200,
                            }
                        ] if role == "assistant" else [],
                    } if role == "assistant" else None,
                    input_tokens=450 if role == "assistant" else None,
                    output_tokens=320 if role == "assistant" else None,
                    cost_usd=Decimal("0.005") if role == "assistant" else None,
                ))

        # ── TIER 7b: Notifications ─────────────────────────────────
        notifs_data = [
            ("New email from Alex Kim", "Re: Term sheet follow-up", "web", 0.9, "sent"),
            ("Approval required: Deploy v2.0", "Production deployment needs review", "web", 0.95, "pending"),
            ("Task completed: Auth fix deployed", "Bug fix verified in staging", "web", 0.5, "read"),
            ("Meeting in 30 minutes: Board Review", "Prep materials attached", "web", 0.8, "sent"),
            ("Slack: Sarah mentioned you", "In #product-launch channel", "web", 0.6, "pending"),
            ("Weekly metrics report ready", "DAU up 12% week-over-week", "web", 0.4, "dismissed"),
        ]
        for title, body, channel, score, status in notifs_data:
            db.add(Notification(
                notification_id=generate_id("notif"),
                user_id=USER_ID,
                workspace_id=WS_ID,
                channel=channel,
                title=title,
                body=body,
                priority_score=score,
                status=status,
                sent_at=NOW - timedelta(minutes=30) if status in ("sent", "read", "dismissed") else None,
                read_at=NOW - timedelta(minutes=10) if status == "read" else None,
            ))

        # ── TIER 8: Artifacts ──────────────────────────────────────
        artifacts_data = [
            ("document", "Investor Update - Week 12", "application/pdf", 245000),
            ("report", "Q1 Metrics Dashboard Export", "text/html", 18500),
            ("code", "deployment-v2.0-rc1.yaml", "text/yaml", 3200),
            ("image", "Product Architecture Diagram", "image/png", 520000),
        ]
        for atype, title, mime, size in artifacts_data:
            db.add(Artifact(
                artifact_id=generate_id("art"),
                user_id=USER_ID,
                workspace_id=WS_ID,
                artifact_type=atype,
                title=title,
                mime_type=mime,
                size_bytes=size,
                s3_key=f"demo/{atype}/{title.lower().replace(' ', '_')}",
                s3_bucket="jarvis-artifacts-demo",
                metadata_={"demo": True},
            ))

        # ── TIER 9: Traces + Model Calls ───────────────────────────
        trace_ids = []
        traces_data = [
            ("chat", "completed", 3200, 4500, 6200, 0, 0, 0.042, ["planner", "operator", "presenter"], ["search", "write_email"]),
            ("chat", "completed", 2100, 3200, 4800, 500, 200, 0.031, ["researcher", "presenter"], ["search"]),
            ("trigger", "completed", 1500, 2800, 3600, 0, 0, 0.022, ["observer", "librarian"], ["fetch_emails"]),
            ("schedule", "completed", 800, 1200, 2000, 0, 0, 0.011, ["observer", "presenter"], ["generate_briefing"]),
            ("chat", "error", 900, 500, 0, 0, 0, 0.004, ["planner"], []),
            ("trigger", "completed", 1800, 3100, 4200, 0, 0, 0.028, ["observer", "librarian", "planner"], ["fetch_github", "create_task"]),
            ("schedule", "completed", 600, 900, 1500, 0, 0, 0.008, ["observer"], ["sync_calendar"]),
            ("chat", "completed", 4500, 6200, 8400, 800, 400, 0.065, ["researcher", "planner", "governor", "operator"], ["search", "create_plan", "execute_step"]),
        ]
        for trigger, status, duration, inp_t, out_t, cache_c, cache_r, cost, agents, tools in traces_data:
            tid = generate_id("trace")
            trace_ids.append(tid)
            db.add(Trace(
                trace_id=tid,
                user_id=USER_ID,
                workspace_id=WS_ID,
                trigger=trigger,
                status=status,
                started_at=NOW - timedelta(hours=len(trace_ids)),
                ended_at=NOW - timedelta(hours=len(trace_ids)) + timedelta(milliseconds=duration),
                duration_ms=duration,
                total_input_tokens=inp_t,
                total_output_tokens=out_t,
                total_cache_creation_tokens=cache_c,
                total_cache_read_tokens=cache_r,
                total_cost_usd=cost,
                span_count=len(agents),
                error_count=1 if status == "error" else 0,
                agents_invoked=agents,
                tools_called=tools,
                memory_writes=2 if trigger == "chat" else 0,
                spans_json=[
                    {
                        "span_id": generate_id("span"),
                        "agent_name": a,
                        "started_at": (NOW - timedelta(hours=len(trace_ids))).isoformat(),
                        "ended_at": (NOW - timedelta(hours=len(trace_ids)) + timedelta(milliseconds=duration // len(agents))).isoformat(),
                        "duration_ms": duration // len(agents),
                        "input_tokens": inp_t // len(agents),
                        "output_tokens": out_t // len(agents),
                        "cache_creation_tokens": 0,
                        "cache_read_tokens": 0,
                        "thinking_tokens": 0,
                        "cost_usd": cost / len(agents),
                        "tool_calls": tools if i == len(agents) - 1 else [],
                        "error": "Connection timeout" if status == "error" and i == 0 else None,
                    }
                    for i, a in enumerate(agents)
                ],
            ))
            # Model calls per trace
            for a in agents:
                db.add(ModelCall(
                    call_id=generate_id("mc"),
                    trace_id=tid,
                    workspace_id=WS_ID,
                    agent_name=a,
                    model="claude-sonnet-4-6-20250514",
                    input_tokens=inp_t // len(agents),
                    output_tokens=out_t // len(agents),
                    cost_usd=cost / len(agents),
                    duration_ms=duration // len(agents),
                    tools_called=tools if a == agents[-1] else [],
                    error="Connection timeout" if status == "error" else None,
                ))

        # ── TIER 10: DLQ ───────────────────────────────────────────
        for i, (op, dlq_status) in enumerate([
            ("send_email", "pending"),
            ("deploy_staging", "resolved"),
            ("sync_calendar", "exhausted"),
        ]):
            db.add(DeadLetterEntry(
                entry_id=generate_id("dlq"),
                user_id=USER_ID,
                workspace_id=WS_ID,
                operation_type=op,
                error_type="ConnectionError",
                error_message=f"Failed after 3 retries: {op}",
                payload={"target": f"step_{i}", "attempt": 3},
                status=dlq_status,
                attempt_count=3,
                max_attempts=3,
            ))

        # ── Commit everything ──────────────────────────────────────
        await db.commit()
        print(f"Demo data seeded successfully!")
        print(f"  User:      {USER_ID} (founder@jarvis.dev)")
        print(f"  Workspace: {WS_ID}")
        print(f"  Session:   {SESSION_TOKEN}")
        print(f"")
        print(f"To use in the frontend, set the auth token in localStorage:")
        print(f'  localStorage.setItem("jarvis_auth_token", "{SESSION_TOKEN}")')
        print(f'  localStorage.setItem("jarvis_auth_user", JSON.stringify({{')
        print(f'    user_id: "{USER_ID}",')
        print(f'    email: "founder@jarvis.dev",')
        print(f'    display_name: "Demo Founder"')
        print(f"  }}))")


async def main():
    try:
        await seed()
    finally:
        engine = get_engine()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

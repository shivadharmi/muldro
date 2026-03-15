/**
 * Jarvis agent tools registered with OpenClaw.
 *
 * Each tool is a thin bridge: it defines a schema for the model,
 * makes an HTTP call to the Jarvis backend, and returns the result.
 * No business logic lives here.
 */

import { Type } from "@sinclair/typebox";
import { callBackend, type BackendConfig } from "./backend-client.js";

type PluginApi = {
  registerTool: (def: unknown, opts?: { optional: boolean }) => void;
};

function textResult(text: string) {
  return { content: [{ type: "text" as const, text }] };
}

function formatResult(res: { success: boolean; data?: unknown; error?: string }) {
  if (!res.success) {
    return textResult(`Error: ${res.error}`);
  }
  return textResult(JSON.stringify(res.data, null, 2));
}

export function registerTools(api: PluginApi, config: BackendConfig) {
  // ── jarvis_command ─────────────────────────────────────────────
  // General-purpose command dispatch to Jarvis backend.
  // The model uses this when the user gives a natural-language request
  // that should be processed by Jarvis's planner.
  api.registerTool({
    name: "jarvis_command",
    description:
      "Send a command to the Jarvis backend for processing. " +
      "Use this for user requests that need planning, research, or action " +
      "(e.g. 'draft a reply to investor email', 'what's my schedule today').",
    parameters: Type.Object({
      command: Type.String({ description: "The user's request in natural language" }),
      context: Type.Optional(
        Type.String({ description: "Additional context from the conversation" })
      ),
    }),
    async execute(_id: string, params: { command: string; context?: string }) {
      const res = await callBackend(config, "/v1/jarvis/command", "POST", params);
      return formatResult(res);
    },
  });

  // ── jarvis_brief ───────────────────────────────────────────────
  // Fetch the daily briefing for the user.
  api.registerTool({
    name: "jarvis_brief",
    description:
      "Fetch today's daily briefing from Jarvis. " +
      "Returns top priorities, important changes, pending approvals, and recommended actions.",
    parameters: Type.Object({
      date: Type.Optional(
        Type.String({ description: "Date in YYYY-MM-DD format. Defaults to today." })
      ),
    }),
    async execute(_id: string, params: { date?: string }) {
      const date = params.date || new Date().toISOString().split("T")[0];
      const res = await callBackend(config, `/v1/briefings/${date}`, "GET");
      return formatResult(res);
    },
  });

  // ── jarvis_approve ─────────────────────────────────────────────
  // Approve or reject a pending action.
  api.registerTool({
    name: "jarvis_approve",
    description:
      "Approve or reject a pending Jarvis action. " +
      "Use when the user says 'approve', 'reject', 'send it', 'don't send', etc.",
    parameters: Type.Object({
      approval_id: Type.String({ description: "The approval ID to act on" }),
      decision: Type.Union([Type.Literal("approve"), Type.Literal("reject")]),
      reason: Type.Optional(Type.String({ description: "Reason for rejection" })),
    }),
    async execute(
      _id: string,
      params: { approval_id: string; decision: "approve" | "reject"; reason?: string }
    ) {
      const res = await callBackend(
        config,
        `/v1/approvals/${params.approval_id}/${params.decision}`,
        "POST",
        { reason: params.reason }
      );
      return formatResult(res);
    },
  });

  // ── jarvis_tasks ───────────────────────────────────────────────
  // List active tasks and their status.
  api.registerTool({
    name: "jarvis_tasks",
    description:
      "List active Jarvis tasks and their current status. " +
      "Use when the user asks about tasks, progress, or pending items.",
    parameters: Type.Object({
      status: Type.Optional(
        Type.Union([
          Type.Literal("pending"),
          Type.Literal("in_progress"),
          Type.Literal("awaiting_approval"),
          Type.Literal("completed"),
        ])
      ),
      limit: Type.Optional(Type.Number({ description: "Max items to return", default: 10 })),
    }),
    async execute(_id: string, params: { status?: string; limit?: number }) {
      const query = new URLSearchParams();
      if (params.status) query.set("status", params.status);
      if (params.limit) query.set("limit", String(params.limit));
      const qs = query.toString();
      const res = await callBackend(config, `/v1/tasks${qs ? `?${qs}` : ""}`, "GET");
      return formatResult(res);
    },
  });

  // ── jarvis_search ──────────────────────────────────────────────
  // Search Jarvis's memory and world model.
  api.registerTool({
    name: "jarvis_search",
    description:
      "Search Jarvis's memory and knowledge about people, projects, events, and history. " +
      "Use when the user asks about a person, project, past event, or preference.",
    parameters: Type.Object({
      query: Type.String({ description: "Search query" }),
      scope: Type.Optional(
        Type.Union([
          Type.Literal("memory"),
          Type.Literal("entities"),
          Type.Literal("events"),
          Type.Literal("all"),
        ])
      ),
    }),
    async execute(_id: string, params: { query: string; scope?: string }) {
      const res = await callBackend(config, "/v1/search", "POST", params);
      return formatResult(res);
    },
  });

  // ── jarvis_meeting_prep ────────────────────────────────────────
  // Get meeting preparation for an upcoming event.
  api.registerTool(
    {
      name: "jarvis_meeting_prep",
      description:
        "Get meeting preparation for an upcoming calendar event. " +
        "Returns attendee context, agenda, related threads, and action items.",
      parameters: Type.Object({
        meeting_id: Type.Optional(Type.String({ description: "Specific meeting/event ID" })),
        next: Type.Optional(
          Type.Boolean({ description: "If true, prepare for the next upcoming meeting" })
        ),
      }),
      async execute(
        _id: string,
        params: { meeting_id?: string; next?: boolean }
      ) {
        const res = await callBackend(config, "/v1/meetings/prep", "POST", params);
        return formatResult(res);
      },
    },
    { optional: true }
  );

  // ── jarvis_dashboard ─────────────────────────────────────────
  // Canvas dashboard showing approvals, tasks, meetings at a glance.
  api.registerTool({
    name: "jarvis_dashboard",
    description:
      "Show the Jarvis dashboard on Canvas. Displays pending approvals, " +
      "active tasks with progress, upcoming meetings, and recommended actions. " +
      "Use when the user asks for an overview, dashboard, or 'what's going on'.",
    parameters: Type.Object({}),
    async execute() {
      const res = await callBackend(config, "/v1/canvas/dashboard", "GET");
      if (!res.success) return textResult(`Error: ${res.error}`);

      const d = res.data as DashboardData;
      const lines: string[] = [];

      if (d.headline) {
        lines.push(`# ${d.headline}\n`);
      } else {
        lines.push(`# Dashboard — ${d.date}\n`);
      }

      if (d.pending_approvals?.length) {
        lines.push("## Pending Approvals\n");
        for (const a of d.pending_approvals) {
          const risk = a.risk_level === "high" ? " ⚠️" : "";
          lines.push(`- **${a.title}**${risk}`);
          if (a.summary) lines.push(`  ${a.summary}`);
          lines.push(`  ID: \`${a.approval_id}\` | Type: ${a.approval_type || "action"}\n`);
        }
      }

      if (d.active_tasks?.length) {
        lines.push("## Active Tasks\n");
        for (const t of d.active_tasks) {
          const progress =
            t.step_count > 0
              ? ` [${t.steps_completed}/${t.step_count}]`
              : "";
          lines.push(
            `- **${t.goal}**${progress} — ${t.priority} priority, ${t.status}`
          );
        }
        lines.push("");
      }

      if (d.upcoming_meetings?.length) {
        lines.push("## Upcoming Meetings\n");
        for (const m of d.upcoming_meetings) {
          const time = m.starts_at
            ? new Date(m.starts_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
            : "TBD";
          const attendees = m.attendee_count > 0 ? ` (${m.attendee_count} attendees)` : "";
          lines.push(`- **${time}** — ${m.title}${attendees}`);
        }
        lines.push("");
      }

      if (d.recommended_actions?.length) {
        lines.push("## Recommended Actions\n");
        for (const action of d.recommended_actions) {
          lines.push(`- ${action}`);
        }
      }

      return textResult(lines.join("\n"));
    },
  });

  // ── jarvis_approval_card ─────────────────────────────────────
  // Detailed approval card for Canvas.
  api.registerTool({
    name: "jarvis_approval_card",
    description:
      "Show detailed approval information on Canvas. " +
      "Use when the user asks about a specific pending approval or wants more detail " +
      "before approving/rejecting.",
    parameters: Type.Object({
      approval_id: Type.String({ description: "The approval ID to display" }),
    }),
    async execute(_id: string, params: { approval_id: string }) {
      const res = await callBackend(
        config,
        `/v1/approvals/${params.approval_id}`,
        "GET"
      );
      if (!res.success) return textResult(`Error: ${res.error}`);

      const a = res.data as ApprovalDetail;
      const lines: string[] = [
        `# Approval: ${a.title}\n`,
        `| Field | Value |`,
        `|-------|-------|`,
        `| Status | ${a.status} |`,
        `| Risk | ${a.risk_level} |`,
        `| Type | ${a.approval_type || "action"} |`,
      ];
      if (a.plan_goal) lines.push(`| Plan | ${a.plan_goal} |`);
      if (a.created_at) lines.push(`| Created | ${a.created_at} |`);
      if (a.decided_at) lines.push(`| Decided | ${a.decided_at} |`);
      if (a.decision_reason) lines.push(`| Reason | ${a.decision_reason} |`);
      lines.push("");

      if (a.summary) {
        lines.push(`## Summary\n\n${a.summary}\n`);
      }

      if (a.status === "pending") {
        lines.push(
          `## Actions\n\n` +
            `To approve: use \`jarvis_approve\` with ID \`${a.approval_id}\` and decision \`approve\`\n` +
            `To reject: use \`jarvis_approve\` with ID \`${a.approval_id}\` and decision \`reject\``
        );
      }

      return textResult(lines.join("\n"));
    },
  });

  // ── jarvis_task_detail ───────────────────────────────────────
  // Detailed task view with execution steps for Canvas.
  api.registerTool({
    name: "jarvis_task_detail",
    description:
      "Show detailed task information with execution steps on Canvas. " +
      "Use when the user asks about a specific task's progress or details.",
    parameters: Type.Object({
      task_id: Type.String({ description: "The task/plan ID to display" }),
    }),
    async execute(_id: string, params: { task_id: string }) {
      const res = await callBackend(
        config,
        `/v1/tasks/${params.task_id}`,
        "GET"
      );
      if (!res.success) return textResult(`Error: ${res.error}`);

      const t = res.data as TaskDetail;
      const lines: string[] = [
        `# Task: ${t.goal}\n`,
        `| Field | Value |`,
        `|-------|-------|`,
        `| Priority | ${t.priority} |`,
        `| Status | ${t.status} |`,
        `| Risk | ${t.risk_level} |`,
        `| Decision | ${t.decision} |`,
      ];
      if (t.execution_status) lines.push(`| Execution | ${t.execution_status} |`);
      if (t.created_at) lines.push(`| Created | ${t.created_at} |`);
      lines.push("");

      if (t.reasoning_summary) {
        lines.push(`## Reasoning\n\n${t.reasoning_summary}\n`);
      }

      if (t.steps?.length) {
        lines.push("## Steps\n");
        for (const step of t.steps) {
          const icon =
            step.status === "completed"
              ? "✅"
              : step.status === "running"
                ? "🔄"
                : step.status === "failed"
                  ? "❌"
                  : "⬜";
          let line = `${icon} **${step.task_type}** — ${step.status}`;
          if (step.result_summary) line += `\n   ${step.result_summary}`;
          lines.push(line);
        }
      }

      return textResult(lines.join("\n"));
    },
  });

  // ── jarvis_ingest_event ─────────────────────────────────────
  // Ingest an event into the Jarvis intelligence pipeline.
  api.registerTool({
    name: "jarvis_ingest_event",
    description:
      "Ingest an event into the Jarvis intelligence backend for scoring, " +
      "entity extraction, memory extraction, and proactive planning. " +
      "Use this AFTER reading data from external sources (emails via gog gmail, " +
      "calendar events via gog calendar, GitHub activity via gh, Slack messages, etc.). " +
      "Extract the key fields from what you read and pass them here so Jarvis can " +
      "track, score, and act on important events.",
    parameters: Type.Object({
      source: Type.String({ description: "Source system (gmail, calendar, github, slack, etc.)" }),
      event_type: Type.String({ description: "Type of event (email_received, meeting_created, pr_opened, message_received, etc.)" }),
      entity_type: Type.String({ description: "Type of entity (email_thread, calendar_event, pull_request, channel_message, etc.)" }),
      entity_id: Type.String({ description: "Unique ID of the entity from the source system" }),
      title: Type.String({ description: "Human-readable title/subject of the event" }),
      summary: Type.Optional(Type.String({ description: "Brief summary of the event content" })),
      actor: Type.Optional(Type.Object({
        type: Type.Optional(Type.String({ description: "Actor type (person, bot, system)" })),
        name: Type.Optional(Type.String({ description: "Actor display name" })),
        email: Type.Optional(Type.String({ description: "Actor email address" })),
      })),
      occurred_at: Type.Optional(Type.String({ description: "ISO 8601 timestamp of when the event occurred" })),
      raw_payload: Type.Optional(Type.Object({}, { additionalProperties: true })),
    }),
    async execute(
      _id: string,
      params: {
        source: string;
        event_type: string;
        entity_type: string;
        entity_id: string;
        title: string;
        summary?: string;
        actor?: { type?: string; name?: string; email?: string };
        occurred_at?: string;
        raw_payload?: Record<string, unknown>;
      }
    ) {
      const res = await callBackend(config, "/v1/events/ingest", "POST", params);
      if (!res.success) return textResult(`Error: ${res.error}`);
      const d = res.data as { event_id: string | null; status: string; importance_score: number | null };
      if (d.status === "duplicate") {
        return textResult("Event already ingested (duplicate). No action needed.");
      }
      return textResult(
        `Event ingested: ${d.event_id} (importance: ${d.importance_score?.toFixed(2) ?? "n/a"})`
      );
    },
  });

  // ── jarvis_report_observation ───────────────────────────────
  // Report observation cycle results to backend.
  api.registerTool(
    {
      name: "jarvis_report_observation",
      description:
        "Report the results of an observation cycle to Jarvis. " +
        "Call this AFTER completing an observation (checking emails, calendar, GitHub) " +
        "to track observation health and freshness.",
      parameters: Type.Object({
        source: Type.String({ description: "Source that was observed (gmail, calendar, github)" }),
        items_found: Type.Number({ description: "Number of items found during observation", default: 0 }),
        items_ingested: Type.Number({ description: "Number of items ingested to Jarvis", default: 0 }),
        status: Type.Optional(
          Type.Union([Type.Literal("ok"), Type.Literal("error")])
        ),
        error_message: Type.Optional(Type.String({ description: "Error message if status is error" })),
      }),
      async execute(
        _id: string,
        params: {
          source: string;
          items_found: number;
          items_ingested: number;
          status?: string;
          error_message?: string;
        }
      ) {
        const res = await callBackend(config, "/v1/observations/report", "POST", {
          source: params.source,
          items_found: params.items_found,
          items_ingested: params.items_ingested,
          status: params.status ?? "ok",
          error_message: params.error_message,
        });
        if (!res.success) return textResult(`Error: ${res.error}`);
        const d = res.data as { source: string; is_stale: boolean; status: string };
        return textResult(
          `Observation reported: ${d.source} (status: ${d.status}, stale: ${d.is_stale})`
        );
      },
    },
    { optional: true }
  );

  // ── jarvis_schedule ──────────────────────────────────────────
  // Manage backend-owned dynamic schedules.
  api.registerTool({
    name: "jarvis_schedule",
    description:
      "Manage Jarvis schedules — create, list, update, pause, resume, or delete " +
      "scheduled tasks. Use when the user says things like 'check email every 5 minutes', " +
      "'stop monitoring GitHub', 'remind me at 3pm', 'show my schedules', etc.",
    parameters: Type.Object({
      action: Type.Union([
        Type.Literal("create"),
        Type.Literal("list"),
        Type.Literal("update"),
        Type.Literal("pause"),
        Type.Literal("resume"),
        Type.Literal("delete"),
      ]),
      schedule_id: Type.Optional(
        Type.String({ description: "Schedule ID (required for update/pause/resume/delete)" })
      ),
      name: Type.Optional(Type.String({ description: "Human-readable name for the schedule" })),
      description: Type.Optional(Type.String({ description: "Description of what this schedule does" })),
      schedule_type: Type.Optional(
        Type.Union([Type.Literal("recurring"), Type.Literal("one_shot")])
      ),
      cron_expr: Type.Optional(
        Type.String({ description: "Cron expression for recurring schedules (e.g. '*/15 * * * *')" })
      ),
      run_at: Type.Optional(
        Type.String({ description: "ISO 8601 timestamp for one_shot schedules" })
      ),
      action_type: Type.Optional(
        Type.Union([
          Type.Literal("observe_source"),
          Type.Literal("generate_briefing"),
          Type.Literal("meeting_prep"),
          Type.Literal("heartbeat"),
          Type.Literal("custom_agent_task"),
          Type.Literal("wake_agent"),
        ])
      ),
      action_config: Type.Optional(
        Type.Object({}, { additionalProperties: true, description: "Action-specific config" })
      ),
      priority: Type.Optional(
        Type.Union([Type.Literal("low"), Type.Literal("medium"), Type.Literal("high")])
      ),
      enabled: Type.Optional(Type.Boolean()),
      source: Type.Optional(Type.String()),
    }),
    async execute(
      _id: string,
      params: {
        action: string;
        schedule_id?: string;
        name?: string;
        description?: string;
        schedule_type?: string;
        cron_expr?: string;
        run_at?: string;
        action_type?: string;
        action_config?: Record<string, unknown>;
        priority?: string;
        enabled?: boolean;
        source?: string;
      }
    ) {
      const { action, schedule_id } = params;

      if (action === "create") {
        const res = await callBackend(config, "/v1/schedules", "POST", {
          name: params.name,
          description: params.description,
          schedule_type: params.schedule_type ?? "recurring",
          cron_expr: params.cron_expr,
          run_at: params.run_at,
          action_type: params.action_type,
          action_config: params.action_config,
          priority: params.priority ?? "medium",
          enabled: params.enabled ?? true,
          source: params.source ?? "user",
        });
        return formatResult(res);
      }

      if (action === "list") {
        const query = new URLSearchParams();
        if (params.enabled !== undefined) query.set("enabled", String(params.enabled));
        if (params.action_type) query.set("action_type", params.action_type);
        if (params.source) query.set("source", params.source);
        const qs = query.toString();
        const res = await callBackend(config, `/v1/schedules${qs ? `?${qs}` : ""}`, "GET");
        return formatResult(res);
      }

      if (!schedule_id) {
        return textResult(`Error: schedule_id is required for action '${action}'`);
      }

      if (action === "update") {
        const body: Record<string, unknown> = {};
        if (params.name !== undefined) body.name = params.name;
        if (params.description !== undefined) body.description = params.description;
        if (params.cron_expr !== undefined) body.cron_expr = params.cron_expr;
        if (params.run_at !== undefined) body.run_at = params.run_at;
        if (params.action_type !== undefined) body.action_type = params.action_type;
        if (params.action_config !== undefined) body.action_config = params.action_config;
        if (params.priority !== undefined) body.priority = params.priority;
        if (params.enabled !== undefined) body.enabled = params.enabled;
        const res = await callBackend(config, `/v1/schedules/${schedule_id}`, "PATCH", body);
        return formatResult(res);
      }

      if (action === "pause") {
        const res = await callBackend(config, `/v1/schedules/${schedule_id}/pause`, "POST");
        return formatResult(res);
      }

      if (action === "resume") {
        const res = await callBackend(config, `/v1/schedules/${schedule_id}/resume`, "POST");
        return formatResult(res);
      }

      if (action === "delete") {
        const res = await callBackend(config, `/v1/schedules/${schedule_id}`, "DELETE");
        if (!res.success) return textResult(`Error: ${res.error}`);
        return textResult(`Schedule ${schedule_id} deleted.`);
      }

      return textResult(`Error: Unknown action '${action}'`);
    },
  });

  // ── jarvis_brief_feedback ─────────────────────────────────────
  // Report user feedback on a briefing back to the backend.
  api.registerTool({
    name: "jarvis_brief_feedback",
    description:
      "Report user feedback on a daily briefing. Call this when the user " +
      "rates a briefing, acts on a briefing item, dismisses an item, or asks " +
      "a follow-up question about a briefing item. This feeds the learning loop " +
      "so future briefings improve.",
    parameters: Type.Object({
      briefing_id: Type.String({ description: "The briefing ID" }),
      feedback_type: Type.Union([
        Type.Literal("rating"),
        Type.Literal("item_acted_on"),
        Type.Literal("item_dismissed"),
        Type.Literal("follow_up_asked"),
      ]),
      rating: Type.Optional(
        Type.Number({ description: "1-5 rating (required when feedback_type is rating)" })
      ),
      item_section: Type.Optional(
        Type.String({ description: "Briefing section (top_priorities, recommended_actions, changes_since_last)" })
      ),
      item_index: Type.Optional(
        Type.Number({ description: "Index of the item in the section (0-based)" })
      ),
      item_title: Type.Optional(
        Type.String({ description: "Title or text of the item" })
      ),
      comment: Type.Optional(
        Type.String({ description: "User's comment or follow-up question" })
      ),
    }),
    async execute(
      _id: string,
      params: {
        briefing_id: string;
        feedback_type: string;
        rating?: number;
        item_section?: string;
        item_index?: number;
        item_title?: string;
        comment?: string;
      }
    ) {
      const res = await callBackend(
        config,
        `/v1/briefings/${params.briefing_id}/feedback`,
        "POST",
        {
          feedback_type: params.feedback_type,
          rating: params.rating,
          item_section: params.item_section,
          item_index: params.item_index,
          item_title: params.item_title,
          comment: params.comment,
        }
      );
      if (!res.success) return textResult(`Error: ${res.error}`);
      return textResult("Feedback recorded. Thank you!");
    },
  });

  // ── jarvis_heartbeat ────────────────────────────────────────
  // Trigger periodic maintenance tasks.
  api.registerTool(
    {
      name: "jarvis_heartbeat",
      description:
        "Trigger Jarvis heartbeat for periodic maintenance. " +
        "Expires stale plans and approvals, processes dead-letter queue retries, " +
        "and cleans up expired memories. Typically called by cron every hour.",
      parameters: Type.Object({}),
      async execute() {
        const res = await callBackend(config, "/v1/system/heartbeat", "POST");
        return formatResult(res);
      },
    },
    { optional: true }
  );
}

// ── Type helpers for backend responses ────────────────────────────

interface DashboardData {
  headline?: string;
  date: string;
  pending_approvals: Array<{
    approval_id: string;
    title: string;
    summary?: string;
    risk_level: string;
    approval_type: string;
    created_at?: string;
  }>;
  active_tasks: Array<{
    task_id: string;
    goal: string;
    priority: string;
    status: string;
    decision: string;
    step_count: number;
    steps_completed: number;
  }>;
  upcoming_meetings: Array<{
    event_id: string;
    title: string;
    starts_at?: string;
    attendee_count: number;
  }>;
  recommended_actions: string[];
  briefing_id?: string;
}

interface ApprovalDetail {
  approval_id: string;
  status: string;
  title: string;
  summary?: string;
  approval_type?: string;
  risk_level: string;
  created_at?: string;
  decided_at?: string;
  decision_reason?: string;
  execution_id?: string;
  plan_goal?: string;
  artifact_refs?: Record<string, unknown>;
}

interface TaskDetail {
  task_id: string;
  goal: string;
  priority: string;
  status: string;
  decision: string;
  risk_level: string;
  reasoning_summary?: string;
  execution_status?: string;
  created_at?: string;
  steps: Array<{
    task_id: string;
    task_type: string;
    status: string;
    result_summary?: string;
  }>;
}

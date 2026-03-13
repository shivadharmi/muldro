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
}

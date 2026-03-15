/** Static agent configuration — mirrors backend/src/orchestrator/agents.py */

export interface AgentConfig {
  name: string;
  model_tier: string;
  max_tokens: number;
  temperature: number;
  tools: string[];
}

export const AGENT_CONFIGS: AgentConfig[] = [
  {
    name: "observer",
    model_tier: "sonnet",
    max_tokens: 4096,
    temperature: 0.3,
    tools: [
      "gmail_list", "gmail_read", "gmail_search",
      "calendar_list", "calendar_get",
      "drive_list", "drive_search",
      "slack_list_channels", "slack_get_messages", "slack_search",
      "ingest_event", "report_observation",
      "get_observation_cursor", "update_observation_cursor",
    ],
  },
  {
    name: "librarian",
    model_tier: "sonnet",
    max_tokens: 4096,
    temperature: 0.3,
    tools: ["update_entity", "get_entities", "search_memory"],
  },
  {
    name: "planner",
    model_tier: "opus",
    max_tokens: 8192,
    temperature: 0.3,
    tools: ["plan_command", "get_active_plans", "search_memory", "get_entities"],
  },
  {
    name: "governor",
    model_tier: "sonnet",
    max_tokens: 4096,
    temperature: 0.1,
    tools: ["evaluate_policy", "approve_action"],
  },
  {
    name: "operator",
    model_tier: "sonnet",
    max_tokens: 4096,
    temperature: 0.3,
    tools: [
      "gmail_send", "gmail_send_email", "gmail_draft", "gmail_create_draft", "gmail_reply",
      "calendar_create", "calendar_create_event", "calendar_update", "calendar_update_event",
      "slack_post_message", "slack_send_message",
      "github_comment", "github_create_issue", "github_create_pr",
      "update_execution",
    ],
  },
  {
    name: "presenter",
    model_tier: "sonnet",
    max_tokens: 4096,
    temperature: 0.3,
    tools: [
      "get_briefing", "search_memory", "get_entities",
      "send_telegram", "send_approval_prompt", "push_ui_update",
    ],
  },
  {
    name: "researcher",
    model_tier: "sonnet",
    max_tokens: 4096,
    temperature: 0.3,
    tools: [
      "search_memory", "get_entities",
      "gmail_list", "gmail_read", "gmail_search",
      "calendar_list", "calendar_get",
      "drive_list", "drive_search",
      "slack_list_channels", "slack_get_messages", "slack_search",
      "perplexity_search",
      "playwright_navigate", "playwright_screenshot", "playwright_get_text",
    ],
  },
  {
    name: "persona",
    model_tier: "haiku",
    max_tokens: 4096,
    temperature: 0.3,
    tools: ["search_memory", "extract_preferences"],
  },
];

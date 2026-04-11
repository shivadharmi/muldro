"""Intent classification and decision extraction for the Jarvis orchestrator.

Fast Haiku-based intent classifier and structured decision extractor.
Extracted from jarvis.py to reduce orchestrator size.
"""

import json
import logging

from src.llm_utils import parse_llm_json
from src.orchestrator.contracts import PlanOutput, PlanStep

logger = logging.getLogger(__name__)

# Intent classifier prompt — used with Haiku for fast, cheap classification
INTENT_CLASSIFIER_PROMPT = """\
<role>
You classify user messages for a personal AI assistant called Jarvis.
Output ONLY a JSON object, nothing else.
</role>

<intents>
- greeting: Greetings, pleasantries, "hey", "hi", "good morning"
- chitchat: Casual conversation, "how are you", jokes, small talk
- simple_question: Factual question answerable from Jarvis's stored memory or context
  (contacts, prior conversations, stored facts)
- data_fetch: Read from external source (check email, show calendar, read slack)
- status_query: Asking about goals, plans, briefing, pending items, tasks
- approval_response: Approving/rejecting a pending action
- command: Actionable WRITE request needing planning (send email, schedule, create)
- complex: Multi-step, ambiguous, or high-stakes requests needing deep planning
- direct_answer: Question answerable from general world knowledge, no Jarvis memory or
  external service needed ("what's the capital of France", "explain async/await")
- single_read: One read from a specific external service (latest email, today's calendar)
- memory_operation: Store, recall, or update knowledge ("remember this", "what do you know about X")
- acknowledgment: Confirming, thanking, or acknowledging ("ok", "got it", "thanks", "sounds good")
</intents>

<sources>
Optionally include "sources" — external data sources relevant to this message.
Valid values: gmail, calendar, slack, github
Only include sources the user's intent clearly relates to. Omit if none apply.
</sources>

<output_format>
{"intent": "<one of above>", "confidence": 0.0-1.0, "sources": ["source1", ...]}
</output_format>

<examples>
"Hey Jarvis" -> {"intent": "greeting", "confidence": 0.99}
"What's John's email?" -> {"intent": "simple_question", "confidence": 0.9}
"Check my gmail" -> {"intent":"data_fetch","confidence":0.95,"sources":["gmail"]}
"What's the capital of France?" -> {"intent": "direct_answer", "confidence": 0.95}
"Show my latest emails" -> {"intent": "single_read", "confidence": 0.95, "sources": ["gmail"]}
"Remember that John prefers morning meetings" -> {"intent": "memory_operation", "confidence": 0.9}
"Ok got it, thanks" -> {"intent": "acknowledgment", "confidence": 0.95}
"What's on my calendar today" -> {"intent":"data_fetch","confidence":0.95,"sources":["calendar"]}
"Any new Slack messages?" -> {"intent":"data_fetch","confidence":0.9,"sources":["slack"]}
"Did Sarah reply?" -> {"intent":"data_fetch","confidence":0.85,"sources":["gmail","slack"]}
"Any new PRs on the repo?" -> {"intent":"data_fetch","confidence":0.9,"sources":["github"]}
"Show my goals" -> {"intent":"status_query","confidence":0.95}
"Approve that email" -> {"intent":"approval_response","confidence":0.9}
"Send a follow-up to the investor" -> {"intent":"command","confidence":0.95,"sources":["gmail"]}
"Analyze our Q3 pipeline" -> {"intent":"complex","confidence":0.9}
</examples>
"""

# Valid perception sources returned by the intent classifier
VALID_PERCEPTION_SOURCES = {"gmail", "calendar", "slack", "github"}

# Intents that skip the Planner entirely
FAST_INTENTS = {
    "greeting",
    "chitchat",
    "simple_question",
    "data_fetch",
    "status_query",
    "approval_response",
    "direct_answer",
    "single_read",
    "memory_operation",
    "acknowledgment",
}

# Confidence threshold — below this, fall back to Planner
INTENT_CONFIDENCE_THRESHOLD = 0.7

_VALID_INTENTS = {
    "greeting",
    "chitchat",
    "simple_question",
    "data_fetch",
    "status_query",
    "approval_response",
    "command",
    "complex",
    "direct_answer",
    "single_read",
    "memory_operation",
    "acknowledgment",
}


def extract_plan(response_text: str) -> PlanOutput:
    """Parse Planner agent response into validated PlanOutput.

    Uses ``parse_llm_json`` to handle code fences and whitespace,
    then falls back to brace-matching for JSON embedded in prose.
    Validates against the ``PlanOutput`` Pydantic model.
    Falls back to a minimal single-step respond plan on parse failure.
    """
    # First try: code fences + raw JSON via parse_llm_json
    try:
        raw = parse_llm_json(response_text)
        if isinstance(raw, dict):
            return PlanOutput.model_validate(raw)
    except (json.JSONDecodeError, ValueError):
        pass

    # Second try: brace-matching for JSON embedded in prose — scan all top-level blocks
    start = 0
    while start < len(response_text):
        idx = response_text.find("{", start)
        if idx == -1:
            break
        depth = 0
        end = -1
        for i, ch in enumerate(response_text[idx:], idx):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == -1:
            break  # unclosed brace — stop scanning
        try:
            raw = json.loads(response_text[idx : end + 1])
            if isinstance(raw, dict):
                return PlanOutput.model_validate(raw)
        except (json.JSONDecodeError, ValueError):
            pass
        start = end + 1

    return PlanOutput(
        goal=response_text[:200],
        steps=[PlanStep(description="Respond to user", capability="respond")],
    )


def intent_to_plan(intent: str, message: str, capabilities: list[str]) -> PlanOutput:
    """Generate a lightweight PlanOutput from fast intent classification.

    Maps each fast intent to a minimal plan with the appropriate
    capability step.
    """
    goal = message[:200]

    if intent in ("greeting", "chitchat", "acknowledgment"):
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(description="Respond to user", capability="respond")],
            priority="low",
        )

    if intent == "direct_answer":
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(description="Answer from context", capability="reason")],
        )

    if intent == "simple_question":
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(description="Answer question", capability="reason")],
        )

    if intent in ("single_read", "data_fetch"):
        # Use "perceive" as a broad read capability — the Perceiver agent
        # receives ALL its read tools and autonomously decides which to use.
        # This avoids restricting to a single capability family when the
        # user asks about multiple sources (e.g. "check email and calendar").
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(description=goal, capability="perceive", risk="none")],
        )

    if intent == "status_query":
        return PlanOutput(
            goal=goal,
            steps=[
                PlanStep(description="Retrieve status", capability="knowledge.search"),
            ],
        )

    if intent == "memory_operation":
        return PlanOutput(
            goal=goal,
            steps=[
                PlanStep(
                    description="Store or recall knowledge",
                    capability="knowledge.search",
                ),
            ],
        )

    if intent == "approval_response":
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(description="Process approval", capability="respond")],
        )

    # Fallback for unknown intents
    return PlanOutput(
        goal=goal,
        steps=[PlanStep(description="Respond to user", capability="respond")],
        priority="low",
    )


async def classify_intent(
    client,
    model: str,
    message: str,
    history_block: str = "",
) -> tuple[str, float, list[str]]:
    """Classify user message intent using Haiku — fast and cheap.

    Returns (intent, confidence, sources). Falls back to "command" on error.
    ``sources`` contains perception sources relevant to the message.
    """
    classifier_input = message
    if history_block:
        classifier_input = f"{history_block}\n\nUser: {message}"

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=150,
            temperature=0,
            system=[{"type": "text", "text": INTENT_CLASSIFIER_PROMPT}],
            messages=[{"role": "user", "content": classifier_input}],
        )

        text = "".join(b.text for b in response.content if b.type == "text")

        if "{" in text:
            start = text.index("{")
            end = text.rindex("}") + 1
            parsed = json.loads(text[start:end])
            intent = parsed.get("intent", "command")
            confidence = float(parsed.get("confidence", 0.5))

            if intent not in _VALID_INTENTS:
                intent = "command"

            # Extract perception sources (validated)
            raw_sources = parsed.get("sources", [])
            sources = [
                s for s in raw_sources if isinstance(s, str) and s in VALID_PERCEPTION_SOURCES
            ]

            logger.info(
                "intent_classified",
                extra={
                    "intent": intent,
                    "confidence": confidence,
                    "sources": sources,
                    "message_preview": message[:80],
                },
            )
            return intent, confidence, sources

    except Exception as e:
        logger.warning("Intent classification failed, defaulting to command: %s", e)

    return "command", 0.5, []

"""Intent classification and decision extraction for the Jarvis orchestrator.

Fast Haiku-based intent classifier and structured decision extractor.
Extracted from jarvis.py to reduce orchestrator size.
"""

import json
import logging
from typing import Any

from src.orchestrator.contracts import PlannerOutput

logger = logging.getLogger(__name__)

# Intent classifier prompt — used with Haiku for fast, cheap classification
INTENT_CLASSIFIER_PROMPT = """\
<role>
You classify user messages for a personal AI assistant called Jarvis.
Output ONLY a JSON object, nothing else.
</role>

<intents>
- greeting: Greetings, pleasantries, "hey", "hi", "good morning", "thanks"
- chitchat: Casual conversation, "how are you", jokes, small talk
- simple_question: Direct factual question answerable from context/memory
- data_fetch: Read from external source (check email, show calendar, read slack)
- status_query: Asking about goals, plans, briefing, pending items, tasks
- approval_response: Approving/rejecting a pending action
- command: Actionable WRITE request needing planning (send email, schedule, create)
- complex: Multi-step, ambiguous, or high-stakes requests needing deep planning
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
"Show my latest emails" -> {"intent":"data_fetch","confidence":0.95,"sources":["gmail"]}
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
}


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


def intent_to_decision(intent: str, message: str) -> PlannerOutput:
    """Synthesize a lightweight PlannerOutput from a fast intent classification."""
    intent_map = {
        "greeting": "acknowledge",
        "chitchat": "acknowledge",
        "simple_question": "answer_directly",
        "data_fetch": "read_source",
        "status_query": "answer_directly",
        "approval_response": "acknowledge",
    }
    return PlannerOutput(
        decision=intent_map.get(intent, "acknowledge"),
        reasoning=f"Fast-classified as {intent}",
        priority="low" if intent in ("greeting", "chitchat") else "medium",
        risk_level="none" if intent in ("greeting", "chitchat") else "low",
        execution_mode="auto_execute",
        goal=message[:200],
    )


def extract_decision(response_text: str) -> PlannerOutput:
    """Extract and validate structured decision from planner response."""
    raw: dict[str, Any] = {}
    try:
        if "{" in response_text:
            start = response_text.index("{")
            depth = 0
            for i, ch in enumerate(response_text[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        json_str = response_text[start : i + 1]
                        raw = json.loads(json_str)
                        break
    except (json.JSONDecodeError, ValueError):
        pass

    if not raw:
        raw = {"decision": "acknowledge", "reasoning": response_text[:500]}

    return PlannerOutput.model_validate(raw)

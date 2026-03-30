"""Lightweight token estimation without external dependencies.

Used for pre-API context budget checks to avoid sending oversized
prompts that waste tokens or risk context window overflow.
"""


def estimate_tokens(text: str) -> int:
    """Estimate token count. ~4 chars per token for English text."""
    return len(text) // 4 + 1


def estimate_message_tokens(messages: list[dict], system: str = "") -> int:
    """Estimate total input tokens for a Claude API call."""
    total = estimate_tokens(system)
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                total += estimate_tokens(str(block.get("text", "")))
    return total + 50  # overhead for message framing


MODEL_CONTEXT_WINDOWS = {
    "haiku": 200_000,
    "sonnet": 200_000,
    "opus": 200_000,
}

MAX_CONTEXT_UTILIZATION = 0.80  # Stay under 80% of model window

"""Utilities for parsing LLM responses."""

import json
import re

_FENCE_RE = re.compile(r"```(?:\w*)\s*\n?(.*?)```", re.DOTALL)


def parse_llm_json(text: str, default: dict | list | None = None) -> dict | list:
    """Parse JSON from an LLM response, stripping markdown code fences if present.

    Handles common Claude response patterns:
    - Raw JSON
    - JSON wrapped in ```json ... ``` or ``` ... ```
    - Leading/trailing whitespace around fences
    - Empty or non-JSON text (returns *default* when provided)
    """
    text = text.strip()
    if not text:
        if default is not None:
            return default
        raise json.JSONDecodeError("Empty LLM response", text, 0)
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if default is not None:
            return default
        raise

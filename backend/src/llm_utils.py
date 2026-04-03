"""Utilities for parsing LLM responses."""

import json
import re

_FENCE_RE = re.compile(r"```(?:\w*)\s*\n?(.*?)```", re.DOTALL)


def parse_llm_json(text: str) -> dict | list:
    """Parse JSON from an LLM response, stripping markdown code fences if present.

    Handles common Claude response patterns:
    - Raw JSON
    - JSON wrapped in ```json ... ``` or ``` ... ```
    - Leading/trailing whitespace around fences
    """
    text = text.strip()
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()
    return json.loads(text)

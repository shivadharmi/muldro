"""Utilities for parsing LLM responses."""

import json
import re

_FENCE_RE = re.compile(r"```(?:\w*)\s*\n?(.*?)```", re.DOTALL)


def parse_llm_json(text: str, default: dict | list | None = None) -> dict | list:
    """Parse JSON from an LLM response, tolerating fences and surrounding prose.

    Handles common Claude response patterns:
    - Raw JSON
    - JSON wrapped in ```json ... ``` or ``` ... ```
    - Leading/trailing whitespace around fences
    - Leading prose before the first ``{`` / ``[``
    - **Trailing prose after the JSON value** — e.g. a valid object followed by
      an explanatory sentence. Plain ``json.loads`` raises ``Extra data`` here;
      we locate the first ``{``/``[`` and use ``raw_decode`` to parse the first
      complete JSON value, ignoring anything after it.
    - Empty or non-JSON text (returns *default* when provided, else raises
      ``json.JSONDecodeError``)
    """
    text = text.strip()
    if not text:
        if default is not None:
            return default
        raise json.JSONDecodeError("Empty LLM response", text, 0)
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    # Try each ``{``/``[`` candidate in order, decoding the first one that
    # yields a complete JSON value. Leading prose with a stray brace (e.g.
    # ``Here is {the answer}: {...real json...}``) no longer discards valid
    # JSON: the bogus ``{the answer}`` fails raw_decode, so we advance to the
    # next candidate. raw_decode ignores any trailing prose after the value.
    decoder = json.JSONDecoder()
    last_error: json.JSONDecodeError | None = None
    for start in _value_start_candidates(text):
        try:
            value, _ = decoder.raw_decode(text, start)
            return value
        except json.JSONDecodeError as exc:
            last_error = exc
            continue

    if default is not None:
        return default
    if last_error is not None:
        raise last_error
    raise json.JSONDecodeError("No JSON value found in LLM response", text, 0)


def _value_start_candidates(text: str):
    """Yield indices of every ``{`` / ``[`` in *text*, in ascending order.

    Each is a candidate JSON-value start; the parser tries them in turn until
    one decodes. Bounded (one pass over the string) and cheap.
    """
    candidates = sorted(idx for idx in range(len(text)) if text[idx] == "{" or text[idx] == "[")
    yield from candidates


def coerce_to_object(parsed: dict | list, *, list_key: str) -> dict:
    """Coerce a ``parse_llm_json`` result into a dict for object-expecting callers.

    ``parse_llm_json`` returns whatever JSON shape the model emitted. Extraction
    prompts ask for ``{list_key: [...]}`` but the model sometimes returns a **bare
    array** ``[...]`` — which crashes callers that do ``.get(list_key)``. This wraps a
    bare list under ``list_key`` and turns any other non-dict into ``{list_key: []}``.
    """
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {list_key: parsed}
    return {list_key: []}

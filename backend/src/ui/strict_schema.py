"""Turn a Pydantic model's JSON Schema into an OpenAI-strict one, on the way out.

Provider-side structured output (OpenAI Structured Outputs, and the strict tool-schema
dialects modelled on it) rejects a schema unless every object node carries
``additionalProperties: false`` and lists *every* declared property in ``required``.
Pydantic emits neither: ``extra="ignore"`` produces no ``additionalProperties`` key at all
(only ``extra="forbid"`` emits ``false``), and a field with a default is simply left out of
``required``.

The obvious fix — making the models themselves strict — would be wrong, because the same
models parse *inbound* payloads. ``src/ui/component_properties.py`` is shared with the legacy
``A2UIComponent._validate_properties`` path, where ``extract_surface_data`` returns ``None``
for the **whole payload** on any validation failure. Under ``extra="forbid"`` a model that
emitted one stray key would not lose that key — it would lose the entire surface.

So strictness is a property of what we hand a provider, not of how we parse. This module
walks the generated schema and returns a new, strict structure; the models stay lenient and
are never touched.

Free-form maps are **asserted, not repaired**. A ``dict``-typed field generates either
``additionalProperties: {...}`` or a bare ``{"type": "object"}`` with no properties, and
neither can be expressed in a strict schema — coercing them to ``additionalProperties: false``
would silently narrow the field to "accepts only ``{}``". Commits leading up to this one
closed every such shape in the A2UI models; this raises if one comes back.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel

# Keys whose value is a *list of schemas* and must therefore be walked.
_SCHEMA_LIST_KEYS = ("anyOf", "oneOf", "allOf", "prefixItems")

# Keys whose value is a *map of name -> schema*.
_SCHEMA_MAP_KEYS = ("$defs", "definitions")


class FreeFormMapError(ValueError):
    """A schema node is an open map, which no strict dialect can express."""


def to_strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return ``model``'s JSON Schema with strict-mode constraints applied.

    The input model is not modified and neither is the schema Pydantic generated for it.

    Raises:
        FreeFormMapError: if any node is an open map (``additionalProperties`` set to
            anything but ``False``, a ``patternProperties`` node, or an object with no
            declared properties).
    """
    return _strict(copy.deepcopy(model.model_json_schema()))


def _strict(node: Any) -> Any:
    if isinstance(node, list):
        return [_strict(item) for item in node]
    if not isinstance(node, dict):
        return node

    out = dict(node)
    _reject_open_map(out)

    for key in _SCHEMA_MAP_KEYS:
        if isinstance(out.get(key), dict):
            out[key] = {name: _strict(sub) for name, sub in out[key].items()}

    for key in _SCHEMA_LIST_KEYS:
        if isinstance(out.get(key), list):
            out[key] = [_strict(sub) for sub in out[key]]

    if "items" in out:
        out["items"] = _strict(out["items"])

    properties = out.get("properties")
    if isinstance(properties, dict):
        out["properties"] = {name: _strict(sub) for name, sub in properties.items()}
        # Every declared key must be required. A field with a default is still *emitted*
        # by the provider; the default only decides what our own parser does when it is
        # absent, and absence is exactly what strict mode removes.
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False

    return out


def _reject_open_map(node: dict[str, Any]) -> None:
    """Fail loudly on a node that declares runtime-chosen keys."""
    extra = node.get("additionalProperties")
    if extra is not None and extra is not False:
        raise FreeFormMapError(
            f"schema node {node.get('title') or node.get('type')!r} sets "
            f"additionalProperties={extra!r}; a free-form map cannot be made strict"
        )
    if "patternProperties" in node:
        raise FreeFormMapError(
            f"schema node {node.get('title') or node.get('type')!r} uses patternProperties; "
            "a free-form map cannot be made strict"
        )
    if node.get("type") == "object" and "properties" not in node:
        raise FreeFormMapError(
            f"schema node {node.get('title') or 'object'!r} is an object with no declared "
            "properties; a free-form map cannot be made strict"
        )

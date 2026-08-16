"""The single naming contract between OpenConnector actionIds and agent tool names.

OpenConnector actions are namespaced with dots (``gmail.get_profile``). Agent
tool-calling APIs (Anthropic, OpenAI, most OpenAI-compatible) restrict tool
names to ``[A-Za-z0-9_-]`` — dots are illegal. These are two different
namespaces; this module holds the one deterministic mapping between them.

The mapping is NEVER reversed: the OpenConnector actionId is carried explicitly
(bound into each warm-started tool's handler at registration), so a lossy
forward transform is fine as long as it is deterministic, legal, and
collision-free across a provider's action set.
"""

from __future__ import annotations

import re

# Intersection of the providers' rules: charset [A-Za-z0-9_-], length <= 64
# (Anthropic allows 128, but OpenAI/Gemini cap at 64 — honor the tightest).
_LEGAL_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def action_id_to_tool_name(action_id: str) -> str:
    """Map an OpenConnector actionId to an agent-legal tool name.

    ``gmail.get_profile`` -> ``gmail_get_profile``. Raises ``ValueError`` at
    call time (import/registration) if the result is not provider-legal, so an
    un-nameable actionId fails loudly here instead of as a runtime API 400.
    """
    name = action_id.replace(".", "_")
    if not _LEGAL_TOOL_NAME.match(name):
        raise ValueError(
            f"actionId {action_id!r} maps to illegal tool name {name!r} "
            "(must match [A-Za-z0-9_-]{1,64})"
        )
    return name

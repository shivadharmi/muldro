"""Ground-truth OpenConnector schemas, for the gateway registry's anti-drift tests.

The registry's hand-declared input schemas must equal what OpenConnector actually
serves. A previous increment shipped seven Gmail action ids that did not exist in
OpenConnector; every unit test passed because the tests asserted against the same
invented constants, and it only failed on a live call. This fixture is the
mechanical gate against a repeat, so it is committed to the repo rather than read
from a developer's scratch directory -- a check that can silently skip is not a
check.
"""

from __future__ import annotations

import json
from pathlib import Path

_FIXTURE = Path(__file__).parent / "fixtures" / "openconnector_curated_schemas.json"

_DOC = json.loads(_FIXTURE.read_text())
CURATED_ACTIONS: dict[str, dict] = _DOC["actions"]


def input_schema_for(action_id: str) -> dict:
    """Return OpenConnector's own inputSchema for ``action_id``.

    Raises KeyError if the action is absent -- deliberately loud, because an
    action the catalog does not know about is exactly the bug this guards.
    """
    return CURATED_ACTIONS[action_id]["inputSchema"]

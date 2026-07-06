"""Step 6A: JARVIS_RUNTIME selects the chat execution runtime. Default 'legacy' = the
agent_loop path (zero behavior change); 'deep' = the Deep Agents lead path."""

import os
from unittest.mock import patch

from src.config.settings import Settings


def test_runtime_defaults_to_legacy():
    s = Settings(_env_file=None)
    assert s.runtime == "legacy"


def test_runtime_reads_jarvis_runtime_env():
    with patch.dict(os.environ, {"JARVIS_RUNTIME": "deep"}):
        s = Settings(_env_file=None)
        assert s.runtime == "deep"

"""The seed sync must be SYMMETRIC for `managed_local`.

It used to only ever ADD the key, so an installation seeded as managed_local
before it moved to another transport kept `config={"managed_local": True}`
forever, contradicting its own seed — a latent one-way door.

Driven through a fake session (the same idiom as
`test_mcp_pool.test_one_misconfigured_gateway_installation_...`) so the branch
is exercised without a database.
"""

from types import SimpleNamespace
from unittest.mock import patch

import src.integrations.seed_installations as seed_mod
from src.integrations.seed_installations import seed_installations

_BASE_SEED = {
    "server_name": "demo-server",
    "display_name": "Demo",
    "transport": "streamable-http",
    "remote_url": None,
    "command": None,
    "args": None,
    "env_template": {},
    "auth_provider": "platform_jwt",
    "scopes_granted": None,
}


class _Result:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class _Db:
    """Returns the trust lookup then the installation lookup, in call order."""

    def __init__(self, installations):
        self._results = [_Result([]), _Result(installations)]
        self.added: list = []
        self.deleted: list = []

    async def execute(self, *a, **kw):
        return self._results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        return None


def _existing(config) -> SimpleNamespace:
    """An installation row already matching the seed except for `config`."""
    return SimpleNamespace(**{**_BASE_SEED, "trust_id": None, "config": config})


async def _run(seed_overrides: dict, existing_config) -> tuple[SimpleNamespace, int]:
    inst = _existing(existing_config)
    db = _Db([inst])
    with patch.object(seed_mod, "_DEFAULT_INSTALLATIONS", [{**_BASE_SEED, **seed_overrides}]):
        changed = await seed_installations(db, "ws_seedsync", "usr_seedsync")
    return inst, changed


async def test_managed_local_is_cleared_when_the_seed_drops_it():
    inst, changed = await _run({}, {"managed_local": True})
    assert inst.config == {}
    assert changed == 1


async def test_managed_local_is_set_when_the_seed_declares_it():
    inst, changed = await _run({"managed_local": True}, {})
    assert inst.config == {"managed_local": True}
    assert changed == 1


async def test_unrelated_config_keys_survive_the_clear():
    inst, _ = await _run({}, {"managed_local": True, "cloud_id": "abc"})
    assert inst.config == {"cloud_id": "abc"}


async def test_no_write_when_the_flag_already_agrees():
    inst, changed = await _run({}, None)
    assert inst.config is None
    assert changed == 0

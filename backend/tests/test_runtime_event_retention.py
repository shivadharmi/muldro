"""Retention contract (Step 5 §6): runtime_events is the durable system-of-record and
is EXEMPT from EvictionService hard-deletion (unlike normalized_events at 90d). Guard:
running a full eviction never issues a DELETE against runtime_events."""

import inspect

import src.services.eviction_service as es


def test_eviction_service_never_deletes_runtime_events():
    # Guard: the service must not reference the RuntimeEvent MODEL or issue a DELETE
    # against the runtime_events table. We check the CamelCase model symbol + a raw
    # DELETE pattern — NOT the bare table name, which the class docstring legitimately
    # names to document the exemption (so `"runtime_events" not in src` would wrongly
    # contradict test_retention_contract_documented below).
    import re

    src = inspect.getsource(es)
    assert "RuntimeEvent" not in src, "EvictionService must not reference the RuntimeEvent model"
    assert not re.search(r"delete\s+from\s+runtime_events", src, re.IGNORECASE), (
        "EvictionService must not DELETE from runtime_events (system-of-record)"
    )


def test_retention_contract_documented():
    # The class docstring must state the RuntimeEvent exemption so a future maintainer
    # does not add it to the eviction set unaware it is load-bearing for Step 10.
    doc = (es.EvictionService.__doc__ or "").lower()
    assert "runtime_events" in doc and "system-of-record" in doc

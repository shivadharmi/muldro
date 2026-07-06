"""Characterization tests for tool-vs-capability risk monotonicity (TOOL-P3-3).

Per-tool ``risk_level`` may intentionally diverge from the capability-level risk in
``CAPABILITY_CATALOG`` (tool granularity is more precise than capability granularity —
e.g. ``browser_tabs`` is ``low`` but maps to ``browser.open``/``medium``). That divergence
is deliberate but was previously untested, so a future edit could silently understate a
write tool's risk enough to drop it out of the approval gate.

These tests pin the safety-relevant invariants:

1. **Approval monotonicity (bands):** ``none``/``low`` never require approval; ``high``/
   ``critical`` always do. ``medium`` is the discretionary band (browser-family writes are
   intentionally approval-exempt).
2. **Dangerous-capability monotonicity (the headline):** when a tool's *capability* is
   ``high``/``critical`` risk, the tool still requires approval even if the tool's own
   ``risk_level`` is lower. A downward risk divergence can never drop a dangerous
   capability out of the gate.
3. **Downward-divergence allow-list:** the exact set of tools whose risk is strictly below
   their capability's risk is pinned, so any new divergence forces a conscious review.
"""

from __future__ import annotations

from src.integrations.capabilities import CAPABILITY_CATALOG
from src.tools.catalog import EXTERNAL_TOOL_SEEDS, INTERNAL_TOOLS

# Total order over risk strings. Higher rank == more dangerous.
RISK_RANK: dict[str, int] = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

_NO_APPROVAL_BAND = {"none", "low"}
_ALWAYS_APPROVAL_BAND = {"high", "critical"}


def _all_tools() -> list[tuple[str, str, str, bool]]:
    """Normalize both registries to (name, capability, risk_level, requires_approval)."""
    rows: list[tuple[str, str, str, bool]] = []
    for t in INTERNAL_TOOLS:
        rows.append((t.name, t.capability, t.risk_level, t.requires_approval))
    for s in EXTERNAL_TOOL_SEEDS:
        rows.append((s.name, s.capability, s.risk_level, s.requires_approval))
    return rows


def test_all_tool_risk_levels_are_known():
    """Every tool's risk_level is a known rank — guards typos that break ordering."""
    for name, _cap, risk, _approval in _all_tools():
        assert risk in RISK_RANK, f"{name} has unknown risk_level={risk!r}"


def test_low_or_none_risk_never_requires_approval():
    """Approval monotonicity (lower band): none/low risk tools never gate on approval."""
    for name, _cap, risk, requires_approval in _all_tools():
        if risk in _NO_APPROVAL_BAND:
            assert not requires_approval, (
                f"{name} is risk={risk} but requires_approval=True (breaks low-band monotonicity)"
            )


def test_high_or_critical_risk_always_requires_approval():
    """Approval monotonicity (upper band): high/critical risk tools always require approval."""
    for name, _cap, risk, requires_approval in _all_tools():
        if risk in _ALWAYS_APPROVAL_BAND:
            assert requires_approval, (
                f"{name} is risk={risk} but requires_approval=False (breaks high-band monotonicity)"
            )


def test_dangerous_capability_tools_require_approval():
    """Headline invariant: a high/critical *capability* forces approval on every tool that
    maps to it, even when the tool's own risk_level diverges downward.

    This pins a *catalog* invariant (and the degraded fallback gate in
    graph_executor's ``elif not self._trust_engine`` branch, which consults
    ``requires_approval`` directly). The live approval decision on the autonomous path is
    the TrustEngine 4×4 matrix, which keys off runtime RiskAssessor output, not this flag —
    that gate is exercised elsewhere. The value here: a downward tool<capability risk
    divergence can never silently clear the ``requires_approval`` seed for a dangerous
    capability.
    """
    for name, capability, _risk, requires_approval in _all_tools():
        meta = CAPABILITY_CATALOG.get(capability)
        if meta is None:
            continue  # unknown capability — not this test's concern
        if meta.risk_level in _ALWAYS_APPROVAL_BAND:
            assert requires_approval, (
                f"{name} maps to {capability} (capability risk={meta.risk_level}) "
                "but does not require approval — dangerous capability escaped the gate"
            )


def test_read_only_capability_tools_are_low_risk_and_ungated():
    """Read-only capabilities never carry approval and never exceed low risk."""
    for name, capability, risk, requires_approval in _all_tools():
        meta = CAPABILITY_CATALOG.get(capability)
        if meta is None or not meta.read_only:
            continue
        assert risk in _NO_APPROVAL_BAND, (
            f"{name} maps to read-only {capability} but has risk={risk}"
        )
        assert not requires_approval, f"{name} maps to read-only {capability} but requires approval"


def test_downward_risk_divergence_is_allowlisted():
    """Characterization: the exact set of tools whose risk is strictly below their
    capability's risk. New downward divergences must be added here consciously — the point
    is to force review, since a downward divergence is the only direction that can weaken a
    gate. (Upward divergence — tool stricter than capability — is always safe.)
    """
    # name -> (tool_risk, capability_risk). Every entry below is verified safe: gmail
    # label-ops and create_directory keep requires_approval=True; browser ops belong to the
    # approval-exempt browser family. See src/tools/catalog.py risk-divergence note.
    expected = {
        "modify_gmail_message_labels": ("medium", "high"),
        "batch_modify_gmail_message_labels": ("medium", "high"),
        "manage_gmail_filter": ("medium", "high"),
        "manage_gmail_label": ("medium", "high"),
        "browser_tabs": ("low", "medium"),
        "browser_close": ("low", "medium"),
        "browser_resize": ("low", "medium"),
    }

    actual: dict[str, tuple[str, str]] = {}
    for name, capability, risk, _approval in _all_tools():
        meta = CAPABILITY_CATALOG.get(capability)
        if meta is None:
            continue
        if RISK_RANK[risk] < RISK_RANK[meta.risk_level]:
            actual[name] = (risk, meta.risk_level)

    assert actual == expected, (
        "Downward tool<capability risk divergence set drifted.\n"
        f"  new/changed: {set(actual.items()) - set(expected.items())}\n"
        f"  removed: {set(expected.items()) - set(actual.items())}"
    )

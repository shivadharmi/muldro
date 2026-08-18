"""The presence vocabulary (single-lead cutover, invariant 1).

`presence` may only ever DOWNGRADE authority. There is no (mode, presence) pair for which
presence grants something `permission_mode` did not.
"""

import pytest

from src.deep_runtime.confirmation import resolve_effective_permission_mode


@pytest.mark.parametrize(
    ("mode", "presence", "entitled", "expected"),
    [
        # bypass survives ONLY with a present user AND the workspace entitlement.
        ("bypass", "present", True, "bypass"),
        ("bypass", "present", False, "auto"),
        ("bypass", "absent", True, "auto"),
        ("bypass", "absent", False, "auto"),
        # ask/auto are unaffected by presence and by the bypass entitlement.
        ("ask", "present", True, "ask"),
        ("ask", "absent", True, "ask"),
        ("ask", "present", False, "ask"),
        ("ask", "absent", False, "ask"),
        ("auto", "present", True, "auto"),
        ("auto", "absent", True, "auto"),
        ("auto", "present", False, "auto"),
        ("auto", "absent", False, "auto"),
        # Unknown modes fail CLOSED to the strictest mode, never to bypass.
        ("", "present", True, "ask"),
        ("nonsense", "present", True, "ask"),
        (None, "present", True, "ask"),
        # Unknown presence is not "present" — so it cannot keep bypass.
        ("bypass", "", True, "auto"),
        ("bypass", "unknown", True, "auto"),
    ],
)
def test_presence_never_escalates(mode, presence, entitled, expected):
    assert resolve_effective_permission_mode(mode, presence, bypass_entitled=entitled) == expected


def test_no_pair_ever_produces_bypass_without_a_present_entitled_user():
    """The exhaustive statement of invariant 1, independent of the table above."""
    for mode in ("bypass", "ask", "auto", "", "weird"):
        for presence in ("present", "absent", "", "weird"):
            for entitled in (True, False):
                result = resolve_effective_permission_mode(mode, presence, bypass_entitled=entitled)
                if result == "bypass":
                    assert mode == "bypass"
                    assert presence == "present"
                    assert entitled is True

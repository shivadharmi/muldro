"""Trust is evidence about a CAPABILITY, so a rejection must name one.

This was a bare `split(":", 1)[1]` applied to every approval type, so the trust
ladder was fed whatever followed a colon — and a type with no colon fed the
whole literal.
"""

import pytest

from src.api.approval_trust_scope import rejected_capability
from src.integrations.capabilities import CAPABILITY_CATALOG


class TestWhatCountsAsACapability:
    def test_a_step_approval_names_its_capability(self):
        """The case the original split was written for."""
        assert rejected_capability("step:email.send") == "email.send"

    def test_a_governor_approval_is_already_bare(self):
        """Plan-level approvals carry the capability with no prefix at all — a
        prefix-based rule would have had to special-case them."""
        assert rejected_capability("email.send") == "email.send"

    def test_a_tool_name_is_not_a_capability(self):
        """`tool:send_email` yielded "send_email", which no catalogue contains.
        A TrustState row was created for it, given a rejection cooldown, and
        left in the dashboard for ever."""
        assert "send_email" not in CAPABILITY_CATALOG
        assert rejected_capability("tool:send_email") is None

    def test_a_filter_proposal_says_nothing_about_any_capability(self):
        """No colon, so the whole literal was used: declining a proposal to
        quiet some mailing lists demoted a capability named "filter_proposal"."""
        assert rejected_capability("filter_proposal") is None

    def test_a_prepared_action_is_not_one_either(self):
        assert rejected_capability("prepared_action") is None

    def test_an_invented_suffix_is_refused(self):
        """The suffix is CHECKED, not assumed."""
        assert rejected_capability("step:not.a.real.capability") is None

    @pytest.mark.parametrize("value", [None, "", ":", "step:"])
    def test_malformed_input_yields_nothing(self, value):
        assert rejected_capability(value) is None

    def test_every_catalogue_capability_round_trips_under_the_step_prefix(self):
        """Whatever the catalogue holds must survive the prefix strip, or a
        real rejection would be silently discarded."""
        for capability in CAPABILITY_CATALOG:
            assert rejected_capability(f"step:{capability}") == capability

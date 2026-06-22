"""Tests for prefix helpers in src.models.ids.

Covers ``ensure_prefix`` (idempotent single-prefix) and ``strip_prefix``
(remove a single leading ``<word>_``) used to avoid double-prefixed surface ids.
"""

from src.models.ids import ensure_prefix, strip_prefix


class TestEnsurePrefix:
    def test_bare_value_gets_prefix(self):
        assert ensure_prefix("run", "01ABC") == "run_01ABC"

    def test_already_prefixed_value_unchanged(self):
        assert ensure_prefix("run", "run_01ABC") == "run_01ABC"

    def test_idempotent(self):
        once = ensure_prefix("run", "run_01ABC")
        twice = ensure_prefix("run", once)
        assert once == twice == "run_01ABC"

    def test_does_not_match_other_prefix(self):
        # A different prefix that happens to share a substring is not treated
        # as already-prefixed.
        assert ensure_prefix("run", "runner_01") == "run_runner_01"


class TestStripPrefix:
    def test_strips_leading_word_prefix(self):
        assert strip_prefix("run_01ABC") == "01ABC"

    def test_strips_only_first_segment(self):
        assert strip_prefix("summary_run_01ABC") == "run_01ABC"

    def test_no_underscore_returns_value(self):
        assert strip_prefix("01ABC") == "01ABC"

    def test_empty_returns_empty(self):
        assert strip_prefix("") == ""

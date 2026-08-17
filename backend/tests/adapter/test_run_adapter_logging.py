"""run_adapter must configure logging, or warm-start drift warnings are unusable."""

import io
import logging
import sys


def test_configure_logging_attaches_a_root_handler():
    """A drift warning emitted after configure_logging() must reach a handler.

    Regression: run_adapter defined a module logger but never configured one, so
    warm_start's parameter-drift warnings (the whole point of the hybrid schema
    check) were written to a handler-less logger and dropped.
    """
    from run_adapter import configure_logging

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    root.handlers.clear()
    try:
        configure_logging()
        assert root.handlers, "configure_logging() attached no handler to the root logger"
        assert root.level <= logging.INFO, f"root level {root.level} would drop INFO records"
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_a_drift_warning_reaches_stderr_with_level_and_logger_name():
    """The outcome, not the mechanism: what an operator actually reads.

    ``logging.lastResort`` already put WARNING text on stderr bare, so asserting
    only that the message appears would pass with configure_logging() removed.
    The level name and logger name are what this fix adds on top of lastResort,
    so they are the assertions that encode the real gain.

    No caplog: caplog installs its own root handler, so it captures the record
    whether or not configure_logging() ever ran — an unkillable test.
    """
    from run_adapter import configure_logging

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_stderr = sys.stderr
    root.handlers.clear()
    captured = io.StringIO()
    try:
        # basicConfig binds a StreamHandler to the CURRENT sys.stderr, so the
        # redirect has to happen before configure_logging(), not after.
        sys.stderr = captured
        configure_logging()
        logging.getLogger("src.adapter.warm_start").warning(
            "parameter drift — OpenConnector=%s hand-typed=%s", "a", "b"
        )
        for handler in root.handlers:
            handler.flush()
    finally:
        sys.stderr = saved_stderr
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)

    text = captured.getvalue()
    assert "parameter drift" in text, f"drift warning never reached stderr: {text!r}"
    assert "WARNING" in text, f"level name missing — this is bare lastResort output: {text!r}"
    assert "src.adapter.warm_start" in text, f"logger name missing — unattributable: {text!r}"


def test_configure_logging_is_idempotent():
    """Calling it twice must not stack duplicate handlers (double-logged lines)."""
    from run_adapter import configure_logging

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    root.handlers.clear()
    try:
        configure_logging()
        first = len(root.handlers)
        configure_logging()
        assert len(root.handlers) == first, "second call added a duplicate handler"
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)

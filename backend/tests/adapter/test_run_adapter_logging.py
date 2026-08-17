"""run_adapter must configure logging, or warm-start drift warnings are discarded."""

import logging


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

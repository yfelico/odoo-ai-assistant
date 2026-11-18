import logging

from app.agent.audit import AUDIT_LOGGER
from app.logging_config import configure_logging


def test_app_logs_including_the_audit_trail_are_emitted_at_info() -> None:
    configure_logging()

    app_logger = logging.getLogger("app")
    assert logging.getLogger(AUDIT_LOGGER).isEnabledFor(logging.INFO)
    assert app_logger.handlers


def test_configuring_twice_does_not_duplicate_handlers() -> None:
    configure_logging()
    handlers = len(logging.getLogger("app").handlers)

    configure_logging()

    assert len(logging.getLogger("app").handlers) == handlers

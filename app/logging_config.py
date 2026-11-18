"""Logging for the app's own loggers (including the audit trail) at INFO on stderr."""

import logging
from typing import TextIO

APP_LOGGER = "app"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class _AppHandler(logging.StreamHandler[TextIO]):
    """Marks the handler installed here, so configuring twice does not duplicate it."""


def configure_logging() -> None:
    logger = logging.getLogger(APP_LOGGER)
    logger.setLevel(logging.INFO)
    # Idempotent: uvicorn's reloader and tests may import the app more than once.
    if not any(isinstance(handler, _AppHandler) for handler in logger.handlers):
        handler = _AppHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(handler)

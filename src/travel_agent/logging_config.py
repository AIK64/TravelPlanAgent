from __future__ import annotations

import logging
import os


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_SENSITIVE_HTTP_LOGGERS = ("httpx", "httpcore")


def configure_logging() -> None:
    configured_level = os.getenv("APP_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, configured_level, logging.INFO)

    logging.basicConfig(
        level=log_level,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
    )
    logging.getLogger("travel_agent").setLevel(log_level)
    for logger_name in _SENSITIVE_HTTP_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


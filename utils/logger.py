"""Shared logger factory.

Use ``get_logger(__name__)`` everywhere instead of calling ``logging.getLogger``
directly so the formatter / level stays consistent.
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def _configure_root_once() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    _configure_root_once()
    return logging.getLogger(name)

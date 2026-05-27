"""Aggregate aiogram routers for the bot.

Import this module from ``main.py`` and call :func:`register_routers` on
the :class:`aiogram.Dispatcher`.
"""

from __future__ import annotations

from aiogram import Dispatcher

from bot.handlers.menu_handler import router as menu_router
from bot.handlers.start_handler import router as start_router
from bot.handlers.webapp_handler import router as webapp_router


def register_routers(dp: Dispatcher) -> None:
    """Include all feature routers in the dispatcher (order matters)."""
    dp.include_router(start_router)
    dp.include_router(menu_router)
    dp.include_router(webapp_router)


__all__ = ["register_routers"]

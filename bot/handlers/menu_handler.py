"""``/learn`` command — re-open the Web App on demand.

Kept separate from ``start_handler`` so the welcome flow can grow
independently (placement test, settings, etc.) without bloating /learn.
"""

from __future__ import annotations

import os

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import webapp_keyboard
from database.models.user import User
from utils.logger import get_logger

router = Router(name="menu")
log = get_logger(__name__)


@router.message(Command("learn"))
async def cmd_learn(message: Message, session: AsyncSession) -> None:
    """Re-show the Web App button. Ensures the user exists in the DB."""
    tg_user = message.from_user
    if tg_user is None:
        await message.answer("Unable to identify your Telegram account.")
        return

    # Lazily create the user row if /learn was invoked before /start.
    user = await session.get(User, tg_user.id)
    if user is None:
        user = User(
            id=tg_user.id,
            cefr_level="A1",
            total_swipes=0,
            current_streak=0,
            words_swiped_today=0,
            is_tested=False,
        )
        session.add(user)
        await session.flush()
        log.info("Auto-registered user via /learn id=%s", tg_user.id)

    webapp_url = os.getenv("WEBAPP_URL")
    if not webapp_url:
        await message.answer("WEBAPP_URL is not configured on the server.")
        return

    await message.answer(
        "Tap to continue learning:",
        reply_markup=webapp_keyboard(webapp_url, button_text="📚 Continue"),
    )

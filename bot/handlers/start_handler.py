"""``/start`` command — register the Telegram user and offer the Web App."""

from __future__ import annotations

import os

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import webapp_keyboard
from database.models.user import User
from utils.logger import get_logger

router = Router(name="start")
log = get_logger(__name__)


WELCOME_TEXT = (
    "Hi, {name}! 👋\n\n"
    "I'm your English vocabulary trainer. We use a spaced-repetition system: "
    "tap the button below to open the learning app and start swiping through words.\n\n"
    "<b>Controls inside the app</b>\n"
    "• swipe <b>right</b> — I knew this word\n"
    "• swipe <b>left</b> — I don't know it yet\n"
    "• swipe <b>up</b> — I'm not sure"
)


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    """Upsert the user row and reply with a Web-App launch button."""
    tg_user = message.from_user
    if tg_user is None:
        # Should be impossible for a /start, but guard anyway.
        await message.answer("Unable to identify your Telegram account.")
        return

    user = await session.get(User, tg_user.id)
    if user is None:
        # First-time visitor — bootstrap with a default A1 level so that the
        # learning loop can immediately serve words. A proper placement test
        # (calculate_cefr_from_test / save_placement_results) can update
        # cefr_level later via a dedicated handler.
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
        log.info("Registered new user id=%s username=%s", tg_user.id, tg_user.username)

    webapp_url = os.getenv("WEBAPP_URL")
    if not webapp_url:
        await message.answer(
            "Bot is misconfigured — WEBAPP_URL is not set on the server. "
            "Please contact the administrator."
        )
        log.error("WEBAPP_URL env var is empty; cannot send Web App button")
        return

    name = tg_user.first_name or tg_user.username or "there"
    await message.answer(
        WELCOME_TEXT.format(name=name),
        reply_markup=webapp_keyboard(webapp_url, button_text="🚀 Open learning app"),
        parse_mode="HTML",
    )

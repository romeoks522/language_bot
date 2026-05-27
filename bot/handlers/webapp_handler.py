"""Handler for ``web_app_data`` updates.

When the Web App closes via ``Telegram.WebApp.sendData``, Telegram delivers
a regular message containing the payload. We currently use this only as a
diagnostic ping — all real state mutations happen via the FastAPI API
called directly from the Web App during the session.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from utils.logger import get_logger

router = Router(name="webapp_data")
log = get_logger(__name__)


@router.message(F.web_app_data)
async def on_webapp_data(message: Message) -> None:
    data = message.web_app_data.data if message.web_app_data else "<empty>"
    log.info("Received web_app_data from user %s: %s", message.from_user.id, data)
    await message.answer("Got it — your progress is saved. Use /learn to continue.")

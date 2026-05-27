"""Boot entry point — runs the aiogram bot and the FastAPI Web App in parallel.

Required env vars
-----------------
- ``BOT_TOKEN``         — Telegram bot token from @BotFather
- ``WEBAPP_URL``        — HTTPS URL where this app's FastAPI is reachable
                          (use ngrok or similar during local development)
- ``DATABASE_URL``      — optional override; defaults to localhost postgres

Optional
--------
- ``WEBAPP_HOST`` / ``WEBAPP_PORT`` — bind address/port for uvicorn
                                       (defaults: 0.0.0.0:8000)
- ``WEBAPP_DEV_USER_ID`` — bypass Telegram initData HMAC during local dev
- ``LOG_LEVEL``         — DEBUG/INFO/WARNING/ERROR (default INFO)
"""

from __future__ import annotations

import asyncio
import os
import sys

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

from bot.handlers import register_routers
from bot.middlewares import DbSessionMiddleware
from database.connection import AsyncSessionFactory
from utils.logger import get_logger
from webapp.api import app as fastapi_app

log = get_logger(__name__)


async def _run_bot() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        log.error("BOT_TOKEN is not set — the bot polling loop will not start.")
        return

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Inject a fresh AsyncSession into every handler call.
    session_mw = DbSessionMiddleware(AsyncSessionFactory)
    dp.message.middleware(session_mw)
    dp.callback_query.middleware(session_mw)

    register_routers(dp)

    log.info("Starting aiogram polling…")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


async def _run_webapp() -> None:
    host = os.getenv("WEBAPP_HOST", "0.0.0.0")
    port = int(os.getenv("WEBAPP_PORT", "8000"))
    log_level = os.getenv("LOG_LEVEL", "info").lower()

    config = uvicorn.Config(
        fastapi_app,
        host=host,
        port=port,
        log_level=log_level,
        loop="asyncio",
        access_log=False,
    )
    server = uvicorn.Server(config)
    log.info("Starting FastAPI Web App on http://%s:%d", host, port)
    await server.serve()


async def main() -> None:
    load_dotenv()

    # Surface a single point of failure if something inside either task dies.
    bot_task = asyncio.create_task(_run_bot(), name="bot")
    web_task = asyncio.create_task(_run_webapp(), name="webapp")
    done, pending = await asyncio.wait(
        {bot_task, web_task}, return_when=asyncio.FIRST_EXCEPTION
    )
    for t in pending:
        t.cancel()
    for t in done:
        if t.exception() is not None:
            raise t.exception()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)

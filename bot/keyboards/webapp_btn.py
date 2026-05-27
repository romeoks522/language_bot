"""Inline keyboard helpers for launching the Telegram Web App."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo


def webapp_keyboard(
    url: str,
    button_text: str = "Open learning app",
) -> InlineKeyboardMarkup:
    """Build a single-button inline keyboard that opens the Web App.

    Telegram requires the URL to be served over HTTPS. During local
    development, expose ``main.py``'s FastAPI server with ngrok or a
    similar tunnel and pass the HTTPS URL via the ``WEBAPP_URL`` env var.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=button_text,
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )

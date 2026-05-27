"""FastAPI backend for the Telegram Web App.

Endpoints
---------
- ``GET  /``                — serves the Web App HTML shell
- ``GET  /api/next-word``   — returns the next word to display for the user
- ``POST /api/swipe``       — applies the swipe result and schedules the next slot

User identification
-------------------
Every API call MUST include an ``X-Telegram-InitData`` header carrying the
raw ``Telegram.WebApp.initData`` query string. We verify the HMAC against
``BOT_TOKEN`` per the official spec:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app

For local development you can set ``WEBAPP_DEV_USER_ID`` in ``.env``; that
bypasses HMAC verification and pretends every request comes from that user.
Never set this in production.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.srs_engine import (
    cefr_int_to_str,
    get_next_word,
    schedule_word,
)
from database.connection import get_session
from database.models.user import User
from database.models.user_vocabulary import UserVocabulary
from database.models.word import Word
from utils.logger import get_logger


log = get_logger(__name__)

_WEBAPP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_WEBAPP_DIR / "templates"))

app = FastAPI(title="Language Bot Web App", docs_url="/api/docs")
app.mount(
    "/static",
    StaticFiles(directory=str(_WEBAPP_DIR / "static")),
    name="static",
)


# ---------------------------------------------------------------------------
# Telegram initData verification
# ---------------------------------------------------------------------------

def _verify_telegram_init_data(init_data: str, bot_token: str) -> dict:
    """Return the parsed initData dict, or raise HTTPException 401.

    Implements the canonical Telegram Web App HMAC check.
    """
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing X-Telegram-InitData")

    # parse_qsl keeps the original ordering; we need a dict to pull `hash`
    # and the rest to rebuild data_check_string.
    parsed = dict(parse_qsl(init_data, strict_parsing=False, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="initData has no `hash`")

    data_check_string = "\n".join(
        f"{k}={parsed[k]}" for k in sorted(parsed.keys())
    )

    secret_key = hmac.new(
        key=b"WebAppData", msg=bot_token.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    expected_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise HTTPException(status_code=401, detail="initData hash mismatch")

    return parsed


async def get_current_user_id(
    x_telegram_init_data: Annotated[str | None, Header(alias="X-Telegram-InitData")] = None,
) -> int:
    """FastAPI dep: resolve Telegram user id from headers (with dev override)."""
    dev_uid = os.getenv("WEBAPP_DEV_USER_ID")
    if dev_uid:
        try:
            return int(dev_uid)
        except ValueError:
            log.warning("WEBAPP_DEV_USER_ID=%r is not an integer; ignoring", dev_uid)

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise HTTPException(
            status_code=500,
            detail="Server misconfigured: BOT_TOKEN is not set",
        )

    parsed = _verify_telegram_init_data(x_telegram_init_data or "", bot_token)

    user_json = parsed.get("user")
    if not user_json:
        raise HTTPException(status_code=401, detail="initData has no `user` field")
    try:
        return int(json.loads(user_json)["id"])
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail=f"Bad user payload: {exc}") from exc


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class WordPayload(BaseModel):
    id: int
    text: str
    cefr_level: str  # human-readable, e.g. "B1"


class UserStats(BaseModel):
    total_swipes: int
    words_swiped_today: int
    current_streak: int
    words_learned: int          # count of UserVocabulary rows for this user
    cefr_level: str | None


class NextWordResponse(BaseModel):
    word: WordPayload
    vocab_id: int
    user: UserStats


class SwipeRequest(BaseModel):
    vocab_id: int = Field(..., description="UserVocabulary.id from the previous /next-word call")
    direction: str = Field(..., pattern="^(left|right|up)$")


class SwipeResponse(BaseModel):
    user: UserStats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _load_user_or_create(session: AsyncSession, user_id: int) -> User:
    """Find the user; if absent, bootstrap an A1 row so the loop works.

    The /start handler usually creates the row, but if the user opens the
    Web App link before sending /start (rare but possible), we still want
    the app to function.
    """
    user = await session.get(User, user_id)
    if user is None:
        user = User(
            id=user_id,
            cefr_level="A1",
            total_swipes=0,
            current_streak=0,
            words_swiped_today=0,
            is_tested=False,
        )
        session.add(user)
        await session.flush()
        log.info("Bootstrapped user id=%s from Web App", user_id)
    return user


async def _compute_user_stats(session: AsyncSession, user: User) -> UserStats:
    words_learned = (
        await session.execute(
            select(func.count(UserVocabulary.id)).where(UserVocabulary.user_id == user.id)
        )
    ).scalar_one()
    return UserStats(
        total_swipes=user.total_swipes,
        words_swiped_today=user.words_swiped_today,
        current_streak=user.current_streak,
        words_learned=words_learned,
        cefr_level=user.cefr_level,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/next-word", response_model=NextWordResponse)
async def api_next_word(
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NextWordResponse:
    user = await _load_user_or_create(session, user_id)
    try:
        word, vocab = await get_next_word(session, user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await session.commit()
    return NextWordResponse(
        word=WordPayload(
            id=word.id,
            text=word.english_word,
            cefr_level=cefr_int_to_str(word.cefr_level),
        ),
        vocab_id=vocab.id,
        user=await _compute_user_stats(session, user),
    )


@app.post("/api/swipe", response_model=SwipeResponse)
async def api_swipe(
    payload: SwipeRequest,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SwipeResponse:
    user = await _load_user_or_create(session, user_id)

    vocab = await session.get(UserVocabulary, payload.vocab_id)
    if vocab is None or vocab.user_id != user.id:
        raise HTTPException(status_code=404, detail="vocab_id not found for this user")

    word = await session.get(Word, vocab.word_id)
    if word is None:
        raise HTTPException(status_code=404, detail="associated word missing")

    try:
        await schedule_word(session, user, vocab, payload.direction, word)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()
    return SwipeResponse(user=await _compute_user_stats(session, user))

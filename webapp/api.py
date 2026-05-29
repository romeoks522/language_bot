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

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.blocks import (
    SUPPORTED_LEVEL_INTS,
    block_ranges,
    shuffle_for_block,
)
from core.srs_engine import (
    CEFR_STR_TO_INT,
    cefr_int_to_str,
    get_next_word,
    schedule_word,
)
from database.connection import get_session
from database.models.user import User
from database.models.user_block_word import UserBlockWord
from database.models.user_vocabulary import UserVocabulary, VocabStatus
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


# --- Level / block ("Words" tab) models ------------------------------------

class LevelSummary(BaseModel):
    level: str                  # "A1".."B2"
    total_words: int
    blocks_total: int
    blocks_completed: int
    words_completed: int


class BlockSummary(BaseModel):
    block_index: int
    word_count: int
    completed_count: int
    knew: int
    learning: int
    confusing: int
    completed: bool


class BlockWord(BaseModel):
    id: int
    text: str
    cefr_level: str
    status: str | None          # "learning"/"confusing"/"knew_it" or null if unseen


class BlockWordsResponse(BaseModel):
    level: str
    block_index: int
    total: int
    mode: str                   # "full" | "retry"
    words: list[BlockWord]


class BlockSwipeRequest(BaseModel):
    word_id: int = Field(..., description="Word.id of the card that was swiped")
    direction: str = Field(..., pattern="^(left|right|up)$")


class BlockSwipeResponse(BaseModel):
    block: BlockSummary


class BlockCompleteResponse(BaseModel):
    level: str
    block_index: int
    summary: BlockSummary
    retry_word_ids: list[int]
    has_retry: bool


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


# --- Level / block helpers -------------------------------------------------

_STATUS_BY_DIRECTION = {
    "left": VocabStatus.LEARNING,
    "up": VocabStatus.CONFUSING,
    "right": VocabStatus.KNEW_IT,
}


def _level_int_from_path(level: str) -> int:
    """Map an "A1".."B2" path segment to its int, or raise 404."""
    level_int = CEFR_STR_TO_INT.get(level.upper())
    if level_int is None or level_int not in SUPPORTED_LEVEL_INTS:
        raise HTTPException(status_code=404, detail=f"Unknown level: {level!r}")
    return level_int


async def _level_words_ordered(session: AsyncSession, level_int: int) -> list[Word]:
    """All words for a level, easiest -> hardest (frequency desc, id tiebreak)."""
    result = await session.execute(
        select(Word)
        .where(Word.cefr_level == level_int)
        .order_by(Word.frequency_score.desc().nullslast(), Word.id)
    )
    return list(result.scalars().all())


async def _block_statuses(
    session: AsyncSession, user_id: int, level_int: int
) -> dict[int, VocabStatus]:
    """Map of ``word_id -> latest block status`` for a user at a level."""
    result = await session.execute(
        select(UserBlockWord.word_id, UserBlockWord.status).where(
            UserBlockWord.user_id == user_id,
            UserBlockWord.cefr_level == level_int,
        )
    )
    return {row[0]: row[1] for row in result.all()}


def _summarize_block(
    block_index: int,
    block_word_ids: list[int],
    statuses: dict[int, VocabStatus],
) -> BlockSummary:
    knew = learning = confusing = 0
    for wid in block_word_ids:
        st = statuses.get(wid)
        if st is VocabStatus.KNEW_IT:
            knew += 1
        elif st is VocabStatus.LEARNING:
            learning += 1
        elif st is VocabStatus.CONFUSING:
            confusing += 1
    completed_count = knew + learning + confusing
    return BlockSummary(
        block_index=block_index,
        word_count=len(block_word_ids),
        completed_count=completed_count,
        knew=knew,
        learning=learning,
        confusing=confusing,
        completed=len(block_word_ids) > 0 and completed_count >= len(block_word_ids),
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


# ---------------------------------------------------------------------------
# Level / block routes ("Words" tab) — independent of the SRS clock
# ---------------------------------------------------------------------------

@app.get("/api/levels", response_model=list[LevelSummary])
async def api_levels(
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[LevelSummary]:
    """The four CEFR entry-point cards for the Words tab."""
    await _load_user_or_create(session, user_id)

    summaries: list[LevelSummary] = []
    for level_int in SUPPORTED_LEVEL_INTS:
        words = await _level_words_ordered(session, level_int)
        statuses = await _block_statuses(session, user_id, level_int)
        ranges = block_ranges(len(words))

        blocks_completed = 0
        for block_index, (start, end) in enumerate(ranges):
            block_ids = [w.id for w in words[start:end]]
            if _summarize_block(block_index, block_ids, statuses).completed:
                blocks_completed += 1

        summaries.append(
            LevelSummary(
                level=cefr_int_to_str(level_int),
                total_words=len(words),
                blocks_total=len(ranges),
                blocks_completed=blocks_completed,
                words_completed=sum(1 for w in words if w.id in statuses),
            )
        )
    return summaries


@app.get("/api/levels/{level}/blocks", response_model=list[BlockSummary])
async def api_level_blocks(
    level: str,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[BlockSummary]:
    """Scrollable list of blocks (with progress-ring data) for one level."""
    await _load_user_or_create(session, user_id)
    level_int = _level_int_from_path(level)

    words = await _level_words_ordered(session, level_int)
    statuses = await _block_statuses(session, user_id, level_int)
    ranges = block_ranges(len(words))

    return [
        _summarize_block(block_index, [w.id for w in words[start:end]], statuses)
        for block_index, (start, end) in enumerate(ranges)
    ]


@app.get("/api/blocks/{level}/{block_index}", response_model=BlockWordsResponse)
async def api_block_words(
    level: str,
    block_index: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    mode: Annotated[str, Query(pattern="^(full|retry)$")] = "full",
) -> BlockWordsResponse:
    """The shuffled cards for one block.

    ``mode=retry`` returns only the words currently marked Left (learning) or
    Up (confusing) — Right (knew_it) words are filtered out.
    """
    await _load_user_or_create(session, user_id)
    level_int = _level_int_from_path(level)

    words = await _level_words_ordered(session, level_int)
    ranges = block_ranges(len(words))
    if not (0 <= block_index < len(ranges)):
        raise HTTPException(status_code=404, detail="block_index out of range")

    start, end = ranges[block_index]
    block_words = words[start:end]
    ordered = shuffle_for_block(block_words, user_id, level_int, block_index)

    statuses = await _block_statuses(session, user_id, level_int)
    if mode == "retry":
        ordered = [
            w for w in ordered
            if statuses.get(w.id) in (VocabStatus.LEARNING, VocabStatus.CONFUSING)
        ]

    return BlockWordsResponse(
        level=cefr_int_to_str(level_int),
        block_index=block_index,
        total=len(ordered),
        mode=mode,
        words=[
            BlockWord(
                id=w.id,
                text=w.english_word,
                cefr_level=cefr_int_to_str(w.cefr_level),
                status=(statuses[w.id].value if w.id in statuses else None),
            )
            for w in ordered
        ],
    )


@app.post(
    "/api/blocks/{level}/{block_index}/swipe",
    response_model=BlockSwipeResponse,
)
async def api_block_swipe(
    level: str,
    block_index: int,
    payload: BlockSwipeRequest,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BlockSwipeResponse:
    """Record one block swipe (upsert per word) and return block progress.

    This never touches ``UserVocabulary`` / the SRS clock — block mode is a
    fully separate learning surface.
    """
    await _load_user_or_create(session, user_id)
    level_int = _level_int_from_path(level)

    words = await _level_words_ordered(session, level_int)
    ranges = block_ranges(len(words))
    if not (0 <= block_index < len(ranges)):
        raise HTTPException(status_code=404, detail="block_index out of range")

    start, end = ranges[block_index]
    block_word_ids = [w.id for w in words[start:end]]
    if payload.word_id not in block_word_ids:
        raise HTTPException(status_code=404, detail="word_id is not in this block")

    status = _STATUS_BY_DIRECTION[payload.direction]

    stmt = (
        pg_insert(UserBlockWord)
        .values(
            user_id=user_id,
            word_id=payload.word_id,
            cefr_level=level_int,
            block_index=block_index,
            status=status,
        )
        .on_conflict_do_update(
            constraint="uq_user_block_word",
            set_={
                "status": status,
                "cefr_level": level_int,
                "block_index": block_index,
                "updated_at": func.now(),
            },
        )
    )
    await session.execute(stmt)
    await session.commit()

    statuses = await _block_statuses(session, user_id, level_int)
    return BlockSwipeResponse(
        block=_summarize_block(block_index, block_word_ids, statuses)
    )


@app.post(
    "/api/blocks/{level}/{block_index}/complete",
    response_model=BlockCompleteResponse,
)
async def api_block_complete(
    level: str,
    block_index: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BlockCompleteResponse:
    """Finalize a block session: summary + the Left/Up words to retry."""
    await _load_user_or_create(session, user_id)
    level_int = _level_int_from_path(level)

    words = await _level_words_ordered(session, level_int)
    ranges = block_ranges(len(words))
    if not (0 <= block_index < len(ranges)):
        raise HTTPException(status_code=404, detail="block_index out of range")

    start, end = ranges[block_index]
    block_words = words[start:end]
    block_word_ids = [w.id for w in block_words]
    statuses = await _block_statuses(session, user_id, level_int)

    # Preserve the block's shuffled order for the retry set.
    ordered = shuffle_for_block(block_words, user_id, level_int, block_index)
    retry_word_ids = [
        w.id for w in ordered
        if statuses.get(w.id) in (VocabStatus.LEARNING, VocabStatus.CONFUSING)
    ]

    return BlockCompleteResponse(
        level=cefr_int_to_str(level_int),
        block_index=block_index,
        summary=_summarize_block(block_index, block_word_ids, statuses),
        retry_word_ids=retry_word_ids,
        has_retry=len(retry_word_ids) > 0,
    )

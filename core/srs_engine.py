"""
srs_engine.py — Core spaced-repetition scheduling logic.

All scheduling uses an integer index (User.total_swipes) as the clock,
never wall-clock timestamps.  Every public function takes an AsyncSession
so callers can wrap multiple operations in a single transaction.
"""

import random
from datetime import date, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.user import User
from database.models.user_vocabulary import UserVocabulary, VocabStatus
from database.models.word import Word

# ---------------------------------------------------------------------------
# CEFR helpers
# ---------------------------------------------------------------------------

CEFR_STR_TO_INT: dict[str, int] = {
    "A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6,
}
CEFR_INT_TO_STR: dict[int, str] = {v: k for k, v in CEFR_STR_TO_INT.items()}


def cefr_str_to_int(level: str) -> int:
    return CEFR_STR_TO_INT[level.upper()]


def cefr_int_to_str(level: int) -> str:
    return CEFR_INT_TO_STR[level]


# ---------------------------------------------------------------------------
# Shift tables
# ---------------------------------------------------------------------------

def _shift_for_learning() -> int:
    """Left swipe: word is unknown — review soon."""
    return random.randint(5, 10)


def _shift_for_confusing() -> int:
    """Up swipe: word is partially known — review at medium distance."""
    return random.randint(25, 50)


def _shift_for_knew_it(user_cefr_int: int, word_cefr_int: int) -> int:
    """
    Right swipe: word is known.

    The shift grows with the *positive* delta (user level - word level).
    Easy words for a strong user get parked very far away; hard words that
    the user still knows get a smaller but still large shift.
    """
    delta = user_cefr_int - word_cefr_int  # can be negative if word > user

    shift_ranges: dict[int, tuple[int, int]] = {
        -2: (50,   80),
        -1: (80,   150),
         0: (100,  200),
         1: (200,  400),
         2: (700,  1200),
         3: (1200, 2000),
    }
    # Clamp delta to the defined range.
    clamped = max(-2, min(delta, 3))
    lo, hi = shift_ranges[clamped]
    return random.randint(lo, hi)


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------

async def _find_free_slot(
    session: AsyncSession,
    user_id: int,
    desired_index: int,
) -> int:
    """
    Return the first unoccupied target_swipe_index >= desired_index for this user.

    Fetches occupied slots in a single bulk query, then walks linearly.
    In practice collisions are rare so the scan terminates quickly.
    """
    # Pull all occupied slots in a reasonable lookahead window.
    lookahead = 200
    result = await session.execute(
        select(UserVocabulary.target_swipe_index).where(
            and_(
                UserVocabulary.user_id == user_id,
                UserVocabulary.target_swipe_index >= desired_index,
                UserVocabulary.target_swipe_index < desired_index + lookahead,
            )
        )
    )
    occupied: set[int] = {row[0] for row in result.fetchall() if row[0] is not None}

    candidate = desired_index
    while candidate in occupied:
        candidate += 1
    return candidate


# ---------------------------------------------------------------------------
# Scheduling: assign next index after a swipe
# ---------------------------------------------------------------------------

async def schedule_word(
    session: AsyncSession,
    user: User,
    vocab_entry: UserVocabulary,
    swipe_direction: str,  # "left" | "up" | "right"
    word: Word,
) -> None:
    """
    Update vocab_entry.status and target_swipe_index based on swipe direction.
    Increments user.total_swipes *before* computing the next slot so that
    the new index is always strictly in the future.

    Caller is responsible for committing the session.
    """
    user.total_swipes += 1  # advance the global clock first

    user_cefr_int = cefr_str_to_int(user.cefr_level) if user.cefr_level else 1

    if swipe_direction == "left":
        vocab_entry.status = VocabStatus.LEARNING
        raw_shift = _shift_for_learning()

    elif swipe_direction == "up":
        vocab_entry.status = VocabStatus.CONFUSING
        raw_shift = _shift_for_confusing()

    elif swipe_direction == "right":
        vocab_entry.status = VocabStatus.KNEW_IT
        raw_shift = _shift_for_knew_it(user_cefr_int, word.cefr_level)

    else:
        raise ValueError(f"Unknown swipe direction: {swipe_direction!r}")

    desired_index = user.total_swipes + raw_shift
    free_slot = await _find_free_slot(session, user.id, desired_index)
    vocab_entry.target_swipe_index = free_slot

    await _update_streak(session, user)


# ---------------------------------------------------------------------------
# Word feeding
# ---------------------------------------------------------------------------

async def get_next_word(
    session: AsyncSession,
    user: User,
) -> tuple[Word, UserVocabulary]:
    """
    Return the (Word, UserVocabulary) pair to show for the user's next swipe.

    Priority order:
      1. A scheduled word whose target_swipe_index == total_swipes + 1.
      2. A brand-new word from the Word table at the user's CEFR level that
         isn't in UserVocabulary yet.

    Raises LookupError if no words are available at the user's level.
    """
    next_index = user.total_swipes + 1

    # --- Priority 1: scheduled review ---
    scheduled = await session.execute(
        select(UserVocabulary)
        .where(
            and_(
                UserVocabulary.user_id == user.id,
                UserVocabulary.target_swipe_index == next_index,
            )
        )
        .limit(1)
    )
    uv: UserVocabulary | None = scheduled.scalar_one_or_none()

    if uv is not None:
        word_result = await session.get(Word, uv.word_id)
        if word_result is not None:
            return word_result, uv

    # --- Priority 2: inject a new unseen word ---
    user_cefr_int = cefr_str_to_int(user.cefr_level) if user.cefr_level else 1

    # Subquery: word IDs already in this user's vocabulary.
    seen_subq = (
        select(UserVocabulary.word_id)
        .where(UserVocabulary.user_id == user.id)
        .scalar_subquery()
    )

    new_word_result = await session.execute(
        select(Word)
        .where(
            and_(
                Word.cefr_level == user_cefr_int,
                Word.id.not_in(seen_subq),
            )
        )
        # Most common (highest frequency) words first so learners meet the
        # most practical vocabulary earliest. NULL scores sort last; Word.id
        # is the deterministic tie-breaker.
        .order_by(Word.frequency_score.desc().nullslast(), Word.id)
        .limit(1)
    )
    new_word: Word | None = new_word_result.scalar_one_or_none()

    if new_word is None:
        raise LookupError(
            f"No unseen words available for user {user.id} at CEFR level "
            f"{user_cefr_int} ({cefr_int_to_str(user_cefr_int)})"
        )

    # Create a UserVocabulary entry pre-assigned to next_index so the slot
    # is immediately reserved (conflict resolution not needed — new words
    # are always injected into the very next slot; the caller swipes
    # before the next get_next_word call advances the pointer).
    new_uv = UserVocabulary(
        user_id=user.id,
        word_id=new_word.id,
        status=VocabStatus.LEARNING,
        target_swipe_index=next_index,
    )
    session.add(new_uv)
    await session.flush()   # get new_uv.id without committing
    return new_word, new_uv


# ---------------------------------------------------------------------------
# Daily streak management
# ---------------------------------------------------------------------------

async def _update_streak(session: AsyncSession, user: User) -> None:
    """
    Called once per swipe (after total_swipes increment).

    Streak rules:
    - Same day as last_active_date_utc → increment words_swiped_today.
    - Yesterday → set words_swiped_today = 1 (streak survives).
    - Older / None → reset words_swiped_today = 1, reset current_streak = 0.
    - Exactly 30 words swiped today → +1 to current_streak.
    """
    today = date.today()   # UTC date from server clock

    if user.last_active_date_utc is None or user.last_active_date_utc < today:
        delta_days = (
            (today - user.last_active_date_utc).days
            if user.last_active_date_utc
            else 999
        )
        if delta_days == 1:
            user.words_swiped_today = 1
        else:
            user.words_swiped_today = 1
            user.current_streak = 0

        user.last_active_date_utc = today
    else:
        user.words_swiped_today += 1

    if user.words_swiped_today == 30:
        user.current_streak += 1


# ---------------------------------------------------------------------------
# Placement-test CEFR assessment
# ---------------------------------------------------------------------------

def calculate_cefr_from_test(
    known_word_ids: list[int],
    all_test_words: list[Word],
) -> str:
    """
    Given the words the user swiped right on during the placement test,
    return their assigned CEFR level string (e.g. "B1").

    Algorithm:
      - Compute % known per CEFR level band.
      - The *highest* level where the user knows ≥ 60 % of words becomes
        their assigned level (they have solid foundation at that level).
      - Fall back to A1 if nothing meets the threshold.
    """
    known_set = set(known_word_ids)

    # Group test words by CEFR level.
    level_totals: dict[int, int] = {}
    level_known:  dict[int, int] = {}

    for word in all_test_words:
        lvl = word.cefr_level
        level_totals[lvl] = level_totals.get(lvl, 0) + 1
        if word.id in known_set:
            level_known[lvl] = level_known.get(lvl, 0) + 1

    THRESHOLD = 0.60
    assigned_level = 1  # default: A1

    for lvl in sorted(level_totals.keys()):
        total = level_totals[lvl]
        known = level_known.get(lvl, 0)
        if total > 0 and (known / total) >= THRESHOLD:
            assigned_level = lvl

    return cefr_int_to_str(assigned_level)


async def save_placement_results(
    session: AsyncSession,
    user: User,
    known_word_ids: list[int],
    all_test_words: list[Word],
) -> str:
    """
    Persist placement-test results:
      1. Assign CEFR level to user.
      2. Insert UserVocabulary rows for known words (status=knew_it, no schedule slot yet).
      3. Set is_tested = True.

    Returns the assigned CEFR string.
    Caller must commit.
    """
    assigned_level = calculate_cefr_from_test(known_word_ids, all_test_words)
    user.cefr_level = assigned_level
    user.is_tested = True

    user_cefr_int = cefr_str_to_int(assigned_level)
    known_set = set(known_word_ids)

    for word in all_test_words:
        if word.id in known_set:
            # Park known words far in the future — no immediate slot needed.
            far_shift = _shift_for_knew_it(user_cefr_int, word.cefr_level)
            desired = user.total_swipes + far_shift
            free_slot = await _find_free_slot(session, user.id, desired)

            uv = UserVocabulary(
                user_id=user.id,
                word_id=word.id,
                status=VocabStatus.KNEW_IT,
                target_swipe_index=free_slot,
            )
            session.add(uv)

    await session.flush()
    return assigned_level

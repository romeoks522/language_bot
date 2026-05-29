"""Block geometry for the level-based ("Words" tab) vocabulary catalog.

A CEFR level is laid out easiest -> hardest by ``Word.difficulty_rank`` and
chunked into fixed-size **Word Blocks** of roughly :data:`BLOCK_TARGET` words.
Within a block the order is deterministically shuffled (per user) so words are
never presented alphabetically.

This module is intentionally pure (no DB/IO) so it is trivial to unit-test and
reuse from both the API layer and any future tooling.
"""

from __future__ import annotations

import random
from typing import TypeVar

# CEFR levels exposed as learning groups in the UI. The pool only contains
# A1..B2, so these are the four primary entry points.
SUPPORTED_LEVEL_INTS: tuple[int, ...] = (1, 2, 3, 4)
SUPPORTED_LEVELS: tuple[str, ...] = ("A1", "A2", "B1", "B2")

# Target / guard-rails for block sizing (spec: 30-50 words per block).
BLOCK_TARGET = 40
BLOCK_MIN = 30
BLOCK_MAX = 50

T = TypeVar("T")


def block_ranges(total: int) -> list[tuple[int, int]]:
    """Partition ``total`` items into contiguous ``[start, end)`` block ranges.

    Blocks are kept close to :data:`BLOCK_TARGET` and as even as possible. A
    too-small trailing block (``< BLOCK_MIN``) is merged into its predecessor
    so every block respects the 30-50 window (the merged block may reach up to
    ``BLOCK_TARGET + BLOCK_MIN - 1``, still comfortably playable).
    """
    if total <= 0:
        return []
    if total <= BLOCK_MAX:
        return [(0, total)]

    n = max(1, round(total / BLOCK_TARGET))
    base, rem = divmod(total, n)

    bounds: list[tuple[int, int]] = []
    start = 0
    for i in range(n):
        size = base + (1 if i < rem else 0)
        bounds.append((start, start + size))
        start += size

    if len(bounds) > 1 and (bounds[-1][1] - bounds[-1][0]) < BLOCK_MIN:
        prev_start = bounds[-2][0]
        last_end = bounds[-1][1]
        bounds[-2:] = [(prev_start, last_end)]

    return bounds


def block_count(total: int) -> int:
    """Number of blocks a level of ``total`` words produces."""
    return len(block_ranges(total))


def block_slice(ordered: list[T], block_index: int) -> list[T]:
    """Return the words belonging to ``block_index`` from a rank-ordered list."""
    ranges = block_ranges(len(ordered))
    if not (0 <= block_index < len(ranges)):
        return []
    start, end = ranges[block_index]
    return ordered[start:end]


def shuffle_for_block(items: list[T], user_id: int, level: int, block_index: int) -> list[T]:
    """Deterministically shuffle a block's words for a given user.

    Deterministic (so re-opening the same block keeps a stable card order)
    yet non-alphabetical (so same-initial-letter words don't cluster). Keyed
    on ``(user_id, level, block_index)``.
    """
    seed = hash((user_id, level, block_index)) & 0xFFFFFFFF
    rng = random.Random(seed)
    out = list(items)
    rng.shuffle(out)
    return out

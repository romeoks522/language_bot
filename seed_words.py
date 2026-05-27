"""Populate the global ``words`` table from an Oxford 3000 source file.

The Oxford 3000 vocabulary is distributed as plain text lines of the form
``<word> <part-of-speech> <CEFR-level>`` — for example::

    abandon v. B2
    bear (deal with) v. B2
    a, an indefinite article A1
    account n. B1, v. B2
    bank (money) n. Al            # OCR-corrupted A1
    behind prep., adv. Bi         # OCR-corrupted B1
    can1 modal v. A1              # homograph index suffix

This script parses such a file, repairs common OCR mistakes, drops
parenthesised disambiguators, deduplicates entries keeping the **lowest**
CEFR level for any word that appears more than once, and bulk-inserts the
result into the ``words`` table via PostgreSQL's
``INSERT ... ON CONFLICT (english_word) DO NOTHING``.

Usage::

    python seed_words.py                                # default source path
    python seed_words.py path/to/oxford_words.txt       # custom source path
    python seed_words.py --dry-run                      # parse only, no DB writes

Configuration:
    DATABASE_URL — async SQLAlchemy URL (see ``database/connection.py``).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.connection import AsyncSessionFactory
from database.models import Word


# CEFR string -> integer mapping used throughout the rest of the project
# (matches the convention declared on ``Word.cefr_level``).
LEVEL_MAP: dict[str, int] = {
    "A1": 1,
    "A2": 2,
    "B1": 3,
    "B2": 4,
    "C1": 5,
    "C2": 6,
}

# Default location of the seed text file inside the repository.
DEFAULT_SOURCE = Path(__file__).resolve().parent / "database" / "seed" / "oxford_words.txt"

# Parenthesised disambiguators are stripped entirely along with the
# whitespace preceding them, so "bear (deal with) v. B2" becomes "bear v. B2".
_PARENS_RE = re.compile(r"\s*\([^)]*\)")

# OCR fix-ups.  The level "A1" is frequently mis-recognised as "Al"
# (lowercase L) and "B1" as "Bi" (lowercase i).  We only rewrite these
# tokens when they appear as standalone words so we never touch genuine
# vocabulary like "Also" or "Bicycle".
_OCR_FIX_RE = re.compile(r"(?<![A-Za-z])(Al|Bi)(?![A-Za-z])")
_OCR_REPLACEMENTS = {"Al": "A1", "Bi": "B1"}

# A CEFR level token: one of A1/A2/B1/B2/C1/C2.
_LEVEL_RE = re.compile(r"\b([ABC][12])\b")

# Tokens that mark the start of the part-of-speech section.  Once we hit
# one of these, everything to its left is the word zone.
_POS_TOKEN_RE = re.compile(
    r"\b(?:"
    r"v\.|n\.|adj\.|adv\.|prep\.|conj\.|pron\.|det\.|exclam\."
    r"|aux(?:iliary)?\s+v\.|modal\s+v\."
    r"|indefinite\s+article|definite\s+article"
    r"|number\b"
    r"|det\./pron\.?|adj\./adv\.?"
    r")",
    re.IGNORECASE,
)

# Final shape of an accepted word: leading letter then letters, hyphens,
# apostrophes and spaces, capped at the 128-character DB column width.
_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z\-' ]{0,127}$")

# Strips the homograph index suffix used in some dictionaries
# (``can1`` -> ``can``, ``lie2`` -> ``lie``).
_HOMOGRAPH_SUFFIX_RE = re.compile(r"\d+$")


def _ocr_replace(match: re.Match[str]) -> str:
    return _OCR_REPLACEMENTS[match.group(0)]


def parse_entry(entry: str) -> list[tuple[str, int]]:
    """Parse a single vocabulary entry into ``(word, level_int)`` pairs.

    A single entry may yield multiple pairs if the source line groups
    several words together (e.g. ``"a, an indefinite article A1"`` -> two
    pairs).  When the same line declares multiple POS/level combinations
    (e.g. ``"account n. B1, v. B2"``), the minimum (easiest) level is kept.
    Returns ``[]`` for malformed or non-vocabulary lines.
    """
    # Normalise non-breaking spaces and curly apostrophes the PDF source
    # tends to emit so the downstream regexes work on plain ASCII.
    line = entry.replace("\u00a0", " ").replace("\u2019", "'")

    # Step 1: drop "(...)" disambiguators.
    line = _PARENS_RE.sub("", line).strip()
    if not line:
        return []

    # Step 2: repair common OCR mistakes.
    line = _OCR_FIX_RE.sub(_ocr_replace, line)

    # Step 3: split the line into a "word zone" and a "POS/level zone".
    pos_match = _POS_TOKEN_RE.search(line)
    if pos_match:
        word_zone = line[: pos_match.start()]
    else:
        # No recognisable POS — fall back to splitting at the first level.
        level_match = _LEVEL_RE.search(line)
        if not level_match:
            return []
        word_zone = line[: level_match.start()]
    word_zone = word_zone.strip().rstrip(",").strip()

    # Step 4: collect every CEFR level on the line and pick the lowest.
    levels = [LEVEL_MAP[token] for token in _LEVEL_RE.findall(line) if token in LEVEL_MAP]
    if not levels:
        return []
    min_level = min(levels)

    # Step 5: emit one pair per comma-separated word in the word zone.
    results: list[tuple[str, int]] = []
    for piece in word_zone.split(","):
        word = piece.strip()
        word = _HOMOGRAPH_SUFFIX_RE.sub("", word).strip()
        if not word or not _WORD_RE.match(word):
            continue
        results.append((word, min_level))
    return results


def collect_entries(text: str) -> list[str]:
    """Split the raw file content into self-contained entries.

    Some entries wrap onto a second line in the source.  Any line that
    ends with a trailing comma or slash is treated as a continuation and
    joined with the following line.  Document headers (anything mentioning
    "Oxford") are filtered out.
    """
    raw_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or "Oxford" in stripped:
            continue
        raw_lines.append(stripped)

    merged: list[str] = []
    buffer = ""
    for current in raw_lines:
        if buffer:
            current = f"{buffer} {current}"
            buffer = ""
        if current.rstrip().endswith((",", "/")):
            buffer = current
            continue
        merged.append(current)
    if buffer:
        merged.append(buffer)
    return merged


def deduplicate(pairs: list[tuple[str, int]]) -> dict[str, int]:
    """Collapse duplicates keeping the LOWEST CEFR level per ``english_word``.

    A word may legitimately appear several times in the source file as
    different parts of speech (``act v. A2`` then ``act n. B1``) or with
    different senses (``bank (money) n. A1`` and ``bank (river) n. B1``).
    The ``words`` table requires uniqueness on ``english_word``, so we
    keep one row per word and store the lowest (easiest) level — this is
    the level at which a learner first encounters the word.
    """
    best: dict[str, int] = {}
    for word, level in pairs:
        existing = best.get(word)
        if existing is None or level < existing:
            best[word] = level
    return best


async def insert_words(words: dict[str, int]) -> int:
    """Bulk-insert ``{english_word: cefr_level}`` rows; return rows inserted.

    Uses PostgreSQL ``INSERT ... ON CONFLICT (english_word) DO NOTHING``
    so re-running the seeder is idempotent: existing rows are left alone.
    """
    rows = [
        {"english_word": english_word, "cefr_level": cefr_level}
        for english_word, cefr_level in words.items()
    ]
    if not rows:
        return 0

    inserted = 0
    # Chunk to keep individual statements at a reasonable size.
    chunk_size = 500
    async with AsyncSessionFactory() as session:
        async with session.begin():
            for start in range(0, len(rows), chunk_size):
                chunk = rows[start : start + chunk_size]
                stmt = (
                    pg_insert(Word)
                    .values(chunk)
                    .on_conflict_do_nothing(index_elements=["english_word"])
                )
                result = await session.execute(stmt)
                inserted += result.rowcount or 0
    return inserted


async def seed_words(source: Path, dry_run: bool = False) -> tuple[int, int, int]:
    """End-to-end pipeline: parse ``source`` and load it into the DB.

    Returns ``(parsed_pairs, unique_words, rows_inserted)``.  When
    ``dry_run`` is True, ``rows_inserted`` is always 0 and no database
    connection is opened.
    """
    text = source.read_text(encoding="utf-8")
    entries = collect_entries(text)

    pairs: list[tuple[str, int]] = []
    for entry in entries:
        pairs.extend(parse_entry(entry))

    unique = deduplicate(pairs)
    inserted = 0 if dry_run else await insert_words(unique)
    return len(pairs), len(unique), inserted


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed the global vocabulary pool from an Oxford 3000 text file.",
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"Path to the Oxford 3000 text file (default: {DEFAULT_SOURCE}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse the file and print stats without touching the database.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if not args.source.exists():
        sys.exit(f"Source file not found: {args.source}")

    parsed, unique, inserted = asyncio.run(seed_words(args.source, dry_run=args.dry_run))
    print(f"Parsed:   {parsed} (word, level) pairs")
    print(f"Unique:   {unique} words after deduplication (keeping minimum level)")
    if args.dry_run:
        print("Dry run — no database writes performed.")
    else:
        print(f"Inserted: {inserted} new rows (ON CONFLICT DO NOTHING)")


if __name__ == "__main__":
    main()

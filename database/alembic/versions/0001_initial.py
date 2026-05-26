"""Initial schema — users, words, user_vocabulary

Revision ID: 0001_initial
Revises:
Create Date: 2025-01-01 00:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ users
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("cefr_level", sa.String(length=2), nullable=True),
        sa.Column("total_swipes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("words_swiped_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_active_date_utc", sa.Date(), nullable=True),
        sa.Column("is_tested", sa.Boolean(), nullable=False, server_default="false"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ------------------------------------------------------------------ words
    op.create_table(
        "words",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("english_word", sa.String(length=128), nullable=False),
        sa.Column("cefr_level", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("english_word"),
    )
    op.create_index("ix_words_cefr_level", "words", ["cefr_level"])
    op.create_index("ix_words_english_word", "words", ["english_word"])

    # --------------------------------------------------------- user_vocabulary
    op.create_table(
        "user_vocabulary",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("word_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("learning", "confusing", "knew_it", name="vocab_status"),
            nullable=False,
        ),
        sa.Column("target_swipe_index", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["word_id"], ["words.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Unique slot constraint — no two words can share the same index for a user.
        sa.UniqueConstraint("user_id", "target_swipe_index", name="uq_user_swipe_slot"),
    )
    op.create_index("ix_user_vocabulary_user_id", "user_vocabulary", ["user_id"])
    op.create_index("ix_user_vocabulary_word_id", "user_vocabulary", ["word_id"])
    op.create_index(
        "ix_user_vocabulary_target_swipe_index",
        "user_vocabulary",
        ["target_swipe_index"],
    )


def downgrade() -> None:
    op.drop_table("user_vocabulary")
    op.execute("DROP TYPE IF EXISTS vocab_status")
    op.drop_table("words")
    op.drop_table("users")

"""Add user_block_words (level/block "Words" tab flow)

Revision ID: 0003_user_block_words
Revises: 0002_word_frequency_score
Create Date: 2026-05-29 00:00:01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_user_block_words"
down_revision: Union[str, None] = "0002_word_frequency_score"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Re-use the existing ``vocab_status`` enum type created in 0001 rather
    # than declaring a new one (create_type=False prevents a duplicate CREATE).
    vocab_status = postgresql.ENUM(
        "learning", "confusing", "knew_it",
        name="vocab_status",
        create_type=False,
    )

    op.create_table(
        "user_block_words",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("word_id", sa.Integer(), nullable=False),
        sa.Column("cefr_level", sa.Integer(), nullable=False),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("status", vocab_status, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["word_id"], ["words.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "word_id", name="uq_user_block_word"),
    )
    op.create_index("ix_user_block_words_user_id", "user_block_words", ["user_id"])
    op.create_index("ix_user_block_words_word_id", "user_block_words", ["word_id"])
    op.create_index("ix_user_block_words_cefr_level", "user_block_words", ["cefr_level"])
    op.create_index("ix_user_block_words_block_index", "user_block_words", ["block_index"])


def downgrade() -> None:
    op.drop_table("user_block_words")

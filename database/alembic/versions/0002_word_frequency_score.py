"""Add Word.frequency_score

Revision ID: 0002_word_frequency_score
Revises: 0001_initial
Create Date: 2026-05-29 00:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_word_frequency_score"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "words",
        sa.Column("frequency_score", sa.Float(), nullable=True),
    )
    op.create_index("ix_words_frequency_score", "words", ["frequency_score"])


def downgrade() -> None:
    op.drop_index("ix_words_frequency_score", table_name="words")
    op.drop_column("words", "frequency_score")

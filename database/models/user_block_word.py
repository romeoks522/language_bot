from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.connection import Base
from database.models.user_vocabulary import VocabStatus


class UserBlockWord(Base):
    """Per-word knowledge state for the level/block ("Words" tab) flow.

    This table is intentionally **separate** from ``user_vocabulary`` and the
    SRS clock: swiping a card inside a Word Block records the latest gesture
    here and never touches ``target_swipe_index`` or the global study queue.

    A single row per ``(user_id, word_id)`` stores the most recent swipe
    result, plus the block coordinates that produced it so block/level
    progress and the "repeat missed/uncertain" retry set can be computed
    cheaply.
    """

    __tablename__ = "user_block_words"

    __table_args__ = (
        UniqueConstraint("user_id", "word_id", name="uq_user_block_word"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    word_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("words.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Denormalised block coordinates (CEFR level int 1-6 + 0-based block index)
    # so we can aggregate per block without recomputing geometry on writes.
    cefr_level: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    block_index: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    status: Mapped[VocabStatus] = mapped_column(
        Enum(
            VocabStatus,
            name="vocab_status",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,
        ),
        nullable=False,
        default=VocabStatus.LEARNING,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<UserBlockWord user={self.user_id} word={self.word_id} "
            f"L{self.cefr_level} B{self.block_index} status={self.status}>"
        )

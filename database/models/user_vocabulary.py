import enum

from sqlalchemy import BigInteger, Enum, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.connection import Base


class VocabStatus(str, enum.Enum):
    """Possible knowledge states for a word in a user's vocabulary."""

    LEARNING = "learning"       # Swiped left — needs frequent repetition.
    CONFUSING = "confusing"     # Swiped up — needs moderate repetition.
    KNEW_IT = "knew_it"         # Swiped right — rare reinforcement only.


class UserVocabulary(Base):
    """
    Association table linking a User to a Word with SRS scheduling metadata.

    The key scheduling field is `target_swipe_index`: the exact value of
    `User.total_swipes` at which this word should be served next.
    The unique constraint on (user_id, target_swipe_index) enforces that no
    two words compete for the same slot — the conflict-resolution algorithm
    must guarantee this before any INSERT/UPDATE.
    """

    __tablename__ = "user_vocabulary"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "target_swipe_index",
            name="uq_user_swipe_slot",
        ),
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

    status: Mapped[VocabStatus] = mapped_column(
        Enum(VocabStatus, name="vocab_status"),
        nullable=False,
        default=VocabStatus.LEARNING,
    )

    # The SRS clock value at which this word should next be presented.
    # NULL means the word has been seen in the placement test only and is
    # not yet scheduled for the main learning queue.
    target_swipe_index: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # ORM relationships
    user: Mapped["User"] = relationship("User", back_populates="vocabulary")  # noqa: F821
    word: Mapped["Word"] = relationship("Word", back_populates="user_vocabulary")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<UserVocabulary user={self.user_id} word={self.word_id} "
            f"status={self.status} next_at={self.target_swipe_index}>"
        )

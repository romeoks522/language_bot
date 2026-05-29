from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.connection import Base


class Word(Base):
    """A single entry in the global vocabulary pool."""

    __tablename__ = "words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    english_word: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)

    # CEFR level stored as an integer: A1=1, A2=2, B1=3, B2=4, C1=5, C2=6.
    # This allows arithmetic comparisons needed by the SRS delta logic.
    cefr_level: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Corpus frequency score (higher == more common == easier). Populated from
    # the frequency-ranked seed list. Used to order words easiest -> hardest
    # for the SRS "next word" fallback and to lay out the level-based learning
    # blocks ("Words" tab). Nullable: a few parsed words have no score.
    frequency_score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)

    user_vocabulary: Mapped[list["UserVocabulary"]] = relationship(  # noqa: F821
        "UserVocabulary",
        back_populates="word",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<Word id={self.id} word='{self.english_word}' cefr={self.cefr_level}>"

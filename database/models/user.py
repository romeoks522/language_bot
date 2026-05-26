from datetime import date

from sqlalchemy import BigInteger, Boolean, Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.connection import Base


class User(Base):
    """Represents a Telegram user and their global learning progress."""

    __tablename__ = "users"

    # Primary key is the Telegram user ID (fits in BigInteger).
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)

    cefr_level: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # Monotonically increasing counter — the core clock of the SRS engine.
    total_swipes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    current_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    words_swiped_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_active_date_utc: Mapped[date | None] = mapped_column(Date, nullable=True)

    # False until the 150-word placement test is complete.
    is_tested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationship back-ref — lazy="dynamic" avoids loading the whole vocab on access.
    vocabulary: Mapped[list["UserVocabulary"]] = relationship(  # noqa: F821
        "UserVocabulary",
        back_populates="user",
        lazy="select",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} cefr={self.cefr_level} swipes={self.total_swipes}>"
